# Uses a new method of chaining AI calls with paired down HTML to look for selectors before generating new scrapers

# Import libraries
import os
import time
import re
import dotenv
import json
import logging
from datetime import datetime
from jinja2 import Environment, FileSystemLoader
import asyncio

# load environment variables from .env file
dotenv.load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
USE_VERBOSE = os.getenv("USE_VERBOSE", "false").lower() == "true"

def setup_config():
    if not OPENAI_API_KEY:
        raise ValueError("OpenAI API key not set. Please set the OPENAI_API_KEY environment variable.")

    return {
        "api_key": OPENAI_API_KEY,
        "model": "gpt-4o",  # Using gpt-4o for vision capabilities
        "verbose": USE_VERBOSE,
        "headless": False,
    }

def setup_logging(scraper_name):
    """
    Set up logging to capture all LLM interactions
    
    Args:
        scraper_name (str): Name of the scraper for log filename
        
    Returns:
        logging.Logger: Configured logger
    """
    # Create logs directory inside the shared output folder for this method
    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    # Set up logger with unique name to avoid conflicts with test.py
    log_filename = os.path.join(log_dir, f"{scraper_name}_llm.log")
    logger_name = f"llm_generator_{scraper_name}_{id(object())}"  # Unique logger name
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    
    # Remove any existing handlers to avoid duplicates
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # Create file handler
    file_handler = logging.FileHandler(log_filename, mode='w', encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    
    # Create formatter
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    
    # Add handler to logger
    logger.addHandler(file_handler)
    
    logger.info(f"Starting LLM interaction logging for scraper: {scraper_name}")
    logger.info("="*80)
    
    return logger

def log_llm_interaction(logger, interaction_type, prompt, response, model="gpt-4o"):
    """
    Log an LLM interaction with prompt and response
    
    Args:
        logger: Logger instance
        interaction_type (str): Type of interaction (e.g., "DOM Analysis", "Scraper Generation")
        prompt (str): The prompt sent to LLM
        response (str): The response from LLM
        model (str): Model used
    """
    if logger is None:
        print(f"WARNING: Logger is None for {interaction_type}")
        return
        
    try:
        logger.info(f"\n{'='*60}")
        logger.info(f"LLM INTERACTION: {interaction_type}")
        logger.info(f"Model: {model}")
        logger.info(f"{'='*60}")
        logger.info(f"\nPROMPT:\n{prompt}")
        logger.info(f"\n{'-'*40}")
        logger.info(f"\nRESPONSE:\n{response}")
        logger.info(f"\n{'='*60}\n")
        
        # Force flush to ensure it's written
        for handler in logger.handlers:
            if hasattr(handler, 'flush'):
                handler.flush()
                
        print(f"✅ Logged {interaction_type} to {logger.name}")
    except Exception as e:
        print(f"❌ Error logging {interaction_type}: {e}")

def sanitize_filename(name):
    """
    Convert a string to a valid filename
    
    Args:
        name (str): Input string
        
    Returns:
        str: Sanitized filename
    """
    # Replace spaces with underscores
    name = re.sub(r'\s+', '_', name)
    # Remove invalid characters
    name = re.sub(r'[^\w\-.]', '', name)
    # Convert to lowercase
    return name.lower()

def analyze_page_structure(url, config, logger=None):
    """
    Use LLM to analyze the page structure and find article elements and pagination
    """
    from playwright.async_api import async_playwright
    from bs4 import BeautifulSoup

    async def condense_dom(url):
        # scrape dom
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            page = await browser.new_page()
            
            page.set_default_timeout(30000)
            page.set_default_navigation_timeout(30000)
            
            try:
                print(f"Loading page: {url}")
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                print("Page loaded, extracting HTML...")
                html = await page.content()
                print(f"HTML extracted ({len(html)} characters)")
                
                print("Capturing screenshot...")
                screenshot_bytes = await page.screenshot(full_page=True)
                print(f"Screenshot captured ({len(screenshot_bytes)} bytes)")
            except Exception as e:
                print(f"Error loading page: {e}")
                await browser.close()
                return [], None, None  # Return None for html too
            finally:
                await browser.close()
        
        # Return the raw HTML along with condensed DOM
        soup = BeautifulSoup(html, 'html.parser')
        lines = []
        index = 1
        def traverse(node, depth=0):
            nonlocal index
            if node.name is None:
                return
            attrs = []
            for attr in ['aria-label', 'value', 'placeholder', 'href', 'type', 'id', 'class']:
                if node.has_attr(attr):
                    attrs.append(f'{attr}="{node[attr]}"')
            text = node.get_text(strip=True)
            max_text_length = 120
            if len(text) > max_text_length:
                text = text[:max_text_length] + '...'
            if node.name in ['button', 'input', 'select', 'a', 'article', 'nav', 'ul', 'li'] or text:
                indent = '\t' * depth
                attr_str = ' '.join(attrs)
                line = f"{indent}{index}: <{node.name} {attr_str}> {text}".strip()
                lines.append(line)
                index += 1
            for child in node.children:
                traverse(child, depth+1)
        traverse(soup.body if soup.body else soup)
        
        # Summarize repetitive elements
        summarized = []
        prev_tag = None
        prev_class = None
        repeat_count = 0
        sample_lines = []
        import re
        for line in lines:
            m = re.match(r".*<([a-zA-Z0-9]+)[^>]*class=\"([^\"]*)\".*>.*", line)
            if m:
                tag = m.group(1)
                class_val = m.group(2)
            else:
                tag_match = re.match(r".*<([a-zA-Z0-9]+)[^>]*>.*", line)
                tag = tag_match.group(1) if tag_match else None
                class_val = None
            if tag == prev_tag and class_val == prev_class:
                sample_lines.append(line)
                repeat_count += 1
            else:
                if repeat_count > 3:
                    summarized.extend(sample_lines[:3])
                    summarized.append(f"... ({repeat_count-3} more <{prev_tag} class=\"{prev_class}\"> elements omitted)")
                elif repeat_count > 0:
                    summarized.extend(sample_lines)
                sample_lines = [line]
                repeat_count = 1
                prev_tag = tag
                prev_class = class_val
        if repeat_count > 3:
            summarized.extend(sample_lines[:3])
            summarized.append(f"... ({repeat_count-3} more <{prev_tag} class=\"{prev_class}\"> elements omitted)")
        elif repeat_count > 0:
            summarized.extend(sample_lines)
        
        return summarized, screenshot_bytes, html  # Return HTML too

    # split list of chunks
    def chunk_list(lst, chunk_size):
        for i in range(0, len(lst), chunk_size):
            yield lst[i:i+chunk_size]

    # call llm to extract selectors from chunk
    def extract_selectors_from_chunk(chunk, screenshot_bytes, config, logger=None):
        # Call LLM with chunk and screenshot and ask for selectors for articles and pagination
        from openai import OpenAI
        import base64
        
        client = OpenAI(api_key=config["api_key"])
        
        # Encode screenshot to base64
        screenshot_base64 = base64.b64encode(screenshot_bytes).decode('utf-8')
        
        # Load the DOM analysis prompt template
        prompts_dir = os.path.join(os.path.dirname(__file__), "prompts")
        env = Environment(loader=FileSystemLoader(prompts_dir))
        template = env.get_template("dom_analysis_prompt.jinja2")
        
        # Render the template with variables
        prompt_text = template.render(
            dom_chunk=chr(10).join(chunk)
        )
        
        print("Calling LLM for chunk analysis with screenshot...")
        
        response = client.chat.completions.create(
            model=config["model"],
            messages=[
                {"role": "system", "content": "You are a helpful AI assistant specialized in web scraping."},
                {
                    "role": "user", 
                    "content": [
                        {
                            "type": "text",
                            "text": prompt_text
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{screenshot_base64}"
                            }
                        }
                    ]
                }
            ],
            temperature=0.2,
            max_tokens=1000
        )
        result = response.choices[0].message.content
        
        # Log this interaction
        if logger:
            log_llm_interaction(logger, "DOM Chunk Analysis", prompt_text, result, config["model"])
        
        # Clean up the response in case it has markdown code blocks
        cleaned_result = result.strip()
        if cleaned_result.startswith("```json"):
            cleaned_result = cleaned_result.replace("```json", "", 1)
        if cleaned_result.startswith("```"):
            cleaned_result = cleaned_result.replace("```", "", 1)
        if cleaned_result.endswith("```"):
            cleaned_result = cleaned_result[:-3]
        cleaned_result = cleaned_result.strip()
        
        try:
            selectors = json.loads(cleaned_result)
        except Exception as e:
            print(f"Error parsing JSON response: {e}")
            print(f"Cleaned result was: {cleaned_result}")
            selectors = {"article_selectors": [], "next_page_selectors": []}
        
        return selectors

    loop = asyncio.get_event_loop()
    summarized_dom, screenshot_bytes, raw_html = loop.run_until_complete(condense_dom(url))
    
    chunk_size = 500
    all_article_selectors = set()
    all_next_page_selectors = set()
    all_title_selectors = set()
    all_url_selectors = set()
    all_date_selectors = set()

    for chunk in chunk_list(summarized_dom, chunk_size):
        selectors = extract_selectors_from_chunk(chunk, screenshot_bytes, config, logger)
        all_article_selectors.update(selectors.get("article_selectors", []))
        all_next_page_selectors.update(selectors.get("next_page_selectors", []))
        all_title_selectors.update(selectors.get("title_selectors", []))
        all_url_selectors.update(selectors.get("url_selectors", []))
        all_date_selectors.update(selectors.get("date_selectors", []))

    # Use BeautifulSoup on the HTML we already have
    def get_selector_examples(html, selectors, max_examples=1):
        """Get HTML examples using BeautifulSoup on already-fetched HTML"""
        soup = BeautifulSoup(html, 'html.parser')
        examples = {}
        
        for selector in selectors:
            try:
                # Use CSS selector with BeautifulSoup
                elements = soup.select(selector)
                if elements:
                    examples[selector] = []
                    for elem in elements[:max_examples]:
                        elem_html = str(elem)
                        if len(elem_html) > 2000:
                            elem_html = elem_html[:2000] + "..."
                        examples[selector].append(elem_html)
            except Exception as e:
                print(f"Error testing selector '{selector}': {e}")
                continue
        
        return examples
    
    article_examples = get_selector_examples(raw_html, list(all_article_selectors))
    next_page_examples = get_selector_examples(raw_html, list(all_next_page_selectors))
    title_examples = get_selector_examples(raw_html, list(all_title_selectors))
    url_examples = get_selector_examples(raw_html, list(all_url_selectors))
    date_examples = get_selector_examples(raw_html, list(all_date_selectors))

    # Print summary of all candidate selectors found
    print("\n" + "=" * 80)
    print("CANDIDATE SELECTORS FOUND:")
    print("=" * 80)
    print(f"Article selectors ({len(all_article_selectors)}): {list(all_article_selectors)}")
    print(f"Title selectors ({len(all_title_selectors)}): {list(all_title_selectors)}")
    print(f"URL selectors ({len(all_url_selectors)}): {list(all_url_selectors)}")
    print(f"Date selectors ({len(all_date_selectors)}): {list(all_date_selectors)}")
    print(f"Next page selectors ({len(all_next_page_selectors)}): {list(all_next_page_selectors)}")
    print("=" * 80 + "\n")

    return {
        "article_selectors": list(all_article_selectors),
        "next_page_selectors": list(all_next_page_selectors),
        "title_selectors": list(all_title_selectors),
        "url_selectors": list(all_url_selectors),
        "date_selectors": list(all_date_selectors),
        "article_examples": article_examples,
        "next_page_examples": next_page_examples,
        "title_examples": title_examples,
        "url_examples": url_examples,      
        "date_examples": date_examples
    }

# Clean the code output from the LLM, removing markdown code fences.
def clean_scraper_code(result):
    # Remove any explanatory text before the code
    lines = result.split('\n')
    code_started = False
    cleaned_lines = []
    
    for line in lines:
        # Look for the start of Python code (imports, comments, etc.)
        if not code_started:
            if (line.strip().startswith('"""') or 
                line.strip().startswith('import ') or 
                line.strip().startswith('from ') or
                line.strip().startswith('#') and 'import' not in line.lower() and 'python' not in line.lower()):
                code_started = True
                cleaned_lines.append(line)
            elif line.strip().startswith('```python'):
                code_started = True
                continue  # Skip the ```python line
            elif line.strip().startswith('```'):
                continue  # Skip generic ``` lines
        else:
            if line.strip() == '```':
                break  # Stop at closing ```
            cleaned_lines.append(line)
    
    result = '\n'.join(cleaned_lines)
    
    # Original cleaning logic as fallback
    if result.startswith("```python"):
        result = result.replace("```python", "", 1)
        if result.endswith("```"):
            result = result[:-3]
    elif result.startswith("```"):
        result = result.replace("```", "", 1)
        if result.endswith("```"):
            result = result[:-3]
    
    return result.strip()

# Call the OpenAI API to generate the scraper code from the prompt.
def run_script_creator(scraper_prompt, config, logger=None):
    from openai import OpenAI
    client = OpenAI(api_key=config["api_key"])
    response = client.chat.completions.create(
        model=config["model"],
        messages=[
            {"role": "system", "content": "You are a helpful AI assistant specialized in creating web scrapers."},
            {"role": "user", "content": scraper_prompt}
        ],
        temperature=0.2,
        max_tokens=4000
    )
    result = response.choices[0].message.content
    
    # Log this interaction
    if logger:
        log_llm_interaction(logger, "Scraper Generation", scraper_prompt, result, config["model"])
    
    if result and isinstance(result, str):
        return clean_scraper_code(result)
    else:
        print(f"Unexpected response format: {result}")
        return None

def test_scraper_and_get_feedback(scraper_code, scraper_file_path, url):
    """
    Test the generated scraper and return feedback about any errors or issues
    
    Args:
        scraper_code (str): The generated scraper code
        scraper_file_path (str): Path where to write the scraper for testing
        url (str): The target URL for the scraper
        
    Returns:
        dict: Contains success status and error info
    """
    import subprocess
    import tempfile
    import sys
    
    # Write the scraper code to a temporary file
    try:
        with open(scraper_file_path, 'w') as f:
            f.write(scraper_code)
        print(f"Scraper written to: {scraper_file_path}")
    except Exception as e:
        return {
            'success': False,
            'error_type': 'file_write_error',
            'error_message': str(e)
        }
    
    # Try to run the scraper with a short timeout
    try:
        print("Testing generated scraper...")
        # Use a timeout to prevent hanging indefinitely
        result = subprocess.run(
            [sys.executable, scraper_file_path],
            cwd=os.path.dirname(scraper_file_path),
            capture_output=True,
            text=True,
            timeout=60  # 60 second timeout
        )
        
        if result.returncode == 0:
            print("✅ Scraper ran successfully!")
            
            # Check the actual results.json file to see if any articles were found
            results_file_path = os.path.join(os.path.dirname(scraper_file_path), "results.json")
            
            article_count = 0
            try:
                if os.path.exists(results_file_path):
                    with open(results_file_path, 'r') as f:
                        results = json.load(f)
                        article_count = len(results) if isinstance(results, list) else 0
                        print(f"📊 Found {article_count} articles in results.json")
            except Exception as e:
                print(f"⚠️ Could not read results.json: {e}")
            
            # If 0 articles were found, return as failure
            if article_count == 0:
                print("⚠️ 0 articles found - may need headless=False")
                return {
                    'success': False,
                    'error_type': 'zero_results',
                    'stdout': result.stdout,
                    'stderr': result.stderr,
                    'error_message': 'Scraper found 0 articles. May need headless=False or different selectors.'
                }
            
            return {
                'success': True,
                'stdout': result.stdout,
                'stderr': result.stderr,
                'article_count': article_count
            }
        else:
            print(f"❌ Scraper failed with exit code {result.returncode}")
            return {
                'success': False,
                'error_type': 'runtime_error',
                'exit_code': result.returncode,
                'stdout': result.stdout,
                'stderr': result.stderr
            }
            
    except subprocess.TimeoutExpired:
        print("⏰ Scraper test timed out after 60 seconds")
        return {
            'success': False,
            'error_type': 'timeout',
            'error_message': 'Scraper execution timed out after 60 seconds'
        }
    except Exception as e:
        print(f"💥 Error running scraper test: {e}")
        return {
            'success': False,
            'error_type': 'execution_error',
            'error_message': str(e)
        }

def refine_scraper_with_feedback(original_code, feedback, url, scraper_name, config, logger=None):
    """
    Use LLM to refine scraper code based on test feedback
    
    Args:
        original_code (str): The original scraper code that failed
        feedback (dict): Feedback from testing the scraper
        url (str): Target URL
        scraper_name (str): Name of the scraper
        config (dict): Configuration for API calls
        logger: Logger instance for LLM interactions
        
    Returns:
        str: Refined scraper code
    """
    from openai import OpenAI
    
    # Check if this is a zero results issue - if so, force headless=False
    if feedback.get('error_type') == 'zero_results':
        print("🔧 Zero results detected - setting headless=False in scraper code")
        
        import re
        modified_code = original_code
        
        # Case 1: Replace existing headless=True with headless=False
        if re.search(r'headless\s*=\s*True', modified_code, re.IGNORECASE):
            modified_code = re.sub(
                r'headless\s*=\s*True',
                'headless=False',
                modified_code,
                flags=re.IGNORECASE
            )
            print("✅ Changed headless=True to headless=False")
        
        # Case 2: Browser launch without headless parameter - add it
        elif re.search(r'\.chromium\.launch\(\s*\)', modified_code):
            modified_code = re.sub(
                r'\.chromium\.launch\(\s*\)',
                '.chromium.launch(headless=False)',
                modified_code
            )
            print("✅ Added headless=False to browser launch")
        
        # Case 3: Browser launch with other parameters but no headless
        elif re.search(r'\.chromium\.launch\([^)]*\)', modified_code) and 'headless' not in modified_code:
            # Find the launch call and add headless=False as the first parameter
            modified_code = re.sub(
                r'(\.chromium\.launch\()',
                r'\1headless=False, ',
                modified_code
            )
            print("✅ Added headless=False to browser launch with existing parameters")
        
        return modified_code
    
    # Original LLM-based refinement for other errors
    error_info = f"""
Error Type: {feedback.get('error_type', 'unknown')}
Exit Code: {feedback.get('exit_code', 'N/A')}
STDOUT: {feedback.get('stdout', '')}
STDERR: {feedback.get('stderr', '')}
"""
    
    # Load the scraper refinement prompt template
    prompts_dir = os.path.join(os.path.dirname(__file__), "prompts")
    env = Environment(loader=FileSystemLoader(prompts_dir))
    template = env.get_template("scraper_refinement_prompt.jinja2")
    
    # Render the template with variables
    refinement_prompt = template.render(
        original_code=original_code,
        error_info=error_info,
        url=url
    )

    client = OpenAI(api_key=config["api_key"])
    
    print("🔧 Refining scraper based on test feedback...")
    response = client.chat.completions.create(
        model=config["model"],
        messages=[
            {"role": "system", "content": "You are an expert web scraper debugger. Fix broken scrapers based on runtime errors."},
            {"role": "user", "content": refinement_prompt}
        ],
        temperature=0.1,  # Lower temperature for more consistent fixes
        max_tokens=4000
    )
    
    refined_code = response.choices[0].message.content
    
    # Log this interaction
    if logger:
        log_llm_interaction(logger, "Scraper Refinement", refinement_prompt, refined_code, config["model"])
    
    return clean_scraper_code(refined_code)

# Main function to generate a scraper for a given URL with testing and refinement
def generate_scraper(url, scraper_name):
    config = setup_config()
    logger = setup_logging(scraper_name)
    
    print(f"🔧 Set up LLM logger: {logger.name}")
    print(f"📁 Log file handlers: {[h.baseFilename if hasattr(h, 'baseFilename') else str(h) for h in logger.handlers]}")
    
    logger.info(f"Target URL: {url}")
    logger.info(f"Scraper name: {scraper_name}")
    
    print(f"Analyzing page structure for: {url}")
    page_analysis = analyze_page_structure(url, config, logger)
    scraper_prompt = make_prompt(url, scraper_name, page_analysis)
    
    print("Generating initial scraper code...")
    scraper_code = run_script_creator(scraper_prompt, config, logger)
    
    # Define the output directory and file path
    output_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(output_dir, exist_ok=True)
    
    # Test the scraper once
    print("\n" + "="*60)
    print("TESTING GENERATED SCRAPER")
    print("="*60)
    logger.info("TESTING GENERATED SCRAPER")
    
    feedback = test_scraper_and_get_feedback(scraper_code, scraper_file_path, url)
    
    if feedback['success']:
        print("✅ Scraper ran successfully!")
        logger.info("✅ Scraper ran successfully!")
    else:
        print("❌ Scraper failed, attempting one refinement...")
        logger.info("❌ Scraper failed, attempting refinement...")
        logger.info(f"Error details: {feedback}")
        scraper_code = refine_scraper_with_feedback(scraper_code, feedback, url, scraper_name, config, logger)
    
    # Write the final version to file
    try:
        with open(scraper_file_path, 'w') as f:
            f.write(scraper_code)
        print(f"\n📁 Final scraper saved to: {scraper_file_path}")
        logger.info(f"Final scraper saved to: {scraper_file_path}")
    except Exception as e:
        print(f"❌ Error saving final scraper: {e}")
        logger.error(f"Error saving final scraper: {e}")
    
    logger.info("Scraper generation completed")
    logger.info("="*80)
    
    # Force flush all handlers before returning
    for handler in logger.handlers:
        if hasattr(handler, 'flush'):
            handler.flush()
    
    print(f"✅ LLM interactions logged to: {logger.handlers[0].baseFilename if logger.handlers else 'No handlers!'}")
    
    return scraper_code


def make_prompt(url, scraper_name, page_analysis, template_name="generic_template.jinja2"):
    """
    Generate a prompt for the LLM to implement a scraper based on browser-use analysis.
    
    Args:
        url (str): Target URL for the scraper
        scraper_name (str): Name of the scraper file without extension
        page_analysis (dict): Results from browser-use page analysis
        template_name (str): Name of the template file to use
        
    Returns:
        str: Formatted prompt for the LLM
    """
    # Construct the module path
    module_path = f"scrapers.{scraper_name}"
    
    # Load the template from the prompts directory
    prompts_dir = os.path.join(os.path.dirname(__file__), "prompts")
    env = Environment(loader=FileSystemLoader(prompts_dir))
    template = env.get_template("generic_template.jinja2")
    
    # Render the template with our variables
    rendered_template = template.render(
        url=url,
        generated_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        model="gpt-4o",
        module_path=module_path
    )

    # Load the scraper generation prompt template
    prompts_dir = os.path.join(os.path.dirname(__file__), "prompts")
    env_local = Environment(loader=FileSystemLoader(prompts_dir))
    prompt_template = env_local.get_template("scraper_generation_prompt.jinja2")
    
    # Render the prompt template with variables
    return prompt_template.render(
        rendered_template=rendered_template,
        article_examples=format_selectors_with_examples(page_analysis.get('article_examples', {})),
        title_examples=format_selectors_with_examples(page_analysis.get('title_examples', {})),
        url_examples=format_selectors_with_examples(page_analysis.get('url_examples', {})),
        date_examples=format_selectors_with_examples(page_analysis.get('date_examples', {})),
        next_page_examples=format_selectors_with_examples(page_analysis.get('next_page_examples', {}))
    )

def format_selectors_with_examples(selector_examples):
    """Format selectors with their HTML examples for the prompt"""
    if not selector_examples:
        return "No selectors found."
    
    formatted = []
    for selector, examples in selector_examples.items():
        formatted.append(f"- `{selector}` matches:")
        for example in examples:
            formatted.append(f"  {example}")
        formatted.append("")  # Empty line between selectors
    
    return "\n".join(formatted)



# Uses a new method of chaining AI calls with paired down HTML to look for selectors before generating new scrapers

# Import libraries
import os
import sys
import time
import re
import base64
import dotenv
import json
import logging
import subprocess
import asyncio
from datetime import datetime
from jinja2 import Environment, FileSystemLoader
from openai import OpenAI
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from bs4 import BeautifulSoup
import requests
from urllib.parse import urlparse


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

# --- robots.txt helpers ---

# Grouped user-agents by company
SCRAPER_GROUPS = {
    'openai': ['ChatGPT-User', 'GPTBot', 'OAI -SearchBot'],
    'anthropic': ['Claude-Web', 'ClaudeBot', 'anthropic-ai', 'Claude-SearchBot'],
    'apple': ['Applebot-Extended', 'Applebot'],
    'perplexity': ['PerplexityBot'],
    'google': ['Google-Extended'],
    'meta': ['FacebookBot', 'Meta-ExternalAgent']
}

def get_robots_txt(url):
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    robots_url = f"{base_url}/robots.txt"
    try:
        resp = requests.get(robots_url, timeout=5)
        text = resp.text if resp.status_code == 200 else ""
    except Exception as e:
        text = ""
    return text

def get_allowed_scraper_companies(robots_txt):
    allowed_companies = []
    robots_txt = robots_txt.lower()
    lines = robots_txt.splitlines()

    # Check for global disallow: User-agent: * with Disallow: /
    for i, line in enumerate(lines):
        if line.strip() == 'user-agent: *':
            for j in range(i + 1, len(lines)):
                l = lines[j].strip()
                if l.startswith('user-agent:'):
                    break
                if l.startswith('disallow:') and (l == 'disallow: /' or l == 'disallow:/'):
                    # All bots disallowed
                    return []

    for company, agents in SCRAPER_GROUPS.items():
        company_disallowed = False
        for agent in agents:
            user_agent = f"user-agent: {agent.lower()}"
            for i, line in enumerate(lines):
                if line.strip() == user_agent:
                    for j in range(i + 1, len(lines)):
                        l = lines[j].strip()
                        if l.startswith('user-agent:'):
                            break
                        if l.startswith('disallow:'):
                            company_disallowed = True
                            break
                    if company_disallowed:
                        break
            if company_disallowed:
                break
        if not company_disallowed:
            allowed_companies.append(company)
    return allowed_companies


def load_content_config(config_path=None):
    """Load content config from a JSON file. Raises if the file is missing."""
    if config_path is None:
        config_path = os.path.join(os.path.dirname(__file__), '..', 'config.json')
    config_path = os.path.abspath(config_path)
    if not os.path.exists(config_path):
        raise FileNotFoundError(
            f"config.json not found at {config_path}. "
            "Please create one at the project root. See content_configs/ for examples."
        )
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_operator(operator_path=None):
    """Load optional operator identity from operator.json. Returns empty dict if missing."""
    if operator_path is None:
        operator_path = os.path.join(os.path.dirname(__file__), '..', 'operator.json')
    operator_path = os.path.abspath(operator_path)
    if not os.path.exists(operator_path):
        return {}
    with open(operator_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    # Only return fields that have non-empty values
    return {k: v for k, v in data.items() if v and str(v).strip()}


def analyze_page_structure(url, config, logger=None, content_config=None):
    """
    Use LLM to analyze the page structure and find item elements and pagination
    """
    async def condense_dom(url):
        # scrape dom
        async with async_playwright() as p:
            Stealth().hook_playwright_context(p)
            browser = await p.chromium.launch(headless=False)
            page = await browser.new_page()

            page.set_default_timeout(30000)
            page.set_default_navigation_timeout(30000)

            try:
                print(f"Loading page: {url}")
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                
                # Scroll to bottom to trigger any lazy-loaded content
                print("Scrolling to bottom of page...")
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2000)  # Wait for content to load
                
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
                    val = node[attr]
                    # Handle class attribute which BeautifulSoup returns as a list
                    if isinstance(val, list):
                        val = ' '.join(val)
                    attrs.append(f'{attr}="{val}"')
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

    if content_config is None:
        content_config = load_content_config()

    fields = content_config["fields"]
    item_label = content_config.get("item_label", "article")
    content_type = content_config.get("content_type", "articles")
    content_description = content_config.get("description", "")

    # call llm to extract selectors from chunk
    def extract_selectors_from_chunk(chunk, screenshot_bytes, config, logger=None):
        # Call LLM with chunk and screenshot and ask for selectors for items and pagination
        client = OpenAI(api_key=config["api_key"])

        # Encode screenshot to base64
        screenshot_base64 = base64.b64encode(screenshot_bytes).decode('utf-8')

        # Load the DOM analysis prompt template
        prompts_dir = os.path.join(os.path.dirname(__file__), "prompts")
        env = Environment(loader=FileSystemLoader(prompts_dir))
        template = env.get_template("dom_analysis_prompt.jinja2")

        # Render the template with variables
        prompt_text = template.render(
            dom_chunk=chr(10).join(chunk),
            item_label=item_label,
            content_type=content_type,
            content_description=content_description,
            fields=fields,
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
    all_item_selectors = set()
    all_next_page_selectors = set()
    # One set per configured field
    all_field_selectors = {field["name"]: set() for field in fields}

    for chunk in chunk_list(summarized_dom, chunk_size):
        selectors = extract_selectors_from_chunk(chunk, screenshot_bytes, config, logger)
        all_item_selectors.update(selectors.get("item_selectors", []))
        all_next_page_selectors.update(selectors.get("next_page_selectors", []))
        for field in fields:
            all_field_selectors[field["name"]].update(
                selectors.get(f"{field['name']}_selectors", [])
            )

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

    item_examples = get_selector_examples(raw_html, list(all_item_selectors))
    next_page_examples = get_selector_examples(raw_html, list(all_next_page_selectors))
    field_examples = {
        name: get_selector_examples(raw_html, list(sels))
        for name, sels in all_field_selectors.items()
    }

    # Print summary of matched selectors
    print("\n" + "=" * 80)
    print("SELECTORS WITH MATCHES:")
    print("=" * 80)
    print(f"Item selectors ({len(item_examples)}): {list(item_examples.keys())}")
    for name in all_field_selectors:
        matched = field_examples[name]
        print(f"{name} selectors ({len(matched)}): {list(matched.keys())}")
    print(f"Next page selectors ({len(next_page_examples)}): {list(next_page_examples.keys())}")
    print("=" * 80 + "\n")

    result = {
        "item_selectors": list(item_examples.keys()),
        "next_page_selectors": list(next_page_examples.keys()),
        "item_examples": item_examples,
        "next_page_examples": next_page_examples,
    }
    for name in all_field_selectors:
        result[f"{name}_selectors"] = list(field_examples[name].keys())
        result[f"{name}_examples"] = field_examples[name]

    return result

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
    target_dir = os.path.dirname(scraper_file_path) or os.getcwd()
    
    # Write the scraper code to the actual file
    try:
        with open(scraper_file_path, 'w') as f:
            f.write(scraper_code)
    except Exception as e:
        print(f"❌ Error writing scraper file: {e}")
        return {
            'success': False,
            'error_type': 'write_error',
            'error_message': str(e)
        }

    try:
        # Try to run the scraper with a short timeout
        print("Testing generated scraper...")
        # Use a timeout to prevent hanging indefinitely
        result = subprocess.run(
            [sys.executable, scraper_file_path],
            cwd=target_dir,
            capture_output=True,
            text=True,
            timeout=60  # 60 second timeout
        )

        if result.returncode == 0:
            print("✅ Scraper ran successfully!")

            # Check the actual results.json file to see if any articles were found
            results_file_path = os.path.join(target_dir, "results.json")

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

def apply_headless_false(code):
    """Force headless=False in scraper code for sites that need a visible browser."""
    modified = code

    # Case 1: Replace existing headless=True with headless=False
    if re.search(r'headless\s*=\s*True', modified, re.IGNORECASE):
        modified = re.sub(
            r'headless\s*=\s*True',
            'headless=False',
            modified,
            flags=re.IGNORECASE
        )
        print("✅ Changed headless=True to headless=False")

    # Case 2: Browser launch without headless parameter - add it
    elif re.search(r'\.chromium\.launch\(\s*\)', modified):
        modified = re.sub(
            r'\.chromium\.launch\(\s*\)',
            '.chromium.launch(headless=False)',
            modified
        )
        print("✅ Added headless=False to browser launch")

    # Case 3: Browser launch with other parameters but no headless
    elif re.search(r'\.chromium\.launch\([^)]*\)', modified) and 'headless' not in modified:
        modified = re.sub(
            r'(\.chromium\.launch\()',
            r'\1headless=False, ',
            modified
        )
        print("✅ Added headless=False to browser launch with existing parameters")

    return modified


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

def refine_pagination(original_code, next_page_selectors, next_page_examples,
                      page_counts, url, scraper_name, config, logger=None):
    """
    Use LLM to fix the advance_page() function when pagination doesn't produce new articles.
    Only called once.

    Args:
        original_code (str): The scraper code with broken pagination
        next_page_selectors (list): CSS selectors for next-page elements from page analysis
        next_page_examples (dict): HTML examples for each next-page selector
        page_counts (list): Cumulative article counts per page (e.g., [10, 10, 10] = broken)
        url (str): Target URL
        scraper_name (str): Name of the scraper
        config (dict): API config
        logger: Logger instance

    Returns:
        str: Refined scraper code with fixed advance_page()
    """
    prompts_dir = os.path.join(os.path.dirname(__file__), "prompts")
    env = Environment(loader=FileSystemLoader(prompts_dir))
    template = env.get_template("pagination_refinement_prompt.jinja2")

    refinement_prompt = template.render(
        original_code=original_code,
        next_page_selectors=json.dumps(next_page_selectors, indent=2),
        next_page_examples=format_selectors_with_examples(next_page_examples),
        page_counts=page_counts,
        url=url
    )

    client = OpenAI(api_key=config["api_key"])

    print("🔧 Refining pagination based on test feedback...")
    response = client.chat.completions.create(
        model=config["model"],
        messages=[
            {"role": "system", "content": "You are an expert at debugging Playwright-based web scrapers. Focus specifically on fixing the advance_page() function."},
            {"role": "user", "content": refinement_prompt}
        ],
        temperature=0.1,
        max_tokens=4000
    )

    refined_code = response.choices[0].message.content

    if logger:
        log_llm_interaction(logger, "Pagination Refinement", refinement_prompt, refined_code, config["model"])

    return clean_scraper_code(refined_code)


# Main function to generate a scraper for a given URL with testing and refinement
def generate_scraper(url, scraper_name, output_filename="scraper.py", content_config=None):
    if content_config is None:
        content_config = load_content_config()
    config = setup_config()
    logger = setup_logging(scraper_name)

    print(f"🔧 Set up LLM logger: {logger.name}")
    print(f"📁 Log file handlers: {[h.baseFilename if hasattr(h, 'baseFilename') else str(h) for h in logger.handlers]}")

    logger.info(f"Target URL: {url}")
    logger.info(f"Scraper name: {scraper_name}")

    print(f"Analyzing page structure for: {url}")
    page_analysis = analyze_page_structure(url, config, logger, content_config)
    scraper_prompt = make_prompt(url, scraper_name, page_analysis, content_config=content_config)

    print("Generating initial scraper code...")
    scraper_code = run_script_creator(scraper_prompt, config, logger)

    # Define working directory inside scrapers/<name> so results.json lives with the scraper
    sanitized_name = sanitize_filename(scraper_name) or 'scraper'
    output_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', 'scrapers', sanitized_name)
    )
    os.makedirs(output_dir, exist_ok=True)
    scraper_file_path = os.path.join(output_dir, output_filename)

    # Save page_analysis for later use in pagination debugging
    page_analysis_path = os.path.join(output_dir, "page_analysis.json")
    with open(page_analysis_path, 'w') as f:
        json.dump(page_analysis, f, indent=2)
    logger.info(f"Page analysis saved to: {page_analysis_path}")


    # Helper to write scraper code to disk
    def _save_scraper(code):
        with open(scraper_file_path, 'w') as f:
            f.write(code)

    _save_scraper(scraper_code)

    # ── Stepped debugging pipeline ──────────────────────────────────
    from scraper_generator.test import (
        TestContext, RequiredFunctionsTest, GetFirstPageTest,
        GetAllArticlesTest, ResultFileExistsTest, ResultFileReadableTest,
        DataStructureTest, ItemKeysTest, NonBlankValuesTest,
        DateFormatTest, UrlFormatTest,
    )
    from pathlib import Path

    def _run_test(test_cls, context):
        """Run a single test, print its status, return the instance."""
        t = test_cls()
        t.run(context)
        print(t.format_status())
        return t

    # -- Step 1: Check required functions + first page -----------------
    print("\n" + "="*60)
    print("STEP 1: TESTING SCRAPER STRUCTURE & FIRST PAGE")
    print("="*60)
    logger.info("STEP 1: TESTING SCRAPER STRUCTURE & FIRST PAGE")

    ctx = TestContext(Path(scraper_file_path))
    func_test = _run_test(RequiredFunctionsTest, ctx)

    if not func_test.passed:
        # Required functions missing — send to LLM for structural fix
        details = func_test.format_failure_details(ctx.data or [])
        print(f"\n❌ Required functions check failed:\n{details}")
        logger.info(f"Required functions failed: {details}")
        feedback = {
            'error_type': 'runtime_error',
            'error_message': f'Required functions/parameters missing:\n{details}',
            'stdout': '', 'stderr': '',
        }
        scraper_code = refine_scraper_with_feedback(
            scraper_code, feedback, url, scraper_name, config, logger)
        _save_scraper(scraper_code)
        # Rebuild context after rewrite
        ctx = TestContext(Path(scraper_file_path))
        func_test = _run_test(RequiredFunctionsTest, ctx)

    first_page_test = _run_test(GetFirstPageTest, ctx)

    # -- Step 2: Zero articles → try headless=False --------------------
    if first_page_test.passed is False:
        # Check if it was a zero-results issue (returned empty list, no crash)
        zero_results = any(
            'returned 0 items' in f.get('error', '')
            for f in first_page_test.failures
        )
        crashed = any(
            'returned 0 items' not in f.get('error', '')
            for f in first_page_test.failures
        )

        if zero_results and not crashed:
            print("\n" + "="*60)
            print("STEP 2: ZERO ARTICLES — TRYING headless=False")
            print("="*60)
            logger.info("STEP 2: Applying headless=False")

            scraper_code = apply_headless_false(scraper_code)
            _save_scraper(scraper_code)

            ctx = TestContext(Path(scraper_file_path))
            _run_test(RequiredFunctionsTest, ctx)
            first_page_test = _run_test(GetFirstPageTest, ctx)

    # -- Step 3: Runtime error → LLM refinement ------------------------
    if first_page_test.passed is False:
        error_details = "\n".join(
            f.get('error', str(f)) for f in first_page_test.failures
        )
        crashed = any(
            'returned 0 items' not in f.get('error', '')
            for f in first_page_test.failures
        )

        if crashed:
            print("\n" + "="*60)
            print("STEP 3: RUNTIME ERROR — LLM REFINEMENT")
            print("="*60)
            logger.info(f"STEP 3: Runtime error refinement. Errors:\n{error_details}")

            feedback = {
                'error_type': 'runtime_error',
                'error_message': error_details,
                'stdout': '', 'stderr': error_details,
            }
            scraper_code = refine_scraper_with_feedback(
                scraper_code, feedback, url, scraper_name, config, logger)
            _save_scraper(scraper_code)

            ctx = TestContext(Path(scraper_file_path))
            _run_test(RequiredFunctionsTest, ctx)
            first_page_test = _run_test(GetFirstPageTest, ctx)

    # -- Step 4: Other first-page issues → LLM refinement ---------------
    if first_page_test.passed is False:
        error_details = "\n".join(
            f.get('error', str(f)) for f in first_page_test.failures
        )
        print("\n" + "="*60)
        print("STEP 4: FIRST PAGE ISSUES — LLM REFINEMENT")
        print("="*60)
        logger.info(f"STEP 4: First page issues refinement. Errors:\n{error_details}")

        feedback = {
            'error_type': 'runtime_error',
            'error_message': error_details,
            'stdout': '', 'stderr': error_details,
        }
        scraper_code = refine_scraper_with_feedback(
            scraper_code, feedback, url, scraper_name, config, logger)
        _save_scraper(scraper_code)

        ctx = TestContext(Path(scraper_file_path))
        _run_test(RequiredFunctionsTest, ctx)
        first_page_test = _run_test(GetFirstPageTest, ctx)

    # -- Step 5: Pagination test ----------------------------------------
    if first_page_test.passed:
        print("\n" + "="*60)
        print("STEP 5: TESTING PAGINATION")
        print("="*60)
        logger.info("STEP 5: Testing pagination")

        pagination_test = _run_test(GetAllArticlesTest, ctx)

        if not pagination_test.passed and ctx.pagination_failed:
            print("❌ Pagination failed, attempting pagination-specific refinement...")
            logger.info(f"Pagination failed. Page counts: {ctx.pagination_page_counts}")

            with open(page_analysis_path, 'r') as f:
                saved_page_analysis = json.load(f)

            scraper_code = refine_pagination(
                scraper_code,
                saved_page_analysis.get("next_page_selectors", []),
                saved_page_analysis.get("next_page_examples", {}),
                ctx.pagination_page_counts,
                url, scraper_name, config, logger
            )
            _save_scraper(scraper_code)
            print(f"📁 Refined scraper saved to: {scraper_file_path}")
            logger.info(f"Pagination-refined scraper saved to: {scraper_file_path}")
    else:
        print("\n⚠️ Skipping pagination test — first page still failing.")
        logger.info("Skipping pagination test — first page still failing.")

    # -- Step 6: Validation tests (informational) -----------------------
    if ctx.data:
        print("\n" + "="*60)
        print("STEP 6: DATA VALIDATION")
        print("="*60)
        for test_cls in [ItemKeysTest, NonBlankValuesTest, DateFormatTest, UrlFormatTest]:
            _run_test(test_cls, ctx)

    # -- Final: Full test suite summary -----------------------------------
    print("\n" + "="*60)
    print("FINAL SUMMARY: FULL TEST SUITE")
    print("="*60)
    logger.info("FINAL SUMMARY: Running full test suite")

    from scraper_generator.test import run_tests_detailed
    final_results = run_tests_detailed(scraper_file_path)

    if final_results["all_passed"]:
        print("\n✅ All tests passed!")
        logger.info("✅ All tests passed!")
    else:
        print("\n❌ Some tests still failing.")
        logger.info("❌ Some tests still failing.")

    logger.info("Scraper generation completed")
    logger.info("="*80)

    # Force flush all handlers before returning
    for handler in logger.handlers:
        if hasattr(handler, 'flush'):
            handler.flush()

    print(f"LLM interactions logged to: {logger.handlers[0].baseFilename if logger.handlers else 'No handlers!'}")

    return scraper_code, final_results


def make_prompt(url, scraper_name, page_analysis, template_name="generic_template.jinja2", content_config=None, operator=None):
    """
    Generate a prompt for the LLM to implement a scraper based on browser-use analysis.

    Args:
        url (str): Target URL for the scraper
        scraper_name (str): Name of the scraper file without extension
        page_analysis (dict): Results from browser-use page analysis
        template_name (str): Name of the template file to use
        content_config (dict): Content type configuration
        operator (dict): Optional operator identity from operator.json

    Returns:
        str: Formatted prompt for the LLM
    """
    if content_config is None:
        content_config = load_content_config()
    if operator is None:
        operator = load_operator()

    # Build a descriptive user-agent string from operator fields
    if operator:
        parts = ["ScraperFactory/1.0"]
        if operator.get("organization"):
            parts.append(operator["organization"])
        elif operator.get("name"):
            parts.append(operator["name"])
        if operator.get("email"):
            parts.append(f"+mailto:{operator['email']}")
        if operator.get("message"):
            parts.append(operator["message"])
        user_agent = " ".join(parts)
    else:
        user_agent = ""

    fields = content_config["fields"]
    item_label = content_config.get("item_label", "article")
    content_type = content_config.get("content_type", "articles")
    content_description = content_config.get("description", "")
    has_date_field = any(f.get("type") == "date" for f in fields)

    # Construct the module path
    module_path = f"scrapers.{scraper_name}"

    # Load the template from the prompts directory
    prompts_dir = os.path.join(os.path.dirname(__file__), "prompts")
    env = Environment(loader=FileSystemLoader(prompts_dir))
    template = env.get_template("generic_template.jinja2")

    # Render the template with our variables
    rendered_template = template.render(
        url=url,
        org_name=scraper_name,
        generated_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        model="gpt-4o",
        module_path=module_path,
        content_type=content_type,
        item_label=item_label,
        fields=fields,
        has_date_field=has_date_field,
        user_agent=user_agent,
    )

    # Build per-field examples dict for the generation prompt
    field_examples = {
        field["name"]: format_selectors_with_examples(
            page_analysis.get(f"{field['name']}_examples", {})
        )
        for field in fields
    }

    # Load the scraper generation prompt template
    env_local = Environment(loader=FileSystemLoader(prompts_dir))
    prompt_template = env_local.get_template("scraper_generation_prompt.jinja2")

    # Render the prompt template with variables
    return prompt_template.render(
        rendered_template=rendered_template,
        content_type=content_type,
        content_description=content_description,
        item_label=item_label,
        fields=fields,
        has_date_field=has_date_field,
        item_examples=format_selectors_with_examples(page_analysis.get('item_examples', {})),
        field_examples=field_examples,
        next_page_examples=format_selectors_with_examples(page_analysis.get('next_page_examples', {})),
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



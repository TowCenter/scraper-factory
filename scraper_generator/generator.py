"""
Main logic for generating scrapers using ScrapegraphAI's ScriptCreatorGraph.
"""

import os
import time
import traceback
from jinja2 import Environment, FileSystemLoader
from .config import (
    OPENAI_API_KEY, 
    LLM_MODEL,
    SCRAPER_OUTPUT_DIR, 
    MAX_RETRIES,
    RETRY_DELAY,
    USE_VERBOSE,
    LOG_LEVEL, 
    LOG_FILE
)
from utils import (
    validate_url, 
    get_domain,
    setup_logging,
    sanitize_filename
)
from .utils import remove_module_docstring
from .prompts import make_prompt

# Ensure log directory exists if LOG_FILE has a directory
log_dir = os.path.dirname(LOG_FILE)
if log_dir and not os.path.exists(log_dir):
    os.makedirs(log_dir)

# Set up logging
logger = setup_logging(LOG_LEVEL, LOG_FILE)

def setup_graph_config():
    if not OPENAI_API_KEY:
        raise ValueError("OpenAI API key not set. Please set the OPENAI_API_KEY environment variable.")

    return {
        "llm": {
            "api_key": OPENAI_API_KEY,
            "model": LLM_MODEL
        },
        "library": "Playwright",
        "verbose": USE_VERBOSE,
        "headless": False,
    }

def clean_scraper_code(result):
    if result.startswith("```python"):
        result = result.replace("```python", "", 1)
        if result.endswith("```"):
            result = result[:-3]
    elif result.startswith("```"):
        result = result.replace("```", "", 1)
        if result.endswith("```"):
            result = result[:-3]
    return result.strip()

def generate_metadata_and_warning(org_name, url):
    template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
    env = Environment(loader=FileSystemLoader(template_dir))
    metadata_template = env.get_template('metadata_template.jinja2')
    metadata_content = metadata_template.render(
        org_name=org_name,
        generated_at=time.strftime('%Y-%m-%d %H:%M:%S'),
        url=url,
        model=LLM_MODEL
    )
    warning_template = env.get_template('warning_template.jinja2')
    warning_content = warning_template.render()
    return metadata_content + "\n\n" + warning_content + "\n\n"

def run_script_creator(scraper_prompt, url, graph_config):
    from scrapegraphai.graphs import ScriptCreatorGraph
    logger.info("Creating ScriptCreatorGraph instance...")
    script_creator = ScriptCreatorGraph(
        prompt=scraper_prompt,
        source=url,
        config=graph_config
    )
    logger.info("Running ScriptCreatorGraph...")
    result = script_creator.run()
    logger.info(f"ScriptCreatorGraph result type: {type(result)}")

    if result and isinstance(result, str):
        return clean_scraper_code(result)
    else:
        logger.warning(f"Unexpected response format from ScrapegraphAI: {result}")
        return None

def generate_scraper(url, org_name, template_name=None, filename="scraper.py"):
    logger.info(f"Generating scraper for {org_name} ({url})")

    if not validate_url(url):
        logger.error(f"Invalid URL: {url}")
        raise ValueError(f"Invalid URL: {url}")
        
    scraper_name = os.path.splitext(filename)[0]
    assert scraper_name, "Scraper name cannot be empty"
    logger.info(f"Creating scraper with name: {scraper_name}")

    if template_name:
        scraper_prompt = make_prompt(url, org_name, scraper_name, template_name)
        logger.info(f"Using custom template: {template_name}")
    else:
        scraper_prompt = make_prompt(url, org_name, scraper_name)

    graph_config = setup_graph_config()

    scraper_code = None
    for attempt in range(MAX_RETRIES):
        try:
            scraper_code = run_script_creator(scraper_prompt, url, graph_config)
            if scraper_code:
                logger.info(f"Successfully generated scraper for {org_name}")
                break
        except ImportError as e:
            logger.error("Failed to import ScriptCreatorGraph. Ensure scrapegraphai is installed.")
            raise e
        except Exception as e:
            error_details = traceback.format_exc()
            logger.warning(f"ScrapegraphAI call failed (attempt {attempt+1}/{MAX_RETRIES}): {str(e)}\n{error_details}")

        if attempt < MAX_RETRIES - 1:
            logger.info(f"Retrying in {RETRY_DELAY} seconds...")
            time.sleep(RETRY_DELAY)

    if not scraper_code:
        logger.error(f"Failed to generate scraper for {org_name} after {MAX_RETRIES} attempts")
        raise RuntimeError(f"Failed to generate scraper for {org_name}")

    metadata_and_warning = generate_metadata_and_warning(org_name, url)
    scraper_code = metadata_and_warning + remove_module_docstring(scraper_code)

    return scraper_code

def save_scraper(scraper_code, org_name, url, filename="scraper.py"):
    """
    Save the generated scraper code to file and update seed.json.
    """
    if not os.path.exists(SCRAPER_OUTPUT_DIR):
        os.makedirs(SCRAPER_OUTPUT_DIR)

    org_folder = sanitize_filename(org_name)
    org_dir = os.path.join(SCRAPER_OUTPUT_DIR, org_folder)
    if not os.path.exists(org_dir):
        os.makedirs(org_dir)

    file_path = os.path.join(org_dir, filename)
    with open(file_path, 'w') as f:
        f.write(scraper_code)

    logger.info(f"Saved scraper to {file_path}")

    return file_path

def get_scraper_metadata(scraper_code, org_name, url):
    domain = get_domain(url)
    has_pagination = 'pagination' in scraper_code.lower()
    has_playwright = 'playwright' in scraper_code.lower()
    error_handling = 'try:' in scraper_code

    return {
        'org_name': org_name,
        'url': url,
        'domain': domain,
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'features': {
            'has_pagination': has_pagination,
            'uses_playwright': has_playwright,
            'has_error_handling': error_handling,
        },
        'code_size': len(scraper_code),
    }

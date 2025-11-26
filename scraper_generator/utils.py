"""
Specialized utility functions for the scraper generator.
"""

import ast
import logging
from urllib.parse import urlparse
from .config import LOG_LEVEL, LOG_FILE, SCRAPER_OUTPUT_DIR
import os
import re
import time
from pathlib import Path

def setup_logging(log_level, log_file):
    """Configure logging for the module
    
    Args:
        log_level (str): Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file (str): Path to the log file
        
    Returns:
        logger: Configured logger instance
    """
    log_dir = os.path.dirname(log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # Remove all handlers associated with the root logger object.
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    logging.basicConfig(
        level=getattr(logging, log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )
    
    # Suppress excessive logging from requests and urllib3
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    
    return logging.getLogger(__name__)

# Initialize logger 
logger = setup_logging(LOG_LEVEL, LOG_FILE)

def remove_module_docstring(source: str) -> str:
    """Return *source* minus its top-level docstring (if one exists)."""
    tree = ast.parse(source)
    # A module docstring is always the very first statement and an ast.Expr
    if (tree.body and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)
            and isinstance(tree.body[0].value.value, str)):
        first_stmt = tree.body[0]
        # lineno/end_lineno are 1-based, end_lineno is inclusive
        start = first_stmt.lineno - 1
        end = first_stmt.end_lineno           # slice is exclusive, so no +1
        lines = source.splitlines()
        del lines[start:end]
        # remove any leading blank lines left behind
        while lines and not lines[0].strip():
            lines.pop(0)
        return "\n".join(lines)
    return source  # no module docstring found

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


def validate_url(url):
    """
    Validate that a URL is properly formed and accessible
    
    Args:
        url (str): URL to validate
        
    Returns:
        bool: True if valid, False otherwise
    """
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc]) and result.scheme in ['http', 'https']
    except Exception:
        return False

def get_domain(url):
    """
    Extract the domain from a URL
    
    Args:
        url (str): URL to extract domain from
        
    Returns:
        str: Domain name
    """
    try:
        parsed_url = urlparse(url)
        return parsed_url.netloc
    except Exception:
        return None

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

def check_org_scrapers_seed(org_name):
    """Get scrapers for a org from the seed_data.json file"""
    import json
    import os
    import logging
    logger = logging.getLogger(__name__)
    SCRAPERS_DIR = Path("scrapers")
    code_slug = sanitize_filename(org_name)

    seed_data_path = SCRAPERS_DIR / code_slug / "seed.json"
    try:
        if not os.path.exists(seed_data_path):
            logger.warning(f"Seed data file not found: {seed_data_path}")
            return []
        with open(seed_data_path, 'r', encoding='utf-8') as f:
            seed_data = json.load(f)
        if seed_data.get('name', '').lower() == org_name.lower():
            return seed_data.get('scrapers', [])
        return []
    except Exception as e:
        logger.warning(f"Error reading seed data: {str(e)}")
        return []

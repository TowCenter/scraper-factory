"""
Configuration settings for the Scraper Generator module.
"""

import os
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

# LLM API configuration (for ScrapegraphAI)
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
LLM_MODEL = os.getenv('LLM_MODEL', 'openai/gpt-4o-mini')  # Default to a smaller OpenAI model

# Output configuration
SCRAPER_OUTPUT_DIR = os.getenv('SCRAPER_OUTPUT_DIR', 'scrapers')

# Request configuration
REQUEST_TIMEOUT = int(os.getenv('REQUEST_TIMEOUT', '60'))  # seconds
MAX_RETRIES = int(os.getenv('MAX_RETRIES', '3'))
RETRY_DELAY = int(os.getenv('RETRY_DELAY', '5'))  # seconds

# Scraper configuration
USE_HEADLESS = os.getenv('USE_HEADLESS', 'True').lower() == 'true'
USE_VERBOSE = os.getenv('USE_VERBOSE', 'True').lower() == 'true'

# Logging
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
LOG_FILE = os.getenv('LOG_FILE', 'logs/other_generator.log')
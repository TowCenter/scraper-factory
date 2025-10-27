"""
Scraper Generator Module
-----------------------
This module handles the generation of web scrapers for article pages
using ScrapegraphAI.
"""

from .generator import generate_scraper, save_scraper, get_scraper_metadata

__all__ = ['generate_scraper', 'save_scraper', 'get_scraper_metadata']
"""
Articles Scraper for MPR News

Generated at: 2026-03-10 14:23:19
Target URL: https://www.mprnews.org/content/2026-03-02
Generated using: gpt-5-mini-2025-08-07
Content type: articles
Fields: title, date, url

"""

import json
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio

base_url = 'https://www.mprnews.org/content/2026-03-02'

# Scraper module path for tracking the source of scraped data
SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

# Operator user-agent (set in operator.json)
USER_AGENT = ''

class PlaywrightContext:
    """Context manager for Playwright browser sessions."""

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=False)
        context_kwargs = {'user_agent': USER_AGENT} if USER_AGENT else {}
        self.context = await self.browser.new_context(**context_kwargs)
        return self.context

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.browser.close()
        await self.playwright.stop()

async def scrape_page(page):
    """
    Extract article data from the current page.

    Parameters:
        page: Playwright page object

    Returns:
        List of dictionaries containing article data with keys:
        - title: Headline or title of the article
        - date: Publication date in YYYY-MM-DD format (optional — None if not found)
        - url: Link to the full article
        - scraper: module path for traceability
    """
    items = []

    # Use article.teaser as the primary container for each article
    article_nodes = await page.query_selector_all('article.teaser')
    for node in article_nodes:
        try:
            # Title: prefer h2.hdg-2 inside the teaser header or the link text
            title = None
            title_elem = await node.query_selector('h2.hdg-2')
            if title_elem:
                raw_title = await title_elem.text_content()
                if raw_title:
                    title = raw_title.strip()

            if not title:
                # fallback to link text in header
                link_in_header = await node.query_selector('.teaser_header a')
                if link_in_header:
                    raw_title = await link_in_header.text_content()
                    if raw_title:
                        title = raw_title.strip()

            # URL: anchor inside teaser_header (relative links like /story/...)
            url = None
            header_anchor = await node.query_selector('.teaser_header a')
            if header_anchor:
                href = await header_anchor.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(base_url, href)

            # Date: prefer datetime attribute on <time>, fallback to visible text parsed
            date = None
            time_elem = await node.query_selector('.teaser_meta time')
            if time_elem:
                datetime_attr = await time_elem.get_attribute('datetime')
                if datetime_attr:
                    try:
                        dt = parse(datetime_attr)
                        date = dt.date().isoformat()
                    except Exception:
                        date = None
                else:
                    # parse visible text content
                    text = await time_elem.text_content()
                    if text:
                        try:
                            dt = parse(text.strip())
                            date = dt.date().isoformat()
                        except Exception:
                            date = None

            # Only include items that have at least title and url (url required)
            if title and url:
                items.append({
                    'title': title,
                    'date': date,
                    'url': url,
                    'scraper': SCRAPER_MODULE_PATH,
                })
            else:
                # Attempt to still include items that may have missing optional fields,
                # but URL is required per specification. Skip if URL missing.
                if url and not title:
                    items.append({
                        'title': None,
                        'date': date,
                        'url': url,
                        'scraper': SCRAPER_MODULE_PATH,
                    })
        except Exception:
            # Robustness: skip problematic nodes rather than failing the entire scrape
            continue

    return items

async def advance_page(page):
    """
    Finds the next page button or link to navigate to the next page of articles.
    Clicks button or navigates to next page URL if found. Scroll load more button into view if not visible.
    Defaults to infinite scroll if no pagination found.

    Parameters:
        page: Playwright page object
    """
    # Try to find traditional pagination links first
    try:
        # Collect candidate anchors in common pagination container
        nav_anchors = await page.query_selector_all('.page-header-nav a, nav a, .pagination a, .pager a')
        candidates = []
        for a in nav_anchors:
            href = await a.get_attribute('href')
            text = await a.text_content()
            text_norm = (text or '').strip().lower()
            if href:
                candidates.append({'element': a, 'href': href, 'text': text_norm})

        # Rank candidates by preferred keywords
        preferred_order = ['next', 'more', 'load', 'older', 'previous', 'prev']
        chosen = None
        for keyword in preferred_order:
            for c in candidates:
                if keyword in c['text']:
                    chosen = c
                    break
            if chosen:
                break

        # If no keyword match, prefer first candidate that looks like an internal page link
        if not chosen and candidates:
            chosen = candidates[0]

        if chosen:
            next_url = urllib.parse.urljoin(base_url, chosen['href'])
            # Navigate to next page URL directly for reliability
            await page.goto(next_url)
            # Wait for network and DOM to stabilize
            try:
                await page.wait_for_load_state('load', timeout=8000)
            except Exception:
                # ignore timeout, continue
                pass
            return

    except Exception:
        # Continue to fallback infinite scroll if pagination fails
        pass

    # Fallback: infinite scroll behavior - scroll to bottom and wait for content to load
    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        # Give site a chance to lazy-load or append new items
        await page.wait_for_timeout(3000)
    except Exception:
        # If anything fails, do nothing and return
        await page.wait_for_timeout(1000)

async def get_first_page(base_url=base_url):
    """Fetch only the first page of articles."""
    async with PlaywrightContext() as context:
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)
        await page.goto(base_url)
        items = await scrape_page(page)
        await page.close()
        return items

async def get_all_articles(base_url=base_url, max_pages=100):
    """Fetch all articles from all pages."""

    async with PlaywrightContext() as context:
        items = []
        seen = set()
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)
        page_count = 0

        await page.goto(base_url)

        page_count = 0
        item_count = 0  # previous count
        new_item_count = 0  # current count

        try:
            while page_count < max_pages:
                page_items = await scrape_page(page)
                for item in page_items:
                    key = tuple(sorted((k, v) for k, v in item.items() if v is not None))
                    if key not in seen:
                        seen.add(key)
                        items.append(item)
                new_item_count = len(items)

                if new_item_count <= item_count:
                    break

                page_count += 1
                item_count = new_item_count

                await advance_page(page)

        except Exception as e:
            print(f"Error occurred while getting next page: {e}")


        await page.close()
        return items

async def main():
    """Main execution function."""
    all_items = await get_all_articles()

    # Save results to JSON
    result_path = os.path.join(os.path.dirname(__file__), 'result.json')
    with open(result_path, 'w') as f:
        json.dump(all_items, f, indent=2)
    print(f"Results saved to {result_path}")

if __name__ == "__main__":
    asyncio.run(main())
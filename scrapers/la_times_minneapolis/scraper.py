"""
Articles Scraper for LA Times Minneapolis

Generated at: 2026-03-12 14:49:28
Target URL: https://www.latimes.com/search?q=minneapolis&s=1
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

base_url = 'https://www.latimes.com/search?q=minneapolis&s=1'

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
        - date: Publication date in YYYY-MM-DD format
        - url: Link to the full article
        - scraper: module path for traceability
    """
    items = []

    # Wait briefly for search results container to appear (if present)
    try:
        await page.wait_for_selector('ul.search-results-module-results-menu', timeout=5000)
    except Exception:
        # Continue even if selector doesn't appear within timeout
        pass

    # Primary container selector for list items
    containers = await page.query_selector_all('ul.search-results-module-results-menu > li')

    # Fallback: sometimes articles may be direct children without the specific list wrapper
    if not containers:
        containers = await page.query_selector_all('li')

    for li in containers:
        try:
            # Title extraction: prefer headline link inside h3.promo-title
            title = None
            url = None
            title_anchor = await li.query_selector('h3.promo-title a')
            if not title_anchor:
                # fallback to any link with promo class
                title_anchor = await li.query_selector('a.link.promo-placeholder')
            if title_anchor:
                raw_title = await title_anchor.text_content()
                if raw_title:
                    title = raw_title.strip()
                href = await title_anchor.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(base_url, href)

            # Another fallback: h3.promo-title without anchor
            if not title:
                title_el = await li.query_selector('h3.promo-title')
                if title_el:
                    raw_title = await title_el.text_content()
                    if raw_title:
                        title = raw_title.strip()

            # Date extraction: look for time.promo-timestamp and its datetime attribute
            date = None
            time_el = await li.query_selector('time.promo-timestamp')
            if time_el:
                dt_attr = await time_el.get_attribute('datetime')
                if dt_attr:
                    try:
                        date = parse(dt_attr).date().isoformat()
                    except Exception:
                        # fallback to text inside time element
                        text = await time_el.text_content()
                        try:
                            date = parse(text).date().isoformat()
                        except Exception:
                            date = None
                else:
                    # If no datetime attribute, try parsing text
                    text = await time_el.text_content()
                    if text:
                        try:
                            date = parse(text).date().isoformat()
                        except Exception:
                            date = None

            # Ensure required fields are present; title and url are required per spec
            item = {
                'title': title if title else None,
                'date': date if date else None,
                'url': url if url else None,
                'scraper': SCRAPER_MODULE_PATH,
            }

            # Only append items that have at least a title and url
            if item['title'] and item['url']:
                items.append(item)

        except Exception:
            # Ignore malformed items and continue
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
    # Candidate selectors for "next page" links/buttons
    next_selectors = [
        '.search-results-module-next-page a',
        '.search-results-module-pagination a',
        'a[rel="next"]'
    ]

    for sel in next_selectors:
        try:
            el = await page.query_selector(sel)
            if not el:
                continue

            # Prefer href navigation if available
            href = await el.get_attribute('href')
            if href:
                next_url = urllib.parse.urljoin(page.url, href)
                try:
                    # Navigate directly to the href; this is simpler and avoids click-handling issues
                    await page.goto(next_url)
                    # Wait for network to be idle to ensure new content loads
                    try:
                        await page.wait_for_load_state('networkidle', timeout=10000)
                    except Exception:
                        # If networkidle times out, allow a small pause
                        await page.wait_for_timeout(1500)
                    return
                except Exception:
                    # If direct goto fails, attempt to click with navigation wait
                    try:
                        await asyncio.gather(page.wait_for_navigation(timeout=10000), el.click())
                        return
                    except Exception:
                        # fallback to continue to other selectors or infinite scroll
                        continue
            else:
                # No href — try clicking the element and waiting for navigation
                try:
                    await asyncio.gather(page.wait_for_navigation(timeout=10000), el.click())
                    return
                except Exception:
                    # If click doesn't navigate, try scrolling into view and clicking
                    try:
                        await el.scroll_into_view_if_needed()
                        await asyncio.gather(page.wait_for_navigation(timeout=10000), el.click())
                        return
                    except Exception:
                        continue
        except Exception:
            # If selector handling errors, try the next selector
            continue

    # Fallback: infinite scroll behavior
    try:
        prev_height = await page.evaluate("() => document.body.scrollHeight")
        await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        # Wait for potential lazy-loaded content to render
        await page.wait_for_timeout(3000)
        new_height = await page.evaluate("() => document.body.scrollHeight")
        # If no increase in height, try a couple more small scrolls to trigger load
        if new_height <= prev_height:
            for _ in range(2):
                await page.evaluate("() => window.scrollBy(0, window.innerHeight)")
                await page.wait_for_timeout(1500)
    except Exception:
        # If any errors during scroll fallback, just wait a bit and continue
        await page.wait_for_timeout(2000)
    return

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
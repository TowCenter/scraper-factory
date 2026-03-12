"""
Articles Scraper for Bethel University Clarion

Generated at: 2026-03-10 15:11:41
Target URL: https://thebuclarion.com/category/news/
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

base_url = 'https://thebuclarion.com/category/news/'

# Scraper module path for tracking the source of scraped data
SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

# Operator user-agent (set in operator.json)
USER_AGENT = ''

class PlaywrightContext:
    """Context manager for Playwright browser sessions."""

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch()
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
        - date: Publication date in YYYY-MM-DD format (optional — use None if not found)
        - url: Link to the full article
        - scraper: module path for traceability
    """
    items = []

    # Use a robust container selector that matches the examples provided.
    # This will match primary article tiles used on the category listing.
    container_selector = '.profile-rendered.catlist-tile, .catlist_tiles_wrap .profile-rendered'

    # Find all article containers on the page
    containers = await page.query_selector_all(container_selector)

    for container in containers:
        try:
            # Title and URL: prefer the anchor with class 'homeheadline', fallback to any H2 > a
            title_anchor = await container.query_selector('a.homeheadline, h2 a')
            title_text = None
            url = None
            if title_anchor:
                # use text_content() as requested
                title_text = await title_anchor.text_content()
                if title_text:
                    title_text = title_text.strip()
                href = await title_anchor.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(base_url, href.strip())

            # Date: look inside the container for time-wrapper or catlist-date
            date_el = await container.query_selector('.catlist-meta .time-wrapper, span.catlist-date')
            date_value = None
            if date_el:
                date_text = await date_el.text_content()
                if date_text:
                    date_text = date_text.strip()
                    # Attempt to parse the date; return YYYY-MM-DD or None on failure
                    try:
                        dt = parse(date_text, fuzzy=True)
                        date_value = dt.date().isoformat()
                    except Exception:
                        date_value = None

            # If required fields missing, still include entry but enforce required fields:
            # title and url are required per spec — skip if missing.
            if not title_text or not url:
                continue

            items.append({
                'title': title_text,
                'date': date_value,
                'url': url,
                'scraper': SCRAPER_MODULE_PATH,
            })

        except Exception:
            # Be resilient to malformed item HTML; skip problematic item
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
    # Prioritize explicit "Load more" / next button if present
    load_more_selectors = [
        '.sno-infinite-scroll-button-active',  # active variant
        '.sno-infinite-scroll-button',         # general variant
    ]

    for sel in load_more_selectors:
        try:
            btn = await page.query_selector(sel)
            if btn:
                try:
                    # Ensure button is in view and clickable
                    await btn.scroll_into_view_if_needed()
                    await btn.click()
                    # Wait for new content to load (XHR-driven sites may not navigate)
                    await page.wait_for_timeout(3000)
                    # Return after clicking load more
                    return
                except Exception:
                    # If click fails, continue to fallback behavior
                    pass
        except Exception:
            continue

    # Fallback to infinite scroll: scroll to bottom and wait for new content
    try:
        previous_height = await page.evaluate("() => document.body.scrollHeight")
        await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(3000)
        new_height = await page.evaluate("() => document.body.scrollHeight")
        # If height didn't change, attempt a few incremental scrolls to trigger lazy loading
        if new_height == previous_height:
            for _ in range(3):
                await page.evaluate("() => window.scrollBy(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1000)
                new_height = await page.evaluate("() => document.body.scrollHeight")
                if new_height > previous_height:
                    break
    except Exception:
        # If anything goes wrong with scrolling, do a short wait and return
        await page.wait_for_timeout(1000)
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
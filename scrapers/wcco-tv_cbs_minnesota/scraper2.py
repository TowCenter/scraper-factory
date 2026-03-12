"""
Articles Scraper for WCCO-TV  CBS Minnesota

Generated at: 2026-03-10 14:32:23
Target URL: https://www.cbsnews.com/minnesota/local-news/
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

base_url = 'https://www.cbsnews.com/minnesota/local-news/'

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
        - date: Publication date in YYYY-MM-DD format
        - url: Link to the full article
        - scraper: module path for traceability
    """
    items = []

    # Use article.item as the main container selector (robust and shown in examples)
    article_elements = await page.query_selector_all('article.item')
    for article in article_elements:
        try:
            # URL: prefer anchor with class item__anchor
            url = None
            anchor = await article.query_selector('a.item__anchor[href]')
            if anchor:
                href = await anchor.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(base_url, href.strip())

            # Title: try multiple selectors in order (use text_content to capture hidden content)
            title = None
            title_selectors = [
                '.item__title-wrapper h4',
                'h4.item__component-headline',
                'h4.item__hed'
            ]
            for sel in title_selectors:
                el = await article.query_selector(sel)
                if el:
                    raw = await el.text_content()
                    if raw:
                        cleaned = ' '.join(raw.split()).strip()
                        if cleaned:
                            title = cleaned
                            break

            # Fallback: sometimes the anchor itself contains the headline text
            if not title and anchor:
                raw_anchor_text = await anchor.text_content()
                if raw_anchor_text:
                    cleaned = ' '.join(raw_anchor_text.split()).strip()
                    if cleaned:
                        title = cleaned

            # Date: li.item__date (optional)
            date_str = None
            date_el = await article.query_selector('li.item__date')
            if date_el:
                raw_date = await date_el.text_content()
                if raw_date:
                    raw_date = ' '.join(raw_date.split()).strip()
                    try:
                        parsed = parse(raw_date, fuzzy=True)
                        date_str = parsed.date().isoformat()
                    except Exception:
                        date_str = None

            # Build item dict (ensure all required keys present)
            items.append({
                'title': title if title else None,
                'date': date_str,
                'url': url if url else None,
                'scraper': SCRAPER_MODULE_PATH,
            })

        except Exception:
            # On any per-item error, continue to next item (do not fail entire scrape)
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
    # Try "Show More" / view more link first (preferred)
    try:
        view_more = await page.query_selector('a.component__view-more')
        if view_more:
            # If view_more has an href, navigate to it (resolving relative URLs)
            href = await view_more.get_attribute('href')
            if href:
                next_url = urllib.parse.urljoin(base_url, href.strip())
                # If the link points to the same page pattern but different path, navigate
                try:
                    await page.goto(next_url)
                    # Wait for network to settle and a short delay for render
                    await page.wait_for_load_state('networkidle')
                    await page.wait_for_timeout(1000)
                    return
                except Exception:
                    # If navigation fails, try clicking as a fallback
                    try:
                        await view_more.scroll_into_view_if_needed()
                        await view_more.click()
                        await page.wait_for_timeout(2000)
                        return
                    except Exception:
                        pass
            else:
                # No href present: attempt click (some "load more" are AJAX)
                try:
                    await view_more.scroll_into_view_if_needed()
                    await view_more.click()
                    # Wait briefly for new content to load
                    await page.wait_for_timeout(2000)
                    return
                except Exception:
                    pass
    except Exception:
        # proceed to infinite scroll fallback below
        pass

    # If no explicit pagination found, perform infinite scroll fallback
    try:
        # Perform a few incremental scrolls to the bottom to trigger lazy loading
        previous_height = await page.evaluate("() => document.body.scrollHeight")
        for _ in range(3):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)
            new_height = await page.evaluate("() => document.body.scrollHeight")
            if new_height == previous_height:
                # little or no new content — break early
                break
            previous_height = new_height
        # final small wait to allow content to render
        await page.wait_for_timeout(1000)
    except Exception:
        # As a last resort, just wait a bit
        await page.wait_for_timeout(2000)


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
                    # Use tuple of sorted key-value pairs as a dedupe key (exclude None values consistently)
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
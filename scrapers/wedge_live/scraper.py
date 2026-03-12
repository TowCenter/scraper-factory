"""
Articles Scraper for Wedge LIVE

Generated at: 2026-03-10 14:42:10
Target URL: https://wedgelive.com/
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

base_url = 'https://wedgelive.com/'

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
        - date: Publication date in YYYY-MM-DD format (optional — None if not found)
        - url: Link to the full article
        - scraper: module path for traceability
    """
    items = []

    # Robust item selector using examples provided. Avoid overly-specific selectors.
    ITEM_SELECTOR = "article.post, article.hentry, #main article"
    # Title anchor inside article header
    TITLE_SELECTOR = "h3.entry-title a"
    # Date selector (prefer machine-readable datetime attribute)
    DATE_SELECTOR = "time.entry-date.published, .entry-meta time.entry-date, .entry-meta time.published"

    article_elements = await page.query_selector_all(ITEM_SELECTOR)
    for el in article_elements:
        try:
            # Title extraction
            title_el = await el.query_selector(TITLE_SELECTOR)
            title_value = None
            url_value = None
            if title_el:
                raw_title = await title_el.text_content()
                if raw_title:
                    title_value = raw_title.strip() or None
                href = await title_el.get_attribute("href")
                if href:
                    url_value = urllib.parse.urljoin(base_url, href.strip())

            # Date extraction
            date_value = None
            date_el = await el.query_selector(DATE_SELECTOR)
            if date_el:
                # Prefer datetime attribute if available
                datetime_attr = await date_el.get_attribute("datetime")
                date_text = None
                if datetime_attr:
                    date_text = datetime_attr.strip()
                else:
                    # fallback to visible text content
                    text = await date_el.text_content()
                    if text:
                        date_text = text.strip()

                if date_text:
                    try:
                        dt = parse(date_text)
                        # Normalize to YYYY-MM-DD
                        date_value = dt.date().isoformat()
                    except Exception:
                        # If parsing fails, set None (do not raise)
                        date_value = None

            # Ensure required fields are present
            # title and url are required by spec; if missing, skip this element
            if not title_value or not url_value:
                # Skip malformed article items preserving stability
                continue

            items.append({
                'title': title_value,
                'date': date_value,
                'url': url_value,
                'scraper': SCRAPER_MODULE_PATH,
            })

        except Exception:
            # Skip this item on any unexpected error to avoid breaking the whole page scrape
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
    # Candidate selectors for explicit pagination / next page links or load-more buttons
    NEXT_SELECTORS = [
        "a.next.page-numbers",
        ".navigation.pagination a.next.page-numbers",
        ".nav-links a.next.page-numbers",
        "a.load-more",
        "button.load-more",
        ".load-more a",
        ".load-more button",
    ]

    # Try to find and use a next-page/link or load-more control
    for sel in NEXT_SELECTORS:
        try:
            el = await page.query_selector(sel)
            if not el:
                continue

            # If it's an anchor with href, prefer navigation to that URL
            href = await el.get_attribute("href")
            if href:
                next_url = urllib.parse.urljoin(base_url, href)
                try:
                    await page.goto(next_url)
                    # wait for content to load
                    await page.wait_for_load_state('networkidle')
                except Exception:
                    # If navigation fails, attempt click as fallback
                    try:
                        await el.scroll_into_view_if_needed()
                        await el.click()
                        await page.wait_for_load_state('networkidle')
                    except Exception:
                        # last resort sleep to allow content to load
                        await page.wait_for_timeout(3000)
                return

            # If no href, attempt to click the control (button/link that loads more via JS)
            try:
                await el.scroll_into_view_if_needed()
                await el.click()
                # allow JS to fetch more items
                await page.wait_for_timeout(2000)
                return
            except Exception:
                # ignore and continue to other selectors or fallback
                continue

        except Exception:
            # ignore selector-specific errors and continue
            continue

    # Fallback: infinite scroll behavior
    # Attempt a few scroll passes; stop early if page height increases (new content loaded)
    try:
        previous_height = await page.evaluate("() => document.body.scrollHeight")
    except Exception:
        previous_height = None

    # Perform incremental scrolls with waits to allow lazy-loaded content to appear
    for _ in range(4):
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            # Wait a bit for potential lazy loading to occur
            await page.wait_for_timeout(2500)
            new_height = await page.evaluate("() => document.body.scrollHeight")
            if previous_height is None or (new_height and new_height > previous_height):
                # new content likely loaded; exit to allow scraper to parse additional items
                return
            previous_height = new_height
        except Exception:
            # If any error occurs during scrolling, just wait briefly and return
            await page.wait_for_timeout(2000)
            return

    # If no change detected after scrolling attempts, return (no further navigation)
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
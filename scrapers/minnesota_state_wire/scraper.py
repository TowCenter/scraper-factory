import json
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio

base_url = 'https://minnesotastatewire.com/stories/'

# Scraper module path for tracking the source of scraped data
SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

# Operator user-agent (set in operator.json)
USER_AGENT = ''

class PlaywrightContext:
    """Context manager for Playwright browser sessions."""

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        # Use headless default; can be tweaked if needed
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
        - date: Publication date in YYYY-MM-DD format or None
        - url: Link to the full article (absolute)
        - scraper: module path for traceability
    """
    items = []

    # Use a general item container selector observed on the page
    containers = await page.query_selector_all('.ultp-block-item')
    for c in containers:
        try:
            # Title and URL: prefer the title anchor inside the block title
            title_el = await c.query_selector('h3.ultp-block-title a, .ultp-block-title a')
            title = None
            url = None
            if title_el:
                raw_title = await title_el.text_content()
                if raw_title:
                    title = raw_title.strip() or None
                href = await title_el.get_attribute('href')
                if href:
                    # Make absolute relative to the current page
                    url = urllib.parse.urljoin(page.url or base_url, href.strip())

            # Date extraction: best-effort. Try multiple common patterns.
            date_value = None
            # 1) Look for <time> element and prefer datetime attribute
            time_el = await c.query_selector('time')
            if time_el:
                datetime_attr = await time_el.get_attribute('datetime')
                if datetime_attr:
                    date_text = datetime_attr.strip()
                else:
                    date_text = (await time_el.text_content() or '').strip()
                if date_text:
                    try:
                        dt = parse(date_text, fuzzy=True)
                        date_value = dt.strftime('%Y-%m-%d')
                    except Exception:
                        date_value = None
            # 2) If not found, look for meta with datePublished inside container
            if date_value is None:
                meta_el = await c.query_selector('meta[itemprop="datePublished"], meta[name="date"], meta[property="article:published_time"]')
                if meta_el:
                    meta_content = await meta_el.get_attribute('content') or await meta_el.get_attribute('datetime')
                    if meta_content:
                        try:
                            dt = parse(meta_content.strip(), fuzzy=True)
                            date_value = dt.strftime('%Y-%m-%d')
                        except Exception:
                            date_value = None
            # 3) Fallback: search for any element with a class that contains "date" or "time" or dynamic content fields that might contain dates
            if date_value is None:
                fallback_el = await c.query_selector('[class*="date"], [class*="Date"], [class*="time"], .ultp-dynamic-content-field-dc, .ultp-block-meta')
                if fallback_el:
                    fallback_text = (await fallback_el.text_content() or '').strip()
                    if fallback_text:
                        try:
                            dt = parse(fallback_text, fuzzy=True)
                            date_value = dt.strftime('%Y-%m-%d')
                        except Exception:
                            date_value = None

            # Ensure required fields; title and url are required per spec
            if not title and not url:
                # Skip entries with no meaningful data
                continue

            item = {
                'title': title,
                'date': date_value,
                'url': url,
                'scraper': SCRAPER_MODULE_PATH,
            }
            items.append(item)
        except Exception:
            # Defensive: don't let one broken item stop scraping
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
    # Try to find explicit "next" pagination link(s)
    try:
        # Prefer dedicated next-page selector observed on the site
        next_link = await page.query_selector('.ultp-next-page-numbers a, a[rel="next"]')

        # If not directly found, scan pagination links for one whose text contains "next" or similar
        if not next_link:
            pagination_links = await page.query_selector_all('.ultp-pagination a, .ultp-next-page-numbers a, nav a')
            for pl in pagination_links:
                try:
                    txt = (await pl.text_content() or '').strip().lower()
                    if 'next' in txt or '>' in txt or 'more' in txt:
                        next_link = pl
                        break
                except Exception:
                    continue

        if next_link:
            # Prefer to get href and navigate to it; fallback to click if no href.
            href = await next_link.get_attribute('href')
            if href:
                next_url = urllib.parse.urljoin(page.url or base_url, href.strip())
                # Navigate to the next page URL and wait for content to load
                await page.goto(next_url)
                # Wait until new article items are available or network is idle
                try:
                    await page.wait_for_selector('.ultp-block-item', timeout=7000)
                except Exception:
                    await page.wait_for_load_state('networkidle')
                return
            else:
                # If no href, try clicking the element (may trigger client-side pagination)
                try:
                    await next_link.scroll_into_view_if_needed()
                    await next_link.click()
                    try:
                        await page.wait_for_selector('.ultp-block-item', timeout=7000)
                    except Exception:
                        await page.wait_for_load_state('networkidle')
                    return
                except Exception:
                    # Fall through to infinite scroll fallback below
                    pass

    except Exception:
        # On any unexpected error, fall back to infinite scroll behavior
        pass

    # Fallback: attempt infinite scroll / load more by scrolling to the bottom a few times
    try:
        # Perform a few scroll attempts to trigger lazy-loading or "load more"
        for _ in range(3):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            # Give time for new content to load
            await page.wait_for_timeout(3000)
        # After scrolling attempts, wait for potential new items or network idle
        try:
            await page.wait_for_selector('.ultp-block-item', timeout=3000)
        except Exception:
            await page.wait_for_load_state('networkidle')
    except Exception:
        # Ignore any errors here; advance_page should not raise to outer loop
        pass

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
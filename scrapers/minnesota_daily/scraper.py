import json
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio

base_url = 'https://mndaily.com/category/city/'

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

async def _parse_date(text):
    """
    Parse a date-like string and return YYYY-MM-DD or None on failure.
    Uses fuzzy parsing to tolerate prefixes like 'Published'.
    """
    if not text:
        return None
    text = text.strip()
    # Remove common prefixes that might confuse parser
    # e.g., "Published  March 8, 2026"
    for prefix in ("Published", "published", "Updated", "posted", "Posted"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    try:
        dt = parse(text, fuzzy=True)
        return dt.date().isoformat()
    except Exception:
        return None

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

    # Choose a reliable container selector observed on the page.
    # 'div.catlist-panel-inner' is the inner wrapper that contains headline, date, and link.
    containers = await page.query_selector_all("div.catlist-panel-inner")

    for container in containers:
        try:
            # Title & URL: prefer anchor with class 'homeheadline'
            anchor = await container.query_selector("a.homeheadline")
            if not anchor:
                # fallback to a headline anchor inside an h2
                anchor = await container.query_selector("h2 a")
            title_text = None
            url = None
            if anchor:
                raw_title = await anchor.text_content()
                if raw_title:
                    title_text = raw_title.strip()
                href = await anchor.get_attribute("href")
                if href:
                    url = urllib.parse.urljoin(page.url, href.strip())
            # If no anchor found, try to locate an h2 text as a last resort
            if not title_text:
                h2 = await container.query_selector("h2")
                if h2:
                    raw_title = await h2.text_content()
                    if raw_title:
                        title_text = raw_title.strip()

            # Date: look for the time-wrapper inside catlist-date
            date_node = await container.query_selector("span.catlist-date .time-wrapper, span.catlist-date, span.time-wrapper")
            date_val = None
            if date_node:
                raw_date = await date_node.text_content()
                date_val = await _parse_date(raw_date)
            else:
                # Some items might not have date in the expected spot; try to find any 'Published' string
                text_blob = await container.text_content()
                if text_blob and "Published" in text_blob:
                    date_val = await _parse_date(text_blob)

            # Required fields: title and url (url may be None if not found)
            item = {
                'title': title_text or None,
                'date': date_val,
                'url': url or None,
                'scraper': SCRAPER_MODULE_PATH,
            }
            items.append(item)
        except Exception:
            # Skip malformed container but continue processing others
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
    # First attempt: find a 'next' link (common pattern on this site)
    try:
        # Prefer explicit next-link selectors
        next_link = await page.query_selector("div.category-pagination a.next, a.next")
        if next_link:
            href = await next_link.get_attribute("href")
            if href:
                next_url = urllib.parse.urljoin(page.url, href.strip())
                # Navigate to the next page URL
                await page.goto(next_url)
                # Wait for network to be quiet and a short delay for rendering
                try:
                    await page.wait_for_load_state('networkidle', timeout=5000)
                except Exception:
                    # ignore timeout and proceed
                    pass
                await page.wait_for_timeout(1000)
                return
            else:
                # If no href, attempt to click the element (e.g., button-like link)
                try:
                    await next_link.scroll_into_view_if_needed()
                    await next_link.click()
                    try:
                        await page.wait_for_load_state('networkidle', timeout=5000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(1000)
                    return
                except Exception:
                    # If click fails, fall back to infinite scroll
                    pass

        # Second attempt: pagination via page number links (ol.wp-paginate)
        # Try to find an anchor whose aria-label indicates next page number (e.g., "Go to page 2")
        pagelinks = await page.query_selector_all("ol.wp-paginate a.page")
        if pagelinks:
            # Heuristic: find the link with the highest numeric title greater than current page
            current_url = page.url
            # Try to click the first pagelink that points to a different URL than current
            for pl in pagelinks:
                href = await pl.get_attribute("href")
                if href:
                    full = urllib.parse.urljoin(page.url, href.strip())
                    if full != current_url:
                        await page.goto(full)
                        try:
                            await page.wait_for_load_state('networkidle', timeout=5000)
                        except Exception:
                            pass
                        await page.wait_for_timeout(1000)
                        return

        # If no explicit pagination found, fallback to infinite scroll
        # Scroll to bottom and wait for additional content to load
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        # Give the page some time to load more items (if it supports infinite scroll)
        await page.wait_for_timeout(3000)
        return

    except Exception:
        # On unexpected errors, fallback to infinite scroll behavior
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(3000)
        except Exception:
            pass
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
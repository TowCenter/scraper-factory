import json
import os
import re
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio

"""
Articles Scraper for Alliance for Metropolitan Stability

Generated at: 2026-03-12 11:24:13
Target URL: http://thealliancetc.org/news-events/news/
Generated using: gpt-5-mini-2025-08-07
Content type: articles
Fields: title, date, url

"""

base_url = 'http://thealliancetc.org/news-events/news/'

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
        - date: Publication date in YYYY-MM-DD format or None
        - url: Link to the full article
        - scraper: module path for traceability
    """
    items = []

    # Use robust container selector that captures each post block
    containers = await page.query_selector_all('div.post-block, div[id^="post-"]')
    for container in containers:
        try:
            # Title: prefer h3 a, fallback to .title a
            title_handle = await container.query_selector('h3 a, .title a, a[rel="bookmark"]')
            title = None
            url = None
            if title_handle:
                title = (await title_handle.text_content()) or ''
                title = title.strip() if title else None
                href = await title_handle.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(page.url, href.strip())

            # Date: look for elements in postmetadata; prefer the specific h6/em
            date_handle = await container.query_selector('.postmetadata h6, .postmetadata em, .postmetadata')
            date_value = None
            if date_handle:
                raw = await date_handle.text_content() or ''
                raw = raw.strip()
                # Some text like "Posted on: March 5, 2026" -> remove leading "Posted on:" if present
                raw = re.sub(r'^[Pp]ost(ed by|ed on):?\s*', '', raw)
                # Try to parse a date from the raw string
                try:
                    parsed = parse(raw, fuzzy=True)
                    date_value = parsed.date().isoformat()
                except Exception:
                    date_value = None

            # Ensure required fields; skip if title or url missing
            if not title and not url:
                continue

            items.append({
                'title': title,
                'date': date_value,
                'url': url,
                'scraper': SCRAPER_MODULE_PATH,
            })

        except Exception:
            # Defensive: skip malformed container but continue processing others
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
        pagination_links = await page.query_selector_all('div.pagination a[href*="/page/"], nav a[href*="/page/"], a[href*="/page/"]')
        # Build list of (href, page_num)
        links = []
        for link in pagination_links:
            href = await link.get_attribute('href')
            if not href:
                continue
            # normalize
            full = urllib.parse.urljoin(page.url, href)
            m = re.search(r'/page/(\d+)[/]*', full)
            if m:
                try:
                    num = int(m.group(1))
                    links.append((full, num, link))
                except Exception:
                    continue

        # Determine current page number (0 for base)
        current_num = 0
        mcur = re.search(r'/page/(\d+)[/]*', page.url)
        if mcur:
            try:
                current_num = int(mcur.group(1))
            except Exception:
                current_num = 0

        # Choose the smallest page number greater than current (i.e., next)
        next_candidates = [t for t in links if t[1] > current_num]
        if next_candidates:
            next_candidates.sort(key=lambda x: x[1])
            next_href, next_num, link_handle = next_candidates[0]
            # Try clicking if the element is visible and clickable; otherwise navigate directly
            try:
                await link_handle.scroll_into_view_if_needed()
                await link_handle.click()
                # wait for load
                await page.wait_for_load_state('networkidle', timeout=10000)
                return
            except Exception:
                try:
                    await page.goto(next_href)
                    await page.wait_for_load_state('networkidle', timeout=10000)
                    return
                except Exception:
                    # fall through to infinite scroll fallback
                    pass

        # If we found pagination links but none seem to be next (e.g., only prev),
        # attempt to find a link labeled 'Next', 'Older', '>' or '»'
        for text_label in ['next', 'older', '>', '»', 'more']:
            for link in pagination_links:
                txt = (await link.text_content() or '').strip().lower()
                if text_label in txt:
                    href = await link.get_attribute('href')
                    if href:
                        full = urllib.parse.urljoin(page.url, href)
                        try:
                            await link.scroll_into_view_if_needed()
                            await link.click()
                            await page.wait_for_load_state('networkidle', timeout=10000)
                            return
                        except Exception:
                            try:
                                await page.goto(full)
                                await page.wait_for_load_state('networkidle', timeout=10000)
                                return
                            except Exception:
                                pass

    except Exception:
        # If anything fails, fall back to infinite scroll below
        pass

    # Fallback: infinite scroll behavior (scroll to bottom and wait for new content)
    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        # small pause to allow lazy loading
        await page.wait_for_timeout(3000)
    except Exception:
        await page.wait_for_timeout(3000)

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
                    # Create deterministic key for deduplication
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
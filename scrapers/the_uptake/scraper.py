"""
Articles Scraper for The UpTake

Generated at: 2026-03-10 14:54:45
Target URL: https://theuptake.org/
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

base_url = 'https://theuptake.org/'

# Scraper module path for tracking the source of scraped data
SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

# Operator user-agent (set in operator.json)
USER_AGENT = ''

class PlaywrightContext:
    """Context manager for Playwright browser sessions."""

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        # Launch headless browser (default). Adjust args if needed.
        self.browser = await self.playwright.chromium.launch()
        context_kwargs = {'user_agent': USER_AGENT} if USER_AGENT else {}
        self.context = await self.browser.new_context(**context_kwargs)
        return self.context

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.browser.close()
        await self.playwright.stop()

async def _safe_text(el):
    """Return text_content() or None if element is missing."""
    if not el:
        return None
    try:
        txt = await el.text_content()
        if txt is None:
            return None
        return txt.strip()
    except Exception:
        return None

async def _safe_attr(el, name):
    """Return attribute value or None if element or attribute missing."""
    if not el:
        return None
    try:
        return await el.get_attribute(name)
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
        - url: Link to the full article
        - scraper: module path for traceability
    """
    items = []

    # Use the post grid item selector as primary container
    # It is resilient and present inside .fl-post-grid blocks
    try:
        containers = await page.query_selector_all('.fl-post-grid-post')
    except Exception:
        containers = []

    for container in containers:
        try:
            # Title and url: prefer the headline anchor inside h2.fl-post-grid-title
            title_anchor = await container.query_selector('h2.fl-post-grid-title a')
            if not title_anchor:
                # Fallback to any anchor inside .fl-post-grid-text or container heading
                title_anchor = await container.query_selector('.fl-post-grid-text a[href], a[title]')
            title_text = await _safe_text(title_anchor)
            href = await _safe_attr(title_anchor, 'href')

            if href:
                url = urllib.parse.urljoin(base_url, href.strip())
            else:
                url = None

            # Date: prefer meta[itemprop="datePublished"] inside the container, then visible span
            date_meta = await container.query_selector('meta[itemprop="datePublished"]')
            date_value = None
            if date_meta:
                content = await _safe_attr(date_meta, 'content')
                if content:
                    date_value = content.strip()
            if not date_value:
                # Try the visible date string span
                date_span = await container.query_selector('span.fl-post-grid-date')
                date_text = await _safe_text(date_span)
                if date_text:
                    date_value = date_text

            # Normalize date to YYYY-MM-DD if possible
            parsed_date = None
            if date_value:
                try:
                    # parse accepts many date formats
                    dt = parse(date_value, fuzzy=True)
                    parsed_date = dt.date().isoformat()
                except Exception:
                    parsed_date = None

            # title and url are required fields; skip if missing
            if not title_text or not url:
                continue

            items.append({
                'title': title_text,
                'date': parsed_date,
                'url': url,
                'scraper': SCRAPER_MODULE_PATH,
            })
        except Exception:
            # Skip problematic container but continue processing others
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
    # Try to find conventional next-page selectors first (if any)
    next_selectors = [
        'a[rel="next"]',
        'a.next',
        'a.pagination-next',
        '.pagination a.next',
        'a[aria-label*="Next"]',
        'a[aria-label*="next"]',
        'a.more-link',
        'a.load-more',
        'button.load-more',
        '.load-more',
        '.show-more',
        '.nav-next a',
    ]

    for sel in next_selectors:
        try:
            el = await page.query_selector(sel)
            if not el:
                continue
            # If element has href, navigate to it
            href = await _safe_attr(el, 'href')
            if href:
                next_url = urllib.parse.urljoin(await page.evaluate("location.href"), href)
                try:
                    # navigate to the next page URL
                    await page.goto(next_url)
                    # small pause to allow content to render
                    await page.wait_for_timeout(1500)
                    return
                except Exception:
                    # If goto fails, attempt click fallback
                    try:
                        await el.scroll_into_view_if_needed()
                        await el.click()
                        await page.wait_for_timeout(1500)
                        return
                    except Exception:
                        continue
            else:
                # No href: attempt to click (e.g., button that loads more)
                try:
                    await el.scroll_into_view_if_needed()
                    # Some buttons are dynamically handled without navigation
                    await el.click()
                    # wait a short while for new content to load
                    await page.wait_for_timeout(2000)
                    return
                except Exception:
                    continue
        except Exception:
            continue

    # No explicit next selectors found or none worked -> perform infinite scroll fallback
    # Scroll to bottom and wait for new content. Try a few incremental attempts.
    try:
        max_attempts = 3
        for attempt in range(max_attempts):
            previous_height = await page.evaluate("document.body.scrollHeight")
            # Scroll to bottom
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            # Wait for potential async content load
            await page.wait_for_timeout(3000 + attempt * 1000)
            new_height = await page.evaluate("document.body.scrollHeight")
            if new_height > previous_height:
                # New content likely loaded; return to let caller scrape again
                return
        # If we reach here, no new content appeared. As a final attempt, nudge by small scrolls
        await page.evaluate("""
            (function(){
                window.scrollBy(0, -200);
                window.scrollBy(0, 200);
            })()
        """)
        await page.wait_for_timeout(1000)
        return
    except Exception:
        # Silent fallback; nothing else to do
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
                    # Use title + url + date as dedupe key; normalize None -> sentinel
                    key = (item.get('title'), item.get('url'), item.get('date'))
                    if key not in seen:
                        seen.add(key)
                        items.append(item)
                new_item_count = len(items)

                # If no new items discovered this iteration, stop paging
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
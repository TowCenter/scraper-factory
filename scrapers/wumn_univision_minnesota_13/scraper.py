import json
import os
import re
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio

base_url = 'https://www.univisionminnesota.com/noticias/'

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

async def _safe_text(element, selector):
    """Return text_content() for selector under element or None if missing."""
    try:
        child = await element.query_selector(selector)
        if not child:
            return None
        txt = await child.text_content()
        if txt is None:
            return None
        return txt.strip()
    except Exception:
        return None

async def _safe_attr(element, selector, attr='href'):
    """Return attribute value for selector under element or None if missing."""
    try:
        child = await element.query_selector(selector)
        if not child:
            return None
        val = await child.get_attribute(attr)
        return val.strip() if val else None
    except Exception:
        return None

def _format_date(date_str):
    """Parse a date string and return YYYY-MM-DD or None if parse fails."""
    if not date_str:
        return None
    try:
        # dateutil can parse spanish month abbreviations like 'mar' -> March
        dt = parse(date_str, fuzzy=True, dayfirst=False)
        return dt.date().isoformat()
    except Exception:
        # try to clean common patterns like '09 mar 2026' -> keep as-is and attempt parse again
        try:
            cleaned = re.sub(r'\s+', ' ', date_str.strip())
            dt = parse(cleaned, fuzzy=True)
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
        - url: Link to the full article
        - scraper: module path for traceability
    """
    items = []

    # We handle two primary container types observed on the page:
    # 1) div.gdl-blog-grid : main article cards with h2.blog-title, div.blog-date a
    # 2) div.recent-post-widget : sidebar/recent widgets with h4.recent-post-widget-title a
    # We'll collect both. Use page.url as base for resolving relative links.
    page_base = page.url or base_url

    # Get all gdl-blog-grid article nodes
    try:
        grid_nodes = await page.query_selector_all('div.gdl-blog-grid')
    except Exception:
        grid_nodes = []

    for node in grid_nodes:
        # Title: h2.blog-title a
        title = await _safe_text(node, 'h2.blog-title a')
        # URL: try h2.blog-title a first, else image link inside blog-media-wrapper
        href = await _safe_attr(node, 'h2.blog-title a', 'href')
        if not href:
            href = await _safe_attr(node, 'div.blog-media-wrapper a', 'href')
        url = urllib.parse.urljoin(page_base, href) if href else None

        # Date: div.blog-date a (may contain '09 mar 2026')
        date_text = await _safe_text(node, 'div.blog-date a')
        date = _format_date(date_text)

        # Only include items with at least a title and url
        if title and url:
            items.append({
                'title': title,
                'date': date,
                'url': url,
                'scraper': SCRAPER_MODULE_PATH,
            })

    # Get recent-post-widget nodes (sidebar small items)
    try:
        recent_nodes = await page.query_selector_all('div.recent-post-widget')
    except Exception:
        recent_nodes = []

    for node in recent_nodes:
        title = await _safe_text(node, 'h4.recent-post-widget-title a')
        href = await _safe_attr(node, 'div.recent-post-widget-thumbnail a', 'href')
        if not href:
            href = await _safe_attr(node, 'h4.recent-post-widget-title a', 'href')
        url = urllib.parse.urljoin(page_base, href) if href else None

        # recent widgets may not show dates; return None if not found
        date_text = await _safe_text(node, 'div.recent-post-widget-date')
        date = _format_date(date_text)

        if title and url:
            items.append({
                'title': title,
                'date': date,
                'url': url,
                'scraper': SCRAPER_MODULE_PATH,
            })

    # Deduplicate items by url/title combination while preserving order
    seen = set()
    deduped = []
    for it in items:
        key = (it.get('url'), it.get('title'))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)

    return deduped

async def advance_page(page):
    """
    Finds the next page button or link to navigate to the next page of articles.
    Clicks button or navigates to next page URL if found. Scroll load more button into view if not visible.
    Defaults to infinite scroll if no pagination found.

    Parameters:
        page: Playwright page object
    """
    # Strategy:
    # 1. Try to find a "Next" link inside pagination (div.gdl-pagination a with text 'Next' or similar)
    # 2. If not found, inspect pagination anchors with href containing '/noticias/page/' and attempt to find the next higher page number
    # 3. If none found, perform infinite scroll fallback (scroll to bottom + wait for new content)
    try:
        # 1) direct next button by visible text
        next_btn = await page.query_selector('div.gdl-pagination a:has-text("Next")')
        if not next_btn:
            # Also try 'Next ›' or the Spanish variant might still be 'Next'
            next_btn = await page.query_selector('div.gdl-pagination a:has-text("›")')
        if next_btn:
            # If the element has href, navigate to it; otherwise click it.
            href = await next_btn.get_attribute('href')
            if href:
                next_url = urllib.parse.urljoin(page.url or base_url, href)
                await page.goto(next_url)
                # wait for network and content
                await page.wait_for_load_state('networkidle')
                await page.wait_for_timeout(1000)
                return
            else:
                try:
                    await next_btn.scroll_into_view_if_needed()
                    await next_btn.click()
                    await page.wait_for_load_state('networkidle')
                    await page.wait_for_timeout(1000)
                    return
                except Exception:
                    pass

        # 2) Find numeric pagination links and choose the next page relative to current URL
        anchors = await page.query_selector_all('div.gdl-pagination a[href*="/noticias/page/"]')
        hrefs = []
        for a in anchors:
            href = await a.get_attribute('href')
            if not href:
                continue
            full = urllib.parse.urljoin(page.url or base_url, href)
            hrefs.append(full)

        # Parse current page number from current URL
        current_url = page.url or base_url
        m = re.search(r'/page/(\d+)/', current_url)
        try:
            current_page_num = int(m.group(1)) if m else 1
        except Exception:
            current_page_num = 1

        # Build map of page numbers to hrefs
        page_map = {}
        for h in hrefs:
            mm = re.search(r'/page/(\d+)/', h)
            if mm:
                try:
                    pnum = int(mm.group(1))
                    page_map[pnum] = h
                except Exception:
                    continue

        if page_map:
            # find the smallest page number greater than current_page_num
            higher = [p for p in page_map.keys() if p > current_page_num]
            if higher:
                next_p = min(higher)
                next_url = page_map[next_p]
                await page.goto(next_url)
                await page.wait_for_load_state('networkidle')
                await page.wait_for_timeout(1000)
                return

        # 3) Fallback infinite scroll: scroll to bottom and wait for new content
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        # Give time for lazy load / infinite scroll to fetch content
        await page.wait_for_timeout(3000)
        return

    except Exception:
        # If anything fails, fallback to infinite scroll behavior
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
                    # Use url and title as dedupe key parts, include date if present
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
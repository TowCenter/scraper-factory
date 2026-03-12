import json
import os
import re
from datetime import datetime, timedelta
from dateutil.parser import parse as dt_parse
import urllib.parse
import asyncio

from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API

base_url = 'https://tusmotimes.com/category/local-news/'

# Scraper module path for tracking the source of scraped data
SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

# Operator user-agent (set in operator.json)
USER_AGENT = ''

class PlaywrightContext:
    """Context manager for Playwright browser sessions."""

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        # Launch headless by default; options could be extended if needed
        self.browser = await self.playwright.chromium.launch()
        context_kwargs = {'user_agent': USER_AGENT} if USER_AGENT else {}
        self.context = await self.browser.new_context(**context_kwargs)
        return self.context

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.browser.close()
        await self.playwright.stop()

# Combined item selector covering the common article containers observed
ITEM_CONTAINER_SELECTOR = "li.mvp-blog-story-wrap, .mvp-feat1-list-out, .mvp-widget-feat2-right-cont"

async def parse_date_text(text):
    """
    Parse a date string into YYYY-MM-DD. Handles absolute dates (via dateutil)
    and simple relative phrases like '2 days ago', '5 hours ago', 'Yesterday'.
    Returns a string in YYYY-MM-DD format or None if parsing fails.
    """
    if not text:
        return None
    txt = text.strip()
    # Try absolute parse first
    try:
        dt = dt_parse(txt, fuzzy=True)
        return dt.date().isoformat()
    except Exception:
        pass

    # Normalize text
    txt_lower = txt.lower()

    now = datetime.utcnow()

    # Handle 'yesterday' and 'today'
    if "yesterday" in txt_lower:
        return (now - timedelta(days=1)).date().isoformat()
    if "today" in txt_lower:
        return now.date().isoformat()

    # Match patterns like "2 days ago", "5 hours ago", "30 minutes ago"
    m = re.search(r'(\d+)\s*(day|days|hour|hours|minute|minutes|month|months|week|weeks)\s*ago', txt_lower)
    if m:
        value = int(m.group(1))
        unit = m.group(2)
        if unit.startswith('day'):
            dt = now - timedelta(days=value)
        elif unit.startswith('hour'):
            dt = now - timedelta(hours=value)
        elif unit.startswith('minute'):
            dt = now - timedelta(minutes=value)
        elif unit.startswith('week'):
            dt = now - timedelta(weeks=value)
        elif unit.startswith('month'):
            # approximate month as 30 days
            dt = now - timedelta(days=30 * value)
        else:
            dt = now
        return dt.date().isoformat()

    # If nothing matched, return None
    return None

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

    # Find all article containers using a combined selector to capture
    # multiple layout variations on the page.
    containers = await page.query_selector_all(ITEM_CONTAINER_SELECTOR)

    for c in containers:
        # Title: prefer an H2 inside the container
        title = None
        try:
            title_el = await c.query_selector('h2')
            if title_el:
                raw_title = await title_el.text_content()
                if raw_title:
                    title = raw_title.strip()
        except Exception:
            title = None

        # URL: try several fallbacks:
        # 1) anchor directly inside the container (e.g., li.mvp-blog-story-wrap > a)
        # 2) anchor wrapping the H2
        # 3) any anchor inside the container
        url = None
        try:
            # first try H2 > a
            link_el = await c.query_selector('h2 a[href]')
            if not link_el:
                # then try a[href] directly under container
                link_el = await c.query_selector('a[href]')
            if link_el:
                href = await link_el.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(base_url, href.strip())
        except Exception:
            url = None

        # Date: look for .mvp-cd-date or .mvp-cat-date-wrap .mvp-cd-date
        date_str = None
        try:
            date_el = await c.query_selector('.mvp-cd-date')
            if date_el:
                raw_date = await date_el.text_content()
                if raw_date:
                    date_str = raw_date.strip()
        except Exception:
            date_str = None

        # Parse date string into YYYY-MM-DD or None
        parsed_date = await parse_date_text(date_str) if date_str else None

        # Only include items that have at least a title and url
        if title and url:
            items.append({
                'title': title,
                'date': parsed_date,
                'url': url,
                'scraper': SCRAPER_MODULE_PATH,
            })

    return items

async def advance_page(page):
    """
    Finds the next page button or link to navigate to the next page of articles.
    Clicks button or navigates to next page URL if found. Scroll load more button into view if not visible.
    Defaults to infinite scroll if no pagination found.

    Strategy:
    1. Prefer a "Load more" button with class .mvp-inf-more-but (click it and wait for new content).
    2. Then look for numbered pagination links: .pagination a.inactive (navigate to first such link).
    3. Fallback to infinite scroll: scroll to bottom and wait for new content.
    """
    # Helper to get current count of article containers
    async def current_item_count():
        els = await page.query_selector_all(ITEM_CONTAINER_SELECTOR)
        return len(els)

    # 1) Try "Load more" button
    try:
        load_more = await page.query_selector('a.mvp-inf-more-but')
        if load_more:
            # Scroll into view then click
            try:
                await load_more.scroll_into_view_if_needed()
            except Exception:
                pass
            # Record current count and click; then wait for more items or a short timeout
            before = await current_item_count()
            try:
                await load_more.click()
            except Exception:
                # some sites use JS handlers and require evaluation click
                try:
                    await page.evaluate('(el) => el.click()', load_more)
                except Exception:
                    pass
            # Wait until new items appear or timeout
            for _ in range(12):  # up to ~6 seconds (12 * 0.5)
                await asyncio.sleep(0.5)
                after = await current_item_count()
                if after > before:
                    return
            # If clicking didn't yield new items, continue to other strategies
    except Exception:
        pass

    # 2) Try numbered pagination links (traditional page navigation).
    # Find pagination anchors with class 'inactive' (these appear to point to other pages).
    try:
        pag_links = await page.query_selector_all('.pagination a.inactive')
        # Choose the first pagination link whose href is different from current URL
        if pag_links:
            current_href = page.url
            chosen_href = None
            for a in pag_links:
                href = await a.get_attribute('href')
                if href:
                    full = urllib.parse.urljoin(base_url, href.strip())
                    if full and full != current_href:
                        chosen_href = full
                        break
            if chosen_href:
                await page.goto(chosen_href)
                # give the page some time to load content
                await page.wait_for_load_state('networkidle')
                return
    except Exception:
        pass

    # 3) Try pagination container that may contain anchors (fallback)
    try:
        nav_anchors = await page.query_selector_all('.mvp-nav-links a[href]')
        if nav_anchors:
            current_href = page.url
            chosen = None
            for a in nav_anchors:
                href = await a.get_attribute('href')
                if href:
                    full = urllib.parse.urljoin(base_url, href.strip())
                    if full != current_href:
                        chosen = full
                        break
            if chosen:
                await page.goto(chosen)
                await page.wait_for_load_state('networkidle')
                return
    except Exception:
        pass

    # 4) Fallback to infinite scroll: scroll to bottom and wait for new content
    try:
        before = await current_item_count()
        # Perform multiple incremental scrolls to allow lazy loading
        for _ in range(6):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(1.0)
            after = await current_item_count()
            if after > before:
                return
        # Final wait in case content loads after a short delay
        await asyncio.sleep(2.0)
    except Exception:
        # Last-resort sleep to throttle scraping loop
        await asyncio.sleep(2.0)
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

        await page.goto(base_url)
        page_count = 0
        item_count = 0  # previous count
        new_item_count = 0  # current count

        try:
            while page_count < max_pages:
                page_items = await scrape_page(page)
                for item in page_items:
                    # Create a deduplication key using title and url primarily
                    key = (item.get('title'), item.get('url'))
                    if key not in seen:
                        seen.add(key)
                        items.append(item)
                new_item_count = len(items)

                # If no new items were added in this iteration, stop pagination
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
    with open(result_path, 'w', encoding='utf-8') as f:
        json.dump(all_items, f, indent=2, ensure_ascii=False)
    print(f"Results saved to {result_path}")

if __name__ == "__main__":
    asyncio.run(main())
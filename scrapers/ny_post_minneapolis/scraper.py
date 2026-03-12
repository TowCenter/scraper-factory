import json
import os
import re
import asyncio
import urllib.parse
from dateutil.parser import parse
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API

"""
Articles Scraper for NY Post Minneapolis

Generated at: 2026-03-12 14:44:53
Target URL: https://nypost.com/search/minneapolis/?orderby=date&order=desc
Generated using: gpt-5-mini-2025-08-07
Content type: articles
Fields: title, date, url

"""

base_url = 'https://nypost.com/search/minneapolis/?orderby=date&order=desc'

# Scraper module path for tracking the source of scraped data
SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

# Operator user-agent (set in operator.json)
USER_AGENT = ''

class PlaywrightContext:
    """Context manager for Playwright browser sessions."""

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        # launch headless by default; adjust if debugging
        self.browser = await self.playwright.chromium.launch()
        context_kwargs = {'user_agent': USER_AGENT} if USER_AGENT else {}
        self.context = await self.browser.new_context(**context_kwargs)
        return self.context

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.browser.close()
        await self.playwright.stop()

async def _clean_text(text):
    """Normalize whitespace and return stripped text or None."""
    if text is None:
        return None
    t = re.sub(r'\s+', ' ', text).strip()
    return t if t else None

async def _parse_date(text):
    """
    Parse a date from a blob of text using dateutil.parse with fuzzy matching.
    Return YYYY-MM-DD or None on failure.
    """
    if not text:
        return None
    try:
        dt = parse(text, fuzzy=True, default=None)
        # dateutil.parse with default=None would raise TypeError in some versions,
        # so handle if dt is None
        if not dt:
            # try without default
            dt = parse(text, fuzzy=True)
        return dt.date().isoformat()
    except Exception:
        # try to extract a likely date substring (e.g., "March 12, 2026")
        m = re.search(r'([A-Za-z]+\s+\d{1,2},\s*\d{4})', text)
        if m:
            try:
                dt = parse(m.group(1), fuzzy=True)
                return dt.date().isoformat()
            except Exception:
                return None
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

    # Prefer the top-level container for search results; fallback to story item
    container_selectors = ['.search-results__story', '.story.story--archive', '.story--archive']
    containers = []
    for sel in container_selectors:
        found = await page.query_selector_all(sel)
        if found:
            containers = found
            break

    # If none of the container selectors matched, try a more general story selector
    if not containers:
        containers = await page.query_selector_all('.story')

    for container in containers:
        try:
            # Title: prefer the headline link
            a = await container.query_selector('h3.story__headline a')
            if not a:
                # sometimes the anchor is directly under the container (image link or other)
                a = await container.query_selector('a[class^="postid-"]')
            title = None
            url = None
            if a:
                title_text = await a.text_content()
                title = await _clean_text(title_text)
                href = await a.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(base_url, href)
            else:
                # fallback: try headline element without anchor
                h = await container.query_selector('h3.story__headline')
                if h:
                    title_text = await h.text_content()
                    title = await _clean_text(title_text)
                # try to find any link inside container
                any_a = await container.query_selector('a')
                if any_a:
                    href = await any_a.get_attribute('href')
                    if href:
                        url = urllib.parse.urljoin(base_url, href)

            # Date: read the byline/meta element text and parse a date from it
            date_el = await container.query_selector('.meta.meta--byline, .meta--byline')
            date_val = None
            if date_el:
                date_text = await date_el.text_content()
                date_val = await _parse_date(date_text)

            # Only include items that have at least a title and url
            if title and url:
                items.append({
                    'title': title,
                    'date': date_val,
                    'url': url,
                    'scraper': SCRAPER_MODULE_PATH,
                })
        except Exception:
            # Skip malformed container but continue scraping others
            continue

    return items

async def advance_page(page):
    """
    Finds the next page button or link to navigate to the next page of articles.
    Clicks button or navigates to next page URL if found. Scrolls load more button into view if not visible.
    Defaults to infinite scroll if no pagination found.

    Parameters:
        page: Playwright page object
    """
    # Try "See More Stories" / load more button link first
    next_selectors = ['.search-results__more a.button--solid', 'a.button.button--solid', 'a.button--solid']
    next_el = None
    next_href = None

    for sel in next_selectors:
        next_el = await page.query_selector(sel)
        if next_el:
            next_href = await next_el.get_attribute('href')
            break

    if next_el and next_href:
        # Prefer clicking if element is visible; otherwise navigate to href
        try:
            # Scroll into view in case it's not visible
            await next_el.scroll_into_view_if_needed()
            # Use click with a navigation wait
            # Some anchors navigate via normal link; clicking is fine and preserves history
            await asyncio.gather(
                next_el.click(),
                page.wait_for_load_state('networkidle', timeout=15000),
            )
            return
        except Exception:
            try:
                # fallback: navigate directly
                await page.goto(urllib.parse.urljoin(base_url, next_href))
                try:
                    await page.wait_for_load_state('networkidle', timeout=15000)
                except Exception:
                    await page.wait_for_timeout(2000)
                return
            except Exception:
                # if navigation fails, fall back to infinite scroll behavior below
                pass

    # If no next page button found or navigation failed, perform infinite scroll fallback
    # Scroll to bottom, wait for potential new content to load
    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        # Give time for lazy loading / "load more on scroll" behavior
        await page.wait_for_timeout(3000)
    except Exception:
        # ignore scroll errors
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
        try:
            await page.wait_for_load_state('networkidle', timeout=15000)
        except Exception:
            await page.wait_for_timeout(2000)

        page_count = 0
        item_count = 0  # previous count
        new_item_count = 0  # current count

        try:
            while page_count < max_pages:
                page_items = await scrape_page(page)
                for item in page_items:
                    # Create a dedupe key from title+url+date (consistent ordering)
                    key = (item.get('title'), item.get('url'), item.get('date'))
                    if key not in seen:
                        seen.add(key)
                        items.append(item)
                new_item_count = len(items)

                # If no new items were added, stop iterating
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
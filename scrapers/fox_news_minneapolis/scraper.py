"""
Articles Scraper for Fox News Minneapolis

Generated at: 2026-03-12 15:16:23
Target URL: https://www.foxnews.com/search-results/search#q=minneapolis
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

base_url = 'https://www.foxnews.com/search-results/search#q=minneapolis'

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

    # primary article container selector based on examples
    article_selectors = 'article.article'

    article_elements = await page.query_selector_all(article_selectors)

    for article in article_elements:
        # Title: prefer h2.title a
        title = None
        url = None
        date_str = None

        title_anchor = await article.query_selector('h2.title a')
        if not title_anchor:
            # fallback: any link inside a title element or first link in article
            title_anchor = await article.query_selector('h2.title')
        if title_anchor:
            try:
                # use text_content() per instructions
                title_text = await title_anchor.text_content()
                if title_text:
                    title = title_text.strip() or None
            except Exception:
                title = None

        # URL: try title link first, then first <a href> inside article
        url_candidate = None
        if title_anchor:
            href = await title_anchor.get_attribute('href')
            if href:
                url_candidate = href
        if not url_candidate:
            first_link = await article.query_selector('a[href]')
            if first_link:
                href = await first_link.get_attribute('href')
                if href:
                    url_candidate = href

        if url_candidate:
            # Normalize relative URLs
            try:
                url = urllib.parse.urljoin(page.url or base_url, url_candidate)
            except Exception:
                url = url_candidate

        # Date: look for span.time inside article
        try:
            date_el = await article.query_selector('span.time')
            if date_el:
                date_text = await date_el.text_content()
                if date_text:
                    date_text = date_text.strip()
                    # Skip empty strings
                    if date_text:
                        try:
                            # parse date, allow partial dates like "February 6"
                            parsed = parse(date_text, fuzzy=True)
                            date_str = parsed.strftime('%Y-%m-%d')
                        except Exception:
                            # If parse fails, set None
                            date_str = None
        except Exception:
            date_str = None

        # Required fields: title and url. If missing, still include entry but with None values.
        item = {
            'title': title,
            'date': date_str,
            'url': url,
            'scraper': SCRAPER_MODULE_PATH,
        }

        items.append(item)

    return items

async def advance_page(page):
    """
    Finds the next page button or link to navigate to the next page of articles.
    Clicks button or navigates to next page URL if found. Scroll load more button into view if not visible.
    Defaults to infinite scroll if no pagination found.

    Parameters:
        page: Playwright page object
    """
    # Try to find a "Load More" button as shown in examples
    try:
        # Prefer an anchor inside .button.load-more
        load_more_container = await page.query_selector('div.button.load-more')
        if load_more_container:
            anchor = await load_more_container.query_selector('a')
            if anchor:
                href = await anchor.get_attribute('href')
                # If href provided, navigate to it
                if href:
                    next_url = urllib.parse.urljoin(page.url or base_url, href)
                    try:
                        await page.goto(next_url)
                        await page.wait_for_load_state('networkidle')
                        return
                    except Exception:
                        # fallback to clicking if navigation fails
                        pass
                # If no href or navigation failed, try clicking
                try:
                    # ensure element is in view then click
                    await anchor.scroll_into_view_if_needed()
                    await anchor.click()
                    # wait a bit for more content to load
                    await page.wait_for_timeout(2000)
                    return
                except Exception:
                    # If click fails, proceed to fallback
                    pass

        # Also try to find a standalone anchor that looks like load more
        load_more_anchor = await page.query_selector('div.button.load-more a, a.load-more, a[aria-label="Load More"], a:has-text("Load More")')
        if load_more_anchor:
            href = await load_more_anchor.get_attribute('href')
            if href:
                try:
                    next_url = urllib.parse.urljoin(page.url or base_url, href)
                    await page.goto(next_url)
                    await page.wait_for_load_state('networkidle')
                    return
                except Exception:
                    pass
            try:
                await load_more_anchor.scroll_into_view_if_needed()
                await load_more_anchor.click()
                await page.wait_for_timeout(2000)
                return
            except Exception:
                pass

    except Exception:
        # If anything goes wrong with the "load more" flow, fall back to infinite scroll
        pass

    # Fallback: infinite scroll
    try:
        # Attempt to detect new articles after scrolling; do several incremental scrolls
        article_selector = 'article.article'
        prev_count = len(await page.query_selector_all(article_selector))
        max_scrolls = 6
        for _ in range(max_scrolls):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            # allow time for lazy-loaded content to fetch
            await page.wait_for_timeout(2000)
            curr_count = len(await page.query_selector_all(article_selector))
            if curr_count > prev_count:
                # new content loaded
                return
            prev_count = curr_count
        # Final wait as a last attempt
        await page.wait_for_timeout(1500)
    except Exception:
        # As a last resort, do a simple sleep to let dynamic loading happen
        await page.wait_for_timeout(2000)
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
                    # Create a stable key for deduplication (ignore None values differences)
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
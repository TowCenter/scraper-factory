"""
Articles Scraper for Workday Magazine

Generated at: 2026-03-10 14:40:36
Target URL: https://workdaymagazine.org/category/minnesota/
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

base_url = 'https://workdaymagazine.org/category/minnesota/'

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

    # Choose robust article container selector (examples show either class 'hentry' or 'post')
    article_selectors = 'article.hentry, article.post'

    article_els = await page.query_selector_all(article_selectors)

    for art in article_els:
        try:
            # Title and URL: prefer the headline link inside h2.entry-title
            title_el = await art.query_selector('h2.entry-title a')
            if not title_el:
                # fallback: any anchor directly under article with href
                title_el = await art.query_selector('a[href]')
            if not title_el:
                # required field missing; skip this article
                continue

            raw_title = await title_el.text_content()
            title = raw_title.strip() if raw_title else None
            href = await title_el.get_attribute('href')
            url = urllib.parse.urljoin(base_url, href) if href else None

            # Date: try datetime attribute first, then text content
            date_el = await art.query_selector('time.entry-date, time.pubdate, time')
            date_iso = None
            if date_el:
                datetime_attr = await date_el.get_attribute('datetime')
                date_text = None
                if datetime_attr:
                    # sometimes datetime includes timezone; parse then format to YYYY-MM-DD
                    try:
                        dt = parse(datetime_attr)
                        date_iso = dt.date().isoformat()
                    except Exception:
                        date_iso = None
                if not date_iso:
                    # fallback to visible text content
                    date_text = await date_el.text_content()
                    if date_text:
                        try:
                            dt = parse(date_text.strip())
                            date_iso = dt.date().isoformat()
                        except Exception:
                            date_iso = None

            # Ensure required fields exist
            if not title or not url:
                # Skip malformed items missing required fields
                continue

            items.append({
                'title': title,
                'date': date_iso,
                'url': url,
                'scraper': SCRAPER_MODULE_PATH,
            })
        except Exception:
            # Skip this article on any unexpected error and continue with others
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
    # Strategy:
    # 1. Try rel="next" link (standard)
    # 2. Try "Load more" area: div.load-more a or nav#nav-below a
    # 3. Try any anchor in nav#nav-below (common WordPress older/newer posts)
    # 4. Fallback to infinite scroll (scroll to bottom and wait)

    try:
        # 1) rel="next"
        next_el = await page.query_selector('a[rel="next"]')
        if next_el:
            href = await next_el.get_attribute('href')
            if href:
                await page.goto(href)
                await page.wait_for_load_state('networkidle')
                return
            # if no href, attempt click
            try:
                await next_el.scroll_into_view_if_needed()
                await next_el.click()
                await page.wait_for_load_state('networkidle')
                return
            except Exception:
                pass

        # 2) Load more selectors
        load_more_el = await page.query_selector('div.load-more a, nav#nav-below a')
        if load_more_el:
            href = await load_more_el.get_attribute('href')
            if href:
                await page.goto(href)
                await page.wait_for_load_state('networkidle')
                return
            try:
                await load_more_el.scroll_into_view_if_needed()
                await load_more_el.click()
                await page.wait_for_load_state('networkidle')
                return
            except Exception:
                pass

        # 3) Try to find any meaningful pagination link inside nav#nav-below (prefer hrefs that look like /page/X/)
        nav_links = await page.query_selector_all('nav#nav-below a[href]')
        if nav_links:
            for a in nav_links:
                href = await a.get_attribute('href')
                if not href:
                    continue
                # prefer links containing '/page/' or text like 'Load' or 'Older'
                text = (await a.text_content() or '').strip().lower()
                if '/page/' in href or 'load' in text or 'older' in text or 'next' in text:
                    await page.goto(urllib.parse.urljoin(base_url, href))
                    await page.wait_for_load_state('networkidle')
                    return
            # fallback to first nav link if nothing matched
            first_href = await nav_links[0].get_attribute('href')
            if first_href:
                await page.goto(urllib.parse.urljoin(base_url, first_href))
                await page.wait_for_load_state('networkidle')
                return

    except Exception:
        # If any of the above fail, fallback to infinite scroll below
        pass

    # Fallback: infinite scroll attempt
    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        # Wait a short time for lazy-load or new content to appear
        await page.wait_for_timeout(3000)
        # Another small scroll to ensure dynamic loaders trigger
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(2000)
    except Exception:
        # If scrolling fails silently, just return to let caller decide
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
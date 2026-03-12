"""
Articles Scraper for Breakthrough News

Generated at: 2026-03-12 11:28:53
Target URL: https://breakthroughnews.org/category/united-states/
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

base_url = 'https://breakthroughnews.org/category/united-states/'

# Scraper module path for tracking the source of scraped data
SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

# Operator user-agent (set in operator.json)
USER_AGENT = ''

class PlaywrightContext:
    """Context manager for Playwright browser sessions."""

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=False)
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

    # Use robust container selector (covers both examples)
    container_locator = page.locator('.row.postlist.post_type_post, .postlist.post_type_post')

    count = await container_locator.count()
    for i in range(count):
        node = container_locator.nth(i)

        # Title: prefer the h3 inside the posttitle link
        title = None
        try:
            title_ele = node.locator('a.posttitle h3')
            if await title_ele.count() > 0:
                raw = await title_ele.nth(0).text_content()
                if raw:
                    title = raw.strip()
            else:
                # fallback to any element with class posttitle
                title_ele2 = node.locator('a.posttitle, .posttitle')
                if await title_ele2.count() > 0:
                    raw2 = await title_ele2.nth(0).text_content()
                    if raw2:
                        title = raw2.strip()
        except Exception:
            title = None

        # URL: from the posttitle link's href
        url = None
        try:
            link_ele = node.locator('a.posttitle[href]').first
            if await node.locator('a.posttitle[href]').count() > 0:
                href = await node.locator('a.posttitle[href]').nth(0).get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(base_url, href.strip())
        except Exception:
            url = None

        # Date: parse into YYYY-MM-DD, return None on failure
        date_val = None
        try:
            date_ele = node.locator('span.postdate, .cat_date .postdate')
            if await date_ele.count() > 0:
                raw_date = await date_ele.nth(0).text_content()
                if raw_date:
                    raw_date = raw_date.strip()
                    try:
                        parsed = parse(raw_date, fuzzy=True)
                        date_val = parsed.date().isoformat()
                    except Exception:
                        date_val = None
        except Exception:
            date_val = None

        # Only include items that at least have title and url (as required)
        # Still include item if date is None
        if title and url:
            items.append({
                'title': title,
                'date': date_val,
                'url': url,
                'scraper': SCRAPER_MODULE_PATH,
            })

    return items

async def advance_page(page):
    """
    Finds the next page button or link to navigate to the next page of articles.
    Clicks button or navigates to next page URL if found. Scroll load more button into view if not visible.
    Defaults to infinite scroll if no pagination found.

    Parameters:
        page: Playwright page object
    """
    # Prioritize "Load More" button(s) as indicated in the page examples.
    loadmore_selector = '.misha_loadmore.btn, #load-more-container .misha_loadmore'
    try:
        loadmore_locator = page.locator(loadmore_selector)
        lm_count = await loadmore_locator.count()
        if lm_count > 0:
            # Use the first load-more element
            lm = loadmore_locator.nth(0)
            try:
                # If not visible, try scrolling it into view
                is_vis = await lm.is_visible()
                if not is_vis:
                    handle = await lm.element_handle()
                    if handle:
                        try:
                            await handle.scroll_into_view_if_needed()
                        except Exception:
                            # Fallback: page-level scroll
                            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                            await page.wait_for_timeout(500)
                # Click the load more button
                await lm.click()
                # Wait for new content to load (give time for AJAX)
                await page.wait_for_timeout(2500)
                return
            except Exception:
                # If clicking the button fails, fall back to infinite scroll below
                pass

        # If no explicit load-more found or clicking failed, fallback to infinite scroll
        # Perform one controlled scroll to bottom and wait for new content
        previous_height = await page.evaluate("document.body.scrollHeight")
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(3000)
        new_height = await page.evaluate("document.body.scrollHeight")
        # If height didn't change, wait a bit longer to allow JS loading
        if new_height == previous_height:
            await page.wait_for_timeout(1500)

    except Exception:
        # As a safe fallback, attempt a simple scroll and short wait
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)
        except Exception:
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
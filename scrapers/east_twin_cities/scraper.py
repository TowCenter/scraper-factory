"""
Articles Scraper for East Twin Cities

Generated at: 2026-03-12 11:27:27
Target URL: https://easttwincities.com/category/local-government/
Generated using: gpt-5-mini-2025-08-07
Content type: articles
Fields: title, date, url

"""

import json
import os
import re
import urllib.parse
import asyncio
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
from datetime import datetime

base_url = 'https://easttwincities.com/category/local-government/'

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
        - date: Publication date in YYYY-MM-DD format (or None)
        - url: Link to the full article
        - scraper: module path for traceability
    """
    items = []

    # Use the article container selector observed in the page examples
    article_selector = 'article.ultp-block-item'

    article_elements = await page.query_selector_all(article_selector)

    for article in article_elements:
        try:
            # Title & URL: prefer the title link inside the h3.ultp-block-title
            title_el = await article.query_selector('h3.ultp-block-title a')
            title_text = None
            url = None
            if title_el:
                title_text = (await title_el.text_content()) or ''
                href = await title_el.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(page.url, href)

            # Date: span with class ultp-block-date
            date_el = await article.query_selector('span.ultp-block-date')
            date_str = None
            date_val = None
            if date_el:
                date_str = (await date_el.text_content()) or ''
                date_str = date_str.strip()
                if date_str:
                    try:
                        # Use dateutil to parse multiple human-readable date formats.
                        dt = parse(date_str, fuzzy=True)
                        date_val = dt.date().isoformat()  # YYYY-MM-DD
                    except Exception:
                        date_val = None

            # Fallbacks: ensure required fields exist
            title_final = title_text.strip() if title_text else None
            url_final = url if url else None

            # Construct item only if title and url are present (url and title required by spec)
            item = {
                'title': title_final,
                'date': date_val,
                'url': url_final,
                'scraper': SCRAPER_MODULE_PATH,
            }

            items.append(item)
        except Exception:
            # Skip malformed article entries but continue scraping others
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
    try:
        # 1) Primary: a 'Next' pagination control often wrapped in a list item
        next_sel = await page.query_selector('li.ultp-next-page-numbers a')
        if next_sel:
            href = await next_sel.get_attribute('href')
            if href:
                next_url = urllib.parse.urljoin(page.url, href)
                # Navigate directly to the next page URL for reliability
                await page.goto(next_url)
                await page.wait_for_load_state('networkidle')
                return

            # If no href, attempt to click it
            try:
                await next_sel.scroll_into_view_if_needed()
                await asyncio.gather(
                    page.wait_for_navigation(wait_until='networkidle'),
                    next_sel.click()
                )
                return
            except Exception:
                # fall through to pagination anchors
                pass

        # 2) Secondary: pagination anchors under ul.ultp-pagination with /page/ in href
        anchors = await page.query_selector_all('ul.ultp-pagination a[href*="/page/"]')
        if anchors:
            # Prefer anchor whose text includes "next"
            next_href = None
            for a in anchors:
                try:
                    text = (await a.text_content() or '').strip().lower()
                    if 'next' in text:
                        href = await a.get_attribute('href')
                        if href:
                            next_href = urllib.parse.urljoin(page.url, href)
                            break
                except Exception:
                    continue

            if next_href:
                await page.goto(next_href)
                await page.wait_for_load_state('networkidle')
                return

            # If no explicit "Next", attempt to pick the anchor with page number > current page
            # Determine current page number from URL
            def extract_page_num(u):
                m = re.search(r'/page/(\d+)/?', u)
                if m:
                    try:
                        return int(m.group(1))
                    except Exception:
                        return None
                return None

            current_num = extract_page_num(page.url) or 1
            candidate = None
            candidate_num = None
            for a in anchors:
                try:
                    href = await a.get_attribute('href')
                    if not href:
                        continue
                    abs_href = urllib.parse.urljoin(page.url, href)
                    num = extract_page_num(abs_href)
                    if num and num > current_num:
                        if candidate_num is None or num < candidate_num:
                            candidate = abs_href
                            candidate_num = num
                except Exception:
                    continue

            if candidate:
                await page.goto(candidate)
                await page.wait_for_load_state('networkidle')
                return

        # 3) Fallback to infinite scroll: scroll to bottom and wait for new content
        # Measure current number of article containers
        article_selector = 'article.ultp-block-item'
        before = await page.query_selector_all(article_selector)
        before_count = len(before)

        # Scroll to bottom multiple times to encourage lazy loading
        for _ in range(3):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1500)

        # Wait a little for dynamic content to load
        await page.wait_for_timeout(2000)

        after = await page.query_selector_all(article_selector)
        after_count = len(after)

        # If no new items were added, a single longer wait to be safe
        if after_count <= before_count:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(3000)

        # Stay on the same URL; get_all_articles logic will detect no new items and stop.
        return

    except Exception as e:
        # On unexpected errors, attempt a conservative infinite scroll before giving up.
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
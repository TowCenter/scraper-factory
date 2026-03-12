import json
import os
import re
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio

"""
Articles Scraper for American Civil Liberties Union of Minnesota Foundation

Generated at: 2026-03-12 11:26:11
Target URL: https://www.aclu-mn.org/press-releases/
Generated using: gpt-5-mini-2025-08-07
Content type: articles
Fields: title, date, url

"""

base_url = 'https://www.aclu-mn.org/press-releases/'

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
        - date: Publication date in YYYY-MM-DD format (optional — None if not found)
        - url: Link to the full article
        - scraper: module path for traceability
    """
    items = []

    # Use the article container selector identified from the site
    container_locator = page.locator("div.card--listing--press")
    count = await container_locator.count()

    for i in range(count):
        item_el = container_locator.nth(i)

        # Title and URL: prefer h3.is-heading a or generic a.hocus--opacity inside the container
        link_locator = item_el.locator("h3.is-heading a, a.hocus--opacity").first
        title = None
        url = None

        try:
            title_text = await link_locator.text_content()
            if title_text:
                # Normalize whitespace
                title = " ".join(title_text.split())
        except Exception:
            title = None

        try:
            href = await link_locator.get_attribute("href")
            if href:
                url = urllib.parse.urljoin(page.url, href.strip())
        except Exception:
            url = None

        # Date: look for the date element (desktop and mobile variants)
        date_locator = item_el.locator("div.is-body.font-bold.color-link.is-size-6, div.is-body.font-bold.color-link.is-size-6.flex").first
        date_iso = None
        try:
            date_text = await date_locator.text_content()
            if date_text:
                date_text = " ".join(date_text.split())  # normalize whitespace
                # Remove any decorative spans or trailing characters that are not part of the date
                # Use dateutil parser with fuzzy to handle formats like 'Mar 03, 2026'
                try:
                    parsed = parse(date_text, fuzzy=True)
                    date_iso = parsed.date().isoformat()
                except Exception:
                    date_iso = None
        except Exception:
            date_iso = None

        # Only include items that have at least a title and url
        if title and url:
            items.append({
                'title': title,
                'date': date_iso,
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
    # Try to find an explicit "next" link first
    next_locator = page.locator("nav.aclu-pagination a.next.page-numbers")
    try:
        next_count = await next_locator.count()
    except Exception:
        next_count = 0

    if next_count:
        # Prefer using href if available to ensure navigation occurs reliably
        try:
            href = await next_locator.first.get_attribute("href")
            if href:
                await page.goto(urllib.parse.urljoin(page.url, href))
                await page.wait_for_load_state("networkidle")
                return
        except Exception:
            # Fallback to clicking if href retrieval failed
            try:
                async with page.expect_navigation(timeout=10000):
                    await next_locator.first.click()
                await page.wait_for_load_state("networkidle")
                return
            except Exception:
                pass

    # If no explicit "next" link, try to find numeric pagination and navigate to the next numeric page
    page_numbers_locator = page.locator("nav.aclu-pagination a.page-numbers")
    try:
        pn_count = await page_numbers_locator.count()
    except Exception:
        pn_count = 0

    if pn_count:
        # Determine current page number from URL, default to 1
        current_page_num = 1
        m = re.search(r"/page/(\d+)/", page.url)
        if m:
            try:
                current_page_num = int(m.group(1))
            except Exception:
                current_page_num = 1

        target_num = current_page_num + 1

        # Search for a link whose visible text equals the target page number
        found_href = None
        for i in range(pn_count):
            try:
                el = page_numbers_locator.nth(i)
                txt = await el.text_content()
                if not txt:
                    continue
                txt = txt.strip()
                if txt == str(target_num):
                    href = await el.get_attribute("href")
                    if href:
                        found_href = href
                        break
            except Exception:
                continue

        if found_href:
            try:
                await page.goto(urllib.parse.urljoin(page.url, found_href))
                await page.wait_for_load_state("networkidle")
                return
            except Exception:
                pass

    # Fallback: infinite scroll - scroll to bottom and wait for content to load
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await page.wait_for_timeout(3000)
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
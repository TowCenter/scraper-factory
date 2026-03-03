"""
Journo bios Scraper for Northwestern University

Generated at: 2026-03-03 11:27:07
Target URL: https://www.medill.northwestern.edu/directory/faculty/journalism/
Generated using: gpt-5-mini-2025-08-07
Content type: journo_bios
Fields: name, url, position

"""

import json
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio

base_url = 'https://www.medill.northwestern.edu/directory/faculty/journalism/'

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
    Extract bio data from the current page.

    Parameters:
        page: Playwright page object

    Returns:
        List of dictionaries containing bio data with keys:
        - name: Name of the faculty member
        - url: Link to faculty bio
        - position: Position of the faculty (e.g., Professor, Assistant Professor, Lecturer)
        - scraper: module path for traceability
    """

    items = []

    # Use the card element that appears consistent in the page examples.
    # Primary container selector: .c-card.js-filter-data
    # Some cards may be wrapped inside layout items (.c-cards__item) but the .c-card is the reliable card.
    card_selector = '.c-card.js-filter-data'

    card_elements = await page.query_selector_all(card_selector)

    for card in card_elements:
        # Extract name: prefer data-name attribute if present, fallback to heading/title text
        name = None
        try:
            name_attr = await card.get_attribute('data-name')
            if name_attr:
                name = name_attr.strip()
        except Exception:
            name = None

        if not name:
            # try visible heading span or title inside hover
            h_heading = await card.query_selector('.c-card__heading span')
            if h_heading:
                try:
                    text = await h_heading.text_content()
                    if text:
                        name = text.strip()
                except Exception:
                    name = name
        if not name:
            h_title = await card.query_selector('.c-card__title')
            if h_title:
                try:
                    text = await h_title.text_content()
                    if text:
                        name = text.strip()
                except Exception:
                    pass

        # Extract url: first anchor with href within the card (image link or view more link)
        url = None
        try:
            link = await card.query_selector('a[href]')
            if link:
                href = await link.get_attribute('href')
                if href:
                    # Make absolute URL relative to base_url
                    url = urllib.parse.urljoin(base_url, href.strip())
        except Exception:
            url = None

        # Extract position: prefer data-title attribute, fallback to caption or card-info text
        position = None
        try:
            title_attr = await card.get_attribute('data-title')
            if title_attr:
                position = title_attr.strip()
        except Exception:
            position = None

        if not position:
            caption = await card.query_selector('.c-card__caption')
            if caption:
                try:
                    text = await caption.text_content()
                    if text:
                        position = text.strip()
                except Exception:
                    pass

        if not position:
            info = await card.query_selector('.c-card-info')
            if info:
                try:
                    text = await info.text_content()
                    if text:
                        position = text.strip()
                except Exception:
                    pass

        # Ensure required keys exist; if missing, set to None (tests expect presence)
        item = {
            'name': name or None,
            'url': url or None,
            'position': position or None,
            'scraper': SCRAPER_MODULE_PATH,
        }

        items.append(item)

    return items

async def advance_page(page):
    """
    Finds the next page button or link to navigate to the next page of bios.
    Clicks button or navigates to next page URL if found. Scroll load more button into view if not visible.
    Defaults to infinite scroll if no pagination found.

    Parameters:
        page: Playwright page object
    """

    # Try common "next" or "load more" selectors first (if present on the site).
    next_selectors = [
        'a[rel="next"]',
        'a.pagination-next',
        'a.next',
        '.pagination__next a',
        'button.load-more',
        'a.load-more',
        'button[aria-label="Load more"]',
        '.c-pagination__next a',
    ]

    for sel in next_selectors:
        try:
            elem = await page.query_selector(sel)
            if elem:
                # If element is a link with href, navigate to that URL
                href = await elem.get_attribute('href')
                if href:
                    next_url = urllib.parse.urljoin(base_url, href.strip())
                    try:
                        await page.goto(next_url)
                        # small wait to allow new content to load
                        await page.wait_for_timeout(1500)
                        return
                    except Exception:
                        # fallback to clicking if navigation failed
                        pass

                # Otherwise try clicking (may be a button that loads more via JS)
                try:
                    await elem.scroll_into_view_if_needed()
                    await elem.click()
                    # wait for potential new content to load
                    await page.wait_for_timeout(2000)
                    return
                except Exception:
                    # ignore and try next selector
                    pass
        except Exception:
            # ignore selector errors and continue
            pass

    # If no explicit pagination or load-more found, perform infinite scroll fallback.
    # Scroll to bottom repeatedly until no increase in document height or max iterations reached.
    max_scrolls = 6
    scroll_pause = 1500  # milliseconds
    for _ in range(max_scrolls):
        try:
            previous_height = await page.evaluate("() => document.body.scrollHeight")
            await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(scroll_pause)
            new_height = await page.evaluate("() => document.body.scrollHeight")
            if new_height == previous_height:
                # small additional attempt to trigger lazy loads by nudging
                await page.evaluate("() => window.scrollBy(0, -200)")
                await page.wait_for_timeout(800)
                await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1000)
                final_height = await page.evaluate("() => document.body.scrollHeight")
                if final_height == new_height:
                    break
        except Exception:
            # If any JS evaluation fails, break out to avoid infinite loop
            break

    # leave the page as-is (same URL). The get_all_articles loop will call scrape_page again.

async def get_first_page(base_url=base_url):
    """Fetch only the first page of bios."""
    async with PlaywrightContext() as context:
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)
        await page.goto(base_url)
        items = await scrape_page(page)
        await page.close()
        return items

async def get_all_articles(base_url=base_url, max_pages=100):
    """Fetch all bios from all pages."""

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
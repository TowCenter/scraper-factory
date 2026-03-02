import json
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
import asyncio
import urllib.parse

base_url = 'https://journalism.columbia.edu/content/full-time-faculty'

# Scraper module path for tracking the source of scraped data
SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

# Operator user-agent (set in operator.json)
USER_AGENT = ''

class PlaywrightContext:
    """Context manager for Playwright browser sessions."""

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        Stealth().hook_playwright_context(self.playwright)
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
    # Select all bio containers
    bio_containers = await page.query_selector_all('.dynamic-grid-listing-item')

    for container in bio_containers:
        # Extract name
        name_element = await container.query_selector('h2.ng-binding')
        name = await name_element.inner_text() if name_element else None

        # Extract URL
        url_element = await container.query_selector('a[href*="/directory/"]')
        url = await url_element.get_attribute('href') if url_element else None
        if url:
            url = urllib.parse.urljoin(base_url, url)

        # Extract position
        position_element = await container.query_selector('.text.ng-binding')
        position = await position_element.inner_text() if position_element else None

        # Append the extracted data to items list
        if name and url and position:
            items.append({
                'name': name,
                'url': url,
                'position': position,
                'scraper': SCRAPER_MODULE_PATH
            })

    return items

async def advance_page(page):
    """
    Finds the next page button or link to navigate to the next page of bios.
    Clicks button or navigates to next page URL if found. Scroll load more button into view if not visible.
    Defaults to infinite scroll if no pagination found.

    Parameters:
        page: Playwright page object
    """

    # Attempt to find and click the "Next" button
    next_button = await page.query_selector('a[aria-label*="next"]')
    if next_button:
        await next_button.scroll_into_view_if_needed()
        await next_button.click()
        await page.wait_for_load_state('networkidle')
    else:
        # Fallback to infinite scroll
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(3000)

async def get_first_page(base_url=base_url):
    """Fetch only the first page of bios."""
    async with PlaywrightContext() as context:
        page = await context.new_page()
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
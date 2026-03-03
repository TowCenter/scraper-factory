"""
Journo bios Scraper for Columbia University

Generated at: 2026-03-03 11:24:31
Target URL: https://journalism.columbia.edu/content/full-time-faculty
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

base_url = 'https://journalism.columbia.edu/content/full-time-faculty'

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

    # Robust item selector covering observed variants
    item_selector = '.dynamic-grid-listing .grid-item, .dynamic-grid-listing-item'

    # Find all candidate bio containers
    containers = await page.query_selector_all(item_selector)

    for container in containers:
        try:
            # Name: prefer h2 element (seen in examples). Fallback to first .ng-binding within the container.
            name = None
            name_el = await container.query_selector('h2')
            if name_el:
                name_text = await name_el.text_content()
                name = name_text.strip() if name_text else None
            else:
                # fallback
                bind_el = await container.query_selector('.ng-binding')
                if bind_el:
                    bind_text = await bind_el.text_content()
                    name = bind_text.strip() if bind_text else None

            # Url: first anchor inside the container. Convert to absolute URL.
            url = None
            a_el = await container.query_selector('a')
            if a_el:
                href = await a_el.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(base_url, href.strip())

            # Position: element with class "text" (examples show .text.ng-binding)
            position = None
            pos_el = await container.query_selector('.text')
            if pos_el:
                pos_text = await pos_el.text_content()
                if pos_text:
                    pos = pos_text.strip()
                    position = pos if pos != '' else None

            # Ensure required keys are present; if missing, set to None
            item = {
                'name': name or None,
                'url': url or None,
                'position': position or None,
                'scraper': SCRAPER_MODULE_PATH,
            }

            items.append(item)

        except Exception:
            # Be resilient to individual item parsing errors; skip problematic item
            continue

    return items

async def advance_page(page):
    """
    Finds the next page button or link to navigate to the next page of bios.
    Clicks button or navigates to next page URL if found. Scroll load more button into view if not visible.
    Defaults to infinite scroll if no pagination found.

    Parameters:
        page: Playwright page object
    """

    # Item selector used to detect content changes (used by caller to determine new items)
    item_selector = '.dynamic-grid-listing .grid-item, .dynamic-grid-listing-item'

    # Candidate anchors to inspect (broad capture)
    candidate_selector = ", ".join([
        ".pagination-next a",
        "ul.pagination-md .pagination-page a",
        "a[rel='next']",
        "a[aria-label]",
        "nav a",  # generic fallback to capture site pagination anchors
    ])

    try:
        # Current number of items on the page; used to detect that clicking advanced the content
        try:
            current_count = await page.evaluate(
                "() => document.querySelectorAll('.dynamic-grid-listing .grid-item, .dynamic-grid-listing-item').length"
            )
        except Exception:
            current_count = 0

        candidates = await page.query_selector_all(candidate_selector)

        next_btn = None

        # Helper to normalize text/aria/ng-click
        async def _get_text(el):
            try:
                return (await el.text_content() or '').strip()
            except Exception:
                return ''

        async def _get_attr(el, name):
            try:
                return await el.get_attribute(name)
            except Exception:
                return None

        # Priority selection: aria-label containing 'next' > visible text 'next' > ng-click selectPage(page + 1) > rel=next/href
        for el in candidates:
            try:
                aria = (await _get_attr(el, 'aria-label') or '').lower()
                txt = (await _get_text(el) or '').lower()
                rel = (await _get_attr(el, 'rel') or '').lower()
                ng_click = (await _get_attr(el, 'ng-click') or '').lower()

                if 'next' in aria:
                    next_btn = el
                    break
                if 'next' == txt or txt.startswith('next') or '>' == txt or '›' == txt:
                    next_btn = el
                    break
                if 'selectpage' in ng_click and ('+ 1' in ng_click or '+1' in ng_click or 'page + 1' in ng_click or 'page+1' in ng_click):
                    next_btn = el
                    break
                if 'next' in rel:
                    next_btn = el
                    break
            except Exception:
                continue

        # As a last resort, if none matched precisely, try to pick the element inside .pagination-next if present
        if not next_btn:
            try:
                alt = await page.query_selector('.pagination-next a')
                if alt:
                    next_btn = alt
            except Exception:
                pass

        if next_btn:
            # Scroll into view and attempt click first (Angular-style pagination often relies on click handlers)
            try:
                await next_btn.scroll_into_view_if_needed()
            except Exception:
                pass

            clicked = False
            try:
                await next_btn.click()
                clicked = True
            except Exception:
                clicked = False

            # Wait for new content to be added (more items than before) or for network idle / navigation
            try:
                if clicked:
                    # Wait for item count to be greater than previous (common when appends or loads new page content)
                    try:
                        await page.wait_for_function(
                            "(prev) => document.querySelectorAll('.dynamic-grid-listing .grid-item, .dynamic-grid-listing-item').length > prev",
                            current_count,
                            timeout=8000,
                        )
                        return
                    except Exception:
                        # If that didn't happen, fallback to waiting for network activity to quiet down (covers full navigations)
                        try:
                            await page.wait_for_load_state('networkidle', timeout=5000)
                            return
                        except Exception:
                            # continue to other fallbacks below
                            pass
                # If click didn't work or didn't produce detectable change, try navigation via href
                href = await next_btn.get_attribute('href')
                if href and href.strip() and href.strip() != '#':
                    next_url = urllib.parse.urljoin(base_url, href.strip())
                    try:
                        await page.goto(next_url)
                        try:
                            await page.wait_for_load_state('networkidle', timeout=5000)
                        except Exception:
                            await page.wait_for_timeout(1000)
                        return
                    except Exception:
                        pass

                # If ng-click exists but click failed earlier, attempt to simulate click via JS
                ng_click_attr = await next_btn.get_attribute('ng-click')
                if ng_click_attr:
                    try:
                        # Attempt to click via JS dispatch
                        await page.evaluate("(el) => el.click()", next_btn)
                        try:
                            await page.wait_for_function(
                                "(prev) => document.querySelectorAll('.dynamic-grid-listing .grid-item, .dynamic-grid-listing-item').length > prev",
                                current_count,
                                timeout=8000,
                            )
                            return
                        except Exception:
                            try:
                                await page.wait_for_load_state('networkidle', timeout=5000)
                                return
                            except Exception:
                                pass
                    except Exception:
                        pass

            except Exception:
                # If anything unexpected happens, fall back to infinite scroll below
                pass

        # If no next button found or everything failed, fallback to infinite scroll
        previous_height = await page.evaluate("() => document.body.scrollHeight")
        for _ in range(4):
            try:
                await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2000)
                new_height = await page.evaluate("() => document.body.scrollHeight")
                if new_height == previous_height:
                    break
                previous_height = new_height
            except Exception:
                break

    except Exception:
        # As a last resort, perform a single scroll and wait
        try:
            await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1500)
        except Exception:
            pass

    return


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
import json
import os
import re
import urllib.parse
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
import asyncio

base_url = 'https://journalism.missouri.edu/position/faculty/'

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

async def _safe_text(element_handle):
    """Return stripped text_content() or None if element_handle is None."""
    if not element_handle:
        return None
    text = await element_handle.text_content()
    return text.strip() if text and text.strip() != '' else None

async def _extract_page_number(url):
    """Extract page number from a WordPress-like pagination URL, return int or 0 if not present."""
    if not url:
        return 0
    m = re.search(r'/page/(\d+)/?', url)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return 0
    return 0

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

    # Prefer stable container selector for each person
    containers = await page.query_selector_all('div.archive-person')

    # Fallback: if no archive-person containers, try person-link anchors
    fallback_anchors = []
    if not containers:
        fallback_anchors = await page.query_selector_all('a.person-link')

    # Helper to normalize URL
    def _abs(href):
        if not href:
            return None
        return urllib.parse.urljoin(base_url, href)

    # Process archive-person containers
    for container in containers:
        # Name
        name_el = await container.query_selector('h2.person-name')
        name = await _safe_text(name_el)

        # URL - prefer the person-link anchor inside container
        link_el = await container.query_selector('a.person-link[href]')
        url = None
        if link_el:
            href = await link_el.get_attribute('href')
            url = _abs(href)

        # Position
        pos_el = await container.query_selector('span.person-job-title')
        position = await _safe_text(pos_el)

        # If name missing, attempt alternative selectors inside container
        if not name:
            # sometimes name might be inside a link or header tag
            alt_name_el = await container.query_selector('a.person-link > img[alt], header h2, header a')
            if alt_name_el:
                # if it's an img, get alt attribute
                tag_name = await alt_name_el.evaluate("el => el.tagName.toLowerCase()")
                if tag_name == 'img':
                    name = await alt_name_el.get_attribute('alt') or name
                else:
                    name = await _safe_text(alt_name_el) or name

        items.append({
            'name': name or None,
            'url': url or None,
            'position': position or None,
            'scraper': SCRAPER_MODULE_PATH,
        })

    # Process fallback anchors when archive-person containers not present
    for anchor in fallback_anchors:
        # URL from anchor href
        href = await anchor.get_attribute('href')
        url = _abs(href)

        # Try to find a nearby name: image alt or contained text
        name = None
        img = await anchor.query_selector('img[alt]')
        if img:
            name = await img.get_attribute('alt')
        if not name:
            # maybe anchor contains text
            name = await _safe_text(anchor)

        # Position likely not present in anchor-only markup; attempt to find sibling header via DOM
        # Try to locate closest ancestor that may contain job title
        position = None
        try:
            # find closest ancestor with class archive-person then query its job-title
            ancestor_handle = await anchor.evaluate_handle("el => el.closest('.archive-person')")
            if ancestor_handle:
                # convert JSHandle to ElementHandle if possible by querying a child
                # Use evaluate to fetch text directly to avoid complex handle conversions
                pos_text = await page.evaluate(
                    "(anc) => { const el = anc.querySelector('span.person-job-title'); return el ? el.textContent : null; }",
                    ancestor_handle
                )
                if pos_text:
                    position = pos_text.strip()
        except Exception:
            position = None

        items.append({
            'name': (name.strip() if name else None),
            'url': url or None,
            'position': position or None,
            'scraper': SCRAPER_MODULE_PATH,
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
    # Priority 1: explicit "next" link with class 'next page-numbers'
    try:
        next_btn = await page.query_selector('a.next.page-numbers')
        if next_btn:
            href = await next_btn.get_attribute('href')
            if href:
                next_url = urllib.parse.urljoin(base_url, href)
                await page.goto(next_url)
                await page.wait_for_load_state('load')
                return
            else:
                # If no href, try clicking the button
                try:
                    await next_btn.scroll_into_view_if_needed()
                    await next_btn.click()
                    await page.wait_for_load_state('load')
                    return
                except Exception:
                    pass

        # Priority 2: generic pagination links in nav.navigation.pagination
        pagination_links = await page.query_selector_all('nav.navigation.pagination a.page-numbers[href]')
        if pagination_links:
            # Determine current page number
            current_url = page.url
            cur_num = await _extract_page_number(current_url)

            # Collect candidate hrefs with page numbers
            candidates = []
            for a in pagination_links:
                href = await a.get_attribute('href')
                if not href:
                    continue
                full = urllib.parse.urljoin(base_url, href)
                pnum = await _extract_page_number(full)
                # treat no page fragment as page 1 (0)
                if pnum == 0:
                    pnum = 1
                candidates.append((pnum, full))

            # Find smallest page number greater than current
            greater = [t for t in candidates if t[0] > (cur_num if cur_num > 0 else 1)]
            if greater:
                # choose the next page (smallest greater)
                next_page = sorted(greater, key=lambda x: x[0])[0][1]
                await page.goto(next_page)
                await page.wait_for_load_state('load')
                return

            # If no numeric progression found, try to find link with text 'Next'
            for a in pagination_links:
                txt = await (a.text_content() or "")
                if txt and txt.strip().lower() == 'next':
                    href = await a.get_attribute('href')
                    if href:
                        await page.goto(urllib.parse.urljoin(base_url, href))
                        await page.wait_for_load_state('load')
                        return

        # If no pagination links found or navigation attempts failed, fallback to infinite scroll
        # Scroll to bottom, wait for potential content load
        previous_height = await page.evaluate("() => document.body.scrollHeight")
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(3000)  # allow time for lazy loading
        new_height = await page.evaluate("() => document.body.scrollHeight")

        # If page height increased, assume new content loaded; otherwise do nothing (end)
        if new_height > previous_height:
            return
        else:
            # No change - final fallback: attempt to find and click any "Load More" buttons
            load_more = await page.query_selector('button.load-more, a.load-more, .load-more-btn')
            if load_more:
                try:
                    await load_more.scroll_into_view_if_needed()
                    await load_more.click()
                    await page.wait_for_timeout(2000)
                    return
                except Exception:
                    pass

            # Nothing more to do; allow caller to detect no new items by comparing counts
            return

    except Exception:
        # On any error, attempt safe infinite scroll fallback
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(3000)
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
                    # Use tuple of sorted pairs for a stable dedupe key
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
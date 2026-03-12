import json
import os
import asyncio
import urllib.parse
from dateutil.parser import parse, ParserError
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API

base_url = 'https://www.startribune.com/news-politics/twin-cities'

# Scraper module path for tracking the source of scraped data
SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

# Operator user-agent (set in operator.json)
USER_AGENT = ''


class PlaywrightContext:
    """Context manager for Playwright browser sessions."""

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        # Launch headless chromium
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
        - url: Absolute URL to the full article
        - scraper: module path for traceability
    """
    items = []

    # Choose a robust item container selector observed on the page.
    # Keep selector reasonably general but specific enough to target article cards.
    container_selectors = [
        # Primary observed container with responsive grid classes
        'div.rt-Box.col-span-8.md\\:col-span-5',
        # Slightly more specific variant
        'div.rt-Box.col-span-8.md\\:col-span-5.flex.flex-col.gap-2',
    ]

    containers = []
    for sel in container_selectors:
        nodes = await page.query_selector_all(sel)
        if nodes:
            containers = nodes
            break

    # Fallback: try to find article headline elements and derive container from there
    if not containers:
        headline_nodes = await page.query_selector_all('h3.rt-Heading')
        for hn in headline_nodes:
            parent = await hn.evaluate_handle('node => node.closest("div")')
            if parent:
                # Convert handle to ElementHandle if possible
                try:
                    containers.append(parent.as_element())
                except Exception:
                    pass

    # If still no containers found, return empty list
    if not containers:
        return items

    for c in containers:
        try:
            # Title: prefer the h3.rt-Heading element within the container
            title_el = await c.query_selector('h3.rt-Heading')
            title = None
            if title_el:
                raw_title = await title_el.text_content()
                if raw_title:
                    title = raw_title.strip()
            # URL: find the anchor that wraps the headline (anchor containing the h3)
            url = None
            anchor = None
            # Use :has() to locate anchor that contains the h3
            try:
                anchor = await c.query_selector('a:has(h3.rt-Heading)')
            except Exception:
                # Older playwright versions or selector engine differences fallback:
                anchors = await c.query_selector_all('a')
                for a in anchors:
                    # check if this anchor contains the h3 element
                    has_h3 = await a.query_selector('h3.rt-Heading')
                    if has_h3:
                        anchor = a
                        break

            if anchor:
                href = await anchor.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(base_url, href.strip())

            # Date: attempt to find any text in the container that parses as a date.
            date = None
            # Look for likely date-holding spans first by class patterns
            candidate_selectors = [
                'div span.rt-Text.font-utility-label-reg-caps-02',  # observed in examples
                'div span.rt-Text.font-utility-label-reg-caps-02.text-text-tertiary',
                'div span.rt-Text.font-utility-label-reg-caps-02.text-text-tertiary',
                'div span.rt-Text',  # general fallback
                'span',  # broad fallback
            ]
            parsed = False
            # Collect unique text candidates to avoid repeated parsing attempts
            seen_texts = set()
            for cs in candidate_selectors:
                nodes = await c.query_selector_all(cs)
                for n in nodes:
                    try:
                        txt = await n.text_content()
                    except Exception:
                        txt = None
                    if not txt:
                        continue
                    txt = txt.strip()
                    if not txt or txt in seen_texts:
                        continue
                    seen_texts.add(txt)
                    # Try to parse any candidate text as a date
                    try:
                        dt = parse(txt, fuzzy=True, default=None)
                        # dateutil.parse with default=None raises TypeError; handle using try/except
                        if dt:
                            # Ensure parsed object has a year (dateutil will set year if not present)
                            # Format as YYYY-MM-DD
                            date = dt.date().isoformat()
                            parsed = True
                            break
                    except (ParserError, TypeError, ValueError):
                        continue
                if parsed:
                    break

            # As last resort, search for any element text that looks like "Month Day, Year"
            if not parsed:
                all_text = await c.text_content() or ''
                # Try to find a substring that looks like month pattern using dateutil fuzzy parsing
                try:
                    dt = parse(all_text, fuzzy=True)
                    if dt:
                        date = dt.date().isoformat()
                except Exception:
                    date = None

            # Build item only if title and url present (title and url are required)
            if title and url:
                items.append({
                    'title': title,
                    'date': date if date else None,
                    'url': url,
                    'scraper': SCRAPER_MODULE_PATH,
                })

        except Exception:
            # Skip individual container errors but continue processing others
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
    # Try to find an explicit "Load More" or pagination button first.
    try:
        # Prefer button with visible "Load More" text
        load_more_btn = await page.query_selector('button:has-text("Load More")')
        if load_more_btn:
            try:
                await load_more_btn.scroll_into_view_if_needed()
            except Exception:
                pass
            try:
                await load_more_btn.click()
            except Exception:
                # Some sites may require evaluating click in page context
                await page.evaluate('(el) => el.click()', load_more_btn)
            # Wait briefly for additional content to load
            try:
                await page.wait_for_load_state('networkidle', timeout=5000)
            except Exception:
                await page.wait_for_timeout(2000)
            return

        # Try the observed specific class for the load more button as a fallback
        load_more_btn2 = await page.query_selector('button.Button_secondary-default-mode__yzoDW')
        if load_more_btn2:
            try:
                await load_more_btn2.scroll_into_view_if_needed()
            except Exception:
                pass
            try:
                await load_more_btn2.click()
            except Exception:
                await page.evaluate('(el) => el.click()', load_more_btn2)
            try:
                await page.wait_for_load_state('networkidle', timeout=5000)
            except Exception:
                await page.wait_for_timeout(2000)
            return

        # If there's an anchor-based "next" link (rare on this site), click it
        next_link = await page.query_selector('a[rel="next"], a:has-text("Next")')
        if next_link:
            href = await next_link.get_attribute('href')
            if href:
                next_url = urllib.parse.urljoin(page.url, href)
                await page.goto(next_url)
                try:
                    await page.wait_for_load_state('networkidle', timeout=5000)
                except Exception:
                    await page.wait_for_timeout(2000)
                return
            else:
                try:
                    await next_link.scroll_into_view_if_needed()
                except Exception:
                    pass
                try:
                    await next_link.click()
                except Exception:
                    await page.evaluate('(el) => el.click()', next_link)
                try:
                    await page.wait_for_load_state('networkidle', timeout=5000)
                except Exception:
                    await page.wait_for_timeout(2000)
                return

    except Exception:
        # If any of the above attempts throw, fall through to infinite scroll fallback
        pass

    # Fallback: infinite scroll behavior
    # Scroll to bottom, wait for content to load. Repeat a small number of times to allow lazy loading.
    try:
        previous_height = await page.evaluate('() => document.body.scrollHeight')
        for _ in range(3):
            await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            await page.wait_for_timeout(2000)  # allow lazy-loaded content to load
            new_height = await page.evaluate('() => document.body.scrollHeight')
            if new_height == previous_height:
                # no more content loaded
                break
            previous_height = new_height
    except Exception:
        # As final fallback, just wait briefly
        await page.wait_for_timeout(2000)


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
                    # Create a deterministic key from the item dict to deduplicate
                    key = tuple(sorted((k, v) for k, v in item.items() if v is not None))
                    if key not in seen:
                        seen.add(key)
                        items.append(item)
                new_item_count = len(items)

                # If no new items were added this iteration, stop pagination
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
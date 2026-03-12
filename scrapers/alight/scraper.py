import json
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio

base_url = 'https://www.wearealight.org/news-stories'

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
        - date: Publication date in YYYY-MM-DD format or None
        - url: Link to the full article (absolute URL)
        - scraper: module path for traceability
    """
    items = []

    # Primary item container selectors (combined for resilience)
    item_selector = (
        '.image-text-col-4.w-dyn-item, '
        '.card-image-text-block .w-dyn-item, '
        '.post-collections .w-dyn-item, '
        '.w-dyn-item'
    )

    article_nodes = await page.query_selector_all(item_selector)

    for node in article_nodes:
        try:
            # TITLE: try multiple reliable selectors in order
            title = None
            # 1) anchor with class post-heading (often contains h4)
            post_heading = await node.query_selector('a.post-heading')
            if post_heading:
                # prefer contained h4 if present
                h4 = await post_heading.query_selector('h4')
                if h4:
                    title_text = await h4.text_content()
                else:
                    title_text = await post_heading.text_content()
                if title_text:
                    title = title_text.strip()

            # 2) direct h4.content-heading
            if not title:
                h4_direct = await node.query_selector('h4.content-heading')
                if h4_direct:
                    text = await h4_direct.text_content()
                    if text:
                        title = text.strip()

            # 3) legacy/story specific anchor selector
            if not title:
                story_anchor = await node.query_selector('a.story_sub_heading.post-title')
                if story_anchor:
                    text = await story_anchor.text_content()
                    if text:
                        title = text.strip()

            # URL: find first anchor that likely links to the article
            url = None
            # prefer post-heading anchor
            if post_heading:
                href = await post_heading.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(base_url, href.strip())

            # fallback to story_sub_heading
            if not url:
                story_anchor = await node.query_selector('a.story_sub_heading.post-title')
                if story_anchor:
                    href = await story_anchor.get_attribute('href')
                    if href:
                        url = urllib.parse.urljoin(base_url, href.strip())

            # fallback to anchor with class story_link or anchor-link inside card
            if not url:
                story_link = await node.query_selector('a.story_link, a.anchor-link')
                if story_link:
                    href = await story_link.get_attribute('href')
                    if href:
                        url = urllib.parse.urljoin(base_url, href.strip())

            # DATE: check known date class variants
            date_text = None
            date_selectors = ['.custom-published-date', '.default-published-date', '.date-published', 'time']
            for ds in date_selectors:
                el = await node.query_selector(ds)
                if el:
                    txt = await el.text_content()
                    if txt:
                        date_text = txt.strip()
                        break

            # Normalize date to YYYY-MM-DD if possible
            date_iso = None
            if date_text:
                try:
                    # Use dateutil parse; prefer dayfirst=False (US-style common)
                    dt = parse(date_text, fuzzy=True)
                    date_iso = dt.date().isoformat()
                except Exception:
                    date_iso = None

            # Only include items with at least title and url
            if title and url:
                items.append({
                    'title': title,
                    'date': date_iso,
                    'url': url,
                    'scraper': SCRAPER_MODULE_PATH,
                })

        except Exception:
            # Skip malformed item but continue processing others
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
    # Primary selector used to count items on the page (same as scrape_page)
    item_selector = (
        '.image-text-col-4.w-dyn-item, '
        '.card-image-text-block .w-dyn-item, '
        '.post-collections .w-dyn-item, '
        '.w-dyn-item'
    )

    async def current_item_count():
        return await page.evaluate(f"document.querySelectorAll('{item_selector}').length")

    async def current_pagecount_tuple():
        el = await page.query_selector('.paginationpagecount')
        if not el:
            return None
        txt = await el.text_content()
        if not txt:
            return None
        parts = txt.split('/')
        if len(parts) == 2:
            try:
                current = int(parts[0].strip())
                total = int(parts[1].strip())
                return (current, total)
            except Exception:
                return None
        return None

    prev_count = await current_item_count()
    prev_url = page.url
    prev_pagecount = await current_pagecount_tuple()

    # Candidate selectors for the next control
    next_selectors = [
        'a.paginationnext',
        '.paginationnext',
        '.pagination-next',
        'a.paginationnext.w-inline-block',
    ]

    # Try to click any next control and wait for either DOM or page change.
    for sel in next_selectors:
        try:
            next_btn = await page.query_selector(sel)
            if not next_btn:
                continue

            # If the control is disabled (pointer-events: none or aria-disabled), skip
            try:
                style = await next_btn.get_attribute('style') or ''
                aria_disabled = await next_btn.get_attribute('aria-disabled')
                if 'pointer-events: none' in style or (aria_disabled and aria_disabled.lower() in ['true', 'disabled']):
                    continue
            except Exception:
                pass

            # If the anchor has a real href that is not a fragment or javascript, navigate directly
            href = await next_btn.get_attribute('href')
            if href and href.strip() and not href.strip().startswith('#') and not href.strip().lower().startswith('javascript'):
                next_url = urllib.parse.urljoin(base_url, href.strip())
                # navigate and wait for load
                try:
                    await page.goto(next_url)
                    # allow content to load
                    await page.wait_for_timeout(1500)
                    return
                except Exception:
                    # proceed to attempt clicking if navigate fails
                    pass

            # Otherwise attempt to click the element (or its inner clickable element)
            try:
                await next_btn.scroll_into_view_if_needed()
            except Exception:
                pass

            # Try clicking the inner element if present (sometimes arrow is inside)
            clicked = False
            try:
                await next_btn.click()
                clicked = True
            except Exception:
                # try clicking a child element as fallback
                try:
                    child = await next_btn.query_selector('div, span, svg, .paginationnextarrow, .paginationprevarrow')
                    if child:
                        await child.click()
                        clicked = True
                except Exception:
                    clicked = False

            if not clicked:
                # Couldn't click this control; try next selector
                continue

            # After click, wait for either navigation, or item count change, or pagecount change.
            max_wait_ms = 10000
            interval = 0.5
            waited = 0.0
            while waited < (max_wait_ms / 1000.0):
                # Check URL change
                if page.url != prev_url:
                    # navigation happened
                    await page.wait_for_timeout(500)
                    return

                # Check item count change
                try:
                    new_count = await current_item_count()
                    if new_count != prev_count:
                        # items updated
                        # allow a small buffer for rendering
                        await page.wait_for_timeout(500)
                        return
                except Exception:
                    pass

                # Check pagination control update (e.g., "1 / 19" -> "2 / 19")
                try:
                    new_pc = await current_pagecount_tuple()
                    if prev_pagecount and new_pc and new_pc[0] != prev_pagecount[0]:
                        await page.wait_for_timeout(300)
                        return
                except Exception:
                    pass

                await asyncio.sleep(interval)
                waited += interval

            # If click didn't cause change, continue to try other selectors/fallbacks
        except Exception:
            # on any error try next selector
            continue

    # If no next button produced a change, try a "load more" control (common pattern)
    try:
        load_more_selectors = ['button.load-more', '.load-more', 'a.load-more', '.btn-load-more']
        for sel in load_more_selectors:
            try:
                lm = await page.query_selector(sel)
                if not lm:
                    continue
                try:
                    await lm.scroll_into_view_if_needed()
                except Exception:
                    pass
                try:
                    await lm.click()
                except Exception:
                    try:
                        child = await lm.query_selector('div, span, svg, button')
                        if child:
                            await child.click()
                    except Exception:
                        pass

                # wait for DOM change similarly
                max_wait_ms = 8000
                interval = 0.5
                waited = 0.0
                while waited < (max_wait_ms / 1000.0):
                    new_count = await current_item_count()
                    if new_count != prev_count:
                        await page.wait_for_timeout(300)
                        return
                    await asyncio.sleep(interval)
                    waited += interval
            except Exception:
                continue
    except Exception:
        pass

    # Final fallback: infinite scroll - attempt to load more content by scrolling to bottom
    try:
        previous_height = await page.evaluate('document.body.scrollHeight')
        await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
        # Wait for lazy-loaded content or JS to append items
        await page.wait_for_timeout(3000)
        # Optionally wait until height increases or item count increases or timeout
        for _ in range(6):
            new_height = await page.evaluate('document.body.scrollHeight')
            new_count = await current_item_count()
            if (new_height and new_height > previous_height) or (new_count != prev_count):
                await page.wait_for_timeout(1000)
                return
            await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            await page.wait_for_timeout(1000)
    except Exception:
        # If scrolling fails, silently return (caller will stop if no new items)
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
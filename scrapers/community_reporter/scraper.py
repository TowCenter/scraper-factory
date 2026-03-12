import json
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio

base_url = 'https://communityreporter.org/'

# Scraper module path for tracking the source of scraped data
SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

# Operator user-agent (set in operator.json)
USER_AGENT = ''

class PlaywrightContext:
    """Context manager for Playwright browser sessions."""

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        # Use headless mode by default for automation environments
        self.browser = await self.playwright.chromium.launch(headless=True)
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
        - url: Link to the full article
        - scraper: module path for traceability
    """
    items = []

    # A set of potential container selectors; try each until we find containers
    container_selectors = [
        'li.wp-block-post',
        'article',
        'article.post',
        'article.hentry',
        'div.post',
        'div.entry',
        'div.hentry',
        'li.post',
        '.post-item',
        '.post-preview',
        '.news-item',
        '.archive .post',
        '.card',
        '.teaser',
    ]

    containers = []
    # Try robustly to locate containers; prefer the selector that yields the most matches
    try:
        best = []
        for sel in container_selectors:
            try:
                found = await page.query_selector_all(sel)
                if found and len(found) > len(best):
                    best = found
            except Exception:
                continue
        containers = best
        # As a final fallback, look for anchors that look like article links
        if not containers:
            anchors = await page.query_selector_all('a[rel="bookmark"], a[href*="/20"], article a, .post a')
            # Convert anchors to their nearest article container by using the anchor's closest ancestor that looks like an article
            seen_handles = set()
            containers_temp = []
            for a in anchors:
                try:
                    ancestor = await page.evaluate_handle("(el) => el.closest('article, li, div')", a)
                    if ancestor:
                        # Compare the handle's _impl_obj pointer to avoid duplicates (works across Playwright handles)
                        key = await page.evaluate("(n) => (n && n.outerHTML) ? (n.outerHTML.length) : Math.random()", ancestor)
                        if key not in seen_handles:
                            seen_handles.add(key)
                            containers_temp.append(ancestor)
                except Exception:
                    continue
            # If we found some ancestors, use them (convert handles to element handles accepted)
            if containers_temp:
                # The ancestor handles might be JSHandles; try to transform to element handles via query_selector_all fallback
                containers = []
                for ch in containers_temp:
                    try:
                        # Use the outerHTML to locate a matching element handle on the page
                        outer = await (await ch.get_property('outerHTML')).json_value()
                        if outer:
                            # Try to find the element by matching a unique string from the outerHTML (risky), better to fallback to anchors list
                            pass
                    except Exception:
                        pass
                # If conversion failed, fallback to raw anchors as containers
                if not containers:
                    containers = anchors

    except Exception:
        containers = []

    # If still empty, return empty list early
    if not containers:
        return items

    for container in containers:
        try:
            # The container may be an ElementHandle or a JSHandle; ensure it supports query_selector
            # We'll attempt to query within it for title anchors and time elements.

            # Title: prefer common title selectors
            title_anchor = None
            title_selectors = [
                'h2.entry-title a',
                'h2.post-title a',
                'h3.entry-title a',
                'h3.post-title a',
                'a[rel="bookmark"]',
                'header a',
                '.post-title a',
                '.entry-title a',
                'a'
            ]
            for ts in title_selectors:
                try:
                    title_anchor = await container.query_selector(ts)
                except Exception:
                    title_anchor = None
                if title_anchor:
                    break

            title_text = None
            url = None

            if title_anchor:
                raw_title = await title_anchor.text_content()
                if raw_title:
                    title_text = raw_title.strip()
                raw_href = await title_anchor.get_attribute('href')
                if raw_href:
                    url = urllib.parse.urljoin(base_url, raw_href.strip())

            # Fallback: first anchor with href inside container
            if not url:
                try:
                    first_anchor = await container.query_selector('a[href]')
                    if first_anchor:
                        raw_href = await first_anchor.get_attribute('href')
                        if raw_href:
                            url = urllib.parse.urljoin(base_url, raw_href.strip())
                except Exception:
                    pass

            # Date extraction: try <time> first, then common meta elements
            date_value = None
            try:
                time_el = await container.query_selector('time')
                if time_el:
                    datetime_attr = await time_el.get_attribute('datetime')
                    text_val = None
                    if datetime_attr:
                        try:
                            dt = parse(datetime_attr)
                            date_value = dt.date().isoformat()
                        except Exception:
                            text_val = await time_el.text_content()
                    else:
                        text_val = await time_el.text_content()

                    if not date_value and text_val:
                        try:
                            dt = parse(text_val.strip(), fuzzy=True)
                            date_value = dt.date().isoformat()
                        except Exception:
                            date_value = None
                else:
                    # Look for common meta selectors
                    possible = None
                    for sel in ['.posted-on', '.posted', '.date', '.post-meta', 'span.posted-on', 'p.post-meta', 'div.post-meta', '.byline', '.meta']:
                        try:
                            possible = await container.query_selector(sel)
                        except Exception:
                            possible = None
                        if possible:
                            break
                    if possible:
                        try:
                            txt = await possible.text_content()
                            if txt:
                                try:
                                    dt = parse(txt.strip(), fuzzy=True)
                                    date_value = dt.date().isoformat()
                                except Exception:
                                    date_value = None
                        except Exception:
                            date_value = None
            except Exception:
                date_value = None

            # Ensure required fields; title and url expected, date may be None
            if title_text:
                item = {
                    'title': title_text,
                    'date': date_value,
                    'url': url,
                    'scraper': SCRAPER_MODULE_PATH,
                }
                items.append(item)
            else:
                # If no title found but URL exists, still include with placeholder title from URL path
                if url:
                    fallback_title = urllib.parse.urlparse(url).path.rstrip('/').split('/')[-1].replace('-', ' ')
                    item = {
                        'title': fallback_title or url,
                        'date': date_value,
                        'url': url,
                        'scraper': SCRAPER_MODULE_PATH,
                    }
                    items.append(item)
                # else skip malformed entry

        except Exception:
            # Continue on per-item errors to avoid breaking entire page parse
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
        # First, attempt to find canonical next links (rel="next")
        next_href = await page.evaluate("""
() => {
  // Prefer rel="next"
  const relNext = document.querySelector('a[rel="next"]');
  if (relNext && relNext.href) return relNext.href;

  // Common pagination link texts to look for
  const textCandidates = ['next', 'older posts', 'older', 'load more', 'more', '›', '→', '»'];
  const anchors = Array.from(document.querySelectorAll('a[href]'));
  for (const a of anchors) {
    const txt = (a.textContent || '').trim().toLowerCase();
    const aria = (a.getAttribute('aria-label') || '').trim().toLowerCase();
    // check aria-label or visible text for candidate words
    for (const cand of textCandidates) {
      if (aria.includes(cand) || txt === cand || txt.includes(cand)) {
        if (a.href) return a.href;
      }
    }
    // also check pagination rel attribute or class names
    const rel = (a.getAttribute('rel') || '').toLowerCase();
    const cls = (a.className || '').toLowerCase();
    if (rel.includes('next') || cls.includes('next') || cls.includes('pagination-next') || cls.includes('older-posts')) {
      if (a.href) return a.href;
    }
  }
  return null;
}
""")
        if next_href:
            # Navigate to absolute URL of next page
            try:
                await page.goto(next_href)
                # give the page a moment to load content
                await page.wait_for_load_state('networkidle', timeout=8000)
                await asyncio.sleep(1)
                return
            except Exception:
                # If direct navigation fails, attempt click on the element instead
                try:
                    next_el = await page.query_selector(f'a[href="{next_href}"]')
                    if next_el:
                        await next_el.scroll_into_view_if_needed()
                        await next_el.click()
                        await page.wait_for_load_state('networkidle', timeout=8000)
                        await asyncio.sleep(1)
                        return
                except Exception:
                    pass

        # If no next link found or navigation failed, perform infinite-scroll fallback
        post_selector = 'article, li.wp-block-post, .post, .post-item'
        previous_count = 0
        try:
            previous_count = await page.locator(post_selector).count()
        except Exception:
            previous_count = 0

        # Try several scroll attempts to load more content
        max_scrolls = 6
        for _ in range(max_scrolls):
            # Scroll to bottom
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            # Wait a bit for lazy load / network requests
            await asyncio.sleep(2.5)
            # Attempt to click any visible "Load more" button if appears
            try:
                load_more = await page.query_selector('button:has-text("Load more"), button:has-text("Load More"), a:has-text("Load more"), a:has-text("Load More"), button.load-more, a.load-more')
                if load_more:
                    try:
                        await load_more.scroll_into_view_if_needed()
                        await load_more.click()
                        await asyncio.sleep(1.5)
                    except Exception:
                        pass
            except Exception:
                # older versions of Playwright might not support :has-text in query_selector; ignore
                pass

            # Check if new posts loaded
            try:
                new_count = await page.locator(post_selector).count()
            except Exception:
                new_count = previous_count

            if new_count > previous_count:
                # New content loaded; stop scrolling further
                return
            previous_count = new_count

        # Final wait to allow any asynchronous loading to settle
        await asyncio.sleep(1.5)
        return

    except Exception:
        # In case of unexpected errors, fallback to a single scroll and wait
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
        # Wait for network to settle and for body to be present
        try:
            await page.wait_for_load_state('networkidle', timeout=10000)
        except Exception:
            try:
                await page.wait_for_selector('body', timeout=8000)
            except Exception:
                pass
        # Give extra time for dynamic content
        await asyncio.sleep(1.5)
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
        try:
            await page.wait_for_load_state('networkidle', timeout=10000)
        except Exception:
            try:
                await page.wait_for_selector('body', timeout=8000)
            except Exception:
                pass
        await asyncio.sleep(1.5)

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
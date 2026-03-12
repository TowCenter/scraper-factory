import json
import os
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio
from typing import List

base_url = 'https://hbctv.net/category/3hmongtv-news/'

# Scraper module path for tracking the source of scraped data
SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

# Operator user-agent (set in operator.json). Provide a sensible default to avoid blocking.
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

# Broad set of possible article container selectors commonly used by news sites / Wordpress themes.
ITEM_SELECTORS = [
    "article",                 # semantic article tag
    ".post",                   # common post class
    ".post-item",
    ".post-wrap",
    ".post-block",
    ".article",
    ".entry",
    ".entry-item",
    ".entry-wrap",
    ".td_module_wrap",         # td themes
    ".jeg_post",               # jeg theme
    ".item",                   # generic item
    ".listing-item",
    ".news-item",
    ".blog-item",
    ".archive-item",
    ".card",                   # card-based layouts
]
# Combined selector string used for query_selector_all
COMBINED_ITEM_SELECTOR = ",".join(ITEM_SELECTORS)

# Candidate selectors for "next page" and "load more" controls.
NEXT_PAGE_LINK_SELECTORS = [
    'a[rel="next"]',
    'a.next',
    'a.next.page-numbers',
    '.nav-next a',
    'a.pagination-next',
    '.pagination .next a',
    'a[aria-label="next"]',
]
LOAD_MORE_BUTTON_SELECTORS = [
    'button.load-more',
    'a.load-more',
    '.load-more a',
    '.load_more',
    'button.more',
    '.btn-load-more',
    '.infinite-load button',
]


class PlaywrightContext:
    """Context manager for Playwright browser sessions."""

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        # Use headless to be CI-friendly
        self.browser = await self.playwright.chromium.launch(headless=True)
        context_kwargs = {}
        if USER_AGENT:
            context_kwargs['user_agent'] = USER_AGENT
        # create a persistent browser context
        self.context = await self.browser.new_context(**context_kwargs)
        return self.context

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            await self.context.close()
        except Exception:
            pass
        await self.browser.close()
        await self.playwright.stop()


async def _safe_text(el):
    """Return stripped text_content of element or None if not available."""
    try:
        if el is None:
            return None
        txt = await el.text_content()
        if txt is None:
            return None
        return txt.strip()
    except Exception:
        return None


async def _safe_attr(el, name):
    """Return attribute value or None safely."""
    try:
        if el is None:
            return None
        return await el.get_attribute(name)
    except Exception:
        return None


async def _parse_date_string(date_str):
    """Try to parse a date string and return YYYY-MM-DD or None."""
    if not date_str:
        return None
    try:
        dt = parse(date_str, fuzzy=True)
        return dt.date().isoformat()
    except Exception:
        return None


async def scrape_page(page) -> List[dict]:
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
    seen_urls = set()

    # ensure page has settled a bit
    try:
        await page.wait_for_load_state('networkidle', timeout=10000)
    except Exception:
        pass

    # Find candidate containers using a broad list of selectors to be resilient.
    try:
        containers = await page.query_selector_all(COMBINED_ITEM_SELECTOR)
    except Exception:
        containers = []

    # If no containers found, fall back to selecting items that contain links in article listing regions:
    if not containers:
        try:
            containers = await page.query_selector_all("main, .content, .site-content, #content, .archive, .blog")
            # if that yields containers, we'll search anchors inside them later
        except Exception:
            containers = []

    # Primary pass: if we have container elements, extract items from them
    if containers:
        for el in containers:
            try:
                # Attempt to find a title anchor inside the container using common heading selectors
                title_el = await el.query_selector("h1 a, h2 a, h3 a, .entry-title a, .post-title a, a[rel='bookmark'], a.title, .title a")

                # If no heading anchor, try the first anchor with visible text
                if not title_el:
                    anchors = await el.query_selector_all("a[href]")
                    title_el = None
                    for a in anchors:
                        txt = await _safe_text(a)
                        href = await _safe_attr(a, "href")
                        if txt and href and len(txt) > 3:
                            title_el = a
                            break

                title = await _safe_text(title_el) if title_el else None

                # If title still missing, try aria-label or title attribute
                if not title and title_el:
                    title = (await _safe_attr(title_el, "aria-label")) or (await _safe_attr(title_el, "title"))
                    if title:
                        title = title.strip()

                # Extract URL - prioritize href from title anchor
                url = None
                if title_el:
                    href = await _safe_attr(title_el, "href")
                    if href:
                        url = urllib.parse.urljoin(base_url, href.strip())

                # If still no URL, try first anchor in container
                if not url:
                    first_anchor = await el.query_selector("a[href]")
                    if first_anchor:
                        href = await _safe_attr(first_anchor, "href")
                        if href:
                            url = urllib.parse.urljoin(base_url, href.strip())

                # Normalize url (remove fragments)
                if url:
                    try:
                        parsed = urllib.parse.urlparse(url)
                        url = urllib.parse.urlunparse(parsed._replace(fragment=""))
                    except Exception:
                        pass

                # Extract date: prefer <time datetime> or time text, then common classes
                date = None
                time_el = await el.query_selector("time[datetime], time")
                if time_el:
                    datetime_attr = await _safe_attr(time_el, "datetime")
                    if datetime_attr:
                        date = await _parse_date_string(datetime_attr)
                    else:
                        time_text = await _safe_text(time_el)
                        date = await _parse_date_string(time_text)

                if not date:
                    # try common date class selectors
                    date_candidates = await el.query_selector_all(".post-date, .entry-date, .date, .published, .meta .date, .post-meta time, .meta-date, .time")
                    for dc in date_candidates:
                        txt = await _safe_text(dc)
                        date = await _parse_date_string(txt)
                        if date:
                            break

                # Required fields: title and url
                if not title or not url:
                    # skip incomplete items
                    continue

                # De-duplicate by URL
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                items.append({
                    'title': title,
                    'date': date,
                    'url': url,
                    'scraper': SCRAPER_MODULE_PATH,
                })

            except Exception:
                # be tolerant of malformed items; skip and continue
                continue

    # Secondary fallback: If no items found from containers, scan anchors across the page and heuristically detect article links.
    if not items:
        try:
            anchors = await page.query_selector_all("main a[href], article a[href], a[href]")
        except Exception:
            anchors = []

        for a in anchors:
            try:
                href = await _safe_attr(a, "href")
                txt = await _safe_text(a)
                # try to get title from aria-label/title attr if text is empty
                if not txt or len(txt) < 4:
                    txt = (await _safe_attr(a, "aria-label")) or (await _safe_attr(a, "title")) or txt
                if not href:
                    continue
                href = href.strip()
                # Skip fragments and mailto/tel
                if href.startswith('#') or href.startswith('mailto:') or href.startswith('tel:'):
                    continue
                # Normalize
                url = urllib.parse.urljoin(base_url, href)
                # Skip obvious non-article assets
                lower = url.lower()
                if any(lower.endswith(ext) for ext in ('.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp', '.pdf')):
                    continue
                # Skip links to categories or tags or pagination
                if '/category/' in url or '/tag/' in url or '/page/' in url:
                    # but allow if link text looks like an article (long title)
                    if not txt or len(txt) < 10:
                        continue
                # Heuristic: require reasonable title text
                if not txt or len(txt) < 8:
                    continue

                # Remove fragment
                try:
                    parsed = urllib.parse.urlparse(url)
                    url = urllib.parse.urlunparse(parsed._replace(fragment=""))
                except Exception:
                    pass

                if url in seen_urls:
                    continue
                seen_urls.add(url)

                items.append({
                    'title': txt.strip(),
                    'date': None,
                    'url': url,
                    'scraper': SCRAPER_MODULE_PATH,
                })
            except Exception:
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
    # 1) Try to find an explicit "next page" link (href navigation)
    try:
        for sel in NEXT_PAGE_LINK_SELECTORS:
            try:
                el = await page.query_selector(sel)
            except Exception:
                el = None
            if el:
                href = await _safe_attr(el, "href")
                if href:
                    next_url = urllib.parse.urljoin(base_url, href.strip())
                    try:
                        await page.goto(next_url)
                        # allow content to load
                        await page.wait_for_load_state('load', timeout=10000)
                        await page.wait_for_load_state('networkidle', timeout=10000)
                    except PlaywrightTimeoutError:
                        # if navigation didn't finish, still proceed
                        pass
                    return

                # if element is a linkless anchor (JS click)
                try:
                    await el.scroll_into_view_if_needed()
                    await el.click()
                    await page.wait_for_load_state('networkidle', timeout=7000)
                    return
                except Exception:
                    # fall through to next selector
                    pass
    except Exception:
        pass

    # 2) Try to find "load more" buttons and click them (AJAX load)
    try:
        for sel in LOAD_MORE_BUTTON_SELECTORS:
            try:
                btn = await page.query_selector(sel)
            except Exception:
                btn = None
            if btn:
                try:
                    await btn.scroll_into_view_if_needed()
                except Exception:
                    pass
                try:
                    await btn.click()
                    # give AJAX some time
                    await page.wait_for_load_state('networkidle', timeout=7000)
                    # sometimes content loads slowly
                    await page.wait_for_timeout(2000)
                    return
                except Exception:
                    # try JS click fallback
                    try:
                        await page.evaluate("(el) => el.click()", btn)
                        await page.wait_for_timeout(2000)
                        return
                    except Exception:
                        continue
    except Exception:
        pass

    # 3) Fallback: infinite scroll behavior.
    try:
        # Count current items
        try:
            prev_items = await page.query_selector_all(COMBINED_ITEM_SELECTOR)
            prev_count = len(prev_items) if prev_items else 0
        except Exception:
            prev_count = 0

        # Perform a series of scrolls to attempt to load more content
        max_scrolls = 5
        for _ in range(max_scrolls):
            # scroll to bottom
            try:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                pass
            # wait for potential lazy load
            await page.wait_for_timeout(2500)

            # check new count
            try:
                new_items = await page.query_selector_all(COMBINED_ITEM_SELECTOR)
                new_count = len(new_items) if new_items else 0
            except Exception:
                new_count = prev_count

            if new_count > prev_count:
                # new content loaded; return to let caller scrape again
                return
            prev_count = new_count

        # If no new items after scrolling, do one final wait to ensure not missing delayed loads
        await page.wait_for_timeout(2000)
    except Exception:
        # If anything goes wrong, just return and let the caller detect no progress.
        return


async def get_first_page(base_url=base_url):
    """Fetch only the first page of articles."""
    async with PlaywrightContext() as context:
        page = await context.new_page()
        try:
            # apply stealth if available, but do not fail if it errors
            try:
                await Stealth().apply_stealth_async(page)
            except Exception:
                try:
                    # fallback to other possible API name
                    await Stealth().apply_async(page)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            await page.goto(base_url)
            try:
                await page.wait_for_load_state('networkidle', timeout=10000)
            except Exception:
                await page.wait_for_timeout(1500)
            items = await scrape_page(page)
        finally:
            await page.close()
        return items


async def get_all_articles(base_url=base_url, max_pages=100):
    """Fetch all articles from all pages."""

    async with PlaywrightContext() as context:
        items = []
        seen = set()
        page = await context.new_page()
        try:
            try:
                await Stealth().apply_stealth_async(page)
            except Exception:
                try:
                    await Stealth().apply_async(page)
                except Exception:
                    pass
        except Exception:
            pass

        page_count = 0
        await page.goto(base_url)
        try:
            try:
                await page.wait_for_load_state('networkidle', timeout=10000)
            except Exception:
                await page.wait_for_timeout(1500)

            page_count = 0
            item_count = 0  # previous count
            new_item_count = 0  # current count

            try:
                while page_count < max_pages:
                    page_items = await scrape_page(page)
                    for item in page_items:
                        # deduplicate by URL when available, otherwise use title+date
                        key_url = item.get('url')
                        if key_url:
                            key = ('url', key_url)
                        else:
                            key = ('title_date', item.get('title'), item.get('date'))
                        if key not in seen:
                            seen.add(key)
                            items.append(item)
                    new_item_count = len(items)

                    if new_item_count <= item_count:
                        # no progress, stop pagination
                        break

                    page_count += 1
                    item_count = new_item_count

                    await advance_page(page)

            except Exception as e:
                print(f"Error occurred while getting next page: {e}")

        finally:
            try:
                await page.close()
            except Exception:
                pass

        return items


async def main():
    """Main execution function."""
    all_items = await get_all_articles()

    # Save results to JSON
    result_path = os.path.join(os.path.dirname(__file__), 'result.json')
    with open(result_path, 'w', encoding='utf-8') as f:
        json.dump(all_items, f, indent=2, ensure_ascii=False)
    print(f"Results saved to {result_path}")


if __name__ == "__main__":
    asyncio.run(main())
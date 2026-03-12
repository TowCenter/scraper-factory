"""
Articles Scraper for Axios Twin Cities

Generated at: 2026-03-10 14:36:21
Target URL: https://www.axios.com/local/twin-cities/news
Generated using: gpt-5-mini-2025-08-07
Content type: articles
Fields: title, date, url

"""

import json
import os
import re
import urllib.parse
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import asyncio

base_url = 'https://www.axios.com/local/twin-cities/news'

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

    # Strategy:
    # - Find article anchors with data-cy="story-promo-headline"
    # - Get title from the <h2> inside the anchor (or anchor text)
    # - Get date by looking for a nearby p[data-cy="timestamp"] or time element inside the same <li>
    # - If timestamp is relative (contains "ago", "hour", etc.), try to extract date from the article URL (common Axios pattern /YYYY/MM/DD/)
    # - Normalize URL to absolute using base_url

    anchors = await page.query_selector_all('a[data-cy="story-promo-headline"][href]')
    for a in anchors:
        try:
            # URL
            href = await a.get_attribute('href')
            if not href:
                continue
            url = urllib.parse.urljoin(base_url, href)

            # Title: prefer h2 text, fallback to anchor text content
            title_text = None
            h2 = await a.query_selector('h2')
            if h2:
                title_text = (await h2.text_content()) or ''
            if not title_text:
                title_text = (await a.text_content()) or ''
            title = title_text.strip() if title_text else None
            if not title:
                # skip items without titles
                continue

            # Date: search within closest li for a timestamp or time element
            date_text = await page.evaluate(
                """(el) => {
                    const li = el.closest('li');
                    if (!li) return null;
                    // Prefer explicit timestamp or time element; use common classes as fallback
                    const selectors = [
                        'time',
                        'p[data-cy=\"timestamp\"]',
                        'p.label-utility-020-thin',
                        'p.mb-1',
                        'span.timestamp',
                        'div.timestamp'
                    ];
                    for (const sel of selectors) {
                        const node = li.querySelector(sel);
                        if (node && node.textContent && node.textContent.trim()) {
                            return node.textContent.trim();
                        }
                    }
                    return null;
                }""",
                a
            )

            date_iso = None
            # Helper: try to parse human-readable absolute dates
            def try_parse_date(txt):
                try:
                    dt = parse(txt, fuzzy=True)
                    return dt.date().isoformat()
                except Exception:
                    return None

            if date_text:
                # If date_text contains relative indicators, avoid parsing directly
                if re.search(r'\b(ago|hour|hours|minute|minutes|yesterday|today)\b', date_text, re.I):
                    date_iso = None
                else:
                    date_iso = try_parse_date(date_text)

            # If we still don't have a date, try to extract from URL: /YYYY/MM/DD/ or /YYYY/MM/DD
            if not date_iso:
                m = re.search(r'/(\d{4})/(\d{2})/(\d{2})/', href)
                if not m:
                    # try also when URL ends with /YYYY/MM/DD or contains /YYYY/MM/DD-
                    m = re.search(r'/(\d{4})/(\d{2})/(\d{2})(?:$|/|[-_])', href)
                if m:
                    y, mo, d = m.group(1), m.group(2), m.group(3)
                    try:
                        date_iso = f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
                    except Exception:
                        date_iso = None

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
    # Helper selector for article anchors
    ARTICLE_SEL = 'a[data-cy="story-promo-headline"][href]'

    # Get current article count so we can detect if clicking loads more
    try:
        pre_anchors = await page.query_selector_all(ARTICLE_SEL)
        pre_count = len(pre_anchors)
    except Exception:
        pre_count = 0

    # Utility to check if an element is visible
    async def is_visible(el):
        try:
            return await page.evaluate(
                '(e) => !!(e && (e.offsetWidth || e.offsetHeight || e.getClientRects().length))',
                el
            )
        except Exception:
            return False

    # Avoid clicking elements that are clearly auth/subscribe related
    def looks_like_auth(text, aria, data_cy, cls):
        text_l = (text or '').lower()
        aria_l = (aria or '').lower()
        dc = (data_cy or '').lower()
        c = (cls or '').lower()
        if any(k in text_l for k in ['login', 'log in', 'sign in', 'subscribe', 'account']):
            return True
        if any(k in aria_l for k in ['login', 'sign in', 'subscribe', 'account']):
            return True
        if 'local-login' in dc or 'login' in dc:
            return True
        if 'login' in c:
            return True
        return False

    # Prefer explicit pagination / load-more elements first with robust heuristics
    try:
        # Try common explicit selectors that likely represent loading more content or navigation
        explicit_selectors = [
            'a[rel="next"]',
            'a.pagination-next',
            'a[aria-label="Next"]',
            'a[aria-label*="next"]',
            'button.load-more',
            'button[data-cy*="load-more"]',
            'button[data-cy*="more"]',
            'button.bttn',
            'a:has-text("More")',
            'button:has-text("More")',
            'a:has-text("Load more")',
            'button:has-text("Load more")',
            'a:has-text("Show more")',
            'button:has-text("Show more")',
        ]

        # First pass: try explicit selectors but validate they are not auth widgets
        for sel in explicit_selectors:
            try:
                el = await page.query_selector(sel)
            except Exception:
                el = None
            if not el:
                continue
            try:
                text = (await el.text_content() or '').strip()
            except Exception:
                text = ''
            try:
                aria = (await el.get_attribute('aria-label') or '').strip()
            except Exception:
                aria = ''
            try:
                data_cy = (await el.get_attribute('data-cy') or '').strip()
            except Exception:
                data_cy = ''
            try:
                cls = (await el.get_attribute('class') or '').strip()
            except Exception:
                cls = ''
            # Skip auth buttons
            if looks_like_auth(text, aria, data_cy, cls):
                continue
            # Ensure element is visible
            if not await is_visible(el):
                # try to scroll into view anyway; sometimes off-screen but still clickable
                try:
                    await el.scroll_into_view_if_needed()
                except Exception:
                    pass
                if not await is_visible(el):
                    continue
            # Try clicking and wait for new content or navigation
            try:
                initial_url = page.url
                await el.scroll_into_view_if_needed()
                try:
                    await el.click(timeout=3000)
                except Exception:
                    # fallback to DOM click
                    try:
                        await page.evaluate('(e) => e.click()', el)
                    except Exception:
                        pass
                # Wait for either new articles to appear or navigation
                try:
                    await page.wait_for_function(
                        "sel, prev => document.querySelectorAll(sel).length > prev",
                        ARTICLE_SEL,
                        pre_count,
                        timeout=8000
                    )
                    return
                except Exception:
                    # maybe navigation happened
                    try:
                        await page.wait_for_load_state('networkidle', timeout=5000)
                    except Exception:
                        await page.wait_for_timeout(2000)
                    # check if url changed or more items
                    if page.url != initial_url:
                        # navigation occurred; consider success
                        return
                    # final check of article count
                    try:
                        post_anchors = await page.query_selector_all(ARTICLE_SEL)
                        if len(post_anchors) > pre_count:
                            return
                    except Exception:
                        pass
                    # if nothing changed, continue trying other candidates
                    continue
            except Exception:
                continue

        # Second pass: scan all anchors and buttons with broader heuristics
        candidates = await page.query_selector_all('a, button')
        for el in candidates:
            try:
                text = (await el.text_content() or '').strip()
            except Exception:
                text = ''
            try:
                aria = (await el.get_attribute('aria-label') or '').strip()
            except Exception:
                aria = ''
            try:
                rel = (await el.get_attribute('rel') or '').strip()
            except Exception:
                rel = ''
            try:
                data_cy = (await el.get_attribute('data-cy') or '').strip()
            except Exception:
                data_cy = ''
            try:
                cls = (await el.get_attribute('class') or '').strip()
            except Exception:
                cls = ''
            try:
                href = (await el.get_attribute('href') or '').strip()
            except Exception:
                href = ''

            # skip auth-like controls
            if looks_like_auth(text, aria, data_cy, cls):
                continue

            # Heuristics to detect next/load more
            is_next = False
            if 'next' in rel.lower() or 'next' in aria.lower():
                is_next = True
            # textual heuristics
            t_lower = text.lower()
            if any(kw in t_lower for kw in ['load more', 'loadmore', 'show more', 'see more', 'more', 'next']):
                # ensure it's not a very short "more" used in unrelated places with no visibility
                is_next = True
            if 'load-more' in data_cy or 'loadmore' in data_cy or 'feed-load' in data_cy:
                is_next = True
            if any(kw in cls.lower() for kw in ['load', 'more', 'next', 'pagination', 'pager', 'bttn']):
                is_next = True

            if not is_next:
                continue

            # check visibility
            if not await is_visible(el):
                try:
                    await el.scroll_into_view_if_needed()
                except Exception:
                    pass
                if not await is_visible(el):
                    continue

            # Attempt click / navigation
            try:
                initial_url = page.url
                await el.scroll_into_view_if_needed()
                try:
                    await el.click(timeout=3000)
                except Exception:
                    # fallback DOM click
                    try:
                        await page.evaluate('(e) => e.click()', el)
                    except Exception:
                        pass

                # Wait for new content or navigation
                try:
                    await page.wait_for_function(
                        "sel, prev => document.querySelectorAll(sel).length > prev",
                        ARTICLE_SEL,
                        pre_count,
                        timeout=8000
                    )
                    return
                except Exception:
                    try:
                        await page.wait_for_load_state('networkidle', timeout=5000)
                    except Exception:
                        await page.wait_for_timeout(2000)
                    if page.url != initial_url:
                        return
                    try:
                        post_anchors = await page.query_selector_all(ARTICLE_SEL)
                        if len(post_anchors) > pre_count:
                            return
                    except Exception:
                        pass
                    continue
            except Exception:
                continue

        # If we reach here, no explicit "next" was found or clicking didn't produce more content
    except Exception:
        # unexpected errors – fall back to infinite scroll below
        pass

    # Infinite scroll fallback: attempt a few scrolls until height stabilizes
    try:
        last_height = await page.evaluate('() => document.body.scrollHeight')
        for _ in range(6):
            await page.evaluate('() => window.scrollTo(0, document.body.scrollHeight)')
            # allow potential lazy-loaded content to load
            await page.wait_for_timeout(2000)
            new_height = await page.evaluate('() => document.body.scrollHeight')
            if new_height == last_height:
                # small extra wait to let any JS trigger
                await page.wait_for_timeout(1000)
                new_height2 = await page.evaluate('() => document.body.scrollHeight')
                if new_height2 == last_height:
                    break
                else:
                    last_height = new_height2
            else:
                last_height = new_height
    except Exception:
        # If scroll fails, simply wait briefly
        await page.wait_for_timeout(2000)
    # end advance_page

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
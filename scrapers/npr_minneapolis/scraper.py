import json
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio

base_url = 'https://www.npr.org/search/?query=minneapolis&page=1&sortType=byDate'

# Scraper module path for tracking the source of scraped data
SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

# Operator user-agent (set in operator.json)
USER_AGENT = ''

class PlaywrightContext:
    """Context manager for Playwright browser sessions."""

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        # headless by default for automated environments
        self.browser = await self.playwright.chromium.launch(headless=True)
        context_kwargs = {'user_agent': USER_AGENT} if USER_AGENT else {}
        self.context = await self.browser.new_context(**context_kwargs)
        return self.context

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.browser.close()
        await self.playwright.stop()

async def _format_date(dt_str):
    """
    Parse a date/time string into YYYY-MM-DD or return None if parsing fails.
    Accepts ISO datetimes or human-readable time strings.
    """
    if not dt_str:
        return None
    try:
        dt = parse(dt_str)
        return dt.date().isoformat()
    except Exception:
        # Try stripping common separators and reparse
        try:
            cleaned = dt_str.strip().replace('•', '').replace('·', '')
            dt = parse(cleaned, fuzzy=True)
            return dt.date().isoformat()
        except Exception:
            return None

async def scrape_page(page):
    """
    Extract article data from the current page.

    Parameters:
        page: Playwright page object

    Returns:
        List of dictionaries containing article data with keys:
        - title: Headline or title of the article
        - date: Publication date in YYYY-MM-DD format
        - url: Link to the full article
        - scraper: module path for traceability
    """
    items = []

    # Broader set of selectors to match NPR search results variants
    item_selectors = [
        'ul.ais-InfiniteHits-list > li.ais-InfiniteHits-item',
        'div.item',
        'article.item',
        'li.search-result',
        'div.storyInfo',
        'div.result',
        'div.search-result'
    ]

    elements = []
    for sel in item_selectors:
        elements = await page.query_selector_all(sel)
        if elements:
            break

    # If nothing matched, try a generic result area (fallback)
    if not elements:
        # try to select anchors in results region
        results_region = await page.query_selector_all('main a[href], .search-results a[href]')
        if not results_region:
            return items
        # create pseudo-elements from anchors for simple extraction
        for a in results_region:
            try:
                href = await a.get_attribute('href')
                if not href:
                    continue
                href = urllib.parse.urljoin('https://www.npr.org', href)
                text = (await a.text_content() or '').strip()
                if not text:
                    text = (await a.get_attribute('title') or '').strip()
                if not text:
                    continue
                items.append({
                    'title': text,
                    'date': None,
                    'url': href,
                    'scraper': SCRAPER_MODULE_PATH,
                })
            except Exception:
                continue
        return items

    for el in elements:
        try:
            title = None
            url = None

            # Prefer explicit headline anchor inside common headline tags
            headline_anchor = await el.query_selector('h1 a, h2 a, h3 a, h4 a, .title a, .headline a')
            if headline_anchor:
                href = await headline_anchor.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin('https://www.npr.org', href)
                raw_title = await headline_anchor.text_content()
                if raw_title:
                    title = raw_title.strip()

            # If not found, look for any meaningful anchor inside the item
            if not url or not title:
                anchors = await el.query_selector_all('a[href]')
                if anchors:
                    for a in anchors:
                        try:
                            href = await a.get_attribute('href')
                            if not href:
                                continue
                            href = href.strip()
                            # skip anchors that are anchors or mailto/javascript
                            if href.startswith('#') or href.startswith('javascript:') or href.startswith('mailto:'):
                                continue
                            text = (await a.text_content() or '').strip()
                            title_attr = await a.get_attribute('title') or ''
                            # prefer anchors with visible text
                            if text:
                                if not title:
                                    title = text
                                if not url:
                                    url = urllib.parse.urljoin('https://www.npr.org', href)
                                # prefer the first anchor with non-empty text/href as the article link
                                break
                            elif title_attr and not title:
                                title = title_attr.strip()
                                if not url:
                                    url = urllib.parse.urljoin('https://www.npr.org', href)
                                break
                        except Exception:
                            continue

            # Additional fallback: heading text without anchor
            if not title:
                heading = await el.query_selector('h1, h2, h3, h4, .title, .headline')
                if heading:
                    ht = await heading.text_content()
                    if ht:
                        title = ht.strip()

            # Fallback for URL: sometimes data-url or link in meta
            if not url:
                data_url = await el.get_attribute('data-url')
                if data_url:
                    url = urllib.parse.urljoin('https://www.npr.org', data_url)

            # Final fallback: first anchor even if textless
            if not url:
                any_a = await el.query_selector('a[href]')
                if any_a:
                    href = await any_a.get_attribute('href')
                    if href:
                        url = urllib.parse.urljoin('https://www.npr.org', href)

            # Date extraction: try <time>, then common date spans
            date = None
            time_el = await el.query_selector('time')
            if time_el:
                datetime_attr = await time_el.get_attribute('datetime')
                if datetime_attr:
                    date = await _format_date(datetime_attr)
                else:
                    txt = (await time_el.text_content() or '').strip()
                    date = await _format_date(txt)
            if not date:
                span_date = await el.query_selector('span.date, .teaser-date, .byline-date, .pub-date')
                if span_date:
                    txt = (await span_date.text_content() or '').strip()
                    date = await _format_date(txt)

            # Ensure required fields exist; title and url are required
            if not title or not url:
                continue

            items.append({
                'title': title,
                'date': date,
                'url': url,
                'scraper': SCRAPER_MODULE_PATH,
            })

        except Exception:
            # Protect the scraper from a single-bad-item crash; skip item
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
    async def _count_items():
        els = await page.query_selector_all('ul.ais-InfiniteHits-list > li.ais-InfiniteHits-item')
        if els:
            return len(els)
        els = await page.query_selector_all('article.item, div.item, div.storyInfo, li.search-result')
        return len(els)

    try:
        # 1) Load more button
        load_more = await page.query_selector('button.ais-InfiniteHits-loadMore, button.load-more, button[data-action="load-more"]')
        if load_more:
            try:
                await load_more.scroll_into_view_if_needed()
                before = await _count_items()
                await load_more.click()
                for _ in range(15):
                    await page.wait_for_timeout(500)
                    after = await _count_items()
                    if after > before:
                        return
                return
            except Exception:
                pass

        # 2) Next-page link (common patterns)
        next_link_selectors = [
            'a[rel="next"]',
            'a.next, a[aria-label="Next"], a[aria-label="next"]',
            'ul.pagination a.next',
            '.pagination a[rel="next"]'
        ]
        for sel in next_link_selectors:
            next_a = await page.query_selector(sel)
            if next_a:
                href = await next_a.get_attribute('href')
                if href:
                    next_url = urllib.parse.urljoin('https://www.npr.org', href)
                    try:
                        await page.goto(next_url)
                        await page.wait_for_load_state('networkidle')
                        return
                    except Exception:
                        try:
                            await next_a.scroll_into_view_if_needed()
                            await next_a.click()
                            await page.wait_for_load_state('networkidle')
                            return
                        except Exception:
                            pass

        # 3) Infinite scroll fallback
        before = await _count_items()
        for _ in range(6):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1000)
            after = await _count_items()
            if after > before:
                return
        await page.wait_for_timeout(2000)
        return

    except Exception:
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
        try:
            await page.wait_for_load_state('networkidle', timeout=5000)
        except Exception:
            # continue even if networkidle not achieved
            pass
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
            await page.wait_for_load_state('networkidle', timeout=5000)
        except Exception:
            pass

        page_count = 0
        item_count = 0  # previous count
        new_item_count = 0  # current count

        try:
            while page_count < max_pages:
                page_items = await scrape_page(page)
                for item in page_items:
                    key = (item.get('title'), item.get('url'), item.get('date'))
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
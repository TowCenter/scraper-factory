import json
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio

base_url = 'https://www.zerkalomn.com/news'

# Scraper module path for tracking the source of scraped data
SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

# Operator user-agent (set in operator.json)
USER_AGENT = ''

class PlaywrightContext:
    """Context manager for Playwright browser sessions."""

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        # Headless by default; to debug set headless=False here
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
        - url: Link to the full article (absolute URL or None)
        - scraper: module path for traceability (SCRAPER_MODULE_PATH)
    """
    items = []

    # Use article container selector observed on the site
    article_selector = 'div.blog-post'
    article_handles = await page.query_selector_all(article_selector)

    for handle in article_handles:
        # Title: anchor inside h2.blog-title or any anchor with class blog-title-link
        title = None
        url = None
        date = None

        try:
            title_anchor = await handle.query_selector('h2.blog-title a.blog-title-link, a.blog-title-link')
            if title_anchor:
                raw_title = await title_anchor.text_content()
                if raw_title:
                    title = raw_title.strip() or None
                href = await title_anchor.get_attribute('href')
                if href:
                    # Resolve relative and scheme-relative URLs
                    url = urllib.parse.urljoin(page.url, href.strip())
        except Exception:
            # Safely ignore per-item extraction errors and continue
            title = title or None
            url = url or None

        # Date: span.date-text or p.blog-date
        try:
            date_el = await handle.query_selector('span.date-text, p.blog-date')
            if date_el:
                raw_date = await date_el.text_content()
                if raw_date:
                    # Normalize whitespace and remove non-breaking spaces
                    raw_date = " ".join(raw_date.split())
                    try:
                        dt = parse(raw_date, fuzzy=True)
                        date = dt.date().isoformat()
                    except Exception:
                        date = None
        except Exception:
            date = None

        # Ensure required fields: title and url. If missing, still include but set to None per instructions.
        item = {
            'title': title,
            'date': date,
            'url': url,
            'scraper': SCRAPER_MODULE_PATH,
        }
        items.append(item)

    return items

async def advance_page(page):
    """
    Finds the next page button or link to navigate to the next page of articles.
    Clicks button or navigates to next page URL if found. Scrolls load more into view if not visible.
    Falls back to infinite scroll if no pagination found.

    Parameters:
        page: Playwright page object
    """

    # Prioritized selectors including ones targeting "previous" because the site uses that for older pages
    candidates = [
        'a.blog-link[href*="/previous"]',
        'a.blog-link[href*="/next"]',
        'div.blog-page-nav-previous a.blog-link',
        'div.blog-page-nav-next a.blog-link',
        'a.blog-link[rel="next"]',
        'nav a.blog-link',
        'a.blog-link',
    ]

    for sel in candidates:
        try:
            locator = page.locator(sel)
            count = await locator.count()
            if not count:
                continue

            # Try each matched element to find a valid navigation target
            for idx in range(count):
                el = locator.nth(idx)
                try:
                    href = await el.get_attribute('href')
                except Exception:
                    href = None

                # If there's an href, prefer navigating by URL (more reliable)
                if href:
                    next_url = urllib.parse.urljoin(page.url, href.strip())
                    # Skip if the target URL is same as current
                    if next_url == page.url:
                        # try clicking instead if it might trigger JS navigation
                        pass
                    else:
                        try:
                            await page.goto(next_url, wait_until='networkidle', timeout=15000)
                            # Ensure some content loaded (a minimal sanity check)
                            await page.wait_for_selector('div.blog-post', timeout=10000)
                            return
                        except Exception:
                            # If goto fails, fall back to attempting a click
                            try:
                                await el.scroll_into_view_if_needed()
                                # click and wait for navigation
                                await asyncio.gather(
                                    page.wait_for_navigation(wait_until='networkidle', timeout=15000),
                                    el.click(),
                                )
                                # Wait for article container
                                await page.wait_for_selector('div.blog-post', timeout=10000)
                                return
                            except Exception:
                                # continue trying other matches/selectors
                                continue
                else:
                    # No href: try clicking (could be a button)
                    try:
                        await el.scroll_into_view_if_needed()
                        await asyncio.gather(
                            page.wait_for_navigation(wait_until='networkidle', timeout=15000),
                            el.click(),
                        )
                        await page.wait_for_selector('div.blog-post', timeout=10000)
                        return
                    except Exception:
                        # If clicking didn't trigger navigation, try a short wait for SPA content change
                        try:
                            await el.click()
                            await page.wait_for_timeout(3000)
                            return
                        except Exception:
                            continue
        except Exception:
            # ignore selector errors and try next candidate
            continue

    # Fallback: infinite scroll - scroll to bottom and wait for new content to load
    try:
        # Perform a few incremental scrolls to trigger lazy loading
        await page.evaluate(
            """() => {
                return new Promise((resolve) => {
                  const distance = Math.max(document.documentElement.clientHeight, 1000);
                  let total = 0;
                  const timer = setInterval(() => {
                    window.scrollBy(0, distance);
                    total += distance;
                    if (total > document.body.scrollHeight) {
                      clearInterval(timer);
                      resolve(true);
                    }
                  }, 500);
                  // Safety resolve after 3.5s if not reached bottom
                  setTimeout(() => { clearInterval(timer); resolve(true); }, 3500);
                });
            }"""
        )
        # Wait a little for network requests to finish and new items to render
        await page.wait_for_timeout(3000)
    except Exception:
        # As a last resort, do a single scroll and wait
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(3000)
        except Exception:
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
                    # key excludes None values to match dedupe behavior in template
                    key = tuple(sorted((k, v) for k, v in item.items() if v is not None))
                    if key not in seen:
                        seen.add(key)
                        items.append(item)
                new_item_count = len(items)

                # If no new items were added, stop
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
    with open(result_path, 'w', encoding='utf-8') as f:
        json.dump(all_items, f, indent=2, ensure_ascii=False)
    print(f"Results saved to {result_path}")

if __name__ == "__main__":
    asyncio.run(main())
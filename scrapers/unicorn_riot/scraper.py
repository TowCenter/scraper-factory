import json
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio

base_url = 'https://unicornriot.ninja/'

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
        - url: Link to the full article
        - scraper: module path for traceability
    """
    items = []

    # Select main article containers on this site (covers both standard posts and carousel slides)
    article_selectors = 'article.post, article.f-carousel__slide'
    nodes = await page.query_selector_all(article_selectors)

    for node in nodes:
        try:
            # Title: prefer the anchor inside h3.title
            title = None
            url = None
            title_anchor = await node.query_selector('h3.title.entry-title a, h3.title a, h3.entry-title a')
            if title_anchor:
                title = (await title_anchor.text_content() or '').strip()
                url = await title_anchor.get_attribute('href')
            else:
                # Fallback: any h3.title text or first anchor text
                h3 = await node.query_selector('h3.title.entry-title, h3.title, h3.entry-title')
                if h3:
                    title = (await h3.text_content() or '').strip()
                    # try to find an anchor inside h3 if not captured earlier
                    h3_a = await h3.query_selector('a')
                    if h3_a and not url:
                        url = await h3_a.get_attribute('href')
                if not title:
                    # last resort: first anchor text inside article
                    first_a = await node.query_selector('a')
                    if first_a:
                        title = (await first_a.text_content() or '').strip()
                        if not url:
                            url = await first_a.get_attribute('href')

            # Normalize URL to absolute if present
            if url:
                url = urllib.parse.urljoin(base_url, url)

            # Date: look for published time element
            date_val = None
            time_el = await node.query_selector('time.entry-date.published, .subtitle.is-5 time.entry-date.published, time.published, time')
            if time_el:
                datetime_attr = await time_el.get_attribute('datetime')
                if datetime_attr:
                    try:
                        date_val = parse(datetime_attr).date().isoformat()
                    except Exception:
                        date_val = None
                if date_val is None:
                    # fallback to parsing visible text
                    time_text = (await time_el.text_content() or '').strip()
                    if time_text:
                        try:
                            date_val = parse(time_text).date().isoformat()
                        except Exception:
                            date_val = None

            # At minimum we require title and url; if they are missing skip this node
            if not title and not url:
                continue

            items.append({
                'title': title or None,
                'date': date_val,
                'url': url or None,
                'scraper': SCRAPER_MODULE_PATH,
            })

        except Exception:
            # Robustness: skip malformed nodes rather than crash
            continue

    return items

async def advance_page(page):
    """
    Finds the next page button or link to navigate to the next page of articles.
    Clicks button or navigates to next page URL if found. Scrolls load more button into view if not visible.
    Falls back to infinite scroll if no pagination found.

    Parameters:
        page: Playwright page object
    """
    # Prioritize explicit "next" pagination links/buttons
    pagination_selectors = [
        'a.pagination-next',                         # rel="next" style link
        'a.pagination-link.larger[rel="next"]',      # alternate next indication
        'a.pagination-link.larger[title^="Page"]',   # numbered page links (pick next by index/href)
        'a.pagination-link.larger',                  # generic pagination links
        'a.pagination-last'                          # link to last page (used if no next available)
    ]

    for sel in pagination_selectors:
        try:
            el = await page.query_selector(sel)
            if not el:
                continue
            href = await el.get_attribute('href')
            # If element has a href, navigate to it. Prefer clicking if element is attached/visible.
            if href:
                next_url = urllib.parse.urljoin(base_url, href)
                try:
                    # Scroll into view then click; if navigation is triggered we'll wait for load
                    await el.scroll_into_view_if_needed()
                    await el.click()
                    # Give the page time to navigate/load content (some sites change URL, some fetch async)
                    await page.wait_for_load_state('load', timeout=5000)
                    await page.wait_for_timeout(1000)
                except Exception:
                    # If click fails (detached element or JS preventing direct click), fallback to goto
                    await page.goto(next_url)
                    await page.wait_for_timeout(1000)
                return
            else:
                # If link lacks href but is clickable (e.g., a button), try clicking it
                try:
                    await el.scroll_into_view_if_needed()
                    await el.click()
                    await page.wait_for_timeout(1500)
                    return
                except Exception:
                    continue
        except Exception:
            continue

    # If no pagination elements found, fallback to an infinite-scroll style load
    # Attempt a small series of scrolls to trigger lazy loading / "load more" behaviors
    try:
        last_height = await page.evaluate("() => document.body.scrollHeight")
        max_scroll_attempts = 5
        for _ in range(max_scroll_attempts):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            # Wait for potential new content to load
            await page.wait_for_timeout(2000)
            new_height = await page.evaluate("() => document.body.scrollHeight")
            if new_height == last_height:
                # No new content loaded; break early
                break
            last_height = new_height
    except Exception:
        # If any JS evaluation fails, fallback to simple wait
        await page.wait_for_timeout(2000)

    return

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
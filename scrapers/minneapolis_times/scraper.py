import json
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio

base_url = 'https://minneapolistimes.com/category/immigration-enforcement/'

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

    # Choose a reasonably broad and stable container selector observed on the page.
    # div.td-module-container appears to be the parent of title, url and date in examples.
    article_elements = await page.query_selector_all('div.td-module-container')

    for el in article_elements:
        try:
            # Title and URL: prefer the headline link under h3.entry-title.td-module-title a
            title_el = await el.query_selector('h3.entry-title.td-module-title a, h3.entry-title a')
            if title_el:
                title = (await title_el.text_content() or "").strip()
                url = await title_el.get_attribute('href')
                if url:
                    url = urllib.parse.urljoin(base_url, url.strip())
                else:
                    url = None
            else:
                # Fallback: sometimes image link contains the article link
                img_link = await el.query_selector('a.td-image-wrap')
                if img_link:
                    url = await img_link.get_attribute('href')
                    url = urllib.parse.urljoin(base_url, url.strip()) if url else None
                else:
                    url = None
                # Try to derive title from any headline-like element
                title_fallback = await el.query_selector('h3, .td-module-title')
                title = (await title_fallback.text_content() or "").strip() if title_fallback else None

            # Date: prefer the <time> element's datetime attribute if present.
            date_value = None
            time_el = await el.query_selector('time.entry-date, span.td-post-date time, time')
            if time_el:
                # Prefer datetime attribute
                datetime_attr = await time_el.get_attribute('datetime')
                date_text = None
                if datetime_attr:
                    date_text = datetime_attr.strip()
                else:
                    # fallback to text content
                    date_text = (await time_el.text_content() or "").strip()

                if date_text:
                    try:
                        parsed = parse(date_text)
                        date_value = parsed.date().isoformat()
                    except Exception:
                        date_value = None
            else:
                # Some entries embed the author/date in span.td-author-date; try to extract a date-like token
                span_date = await el.query_selector('span.td-author-date, .td-editor-date')
                if span_date:
                    txt = (await span_date.text_content() or "").strip()
                    # Attempt to find a parsable date substring
                    try:
                        parsed = parse(txt, fuzzy=True)
                        date_value = parsed.date().isoformat()
                    except Exception:
                        date_value = None

            # Ensure required fields: title and url. If missing, skip this element.
            if not title or not url:
                continue

            items.append({
                'title': title,
                'date': date_value,
                'url': url,
                'scraper': SCRAPER_MODULE_PATH,
            })

        except Exception:
            # Skip malformed entries but continue processing others
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
        selector_candidates = [
            'a.td-ajax-next-page',
            'a[id^="next-page-"]',
            'a#next-page-tdi_90',
            'a[rel="next"]',
            'a.next',
            'a.pagination-next'
        ]

        # Count current number of article containers to wait for an increase
        article_selector = 'div.td-module-container'
        current_count = len(await page.query_selector_all(article_selector))

        next_btn = None
        for sel in selector_candidates:
            el = await page.query_selector(sel)
            if el:
                next_btn = el
                break

        if next_btn:
            try:
                # Ensure visible
                try:
                    await next_btn.scroll_into_view_if_needed()
                except Exception:
                    pass

                # Attempt a robust click: try element.click(), then dispatch MouseEvent if needed
                clicked = False
                try:
                    await next_btn.click(timeout=3000)
                    clicked = True
                except Exception:
                    # Try dispatching a MouseEvent via page.evaluate
                    try:
                        await page.evaluate(
                            "(el) => { el.scrollIntoView(); el.dispatchEvent(new MouseEvent('click', {bubbles:true,cancelable:true,view:window})); }",
                            next_btn
                        )
                        clicked = True
                    except Exception:
                        clicked = False

                # If click succeeded, wait for additional articles to appear (AJAX)
                if clicked:
                    try:
                        await page.wait_for_function(
                            "(o) => document.querySelectorAll(o.sel).length > o.prev",
                            {'sel': article_selector, 'prev': current_count},
                            timeout=10000
                        )
                        return
                    except Exception:
                        # timed out waiting for increased items; fall through to other fallbacks
                        pass

                # If click didn't cause new items, try to follow href if it's a real URL
                try:
                    href = await next_btn.get_attribute('href')
                    if href and href.strip() and not href.strip().startswith('#'):
                        next_url = urllib.parse.urljoin(base_url, href.strip())
                        await page.goto(next_url)
                        await page.wait_for_load_state('networkidle')
                        return
                except Exception:
                    pass

            except Exception:
                # continue to infinite scroll fallback
                pass

        # No explicit next button found or click didn't load new content — use infinite scroll fallback
        previous_height = await page.evaluate('() => document.body.scrollHeight')
        await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
        await page.wait_for_timeout(2500)

        # Wait for more article containers to appear
        try:
            await page.wait_for_function(
                "(o) => document.querySelectorAll(o.sel).length > o.prev",
                {'sel': article_selector, 'prev': current_count},
                timeout=8000
            )
            return
        except Exception:
            # If no new items, attempt another gentle scroll and wait
            new_height = await page.evaluate('() => document.body.scrollHeight')
            if new_height > previous_height:
                await page.wait_for_timeout(1500)
                return
            await page.evaluate('window.scrollBy(0, -300)')
            await page.wait_for_timeout(500)
            await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            await page.wait_for_timeout(2000)
            return

    except Exception:
        # On any unexpected error, ensure we at least attempt a scroll as fallback
        try:
            await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            await page.wait_for_timeout(2000)
        except Exception:
            pass
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
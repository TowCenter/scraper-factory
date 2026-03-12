"""
Articles Scraper for The Wake Magazine

Generated at: 2026-03-10 14:43:49
Target URL: https://wakemag.org/features
Generated using: gpt-5-mini-2025-08-07
Content type: articles
Fields: title, date, url

"""

import json
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio

base_url = 'https://wakemag.org/features'

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

def _absolute_url(href: str, base: str = base_url) -> str:
    """Convert a possibly relative href to an absolute URL using base_url."""
    if not href:
        return None
    return urllib.parse.urljoin(base, href)

def _format_date(date_str: str) -> str:
    """
    Parse a date string (e.g., from datetime attribute or visible text)
    and return ISO date string YYYY-MM-DD. Return None if parsing fails.
    """
    if not date_str:
        return None
    try:
        dt = parse(date_str)
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
        - date: Publication date in YYYY-MM-DD format (or None)
        - url: Absolute link to the full article
        - scraper: module path for traceability
    """
    items = []

    # Use article.BlogList-item as the article container selector.
    # Inside each container, title is in a.BlogList-item-title and date in time.Blog-meta-item--date.
    article_selector = 'article.BlogList-item'
    title_selector = 'a.BlogList-item-title'
    date_selector = 'time.Blog-meta-item--date'

    # Query all article containers
    article_elements = await page.query_selector_all(article_selector)

    for art in article_elements:
        try:
            # Title (required)
            title_el = await art.query_selector(title_selector)
            if not title_el:
                # Skip items without a title
                continue
            # Use text_content() per instruction (works even if element is hidden)
            title_text = await title_el.text_content()
            title_text = title_text.strip() if title_text else None
            if not title_text:
                # Skip if title is empty
                continue

            # URL (required) - prefer href from the title link
            href = await title_el.get_attribute('href')
            url = _absolute_url(href) if href else None
            if not url:
                # fallback to image link within the article item
                img_link = await art.query_selector('a.BlogList-item-image-link')
                if img_link:
                    href2 = await img_link.get_attribute('href')
                    url = _absolute_url(href2) if href2 else None

            # Date (optional) - check for datetime attribute first, then text content
            date_el = await art.query_selector(date_selector)
            date_iso = None
            if date_el:
                datetime_attr = await date_el.get_attribute('datetime')
                if datetime_attr:
                    date_iso = _format_date(datetime_attr)
                else:
                    date_text = await date_el.text_content()
                    date_iso = _format_date(date_text)

            item = {
                'title': title_text,
                'date': date_iso,
                'url': url,
                'scraper': SCRAPER_MODULE_PATH,
            }
            items.append(item)
        except Exception:
            # Do not allow a single malformed article to break the scraper.
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
    # Primary pagination selector observed on the site.
    pagination_link_selector = 'a.BlogList-pagination-link'
    article_selector = 'article.BlogList-item'

    try:
        links = await page.query_selector_all(pagination_link_selector)

        if links:
            # Prefer a link with label indicating "Older" or "Next", otherwise fallback to first href-bearing link.
            next_link = None
            for l in links:
                try:
                    text = (await l.text_content() or '').strip().lower()
                except Exception:
                    text = ''
                href = await l.get_attribute('href')
                if href and ('older' in text or 'next' in text):
                    next_link = l
                    break
            if not next_link:
                # fallback: pick the first link that has an href
                for l in links:
                    href = await l.get_attribute('href')
                    if href:
                        next_link = l
                        break

            if next_link:
                # record previous state to detect content change
                try:
                    prev_count = await page.evaluate(f"() => document.querySelectorAll('{article_selector}').length")
                except Exception:
                    prev_count = None
                prev_url = await page.evaluate("() => window.location.href")

                href = await next_link.get_attribute('href')
                next_url = _absolute_url(href) if href else None

                # Ensure the link is visible
                try:
                    await next_link.scroll_into_view_if_needed()
                except Exception:
                    pass

                clicked = False
                # Try to click; it's possible click will load content via PJAX/XHR rather than full navigation.
                try:
                    await next_link.click()
                    clicked = True
                except Exception:
                    clicked = False

                # After click attempt, wait for either navigation (url change) or additional articles to appear
                try:
                    await page.wait_for_function(
                        """(state) => {
                            // state: {prevCount, prevUrl, selector}
                            const { prevCount, prevUrl, selector } = state;
                            if (prevUrl !== window.location.href) return true;
                            try {
                                const cur = document.querySelectorAll(selector).length;
                                if (typeof prevCount === 'number' && cur > prevCount) return true;
                            } catch(e) {}
                            return false;
                        }""",
                        arg={'prevCount': prev_count, 'prevUrl': prev_url, 'selector': article_selector},
                        timeout=8000
                    )
                    return
                except Exception:
                    # If waiting didn't observe changes, fallback to navigating via href if available
                    if next_url:
                        try:
                            await page.goto(next_url)
                            try:
                                await page.wait_for_load_state('networkidle', timeout=8000)
                            except Exception:
                                await page.wait_for_timeout(2000)
                            return
                        except Exception:
                            pass
                    # Give a small pause before fallback to scrolling
                    await page.wait_for_timeout(1500)
                    return

        # If no pagination link found, fallback to infinite scroll behavior.
        # Perform several scroll steps allowing JS to load more items.
        previous_height = await page.evaluate("() => document.body.scrollHeight")
        # Attempt a few incremental scrolls
        for _ in range(4):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            # Allow time for potential network requests and DOM updates
            await page.wait_for_timeout(2500)
            new_height = await page.evaluate("() => document.body.scrollHeight")
            if new_height == previous_height:
                # No more content appeared; break early
                break
            previous_height = new_height

    except Exception:
        # As a last resort, ensure we pause briefly so caller can detect no new items.
        await page.wait_for_timeout(1500)
    # If we reach here, either pagination was handled or infinite scroll attempt completed.
    return


async def get_first_page(base_url=base_url):
    """Fetch only the first page of articles."""
    async with PlaywrightContext() as context:
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)
        await page.goto(base_url)
        # Wait a short time to allow dynamic content to render
        try:
            await page.wait_for_load_state('networkidle', timeout=8000)
        except Exception:
            await page.wait_for_timeout(1000)
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
            await page.wait_for_load_state('networkidle', timeout=8000)
        except Exception:
            await page.wait_for_timeout(1000)

        page_count = 0
        item_count = 0  # previous count
        new_item_count = 0  # current count

        try:
            while page_count < max_pages:
                page_items = await scrape_page(page)
                for item in page_items:
                    # Use a stable dedupe key ignoring 'scraper' (though it is constant)
                    key = (item.get('url'), item.get('title'), item.get('date'))
                    if key not in seen:
                        seen.add(key)
                        items.append(item)
                new_item_count = len(items)

                # If no new items were added this iteration, stop
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
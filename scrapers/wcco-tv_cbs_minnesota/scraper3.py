"""
Articles Scraper for WCCO-TV  CBS Minnesota

Generated at: 2026-03-12 11:32:06
Target URL: https://www.cbsnews.com/minnesota/local-news/
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

base_url = 'https://www.cbsnews.com/minnesota/local-news/'

# Scraper module path for tracking the source of scraped data
SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

# Operator user-agent (set in operator.json)
USER_AGENT = ''

class PlaywrightContext:
    """Context manager for Playwright browser sessions."""

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        # launch headless by default; change to headful if debugging
        self.browser = await self.playwright.chromium.launch()
        context_kwargs = {'user_agent': USER_AGENT} if USER_AGENT else {}
        self.context = await self.browser.new_context(**context_kwargs)
        return self.context

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.browser.close()
        await self.playwright.stop()

async def _safe_text(element, selector):
    """Return stripped text_content() for selector within element or None."""
    try:
        el = await element.query_selector(selector)
        if not el:
            return None
        txt = await el.text_content()
        if txt is None:
            return None
        txt = txt.strip()
        return txt if txt else None
    except Exception:
        return None

async def _safe_attr(element, selector, attr):
    """Return attribute value for selector within element or None."""
    try:
        el = await element.query_selector(selector)
        if not el:
            return None
        val = await el.get_attribute(attr)
        return val
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
        - url: Link to the full article
        - scraper: module path for traceability
    """
    items = []

    # Use broad article container selector that matches examples
    article_selector = 'article.item'

    article_elements = await page.query_selector_all(article_selector)

    for article in article_elements:
        # Title: prefer .item__hed, fallback to .item__component-headline or first h4
        title = await _safe_text(article, '.item__hed')
        if not title:
            title = await _safe_text(article, '.item__component-headline')
        if not title:
            title = await _safe_text(article, 'h4')

        # URL: anchor with class item__anchor within the article
        href = await _safe_attr(article, 'a.item__anchor', 'href')
        url = None
        if href:
            url = urllib.parse.urljoin(base_url, href)

        # Date: li.item__date within metadata
        date_text = await _safe_text(article, 'li.item__date')
        date_value = None
        if date_text:
            # Try to parse absolute dates; if parsing fails (relative like "1H ago"), set None
            try:
                # Use dateutil.parser to parse common formats; fuzzy allows surrounding text
                dt = parse(date_text, fuzzy=True)
                # Format to YYYY-MM-DD
                date_value = dt.date().isoformat()
            except Exception:
                date_value = None

        # Only include items that have at least title and url (per requirements)
        if title and url:
            items.append({
                'title': title,
                'date': date_value,
                'url': url,
                'scraper': SCRAPER_MODULE_PATH,
            })
    return items

async def advance_page(page):
    """
    Finds the next page button or link to navigate to the next page of articles.
    Clicks button or navigates to next page URL if found. Scroll load more button into view if not visible.
    Defaults to infinite scroll if no pagination found.

    Parameters:
        page: Playwright page object
    """
    # Try to find explicit "view more" / pagination links first (prefer clicking)
    # Prioritize small view-more (examples), generic component__view-more, and large variant
    selectors = [
        'a.component__view-more--sm',
        'a.component__view-more',
        'a.component__view-more--lg',
        'a.component__view-more.component__view-more--sm'
    ]

    next_el = None
    next_href = None

    for sel in selectors:
        el = await page.query_selector(sel)
        if el:
            next_el = el
            href = await el.get_attribute('href')
            if href:
                next_href = urllib.parse.urljoin(base_url, href)
            break

    if next_el:
        try:
            # Ensure element is in view before clicking
            await next_el.scroll_into_view_if_needed()
            # Record current URL to detect navigation
            old_url = page.url
            # Attempt to click; some 'view more' links load inline content rather than full navigation
            await next_el.click()
            # Wait briefly for content to load
            await page.wait_for_timeout(2500)
            # If clicking did not change the page and we have an href, navigate directly
            if next_href and page.url == old_url:
                try:
                    await page.goto(next_href)
                    await page.wait_for_load_state('domcontentloaded')
                except Exception:
                    # If navigation fails, fallback to scroll behavior
                    await page.wait_for_timeout(1000)
            return
        except Exception:
            # If clicking fails but we have an href, navigate to href
            if next_href:
                try:
                    await page.goto(next_href)
                    await page.wait_for_load_state('domcontentloaded')
                    return
                except Exception:
                    pass
            # fall through to infinite scroll fallback

    # Fallback: infinite scroll / scroll-to-bottom to load more items
    # Perform a few progressive scrolls to ensure lazy-load triggers
    try:
        for _ in range(3):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
            await page.wait_for_timeout(2000)
    except Exception:
        # As last resort, simple wait
        await page.wait_for_timeout(3000)

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
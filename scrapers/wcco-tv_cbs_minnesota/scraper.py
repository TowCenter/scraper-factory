"""
Articles Scraper for WCCO-TV  CBS Minnesota

Generated at: 2026-03-10 14:02:25
Target URL: https://www.cbsnews.com/minnesota/local-news/
Generated using: gpt-5-mini-2025-08-07
Content type: articles
Fields: title, date, url

"""

import json
import os
import re
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
        - url: Link to the full article
        - scraper: module path for traceability
    """
    items = []

    # Find title elements directly (covers both selectors provided)
    title_nodes = await page.query_selector_all('h4.item__component-headline, h4.item__hed')
    for node in title_nodes:
        try:
            # Title: use text_content() to get text even if hidden in DOM
            title_text = await node.text_content()
            title = title_text.strip() if title_text else None

            # Find URL: look for an anchor within the closest logical container (article/div/li)
            href = await node.evaluate("""
                (el) => {
                    function tryQuery(root) {
                        if (!root) return null;
                        const a = root.querySelector('a.item__anchor, a');
                        if (a) return a.href || null;
                        return null;
                    }
                    // try a set of likely ancestor containers
                    const roots = [
                        el.closest('article.item'),
                        el.closest('article'),
                        el.closest('div.item'),
                        el.closest('li'),
                        el.parentElement
                    ];
                    for (const r of roots) {
                        const found = tryQuery(r);
                        if (found) return found;
                    }
                    // fallback: anchor directly on the title element or nearest anchor ancestor
                    const a1 = el.querySelector('a.item__anchor, a');
                    if (a1) return a1.href || null;
                    const a2 = el.closest('a.item__anchor') || el.closest('a');
                    if (a2) return a2.href || null;
                    return null;
                }
            """)

            url = None
            if href:
                # If href is an absolute URL returned by the browser, use it; otherwise join with base
                url = href if href.startswith('http') else urllib.parse.urljoin(base_url, href)

            # Date: search for nearest li.item__date inside metadata or ancestor container
            date_text = await node.evaluate("""
                (el) => {
                    const root = el.closest('article.item') || el.closest('article') || el.closest('div.item') || el.closest('li') || el.parentElement;
                    if (root) {
                        const dateEl = root.querySelector('ul.item__metadata li.item__date, li.item__date');
                        if (dateEl && dateEl.textContent) return dateEl.textContent.trim();
                    }
                    // fallback: look for a nearby metadata container
                    const meta = el.closest('.item__metadata') || document.querySelector('.item__metadata');
                    if (meta) {
                        const d = meta.querySelector('li.item__date');
                        if (d && d.textContent) return d.textContent.trim();
                    }
                    return null;
                }
            """)

            date = None
            if date_text:
                if 'ago' not in date_text.lower():
                    try:
                        parsed = parse(date_text, fuzzy=True)
                        date = parsed.strftime('%Y-%m-%d')
                    except Exception:
                        date = None

            items.append({
                'title': title if title is not None else None,
                'date': date,
                'url': url if url is not None else None,
                'scraper': SCRAPER_MODULE_PATH,
            })

        except Exception:
            # On any item-level error, continue to next title node
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

    # Try to find "view more" / pagination link first
    try:
        view_more = await page.query_selector('a.component__view-more, a.component__view-more.component__view-more--sm')
        if view_more:
            # Try to get href and navigate to that URL
            href = await view_more.get_attribute('href')
            if href:
                next_url = urllib.parse.urljoin(base_url, href.strip())
                current = page.url
                # Avoid navigating to same URL repeatedly
                if next_url and next_url != current:
                    # Navigate to next page and wait until network is idle
                    await page.goto(next_url)
                    await page.wait_for_load_state('networkidle')
                    return
                else:
                    # If href equals current page or missing, attempt click as fallback
                    try:
                        await view_more.scroll_into_view_if_needed()
                        await view_more.click()
                        await page.wait_for_load_state('networkidle')
                        return
                    except Exception:
                        # fall back to infinite scroll behavior below
                        pass

    except Exception:
        # If anything goes wrong finding/clicking view more, fall back to infinite scroll
        pass

    # Fallback: infinite scroll behavior
    # Scroll in increments to allow lazy content to load
    try:
        previous_height = await page.evaluate("() => document.body.scrollHeight")
        # Perform a few incremental scrolls
        for _ in range(3):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)  # wait for lazy-loaded content
            new_height = await page.evaluate("() => document.body.scrollHeight")
            if new_height == previous_height:
                # No new content loaded; break early
                break
            previous_height = new_height
        # give a short pause for any final loads
        await page.wait_for_timeout(1000)
    except Exception:
        # If scroll fails, do a basic wait
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
                    # Create a stable key excluding None values ordering-insensitive
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
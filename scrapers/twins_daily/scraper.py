"""
Articles Scraper for Twins Daily

Generated at: 2026-03-10 14:48:06
Target URL: https://twinsdaily.com/
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

base_url = 'https://twinsdaily.com/'

# Scraper module path for tracking the source of scraped data
SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

# Operator user-agent (set in operator.json)
USER_AGENT = ''

class PlaywrightContext:
    """Context manager for Playwright browser sessions."""

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        # Launch in headless mode by default (can be changed if needed)
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

    # Choose item containers that reliably represent an article on this site.
    # article.news-entry-list is the main article container; front-page hero uses div.front-page-article-title.
    item_selectors = 'article.news-entry-list, div.front-page-article-title'

    nodes = await page.query_selector_all(item_selectors)

    for node in nodes:
        try:
            # Find the primary anchor for the title / url.
            # Try multiple common patterns: h2.ipsType_pageTitle a, front-page anchors, or the first anchor in the container.
            anchor = await node.query_selector(
                'h2.ipsType_pageTitle a, div.front-page-article-title a, h2 a, a.ipsType_pageTitle, a[href]'
            )

            title = None
            url = None
            date_val = None

            if anchor:
                # Use text_content() per instructions (works even if hidden)
                title_text = await anchor.text_content()
                if title_text:
                    title = title_text.strip()

                href = await anchor.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(page.url, href.strip())

            # Attempt to get the date from a <time> element inside the node
            time_el = await node.query_selector('time')
            if time_el:
                # Prefer the datetime attribute when available
                datetime_attr = await time_el.get_attribute('datetime')
                time_text = None
                if datetime_attr:
                    try:
                        dt = parse(datetime_attr)
                        date_val = dt.date().isoformat()
                    except Exception:
                        # fallback to parsing visible text if datetime parsing fails
                        try:
                            time_text = await time_el.text_content()
                            if time_text:
                                dt = parse(time_text.strip(), fuzzy=True)
                                date_val = dt.date().isoformat()
                        except Exception:
                            date_val = None
                else:
                    # No datetime attribute, try to parse the visible time text
                    try:
                        time_text = await time_el.text_content()
                        if time_text:
                            dt = parse(time_text.strip(), fuzzy=True)
                            date_val = dt.date().isoformat()
                    except Exception:
                        date_val = None

            # If title or url not found, attempt other fallback strategies
            if not title:
                # Try to grab any heading inside the node
                heading = await node.query_selector('h1, h2, h3, .ipsType_pageTitle')
                if heading:
                    txt = await heading.text_content()
                    if txt:
                        title = txt.strip()

            if not url:
                # Try to find an image link or other link inside the node
                other_anchor = await node.query_selector('a[href]')
                if other_anchor:
                    href = await other_anchor.get_attribute('href')
                    if href:
                        url = urllib.parse.urljoin(page.url, href.strip())

            # Ensure required fields: title and url. If missing, skip the item.
            if not title or not url:
                continue

            item = {
                'title': title,
                'date': date_val if date_val else None,
                'url': url,
                'scraper': SCRAPER_MODULE_PATH,
            }
            items.append(item)

        except Exception:
            # If any item's extraction fails, skip that item but continue processing others.
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

    # 1) Prefer a clear "next" pagination link
    next_selectors = [
        'ul.ipsPagination li.ipsPagination_next a',  # explicit next button
        'a[rel="next"]',                             # rel=next links
        'ul.ipsPagination a[href*="/page/"]'         # numeric page links
    ]

    next_el = None
    for sel in next_selectors:
        next_el = await page.query_selector(sel)
        if next_el:
            break

    if next_el:
        try:
            href = await next_el.get_attribute('href')
            if href and href.strip() and not href.strip().startswith('#'):
                next_url = urllib.parse.urljoin(page.url, href.strip())
                # Navigate to the next page URL
                await page.goto(next_url)
                # wait briefly for content to load
                await page.wait_for_load_state('networkidle')
                await page.wait_for_timeout(1000)
                return
            else:
                # If href is absent or a JS anchor, try clicking
                try:
                    await next_el.scroll_into_view_if_needed()
                    await next_el.click()
                    await page.wait_for_load_state('networkidle')
                    await page.wait_for_timeout(1000)
                    return
                except Exception:
                    # Fall through to infinite scroll fallback below
                    pass
        except Exception:
            # If anything goes wrong with navigation/clicking, fall back to infinite scroll
            pass

    # 2) Look for a "Load more" interactive element (button or link)
    load_more_selectors = [
        'button:has-text("Load more")',
        'a:has-text("Load more")',
        'button:has-text("More")',
        'a:has-text("More")',
        'button:has-text("Load More")',
        'a:has-text("Load More")',
    ]
    for sel in load_more_selectors:
        try:
            el = await page.query_selector(sel)
            if el:
                try:
                    await el.scroll_into_view_if_needed()
                    await el.click()
                    await page.wait_for_timeout(1500)
                    return
                except Exception:
                    # If click fails, try clicking via evaluate
                    try:
                        await page.evaluate('(el) => el.click()', el)
                        await page.wait_for_timeout(1500)
                        return
                    except Exception:
                        pass
        except Exception:
            continue

    # 3) Fallback: infinite scroll (scroll to bottom a few times, waiting for new content)
    previous_height = await page.evaluate("() => document.body.scrollHeight")
    for _ in range(3):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(3000)
        new_height = await page.evaluate("() => document.body.scrollHeight")
        if new_height == previous_height:
            # no change, small wait and try again
            await page.wait_for_timeout(1000)
        else:
            previous_height = new_height

    # leave the page at the same URL — get_all_articles will call scrape_page again to pick up newly loaded items
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
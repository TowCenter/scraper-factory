"""
Articles Scraper for MinnPost

Generated at: 2026-03-10 14:27:32
Target URL: https://www.minnpost.com/tag/news/
Generated using: gpt-5-mini-2025-08-07
Content type: articles
Fields: title, date, url

"""

import json
import os
import asyncio
import urllib.parse
from dateutil.parser import parse
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API

base_url = 'https://www.minnpost.com/tag/news/'

# Scraper module path for tracking the source of scraped data
SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

# Operator user-agent (set in operator.json)
USER_AGENT = ''

class PlaywrightContext:
    """Context manager for Playwright browser sessions."""

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        # Headed/headless controlled by environment/browser defaults; using chromium
        self.browser = await self.playwright.chromium.launch()
        context_kwargs = {'user_agent': USER_AGENT} if USER_AGENT else {}
        self.context = await self.browser.new_context(**context_kwargs)
        return self.context

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.browser.close()
        await self.playwright.stop()

async def _parse_date_from_element(elem):
    """
    Given an ElementHandle that may contain time elements, attempt to extract a
    datetime string and normalize it to YYYY-MM-DD. Return None if cannot parse.
    """
    try:
        # Prefer published time element
        time_handle = await elem.query_selector('time.entry-date.published')
        if not time_handle:
            # fallback to updated time or any time element inside
            time_handle = await elem.query_selector('time.updated') or await elem.query_selector('time')
        if time_handle:
            dt_attr = await time_handle.get_attribute('datetime')
            text = await time_handle.text_content()
            if dt_attr:
                # dt_attr typically ISO 8601, parse directly
                dt = parse(dt_attr)
                return dt.date().isoformat()
            elif text:
                # text might be like "03/10/2026" or "58 minutes ago" — try to parse
                try:
                    dt = parse(text, fuzzy=True)
                    return dt.date().isoformat()
                except Exception:
                    return None
    except Exception:
        return None
    return None

async def scrape_page(page):
    """
    Extract article data from the current page.

    Parameters:
        page: Playwright page object

    Returns:
        List of dictionaries containing article data with keys:
        - title: Headline or title of the article
        - date: Publication date in YYYY-MM-DD format or None
        - url: Absolute link to the full article
        - scraper: module path for traceability
    """
    items = []

    # Use a set of robust article container selectors observed on the site.
    # Prefer classed article containers, but fall back to generic article tags.
    try:
        handles = await page.query_selector_all('article.post, article.tag-news, article')
    except Exception:
        handles = []

    for handle in handles:
        try:
            # Title: try multiple common title selectors inside the article container.
            title_handle = (
                await handle.query_selector('h2.entry-title a')
                or await handle.query_selector('h3.entry-title a')
                or await handle.query_selector('.entry-title a')
                or await handle.query_selector('.entry-header a')
                or await handle.query_selector('a.entry-title')
            )

            title = None
            url = None
            if title_handle:
                raw_title = await title_handle.text_content()
                if raw_title:
                    title = raw_title.strip()
                href = await title_handle.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(page.url, href)

            # If title or url missing, attempt to get url from thumbnail link
            if not url:
                thumb = await handle.query_selector('a.post-thumbnail-inner')
                if thumb:
                    href = await thumb.get_attribute('href')
                    if href:
                        url = urllib.parse.urljoin(page.url, href)

            # Date: attempt to parse using time elements
            date = await _parse_date_from_element(handle)

            # Only include items that have at least a title and url
            if title and url:
                items.append({
                    'title': title,
                    'date': date,
                    'url': url,
                    'scraper': SCRAPER_MODULE_PATH,
                })

        except Exception:
            # Skip malformed article container but continue with others
            continue

    return items

async def advance_page(page):
    """
    Finds the next page button or link to navigate to the next page of articles.
    Prioritizes 'next' links/buttons; falls back to pagination anchors; finally uses
    infinite scroll if no pagination controls are available.

    Parameters:
        page: Playwright page object
    """
    # 1) Prefer an explicit "next" link/button
    try:
        next_locator = page.locator('a.next.page-numbers, .nav-links a.next.page-numbers')
        if await next_locator.count() > 0:
            # Use the first matching "next" link
            nxt = next_locator.first
            href = await nxt.get_attribute('href')
            try:
                # Try to click and wait for navigation
                await asyncio.gather(page.wait_for_navigation(), nxt.click())
            except Exception:
                # If click/navigation fails, navigate directly if href is available
                if href:
                    await page.goto(urllib.parse.urljoin(page.url, href))
            return
    except Exception:
        # ignore and continue to other strategies
        pass

    # 2) Look for numbered pagination and try to find the link after the current page
    try:
        handles = await page.query_selector_all('nav.navigation.pagination a.page-numbers')
        if handles:
            current_index = None
            for i, h in enumerate(handles):
                cls = (await h.get_attribute('class')) or ''
                aria = (await h.get_attribute('aria-current')) or ''
                if 'current' in cls or aria:
                    current_index = i
                    break
            next_handle = None
            if current_index is not None:
                if current_index + 1 < len(handles):
                    next_handle = handles[current_index + 1]
            # If no explicit current found, try to find a link whose text suggests older/next
            if not next_handle:
                for h in handles:
                    txt = (await h.text_content() or '').strip().lower()
                    if 'older' in txt or 'next' in txt:
                        next_handle = h
                        break
            # Fallback: if handles are numeric and not current found, pick the last one (likely next pages)
            if not next_handle and len(handles) > 1:
                # choose the handle with the highest numeric text if available
                numeric_index = None
                max_num = -1
                for h in handles:
                    txt = (await h.text_content() or '').strip()
                    try:
                        n = int(txt)
                        if n > max_num:
                            max_num = n
                            numeric_index = h
                    except Exception:
                        continue
                if numeric_index:
                    # Try to click the numeric link that is greater than current page if possible.
                    next_handle = numeric_index

            if next_handle:
                href = await next_handle.get_attribute('href')
                try:
                    await asyncio.gather(page.wait_for_navigation(), next_handle.click())
                except Exception:
                    if href:
                        await page.goto(urllib.parse.urljoin(page.url, href))
                return
    except Exception:
        pass

    # 3) Fallback to infinite scroll: attempt to scroll to bottom and wait for more content.
    try:
        prev_height = await page.evaluate('() => document.body.scrollHeight')
        await page.evaluate('() => window.scrollTo(0, document.body.scrollHeight)')
        # Allow time for lazy loading / JS to fetch more articles
        await page.wait_for_timeout(3000)
        new_height = await page.evaluate('() => document.body.scrollHeight')
        # If page grew, assume more content loaded; otherwise, no more pages.
        if new_height > prev_height:
            return
        # Try one more time with a longer wait in case of slow loads
        await page.evaluate('() => window.scrollTo(0, document.body.scrollHeight)')
        await page.wait_for_timeout(4000)
    except Exception:
        # If anything fails here, silently return so caller can break out if nothing new is added.
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
                    # Use a stable dedupe key based on available values
                    key = tuple(sorted((k, v) for k, v in item.items() if v is not None))
                    if key not in seen:
                        seen.add(key)
                        items.append(item)
                new_item_count = len(items)

                # If no new items were added, stop paging
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
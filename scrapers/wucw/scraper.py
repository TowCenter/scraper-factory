"""
Articles Scraper for WUCW

Generated at: 2026-03-10 15:01:42
Target URL: https://thecwtc.com/news/local
Generated using: gpt-5-mini-2025-08-07
Content type: articles
Fields: title, date, url

"""

import json
import os
import re
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse as dateutil_parse
import urllib.parse
import asyncio

base_url = 'https://thecwtc.com/news/local'

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


def _parse_relative_time(text: str):
    """
    Attempt to parse relative time strings like '3 months ago', '2 years ago',
    'yesterday', 'today' into a YYYY-MM-DD string. Returns None if not recognized.
    """
    if not text:
        return None
    t = text.strip().lower()

    if t in ('today',):
        return datetime.utcnow().date().isoformat()
    if t in ('yesterday',):
        return (datetime.utcnow().date() - timedelta(days=1)).isoformat()

    m = re.match(r'(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago', t)
    if m:
        qty = int(m.group(1))
        unit = m.group(2)
        if unit == 'second':
            delta = timedelta(seconds=qty)
        elif unit == 'minute':
            delta = timedelta(minutes=qty)
        elif unit == 'hour':
            delta = timedelta(hours=qty)
        elif unit == 'day':
            delta = timedelta(days=qty)
        elif unit == 'week':
            delta = timedelta(weeks=qty)
        elif unit == 'month':
            # approximate a month as 30 days
            delta = timedelta(days=30 * qty)
        elif unit == 'year':
            # approximate a year as 365 days
            delta = timedelta(days=365 * qty)
        else:
            return None
        return (datetime.utcnow() - delta).date().isoformat()
    return None


def _parse_date_text(text: str):
    """
    Try multiple strategies to convert a date-like text into YYYY-MM-DD.
    Returns None if parsing fails.
    """
    if not text:
        return None
    text = text.strip()
    # Try ISO/datetext parsing first
    try:
        dt = dateutil_parse(text, fuzzy=True)
        return dt.date().isoformat()
    except Exception:
        pass
    # Try relative parsing (e.g., '3 months ago')
    rel = _parse_relative_time(text)
    if rel:
        return rel
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

    # Use a broad but reliable item selector for teaser items
    item_elements = await page.query_selector_all('li.teaserItem')

    for item in item_elements:
        try:
            # 1) Title extraction: prefer the headline anchor inside headline containers
            title = None
            title_el = await item.query_selector('.index-module_headlineLarge__yjN9 a, .index-module_headline__h51M a')
            if title_el:
                title = (await title_el.text_content()) or None
                title = title.strip() if title else None

            # Fallback: find an anchor that appears to link to a story (href containing '/news/')
            if not title:
                fallback_anchor = await item.query_selector('a[href*="/news/"]')
                if fallback_anchor:
                    # The image anchor sometimes contains a descriptive aria-label/title we can use
                    title = (await fallback_anchor.get_attribute('aria-label')) or (await fallback_anchor.get_attribute('title')) or (await fallback_anchor.text_content())
                    title = title.strip() if title else None

            # Ensure title is present (required). If still missing, skip this item.
            if not title:
                continue

            # 2) URL extraction: prefer headline anchor href, else first story-like anchor
            url = None
            url_el = await item.query_selector('.index-module_headlineLarge__yjN9 a, .index-module_headline__h51M a')
            if url_el:
                href = await url_el.get_attribute('href')
                if href:
                    url = urllib.parse.urljoin(base_url, href)
            if not url:
                anchor = await item.query_selector('a[href*="/news/"]')
                if anchor:
                    href = await anchor.get_attribute('href')
                    if href:
                        url = urllib.parse.urljoin(base_url, href)

            # If still no URL, attempt any href on the item
            if not url:
                any_anchor = await item.query_selector('a[href]')
                if any_anchor:
                    href = await any_anchor.get_attribute('href')
                    if href:
                        url = urllib.parse.urljoin(base_url, href)

            # 3) Date extraction: look for time[datetime], then common publishedSince span, then parse if possible
            date_value = None
            time_el = await item.query_selector('time[datetime]')
            if time_el:
                dt_attr = await time_el.get_attribute('datetime')
                date_value = _parse_date_text(dt_attr)

            if not date_value:
                # publishedSince often contains relative times like '3 months ago'
                pub_el = await item.query_selector('.index-module_publishedSinceWithTeaserStatus__tkAa .publishedSince, .publishedSince')
                if pub_el:
                    txt = await pub_el.text_content()
                    date_value = _parse_date_text(txt)

            # If no date found, set None (optional field)
            if not date_value:
                date_value = None

            items.append({
                'title': title,
                'date': date_value,
                'url': url,
                'scraper': SCRAPER_MODULE_PATH,
            })
        except Exception:
            # Skip problematic item but continue processing others
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
    # Attempt to find conventional pagination controls first (none were detected in analysis),
    # but include some common patterns in case they exist.
    next_selectors = [
        'a[rel="next"]',
        'a.pagination__next, a.pager-next, .pagination-next a',
        'button.load-more, .loadMore, button#load-more, button[data-action="load-more"]',
        'a.load-more, a.btn-load-more',
    ]

    for sel in next_selectors:
        try:
            next_el = await page.query_selector(sel)
            if next_el:
                # If it's an anchor with href, navigate to it
                href = await next_el.get_attribute('href')
                if href:
                    next_url = urllib.parse.urljoin(page.url, href)
                    await next_el.scroll_into_view_if_needed()
                    # Prefer direct navigation to avoid potential JS click handlers failing in headless
                    await page.goto(next_url)
                    return
                # Otherwise try clicking (for buttons or anchors with JS handlers)
                try:
                    await next_el.scroll_into_view_if_needed()
                    await next_el.click()
                    # Allow content to load after click
                    await page.wait_for_timeout(2000)
                    return
                except Exception:
                    # If click fails, continue to fallback
                    pass
        except Exception:
            continue

    # Fallback: infinite scroll strategy
    # Scroll repeatedly until no new content loads or until attempts exhausted
    max_attempts = 6
    attempt = 0
    last_height = await page.evaluate("() => document.body.scrollHeight")
    while attempt < max_attempts:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        # Allow time for lazy-load or XHR to fetch more items
        await page.wait_for_timeout(2000 + attempt * 500)
        new_height = await page.evaluate("() => document.body.scrollHeight")
        if new_height == last_height:
            # No change in height -> likely no more content
            break
        last_height = new_height
        attempt += 1

    # Give a brief pause after scrolling attempts
    await page.wait_for_timeout(1000)
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
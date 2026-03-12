"""
Articles Scraper for Twin Cities Business

Generated at: 2026-03-10 15:21:15
Target URL: https://tcbmag.com/news/
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

base_url = 'https://tcbmag.com/news/'

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

async def _safe_text(el):
    """Return text_content() stripped or None if element missing."""
    if not el:
        return None
    txt = await el.text_content()
    if txt is None:
        return None
    return txt.strip()

async def _safe_attr(el, name):
    """Return attribute value or None if element missing."""
    if not el:
        return None
    return await el.get_attribute(name)

async def _parse_date(text):
    """Parse a date string into YYYY-MM-DD or return None."""
    if not text:
        return None
    try:
        dt = parse(text, fuzzy=True)
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
        - date: Publication date in YYYY-MM-DD format (optional)
        - url: Link to the full article
        - scraper: module path for traceability
    """
    items = []

    # Use robust container selector for articles
    article_elements = await page.query_selector_all('article.post')
    if not article_elements:
        # fallback to generic article tags
        article_elements = await page.query_selector_all('div.list-grid article, article')

    for art in article_elements:
        try:
            # Prefer the headline link inside h2.entry-title
            title_el = await art.query_selector('h2.entry-title a')
            # Fallback: any linked title inside article
            if not title_el:
                title_el = await art.query_selector('a.thumb-link')
            title = await _safe_text(title_el)

            # URL: prefer the href from the title link; fallback to thumb-link
            url = None
            if title_el:
                href = await _safe_attr(title_el, 'href')
                if href:
                    url = urllib.parse.urljoin(base_url, href)
            if not url:
                thumb = await art.query_selector('a.thumb-link')
                href = await _safe_attr(thumb, 'href') if thumb else None
                if href:
                    url = urllib.parse.urljoin(base_url, href)

            # Date extraction: try common patterns
            date = None
            # 1) look for <time datetime="...">
            time_el = await art.query_selector('time')
            if time_el:
                datetime_attr = await _safe_attr(time_el, 'datetime')
                if datetime_attr:
                    date = await _parse_date(datetime_attr)
                if not date:
                    # try text inside time
                    time_text = await _safe_text(time_el)
                    date = await _parse_date(time_text)
            if not date:
                # 2) look for common meta classes
                possible_selectors = [
                    '.entry-meta time',
                    '.post-meta time',
                    '.posted-on time',
                    '.post-date time',
                    '.entry-header .byline time',
                    '.posted-on',
                    '.post-date',
                ]
                for sel in possible_selectors:
                    if date:
                        break
                    el = await art.query_selector(sel)
                    if el:
                        # try datetime attribute first
                        datetime_attr = await _safe_attr(el, 'datetime')
                        if datetime_attr:
                            date = await _parse_date(datetime_attr)
                            break
                        text = await _safe_text(el)
                        date = await _parse_date(text)
            # If still not found, set None
            if date is None:
                date = None

            # Ensure required fields exist; skip articles missing title or url
            if not title and not url:
                continue

            item = {
                'title': title if title else None,
                'date': date,
                'url': url if url else None,
                'scraper': SCRAPER_MODULE_PATH,
            }
            items.append(item)
        except Exception:
            # Don't let one malformed article break the whole page scrape
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
    # Priority list of selectors for "next page" behavior
    next_selectors = [
        'nav[aria-label="Posts navigation"] a.next.page-link',
        'ul.pagination a.next.page-link',
        'a.next.page-link',
        'a.page-numbers.next',
        'a[rel="next"]',
        'a.load-more',
        'button.load-more',
        'button#load-more',
    ]

    for sel in next_selectors:
        try:
            el = await page.query_selector(sel)
            if not el:
                continue

            # If link has href, navigate directly to its href to avoid JS-click issues
            href = await _safe_attr(el, 'href')
            if href:
                next_url = urllib.parse.urljoin(base_url, href)
                # If the next url is same as current, attempt click fallback
                current_url = page.url
                if next_url == current_url:
                    try:
                        await el.scroll_into_view_if_needed()
                        await el.click()
                        await page.wait_for_load_state('networkidle', timeout=8000)
                        return
                    except Exception:
                        # fallback to scrolling
                        break
                else:
                    # Navigate to the next page url
                    try:
                        await page.goto(next_url)
                        await page.wait_for_load_state('networkidle', timeout=8000)
                        return
                    except Exception:
                        # If direct navigation fails, try clicking
                        try:
                            await el.scroll_into_view_if_needed()
                            await el.click()
                            await page.wait_for_load_state('networkidle', timeout=8000)
                            return
                        except Exception:
                            break
            else:
                # No href, attempt click
                try:
                    await el.scroll_into_view_if_needed()
                    await el.click()
                    await page.wait_for_load_state('networkidle', timeout=8000)
                    return
                except Exception:
                    # If click failed, continue to next selector
                    continue
        except Exception:
            continue

    # If no pagination link/button found or all attempts failed -> infinite scroll fallback
    # Perform several incremental scrolls to try to load new items
    previous_height = await page.evaluate('document.body.scrollHeight')
    for _ in range(6):
        await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
        # small delay to allow lazy-loading
        await page.wait_for_timeout(1500)
        new_height = await page.evaluate('document.body.scrollHeight')
        if new_height == previous_height:
            # try a short page up/down to trigger lazy loaders
            await page.evaluate('window.scrollTo(0, 0)')
            await page.wait_for_timeout(500)
            await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            await page.wait_for_timeout(1500)
            new_height = await page.evaluate('document.body.scrollHeight')
            if new_height == previous_height:
                # nothing more loaded; break out
                break
        previous_height = new_height

    # leave the page as-is (same URL) after attempting infinite scroll
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
                    # Normalize key for deduplication (title + url + date)
                    dedup_key = (item.get('title'), item.get('url'), item.get('date'))
                    if dedup_key not in seen:
                        seen.add(dedup_key)
                        items.append(item)
                new_item_count = len(items)

                if new_item_count <= item_count:
                    # no new items loaded, stop
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
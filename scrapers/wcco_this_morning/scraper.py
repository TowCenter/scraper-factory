import json
import os
import re
import urllib.parse
from dateutil.parser import parse
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
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
        - date: Publication date in YYYY-MM-DD format or None
        - url: Link to the full article
        - scraper: module path for traceability
    """
    items = []

    # Use a broad, stable container selector for article items
    article_selectors = 'article.item'
    article_elements = await page.query_selector_all(article_selectors)

    for art in article_elements:
        try:
            # Find the anchor that wraps the item (source of URL)
            anchor = await art.query_selector('a.item__anchor')
            href = None
            if anchor:
                href_val = await anchor.get_attribute('href')
                if href_val:
                    href = urllib.parse.urljoin(base_url, href_val.strip())

            # Title: prefer the main headline (.item__hed), fallback to component headline
            title_el = await art.query_selector('h4.item__hed')
            if not title_el:
                title_el = await art.query_selector('h4.item__component-headline')
            title = None
            if title_el:
                title_text = await title_el.text_content()
                if title_text:
                    title = title_text.strip()

            # Date: look for list item with class item__date
            date_el = await art.query_selector('li.item__date')
            date_value = None
            if date_el:
                date_text = (await date_el.text_content() or '').strip()
                # If the date text looks relative (e.g., "50M ago", "3H ago", "27M ago"), treat as None
                lower = date_text.lower()
                if date_text:
                    # Patterns indicating relative times
                    if ('ago' in lower) or re.search(r'^\d+\s*[mh]\b', lower) or re.search(r'\b(min|minute|hour|hr|h|m)\b', lower):
                        date_value = None
                    else:
                        try:
                            dt = parse(date_text, fuzzy=True)
                            date_value = dt.date().isoformat()
                        except Exception:
                            date_value = None

            # Only include items that have at least title and url
            if title and href:
                items.append({
                    'title': title,
                    'date': date_value,
                    'url': href,
                    'scraper': SCRAPER_MODULE_PATH,
                })

        except Exception:
            # Skip malformed article entries but continue processing others
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
    # Try to find "view more" / pagination link(s). Prefer anchors with class component__view-more.
    try:
        view_more_selectors = 'a.component__view-more'
        view_more_links = await page.query_selector_all(view_more_selectors)
        chosen = None
        chosen_href = None

        for link in view_more_links:
            href = await link.get_attribute('href')
            # Skip empty hrefs
            if not href:
                continue
            full = urllib.parse.urljoin(base_url, href.strip())
            # Prefer links that look like the next page (contain '/minnesota/local-news/')
            if '/minnesota/local-news/' in full:
                chosen = link
                chosen_href = full
                break
            # otherwise accept the first available link
            if not chosen:
                chosen = link
                chosen_href = full

        if chosen and chosen_href:
            try:
                # Scroll into view then click; some "view more" buttons load content via JS
                await chosen.scroll_into_view_if_needed()
                # If the element is a real link that navigates, clicking may navigate.
                await chosen.click()
                # Wait a bit for new content to load
                await page.wait_for_load_state('networkidle', timeout=5000)
                # Also give the page a moment to render loaded items
                await page.wait_for_timeout(1500)
                return
            except Exception:
                # If click fails (e.g., intercepted), fallback to navigating directly
                try:
                    await page.goto(chosen_href)
                    await page.wait_for_load_state('networkidle', timeout=5000)
                    await page.wait_for_timeout(1000)
                    return
                except Exception:
                    # If navigation fails, fall through to infinite scroll fallback
                    pass

    except Exception:
        # Any errors finding/clicking view more will fall back to infinite scroll below
        pass

    # Fallback: infinite scroll (scroll to bottom and wait for more content to load)
    try:
        # Perform a few incremental scrolls to the bottom to trigger lazy loading
        for _ in range(3):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)
        # Final wait to let content settle
        await page.wait_for_timeout(1500)
    except Exception:
        # If scroll fails, do nothing
        await page.wait_for_timeout(1000)

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
                    # Use a stable key for deduplication (exclude None values)
                    key = tuple(sorted((k, v) for k, v in item.items() if v is not None))
                    if key not in seen:
                        seen.add(key)
                        items.append(item)
                new_item_count = len(items)

                # If no new items were added after scraping, stop
                if new_item_count <= item_count:
                    break

                page_count += 1
                item_count = new_item_count

                # Try to advance to the next page / load more content
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
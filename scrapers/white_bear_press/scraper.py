import json
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio

base_url = 'https://www.presspubs.com/white_bear/'

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
        - url: Absolute link to the full article
        - scraper: module path for traceability
    """
    items = []

    # Primary container selector: keep it broad to match multiple card variants
    container_selector = "article.card, article.tnt-asset-type-article, article.tnt-asset-type-article.card, article.card.summary, article.card.showcase"

    # Query all article containers
    article_elements = await page.query_selector_all(container_selector)

    for article in article_elements:
        try:
            # Try to find headline link inside h2/h3 headline first, fallback to any asset link
            title_anchor = await article.query_selector(
                "h2.tnt-headline a.tnt-asset-link, h3.tnt-headline a.tnt-asset-link, a.tnt-asset-link"
            )

            title = None
            url = None

            if title_anchor:
                # Prefer visible/text content for title. Use text_content() per instructions.
                raw_title = await title_anchor.text_content()
                raw_title = raw_title.strip() if raw_title else ""

                # Some image anchors may not contain inner text; fallback to aria-label attribute
                if raw_title:
                    title = raw_title
                else:
                    aria = await title_anchor.get_attribute("aria-label")
                    title = aria.strip() if aria else None

                href = await title_anchor.get_attribute("href") if title_anchor else None
                if href:
                    url = urllib.parse.urljoin(base_url, href)

            # Date extraction: look for time element with class tnt-date
            date_value = None
            time_el = await article.query_selector("time.tnt-date")
            if time_el:
                datetime_attr = await time_el.get_attribute("datetime")
                text_val = None
                if datetime_attr:
                    # Parse datetime attribute if available
                    try:
                        dt = parse(datetime_attr)
                        date_value = dt.date().isoformat()
                    except Exception:
                        date_value = None
                if date_value is None:
                    # Fallback to parsing the visible text content
                    text_val = await time_el.text_content()
                    if text_val:
                        try:
                            dt = parse(text_val.strip())
                            date_value = dt.date().isoformat()
                        except Exception:
                            date_value = None

            # If title or url are missing, skip this item (required fields)
            if not title or not url:
                # Skip malformed entries gracefully
                continue

            items.append({
                "title": title,
                "date": date_value,
                "url": url,
                "scraper": SCRAPER_MODULE_PATH,
            })

        except Exception:
            # Don't let one bad article break the whole page scrape
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
    # Try common next-page or load-more selectors first
    next_selectors = [
        "a[rel='next']",
        "a:has-text('Next')",
        "a:has-text('next')",
        "a:has-text('More')",
        "a:has-text('more')",
        "a:has-text('Load More')",
        "a:has-text('Load more')",
        "button:has-text('Load More')",
        "button:has-text('Load more')",
        "button:has-text('More')",
    ]

    for sel in next_selectors:
        try:
            el = await page.query_selector(sel)
            if el:
                # If it's a link with href, navigate to it; otherwise try clicking
                href = await el.get_attribute("href")
                if href:
                    next_url = urllib.parse.urljoin(base_url, href)
                    try:
                        await page.goto(next_url)
                        return
                    except Exception:
                        # fallback to click if navigation failed
                        pass
                try:
                    await el.scroll_into_view_if_needed()
                    await el.click()
                    # wait a little for new content
                    await page.wait_for_timeout(2000)
                    return
                except Exception:
                    # continue to next selector if click fails
                    continue
        except Exception:
            continue

    # No explicit pagination found: perform infinite scroll fallback.
    # Attempt a few scrolls until no new articles appear.
    container_selector = "article.card, article.tnt-asset-type-article, article.card.summary, article.card.showcase"
    max_iterations = 6
    stable_iterations = 0
    previous_count = await page.locator(container_selector).count()

    for _ in range(max_iterations):
        # Scroll to bottom
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            # As a fallback, attempt smaller scroll
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
        # Wait for potential lazy load/network activity
        await page.wait_for_timeout(2000)

        current_count = await page.locator(container_selector).count()
        if current_count > previous_count:
            # New items loaded; reset stable counter and continue to try loading more
            previous_count = current_count
            stable_iterations = 0
            # small delay before next scroll
            await page.wait_for_timeout(800)
            continue
        else:
            stable_iterations += 1
            if stable_iterations >= 2:
                # No more new items after a couple of tries
                break
            # slight pause and try again
            await page.wait_for_timeout(1000)

    # As a last resort, ensure we slightly pause to allow any JS-driven loading to complete
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
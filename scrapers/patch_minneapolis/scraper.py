import json
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio

base_url = 'https://patch.com/minnesota/minneapolis'

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

    # Use relatively robust selectors that match CSS-module class patterns
    # Article container: match elements whose class contains 'ArticleCard' or 'Card__'
    article_selector = "article[class*='ArticleCard'], article[class*='Card__']"
    # Title link inside article: anchor whose class contains 'TitleLink'
    title_link_selector = "a[class*='TitleLink']"
    # Date: time element inside article
    time_selector = "time"
    try:
        article_elements = await page.query_selector_all(article_selector)
    except Exception:
        article_elements = []

    for article in article_elements:
        # Title extraction (required)
        title = None
        url = None
        date_val = None

        try:
            title_el = await article.query_selector(title_link_selector)
            if title_el:
                title_text = await title_el.text_content()
                if title_text:
                    title = title_text.strip()
                href = await title_el.get_attribute('href')
                if href:
                    # Build absolute URL relative to current page
                    url = urllib.parse.urljoin(page.url, href.strip())
        except Exception:
            title = title or None
            url = url or None

        # Date extraction (optional)
        try:
            time_el = await article.query_selector(time_selector)
            if time_el:
                # Prefer datetime attribute, fall back to text content
                datetime_attr = await time_el.get_attribute('datetime')
                date_text = None
                if datetime_attr:
                    date_text = datetime_attr.strip()
                else:
                    text = await time_el.text_content()
                    if text:
                        date_text = text.strip()
                if date_text:
                    # Try to parse into YYYY-MM-DD
                    try:
                        parsed = parse(date_text)
                        date_val = parsed.date().isoformat()
                    except Exception:
                        # If parse fails (e.g., relative "15h"), set None
                        date_val = None
        except Exception:
            date_val = None

        # Ensure required fields exist before adding
        if title and url:
            items.append({
                'title': title,
                'date': date_val,
                'url': url,
                'scraper': SCRAPER_MODULE_PATH,
            })

    return items

async def advance_page(page):
    """
    Finds the next page button or link to navigate to the next page of articles.
    Clicks/navigates to next page URL if found. Scrolls to load more if no pagination found.

    Parameters:
        page: Playwright page object
    """
    # Strategy:
    # - Collect candidate anchors (with href or rel=next)
    # - Score them preferring page-based pagination (?page= or page=), rel=next, pagination classes, and "Read more"/"Next" text
    # - Avoid links that go to unrelated sections (e.g., /events)
    # - Try navigation via page.goto for stable URL-based pagination; fallback to click+wait_for_navigation
    previous_url = page.url

    try:
        anchors = await page.query_selector_all("a[href], a[rel='next']")
    except Exception:
        anchors = []

    best_candidate = None
    best_score = -9999

    for a in anchors:
        try:
            href = await a.get_attribute('href')
            if not href:
                continue
            href = href.strip()
            full_url = urllib.parse.urljoin(page.url, href)
            # Skip anchors that don't change the page (anchors to same URL or fragment)
            if full_url == previous_url:
                # Could still be JS-driven, consider a small negative score but continue
                pass

            # Gather metadata for scoring
            cls = (await a.get_attribute('class')) or ''
            rel = (await a.get_attribute('rel')) or ''
            text = (await a.text_content()) or ''
            ltext = text.lower()
            lhref = href.lower()
            lcls = cls.lower()

            score = 0
            if 'page=' in lhref:
                score += 30
            if 'rel' in rel.lower() and 'next' in rel.lower():
                score += 20
            # class-based indicators
            if 'pagination' in lcls or 'pagination' in lhref:
                score += 8
            if 'section__linkbutton' in lcls or 'linkbutton' in lcls:
                score += 3
            # text indicators
            if 'read more' in ltext or 'readmore' in ltext or 'read more' in text:
                score += 6
            if 'next' in ltext and 'page' in ltext:
                score += 6
            if 'next' in ltext and score == 0:
                score += 4

            # Penalize likely irrelevant destinations
            if '/events' in lhref or '/search' in lhref or 'mailto:' in lhref:
                score -= 50
            if lhref.startswith('#'):
                score -= 50

            # If href leads to a new URL (different from current), give small boost
            if full_url != previous_url:
                score += 2

            if score > best_score:
                best_score = score
                best_candidate = (a, href, full_url, score)
        except Exception:
            continue

    # If we didn't find a scored candidate but there are anchors matching common pagination classes, try them
    if best_candidate is None or best_score < 0:
        # fallback selectors (original intent)
        fallback_selectors = [
            "a[rel='next']",
            "a[class*='Pagination__link']",
            "a[class*='Section__linkButton']",
            "a[class*='linkButton']",
            "a[class*='styles_Pagination__link']",
            "a[class*='styles_Section__linkButton']",
        ]
        for sel in fallback_selectors:
            try:
                el = await page.query_selector(sel)
            except Exception:
                el = None
            if el:
                try:
                    href = await el.get_attribute('href')
                except Exception:
                    href = None
                if href:
                    full_url = urllib.parse.urljoin(page.url, href.strip())
                    best_candidate = (el, href.strip(), full_url, 1)
                    break
                else:
                    best_candidate = (el, None, None, 0)
                    break

    # Attempt to navigate using the best candidate
    if best_candidate:
        el, href, full_url, score = best_candidate
        # Prefer URL-based navigation when href looks like page-based pagination
        if href:
            try:
                # prefer using goto if it will change URL
                target_url = urllib.parse.urljoin(page.url, href)
                if target_url != previous_url:
                    try:
                        await page.goto(target_url)
                        try:
                            await page.wait_for_load_state('networkidle', timeout=8000)
                        except Exception:
                            # give a small grace period
                            await page.wait_for_timeout(1500)
                        # ensure URL changed; if not, fall back to click
                        if page.url != previous_url:
                            return
                    except Exception:
                        # goto failed, fall back to click below
                        pass

                # If target_url == previous_url or goto didn't change, try click
                try:
                    await el.scroll_into_view_if_needed()
                except Exception:
                    pass
                try:
                    # Use a wait_for_navigation to capture single-page-app navigations
                    try:
                        await asyncio.wait_for(
                            asyncio.gather(
                                page.wait_for_navigation(timeout=8000),
                                el.click()
                            ),
                            timeout=10
                        )
                    except Exception:
                        # If navigation didn't happen, still allow some time for content to update
                        await page.wait_for_timeout(1500)
                    return
                except Exception:
                    # fallback to simple click without waiting
                    try:
                        await el.click()
                        try:
                            await page.wait_for_load_state('networkidle', timeout=8000)
                        except Exception:
                            await page.wait_for_timeout(1500)
                        return
                    except Exception:
                        pass
            except Exception:
                pass
        else:
            # Element exists but no href: try clicking it (JS-driven)
            try:
                try:
                    await el.scroll_into_view_if_needed()
                except Exception:
                    pass
                try:
                    await asyncio.gather(
                        page.wait_for_navigation(timeout=8000),
                        el.click()
                    )
                    return
                except Exception:
                    try:
                        await el.click()
                        try:
                            await page.wait_for_load_state('networkidle', timeout=8000)
                        except Exception:
                            await page.wait_for_timeout(1500)
                        return
                    except Exception:
                        pass
            except Exception:
                pass

    # Fallback: infinite scroll approach (if no pagination link worked)
    previous_height = None
    for _ in range(3):
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)
            new_height = await page.evaluate("document.body.scrollHeight")
            if previous_height is not None and new_height == previous_height:
                break
            previous_height = new_height
        except Exception:
            await page.wait_for_timeout(2000)

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
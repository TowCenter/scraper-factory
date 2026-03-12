import json
import os
import re
from playwright.async_api import async_playwright
from playwright_stealth import Stealth  # v2.0.1 API
from dateutil.parser import parse
import urllib.parse
import asyncio

base_url = 'https://www.breitbart.com/search/?s=minneapolis'

# Scraper module path for tracking the source of scraped data
SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

# Operator user-agent (set in operator.json)
USER_AGENT = ''

class PlaywrightContext:
    """Context manager for Playwright browser sessions."""

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        # Use headless mode for automated environments and include common args for CI
        launch_args = {'headless': True, 'args': ['--no-sandbox', '--disable-setuid-sandbox']}
        self.browser = await self.playwright.chromium.launch(**launch_args)
        context_kwargs = {'user_agent': USER_AGENT} if USER_AGENT else {}
        self.context = await self.browser.new_context(**context_kwargs)
        return self.context

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.browser.close()
        await self.playwright.stop()

async def _safe_text(elem):
    """Return stripped text_content() of element or None if element is None."""
    if not elem:
        return None
    txt = await elem.text_content()
    if txt is None:
        return None
    return txt.strip()

def _looks_like_date(text):
    """Heuristic to determine whether the text contains a date-like token."""
    if not text:
        return False
    # Look for month names, numeric date patterns, or 4-digit year
    month_re = re.compile(r'\b(January|February|March|April|May|June|July|August|September|October|November|December|'
                          r'Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\b', re.I)
    numeric_re = re.compile(r'\b\d{1,2}/\d{1,2}/\d{2,4}\b')
    year_re = re.compile(r'\b(19|20)\d{2}\b')
    if month_re.search(text) or numeric_re.search(text) or year_re.search(text):
        return True
    return False

async def _extract_date_from_text(text):
    """Try to parse a date from text using dateutil with fuzzy parsing. Return YYYY-MM-DD or None."""
    if not text or not _looks_like_date(text):
        return None
    try:
        # First try fuzzy parsing
        dt = parse(text, fuzzy=True)
    except Exception:
        try:
            dt = parse(text)
        except Exception:
            return None
    if not dt:
        return None
    year = getattr(dt, 'year', None)
    if not year or year < 1900 or year > 2100:
        return None
    return dt.date().isoformat()

async def scrape_page(page):
    """
    Extract article data from the current page. This implementation is more robust for Breitbart's search
    result layout: it finds meaningful anchors pointing to breitbart.com, extracts headline text, and
    attempts to find a nearby <time> element for the date.
    """
    items = []
    # Choose a logical container to limit anchors; fall back to whole page
    container = await page.query_selector('main') or await page.query_selector('#content') or page

    anchors = await container.query_selector_all('a[href]')
    for a in anchors:
        href = await a.get_attribute('href') or ''
        href = href.strip()
        if not href:
            continue

        # Normalize relative URLs to absolute
        href = urllib.parse.urljoin(page.url or base_url, href)

        # Only accept links that go to Breitbart article pages
        if 'breitbart.com' not in href:
            continue
        # Skip obvious navigation or search links
        if '/search' in href or href.endswith('#') or href.lower().startswith('mailto:'):
            continue

        # Get headline text — prefer visible text from the anchor
        title = await _safe_text(a)
        if not title or len(title) < 5:
            # try image alt or surrounding heading elements
            img = await a.query_selector('img[alt]')
            if img:
                alt = await img.get_attribute('alt') or ''
                if len(alt.strip()) >= 5:
                    title = alt.strip()
                else:
                    continue
            else:
                # attempt to find heading within a's ancestor nodes
                heading = await a.evaluate('''(el) => {
                    let p = el;
                    for (let i = 0; i < 5 && p; i++) {
                        p = p.parentElement;
                        if (!p) break;
                        const h = p.querySelector('h1, h2, h3, .headline, .title');
                        if (h && h.textContent && h.textContent.trim().length > 4) return h.textContent;
                    }
                    return null;
                }''')
                if heading:
                    title = heading.strip() if isinstance(heading, str) else None
                else:
                    continue

        # Attempt to find a date in nearby nodes (look up to several ancestor levels)
        raw_dt = await a.evaluate(r'''
            (el) => {
                let p = el;
                for (let i = 0; i < 6 && p; i++) {
                    p = p.parentElement;
                    if (!p) break;
                    // standard <time datetime="...">
                    const t = p.querySelector('time');
                    if (t) {
                        if (t.getAttribute('datetime')) return t.getAttribute('datetime');
                        if (t.textContent) return t.textContent;
                    }
                    // common meta selectors
                    const meta = p.querySelector('.byline, .meta, .date, .posted, .post-meta, .article-meta, span');
                    if (meta && meta.textContent && /\\d{4}|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec/i.test(meta.textContent)) {
                        return meta.textContent;
                    }
                }
                // also check immediate siblings
                p = el.parentElement;
                if (p) {
                    const siblingTime = p.querySelector('time');
                    if (siblingTime) {
                        if (siblingTime.getAttribute('datetime')) return siblingTime.getAttribute('datetime');
                        if (siblingTime.textContent) return siblingTime.textContent;
                    }
                }
                return null;
            }
        ''')

        date = None
        if raw_dt:
            parsed_date = await _extract_date_from_text(raw_dt)
            date = parsed_date

        items.append({
            'title': title.strip(),
            'date': date,
            'url': href,
            'scraper': SCRAPER_MODULE_PATH,
        })

    # Deduplicate by URL, keeping first occurrence
    seen = set()
    deduped = []
    for it in items:
        if it['url'] in seen:
            continue
        seen.add(it['url'])
        deduped.append(it)

    return deduped

async def advance_page(page):
    """
    Finds the next page button or link to navigate to the next page of articles.
    Clicks button or navigates to next page URL if found. Scroll load more button into view if not visible.
    Defaults to infinite scroll if no pagination found.
    """
    try:
        # Try common "next" link patterns
        next_link = await page.query_selector('a[rel="next"], a.next, .pagination a.next, .nav-next a')
        if next_link:
            href = await next_link.get_attribute('href')
            if href:
                href = urllib.parse.urljoin(page.url or base_url, href)
                await page.goto(href)
                await page.wait_for_load_state('networkidle', timeout=8000)
                await page.wait_for_timeout(1000)
                return
            else:
                try:
                    await next_link.scroll_into_view_if_needed()
                    await next_link.click()
                    await page.wait_for_load_state('networkidle', timeout=8000)
                    await page.wait_for_timeout(1000)
                    return
                except Exception:
                    pass

        # Fallback to original Google CSE style pagination
        cursor_items = await page.query_selector_all('div.gsc-cursor-page')
        if cursor_items:
            current_num = None
            for el in cursor_items:
                cls = await el.get_attribute('class') or ''
                if 'gsc-cursor-current-page' in cls:
                    txt = await el.text_content()
                    try:
                        current_num = int(txt.strip())
                    except Exception:
                        current_num = None
                    break
            next_el = None
            if current_num is not None:
                target_label = f'Page {current_num + 1}'
                next_el = await page.query_selector(f'div.gsc-cursor-page[aria-label="{target_label}"]')
                if not next_el:
                    for el in cursor_items:
                        aria = await el.get_attribute('aria-label') or ''
                        if 'Page' in aria:
                            m = re.search(r'Page\s+(\d+)', aria)
                            if m and int(m.group(1)) == current_num + 1:
                                next_el = el
                                break
            if not next_el:
                for el in cursor_items:
                    aria = await el.get_attribute('aria-label') or ''
                    cls = await el.get_attribute('class') or ''
                    txt = (await el.text_content()) or ''
                    if 'gsc-cursor-current-page' in cls:
                        continue
                    if 'Page' in aria or txt.strip().isdigit():
                        next_el = el
                        break
            if next_el:
                try:
                    await next_el.scroll_into_view_if_needed()
                    await next_el.click()
                    await page.wait_for_load_state('networkidle', timeout=8000)
                    await page.wait_for_timeout(1500)
                    return
                except Exception:
                    href = await next_el.get_attribute('href')
                    if href:
                        await page.goto(href)
                        await page.wait_for_load_state('networkidle', timeout=8000)
                        await page.wait_for_timeout(1000)
                        return
    except Exception:
        pass

    # Final fallback: simple infinite scroll to try loading more results
    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(3000)
        for _ in range(2):
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            await page.wait_for_timeout(1500)
    except Exception:
        await page.wait_for_timeout(1000)


async def get_first_page(base_url=base_url):
    """Fetch only the first page of articles."""
    async with PlaywrightContext() as context:
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)
        await page.goto(base_url)
        # give the page a moment to render dynamic content
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
        await page.wait_for_timeout(1000)

        page_count = 0
        item_count = 0  # previous count
        new_item_count = 0  # current count

        try:
            while page_count < max_pages:
                page_items = await scrape_page(page)
                for item in page_items:
                    key = item.get('url')
                    if key and key not in seen:
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
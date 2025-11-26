import json
import os
from playwright.async_api import async_playwright
from dateutil.parser import parse
import urllib.parse
import asyncio

base_url = 'https://president.columbia.edu/announcements'

SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

class PlaywrightContext:
    """Context manager for Playwright browser sessions."""
    
    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=False)
        self.context = await self.browser.new_context()
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
        List of dictionaries containing article data (title, date, url)
    """
    articles = []
    
    # Select all article containers
    article_elements = await page.query_selector_all('.views-row')
    
    for article in article_elements:
        # Extract title
        title_element = await article.query_selector('.views-field-title a')
        title = await title_element.inner_text() if title_element else None
        
        # Extract URL
        url = await title_element.get_attribute('href') if title_element else None
        if url:
            url = urllib.parse.urljoin(base_url, url)
        
        # Extract date
        date_element = await article.query_selector('.views-field-field-cu-date .field-content time')
        date_str = await date_element.get_attribute('datetime') if date_element else None
        if date_str:
            date_str = parse(date_str).strftime('%Y-%m-%d')
        
        # Append article data
        articles.append({
            'title': title,
            'date': date_str,
            'url': url,
            'scraper': SCRAPER_MODULE_PATH
        })
    
    return articles

async def advance_page(page):
    """
    Finds the next page button or link to navigate to the next page of articles.
    Clicks button or navigates to next page URL if found. Scroll load more button into view if not visible. 
    Defaults to infinite scroll if no pagination found.

    Parameters:
        page: Playwright page object
    """
    # Try to find a next page link
    next_page_link = await page.query_selector('.pagination a, .page-item a')
    
    if next_page_link:
        # Click the next page link
        await next_page_link.click()
        await page.wait_for_load_state('networkidle')
    else:
        # Fallback to infinite scroll
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(3000)

async def get_first_page(base_url=base_url):
    """Fetch only the first page of articles."""
    async with PlaywrightContext() as context:
        page = await context.new_page()
        await page.goto(base_url)
        articles = await scrape_page(page)
        await page.close()
        return articles

async def get_all_articles(base_url=base_url, max_pages=100):
    """Fetch all articles from all pages."""

    async with PlaywrightContext() as context:
        articles = []
        seen = set()
        page = await context.new_page()
        page_count = 0

        await page.goto(base_url)

        page_count = 0
        article_count = 0  # previous count
        new_article_count = 0  # current count

        try:
            while page_count < max_pages:
                page_articles = await scrape_page(page)
                for article in page_articles:
                    key = tuple(sorted(article.items()))
                    if key not in seen:
                        seen.add(key)
                        articles.append(article)
                new_article_count = len(articles)

                if new_article_count <= article_count:
                    break

                page_count += 1
                article_count = new_article_count

                await advance_page(page)
                            
        except Exception as e:
            print(f"Error occurred while getting next page: {e}")

            
        await page.close()
        return articles

async def main():
    """Main execution function."""
    all_articles = await get_all_articles()
    
    # Save results to JSON
    result_path = os.path.join(os.path.dirname(__file__), 'result.json')
    with open(result_path, 'w') as f:
        json.dump(all_articles, f, indent=2)
    print(f"Results saved to {result_path}")

if __name__ == "__main__":
    asyncio.run(main())
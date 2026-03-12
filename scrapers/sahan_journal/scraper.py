"""
Articles Scraper for Sahan Journal

Uses WordPress REST API directly.
Fetches all articles (no date filter - full archive).
"""

import json
import os
import time
import requests
import asyncio

base_url = 'https://sahanjournal.com/archive/'
API_URL = 'https://sahanjournal.com/wp-json/wp/v2/posts'
PAGE_SIZE = 20

SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}


def _fetch_page(page_num, retries=3):
    params = {
        'per_page': PAGE_SIZE,
        'page': page_num,
        'orderby': 'date',
        'order': 'desc',
    }
    for attempt in range(retries):
        try:
            r = requests.get(API_URL, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            total_pages = int(r.headers.get('X-WP-TotalPages', 1))
            total = int(r.headers.get('X-WP-Total', 0))
            return r.json(), total_pages, total
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(2)


def _parse_result(item):
    return {
        'title': item.get('title', {}).get('rendered', '').strip(),
        'date': item.get('date', '')[:10] or None,
        'url': item.get('link', ''),
        'scraper': SCRAPER_MODULE_PATH,
    }


async def get_first_page(base_url=base_url):
    results, _, _ = _fetch_page(1)
    return [_parse_result(r) for r in results]


async def get_all_articles(base_url=base_url, max_pages=200):
    items = []
    page = 1

    while page <= max_pages:
        try:
            results, total_pages, total = _fetch_page(page)
        except Exception as e:
            print(f"  Error on page {page}: {e} — saving what we have")
            break
        if not results:
            break

        items.extend(_parse_result(r) for r in results)
        print(f"  Fetched page {page}/{total_pages} ({len(items)}/{total} articles)")

        if page >= total_pages:
            break

        page += 1
        time.sleep(1)

    return items


async def main():
    print("Scraping Sahan Journal (full archive)...")
    all_items = await get_all_articles()

    result_path = os.path.join(os.path.dirname(__file__), 'result.json')
    with open(result_path, 'w', encoding='utf-8') as f:
        json.dump(all_items, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(all_items)} articles to {result_path}")



if __name__ == "__main__":
    asyncio.run(main())

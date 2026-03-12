"""
Articles Scraper for The Gateway Pundit - Minneapolis

Uses WordPress REST API directly.
Stops at December 1, 2025.
"""

import json
import os
import time
import requests
import asyncio

base_url = 'https://www.thegatewaypundit.com/?s=minneapolis'
API_URL = 'https://www.thegatewaypundit.com/wp-json/wp/v2/posts'
QUERY = 'minneapolis'
DATE_FROM = '2025-12-01T00:00:00'
PAGE_SIZE = 100

SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])

HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}


def _fetch_page(page_num):
    params = {
        'search': QUERY,
        'per_page': PAGE_SIZE,
        'page': page_num,
        'orderby': 'date',
        'order': 'desc',
        'after': DATE_FROM,
    }
    r = requests.get(API_URL, params=params, headers=HEADERS, timeout=20)
    r.raise_for_status()
    total_pages = int(r.headers.get('X-WP-TotalPages', 1))
    return r.json(), total_pages


def _parse_result(item):
    return {
        'title': item.get('title', {}).get('rendered', '').strip(),
        'date': item.get('date', '')[:10] or None,
        'url': item.get('link', ''),
        'body': item.get('excerpt', {}).get('rendered', '').strip(),
        'scraper': SCRAPER_MODULE_PATH,
    }


async def get_first_page(base_url=base_url):
    results, _ = _fetch_page(1)
    return [_parse_result(r) for r in results]


async def get_all_articles(base_url=base_url, max_pages=100):
    items = []
    page = 1

    while page <= max_pages:
        results, total_pages = _fetch_page(page)
        if not results:
            break

        items.extend(_parse_result(r) for r in results)
        print(f"  Fetched page {page}/{total_pages} ({len(items)} articles)")

        if page >= total_pages:
            break

        page += 1
        time.sleep(0.3)

    return items


async def main():
    print(f"Scraping Gateway Pundit for '{QUERY}' since {DATE_FROM[:10]}...")
    all_items = await get_all_articles()

    result_path = os.path.join(os.path.dirname(__file__), 'result.json')
    with open(result_path, 'w', encoding='utf-8') as f:
        json.dump(all_items, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(all_items)} articles to {result_path}")



if __name__ == "__main__":
    asyncio.run(main())

"""
Articles Scraper for CNN Minneapolis

Uses CNN's internal search API directly.
Fields: title, date, url, body
"""

import json
import os
import uuid
import time
import requests
import asyncio

base_url = 'https://www.cnn.com/search?q=minneapolis&from=0&size=10&page=1&sort=newest&types=all&section='
API_URL = 'https://search.prod.di.api.cnn.io/content'
QUERY = 'minneapolis'
PAGE_SIZE = 100

SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])
USER_AGENT = ''

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    'Referer': 'https://www.cnn.com/search?q=minneapolis',
    'Origin': 'https://www.cnn.com',
}


def _fetch_page(from_offset, size=PAGE_SIZE):
    params = {
        'q': QUERY,
        'from': from_offset,
        'size': size,
        'sort': 'newest',
        'types': 'all',
        'request_id': str(uuid.uuid4()),
    }
    r = requests.get(API_URL, params=params, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()


def _parse_result(item):
    raw_date = item.get('lastModifiedDate', '')
    date_value = None
    if raw_date:
        try:
            from dateutil.parser import parse
            date_value = parse(raw_date).date().isoformat()
        except Exception:
            pass
    return {
        'title': item.get('headline', '').strip(),
        'date': date_value,
        'url': item.get('path') or item.get('url', ''),
        'body': item.get('body', '').strip(),
        'scraper': SCRAPER_MODULE_PATH,
    }


async def get_first_page(base_url=base_url):
    data = _fetch_page(0, size=10)
    return [_parse_result(r) for r in data.get('result', [])]


async def get_all_articles(base_url=base_url, max_pages=100):
    items = []
    offset = 0

    while True:
        data = _fetch_page(offset)
        results = data.get('result', [])
        if not results:
            break

        items.extend(_parse_result(r) for r in results)

        meta = data.get('meta', {})
        total = meta.get('of', 0)
        end = meta.get('end', offset + len(results))

        print(f"  Fetched {len(items)}/{total} articles")

        if end >= total or len(items) >= max_pages * PAGE_SIZE:
            break

        offset += PAGE_SIZE
        time.sleep(0.5)

    return items


async def main():
    print(f"Scraping CNN search for '{QUERY}'...")
    all_items = await get_all_articles()

    result_path = os.path.join(os.path.dirname(__file__), 'result.json')
    with open(result_path, 'w', encoding='utf-8') as f:
        json.dump(all_items, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(all_items)} articles to {result_path}")



if __name__ == "__main__":
    asyncio.run(main())

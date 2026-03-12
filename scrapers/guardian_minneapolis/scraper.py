"""
Articles Scraper for The Guardian - Minneapolis

Uses Guardian's free open Content API.
Stops at December 1, 2025.
"""

import json
import os
import time
import requests
import asyncio

base_url = 'https://www.theguardian.com/us-news/minneapolis'
API_URL = 'https://content.guardianapis.com/search'
QUERY = 'minneapolis'
API_KEY = 'test'  # free open key
PAGE_SIZE = 50
DATE_FROM = '2025-12-01'

SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])


def _fetch_page(page_num):
    params = {
        'q': QUERY,
        'page': page_num,
        'page-size': PAGE_SIZE,
        'order-by': 'newest',
        'from-date': DATE_FROM,
        'show-fields': 'bodyText,headline,trailText',
        'api-key': API_KEY,
    }
    r = requests.get(API_URL, params=params, timeout=20)
    r.raise_for_status()
    return r.json()['response']


def _parse_result(item):
    date_value = item.get('webPublicationDate', '')[:10] or None
    fields = item.get('fields', {})
    return {
        'title': fields.get('headline') or item.get('webTitle', '').strip(),
        'date': date_value,
        'url': item.get('webUrl', ''),
        'body': (fields.get('bodyText') or fields.get('trailText') or '').strip(),
        'scraper': SCRAPER_MODULE_PATH,
    }


async def get_first_page(base_url=base_url):
    data = _fetch_page(1)
    return [_parse_result(r) for r in data.get('results', [])]


async def get_all_articles(base_url=base_url, max_pages=100):
    items = []
    page = 1

    while page <= max_pages:
        data = _fetch_page(page)
        results = data.get('results', [])
        if not results:
            break

        items.extend(_parse_result(r) for r in results)

        total_pages = data.get('pages', 1)
        print(f"  Fetched page {page}/{total_pages} ({len(items)} articles)")

        if page >= total_pages:
            break

        page += 1
        time.sleep(0.2)

    return items


async def main():
    print(f"Scraping Guardian for '{QUERY}' since {DATE_FROM}...")
    all_items = await get_all_articles()

    result_path = os.path.join(os.path.dirname(__file__), 'result.json')
    with open(result_path, 'w', encoding='utf-8') as f:
        json.dump(all_items, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(all_items)} articles to {result_path}")



if __name__ == "__main__":
    asyncio.run(main())

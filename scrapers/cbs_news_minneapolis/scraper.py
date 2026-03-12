"""
Articles Scraper for CBS News - Minneapolis

Uses CBS News's Queryly search API directly.
Stops at December 1, 2025.
"""

import json
import os
import time
import datetime
import requests
import asyncio

base_url = 'https://www.cbsnews.com/search/?q=minneapolis&sort=date'
API_URL = 'https://api.queryly.com/json.aspx'
QUERYLY_KEY = '4690eece66c6499f'
QUERY = 'minneapolis'
BATCH_SIZE = 100
STOP_DATE = datetime.date(2025, 12, 1)

SCRAPER_MODULE_PATH = '.'.join(os.path.splitext(os.path.abspath(__file__))[0].split(os.sep)[-3:])


def _fetch_batch(endindex=None):
    params = {
        'queryly_key': QUERYLY_KEY,
        'query': QUERY,
        'batchsize': BATCH_SIZE,
        'showfaceted': 'true',
        'facetedkey': 'pubDate',
        'facetedvalue': '26280',  # past 3 years
    }
    if endindex is not None:
        params['endindex'] = endindex
    r = requests.get(API_URL, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def _parse_item(item):
    ts = item.get('pubdateunix', 0)
    date_value = datetime.datetime.fromtimestamp(ts).date().isoformat() if ts else None
    return {
        'title': item.get('title', '').strip(),
        'date': date_value,
        'url': item.get('link', ''),
        'scraper': SCRAPER_MODULE_PATH,
    }


async def get_first_page(base_url=base_url):
    data = _fetch_batch()
    return [_parse_item(i) for i in data.get('items', [])]


async def get_all_articles(base_url=base_url, max_pages=100):
    items = []
    endindex = None

    for page in range(max_pages):
        data = _fetch_batch(endindex)
        batch = data.get('items', [])
        if not batch:
            break

        stop = False
        for item in batch:
            parsed = _parse_item(item)
            if parsed['date'] and parsed['date'] < STOP_DATE.isoformat():
                stop = True
                break
            items.append(parsed)

        meta = data.get('metadata', {})
        total = meta.get('total', 0)
        endindex = meta.get('endindex', 0)
        print(f"  Fetched {len(items)}/{total} articles (endindex={endindex})")

        if stop or endindex >= total:
            break

        time.sleep(0.3)

    return items


async def main():
    print(f"Scraping CBS News for '{QUERY}' since {STOP_DATE}...")
    all_items = await get_all_articles()

    result_path = os.path.join(os.path.dirname(__file__), 'result.json')
    with open(result_path, 'w', encoding='utf-8') as f:
        json.dump(all_items, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(all_items)} articles to {result_path}")


if __name__ == "__main__":
    asyncio.run(main())

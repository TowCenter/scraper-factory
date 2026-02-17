#!/usr/bin/env python3
"""
Runner script to fetch and update article content for articles stored in MongoDB.

Usage:
    python scrape_articles.py [--org <org_name>]

Options:
    --org <org_name>  Specify a org name to scrape only articles from that org.
                               If omitted, all orgs' articles will be scraped.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import asyncio
import argparse
import json
import urllib.request
from datetime import datetime, timezone, timedelta
from newspaper import Article, Config
from bs4 import BeautifulSoup
from readability import Document
import extruct
from w3lib.html import get_base_url
from dateutil.parser import parse as parse_date
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from utils import setup_logging
import logging

# Load environment vars from .env file
load_dotenv(override=True)

ARTICLE_TIMEOUT_SECONDS = int(os.environ.get("ARTICLE_TIMEOUT_SECONDS", "180"))
# Ensure logs/scripts directory exists
log_dir = os.path.join(os.path.dirname(__file__), '..', 'logs', 'scripts')
os.makedirs(log_dir, exist_ok=True)
# Set up logging for this script
script_name = os.path.splitext(os.path.basename(__file__))[0]
log_file = os.path.join(os.path.dirname(__file__), '..', 'logs', 'scripts', f"{script_name}.log")
logger = setup_logging('INFO', log_file)
logging.getLogger("readability.readability").setLevel(logging.WARNING)

def parse_args():
    parser = argparse.ArgumentParser(description="Scrape article content from URLs in the database.")
    parser.add_argument(
        "--org",
        type=str,
        help="Specify a org name to scrape only articles from that org. If omitted, all orgs' articles will be scraped."
    )
    return parser.parse_args()

async def get_content_with_playwright(url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/121.0.0.0 Safari/537.36"
        ))
        page = await context.new_page()
        try:
            await page.goto(url, wait_until='networkidle', timeout=30000)
        except:
            pass  # Element might not exist on all pages

        # Get the full HTML content
        await page.wait_for_timeout(1000)
        html_content = await page.content()
        await browser.close()
        return html_content


META_DATE_KEYS = {
    "article:published_time",
    "article:published",
    "article:publication_time",
    "article-publish-date",
    "og:published_time",
    "og:pubdate",
    "pubdate",
    "publish_date",
    "publish-date",
    "publishdate",
    "datepublished",
    "date_published",
    "dc.date.issued",
    "dc:date.issued",
    "citation_publication_date",
    "parsely-pub-date",
    "article:modified_time",
    "og:modified_time",
    "dateModified",
}

META_DATE_KEYS_LOWER = {k.lower() for k in META_DATE_KEYS}

JSONLD_DATE_KEYS = {
    "datePublished",
    "dateCreated",
    "dateModified",
    "dateUpdated",
}


def _parse_date_candidate(value):
    if not value:
        return None
    try:
        dt = parse_date(value)
    except Exception:
        return None

    # Reject future dates
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        if dt > datetime.now():
            return None
    else:
        if dt.astimezone(timezone.utc) > now:
            return None

    return dt


def _iter_kv_candidates(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            yield from _iter_kv_candidates(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_kv_candidates(item)


def extract_date_with_extruct(html_content, base_url=""):
    if not html_content:
        return None, None

    try:
        metadata = extruct.extract(
            html_content,
            base_url=get_base_url(html_content, base_url or ""),
            syntaxes=("json-ld", "microdata", "opengraph", "rdfa", "microformat"),
            uniform=True,
        )
    except Exception:
        return None, None

    normalized_keys = {k.lower() for k in JSONLD_DATE_KEYS} | META_DATE_KEYS_LOWER

    for syntax, payload in metadata.items():
        for key, value in _iter_kv_candidates(payload):
            if not isinstance(key, str):
                continue
            key_norm = key.strip().lower()
            if key_norm not in normalized_keys:
                continue
            if isinstance(value, list):
                candidates = value
            else:
                candidates = [value]

            for candidate in candidates:
                if isinstance(candidate, dict):
                    for _, nested in _iter_kv_candidates(candidate):
                        if isinstance(nested, str):
                            dt = _parse_date_candidate(nested)
                            if dt:
                                return dt, f"extruct:{syntax}:{key_norm}"
                elif isinstance(candidate, str):
                    dt = _parse_date_candidate(candidate)
                    if dt:
                        return dt, f"extruct:{syntax}:{key_norm}"

    return None, None


def extract_date_from_html(html_content):
    if not html_content:
        return None, None

    soup = BeautifulSoup(html_content, "lxml")

    # 1) JSON-LD datePublished
    for script in soup.find_all("script", type="application/ld+json"):
        text = script.string or script.get_text()
        if not text:
            continue
        try:
            data = json.loads(text)
        except Exception:
            continue

        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            for key in JSONLD_DATE_KEYS:
                if key in item:
                    dt = _parse_date_candidate(item.get(key))
                    if dt:
                        return dt, f"jsonld:{key}"
            # nested in @graph
            if "@graph" in item and isinstance(item["@graph"], list):
                for g in item["@graph"]:
                    if not isinstance(g, dict):
                        continue
                    for key in JSONLD_DATE_KEYS:
                        if key in g:
                            dt = _parse_date_candidate(g.get(key))
                            if dt:
                                return dt, f"jsonld:{key}"

    # 2) <meta> tags
    for meta in soup.find_all("meta"):
        key = meta.get("property") or meta.get("name") or meta.get("itemprop")
        if not key:
            continue
        key_lower = key.strip().lower()
        if key_lower in META_DATE_KEYS_LOWER:
            dt = _parse_date_candidate(meta.get("content"))
            if dt:
                return dt, f"meta:{key_lower}"

    # 3) extruct structured metadata (microdata/rdfa/opengraph/etc.)
    dt, source = extract_date_with_extruct(html_content)
    if dt:
        return dt, source

    # 4) <time datetime="...">
    for t in soup.find_all("time"):
        dt_value = t.get("datetime")
        if dt_value:
            dt = _parse_date_candidate(dt_value)
            if dt:
                return dt, "time:datetime"

    # 5) Common publish date classnames (e.g. <li class="article-publish-date">...)
    for selector in (".article-publish-date", ".publish-date", ".published-date", ".date-published"):
        el = soup.select_one(selector)
        if not el:
            continue
        text = el.get_text(strip=True)
        dt = _parse_date_candidate(text)
        if dt:
            return dt, f"class:{selector}"

    return None, None


def extract_with_readability(html_content):
    if not html_content:
        return None

    try:
        doc = Document(html_content)
        clean_html = doc.summary(html_partial=True)
        soup = BeautifulSoup(clean_html, "lxml")

        for tag in soup(["script", "style", "nav", "footer", "aside"]):
            tag.decompose()

        paragraphs = []
        for p in soup.find_all("p"):
            text = p.get_text(strip=True)
            if len(text) > 40:
                paragraphs.append(text)

        if not paragraphs:
            return None

        return "\n\n".join(paragraphs)
    except Exception:
        return None


def _clean_jina_text(raw_text):
    if not raw_text:
        return None

    cleaned_lines = []
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("URL Source:"):
            continue
        if stripped.startswith("Markdown Content:"):
            continue
        cleaned_lines.append(stripped)

    if not cleaned_lines:
        return None

    content = "\n\n".join(cleaned_lines).strip()
    return content if len(content) >= 120 else None


def extract_with_jina_ai(url):
    if not url:
        return None

    target_url = f"https://r.jina.ai/{url}"
    request = urllib.request.Request(
        target_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            )
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = getattr(response, "status", 200)
            if status >= 400:
                return None
            raw_text = response.read().decode("utf-8", errors="replace")
            return _clean_jina_text(raw_text)
    except Exception:
        return None

async def process_article(article, collection, config, update_content=True, update_date=True, update_author=True, phase: str = "content"):
    url = article.get("url")
    org = article.get("org", "unknown")
    scraper = article.get("scraper", "unknown")
    title = article.get("title", "")

    if not url:
        return

    article_id = article["_id"]

    content_status = "skipped" if not update_content else "init"
    date_status = "skipped"    if not update_date    else "init"
    author_status = "skipped"  if not update_author  else "init"
    write_status = "no-op"

    pub_date = None
    content = None
    authors = None
    author_str = None

    # Record errors and fallback status
    last_error_str = None
    used_playwright = False
    fallback_reason = None

    try:
        # First try normal newspaper extraction
        def _download_and_parse():
            news_article = Article(url, config=config)
            news_article.download()
            news_article.parse()
            return news_article

        news_article = await asyncio.to_thread(_download_and_parse)
        if update_content:
            content = (news_article.text or "").strip()
            content_status = "parsed" if content else "no content in parse"

        if update_date:
            pub_date = news_article.publish_date
            date_status = "parsed" if pub_date else "no date in parse"
            if not pub_date:
                extracted, source = extract_date_from_html(getattr(news_article, "html", None))
                if extracted:
                    pub_date = extracted
                    date_status = f"extracted {source}"

        if update_author:
            authors = news_article.authors if news_article.authors else None
            author_str = ", ".join(authors) if authors else None
            author_status = "parsed" if author_str else "no author in parse"

    except Exception as e:
        last_error_str = str(e)
        if update_content:
            content_status = f"error: {last_error_str}"
        if update_date and date_status != "skipped":
            date_status = f"error: {last_error_str}"
        if update_author and author_status != "skipped":
            author_status = f"error: {last_error_str}"

    # If error has 403/Forbidden
    if last_error_str and (update_content or update_author or update_date):
        fallback_reason = "error"
    # if content is empty
    elif update_content and content_status == "no content in parse":
        fallback_reason = "content-empty"
    # if author is empty
    elif update_author and author_status == "no author in parse":
        fallback_reason = "author-empty"
    # if date is empty
    elif update_date and date_status == "no date in parse":
        fallback_reason = "date-empty"

    if fallback_reason and not used_playwright:
        try:
            html_content = await get_content_with_playwright(url)
            used_playwright = True

            def _parse_html():
                news_article = Article(url, config=config)
                news_article.set_html(html_content)
                news_article.parse()
                return news_article

            news_article = await asyncio.to_thread(_parse_html)

            if update_content and not content:
                content = (news_article.text or "").strip()
                content_status = "parsed after playwright" if content else "no content after playwright"

            if update_content and not content:
                readability_content = extract_with_readability(html_content)
                if readability_content:
                    content = readability_content
                    content_status = "readability-extracted"

            if update_content and not content:
                jina_content = await asyncio.to_thread(extract_with_jina_ai, url)
                if jina_content:
                    content = jina_content
                    content_status = "jina-ai-extracted"

            if update_date and not pub_date:
                pub_date = news_article.publish_date
                date_status = "parsed after playwright" if pub_date else "no date after playwright"
                if not pub_date:
                    extracted, source = extract_date_from_html(html_content)
                    if extracted:
                        pub_date = extracted
                        date_status = f"extracted after playwright {source}"

            if update_author and not author_str:
                authors = news_article.authors if news_article.authors else None
                author_str = ", ".join(authors) if authors else None
                author_status = "parsed after playwright" if author_str else "no author after playwright"

        except Exception as p_e:
            pe = str(p_e)
            if update_content and (content_status in ("no content in parse",) or content_status.startswith("error:")):
                content_status = f"playwright error: {pe}"
            if update_date and (date_status in ("no date in parse",) or date_status.startswith("error:")):
                date_status = f"playwright error: {pe}"
            if update_author and (author_status in ("no author in parse",) or author_status.startswith("error:")):
                author_status = f"playwright error: {pe}"

    # Update the document if we got new data
    update_fields = {}
    if update_content and content:
        update_fields["content"] = content
    if update_date and pub_date:
        update_fields["date"] = pub_date
    if update_author and author_str:
        update_fields["author"] = author_str

    if update_fields:
        try:
            set_stage = {k: v for k, v in update_fields.items()}
            changed_conds = [{"$ne": [f"${k}", v]} for k, v in update_fields.items()]
            set_stage["last_updated_at"] = {
                "$cond": [
                    {"$or": changed_conds},
                    "$$NOW",
                    "$last_updated_at"
                ]
            }
            result = await asyncio.to_thread(
                collection.update_one,
                {"_id": article_id},
                [{"$set": set_stage}]
            )
            write_status = "UPDATED" if result.modified_count else "UNCHANGED"
        except PyMongoError as e:
            write_status = f"DBERROR:{e}"
    else:
        write_status = "NOOP"

    # --- standardized metric line ---
    # normalize statuses (no spaces) for grepping:
    def norm(s: str) -> str:
        return s.replace(" ", "_").upper() if isinstance(s, str) else str(s).upper()

    # machine-friendly flags:
    playwright_used = "YES" if used_playwright else "NO"
    playwright_ok = "YES" if (
        used_playwright and (
            (isinstance(content_status, str) and ("parsed after playwright" in content_status)) or
            (isinstance(date_status, str) and ("parsed after playwright" in date_status)) or
            (isinstance(author_status, str) and ("parsed after playwright" in author_status))
        )
    ) else "NO"
    # error if any field contains "error" or DBERROR
    error_flag = "YES" if (
            (isinstance(content_status, str) and "ERROR" in content_status.upper()) or
            (isinstance(date_status, str) and "ERROR" in date_status.upper()) or
            (isinstance(author_status, str) and "ERROR" in author_status.upper()) or
            (isinstance(write_status, str) and write_status.startswith("DBERROR"))
    ) else "NO"

    # empty if any field is empty
    empty_flag = "YES" if (
            (isinstance(content_status, str) and content_status.startswith("no content")) or
            (isinstance(date_status, str) and date_status.startswith("no date")) or
            (isinstance(author_status, str) and author_status.startswith("no author"))
    ) else "NO"

    log = logger.error if error_flag=="YES" else logger.info

    log(
        f"ART_METRIC "
        f"PHASE={norm(phase)} "
        f"RESULT={norm(write_status)} "
        f"ERROR={error_flag} "
        f"EMPTY={empty_flag} "
        f"PLAYWRIGHT_USED={playwright_used} "
        f"PLAYWRIGHT_OK={playwright_ok} "
        f"CONTENT_STATUS={norm(content_status)} "
        f"DATE_STATUS={norm(date_status)} "
        f"AUTHOR_STATUS={norm(author_status)} "
        f"FALLBACK={norm(fallback_reason or 'none')} "
        f"URL=\"{url}\"",
        extra={
            "article_url": url,
            "article_org": org,
            "article_scraper": scraper,
            "article_title": title,
            "countent_status": norm(content_status),
            "date_status": norm(date_status),
            "author_status": norm(author_status),
        },
    )

async def process_article_wrapper(article, collection, config, semaphore, update_content=True, update_date=True, update_author=True, phase="content"):
    async with semaphore:
        url = article.get("url")
        try:
            await asyncio.wait_for(
                process_article(article, collection, config, update_content, update_date, update_author, phase),
                timeout=ARTICLE_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            logger.error(
                f"ART_TIMEOUT PHASE={phase.upper()} URL=\"{url}\" "
                f"TIMEOUT_SECONDS={ARTICLE_TIMEOUT_SECONDS}"
            )

async def process_missing_content_articles(collection, config, org_name=None, semaphore=None):
    # Query for articles with empty content
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=30)
    query = {
                "$and": [
                    {"last_updated_at": {"$gte": cutoff_date}},
                    {
                        "$or": [
                            {"content": {"$exists": False}},
                            {"content": None},
                            {"content": ""}
                        ]
                    }
                ]
            }
    if org_name:
        query["org"] = org_name
        logger.info(f"Filtering for org (content): {org_name}")
    article_count = collection.count_documents(query)
    logger.info(f"Found {article_count} articles with missing content.", extra={"missing_content_count": article_count})
    if article_count == 0:
        return
    articles = collection.find(query)
    # Use the wrapper
    tasks = []
    for article in articles:
        tasks.append(process_article_wrapper(
            article, collection, config, semaphore, update_content=True, update_date=False, update_author=False, phase="content"
        ))
    await asyncio.gather(*tasks)

async def process_missing_date_articles(collection, config, org_name=None, semaphore=None):
    # Query for articles with missing date
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=30)
    query = {
            "$and": [
                    {"last_updated_at": {"$gte": cutoff_date}},
                    {
                        "$or": [
                            {"date": {"$exists": False}},
                            {"date": None},
                            {"date": ""}
                        ]
                    }
                ]
            }
    if org_name:
        query["org"] = org_name
        logger.info(f"Filtering for org (date): {org_name}")
    article_count = collection.count_documents(query)
    logger.info(f"Found {article_count} articles with missing date.", extra={"missing_date_count": article_count})
    if article_count == 0:
        return
    articles = collection.find(query)
    # Use the wrapper
    tasks = []
    for article in articles:
        tasks.append(process_article_wrapper(
            article, collection, config, semaphore, update_content=False, update_date=True, update_author=False, phase="date"
        ))
    await asyncio.gather(*tasks)

async def process_missing_author_articles(collection, config, org_name=None, semaphore=None):
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=2)
    query = {
        "$and": [
            {"last_updated_at": {"$gte": cutoff_date}},
            {
                "$or": [
                    {"author": {"$exists": False}},
                    {"author": None},
                    {"author": ""}
                ]
            }
        ]
    }
    if org_name:
        query["org"] = org_name
        logger.info(f"Filtering for org (author): {org_name}")
    article_count = collection.count_documents(query)
    logger.info(f"Found {article_count} articles with missing author.", extra={"missing_author_count": article_count})
    if article_count == 0:
        return
    articles = collection.find(query)
    tasks = []
    for article in articles:
        tasks.append(process_article_wrapper(
            article, collection, config, semaphore, update_content=False, update_date=False, update_author=True, phase="author"
        ))
    await asyncio.gather(*tasks)

async def run():
    # Parse command-line arguments
    args = parse_args()
    org_name = args.org

    # Configuration
    MONGO_URI = os.environ.get("MONGO_URI")
    DB_NAME = os.environ.get("DB_NAME")

    # Configure newspaper with browser headers
    user_agent = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
    config = Config()
    config.browser_user_agent = user_agent
    config.request_timeout = 30

    # Connect to MongoDB
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
    except PyMongoError as e:
        logger.error(f"❌ Could not connect to MongoDB at {MONGO_URI}: {e}")
        sys.exit(1)

    db = client[DB_NAME]
    collection = db["articles"]

    # Set concurrency limit (e.g., 10 tasks at a time)
    concurrency_limit = 10
    semaphore = asyncio.Semaphore(concurrency_limit)
    logger.info(f"Starting scrape with concurrency limit: {concurrency_limit}")
    # ---

    # Process missing content articles
    await process_missing_content_articles(collection, config, org_name, semaphore)
    # Process missing date articles
    await process_missing_date_articles(collection, config, org_name, semaphore)
    # Process missing author articles
    await process_missing_author_articles(collection, config, org_name, semaphore)

    logger.info("🎉 Article scraping complete.")

if __name__ == "__main__":
    asyncio.run(run())
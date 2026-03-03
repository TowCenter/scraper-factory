#!/usr/bin/env python3
"""
Runner script to iterate through orgs with defined scraper scrapers,
execute their scrapers, and store scraped articles into MongoDB.

Usage:
    python scrape_index.py [--org <org_name>] [--all]

Options:
    --org <org_name>  Specify a org name to scrape only that org.
                           If omitted, all orgs with scrapers will be scraped.
    --all                  Use get_all_articles instead of get_first_page
                           to get historical articles.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import importlib
import asyncio
import argparse
from datetime import datetime, timezone
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from dateutil.parser import parse as parse_date, ParserError
from scraper_generator.utils import setup_logging
from seed import main as run_seed
from scraper_generator.generator import load_content_config

# load environment vars from .env file
from dotenv import load_dotenv
import inspect
load_dotenv(override=True)
# Ensure logs/scripts directory exists
log_dir = os.path.join(os.path.dirname(__file__), '..', 'logs', 'scripts')
os.makedirs(log_dir, exist_ok=True)
# Set up logging for this script
script_name = os.path.splitext(os.path.basename(__file__))[0]
log_file = os.path.join(os.path.dirname(__file__), '..', 'logs', 'scripts', f"{script_name}.log")
logger = setup_logging('INFO', log_file)

def parse_args():
    parser = argparse.ArgumentParser(description="Scrape articles for orgs.")
    parser.add_argument(
        "--maxpages",
        type=int,
        default=100,
        help="Maximum number of pages to scrape when using --all flag"
    )
    parser.add_argument(
        "--org",
        type=str,
        help="Specify a org name to scrape only that org. If omitted, all orgs will be scraped."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Use get_all_articles instead of get_first_page to get historical articles."
    )
    return parser.parse_args()

async def run():
    # Seed orgs collection before scraping
    run_seed()

    # Parse command-line arguments
    args = parse_args()
    org_name = args.org
    use_all = args.all
    max_pages = args.maxpages

    # Configuration
    MONGO_URI = os.environ.get("MONGO_URI")
    DB_NAME = os.environ.get("DB_NAME")

    # Load content config for dynamic collection/field names
    content_config = load_content_config()
    content_col  = content_config["content_type"]
    scrapers_col = content_config["content_type"] + "_scrapers"
    fields       = content_config["fields"]
    url_field    = next((f["name"] for f in fields if f.get("type") == "url"), "url")
    date_field   = next((f["name"] for f in fields if f.get("type") == "date"), None)

    # Ensure project root is in the path so scrapers can be imported
    script_dir = os.path.dirname(__file__)
    project_root = os.path.abspath(os.path.join(script_dir, os.pardir))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    # Connect to MongoDB
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
    except PyMongoError as e:
        logger.exception(f"DBERROR: Could not connect to MongoDB at {MONGO_URI}: {e}", extra={"error": e})
        sys.exit(1)

    db = client[DB_NAME]

    # Find orgs to process
    query = {"scrapers": {"$exists": True, "$ne": []}}
    if org_name:
        query["name"] = org_name

    cursor = db[scrapers_col].find(query)
    orgs = list(cursor)
    if org_name:
        logger.info(f"Found {len(orgs)} org(s) matching name '{org_name}'.")
    else:
        logger.info(f"Found {len(orgs)} orgs with scrapers.")

    if not orgs:
        logger.info("No orgs to process.")
        return

    for org in orgs:
        org_name = org.get("name")
        scrapers = org.get("scrapers", [])
        for mod_cfg in scrapers:
            path = mod_cfg.get("path")
            scraper_url = mod_cfg.get("url")
            active = mod_cfg.get("active", True)
            manual_force_export_requested = mod_cfg.get("manual_force_export", False)
            if not isinstance(active, bool):
                active = True
            if not isinstance(manual_force_export_requested, bool):
                manual_force_export_requested = False
            # One-time override: always reset to false on the next scrape run.
            manual_force_export = False

            if manual_force_export_requested:
                logger.info(
                    f"SCRAPER_INFO: manual_force_export was true for '{path}' ({org_name}); it will be reset to false in this run.",
                    extra={
                        "org": org_name,
                        "scraper_url": scraper_url,
                        "path": path,
                    },
                )

            method_name = "get_all_articles" if use_all else "get_first_page"

            logger.info(f"SCRAPER_START Running '{method_name}' for '{path}' for org '{org_name}'")

            if active is False:
                skip_status = mod_cfg.get("last_run_status")
                if skip_status not in {"pass", "error", "unable_to_fetch"}:
                    skip_status = "unable_to_fetch"
                try:
                    db[scrapers_col].update_one(
                        {"_id": org.get("_id"), "scrapers.path": path},
                        {
                            "$set": {
                                "scrapers.$.active": active,
                                "scrapers.$.manual_force_export": manual_force_export,
                                "scrapers.$.last_run_status": skip_status,
                            }
                        },
                    )
                except PyMongoError as update_err:
                    logger.exception(
                        f"SCRAPER_FINISH status=failed: DB error updating fields for skipped scraper '{path}': {update_err}",
                        extra={"org": org_name, "scraper_url": scraper_url, "method": method_name, "path": path, "error": update_err},
                    )
                logger.info(
                    f"SCRAPER_FINISH status=skipped: Skipping inactive scraper '{path}' for org '{org_name}'",
                    extra={
                        "org": org_name,
                        "scraper_url": scraper_url,
                        "method": method_name,
                        "path": path,
                        "active": active,
                    },
                )
                continue

            def update_scraper_run_fields(last_run_status, last_run_count=0):
                db[scrapers_col].update_one(
                    {"_id": org.get("_id"), "scrapers.path": path},
                    {
                        "$set": {
                            "scrapers.$.active": active,
                            "scrapers.$.manual_force_export": manual_force_export,
                            "scrapers.$.last_run": datetime.now(timezone.utc),
                            "scrapers.$.last_run_count": last_run_count,
                            "scrapers.$.last_run_status": last_run_status,
                        }
                    },
                )

            # Dynamically import the scraper module
            try:
                scraper = importlib.import_module(path)
            except (ImportError, SyntaxError, Exception) as e:
                try:
                    update_scraper_run_fields(last_run_status="error", last_run_count=0)
                except PyMongoError as update_err:
                    logger.exception(
                        f"SCRAPER_FINISH status=failed: DB error updating last_run_status for scraper '{path}' after import failure: {update_err}",
                        extra={"org": org_name, "scraper_url": scraper_url, "method": method_name, "path": path, "error": update_err},
                    )
                logger.exception(f"SCRAPER_FINISH status=failed: Failed to import module '{path}'. Error: {e}", extra={"org": org_name, "scraper_url": scraper_url, "method": method_name, "path": path, "error": e})
                continue

            # Execute the appropriate scraper function
            try:
                if use_all:
                    # Check if get_all_articles accepts max_pages parameter
                    signature = inspect.signature(scraper.get_all_articles)
                    has_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values())

                    if 'max_pages' in signature.parameters or has_kwargs:
                        articles = await scraper.get_all_articles(max_pages=max_pages)
                    else:
                        articles = await scraper.get_all_articles()
                else:
                    articles = await scraper.get_first_page()
            except Exception as e:
                try:
                    update_scraper_run_fields(last_run_status="error", last_run_count=0)
                except PyMongoError as update_err:
                    logger.exception(
                        f"SCRAPER_FINISH status=failed: DB error updating last_run_status for scraper '{path}' after runtime failure: {update_err}",
                        extra={"org": org_name, "scraper_url": scraper_url, "method": method_name, "path": path, "error": update_err},
                    )
                logger.exception(f"SCRAPER_FINISH status=failed: Error running {method_name} for '{path}': {e}", extra={"org": org_name, "scraper_url": scraper_url, "method": method_name, "path": path, "error": e})
                continue

            # Count for statistics
            attempted_count = 0
            inserted_count = 0
            failed_count = 0

            # Upsert articles into MongoDB
            for ann in articles:
                attempted_count += 1
                ann.setdefault("org", org_name)
                ann.setdefault("last_updated_at", datetime.now(timezone.utc))

                # Convert date field to datetime object if it's a string (only if config has a date field)
                if date_field:
                    date_val = ann.get(date_field)
                    if isinstance(date_val, str):
                        try:
                            parsed_date = parse_date(date_val)
                            if parsed_date > datetime.now():
                                logger.warning(f"INVALID DATE FOR ARTICLE: {ann.get(url_field)}: {date_val}", extra={"org": org_name, "error": "future_date"})
                                ann[date_field] = None
                            else:
                                ann[date_field] = parsed_date
                        except (ParserError, ValueError) as e:
                            logger.warning(f"INVALID DATE FOR ARTICLE: {ann.get(url_field)}: {date_val}: {e}", extra={"org": org_name, "error": "bad_date"})
                            ann[date_field] = None
                    elif isinstance(date_val, datetime):
                        if date_val > datetime.now():
                            logger.warning(f"INVALID DATE FOR ARTICLE: {ann.get(url_field)}: {date_val}", extra={"org": org_name, "error": "future_date"})
                            ann[date_field] = None
                    elif date_val is not None:
                        logger.warning(f"INVALID DATE FOR ARTICLE: {ann.get(url_field)}: {date_val}", extra={"org": org_name, "error": "bad_date"})
                        ann[date_field] = None

                try:
                    # Atomic "insert if not exists" operation
                    result = db[content_col].update_one(
                        {url_field: ann.get(url_field)},  # Query condition
                        {"$setOnInsert": ann},             # Only apply these changes if inserting
                        upsert=True                        # Create if not exists
                    )

                    # Check if document was inserted (not updated)
                    if result.upserted_id:
                        inserted_count += 1
                        logger.info(f"ARTICLE ADDED: {ann.get(url_field)}", extra={"org": org_name, "url": ann.get(url_field)})
                        if date_field and ann.get(date_field):
                            logger.info(
                                f"ART_METRIC PHASE=DATE RESULT=UPDATED SOURCE=INDEX URL=\"{ann.get(url_field)}\"",
                                extra={
                                    "org": org_name,
                                    "article_url": ann.get(url_field),
                                    "article_scraper": path,
                                },
                            )
                    else:
                        logger.info(f"ARTICLE SKIPPED: {ann.get(url_field)}", extra={"org": org_name, "url": ann.get(url_field)})

                except PyMongoError as e:
                    failed_count += 1
                    logger.exception(f"ARTICLE FAILED TO ADD: {ann.get(url_field)}: {e}", extra={"org": org_name, "error": e})

            existing_count = attempted_count - inserted_count - failed_count
            if attempted_count == 0:
                logger.warning(
                    f"SCRAPER_SUMMARY {path} ({scraper_url}): attempted={attempted_count}, inserted={inserted_count}, existing={existing_count}, failed={failed_count}",
                    extra={
                        "org": org_name,
                        "scraper_url": scraper_url,
                        "method": method_name,
                        "path": path,
                        "attempted": attempted_count,
                        "inserted": inserted_count,
                        "existing": existing_count,
                        "failed": failed_count,
                    },
                )
            else:
                logger.info(
                    f"SCRAPER_SUMMARY {path} ({scraper_url}): attempted={attempted_count}, inserted={inserted_count}, existing={existing_count}, failed={failed_count}",
                    extra={
                        "org": org_name,
                        "scraper_url": scraper_url,
                        "method": method_name,
                        "path": path,
                        "attempted": attempted_count,
                        "inserted": inserted_count,
                        "existing": existing_count,
                        "failed": failed_count,
                    },
                )
            
            scraper_status = "unable_to_fetch" if attempted_count == 0 else "pass"

            # Update run fields for the specific scraper
            try:
                update_scraper_run_fields(
                    last_run_status=scraper_status,
                    last_run_count=inserted_count,
                )
                logger.info(
                    f"SCRAPER_FINISH status=success: Successfully processed {inserted_count} articles for '{path}' ({org_name}); last_run_status={scraper_status}",
                    extra={"org": org_name, "scraper_url": scraper_url, "method": method_name, "path": path},
                )
            except PyMongoError as e:
                logger.exception(f"SCRAPER_FINISH status=failed: DB error updating last_run for scraper '{path}': {e}", extra={"org": org_name, "scraper_url": scraper_url, "method": method_name, "error": e})

        # Update last_run timestamp for the org
        try:
            db[scrapers_col].update_one(
                {"_id": org.get("_id")},
                {"$set": {"last_run": datetime.now(timezone.utc)}}
            )
        except PyMongoError as e:
            logger.exception(f"DBERROR: Error updating last_run for org '{org_name}': {e}", extra={"org": org_name, "error": e})


    logger.info("Runner execution complete.")

if __name__ == "__main__":
    asyncio.run(run())
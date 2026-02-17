#!/usr/bin/env python3
"""
Simple seeding script for the `orgs` collection in MongoDB.
Update the `orgs` list below with additional entries as needed.
Each org entry supports an optional `scrapers` list of scraper configs.
"""

import os
import sys
import json
from pathlib import Path
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from utils import setup_logging

# Load env vars and logging
load_dotenv(override=True)
log_file = os.path.join(os.path.dirname(__file__), '..', 'logs', 'db.log')
logger = setup_logging('INFO', log_file)

SCRAPERS_DIR = Path("scrapers")

def normalize_scraper_fields(scraper):
    scraper = dict(scraper)
    scraper.setdefault("active", True)
    scraper.setdefault("manual_force_export", False)
    scraper.setdefault("last_run_status", "error")
    return scraper

def collect_seed_data():
    seed_data = []
    for org_dir in SCRAPERS_DIR.iterdir():
        if not org_dir.is_dir():
            continue
        seed_path = org_dir / "seed.json"
        if seed_path.exists():
            try:
                with open(seed_path, "r") as f:
                    org_seed = json.load(f)
                    seed_data.append(org_seed)
            except Exception as e:
                logger.warning(f"⚠️ Could not read {seed_path}: {e}")
    return seed_data

def main():
    MONGO_URI = os.environ.get("MONGO_URI")
    DB_NAME = os.environ.get("DB_NAME")
    if not MONGO_URI or not DB_NAME:
        logger.error("MONGO_URI and DB_NAME must be set in .env")
        return

    orgs = collect_seed_data()

    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
    except PyMongoError as e:
        logger.error(f"❌ MongoDB connection failed: {e}")
        return

    db = client[DB_NAME]

    for org in orgs:
        if "name" not in org:
            logger.warning(f"⚠️ Skipping org missing 'name': {org}")
            continue

        scrapers = org.get("scrapers", [])
        valid_scrapers = [
            normalize_scraper_fields(s)
            for s in scrapers
            if all(k in s for k in ("name", "path", "url"))
        ]
        org["scrapers"] = valid_scrapers

        try:
            result = db.orgs.update_one(
                {"name": org["name"]},
                {"$set": org},
                upsert=True
            )
            action = "Inserted" if result.upserted_id else "Updated"
            logger.info(f"✅ {action} {org['name']} ({len(valid_scrapers)} scraper(s))")
        except PyMongoError as e:
            logger.error(f"❌ Error upserting '{org['name']}': {e}")

    logger.info("🎉 Seeding complete.")

if __name__ == "__main__":
    main()
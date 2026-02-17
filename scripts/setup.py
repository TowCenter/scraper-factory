#!/usr/bin/env python3
"""
MongoDB setup script for the `org_data` database.
Creates/enforces JSON Schema validation and indexes for:
- articles
- events
- orgs (with optional scrapers array)
"""
import os
from pymongo import MongoClient
from pymongo.errors import OperationFailure

# load environment vars from .env file
from dotenv import load_dotenv
load_dotenv(override=True)

from utils import setup_logging

# Set up logging to logs/db.log
log_file = os.path.join(os.path.dirname(__file__), '..', 'logs', 'db.log')
logger = setup_logging('INFO', log_file)

# --- MongoDB connection setup ---
MONGO_URI = os.environ.get("MONGO_URI")
DB_NAME = os.environ.get("DB_NAME")
if not MONGO_URI or not DB_NAME:
    raise ValueError("MONGO_URI and DB_NAME must be set in the environment variables.")

# Connect to MongoDB
client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
db = client[DB_NAME]

# --- articles collection schema & setup ---
article_schema = {
    "bsonType": "object",
    "required": ["org", "title", "url", "content", "last_updated_at"],
    "properties": {
        "_id":             {"bsonType": "objectId"},
        "org":          {"bsonType": "string"},
        "title":           {"bsonType": "string"},
        "url":             {"bsonType": "string"},
        "date":            {"bsonType": [
          "date",
          "null"
        ]}, # can be null, will be scraped after
        "content":         {"bsonType": "string"},
        "last_updated_at": {"bsonType": "date"}
    },
    "additionalProperties": True
}
try:
    db.create_collection(
        "articles",
        validator={"$jsonSchema": article_schema},
        validationLevel="strict"
    )
except OperationFailure:
    db.command({
        "collMod": "articles",
        "validator": {"$jsonSchema": article_schema},
        "validationLevel": "strict"
    })
# Unique index on url
db.articles.create_index("url", unique=True)

# --- orgs collection schema & setup ---
# 'scrapers' is optional; if present, must be an array of objects
org_schema = {
    "bsonType": "object",
    "required": ["name"],
    "properties": {
        "_id":     {"bsonType": "objectId"},
        "name":    {"bsonType": "string"},
        "scrapers": {
            "bsonType": "array",
            "items": {
                "bsonType": "object",
                "required": ["name", "path", "url"],
                "properties": {
                    "name": {"bsonType": "string"},
                    "path": {"bsonType": "string"},
                    "url":  {"bsonType": "string"}
                },
                "additionalProperties": True
            }
        },
        "last_run": {"bsonType": ["date", "null"]}
    },
    "additionalProperties": True
}
try:
    db.create_collection(
        "orgs",
        validator={"$jsonSchema": org_schema},
        validationLevel="strict"
    )
except OperationFailure:
    db.command({
        "collMod": "orgs",
        "validator": {"$jsonSchema": org_schema},
        "validationLevel": "strict"
    })
# Unique index on name
db.orgs.create_index("name", unique=True)

logger.info("✅ MongoDB schema and indexes ensured.")
# You can now use logger.info, logger.warning, etc. for any logging in this script.
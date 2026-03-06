# Scraper Factory

An AI-powered tool that generates custom Playwright-based web scrapers. Point it at a list of pages, and it analyzes the DOM to write a working Python scraper — then tests and refines it automatically.

Built for journalists and researchers who need to monitor many sources without writing code from scratch each time.

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/TowCenter/scraper-factory?quickstart=1)
---

## Project Structure

```text
scraper-factory/
├── cli.py                        # Entry point — generate, test, register
├── config.json                   # What to scrape and which fields to extract
├── .env                          # API keys and DB connection (never commit this)
│
├── scraper_generator/            # Core library
│   ├── generator.py              # Page analysis, code generation, refinement loop
│   ├── test.py                   # Test framework (validates generated scrapers)
│   ├── utils.py                  # Shared utilities
│   └── prompts/                  # Jinja2 templates for LLM prompts
│
├── scrapers/                     # Generated scrapers live here
│   └── <org_name>/
│       ├── scraper.py            # The generated scraper
│       ├── seed.json             # Registration metadata
│       ├── result.json           # Output from the last run
│       └── page_analysis.json    # Selector candidates from DOM analysis
│
├── scripts/
│   ├── seed.py                   # Seeds MongoDB from seed.json files
│   ├── scrape_indexes.py         # Runs all active scrapers
│   ├── scrape_articles.py        # Fetches full article content
│   └── setup.py                  # DB setup
│
├── example_configs/              # Prebuilt content type configs
│   ├── articles.json
│   ├── faculty_bios.json
│   ├── police_reports.json
│   └── school_board_meetings.json
│
├── streamlit/
│   ├── app.py                    # Dashboard for viewing scraped data
│   └── requirements.txt
│
├── requirements.txt
└── Dockerfile
```

---

## Installation

```bash
git clone https://github.com/yourusername/scraper-factory.git
cd scraper-factory
pip install -r requirements.txt
playwright install chromium
```

---

## Step 1: Set Up Your `.env`

Create a `.env` file in the project root. This file is required before you can do anything.

```bash
OPENAI_API_KEY=sk-your-key-here
MONGO_URI=mongodb://localhost:27017
DB_NAME=scraper_data
```

> **MongoDB options:** Use a local MongoDB instance (`mongodb://localhost:27017`) or a hosted cluster like MongoDB Atlas or DigitalOcean Managed MongoDB. The URI goes in `MONGO_URI`.

---

## Step 2: Configure What to Scrape

`config.json` at the project root controls what kind of content to scrape and which fields to extract. Every scraper you generate will follow this schema.

```json
{
  "content_type": "articles",
  "description": "News articles from an organization",
  "item_label": "article",
  "fields": [
    { "name": "title", "description": "Headline or title", "required": true,  "type": "text" },
    { "name": "date",  "description": "Publication date",  "required": false, "type": "date" },
    { "name": "url",   "description": "Link to the article", "required": true, "type": "url" }
  ]
}
```

`content_type` also becomes the MongoDB collection name (e.g., `articles` → data stored in `articles`, scrapers registered in `articles_scrapers`).

**Field types:**

- `"text"` — plain string
- `"date"` — validated as YYYY-MM-DD
- `"url"` — validated as a URL

**Prebuilt configs** are in `example_configs/` — copy one to `config.json` to use it:

```bash
cp example_configs/police_reports.json config.json
```

| Config                       | Fields                          |
| ---------------------------- | ------------------------------- |
| `articles.json`              | title, date, url                |
| `police_reports.json`        | title, date, url, incident_type |
| `school_board_meetings.json` | title, date, url, agenda_url    |
| `faculty_bios.json`          | name, position, department, url |

You can also write your own. Any fields you define here will be passed to the AI and validated on the scraped output.

---

## Step 3: Generate Scrapers

### Single scraper

```bash
python cli.py generate --org "Los Angeles Times" --url "https://www.latimes.com/"
```

> **GitHub Codespaces:** The generator opens a real browser window during refinement. In Codespaces there's no display, so prefix the command with `xvfb-run` to provide a virtual one:
>
> ```bash
> xvfb-run python cli.py generate --org "Los Angeles Times" --url "https://www.latimes.com/"
> ```

Or run without arguments for an interactive prompt:

```bash
python cli.py generate
```

To use a different content config than `config.json`:

```bash
python cli.py generate --org "LAPD" --url "https://lapd.com/reports" --config example_configs/police_reports.json
```

The tool will:

1. Load the page in a headless browser and take a screenshot
2. Send the DOM + screenshot to GPT-4o to identify CSS selectors
3. Generate a complete Playwright scraper based on those selectors
4. Test it — if it fails or returns zero results, it refines automatically
5. Save the scraper to `scrapers/<org_name>/`

### Batch generation

Generate many scrapers at once from a JSON or CSV file.

**JSON format (one entry per URL):**

```json
[
  { "org": "Chicago Tribune", "url": "https://chicagotribune.com/news/" },
  { "org": "LA Times",        "url": "https://www.latimes.com/local" },
  { "org": "Boston Globe",    "url": "https://www.bostonglobe.com/metro" }
]
```

**JSON format (multiple URLs per org):**

```json
[
  {
    "org": "LA Times",
    "urls": [
      "https://www.latimes.com/california",
      "https://www.latimes.com/entertainment-arts",
      "https://www.latimes.com/sports"
    ]
  }
]
```

**CSV format:**

```csv
org,url
Chicago Tribune,https://chicagotribune.com/news/
LA Times,https://www.latimes.com/local
```

Run it:

```bash
python cli.py generate --batch-file batch/local_papers.json
```

Each scraper is generated, tested, and registered independently. Failed ones are logged but won't stop the rest.

---

## Step 4: Set Up the Database

Once you have scrapers generated, seed them into MongoDB so the scraping scripts know what to run.

```bash
python scripts/seed.py
```

This walks through every `scrapers/<org>/seed.json` file and upserts each org into the `{content_type}_scrapers` collection in MongoDB. Each scraper entry gets default metadata:

- `active: true`
- `last_run_status: "error"` (updated when it runs)
- `last_run` and `last_run_count` timestamps

Run this again any time you add new scrapers.

If you need to set up the DB schema or indexes first:

```bash
python scripts/setup.py
```

---

## Step 5: Start Scraping

### Run all active scrapers (only first page)

```bash
python scripts/scrape_indexes.py
```

This reads the `{content_type}_scrapers` collection, runs every scraper marked `active: true`, and writes results into MongoDB (`{content_type}` collection). Each scraper also updates its `last_run`, `last_run_status`, and `last_run_count` metadata.

### Run a single scraper manually

```bash
cd scrapers/los_angeles_times
python scraper.py
```

This writes results to `result.json` in the same directory. Useful for testing a scraper in isolation.

### Fetch full article content (if needed)

```bash
python scripts/scrape_articles.py
```

This is for going deeper than the index page — fetching body content from individual article URLs stored in MongoDB.

---

## View the Dashboard

A Streamlit app lets you browse scraped data, filter by organization, and export to CSV.

```bash
cd streamlit
pip install -r requirements.txt
streamlit run app.py
```

It reads from the same MongoDB collections, using `config.json` to know which collection to query. The `.env` file must be present with valid `MONGO_URI` and `DB_NAME` values. The site can also be easily deployed to production. 

---

## Testing Scrapers

```bash
# Test a single scraper
python cli.py test --path scrapers/los_angeles_times/scraper.py

# Test all scrapers for an org
python cli.py test --org "Los Angeles Times"
```

Generating scripts will automatically run the testing suite, but you can run tests again for changes to the scripts. 

Tests are dynamic based on your `config.json`:

- Checks that scraped items have the expected fields
- Validates `date` fields as YYYY-MM-DD
- Validates `url` fields as valid URLs
- Enforces non-blank values for required fields

---

## Logs

```text
logs/
  generate.log              # Generation process
  test.log                  # Test results
  <scraper_name>_llm.log    # Full LLM prompts and responses (useful for debugging)
```

---

## Notes on Cost and Ethics

- **API cost:** Each scraper generation typically costs $0.01–0.10 in OpenAI API fees. Running scrapers after generation is free.
- **robots.txt:** The tool checks `robots.txt` before generating a scraper. If scraping is disallowed, you'll be warned.
- **Rate limiting:** Don't run scrapers more frequently than necessary. Respect the sites you're scraping.
- **Terms of service:** Check each site's ToS before deploying scrapers against it.

---

## Docker

Skip local setup and run via Docker:

```bash
# Single scraper
docker run -it --init --rm \
  -v "$PWD/scrapers:/app/scrapers" \
  -v "$PWD/logs:/app/logs" \
  -e OPENAI_API_KEY=sk-... \
  towcenter/scraper-factory:latest \
  generate --org "LA Times" --url "https://www.latimes.com/"

# Batch
docker run -it --init --rm \
  -v "$PWD/scrapers:/app/scrapers" \
  -v "$PWD/logs:/app/logs" \
  -v "$PWD/batch:/app/batch" \
  -e OPENAI_API_KEY=sk-... \
  towcenter/scraper-factory:latest \
  generate --batch-file batch/example.json
```

---

**Maintained by:** Tow Center for Digital Journalism
**Last Updated:** February 2026

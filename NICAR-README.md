# Scraper Factory

Let's try out Scraper Factory and make a new scraper!

Today, we'll be working in the command line to dynamically generate and test a new Python scraper. 

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

If you've opened in GitHub codespaces, no installation should be necessary. 

---

## Step 1: Set Up Your `.env`

Find the `.env` file in the project root. This file is required before you can do anything.

For our purposes, you should only need to input a key for OpenAI. Other keys relate to the database setup included in the repo. 

```bash
OPENAI_API_KEY="sk-your-key-here"
MONGO_URI=""
DB_NAME=""
```

---

## Step 2: Configure What to Scrape

Take a look at the site and decide how you'd like to configure the scrapers. 

[https://towcenter.github.io/2026_NICAR/
](https://towcenter.github.io/2026_NICAR/
)

`config.json` at the project root controls what kind of content to scrape and which fields to extract. Every scraper you generate will follow this schema.

```json
{
  "content_type": "sessions",
  "description": "Sessions from a conference",
  "item_label": "session",
  "fields": [
    { "name": "title", "description": "Title of session", "required": true,  "type": "text" },
    { "name": "date",  "description": "Date",  "required": true, "type": "date" },
    { "name": "ADD MORE HERE!",   "description": "Descrption", "required": true, "type": "url" }
  ]
}
```

**Field types:**

- `"text"` — plain string
- `"date"` — validated as YYYY-MM-DD
- `"url"` — validated as a URL
- `"list"` — validated as a list of objects

---

## Step 3: Generate a Scraper

### Single scraper

> **GitHub Codespaces:** The generator opens a real browser window during refinement. In Codespaces there's no display, so prefix the command with `xvfb-run` to provide a virtual one:

```bash
xvfb-run python cli.py generate --org "NICAR" --url "https://towcenter.github.io/2026_NICAR/"
```

Or run without arguments for an interactive prompt:

```bash
xvfb-run python cli.py generate
```

The tool will:

1. Load the page in a headless browser and take a screenshot
2. Send the DOM + screenshot to GPT-4o to identify CSS selectors
3. Generate a complete Playwright scraper based on those selectors
4. Test it — if it fails or returns zero results, it refines automatically
5. Save the scraper to `scrapers/<org_name>/`


## Step 4: Confirm results

Check the **nicar** folder within **scrapers**. **results.json** should have the scraped contents, and **scraper.py** will show the actual code. 

# Scraper Factory

## What is Scraper Factory?

Scraper Factory is an AI-powered tool that automatically creates custom web scrapers for news sites, press release pages, and article archives. Instead of manually copying and pasting articles from websites or struggling with complex code, this tool builds a specialized scraper for each site that can automatically collect article titles, dates, and URLs.

**In simple terms:** Tell the tool which website you want to monitor, and it will create a program that automatically collects all the articles from that site for you.

## Who should use this?

This tool is designed for journalists, researchers, and media professionals who need to:

- Monitor multiple news sources regularly
- Track press releases from organizations
- Build article archives for research
- Collect data from news sites without APIs
- Stay on top of breaking stories across many sources

**No programming experience required** for basic use, though you'll need to be comfortable using a command line terminal. Preferably, you have some level of python experience as well.

## Why use scraper factory?

### Traditional Challenges

Manually collecting articles from news sites is:
- **Time-consuming**: Copy-pasting dozens of articles takes hours
- **Error-prone**: It's easy to miss articles or copy incorrect information
- **Repetitive**: You have to do the same thing over and over
- **Time**:  Journalists literally do not have time for this. We are busy and manual bulk data collection is far too tedious

### The scraper factory solution

With this tool:
- **One-time setup**: Generate a scraper once, use it forever. This limits our reliance on AI and how much of our own data it pulls. 
- **Automatic collection**: The scraper does all the work for you
- **Consistent results**: Never miss an article or get wrong data
- **Scalable**: Monitor hundreds of sites as easily as one

## How it works (non-technical)

Think of Scraper Factory as a smart assistant that learns how to read a specific website:

1. **You provide a URL**: Give the tool the address of an articles page (like a news site's homepage or a press release archive)

2. **AI analyzes the page**: The tool loads the page in a browser, takes a screenshot, and uses AI to understand the page structure:
   - Where are the article titles?
   - Where are the publication dates?
   - Where are the article links?
   - How do you get to the next page? Is there a pagination button? 

3. **Code is generated**: The AI writes a custom Python script specifically designed for that website

4. **Testing happens automatically**: The tool tests the scraper to make sure it works, and if it doesn't, it fixes itself

5. **You get a working scraper**: A Python file is saved that you can run anytime to collect articles from that site

## What You'll Need

### Required

1. **Python 3.8 or newer** - The programming language that runs the tool
2. **OpenAI API Key** - Powers the AI that analyzes pages and writes code
   - You can get one at [platform.openai.com](https://platform.openai.com/)
   - Note: Using the API costs money, but it's typically very cheap (a few cents per scraper generated)
3. **Internet connection** - To access websites and the OpenAI API
4. **A computer** - Mac, Windows, or Linux all work. Scraper Factory was created using Mac computers

### Technical skills

- **Basic**: Comfortable opening a terminal and typing commands
- **Intermediate**: Can install Python packages and set environment variables
- **No coding required**: You don't need to write any code yourself

## Installation guide

### Step 1: Install Python

If you don't have Python installed:

- **Mac**: Python usually comes pre-installed. Open Terminal and type `python3 --version` to check
- **Windows**: Download from [python.org](https://www.python.org/downloads/)
- **Linux**: Usually pre-installed, or install via your package manager

### Step 2: Download/clone scraper factory

```bash
# Clone the repository (or download the ZIP file from GitHub)
git clone https://github.com/yourusername/scraper-factory.git
cd scraper-factory
```

### Step 3: Install required libraries

```bash
# Install all the Python packages the tool needs
pip install -r requirements.txt

# Install Playwright browsers (used for loading web pages)
playwright install chromium
```

### Step 4: Set Up Your API Key

Create a file named `.env` in the scraper-factory folder:

```bash
# Create the .env file
touch .env
```

Open the `.env` file in a text editor and add:

```
OPENAI_API_KEY=your_api_key_here
```

Replace `your_api_key_here` with your actual OpenAI API key.

## How to Use Scraper Factory

### Basic usage: generate a scraper

The simplest way to create a scraper:

```bash
python cli.py generate
```

You'll be prompted to enter:
1. **Organization name**: A name for the site (e.g., "Los Angeles Times" or "White House Press")
2. **URL**: The web address of the articles page

**Example:**
```
Enter the name of the org: Los Angeles Times
Enter the URL of the articles page: https://www.latimes.com/
```

The tool will:
- Analyze the page structure
- Generate scraper code
- Test the scraper
- Save it to a folder named after the organization

### Advanced usage: more options

```bash
# Generate with custom options
python cli.py generate \
  --org "Los Angeles Times" \
  --url "https://www.latimes.com/" \
  --filename "latimes_scraper.py" \
  --verbose
```

**Options:**
- `--org` or `-s`: Organization name
- `--url` or `-u`: URL of the articles page
- `--filename` or `-f`: Custom filename for the scraper (default: scraper.py)
- `--model` or `-m`: AI model to use (default: uses your .env setting, or openai/gpt-4o-mini if no .env setting is found)
- `--verbose` or `-v`: Show detailed output during generation
- `--batch-file` or `-b`: JSON or CSV list of org/url entries for batch generation

### Batch generation

Generate multiple scrapers at once using a list file:

```bash
python cli.py generate --batch-file batch_list.json
```

Supported formats:
- JSON array, one object per URL:
```json
[
   {
      "org": "Los Angeles Times", 
      "url": "https://www.latimes.com"
   }, 
   {
      "org": "BBC", 
      "url": "https://www.bbc.com/news"
   },
   {
      "org": "BBC",
      "url": "https://www.bbc.com/business"
   }
]
```
- JSON array, one object with multiple `urls` for an org:
```json
[
   {
      "org": "Los Angeles Times", 
      "urls": [
         "https://www.latimes.com",
         "https://www.latimes.com/california",
         "https://www.latimes.com/entertainment-arts",
         "https://www.latimes.com/sports"
      ]
   }
]
```
- CSV with columns `org` and `url`; optional `filename`, `template`, `model`, `verbose`
```csv
org,url
Los Angeles Times,https://www.latimes.com
BBC,https://www.bbc.com/news
BBC,https://www.bbc.com/business
```

If you run `python cli.py generate` with no args, you'll be asked whether to use a list file or enter a single org/URL.

### Testing a scraper

After generating a scraper, test it to make sure it works:

```bash
# Test a specific scraper file
python cli.py test --path scrapers/los_angeles_times/scraper.py

# Test all scrapers for an organization
python cli.py test --org "Los Angeles Times"
```

### Registering a scraper

Scrapers are automatically registered when generated, but you can manually register them:

```bash
python cli.py register \
  --name "Los Angeles Times" \
  --url "https://www.latimes.com/" \
  --filename "scraper.py"
```

## Run with Docker

You can skip local Python/Playwright setup by using Docker (replace `OPENAI_API_KEY` with your real key):

- Prerequisite: install Docker (Docker Desktop on macOS/Windows or Docker Engine on Linux) – https://docs.docker.com/get-docker/
- Generate interactively:
  ```bash
  docker run -it --init --rm \
    -v "$PWD/scrapers:/app/scrapers" \
    -v "$PWD/logs:/app/logs" \
    -e OPENAI_API_KEY=sk-... \
    towcenter/scraper-factory:latest \
    generate
  ```
- Generate non-interactively:
  ```bash
  docker run -it --init --rm \
    -v "$PWD/scrapers:/app/scrapers" \
    -v "$PWD/logs:/app/logs" \
    -e OPENAI_API_KEY=sk-... \
    towcenter/scraper-factory:latest \
    generate --org "Los Angeles Times" --url "https://www.latimes.com/"
  ```
- Generate in batch mode:
  ```bash
  docker run -it --init --rm \
    -v "$PWD/scrapers:/app/scrapers" \
    -v "$PWD/logs:/app/logs" \
    -v "$PWD/batch:/app/batch" \
    -e OPENAI_API_KEY=sk-... \
    towcenter/scraper-factory:latest \
    generate --batch-file batch/example.json
  ```
- Test an existing scraper:
  ```bash
  docker run -it --init --rm \
    -v "$PWD/scrapers:/app/scrapers" \
    -v "$PWD/logs:/app/logs" \
    -e OPENAI_API_KEY=sk-... \
    towcenter/scraper-factory:latest \
    test --path scrapers/los_angeles_times/scraper.py
  ```

Optional: build locally (if you want to customize or work offline):
- From repo root: `docker build -t scraper-factory .`
- Then replace `towcenter/scraper-factory:latest` in the commands above with `scraper-factory`.


## Understanding the output

### Generated files

When you generate a scraper for "Example News", you'll get:

```
scrapers/
  example_news/
    scraper.py          # The scraper code
    results.json        # Article data (created when you run the scraper)
    seed.json           # Registration data
```

### Running a scraper

To actually collect articles, run the generated scraper:

```bash
cd scrapers/example_news
python scraper.py
```

This creates a `results.json` file with all the articles:

```json
[
  {
    "title": "Breaking: Major Story Headline",
    "date": "2025-12-02",
    "url": "https://example.com/article-1"
  },
  {
    "title": "Another Important Story",
    "date": "2025-12-01",
    "url": "https://example.com/article-2"
  }
]
```

### Using the data

You can:
- Open `results.json` in any text editor
- Import it into Excel or Google Sheets
- Use it in other programs or scripts
- Build a database of articles over time

## Real-World Examples

### Example 1: Monitoring government press releases

```bash
python cli.py generate \
  --org "White House Briefings" \
  --url "https://www.whitehouse.gov/briefing-room/"
```

Run the scraper daily to track all new press releases automatically.

### Example 2: Tracking multiple local papers

Generate scrapers for multiple local news sites with one batch file:

1) Save this to `batch/local_papers.json`:
```json
[
  {"org": "Chicago Tribune", "url": "https://chicagotribune.com/news/"},
  {"org": "LA Times", "url": "https://www.latimes.com/local"},
  {"org": "Boston Globe", "url": "https://www.bostonglobe.com/metro"}
]
```

2) Generate all scrapers at once:
```bash
python cli.py generate --batch-file batch/local_papers.json
```

### Example 3: Research project archive

Generate scrapers for academic journals or research institutions:

```bash
python cli.py generate \
  --org "MIT News" \
  --url "https://news.mit.edu/topic/artificial-intelligence"
```

## Troubleshooting

### Problem: "OPENAI_API_KEY environment variable is not set"

**Solution:** Make sure you've created a `.env` file with your API key:
```
OPENAI_API_KEY=sk-your-key-here
```

### Problem: Scraper finds 0 articles

**Causes:**
- The website uses heavy JavaScript that loads content dynamically
- The selectors chosen by the AI don't match the actual page structure
- The page requires login or has blocking mechanisms

**Solutions:**
- The tool automatically tries to fix this by setting `headless=False` (opens a visible browser)
- Try running the scraper again manually
- Check if the website requires login
- Look at the generated code and adjust selectors if needed

### Problem: "module not found" errors

**Solution:** Make sure you installed all dependencies:
```bash
pip install -r requirements.txt
playwright install chromium
```

### Problem: Scraper worked before but now fails

**Causes:**
- The website changed its design or structure
- The website added anti-scraping measures

**Solutions:**
- Re-generate the scraper with the same URL
- Choose to overwrite the old scraper when prompted

### Problem: Scraper runs very slowly

**Causes:**
- Website has many pages to load
- Website loads slowly
- Browser automation adds overhead

**Solutions:**
- This is normal for large sites
- Consider adding pagination limits in the generated code
- Run scrapers during off-peak hours

## Best practices

### 1. Respect websites

- **Check terms of service**: Make sure the website allows scraping
- **Don't overload servers**: Run scrapers during reasonable hours
- **Cache results**: Don't scrape the same data repeatedly
- **Use official APIs when available**: Scrapers should be a last resort

### 2. Maintain your scrapers

- **Test regularly**: Websites change, so test your scrapers periodically
- **Update when needed**: Re-generate scrapers if websites redesign
- **Keep backups**: Save your `.env` file and scraper configurations

### 3. Organize your data

- **Use consistent naming**: Name organizations clearly
- **Archive results**: Save historical `results.json` files with dates
- **Document your sources**: Keep notes about which scrapers cover which topics

### 4. Cost management

- **API costs**: Each scraper generation costs a few cents in API fees
- **Monitor usage**: Check your OpenAI account regularly
- **Reuse scrapers**: Generate once, run many times

## Technical Details (Optional)

For those interested in how it works under the hood:

### Architecture

1. **DOM analysis** ([generator.py](scraper_generator/generator.py:122-363))
   - Uses Playwright to load the target page in a headless browser
   - Captures a full-page screenshot
   - Condenses the HTML DOM into a simplified, indexed format
   - Chunks the DOM and sends each chunk + screenshot to GPT-4o
   - AI identifies CSS selectors for articles, titles, dates, URLs, and pagination

2. **Scraper generation** ([generator.py](scraper_generator/generator.py:405-428))
   - Uses Jinja2 templates to create prompts
   - Sends found selectors and HTML examples to GPT-4o
   - AI writes a complete Playwright-based scraper script
   - Includes error handling and navigation logic

3. **Testing & refinement** ([generator.py](scraper_generator/generator.py:430-624))
   - Automatically runs the generated scraper in a test environment
   - Captures any errors or issues (including zero results)
   - If failed, sends error feedback back to the AI for one refinement attempt
   - Automatically adjusts `headless` settings if needed

4. **CLI interface** ([cli.py](cli.py))
   - Provides `generate`, `test`, and `register` commands
   - Interactive prompts for missing parameters
   - Handles file organization and logging

### Key things

- **Playwright**: Browser automation for JavaScript-heavy sites
- **OpenAI GPT-4o**: Vision and code generation capabilities
- **BeautifulSoup**: HTML parsing and selector testing
- **Jinja2**: Template engine for prompts and code
- **Python asyncio**: Asynchronous operations for performance

### Logging

All AI interactions are logged to help with debugging:
- `logs/generate.log` - Generation process logs
- `logs/test.log` - Testing process logs
- `logs/<scraper_name>_llm.log` - All LLM prompts and responses

### Configuration

Environment variables (set in `.env`):

```bash
# Required
OPENAI_API_KEY=your_key_here

# Required for scripts (scrape_indexes.py, scrape_articles.py, seed.py, setup.py)
MONGO_URI=mongodb://localhost:27017       # MongoDB connection string
DB_NAME=org_data                          # MongoDB database name

# Optional (with defaults)
LLM_MODEL=openai/gpt-4o-mini
SCRAPER_OUTPUT_DIR=scrapers
REQUEST_TIMEOUT=60
MAX_RETRIES=3
RETRY_DELAY=5
USE_HEADLESS=True
USE_VERBOSE=True
LOG_LEVEL=INFO
```

### Extending the Tool

You can customize the scraper generation by:

1. **Modifying templates**: Edit files in `scraper_generator/prompts/`
   - `dom_analysis_prompt.jinja2` - How the AI analyzes pages
   - `scraper_generation_prompt.jinja2` - How the AI writes code
   - `generic_template.jinja2` - The base scraper structure

2. **Adjusting configuration**: Edit [config.py](scraper_generator/config.py)

3. **Adding custom logic**: Extend [generator.py](scraper_generator/generator.py)

## Frequently Asked Questions

### Q: Is this legal?

**A:** Web scraping legality depends on:
- The website's Terms of Service
- Whether you have permission
- What you do with the data
- Local laws in your jurisdiction

Always check a website's `robots.txt` file and Terms of Service before scraping. For journalistic research, consult your legal department.

### Q: Will this work on any website?

**A:** It works best on:
- News sites with article lists
- Press release archives
- Blog homepages
- Content management systems

It may struggle with:
- Single-page applications with complex JavaScript
- Sites with heavy anti-bot protection
- Sites requiring login
- Sites with CAPTCHAs

### Q: How much does it cost?

**A:** Costs per scraper generation:
- **OpenAI API**: $0.01-0.10 per scraper (GPT-4o costs)
- **Running scrapers**: Free (just electricity)
- **Your time**: 2-5 minutes per scraper

### Q: Can I use this for commercial purposes?

**A:** The tool itself is open source, but:
- Respect website Terms of Service
- OpenAI API has its own terms
- Consider the ethics of data collection
- Consult legal counsel for commercial use

### Q: What if I need help?

**A:** Resources:
- Check this README first
- Look at the code comments in generated scrapers
- Review log files in the `logs/` folder
- Open an issue on GitHub
- Consult with technical colleagues

### Q: Can I modify generated scrapers?

**A:** Yes! The generated Python scripts are fully editable:
- Add custom filtering logic
- Change output formats
- Adjust pagination limits
- Add error notifications
- Integrate with databases

### Q: How often should I run scrapers?

**A:** It depends on your needs
- **Breaking news**: Every 15-30 minutes
- **Daily news**: Once per day
- **Weekly publications**: Once per week
- **Archives**: Once, then as needed

Consider server load and be respectful.

## Ethical Considerations

### Responsible Scraping

As a journalist or researcher using this tool:

1. **Verify data**: Scraped data may contain errors- ALWAYS fact check your data 
2. **Attribute sources**: Always credit the original publisher
3. **Consider impact**: Keep in mind- excessive scraping can harm small sites
4. **Seek permission**: Contact sites directly when possible to see if they can give you the data 
5. **Use APIs first**: Prefer official APIs when available

### When Not to Use Scrapers

Don't use this tool to:
- Steal paywalled content
- Bypass authentication
- Harvest personal information
- Violate Terms of Service
- Compete with the original site
- Overwhelm servers with requests
- if the robots.txt says you cannot scrape, then 

### Journalism Ethics

Remember:
- Scrapers are research tools, not primary sources
- Verify information through traditional reporting 
- Use the scraper factory ethically, but  your organization's data policies
- Be transparent about your methods in your methedology when publishing stories

## Support & Contributing

### Getting Help

- **Documentation**: You're reading it! Congrats! 
- **Issues, questions, etc**:  email ___ 
- **Updates**: Watch the repository for new releases


## Acknowledgments

This tool uses:
- AI for production
- Playwright for browser automation
- BeautifulSoup for HTML parsing
- Various open-source Python libraries

Built for journalists, by journalists.

---

**Last Updated**: December 2, 2025
**Maintained by**: [NAME HERE]

"""
Prompt generation function for creating scrapers with ScrapegraphAI.
"""
from datetime import datetime
from jinja2 import Environment, FileSystemLoader
import os
from .config import LLM_MODEL

def make_prompt(url, org_name, scraper_name, template_name="generic_template.jinja2"):
    """
    Generate a prompt for the LLM to implement a scraper.
    
    Args:
        url (str): Target URL for the scraper
        org_name (str): Name of the organization
        scraper_name (str): Name of the scraper file without extension (e.g., 'scraper', 'scraper2')
        template_name (str): Name of the template file to use (default: "generic_template.jinja2")
        
    Returns:
        str: Formatted prompt for the LLM
    """
    # Set up Jinja environment
    template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
    env = Environment(loader=FileSystemLoader(template_dir))
    
    # Construct the module path
    from utils import sanitize_filename
    org_folder = sanitize_filename(org_name)
    module_path = f"scrapers.{org_folder}.{scraper_name}"
    
    # Load the template
    template = env.get_template(template_name)
    
    # Render the template with our variables
    rendered_template = template.render(
        org_name=org_name,
        url=url,
        generated_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        model=LLM_MODEL,  # Use the model from config
        module_path=module_path  # Pass the module path to the template
    )

    return f"""
You are implementing a web scraper for a {org_name} articles page. I'm providing you with a template that already has the basic structure for a Playwright-based web scraper.

Here is the template code:

```python
{rendered_template}
```

Your task is to implement the TODO sections in the template code above to extract articles from the website.
Specifically:

1. Implement the selector logic inside scrape_page() to extract article titles, dates, and URLs.
2. Implement the get_next_page() function to handle pagination if it exists on the site.
3. Make sure to handle date formatting consistently (YYYY-MM-DD).
4. Return structured data as a list of dictionaries with keys: title, date, url.
5. This is an async scraper using Playwright's async API. Make sure to use 'await' for ALL async methods, including element.get_attribute(), element.inner_text(), and similar methods. Forgetting to await these calls will cause runtime errors.

Return only the complete, ready-to-use Python script with your implementations.
"""
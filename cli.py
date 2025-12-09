#!/usr/bin/env python
"""
Command Line Interface for the Articles Scraper Generator.
"""

import os
import sys
import json
import csv
import argparse
import inquirer
import logging
import traceback
import subprocess
from dotenv import load_dotenv
from pathlib import Path
from urllib.parse import urlparse


# Add the parent directory to the path so we can import the scraper_generator module
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from scraper_generator import generate_scraper
from scraper_generator.utils import (
    save_scraper,
    setup_logging, 
    sanitize_filename, 
    check_org_scrapers_seed, 
    get_scraper_metadata
)
from scraper_generator.generator import (
    get_robots_txt,
    get_allowed_scraper_companies,
    SCRAPER_GROUPS,
)
from scraper_generator.config import LOG_LEVEL, LOG_FILE, SCRAPER_OUTPUT_DIR
from scraper_generator.test import run_tests  # Import the test module

# Load environment variables
load_dotenv()

# Set up logging in case it's not configured (outside generate or test)
logger = setup_logging(LOG_LEVEL, LOG_FILE)

def find_next_scraper_filename(org_name):
    """
    Find the next available scraper filename for a org.
    
    Args:
        org_name (str): Name of the org
        
    Returns:
        str: The next available scraper filename (e.g., "scraper2.py")
    """
    folder_name = sanitize_filename(org_name)
    org_dir = os.path.join(SCRAPER_OUTPUT_DIR, folder_name)
    
    # Create directory if it doesn't exist
    if not os.path.exists(org_dir):
        os.makedirs(org_dir)
    
    # Find the next available scraper filename
    scraper_files = [f for f in os.listdir(org_dir) 
                    if f.startswith("scraper") and f.endswith(".py")]

    if scraper_files:
        # Extract numbers from existing scrapers (scraper.py -> 1, scraper2.py -> 2, etc.)
        existing_numbers = []
        for sf in scraper_files:
            if sf == "scraper.py":
                existing_numbers.append(1)
            else:
                num_str = sf.replace("scraper", "").replace(".py", "")
                if num_str.isdigit():
                    existing_numbers.append(int(num_str))
        
        # Find the next available number
        next_number = 1
        while next_number in existing_numbers:
            next_number += 1
        
        # Set the new scraper filename
        if next_number == 1:
            output_filename = "scraper.py"
        else:
            output_filename = f"scraper{next_number}.py"
    else:
        output_filename = "scraper.py"
    
    return output_filename


def load_batch_file(batch_path):
    """
    Load batch generation entries from a JSON or CSV file.
    Expected fields: org (or name) and url. Optional: filename, template, model, verbose.
    """
    if not batch_path:
        return []

    if not os.path.exists(batch_path):
        print(f"❌ Batch file not found: {batch_path}")
        return []

    entries = []
    _, ext = os.path.splitext(batch_path.lower())

    try:
        if ext in [".json"]:
            with open(batch_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Support object with items, or single object with org/name and urls list
            if isinstance(data, dict) and "items" in data:
                data = data["items"]
            elif isinstance(data, dict) and (data.get("org") or data.get("name")) and isinstance(data.get("urls"), list):
                data = [
                    {"org": data.get("org") or data.get("name"), "url": url}
                    for url in data.get("urls")
                ]

            if not isinstance(data, list):
                print("❌ Batch JSON must be an array (or an object with an 'items' array) of objects, or a single object with org/name and urls list.")
                return []
            source = data
        elif ext in [".csv"]:
            with open(batch_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                source = list(reader)
        else:
            print("❌ Batch file must be .json or .csv")
            return []
    except Exception as e:
        print(f"❌ Could not read batch file: {e}")
        return []

    for idx, item in enumerate(source, 1):
        if not isinstance(item, dict):
            print(f"⚠️ Skipping entry #{idx}: expected an object/row.")
            continue

        org = item.get("org") or item.get("name")
        url = item.get("url")
        urls_list = item.get("urls") if isinstance(item.get("urls"), list) else None

        # Expand multiple URLs for one org in a single row/object
        if urls_list and not url:
            for url_entry in urls_list:
                entries.append(
                    {
                        "org": org,
                        "url": url_entry,
                        "filename": item.get("filename"),
                        "template": item.get("template"),
                        "model": item.get("model"),
                        "verbose": item.get("verbose"),
                    }
                )
            continue

        if not org or not url:
            print(f"⚠️ Skipping entry #{idx}: 'org' and 'url' are required.")
            continue

        entries.append(
            {
                "org": org,
                "url": url,
                "filename": item.get("filename"),
                "template": item.get("template"),
                "model": item.get("model"),
                "verbose": item.get("verbose"),
            }
        )

    return entries

def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description='Generate article scrapers using ScrapegraphAI'
    )

    # Command subparsers
    subparsers = parser.add_subparsers(dest='command', help='Command to run')
    subparsers.required = True

    # Generate command
    generate_parser = subparsers.add_parser('generate', help='Generate a scraper for a org')
    generate_parser.add_argument('--url', '-u', help='URL of the articles page')
    generate_parser.add_argument('--org', '-s', help='Name of the org')
    generate_parser.add_argument('--filename', '-f', default='scraper.py', help='Custom filename for the scraper (default: scraper.py)')
    generate_parser.add_argument('--model', '-m', help='LLM model to use (default: from .env)')
    generate_parser.add_argument('--template', '-t', help='Template file name to use (default: generic_template.jinja2)')
    generate_parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose output')
    generate_parser.add_argument('--batch-file', '-b', help='Path to JSON or CSV list of org/url entries for batch generation')

    # Test command
    test_parser = subparsers.add_parser('test', help='Test a generated scraper')
    test_parser.add_argument('--path', '-p', help='Path to the scraper file')
    test_parser.add_argument('--org', '-s', help='org name to test all scrapers for that org')
    test_parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose output')

    # Register command
    register_parser = subparsers.add_parser('register', help='Register a org in the database')
    register_parser.add_argument('--name', '-n', help='Name of the org (e.g., Harvard University)')
    register_parser.add_argument('--url', help='URL for the articles page')
    register_parser.add_argument('--filename', '-f', default='scraper.py', help='Filename of the scraper (default: scraper.py)')
    register_parser.add_argument('--run-seed', action='store_true', help='Run db/seed.py after updating seed_data.json')

    args = parser.parse_args()

    # Interactive prompts for missing arguments
    if args.command == 'generate':
        if not args.batch_file and not args.org and not args.url:
            batch_q = [
                inquirer.Confirm(
                    "use_batch",
                    message="Generate multiple scrapers from a list file?",
                    default=False,
                )
            ]
            batch_answer = inquirer.prompt(batch_q)
            if batch_answer and batch_answer.get("use_batch"):
                batch_path_q = [
                    inquirer.Text(
                        "batch_file",
                        message="Path to the list file (JSON or CSV)",
                    )
                ]
                batch_path_answer = inquirer.prompt(batch_path_q)
                args.batch_file = batch_path_answer.get("batch_file") if batch_path_answer else None

        if not args.batch_file:
            if not args.org:
                args.org = prompt_org_name('Enter the name of the org: ')
            if not args.url:
                args.url = input('Enter the URL of the articles page: ')
    elif args.command == 'test':
        if not args.path and not args.org:
            questions = [
                inquirer.List('option',
                              message="What would you like to test?",
                              choices=['A specific scraper', 'All scrapers for a org'],
                              default='A specific scraper'
                             ),
            ]
            answers = inquirer.prompt(questions)
            
            if answers['option'] == 'A specific scraper':
                args.path = input('Enter the path to the scraper file: ')
            else:
                args.org = prompt_org_name('Enter the name of the org: ')
    elif args.command == 'register':
        if not args.name:
            args.name = prompt_org_name('Enter the name of the org (e.g., Harvard University): ')
        if not args.url:
            args.url = input('Enter the URL for the articles page: ')

    return args

def prompt_org_name(prompt_text):
    """Prompt for a org name, ensuring it does not contain a comma."""
    while True:
        name = input(prompt_text)
        if ',' in name:
            print('❌ The org name cannot contain a comma. Please enter again without commas.')
        else:
            return name

def run_generate(args, batch_mode=False, robots_summary=None):
    """Run the generate workflow for a single org."""

    # Track robots.txt notes for optional end-of-run summary
    summary_owned = robots_summary is None
    local_robots_summary = robots_summary or {"disallow_all": [], "blocked_ai": []}

    def print_local_robots_summary():
        if not summary_owned:
            return
        if not local_robots_summary["disallow_all"] and not local_robots_summary["blocked_ai"]:
            return
        print("\nℹ️robots.txt warnings:")
        if local_robots_summary["disallow_all"]:
            print(" ❌  Disallow all crawlers:")
            for robots_url in local_robots_summary["disallow_all"]:
                print(f"   • {robots_url}")
        if local_robots_summary["blocked_ai"]:
            print(" ⚠️robots.txt blocks specific AI crawlers:")
            for msg in local_robots_summary["blocked_ai"]:
                print(f"   • {msg}")
    
    # Set model if provided
    if args.model:
        os.environ['LLM_MODEL'] = args.model
        
    # Set verbose mode if requested
    if args.verbose:
        os.environ['USE_VERBOSE'] = 'True'
        logging.getLogger().setLevel(logging.DEBUG)

    # Set up a separate log file for generate
    generate_log_file = "logs/generate.log"
    global logger
    logger = setup_logging(LOG_LEVEL, generate_log_file)
    
    try:
        logger.info(f"Generating scraper for {args.org}...")

        # --- robots.txt check BEFORE doing any heavy work ------------------
        robots_txt = get_robots_txt(args.url)
        parsed = urlparse(args.url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

        if robots_txt.strip():
            lines = [l.strip().lower() for l in robots_txt.splitlines() if l.strip()]
            disallow_all = False
            for i, line in enumerate(lines):
                if line == "user-agent: *":
                    for l in lines[i + 1:]:
                        if l.startswith("user-agent:"):
                            break
                        if l.startswith("disallow:") and (l == "disallow: /" or l == "disallow:/"):
                            disallow_all = True
                            break
                    if disallow_all:
                        break

            if disallow_all:
                msg = (
                    "robots.txt has 'User-agent: *' with 'Disallow: /' – "
                    "site disallows all crawlers. Aborting."
                )
                print(f"❌ {msg}")
                logger.warning(msg)
                local_robots_summary.setdefault("disallow_all", []).append(robots_url)
                print_local_robots_summary()
                return 0

            # Otherwise, check which AI companies are blocked using your helper
            allowed_companies = get_allowed_scraper_companies(robots_txt)
            blocked_ai = [c for c in SCRAPER_GROUPS.keys() if c not in allowed_companies]

            if blocked_ai:
                msg = (
                    " ⚠️robots.txt blocks specific AI crawlers:"
                    + ", ".join(blocked_ai)
                    + f". Please review robots.txt: {robots_url}"
                )
                print(msg)
                logger.warning(msg)
                local_robots_summary.setdefault("blocked_ai", []).append(
                    f"{args.url} blocks specific AI crawlers: {', '.join(blocked_ai)}. Please review robots.txt: {robots_url}"
                )
        else:
            logger.info("No robots.txt found or it is empty; proceeding with scraper generation.")

        # Check for existing scrapers in the seed_data.json file
        existing_scrapers = check_org_scrapers_seed(args.org)
        same_url_idx = None
        for i, scraper in enumerate(existing_scrapers):
            if scraper.get("url") == args.url:
                same_url_idx = i
                break
        
        if existing_scrapers:
            matching_url = same_url_idx is not None
            should_prompt = (not batch_mode) or matching_url

            if should_prompt:
                if matching_url:
                    scraper_url = existing_scrapers[same_url_idx].get('url', 'No URL')
                    choices = [
                        "Cancel operation",
                        f"Overwrite: ({scraper_url})",
                        "Generate a new scraper",
                    ]
                else:
                    # Format existing scrapers for display
                    scrapers_info = []
                    for i, scraper in enumerate(existing_scrapers, 1):
                        scraper_url = scraper.get('url', 'No URL')
                        scrapers_info.append(f"({scraper_url})")
                    
                    logger.warning(f"Found existing scrapers for {args.org}:")
                    for i, info in enumerate(scrapers_info, 1):
                        logger.info(f"  {i}. {info}")
                    
                    # Create choices list with each scraper
                    choices = ["Cancel operation"]  # Default option first
                    for i, scraper in enumerate(existing_scrapers):
                        scraper_url = scraper.get('url', 'No URL')
                        choices.append(f"Overwrite: ({scraper_url})")
                    choices.append("Generate a new scraper")
                
                # Prompt user for action
                questions = [
                inquirer.List('choice',
                        message="What would you like to do?",
                        choices=choices,
                        default="Cancel operation"
                    ),
                ]
                answers = inquirer.prompt(questions)
                choice = answers['choice']
                
                if choice == "Cancel operation":
                    logger.info("Operation cancelled.")
                    return 0
                elif choice == "Generate a new scraper":
                    logger.info("Continuing with generation of a new scraper...")
                    
                    # Find the next available scraper filename
                    args.filename = find_next_scraper_filename(args.org)
                    logger.info(f"Will save as: {args.filename}")

                elif choice.startswith("Overwrite:"):
                    # Extract index of the scraper to overwrite
                    scraper_idx = None
                    for i, scraper in enumerate(existing_scrapers):
                        scraper_url = scraper.get('url')
                        scraper_path = scraper.get('path')
                        assert scraper_url and scraper_path
                        scraper_path = scraper_path.replace(".", "/") + ".py"

                        if choice == f"Overwrite: ({scraper_url})":
                            scraper_idx = i
                            break
                    
                    if scraper_idx is not None:
                        args.filename = scraper_path.split("/")[-1]
                        logger.info(f"\nOverwriting scraper: {existing_scrapers[scraper_idx]['url']}")
                        logger.info(f"Will save as: {args.filename}")
                else:
                    logger.error("Invalid choice. Operation cancelled.")
                    return 1
            else:
                if not args.filename or args.filename == "scraper.py":
                    args.filename = find_next_scraper_filename(args.org)
                logger.info(f"Found existing scrapers for {args.org}; using next available filename: {args.filename}")
        elif not args.filename:
            # Ensure a filename is always set
            args.filename = find_next_scraper_filename(args.org)

        # Generate the scraper code
        if args.template:
            logger.info(f"Using template: {args.template} (note: template selection is currently not applied during generation)")

        scraper_code = generate_scraper(args.url, args.org, args.filename or "scraper.py")
        
        # Save the scraper code using save_scraper with the specified filename
        output_path = save_scraper(scraper_code, args.org, args.url, args.filename)
        
        # Get metadata about the scraper
        metadata = get_scraper_metadata(scraper_code, args.org, args.url)
        
        logger.info(f"\nScraper generated successfully!")
        logger.info(f"Saved to: {output_path}")
        logger.info(f"Code size: {metadata['code_size']} bytes")
        
        # Print features
        features = [k for k, v in metadata['features'].items() if v]
        if features:
            logger.info(f"Features: {', '.join(features)}")
        
        # Automatically register the generated scraper
        logger.info(f"\nRegistering the generated scraper for {args.org}...")

        register_cmd = [
            sys.executable,
            sys.argv[0],
            "register",
            "--name", args.org,
            "--url", args.url,
            "--filename", args.filename
        ]
        
        subprocess.run(register_cmd)
        
        print_local_robots_summary()
        return 0
    
    except Exception as e:
        logger.error(f"\n❌ Error generating scraper: {str(e)}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


def handle_generate_batch(args):
    """Generate scrapers from a batch file."""
    entries = load_batch_file(args.batch_file)
    if not entries:
        print("❌ No valid entries found in the batch file.")
        return 1

    exit_code = 0
    total = len(entries)
    robots_summary = {"disallow_all": [], "blocked_ai": []}

    for idx, entry in enumerate(entries, 1):
        print(f"\n[{idx}/{total}] Generating scraper for {entry['org']} ({entry['url']})")
        # Prefer entry filename, then CLI-provided, otherwise None so numbering can pick next
        entry_filename = entry.get("filename")
        if not entry_filename and args.filename and args.filename != "scraper.py":
            entry_filename = args.filename

        entry_args = argparse.Namespace(
            command='generate',
            org=entry["org"],
            url=entry["url"],
            filename=entry_filename,
            template=entry.get("template") or args.template,
            model=entry.get("model") or args.model,
            verbose=bool(entry.get("verbose")) or args.verbose,
            batch_file=args.batch_file,
        )

        result = run_generate(entry_args, batch_mode=True, robots_summary=robots_summary)
        if result != 0:
            exit_code = result

    if robots_summary["disallow_all"] or robots_summary["blocked_ai"]:
        print("\nℹ️robots.txt warnings:")
        if robots_summary["disallow_all"]:
            print(" ❌ robots.txt disallows all crawlers:")
            for robots_url in robots_summary["disallow_all"]:
                print(f"   • {robots_url}")
        if robots_summary["blocked_ai"]:
            print(" ⚠️robots.txt blocks specific AI crawlers:")
            for msg in robots_summary["blocked_ai"]:
                print(f"   • {msg}")

    return exit_code


def handle_generate(args):
    """Handle the generate command (single or batch)."""
    if getattr(args, "batch_file", None):
        return handle_generate_batch(args)
    return run_generate(args)

def handle_test(args):
    """Handle the test command"""
    # Set up a separate log file for test
    test_log_file = "logs/test.log"
    global logger
    logger = setup_logging(LOG_LEVEL, test_log_file)

    # Set verbose mode if requested
    if args.verbose:
        os.environ['USE_VERBOSE'] = 'True'
        logging.getLogger().setLevel(logging.DEBUG)
    
    try:
        # Testing a specific scraper by path
        if args.path:
            logger.info(f"Testing scraper at {args.path}...")
            if not os.path.exists(args.path):
                logger.error(f"❌ Scraper file not found: {args.path}")
                return 1
            
            # Run tests using the existing test module
            success = run_tests(args.path)
            return 0 if success else 1
        
        # Testing all scrapers for a specific org
        elif args.org:
            logger.info(f"Testing all scrapers for {args.org}...")

            SCRAPERS_DIR = Path("scrapers")
            code_slug = args.org.lower().replace(' ', '_')

            # Load seed data
            seed_data_path = SCRAPERS_DIR / code_slug / "seed.json"
            if not os.path.exists(seed_data_path):
                logger.error(f"❌ Seed data file not found: {seed_data_path}")
                return 1
            
            with open(seed_data_path, 'r', encoding='utf-8') as f:
                org_data = json.load(f)

            if not org_data:
                logger.error(f"❌ No seed data found for '{args.org}'")
                return 1
            
            # Get scrapers for the org
            scrapers = org_data.get('scrapers', [])
            if not scrapers:
                logger.error(f"❌ No scrapers found for {args.org}")
                return 1
            
            logger.info(f"Found {len(scrapers)} scraper(s) for {args.org}")
            
            # Test each scraper
            all_success = True
            for i, scraper in enumerate(scrapers, 1):
                scraper_path = scraper.get('path')
                if not scraper_path:
                    logger.error(f"❌ Scraper #{i} has no path")
                    all_success = False
                    continue

                # Convert module path to file path
                path_parts = scraper_path.split('.')
                file_path = os.path.join(*path_parts[:-1], f"{path_parts[-1]}.py")

                logger.info(f"\n[{i}/{len(scrapers)}] Testing {scraper_path}...")

                if not os.path.exists(file_path):
                    logger.error(f"❌ Scraper file not found: {file_path}")
                    all_success = False
                    continue

                # Run tests for this scraper
                success = run_tests(file_path)
                if not success:
                    all_success = False

            if all_success:
                logger.info(f"\n✅ All scrapers for {args.org} passed tests")
            else:
                logger.error(f"\n❌ Some scrapers for {args.org} failed tests")

            return 0 if all_success else 1
        
        # Neither path nor org specified
        else:
            logger.error("❌ Please specify either --path or --org")
            return 1
        
    except Exception as e:
        logger.error(f"\n❌ Error testing scraper: {str(e)}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1

def handle_register(args):
    """Handle the register command"""
    try:
        print(f"Registering org {args.name}...")
        
        SCRAPERS_DIR = Path("scrapers")
        code_slug = args.name.lower().replace(' ', '_')

        # Load existing seed data
        seed_data_path = SCRAPERS_DIR / code_slug / "seed.json"
        if os.path.exists(seed_data_path):
            with open(seed_data_path, 'r', encoding='utf-8') as f:
                seed_data = json.load(f)
        else:
            seed_data = []
                
        # Extract module suffix from filename (remove .py extension)
        module_suffix = args.filename.replace('.py', '')
        
        scraper = {
            "path": f"scrapers.{code_slug}.{module_suffix}",
            "url": args.url
        }
        
        # Check if org seed data already exists
        existing_org = None
        if seed_data != [] and seed_data:
            existing_org = seed_data
        
        if existing_org:
            # Add scraper to existing org
            print(f"Org '{args.name}' already exists - adding new scraper.")
                        
            # Check if a scraper with the same path already exists (override case)
            scraper_overwritten = False
            for existing_scraper in seed_data.get('scrapers', []):
                if existing_scraper.get('path') == scraper['path']:
                    print(f"Overwriting existing scraper at path '{scraper['path']}'")
                    existing_scraper['url'] = scraper['url']
                    scraper_overwritten = True

            if not scraper_overwritten:
                # Check if a scraper with the same URL already exists
                for s in existing_org.get('scrapers', []):
                    if s.get('url') == args.url:
                        print(f"❌ Error: A scraper with URL '{args.url}' already exists for this org.")
                        return 1
                
                # Add the new scraper to the existing org
                if 'scrapers' not in existing_org:
                    existing_org['scrapers'] = []
                    
                existing_org['scrapers'].append(scraper)
                
                # Update the org in the seed data
                print(f"Added new scraper '{scraper['path']}' to org '{args.name}'.")
            else:
                print(f"Updated scraper '{scraper['path']}' for org '{args.name}'.")
            
            # Update the org in the seed data
            seed_data = existing_org
        else:
            new_org = {
                "name": args.name,
                "scrapers": [scraper]
            }
            
            # Add to seed data
            seed_data = new_org 
            print(f"Created new org '{args.name}' with scraper '{scraper['path']}'.")
        
        # Save updated seed data
        with open(seed_data_path, 'w', encoding='utf-8') as f:
            json.dump(seed_data, f, indent=4)
        
        print(f"✅ org {args.name} registered successfully!")
        print(f"📁 Updated seed data saved to {seed_data_path}")
        
        # Run seed script if requested
        if args.run_seed:
            print("Running db/seed.py...")
            result = os.system('python db/seed.py')
            if result == 0:
                print("✅ Database seeding completed successfully!")
            else:
                print("❌ Database seeding failed!")
        
        return 0
    
    except Exception as e:
        print(f"\n❌ Error registering org: {str(e)}")
        traceback.print_exc()
        return 1

def main():
    """Main entry point"""
    # Check if OpenAI API key is set
    if not os.getenv('OPENAI_API_KEY'):
        print("⚠️ OPENAI_API_KEY environment variable is not set.")
        print("Please set it in your .env file or environment variables.")
        print("You can get an API key from https://platform.openai.com/")
        return 1
    
    args = parse_args()
    
    if args.command == 'generate':
        return handle_generate(args)
    elif args.command == 'test':
        return handle_test(args)
    elif args.command == 'register':
        return handle_register(args)
    else:
        print(f"Unknown command: {args.command}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
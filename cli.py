#!/usr/bin/env python
"""
Command Line Interface for the Articles Scraper Generator.
"""

import os
import sys
import json
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

def handle_generate(args):
    """Handle the generate command"""
    
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
                return 0

            # Otherwise, check which AI companies are blocked using your helper
            allowed_companies = get_allowed_scraper_companies(robots_txt)
            blocked_ai = [c for c in SCRAPER_GROUPS.keys() if c not in allowed_companies]

            if blocked_ai:
                msg = "⚠️ robots.txt disallows some AI crawlers: " + ", ".join(blocked_ai)
                print(msg)
                logger.warning(msg)

                parsed = urlparse(args.url)
                robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

                # Ask the user if they still want to proceed
                confirm_q = [
                    inquirer.Confirm(
                        "proceed",
                        message=f"Do you still want to generate a scraper for this site? Please read the full robots.txt before deciding: {robots_url}",
                        default=False,
                    )
                ]
                confirm_answer = inquirer.prompt(confirm_q)

                if not confirm_answer or not confirm_answer["proceed"]:
                    logger.info("User cancelled generation due to robots.txt restrictions.")
                    return 0
        else:
            logger.info("No robots.txt found or it is empty; proceeding with scraper generation.")

        # Check for existing scrapers in the seed_data.json file
        existing_scrapers = check_org_scrapers_seed(args.org)
        
        if existing_scrapers:
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
                # Format is "Overwrite: name (url)" so we need to find which one it was
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

        # Generate the scraper code
        if args.template:
            logger.info(f"Using template: {args.template}")
            scraper_code = generate_scraper(args.url, args.org, args.template, args.filename)
        else:
            scraper_code = generate_scraper(args.url, args.org)
        
        # Save the scraper code using save_scraper with the specified filename
        output_path = save_scraper(scraper_code, args.org, args.url, args.filename)
        
        # Get metadata about the scraper
        metadata = get_scraper_metadata(scraper_code, args.org, args.url)
        
        logger.info(f"\n✅ Scraper generated successfully!")
        logger.info(f"📁 Saved to: {output_path}")
        logger.info(f"📊 Code size: {metadata['code_size']} bytes")
        
        # Print features
        features = [k for k, v in metadata['features'].items() if v]
        if features:
            logger.info(f"🔍 Features: {', '.join(features)}")
        
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
        
        return 0
    
    except Exception as e:
        logger.error(f"\n❌ Error generating scraper: {str(e)}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1

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
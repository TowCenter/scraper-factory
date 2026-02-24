#!/usr/bin/env python3
"""scraper_generator/test.py
=================================
Minimal test suite for a news‑scraper.

Usage:
    # As a sub‑command via cli.py
    python cli.py test path/to/scraper.py

    # Direct invocation
    python -m scraper_generator.test path/to/scraper.py
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import ast
import asyncio
import importlib.util
from argparse import ArgumentParser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def _load_scraper_module(scraper_path: Path):
    """Dynamically import a scraper module from an arbitrary file path."""
    spec = importlib.util.spec_from_file_location("scraper_under_test", str(scraper_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_async(coro):
    """Run an async coroutine from synchronous test code."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

# ---------------------------------------------------------------------------
# Test Framework
# ---------------------------------------------------------------------------

class Test:
    """Base class for all tests."""
    
    def __init__(self, name: str = None):
        # Use the class name as test name if not provided
        self.name = name or self.__class__.__name__.replace("Test", "")
        # Use the class docstring as description
        self.description = self.__doc__.strip() if self.__doc__ else "No description provided"
        self.passed = False
        self.failures: List[Dict[str, Any]] = []
    
    def run(self, context: TestContext) -> bool:
        """Run the test and return whether it passed."""
        raise NotImplementedError("Subclasses must implement this method")
    
    def format_status(self) -> str:
        """Format the test result as a string."""
        return f"{self.description} {'✅' if self.passed else '❌'}"
    
    def format_failure_details(self, data: List[Dict[str, Any]]) -> str:
        """Format details about test failures."""
        if not self.failures or self.passed:
            return ""
            
        details = []
        for failure in self.failures[:5]:  # Show at most 5 examples
            idx = failure.get('index', -1)
            if idx >= 0 and idx < len(data):
                details.append(f"{json.dumps(data[idx], indent=2)}")
                
                if 'fields' in failure:
                    details.append(f"Blank fields: {', '.join(failure['fields'])}")
                elif 'keys' in failure:
                    details.append(f"Keys present: {list(data[idx].keys())}")
                elif 'invalid' in failure:
                    details.append(f"Invalid date: {failure['invalid']}")
                elif 'url' in failure:
                    details.append(f"Invalid URL: {failure['url']}")
                    
                details.append("")
                
        if len(self.failures) > 5:
            details.append(f"... and {len(self.failures) - 5} more items with issues")
            
        return "\n".join(details)

def _load_config_json(scraper_path: Path) -> dict:
    """Walk up from the scraper path to find config.json at the project root."""
    candidate = scraper_path.parent
    while True:
        config_file = candidate / "config.json"
        if config_file.is_file():
            with config_file.open(encoding="utf-8") as f:
                return json.load(f)
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    raise FileNotFoundError(
        "config.json not found. Please create one at the project root. "
        "See content_configs/ for examples."
    )


class TestContext:
    """Holds shared data and state for tests."""

    def __init__(self, scraper_path: Path):
        self.scraper_path = scraper_path
        self.results_path = scraper_path.parent / "result.json"
        self.data: Optional[List[Dict[str, Any]]] = None
        self.first_page_articles: Optional[List[Dict[str, Any]]] = None
        self.pagination_failed: bool = False
        self.pagination_page_counts: List[int] = []

        # Load content config from project-root config.json (required)
        content_config = _load_config_json(scraper_path)

        fields = content_config["fields"]
        self.expected_keys: set = {f["name"] for f in fields} | {"scraper"}
        self.required_fields: set = {f["name"] for f in fields if f.get("required", True)}
        self.date_fields: List[str] = [f["name"] for f in fields if f.get("type") == "date"]
        self.url_fields: List[str] = [f["name"] for f in fields if f.get("type") == "url"]

# ---------------------------------------------------------------------------
# Test Implementations
# ---------------------------------------------------------------------------

class ResultFileExistsTest(Test):
    """Check if result.json file was created."""
    
    def run(self, context: TestContext) -> bool:
        self.passed = context.results_path.is_file()
        return self.passed

class ResultFileReadableTest(Test):
    """Check if result.json contains valid JSON."""
    
    def run(self, context: TestContext) -> bool:
        try:
            with context.results_path.open(encoding="utf-8") as fp:
                context.data = json.load(fp)
                self.passed = True
        except Exception as exc:
            self.failures.append({"error": str(exc)})
            self.passed = False
        
        return self.passed

class DataStructureTest(Test):
    """Check if result.json contains a non-empty list of dictionaries."""
    
    def run(self, context: TestContext) -> bool:
        if context.data is None:
            self.passed = False
            return False
            
        self.passed = (
            isinstance(context.data, list) and 
            bool(context.data) and 
            all(isinstance(d, dict) for d in context.data)
        )
        
        return self.passed

class ItemKeysTest(Test):
    """Check each dict has only the configured field keys plus scraper."""

    def run(self, context: TestContext) -> bool:
        if not context.data:
            self.passed = False
            return False

        failures = []
        for i, record in enumerate(context.data):
            if set(record.keys()) != context.expected_keys:
                failures.append({"index": i, "keys": set(record.keys())})

        self.failures = failures
        self.passed = len(failures) == 0
        return self.passed

class NonBlankValuesTest(Test):
    """Check all required fields are non-empty strings (optional fields may be null/empty)."""

    def run(self, context: TestContext) -> bool:
        if not context.data:
            self.passed = False
            return False

        enforce = context.required_fields | {"scraper"}

        failures = []
        for i, record in enumerate(context.data):
            blank_fields = {
                k for k, v in record.items()
                if k in enforce and (not isinstance(v, str) or not v.strip())
            }
            if blank_fields:
                failures.append({"index": i, "fields": blank_fields})

        self.failures = failures
        self.passed = len(failures) == 0
        return self.passed

class DateFormatTest(Test):
    """Check date fields parse as YYYY-MM-DD (skipped if no date field in config)."""

    def run(self, context: TestContext) -> bool:
        if not context.date_fields:
            self.passed = True
            return True

        if not context.data:
            self.passed = False
            return False

        failures = []
        for i, record in enumerate(context.data):
            for field_name in context.date_fields:
                val = record.get(field_name)
                if isinstance(val, str) and not self._valid_date(val):
                    failures.append({"index": i, "invalid": val})

        self.failures = failures
        self.passed = len(failures) == 0
        return self.passed

    def _valid_date(self, s: str) -> bool:
        try:
            datetime.strptime(s, "%Y-%m-%d")
            return True
        except ValueError:
            return False


class UrlFormatTest(Test):
    """Check URL fields are valid (skipped if no url field in config)."""

    def run(self, context: TestContext) -> bool:
        if not context.url_fields:
            self.passed = True
            return True

        if not context.data:
            self.passed = False
            return False

        failures = []
        for i, record in enumerate(context.data):
            for field_name in context.url_fields:
                val = record.get(field_name)
                if isinstance(val, str) and not self._valid_url(val):
                    failures.append({"index": i, "url": val})

        self.failures = failures
        self.passed = len(failures) == 0
        return self.passed

    def _valid_url(self, s: str) -> bool:
        p = urllib.parse.urlparse(s)
        return bool(p.scheme and p.netloc)

class RequiredFunctionsTest(Test):
    """Check if scraper defines required functions with correct parameters and list all functions."""

    def __init__(self):
        super().__init__()
        self.required_functions = {
            "get_first_page": [],
            "get_all_articles": ["max_pages"]
        }
        self.found_functions = []

    def run(self, context: TestContext) -> bool:
        try:
            with context.scraper_path.open(encoding="utf-8") as fp:
                source_code = fp.read()
            tree = ast.parse(source_code)

            # Collect all function definitions (including async)
            defined_functions = {}
            self.found_functions = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    params = [arg.arg for arg in node.args.args if arg.arg != 'self']
                    defined_functions[node.name] = params
                    self.found_functions.append({"name": node.name, "params": params})

            # Check required functions
            missing_functions = []
            param_issues = []
            for func_name, required_params in self.required_functions.items():
                if func_name not in defined_functions:
                    missing_functions.append(func_name)
                else:
                    actual_params = defined_functions[func_name]
                    missing_params = [p for p in required_params if p not in actual_params]
                    if missing_params:
                        param_issues.append({
                            "function": func_name,
                            "missing_params": missing_params,
                            "actual_params": actual_params
                        })

            failures = []
            if missing_functions:
                failures.append({"missing_functions": missing_functions})
            if param_issues:
                failures.append({"parameter_issues": param_issues})

            self.failures = failures
            self.passed = len(failures) == 0

        except Exception as exc:
            self.failures = [{"error": f"Failed to parse Python file: {str(exc)}"}]
            self.passed = False

        return self.passed

    def format_failure_details(self, data: List[Dict[str, Any]]) -> str:
        details = []
        if self.found_functions:
            details.append("All discovered functions:")
            for func in self.found_functions:
                details.append(f"  {func['name']}({', '.join(func['params'])})")
            details.append("")

        for failure in self.failures:
            if "missing_functions" in failure:
                details.append(f"Missing required functions: {', '.join(failure['missing_functions'])}")
            elif "parameter_issues" in failure:
                for issue in failure["parameter_issues"]:
                    func = issue["function"]
                    missing = issue["missing_params"]
                    actual = issue["actual_params"]
                    details.append(f"Function '{func}' missing parameters: {', '.join(missing)}")
                    details.append(f"  Found parameters: {actual}")
            elif "error" in failure:
                details.append(f"Error: {failure['error']}")

        return "\n".join(details)


class GetFirstPageTest(Test):
    """Check if get_first_page() returns a non-empty list of article dicts."""

    def run(self, context: TestContext) -> bool:
        try:
            module = _load_scraper_module(context.scraper_path)
            articles = _run_async(module.get_first_page())

            if not isinstance(articles, list) or len(articles) == 0:
                count = len(articles) if isinstance(articles, list) else "N/A"
                self.failures.append({"error": f"get_first_page returned {count} items"})
                self.passed = False
                return False

            for i, article in enumerate(articles):
                if not isinstance(article, dict):
                    self.failures.append({"index": i, "error": f"Item is {type(article).__name__}, not dict"})

            self.passed = len(self.failures) == 0
            context.first_page_articles = articles
        except Exception as exc:
            self.failures.append({"error": str(exc)})
            self.passed = False

        return self.passed

    def format_failure_details(self, data: List[Dict[str, Any]]) -> str:
        return "\n".join(f.get("error", str(f)) for f in self.failures)


class GetAllArticlesTest(Test):
    """Check if get_all_articles works across 3 pages with pagination growth."""

    MAX_PAGES = 3

    def run(self, context: TestContext) -> bool:
        try:
            module = _load_scraper_module(context.scraper_path)
            articles, page_counts = _run_async(self._scrape_with_growth_tracking(module))

            context.pagination_page_counts = page_counts

            if not isinstance(articles, list) or len(articles) == 0:
                self.failures.append({"error": f"get_all_articles returned 0 articles across {self.MAX_PAGES} pages"})
                self.passed = False
                context.pagination_failed = True
                return False

            # Write result.json for downstream file-based tests
            result_path = context.scraper_path.parent / "result.json"
            with open(result_path, "w") as f:
                json.dump(articles, f, indent=2)

            # Populate context.data for downstream validation tests
            context.data = articles

            # Check pagination growth: article count should increase after each page
            growth_failures = []
            for i in range(1, len(page_counts)):
                if page_counts[i] <= page_counts[i - 1]:
                    growth_failures.append({
                        "page": i + 1,
                        "prev_count": page_counts[i - 1],
                        "curr_count": page_counts[i],
                        "error": f"Page {i + 1}: article count did not increase ({page_counts[i - 1]} -> {page_counts[i]}). Pagination may be broken."
                    })

            if growth_failures:
                self.failures = growth_failures
                self.passed = False
                context.pagination_failed = True
            else:
                self.passed = True
                context.pagination_failed = False

        except Exception as exc:
            self.failures.append({"error": str(exc)})
            self.passed = False
            context.pagination_failed = True

        return self.passed

    async def _scrape_with_growth_tracking(self, module):
        """Navigate through up to MAX_PAGES pages, returning articles and per-page cumulative counts."""
        async with module.PlaywrightContext() as browser_context:
            page = await browser_context.new_page()
            await page.goto(module.base_url)

            all_articles = []
            seen = set()
            page_counts = []

            for page_num in range(self.MAX_PAGES):
                page_articles = await module.scrape_page(page)
                for article in page_articles:
                    key = tuple(sorted(article.items()))
                    if key not in seen:
                        seen.add(key)
                        all_articles.append(article)

                page_counts.append(len(all_articles))

                if page_num < self.MAX_PAGES - 1:
                    await module.advance_page(page)

            await page.close()

        return all_articles, page_counts

    def format_failure_details(self, data: List[Dict[str, Any]]) -> str:
        details = []
        for f in self.failures:
            if "error" in f:
                details.append(f["error"])
            else:
                details.append(str(f))
        if hasattr(self, '_context_page_counts'):
            details.append(f"Page counts: {self._context_page_counts}")
        return "\n".join(details)


# ---------------------------------------------------------------------------
# Test Runner
# ---------------------------------------------------------------------------

def run_tests_detailed(scraper_path: str | Path) -> dict:
    """Run the full test sequence. Return dict with detailed results."""
    scraper = Path(scraper_path).resolve()
    if not scraper.is_file():
        raise FileNotFoundError(f"Scraper not found: {scraper}")

    # Create test context
    context = TestContext(scraper)

    # Define tests to run in sequence
    tests = [
        RequiredFunctionsTest(),
        GetFirstPageTest(),
        GetAllArticlesTest(),
        ResultFileExistsTest(),
        ResultFileReadableTest(),
        DataStructureTest(),
        ItemKeysTest(),
        NonBlankValuesTest(),
        DateFormatTest(),
        UrlFormatTest(),
    ]

    # Run all tests and collect results
    all_passed = True

    for test in tests:
        passed = test.run(context)
        print(test.format_status())

        if not passed:
            all_passed = False

            # For RequiredFunctionsTest, show function-specific details
            if isinstance(test, RequiredFunctionsTest):
                failure_details = test.format_failure_details(context.data or [])
                if failure_details:
                    print(f"\nFunction validation issues:")
                    print(failure_details)

            # For data-related tests, show failure details
            elif context.data is not None:
                details = test.format_failure_details(context.data)
                if details:
                    print(f"\nItems with {test.name.lower()} issues:")
                    print(details)

    print("-" * 80)
    print("All tests passed. " + "✅" if all_passed else "❌")

    return {
        "all_passed": all_passed,
        "pagination_failed": context.pagination_failed,
        "pagination_page_counts": context.pagination_page_counts,
    }


def run_tests(scraper_path: str | Path) -> bool:
    """Run the full test sequence. Return True if all checks pass."""
    return run_tests_detailed(scraper_path)["all_passed"]

# ---------------------------------------------------------------------------
# CLI entry‑point
# ---------------------------------------------------------------------------

def _cli() -> None:
    parser = ArgumentParser(description="Basic assertions for a news scraper.")
    parser.add_argument("scraper", type=Path, help="Path to the scraper Python file")
    args = parser.parse_args()

    success = run_tests(args.scraper)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    _cli()

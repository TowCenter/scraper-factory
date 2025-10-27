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
import subprocess
import sys
import urllib.parse
import ast
import inspect
from argparse import ArgumentParser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

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

class TestContext:
    """Holds shared data and state for tests."""
    
    def __init__(self, scraper_path: Path):
        self.scraper_path = scraper_path
        self.results_path = scraper_path.parent / "result.json"
        self.data: Optional[List[Dict[str, Any]]] = None
        self.scraper_process: Optional[subprocess.CompletedProcess] = None

# ---------------------------------------------------------------------------
# Test Implementations
# ---------------------------------------------------------------------------

class RunScraperTest(Test):
    """Check if scraper executes successfully."""
    
    def run(self, context: TestContext) -> bool:
        try:
            context.scraper_process = subprocess.run(
                [sys.executable, str(context.scraper_path)],
                capture_output=False,
                text=True,
                check=False,
            )
            self.passed = context.scraper_process.returncode == 0
        except Exception:
            self.passed = False
        
        return self.passed

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
    """Check each dict has only title, date, url, and scraper as keys."""

    def run(self, context: TestContext) -> bool:
        if not context.data:
            self.passed = False
            return False
            
        failures = []
        for i, record in enumerate(context.data):
            if set(record.keys()) != {"title", "date", "url", "scraper"}:
                failures.append({"index": i, "keys": set(record.keys())})
        
        self.failures = failures
        self.passed = len(failures) == 0
        return self.passed

class NonBlankValuesTest(Test):
    """Check all fields are non-empty strings."""
    
    def run(self, context: TestContext) -> bool:
        if not context.data:
            self.passed = False
            return False
            
        failures = []
        for i, record in enumerate(context.data):
            blank_fields = {k for k, v in record.items() 
                           if not isinstance(v, str) or not v.strip()}
            if blank_fields:
                failures.append({"index": i, "fields": blank_fields})
        
        self.failures = failures
        self.passed = len(failures) == 0
        return self.passed

class DateFormatTest(Test):
    """Check date strings parse as YYYY-MM-DD."""
    
    def run(self, context: TestContext) -> bool:
        if not context.data:
            self.passed = False
            return False
            
        failures = []
        for i, record in enumerate(context.data):
            date = record.get("date")
            if isinstance(date, str) and not self._valid_date(date):
                failures.append({"index": i, "invalid": date})
        
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
    """Check URLs are in valid format (e.g. https://example.com)."""

    def run(self, context: TestContext) -> bool:
        if not context.data:
            self.passed = False
            return False
            
        failures = []
        for i, record in enumerate(context.data):
            url = record.get("url")
            if isinstance(url, str) and not self._valid_url(url):
                failures.append({"index": i, "url": url})
        
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
    
# ---------------------------------------------------------------------------
# Test Runner
# ---------------------------------------------------------------------------

def run_tests(scraper_path: str | Path) -> bool:
    """Run the full test sequence. Return True if all checks pass."""
    scraper = Path(scraper_path).resolve()
    if not scraper.is_file():
        raise FileNotFoundError(f"Scraper not found: {scraper}")

    # Create test context
    context = TestContext(scraper)
    
    # Define tests to run in sequence
    tests = [
        RunScraperTest(),
        RequiredFunctionsTest(),  # Add this test first to check structure
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
    stop_on_failure = False
    
    for test in tests:
        passed = test.run(context)
        print(test.format_status())
        
        if not passed:
            all_passed = False
            
            # Show scraper errors if that test failed
            if test.name == "Run scraper" and context.scraper_process and context.scraper_process.stderr:
                print("\nScraper error output:")
                print(context.scraper_process.stderr)
                break
            
            # For RequiredFunctionsTest, show function-specific details
            if isinstance(test, RequiredFunctionsTest):
                failure_details = test.format_failure_details(context.data or [])
                if failure_details:
                    print(f"\nFunction validation issues:")
                    print(failure_details)
                                
            # For data-related tests, show failure details
            if context.data is not None:
                print(f"\nItems with {test.name.lower()} issues:")
                print(test.format_failure_details(context.data))
            
            # Stop testing after first failure (except for scraper execution)
            if stop_on_failure and test.name != "Run scraper":
                break

    print("-" * 80)
    print("All tests passed. " + "✅" if all_passed else "❌")
    return all_passed

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

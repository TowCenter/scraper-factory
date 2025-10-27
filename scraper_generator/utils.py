"""
Specialized utility functions for the scraper generator.
"""

import ast
from utils import setup_logging
from .config import LOG_LEVEL, LOG_FILE

# Initialize logger using the shared utility function
logger = setup_logging(LOG_LEVEL, LOG_FILE)

def remove_module_docstring(source: str) -> str:
    """Return *source* minus its top-level docstring (if one exists)."""
    tree = ast.parse(source)
    # A module docstring is always the very first statement and an ast.Expr
    if (tree.body and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)
            and isinstance(tree.body[0].value.value, str)):
        first_stmt = tree.body[0]
        # lineno/end_lineno are 1-based, end_lineno is inclusive
        start = first_stmt.lineno - 1
        end = first_stmt.end_lineno           # slice is exclusive, so no +1
        lines = source.splitlines()
        del lines[start:end]
        # remove any leading blank lines left behind
        while lines and not lines[0].strip():
            lines.pop(0)
        return "\n".join(lines)
    return source  # no module docstring found

"""Functions defined inside `except ... as e:` must not read `e`.

Python deletes the exception name when the except block ends, so a nested generator or
callback that runs later raises NameError instead of reporting the error. Copy the value
first (e.g. `error_message = str(e)`) and use that inside the nested function.
"""
from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"venv", "node_modules", "__pycache__"}


def _python_files():
    for path in BACKEND.rglob("*.py"):
        if SKIP_DIRS.isdisjoint(path.relative_to(BACKEND).parts):
            yield path


def test_nested_functions_do_not_read_exception_name():
    offenders = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for handler in ast.walk(tree):
            if not isinstance(handler, ast.ExceptHandler) or not handler.name:
                continue
            for stmt in handler.body:
                for fn in ast.walk(stmt):
                    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                        continue
                    if any(isinstance(n, ast.Name) and n.id == handler.name for n in ast.walk(fn)):
                        offenders.append(f"{path.relative_to(BACKEND)}:{fn.lineno}")
    assert not offenders, offenders

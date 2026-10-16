"""No function may call get_entitlements() before a function-local import of it.

A local ``from services.entitlements import get_entitlements`` makes the name local
to the whole function, so an earlier call raises UnboundLocalError at runtime.
"""
from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"venv", "packages", "node_modules", "__pycache__"}


def _python_files():
    for path in BACKEND.rglob("*.py"):
        if SKIP_DIRS.isdisjoint(path.relative_to(BACKEND).parts):
            yield path


def test_get_entitlements_not_used_before_local_import():
    offenders = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            import_lines = [
                node.lineno
                for node in ast.walk(fn)
                if isinstance(node, ast.ImportFrom)
                and any((alias.asname or alias.name) == "get_entitlements" for alias in node.names)
            ]
            if not import_lines:
                continue
            first_import = min(import_lines)
            early_uses = [
                node.lineno
                for node in ast.walk(fn)
                if isinstance(node, ast.Name) and node.id == "get_entitlements" and node.lineno < first_import
            ]
            if early_uses:
                offenders.append(f"{path.relative_to(BACKEND)}:{fn.lineno} {fn.name}")
    assert not offenders, offenders

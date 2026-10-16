#!/usr/bin/env python3
"""Apply add_llm_model_layer.sql migration. Run from backend/: python scripts/run_llm_migration.py"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv(override=True)


async def main() -> None:
    from db.connection import database

    migration_path = os.path.join(
        os.path.dirname(__file__), "..", "db", "migrations", "add_llm_model_layer.sql"
    )
    with open(migration_path, encoding="utf-8") as f:
        sql = f.read()

    await database.connect()
    try:
        # Execute statement-by-statement for drivers that dislike multi-statement
        statements = []
        buf = []
        for line in sql.splitlines():
            stripped = line.strip()
            if stripped.startswith("--") and not buf:
                continue
            buf.append(line)
            if stripped.endswith(";"):
                stmt = "\n".join(buf).strip()
                if stmt and not stmt.startswith("--"):
                    statements.append(stmt)
                buf = []
        if buf:
            stmt = "\n".join(buf).strip()
            if stmt:
                statements.append(stmt)

        for stmt in statements:
            try:
                await database.execute(stmt)
            except Exception as e:
                err = str(e).lower()
                if "already exists" in err or "duplicate" in err:
                    continue
                raise
        print("LLM model layer migration applied successfully.")
    finally:
        await database.disconnect()


if __name__ == "__main__":
    asyncio.run(main())

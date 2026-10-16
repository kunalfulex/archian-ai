"""SQL generation prompt must not instruct LLM to dedupe via GROUP BY."""


def test_generate_sql_query_prompt_includes_dedupe_pipeline_guidance():
    # Import module text via a minimal read of the guideline block in source
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "handlers" / "sql_handler.py"
    text = source.read_text(encoding="utf-8")
    assert "Do NOT use GROUP BY" in text
    assert "later pipeline step handles that" in text
    assert "remove duplicates" in text.lower() or "dedupe" in text.lower()

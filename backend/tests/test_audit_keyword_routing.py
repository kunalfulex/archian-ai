"""CI guardrails for keyword routing and clarification compose slots."""

from pathlib import Path
import subprocess
import sys


def test_audit_keyword_routing_passes():
    backend = Path(__file__).resolve().parents[1]
    script = backend / "scripts" / "audit_keyword_routing.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(backend),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

"""Where tests that keep evidence write it.

By default evidence goes to a temporary folder, so a fresh clone runs the whole suite
without any local files. Set GROUPFLOW_KEEP_EVIDENCE=1 to keep it under eval/runs/
(ignored by git) when producing a dated evidence bundle.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
_TEMP = Path(tempfile.mkdtemp(prefix="groupflow-evidence-"))


def evidence_dir(relative: str) -> Path:
    """A directory for one test's evidence, e.g. evidence_dir("2026-10-18-shared-workload/scope")."""
    keep = os.environ.get("GROUPFLOW_KEEP_EVIDENCE", "").strip() not in ("", "0")
    base = ROOT / "eval" / "runs" if keep else _TEMP
    path = base / relative
    path.mkdir(parents=True, exist_ok=True)
    return path

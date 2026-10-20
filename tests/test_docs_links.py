"""A fresh clone must not link to files it does not have (ignored run folders, local data)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_markdown_links_resolve_in_a_fresh_clone():
    spec = importlib.util.spec_from_file_location("check_links", ROOT / "scripts" / "check_links.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    assert module.broken_links() == []

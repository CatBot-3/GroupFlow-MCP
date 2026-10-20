"""Check that every relative link in the repository's Markdown files points at a file a fresh clone has.

A link counts as broken when its target is missing, or exists only locally because .gitignore
excludes it (eval/runs/, data/, .env). Run from the repository root:

    python scripts/check_links.py          # exit 1 and list broken links
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)|!\[[^\]]*\]\(([^)\s]+)\)")
SKIP_DIRS = {".git", ".venv", "node_modules", "eval/runs", "_to_delete", "output", "web/dist"}


def markdown_files() -> list[Path]:
    out = []
    for path in ROOT.rglob("*.md"):
        rel = path.relative_to(ROOT).as_posix()
        if any(rel == d or rel.startswith(d + "/") for d in SKIP_DIRS) or {"node_modules", ".venv", "__pycache__"} & set(path.parts):
            continue
        out.append(path)
    return sorted(out)


def ignored(path: Path) -> bool:
    try:
        r = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=ROOT, capture_output=True)
    except FileNotFoundError:
        return False
    return r.returncode == 0


def broken_links() -> list[tuple[str, int, str, str]]:
    problems = []
    for md in markdown_files():
        for n, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
            for m in LINK.finditer(line):
                target = m.group(1) or m.group(2)
                if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("#"):
                    continue
                file_part = target.split("#", 1)[0]
                if not file_part:
                    continue
                resolved = (md.parent / file_part).resolve()
                rel = md.relative_to(ROOT).as_posix()
                if not resolved.exists():
                    problems.append((rel, n, target, "missing"))
                elif ignored(resolved):
                    problems.append((rel, n, target, "ignored by git (not in a fresh clone)"))
    return problems


def main() -> int:
    problems = broken_links()
    for rel, n, target, why in problems:
        print(f"{rel}:{n}: {target} ({why})")
    print(f"{len(problems)} broken link(s) in {len(markdown_files())} Markdown files.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

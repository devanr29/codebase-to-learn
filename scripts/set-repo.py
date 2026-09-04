"""Stamp the real GitHub owner/repo over the OWNER/codemap placeholder.

Every absolute GitHub URL in this repo is written against the placeholder
``OWNER/codemap`` until the repo has a real home — see CONTRIBUTING.md
"Publishing". Run this once, right after creating the GitHub repo:

    python scripts/set-repo.py <owner>/<repo>

Example: ``python scripts/set-repo.py devanr2911/codemap``. Only touches the
specific files below (not a blind repo-wide grep, so it can't clobber prose
that just happens to mention "OWNER" in passing, e.g. this docstring). Prints
every replacement it makes; review the diff before committing.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TARGET_FILES = [
    "README.md",
    "pyproject.toml",
    "CONTRIBUTING.md",
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    "codemap/config.py",
    "commands/explore.md",
    "commands/course.md",
    "commands/review.md",
    "docs/install.md",
    "skills/codebase-to-course/SKILL.md",
]


def main() -> None:
    if len(sys.argv) != 2 or "/" not in sys.argv[1]:
        print("usage: python scripts/set-repo.py <owner>/<repo>", file=sys.stderr)
        raise SystemExit(2)

    owner, repo = sys.argv[1].split("/", 1)
    slug = f"{owner}/{repo}"

    total = 0
    for rel in TARGET_FILES:
        path = ROOT / rel
        if not path.exists():
            print(f"skip (not found): {rel}")
            continue
        text = path.read_text(encoding="utf-8")
        new_text, n1 = re.subn(r"\bOWNER/codemap\b", slug, text)
        new_text, n2 = re.subn(r"\bOWNER\b", owner, new_text)
        n = n1 + n2
        if n:
            path.write_text(new_text, encoding="utf-8", newline="\n")
            print(f"{rel}: {n} replacement(s)")
            total += n
        else:
            print(f"{rel}: no placeholder found")

    print(f"\ndone — {total} replacement(s) across {len(TARGET_FILES)} file(s).")
    print("Review with `git diff`, then commit and push.")


if __name__ == "__main__":
    main()

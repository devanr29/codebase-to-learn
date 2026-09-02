"""Intent capture (spec M6) and the post-commit hook.

Intent is *captured, not inferred* (spec 5.3). Resolution chain, first hit wins,
the source is always recorded:

  1. ``.codemap/pending-intent``  — an agent/user writes the goal before commit
  2. ``CODEMAP_INTENT`` env var
  3. ``codemap note "<text>"``    — writes ``.codemap/note-intent``
  4. the commit message
  5. ``inferred``                 — nothing stated; the report says so plainly

Levels 1 and 3 apply only to HEAD (the commit just made) and are consumed —
deleted — once read.
"""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

from .config import Config

PENDING_FILE = "pending-intent"
NOTE_FILE = "note-intent"

_HOOK_MARKER = "# >>> codemap post-commit >>>"
_HOOK_BODY = """\
# >>> codemap post-commit >>>
# Explains the commit just made and refreshes .codemap/explore.html. Never fails
# a commit: everything is backgrounded and this block always exits 0. Requires
# `codemap` on PATH (pipx/uv install). The explore rebuild is skipped unless
# [explore] rebuild_on_commit is true in .codemap/config.toml.
(
  codemap scan >"$(git rev-parse --git-dir)/codemap-hook.log" 2>&1
  codemap explain HEAD >>"$(git rev-parse --git-dir)/codemap-hook.log" 2>&1
  codemap explore --quiet --if-enabled >>"$(git rev-parse --git-dir)/codemap-hook.log" 2>&1
) &
# <<< codemap post-commit <<<
"""


def _read(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def resolve(cfg: Config, commit_message: str, *, consume: bool) -> tuple[str, str | None]:
    """Return ``(source, text)``. If ``consume``, delete the file-based intents
    after reading (they described this one commit)."""
    pending = cfg.codemap_dir / PENDING_FILE
    note = cfg.codemap_dir / NOTE_FILE

    if consume:
        text = _read(pending)
        if text:
            pending.unlink(missing_ok=True)
            note.unlink(missing_ok=True)
            return "session", text

    env = os.environ.get("CODEMAP_INTENT", "").strip()
    if env:
        return "env", env

    if consume:
        text = _read(note)
        if text:
            note.unlink(missing_ok=True)
            return "note", text

    body = commit_message.strip()
    if body:
        return "commit_message", body

    return "inferred", None


def capture(
    conn: sqlite3.Connection, cfg: Config, sha: str, commit_message: str, *, consume: bool
) -> tuple[str, str | None]:
    source, text = resolve(cfg, commit_message, consume=consume)
    conn.execute(
        "INSERT INTO intents(commit_sha, source, text) VALUES(?,?,?) "
        "ON CONFLICT(commit_sha) DO UPDATE SET source=excluded.source, text=excluded.text",
        (sha, source, text),
    )
    return source, text


def load(conn: sqlite3.Connection, sha: str) -> tuple[str, str | None]:
    row = conn.execute("SELECT source, text FROM intents WHERE commit_sha=?", (sha,)).fetchone()
    return (row["source"], row["text"]) if row else ("inferred", None)


def write_note(cfg: Config, text: str) -> Path:
    cfg.codemap_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.codemap_dir / NOTE_FILE
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- hook


def install_hook(root: Path | str) -> int:
    root = Path(root)
    git_dir = root / ".git"
    if not git_dir.is_dir():
        print("not a git repository — nothing to install")
        return 0
    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook = hooks_dir / "post-commit"

    if hook.exists():
        existing = hook.read_text(encoding="utf-8")
        if _HOOK_MARKER in existing:
            print(f"post-commit hook already contains codemap block ({hook})")
            return 0
        # chain: keep the existing hook, append our block
        new = existing.rstrip() + "\n\n" + _HOOK_BODY
        print(f"appended codemap block to existing post-commit hook ({hook})")
    else:
        new = "#!/bin/sh\n\n" + _HOOK_BODY
        print(f"installed post-commit hook ({hook})")

    hook.write_text(new, encoding="utf-8")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return 0

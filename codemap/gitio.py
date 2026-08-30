"""Thin ``subprocess`` wrappers around git. No GitPython (spec 4).

Every function takes the repo root as its first argument and shells out to the
``git`` on PATH. Read-only; nothing here mutates the repository.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    pass


def _run(root: Path | str, *args: str, check: bool = True, binary: bool = False):
    proc = subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=not binary,
        # decode as UTF-8 regardless of the platform locale — git emits UTF-8
        encoding=None if binary else "utf-8",
        errors=None if binary else "replace",
    )
    if check and proc.returncode != 0:
        err = proc.stderr if isinstance(proc.stderr, str) else proc.stderr.decode("utf-8", "replace")
        raise GitError(f"git {' '.join(args)}: {err.strip()}")
    return proc


def is_repo(root: Path | str) -> bool:
    proc = _run(root, "rev-parse", "--is-inside-work-tree", check=False)
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def rev_parse(root: Path | str, rev: str) -> str:
    return _run(root, "rev-parse", "--verify", f"{rev}^{{commit}}").stdout.strip()


def head(root: Path | str) -> str | None:
    proc = _run(root, "rev-parse", "--verify", "HEAD", check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def parent_of(root: Path | str, sha: str) -> str | None:
    proc = _run(root, "rev-parse", "--verify", f"{sha}^", check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def rev_list(root: Path | str, since: str | None, until: str = "HEAD") -> list[str]:
    """Commits from ``since`` (exclusive) to ``until`` (inclusive), oldest first.

    ``since=None`` means the entire history reachable from ``until``.
    """
    spec = f"{since}..{until}" if since else until
    proc = _run(root, "rev-list", "--reverse", "--first-parent", spec, check=False)
    if proc.returncode != 0:
        return []
    return [line for line in proc.stdout.splitlines() if line]


@dataclass
class CommitMeta:
    sha: str
    parent_sha: str | None
    ts: int
    author: str
    message: str


def commit_meta(root: Path | str, sha: str) -> CommitMeta:
    fmt = "%H%x1f%P%x1f%ct%x1f%an%x1f%B"
    out = _run(root, "show", "-s", f"--format={fmt}", sha).stdout
    h, parents, ts, author, message = out.split("\x1f", 4)
    parent = parents.split()[0] if parents.strip() else None
    return CommitMeta(
        sha=h.strip(),
        parent_sha=parent,
        ts=int(ts),
        author=author.strip(),
        message=message.strip("\n"),
    )


def ls_tree(root: Path | str, sha: str) -> list[str]:
    """All file paths (posix, repo-relative) present in ``sha``."""
    out = _run(root, "ls-tree", "-r", "--name-only", "-z", sha).stdout
    return [p for p in out.split("\0") if p]


def name_status(root: Path | str, a: str, b: str) -> list[tuple[str, str, str | None]]:
    """``git diff --name-status a..b`` as ``(status, path, old_path)`` tuples.

    ``status`` is one of A/M/D/T or R (rename); for R, ``old_path`` is set.
    """
    out = _run(
        root, "diff", "--name-status", "-M", "-z", f"{a}", f"{b}"
    ).stdout
    parts = [p for p in out.split("\0") if p]
    result: list[tuple[str, str, str | None]] = []
    i = 0
    while i < len(parts):
        code = parts[i]
        letter = code[0]
        if letter == "R":
            old_path, new_path = parts[i + 1], parts[i + 2]
            result.append(("R", new_path, old_path))
            i += 3
        else:
            result.append((letter, parts[i + 1], None))
            i += 2
    return result


def show_bytes(root: Path | str, sha: str, path: str) -> bytes | None:
    proc = _run(root, "show", f"{sha}:{path}", check=False, binary=True)
    return proc.stdout if proc.returncode == 0 else None


def worktree_files(root: Path | str) -> list[str]:
    """Tracked + untracked files honoring .gitignore (posix, repo-relative)."""
    out = _run(
        root, "ls-files", "--cached", "--others", "--exclude-standard", "-z"
    ).stdout
    return sorted({p for p in out.split("\0") if p})

"""File walking and language detection (spec M1).

Three entry points, all returning ``FileEntry`` lists (posix paths, repo-relative):

* ``iter_worktree`` — the working tree, honoring ``.gitignore`` via git itself
  when possible, else a ``pathspec`` fallback so a non-git directory still works.
* ``iter_commit`` — the files present in a specific commit (``git ls-tree``).

Both then apply the hard exclusion list, the config ``ignore`` patterns, and the
language registry (extension must be known; ``config.languages`` may narrow it).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pathspec

from . import gitio
from .config import HARD_EXCLUDES, Config
from .languages import registry


@dataclass(frozen=True)
class FileEntry:
    path: str      # posix, repo-relative
    lang: str
    tier: int


def _hard_excluded(path: str) -> bool:
    return any(part in HARD_EXCLUDES for part in path.split("/"))


def _config_spec(cfg: Config) -> pathspec.PathSpec | None:
    if not cfg.ignore:
        return None
    return pathspec.PathSpec.from_lines("gitignore", cfg.ignore)


def _accept(path: str, cfg: Config, ignore_spec: pathspec.PathSpec | None):
    if _hard_excluded(path):
        return None
    if ignore_spec is not None and ignore_spec.match_file(path):
        return None
    spec = registry.spec_for_path(path)
    if spec is None:
        return None
    if cfg.languages and spec.name not in cfg.languages:
        return None
    return spec


def _entries(paths: list[str], cfg: Config) -> list[FileEntry]:
    ignore_spec = _config_spec(cfg)
    out: list[FileEntry] = []
    for path in paths:
        spec = _accept(path, cfg, ignore_spec)
        if spec is not None:
            out.append(FileEntry(path=path, lang=spec.name, tier=spec.tier))
    return sorted(out, key=lambda e: e.path)


def iter_commit(root: Path | str, sha: str, cfg: Config) -> list[FileEntry]:
    return _entries(gitio.ls_tree(root, sha), cfg)


def iter_worktree(cfg: Config) -> list[FileEntry]:
    root = cfg.root
    if gitio.is_repo(root):
        return _entries(gitio.worktree_files(root), cfg)
    return _entries(_walk_plain(root), cfg)


def _walk_plain(root: Path) -> list[str]:
    """Fallback walk for a non-git directory: prune hard excludes, honor a
    top-level ``.gitignore`` if one exists."""
    gi = root / ".gitignore"
    ignore = (
        pathspec.PathSpec.from_lines("gitignore", gi.read_text(encoding="utf-8").splitlines())
        if gi.exists()
        else None
    )
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in HARD_EXCLUDES]
        for fn in filenames:
            abs_path = Path(dirpath) / fn
            rel = abs_path.relative_to(root).as_posix()
            if ignore is not None and ignore.match_file(rel):
                continue
            found.append(rel)
    return found


def read_worktree_bytes(root: Path | str, rel_path: str) -> bytes | None:
    p = Path(root) / rel_path
    try:
        return p.read_bytes()
    except OSError:
        return None

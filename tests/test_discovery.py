"""M1: file walking honors .gitignore, hard exclusions, and the language registry."""

from __future__ import annotations

import subprocess

from codemap import config, discovery


def _git(root, *args):
    subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True, check=True)


def test_worktree_walk_filters(tmp_path):
    root = tmp_path
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@e.com")
    _git(root, "config", "user.name", "t")

    (root / "pkg").mkdir()
    (root / "pkg" / "a.py").write_text("def a():\n    return 1\n")
    (root / "pkg" / "b.ts").write_text("export function b() { return 2; }\n")
    (root / "README.md").write_text("# not source\n")
    (root / "data.json").write_text("{}\n")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "dep.js").write_text("module.exports = 1;\n")
    (root / ".venv").mkdir()
    (root / ".venv" / "lib.py").write_text("x = 1\n")
    (root / ".gitignore").write_text("ignored/\n")
    (root / "ignored").mkdir()
    (root / "ignored" / "skip.py").write_text("y = 2\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")

    cfg = config.load(root)
    paths = {e.path for e in discovery.iter_worktree(cfg)}
    assert paths == {"pkg/a.py", "pkg/b.ts"}


def test_config_ignore_and_language_narrowing(tmp_path):
    root = tmp_path
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@e.com")
    _git(root, "config", "user.name", "t")
    (root / "keep.py").write_text("def k():\n    return 1\n")
    (root / "gen.py").write_text("def g():\n    return 1\n")
    (root / "app.ts").write_text("export const x = 1;\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")

    cfg = config.load(root)
    cfg.ignore = ["gen.py"]
    cfg.languages = ["python"]
    paths = {e.path for e in discovery.iter_worktree(cfg)}
    assert paths == {"keep.py"}

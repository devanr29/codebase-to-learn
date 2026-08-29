"""Construct a real git repository with a scripted commit sequence (spec section 9).

This is the differ's contract. Each commit carries an ``expected`` set of
``(change_type, subject[, severity])`` tuples that ``test_semdiff.py`` asserts
against. ``build_repo`` is pure I/O — it writes files and shells out to ``git``.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- trees

C1_FILES: dict[str, str] = {
    "app/core.py": '''\
from app.util import clean


def load(path):
    """Load raw records from a path."""
    return clean(read(path))


def read(path):
    return open(path).read()


class Store:
    def put(self, key, value):
        return save(key, value)
''',
    "app/util.py": '''\
def clean(text):
    return text.strip()


def save(key, value):
    return True
''',
    "web/api.ts": '''\
import { load } from "./loader";

export function handler(req: string): string {
  return load(req);
}

export class Router {
  route(path: string): string {
    return dispatch(path);
  }
}
''',
    "web/loader.ts": '''\
export function load(req: string): string {
  return req.trim();
}

export function dispatch(path: string): string {
  return path;
}
''',
}

# commit 2 — add a function with a caller
C2_CORE = '''\
from app.util import clean


def load(path):
    """Load raw records from a path."""
    return clean(read(path))


def read(path):
    return open(path).read()


class Store:
    def put(self, key, value):
        validate(value)
        return save(key, value)


def validate(value):
    return value is not None
'''

# commit 3 — change a signature (structural: save has a caller)
C3_UTIL = '''\
def clean(text):
    return text.strip()


def save(key, value, ttl=0):
    return True
'''

# commit 4 — change a body only (behavioral: clean has a caller)
C4_UTIL = '''\
def clean(text):
    return text.strip().lower()


def save(key, value, ttl=0):
    return True
'''

# commit 5 — comments only (cosmetic): read() in core.py, dispatch() in loader.ts
C5_CORE = '''\
from app.util import clean


def load(path):
    """Load raw records from a path."""
    return clean(read(path))


def read(path):
    # open the file and read the whole thing
    return open(path).read()


class Store:
    def put(self, key, value):
        validate(value)
        return save(key, value)


def validate(value):
    return value is not None
'''
C5_LOADER = '''\
export function load(req: string): string {
  return req.trim();
}

export function dispatch(path: string): string {
  // identity mapping for now
  return path;
}
'''

# commit 6 — rename a symbol and update all references (clean -> sanitize)
C6_UTIL = '''\
def sanitize(text):
    return text.strip().lower()


def save(key, value, ttl=0):
    return True
'''
C6_CORE = '''\
from app.util import sanitize


def load(path):
    """Load raw records from a path."""
    return sanitize(read(path))


def read(path):
    # open the file and read the whole thing
    return open(path).read()


class Store:
    def put(self, key, value):
        validate(value)
        return save(key, value)


def validate(value):
    return value is not None
'''

# commit 7 — move a symbol to another file, unchanged
C7_UTIL = '''\
def sanitize(text):
    return text.strip().lower()
'''
C7_STORE = '''\
def save(key, value, ttl=0):
    return True
'''

# commit 9 — add an external dependency
C9_CORE = '''\
import requests

from app.util import sanitize


def load(path):
    """Load raw records from a path."""
    return sanitize(read(path))


def read(path):
    return requests.get(path).text


class Store:
    def put(self, key, value):
        validate(value)
        return save(key, value)


def validate(value):
    return value is not None
'''

# commit 10 — a syntax error in one file (must not crash the run)
C10_LOADER = '''\
export function load(req: string): string {
  return req.trim(
}

export function dispatch(path: string): string {
  // identity mapping for now
  return path;
}
'''


@dataclass
class Commit:
    tag: str
    message: str
    writes: dict[str, str] = field(default_factory=dict)
    deletes: list[str] = field(default_factory=list)
    expected: set[tuple] = field(default_factory=set)
    sha: str = ""


COMMITS: list[Commit] = [
    Commit(
        tag="c1-initial",
        message="M-fixture c1: initial multi-file project (python + typescript)",
        writes=dict(C1_FILES),
        expected={
            ("file_added", "app/core.py"),
            ("file_added", "app/util.py"),
            ("file_added", "web/api.ts"),
            ("file_added", "web/loader.ts"),
            ("symbol_added", "app/core.py::load"),
            ("symbol_added", "app/core.py::read"),
            ("symbol_added", "app/core.py::Store"),
            ("symbol_added", "app/core.py::Store.put"),
            ("symbol_added", "app/util.py::clean"),
            ("symbol_added", "app/util.py::save"),
            ("symbol_added", "web/api.ts::handler"),
            ("symbol_added", "web/api.ts::Router"),
            ("symbol_added", "web/api.ts::Router.route"),
            ("symbol_added", "web/loader.ts::load"),
            ("symbol_added", "web/loader.ts::dispatch"),
        },
    ),
    Commit(
        tag="c2-add-fn",
        message="M-fixture c2: add validate() with a caller",
        writes={"app/core.py": C2_CORE},
        expected={
            ("symbol_added", "app/core.py::validate"),
            ("body_changed", "app/core.py::Store.put", "behavioral"),
        },
    ),
    Commit(
        tag="c3-signature",
        message="M-fixture c3: add ttl param to save() (structural)",
        writes={"app/util.py": C3_UTIL},
        expected={
            ("signature_changed", "app/util.py::save", "structural"),
        },
    ),
    Commit(
        tag="c4-body",
        message="M-fixture c4: clean() lowercases too (behavioral)",
        writes={"app/util.py": C4_UTIL},
        expected={
            ("body_changed", "app/util.py::clean", "behavioral"),
        },
    ),
    Commit(
        tag="c5-cosmetic",
        message="M-fixture c5: reformat + comments only (cosmetic)",
        writes={"app/core.py": C5_CORE, "web/loader.ts": C5_LOADER},
        expected={
            ("body_changed", "app/core.py::read", "cosmetic"),
            ("body_changed", "web/loader.ts::dispatch", "cosmetic"),
        },
    ),
    Commit(
        tag="c6-rename",
        message="M-fixture c6: rename clean() -> sanitize(), update refs",
        writes={"app/util.py": C6_UTIL, "app/core.py": C6_CORE},
        expected={
            ("renamed", "app/util.py::clean -> sanitize", "cosmetic"),
            ("body_changed", "app/core.py::load", "cosmetic"),
            ("import_removed", "app/core.py"),
            ("import_added", "app/core.py"),
        },
    ),
    Commit(
        tag="c7-move",
        message="M-fixture c7: move save() to app/store.py, unchanged",
        writes={"app/util.py": C7_UTIL, "app/store.py": C7_STORE},
        expected={
            ("file_added", "app/store.py"),
            ("symbol_moved", "app/util.py::save -> app/store.py::save", "structural"),
        },
    ),
    Commit(
        tag="c8-delete",
        message="M-fixture c8: delete web/api.ts",
        deletes=["web/api.ts"],
        expected={
            ("file_removed", "web/api.ts"),
            ("symbol_removed", "web/api.ts::handler"),
            ("symbol_removed", "web/api.ts::Router"),
            ("symbol_removed", "web/api.ts::Router.route"),
        },
    ),
    Commit(
        tag="c9-dependency",
        message="M-fixture c9: read() uses requests (new dependency)",
        writes={"app/core.py": C9_CORE},
        expected={
            ("dependency_added", "requests", "structural"),
            ("import_added", "app/core.py"),
            ("body_changed", "app/core.py::read", "behavioral"),
        },
    ),
    Commit(
        tag="c10-syntaxerror",
        message="M-fixture c10: introduce a syntax error (must not crash)",
        writes={"web/loader.ts": C10_LOADER},
        expected={
            ("parse_error", "web/loader.ts"),
        },
    ),
]


@dataclass
class FixtureRepo:
    path: Path
    commits: list[Commit]

    def sha(self, tag: str) -> str:
        for c in self.commits:
            if c.tag == tag:
                return c.sha
        raise KeyError(tag)


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def build_repo(dest: Path) -> FixtureRepo:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    _git(dest, "init", "-q")
    _git(dest, "config", "user.email", "fixture@example.com")
    _git(dest, "config", "user.name", "codemap fixtures")
    _git(dest, "config", "commit.gpgsign", "false")

    commits = [
        Commit(c.tag, c.message, dict(c.writes), list(c.deletes), set(c.expected))
        for c in COMMITS
    ]
    for c in commits:
        for rel, content in c.writes.items():
            p = dest / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8", newline="\n")
        for rel in c.deletes:
            (dest / rel).unlink(missing_ok=True)
        _git(dest, "add", "-A")
        _git(dest, "commit", "-q", "-m", c.message)
        c.sha = _git(dest, "rev-parse", "HEAD")

    return FixtureRepo(path=dest, commits=commits)


if __name__ == "__main__":
    import sys
    import tempfile

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp())
    repo = build_repo(out)
    for c in repo.commits:
        print(f"{c.sha[:7]}  {c.tag:18}  {c.message}")
    print(f"\nrepo at {repo.path}")

"""A call made on an imported module (``config.load()``, ``_model.build()``) resolves
into exactly that module -- however the module was imported.

Regressions from dogfooding codemap on itself: ``from . import a, b, c`` only ever
looked at ``a``, and ``from .site import model as _model`` resolved to the package
rather than ``model.py``, so ``_model.build`` had no caller and ``config.load`` was
wrongly linked to an unrelated ``intent.load``.
"""

from __future__ import annotations

import subprocess

import pytest

from codemap import config, db, impact, indexer

FILES = {
    "pkg/__init__.py": '__version__ = "1"\n',
    "pkg/config.py": "def load():\n    return 1\n\n\ndef get():\n    return 2\n",
    "pkg/intent.py": "def load():\n    return 3\n",
    "pkg/db.py": "def connect():\n    return 4\n",
    "pkg/site/__init__.py": "",
    "pkg/site/model.py": "def build():\n    return 5\n",
    "pkg/site/brief.py": "def build():\n    return 6\n",
    "pkg/cli.py": (
        "from . import __version__, config, db\n"
        "from .site import model as _model\n"
        "from .site import brief as _brief\n"
        "from . import intent\n\n\n"
        "def use_config():\n    return config.load()\n\n\n"
        "def use_config_get():\n    return config.get()\n\n\n"
        "def use_db():\n    return db.connect()\n\n\n"
        "def use_model():\n    return _model.build()\n\n\n"
        "def use_brief():\n    return _brief.build()\n\n\n"
        "def use_intent():\n    return intent.load()\n\n\n"
        "def use_a_dict(body):\n    return body.get()\n"
    ),
}


def _git(root, *args):
    subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True, check=True)


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    root = tmp_path_factory.mktemp("modalias")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@e.com")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")
    for rel, text in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(root), capture_output=True, text=True
    ).stdout.strip()
    cfg = config.load(root)
    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    indexer.scan(conn, cfg, until=sha)
    return impact.call_graph(conn, sha)


def _callees(g, fn):
    return {dst: g[f"pkg/cli.py::{fn}"][dst]["confidence"] for dst in g.successors(f"pkg/cli.py::{fn}")}


def test_multi_name_import_resolves_each_module(graph):
    assert _callees(graph, "use_config") == {"pkg/config.py::load": impact.INFERRED}
    assert _callees(graph, "use_db") == {"pkg/db.py::connect": impact.INFERRED}


def test_aliased_submodule_resolves_into_that_module_only(graph):
    # brief.py defines a `build` too and cli.py imports it -- import evidence alone
    # would have made both AMBIGUOUS; the alias pins each call to its own module.
    assert _callees(graph, "use_model") == {"pkg/site/model.py::build": impact.INFERRED}
    assert _callees(graph, "use_brief") == {"pkg/site/brief.py::build": impact.INFERRED}


def test_same_named_function_in_another_imported_module_is_not_linked(graph):
    assert _callees(graph, "use_config") == {"pkg/config.py::load": impact.INFERRED}
    assert _callees(graph, "use_intent") == {"pkg/intent.py::load": impact.INFERRED}


def test_module_alias_outranks_the_method_stoplist(graph):
    # `.get` is on the stop-list because a dict's .get() is everywhere -- but
    # `config` is provably the module here.
    assert _callees(graph, "use_config_get") == {"pkg/config.py::get": impact.INFERRED}
    # ...while the same name on an unknown local is still never linked
    assert _callees(graph, "use_a_dict") == {}

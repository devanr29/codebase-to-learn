"""codemap/site/architecture.py — layer placement, components, stores/services,
links, actors, manifests and authored overrides, over already-shaped
``files``/``file_edges``/``folders``/``entry_points``/``nodes`` dicts (no git,
no SQLite; see ``model.py::build()`` for the shapes these mirror)."""

from __future__ import annotations

import json

from codemap import config
from codemap.site import architecture, folders


def mk_file(fi, path, *, loc=20, lang="python", symbols=None, deps=None):
    return {
        "fi": fi,
        "path": path,
        "lang": lang,
        "tier": 1,
        "loc": loc,
        "module": path.split("/")[0] if "/" in path else "(root)",
        "symbols": [fi] if symbols is None else symbols,
        "imports": [],
        "deps": [{"name": d, "kind": "stdlib" if d == "sqlite3" else "third_party"} for d in deps or []],
    }


def mk_nodes(files):
    return [{"i": f["fi"], "key": f"{f['path']}::fn", "file": f["path"]} for f in files]


def run(files, edges=(), entry_points=(), **kw):
    edges = [{"s": s, "t": t} for s, t in edges]
    fl = folders.build(files, edges, list(entry_points))
    return architecture.build(files, edges, fl, list(entry_points), mk_nodes(files), **kw)


def comp(arch, cid):
    return next(c for c in arch["components"] if c["id"] == cid)


def layer_of(arch, path):
    return next(c["layer"] for c in arch["components"]
                if c["path"] == path or any(p == path for p in _paths(arch, c)))


_FILES_BY_ARCH: dict[int, list[dict]] = {}


def _paths(arch, c):
    return [_FILES_BY_ARCH[id(arch)][fi]["path"] for fi in c["files"]]


def flask_app():
    # two files per folder: folders.py folds a one-file folder into its parent
    files = [
        mk_file(0, "app/__init__.py", loc=2, symbols=[]),
        mk_file(1, "app/routes/users.py", deps=["flask"]),
        mk_file(2, "app/views/pages.py", deps=["jinja2"]),
        mk_file(3, "app/services/billing.py", deps=["stripe"]),
        mk_file(4, "app/models/user.py", deps=["sqlalchemy", "psycopg2"]),
        mk_file(5, "app/utils/dates.py"),
        mk_file(6, "tests/test_users.py", deps=["pytest"]),
        mk_file(7, "app/routes/orders.py", deps=["flask"]),
        mk_file(8, "app/views/emails.py", deps=["jinja2"]),
        mk_file(9, "app/services/orders.py"),
        mk_file(10, "app/models/order.py", deps=["sqlalchemy"]),
        mk_file(11, "app/utils/money.py"),
        mk_file(12, "tests/test_orders.py", deps=["pytest"]),
    ]
    edges = [(1, 3), (1, 2), (3, 4), (4, 1), (1, 4), (3, 5), (6, 1)]
    eps = [{"kind": "route", "detail": "GET /users", "node": 1}]
    arch = run(files, edges, eps)
    _FILES_BY_ARCH[id(arch)] = files
    return files, arch


# --------------------------------------------------------------- placement


def test_flask_app_lands_in_the_expected_layers():
    _files, arch = flask_app()
    assert layer_of(arch, "app/routes/users.py") == "entry"
    assert layer_of(arch, "app/views/pages.py") == "views"
    assert layer_of(arch, "app/services/billing.py") == "logic"
    assert layer_of(arch, "app/models/user.py") == "data"
    assert layer_of(arch, "app/utils/dates.py") == "shared"
    assert layer_of(arch, "tests/test_users.py") == "tests"

    routes = comp(arch, "entry:app/routes")
    assert routes["title"] == "routes"
    assert routes["kind"] == "folder"
    assert routes["confidence"] == "strong"
    # evidence counts how many of the component's files share a reason
    assert routes["evidence"] == ["folder named routes/ (2 files)", "imports flask (2 files)", "HTTP route"]
    assert routes["entries"] == [1]
    assert [t["label"] for t in routes["tech"]] == ["Flask"]

    # every layer is listed, in diagram order, with its components
    assert [layer["id"] for layer in arch["layers"]] == architecture.LAYER_IDS
    assert next(layer for layer in arch["layers"] if layer["id"] == "api")["components"] == []


def test_symbol_less_init_rides_along_instead_of_becoming_a_box():
    _files, arch = flask_app()
    assert not any(c["path"].endswith("__init__.py") for c in arch["components"])
    assert any(0 in c["extra_files"] for c in arch["components"])   # still mapped, for links


def test_folder_token_beats_a_stem_token_and_a_vendor_import():
    files = [
        mk_file(0, "pkg/site/model.py", deps=["sqlite3"]),
        mk_file(1, "pkg/site/render.py"),
    ]
    arch = run(files)
    assert [c["id"] for c in arch["components"]] == ["views:pkg/site"]   # one box, not split


def test_tooling_config_files_land_in_shared():
    # a config file has no folder-token evidence of its own (it usually sits
    # at the repo root) and no imports worth scoring — only its filename says
    # what it is. Also covers a bare dotfile (".eslintrc", one dot, no further
    # suffix) that a naive stem = name.rsplit(".", 1)[0] used to reduce to "".
    files = [
        mk_file(0, "tsconfig.json"),
        mk_file(1, ".eslintrc.js"),
        mk_file(2, ".eslintrc"),
        mk_file(3, "next.config.js"),
        mk_file(4, "pyproject.toml"),
    ]
    arch = run(files)
    _FILES_BY_ARCH[id(arch)] = files
    for f in files:
        assert layer_of(arch, f["path"]) == "shared", f["path"]


def test_vendor_import_decides_only_when_nothing_else_does():
    files = [mk_file(0, "pkg/thing.py", deps=["sqlalchemy"]), mk_file(1, "pkg/other.py")]
    arch = run(files)
    thing = next(c for c in arch["components"] if 0 in c["files"])
    assert thing["layer"] == "data"
    assert thing["confidence"] == "weak"
    assert "imports sqlalchemy" in thing["evidence"]


def test_pervasive_import_is_not_a_layer_signal_but_still_reaches_the_store():
    files = [mk_file(i, f"pkg/mod{i}.py", deps=["sqlite3"]) for i in range(6)]
    arch = run(files)
    assert {c["layer"] for c in arch["components"]} == {"logic"}
    sqlite = next(s for s in arch["stores"] if s["id"] == "sqlite")
    assert len(sqlite["components"]) == 1          # one grouped pkg component
    assert all(t["label"] != "SQLite" for c in arch["components"] for t in c["tech"])


def test_no_signal_falls_back_to_weak_logic_with_a_reason():
    arch = run([mk_file(0, "pkg/thing.py"), mk_file(1, "pkg/other.py")])
    (c,) = arch["components"]
    assert c["layer"] == "logic"
    assert c["confidence"] == "weak"
    assert c["evidence"][0].startswith("no clear signal")


def test_mixed_role_folder_is_drawn_file_by_file():
    files = [
        mk_file(0, "tool/cli.py", deps=["argparse"]),
        mk_file(1, "tool/db.py", deps=["sqlite3"]),
        mk_file(2, "tool/indexer.py"),
        mk_file(3, "tool/config.py"),
    ]
    eps = [{"kind": "main", "detail": "__main__ @ tool/cli.py", "node": 0}]
    arch = run(files, entry_points=eps)
    ids = {c["id"] for c in arch["components"]}
    assert ids == {"entry:tool/cli.py", "data:tool/db.py", "logic:tool/indexer.py", "shared:tool/config.py"}
    assert comp(arch, "entry:tool/cli.py")["title"] == "cli.py"
    assert arch["actors"] == [{"id": "terminal", "label": "Terminal", "kind": "cli",
                               "entries": 1, "components": ["entry:tool/cli.py"]}]


# --------------------------------------------------------------- stores, services, stack


def test_stores_services_and_stack_summary():
    _files, arch = flask_app()
    pg = next(s for s in arch["stores"] if s["id"] == "postgresql")
    assert pg["label"] == "PostgreSQL" and pg["kind"] == "database"
    assert pg["via"] == ["psycopg2"]
    assert pg["components"] == ["data:app/models"]
    assert comp(arch, "data:app/models")["stores"] == ["postgresql"]

    stripe = next(s for s in arch["services"] if s["id"] == "stripe")
    assert stripe["components"] == ["logic:app/services"]

    assert arch["stack"][0] == "Python"
    assert "Flask" in arch["stack"] and "PostgreSQL" in arch["stack"] and "Stripe" in arch["stack"]
    assert "pytest" not in arch["stack"]          # tests don't define the stack


# --------------------------------------------------------------- links + actors


def test_links_carry_counts_and_direction():
    _files, arch = flask_app()
    by = {(link["s"], link["t"]): link for link in arch["links"]}
    assert by[("entry:app/routes", "logic:app/services")]["dir"] == "down"
    assert by[("entry:app/routes", "data:app/models")]["dir"] == "down"       # skips Logic
    assert by[("data:app/models", "entry:app/routes")]["dir"] == "up"         # wrong way
    assert by[("logic:app/services", "shared:app/utils")]["dir"] == "side"
    assert by[("tests:tests", "entry:app/routes")]["dir"] == "side"
    assert all(link["n"] >= 1 for link in arch["links"])


def test_views_and_api_share_a_rank_so_their_links_are_same_not_down():
    """Views and API sit side by side in one band row, so an import between them is
    `same`, not `down`. explore.js draws those sideways across the seam between the two
    bands. If someone "fixes" a missing arrow by giving the two layers different ranks,
    the page would stack them vertically and every band arrow would point at the wrong
    band -- see tests/test_arch_arrows.py."""
    files = [
        mk_file(0, "app/views/pages.py", deps=["jinja2"]),
        mk_file(1, "app/views/emails.py", deps=["jinja2"]),
        mk_file(2, "app/api/client.py"),
        mk_file(3, "app/api/schemas.py"),
    ]
    arch = run(files, [(0, 2), (1, 3)])
    layer = {c["id"]: c["layer"] for c in arch["components"]}
    assert architecture.RANK["views"] == architecture.RANK["api"]
    assert {(layer[link["s"]], layer[link["t"]], link["dir"]) for link in arch["links"]} == {
        ("views", "api", "same")
    }


def test_actors_come_from_entry_point_kinds():
    _files, arch = flask_app()
    assert [a["id"] for a in arch["actors"]] == ["internet"]
    assert arch["actors"][0]["components"] == ["entry:app/routes"]


def test_a_script_main_guard_in_the_shared_layer_is_still_a_terminal_actor():
    """A one-off script's __main__ guard is Terminal-worthy wherever the file
    landed — most scripts have no other code importing them, so they land in
    the shared/cross-cutting side panel rather than "entry", and used to be
    silently dropped as an actor entirely because of that."""
    files = [mk_file(0, "scripts/release.py")]
    eps = [{"kind": "main", "detail": "__main__ @ scripts/release.py", "node": 0}]
    arch = run(files, entry_points=eps)
    assert comp(arch, "shared:scripts")["layer"] == "shared"
    terminal = next(a for a in arch["actors"] if a["id"] == "terminal")
    assert terminal["components"] == ["shared:scripts"]


def test_a_scheduler_job_in_a_test_fixture_is_not_an_actor():
    """The one case that still must not produce an actor: a test file that
    merely calls the scheduler as part of its own setup, not the app's."""
    files = [mk_file(0, "tests/test_jobs.py")]
    eps = [{"kind": "task", "detail": "@shared_task", "node": 0}]
    arch = run(files, entry_points=eps)
    assert comp(arch, "tests:tests")["layer"] == "tests"
    assert arch["actors"] == []


def test_empty_repo_yields_empty_lists():
    arch = architecture.build([], [], [], [], [])
    assert arch["components"] == [] and arch["links"] == [] and arch["actors"] == []
    assert arch["stores"] == [] and arch["services"] == [] and arch["stack"] == []
    assert json.loads(json.dumps(arch)) == arch


# --------------------------------------------------------------- manifests


def test_manifests_add_declared_stores_and_frameworks():
    blobs = {
        "docker-compose.yml": b"services:\n  db:\n    image: postgres:16\n  cache:\n    image: 'redis:7-alpine'\n",
        "requirements.txt": b"# pinned\nDjango>=4.2\npsycopg2-binary==2.9.9\n-r base.txt\n",
        "web/package.json": b"{ not json",
        "node_modules/x/package.json": b'{"dependencies": {"express": "1"}}',
    }
    files = [mk_file(0, "app/models/user.py", deps=["psycopg2"]), mk_file(1, "app/models/order.py")]
    arch = run(files, manifest_paths=sorted(blobs), read_bytes=blobs.get)

    pg = next(s for s in arch["stores"] if s["id"] == "postgresql")
    assert pg["components"] == ["data:app/models"]                 # imported AND declared
    assert set(pg["declared_in"]) == {"docker-compose.yml", "requirements.txt"}
    redis = next(s for s in arch["stores"] if s["id"] == "redis")
    assert redis["components"] == [] and redis["source"] == "declared"
    assert redis["declared_in"] == ["docker-compose.yml"]
    assert arch["declared_only"] == [{"label": "Django", "package": "django", "source": "requirements.txt"}]


def test_manifest_signals_parses_pyproject_and_go_mod():
    blobs = {
        "pyproject.toml": b'[project]\ndependencies = ["fastapi>=0.110", "uvicorn[standard]"]\n',
        "go.mod": b"module x\n\nrequire (\n\tgithub.com/gin-gonic/gin v1.9.1\n)\n",
    }
    sigs = architecture.manifest_signals(sorted(blobs), blobs.get)
    names = {s["name"] for s in sigs}
    assert {"fastapi", "uvicorn", "github.com/gin-gonic/gin"} <= names


# --------------------------------------------------------------- authored overrides


def test_overrides_rename_relayer_split_and_add_stores():
    files, _ = flask_app()
    overrides = architecture.clean_overrides({
        "summary": "A Flask app.",
        "layers": {"logic": {"title": "Business logic"}, "nonsense": {"title": "x"}},
        "components": {
            "app/services": {"title": "Billing rules", "body": "Charges cards.", "tech": ["Celery"]},
            "app/utils/dates.py": {"layer": "logic"},
            "does/not/exist": {"title": "ignored"},
        },
        "stores": [{"id": "files", "label": "Uploads bucket", "kind": "storage", "via": ["app/services"]}],
    })
    edges = [{"s": 1, "t": 3}]
    eps = [{"kind": "route", "detail": "GET /users", "node": 1}]
    arch = architecture.build(files, edges, folders.build(files, edges, eps), eps, mk_nodes(files),
                              overrides=overrides)

    assert arch["summary"] == "A Flask app."
    assert arch["authored"] is True
    assert next(layer for layer in arch["layers"] if layer["id"] == "logic")["title"] == "Business logic"

    svc = comp(arch, "logic:app/services")
    assert svc["title"] == "Billing rules" and svc["body"] == "Charges cards."
    assert svc["tech"][0] == {"label": "Celery", "package": None}
    assert svc["authored"] is True
    assert "files" in svc["stores"]

    dates = comp(arch, "logic:app/utils/dates.py")
    assert dates["evidence"][0] == "placed here by architecture.json"
    assert "folder named utils/" in dates["evidence"]          # derived reasons stay visible

    misc = architecture.build(
        [mk_file(0, "pkg/a.py"), mk_file(1, "pkg/b.py")], [], [], [], mk_nodes([mk_file(0, "pkg/a.py"), mk_file(1, "pkg/b.py")]),
        overrides={"components": {"pkg/a.py": {"layer": "data"}}},
    )
    moved = next(c for c in misc["components"] if c["path"] == "pkg/a.py")
    assert moved["evidence"] == ["placed here by architecture.json"]   # fallback reason dropped
    assert moved["confidence"] == "strong"
    assert dates["authored"] is True

    bucket = next(s for s in arch["stores"] if s["id"] == "files")
    assert bucket["label"] == "Uploads bucket" and bucket["components"] == ["logic:app/services"]
    assert not any(c["title"] == "ignored" for c in arch["components"])


def test_load_absent_malformed_then_valid(tmp_path):
    cfg = config.load(tmp_path)
    cfg.codemap_dir.mkdir(exist_ok=True)
    f = cfg.codemap_dir / architecture.ARCHITECTURE_FILE
    assert architecture.load(cfg) is None                       # absent
    f.write_text("{ nope", encoding="utf-8")
    assert architecture.load(cfg) is None                       # malformed
    f.write_text(json.dumps({"components": {"x": "not an object"}, "stores": "nope"}), encoding="utf-8")
    assert architecture.load(cfg) is None                       # nothing usable
    f.write_text(json.dumps({"components": {"/app/api/": {"layer": "api", "title": " API "}}}), encoding="utf-8")
    assert architecture.load(cfg) == {"components": {"app/api": {"title": "API", "layer": "api"}}}

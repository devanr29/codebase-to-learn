"""M1 acceptance: extracted symbol keys match a hand-written golden list for
both languages, on the fixture repo's initial commit."""

from __future__ import annotations

from codemap import config, discovery, gitio
from codemap.languages.registry import spec_for_path
from codemap.parsing import parse_source

GOLDEN_C1_KEYS = {
    "app/core.py::load",
    "app/core.py::read",
    "app/core.py::Store",
    "app/core.py::Store.put",
    "app/util.py::clean",
    "app/util.py::save",
    "web/api.ts::handler",
    "web/api.ts::Router",
    "web/api.ts::Router.route",
    "web/loader.ts::load",
    "web/loader.ts::dispatch",
}


def _parse_commit(repo_path, sha):
    cfg = config.load(repo_path)
    parsed = {}
    for entry in discovery.iter_commit(repo_path, sha, cfg):
        blob = gitio.show_bytes(repo_path, sha, entry.path)
        parsed[entry.path] = parse_source(entry.path, blob, spec_for_path(entry.path))
    return parsed


def test_c1_symbol_keys_match_golden(fixture_repo):
    parsed = _parse_commit(fixture_repo.path, fixture_repo.sha("c1-initial"))
    keys = {s.key for pf in parsed.values() for s in pf.symbols}
    assert keys == GOLDEN_C1_KEYS


def test_c1_kinds_and_signatures(fixture_repo):
    parsed = _parse_commit(fixture_repo.path, fixture_repo.sha("c1-initial"))
    by_key = {s.key: s for pf in parsed.values() for s in pf.symbols}

    assert by_key["app/core.py::Store"].kind == "class"
    assert by_key["app/core.py::Store.put"].kind == "method"
    assert by_key["app/core.py::load"].kind == "function"
    assert by_key["app/core.py::load"].signature == "load(path)"
    assert by_key["app/core.py::load"].docstring == "Load raw records from a path."

    assert by_key["web/api.ts::Router"].kind == "class"
    assert by_key["web/api.ts::Router.route"].kind == "method"
    assert by_key["web/api.ts::handler"].signature == "handler(req: string): string"


def test_c1_imports_and_refs(fixture_repo):
    parsed = _parse_commit(fixture_repo.path, fixture_repo.sha("c1-initial"))

    core = parsed["app/core.py"]
    assert [i.raw for i in core.imports] == ["from app.util import clean"]
    # load() calls clean() and read()
    load_targets = {r.target_name for r in core.refs if r.from_key == "app/core.py::load"}
    assert {"clean", "read"} <= load_targets

    api = parsed["web/api.ts"]
    assert [i.raw for i in api.imports] == ['import { load } from "./loader";']
    route_targets = {r.target_name for r in api.refs if r.from_key == "web/api.ts::Router.route"}
    assert "dispatch" in route_targets


def test_every_file_parses_clean_at_c1(fixture_repo):
    parsed = _parse_commit(fixture_repo.path, fixture_repo.sha("c1-initial"))
    assert all(pf.ok for pf in parsed.values())
    assert set(parsed) == {"app/core.py", "app/util.py", "web/api.ts", "web/loader.ts"}

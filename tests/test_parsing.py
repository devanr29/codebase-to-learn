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


# --------------------------------------------------------- M19: default_export


def _parse_tsx(src: bytes, path: str = "app/screen.tsx"):
    spec = spec_for_path(path)
    return parse_source(path, src, spec)


def test_default_export_named_function_declaration():
    pf = _parse_tsx(b"export default function BudgetScreen() { return null; }")
    assert pf.default_export == "BudgetScreen"


def test_default_export_named_class_declaration():
    pf = _parse_tsx(b"export default class BudgetScreen {}")
    assert pf.default_export == "BudgetScreen"


def test_default_export_re_export_identifier():
    pf = _parse_tsx(b"function BudgetScreen() {}\nexport default BudgetScreen;")
    assert pf.default_export == "BudgetScreen"


def test_default_export_const_arrow_then_export():
    pf = _parse_tsx(b"const BudgetScreen = () => null;\nexport default BudgetScreen;")
    assert pf.default_export == "BudgetScreen"


def test_default_export_wrapped_in_single_arg_call():
    pf = _parse_tsx(b"function BudgetScreen() {}\nexport default memo(BudgetScreen);")
    assert pf.default_export == "BudgetScreen"


def test_default_export_anonymous_function_is_none():
    pf = _parse_tsx(b"export default function () { return null; }")
    assert pf.default_export is None


def test_default_export_absent_for_python():
    pf = parse_source("app/core.py", b"def load(path):\n    return path\n", spec_for_path("app/core.py"))
    assert pf.default_export is None


# -------------------------------------------------- M19: JSX composition edges


def test_jsx_component_reference_becomes_a_call_ref():
    src = b"""
function BudgetOverviewScreen() {
  return <View><TodayCard /><WalletStrip data={x} /></View>;
}
"""
    pf = _parse_tsx(src)
    targets = {r.target_name for r in pf.refs if r.from_key == "app/screen.tsx::BudgetOverviewScreen"}
    assert "TodayCard" in targets
    assert "WalletStrip" in targets


def test_jsx_intrinsic_host_elements_are_not_captured():
    src = b"""
function BudgetOverviewScreen() {
  return <View><div className="x"><span>hi</span></div></View>;
}
"""
    pf = _parse_tsx(src)
    targets = {r.target_name for r in pf.refs if r.from_key == "app/screen.tsx::BudgetOverviewScreen"}
    # `View` is capitalized (a component) and kept; `div`/`span` are lowercase
    # intrinsic host elements and must not read as call-graph edges.
    assert "View" in targets
    assert "div" not in targets
    assert "span" not in targets


def test_jsx_member_expression_component():
    src = b"""
function Screen() {
  return <Comp.Sub />;
}
"""
    pf = _parse_tsx(src)
    targets = {r.target_name for r in pf.refs if r.from_key == "app/screen.tsx::Screen"}
    assert "Sub" in targets


def test_jsx_works_in_plain_jsx_files_too():
    src = b"""
function Screen() {
  return <TodayCard />;
}
"""
    pf = parse_source("app/screen.jsx", src, spec_for_path("app/screen.jsx"))
    targets = {r.target_name for r in pf.refs if r.from_key == "app/screen.jsx::Screen"}
    assert "TodayCard" in targets

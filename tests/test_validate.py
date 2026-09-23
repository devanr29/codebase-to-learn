"""`codemap check`: skill-authored .codemap/*.json compared with the real graph.

Uses a private copy of the impact fixture so the authored files written here
never leak into the session-scoped fixture other test modules share.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from codemap import cli, config, db, indexer
from codemap.site import model, validate
from tests.fixtures.build_repo import build_impact_repo

# real keys in the fixture graph (6 symbols in svc/)
MAIN = "svc/cli.py::main"
FETCH = "svc/data.py::fetch"
BUILD = "svc/report.py::build_report"
RENDER = "svc/report.py::render"
VIEW = "svc/web.py::report_view"


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    repo = build_impact_repo(tmp_path_factory.mktemp("validate_repo"))
    cfg = config.load(repo.path)
    cfg.codemap_dir.mkdir(exist_ok=True)
    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    indexer.scan(conn, cfg, until=repo.sha("i3-sig-partial"))
    data = model.build(conn, cfg)
    yield SimpleNamespace(repo=repo, cfg=cfg, conn=conn, data=data)
    conn.close()


@pytest.fixture(autouse=True)
def _clean_authored(env):
    yield
    for name in validate._AUTHORED:
        (env.cfg.codemap_dir / name).unlink(missing_ok=True)


def run(env, files: dict, *, data=None, all_keys=None) -> list[validate.Issue]:
    for name, body in files.items():
        text = body if isinstance(body, str) else json.dumps(body)
        (env.cfg.codemap_dir / name).write_text(text, encoding="utf-8")
    data = data or env.data
    keys = validate.graph_keys(env.conn, data) if all_keys is None else all_keys
    return validate.check(data, env.cfg, all_keys=keys)


def only(issues, severity=None, file=None):
    return [i for i in issues if (severity is None or i.severity == severity)
            and (file is None or i.file == file)]


def messages(issues) -> str:
    return "\n".join(f"{i.severity} {i.file} {i.path} {i.message}" for i in issues)


# --------------------------------------------------------------------------- nothing authored


def test_no_authored_files_means_no_issues(env):
    assert validate.check(env.data, env.cfg) == []
    assert validate.authored_files(env.cfg) == []
    assert validate.summarize([])["ok"] is True


def test_empty_model_is_not_checked(env):
    assert validate.check({"empty": True}, env.cfg) == []


# --------------------------------------------------------------------------- explanations


def test_valid_explanations_are_clean(env):
    issues = run(env, {"explanations.json": {"symbols": {MAIN: {"what": "Starts the program."}}}})
    assert issues == []


def test_explanation_key_typo_is_an_error_with_a_suggestion(env):
    issues = run(env, {"explanations.json": {"symbols": {"svc/report.py::build_reprot": {"what": "x"}}}})
    (i,) = issues
    assert i.severity == validate.ERROR and i.file == "explanations.json"
    assert 'Did you mean "svc/report.py::build_report"' in i.message


def test_explanation_without_what_is_dropped_and_reported(env):
    issues = run(env, {"explanations.json": {"symbols": {MAIN: {"why": "no what"}, FETCH: "nope"}}})
    assert len(only(issues, validate.ERROR)) == 2
    assert all("dropped" in i.message for i in issues)


def test_malformed_and_wrong_shape_files_are_reported_not_hidden(env):
    (bad,) = run(env, {"explanations.json": "{ not json"})
    assert bad.severity == validate.ERROR and "not valid JSON" in bad.message
    (wrong,) = run(env, {"explanations.json": {"symbols": ["a"]}})
    assert wrong.severity == validate.ERROR and "whole file is ignored" in wrong.message


def test_key_cut_by_the_symbol_budget_is_a_warning_not_a_typo(env, tmp_path):
    small = model.build(env.conn, env.cfg, max_symbols=2)
    kept = {n["key"] for n in small["nodes"]}
    cut = next(k for k in (MAIN, FETCH, BUILD, RENDER, VIEW) if k not in kept)
    issues = run(env, {"explanations.json": {"symbols": {cut: {"what": "x"}}}}, data=small)
    (i,) = issues
    assert i.severity == validate.WARNING and "max_symbols" in i.message


# --------------------------------------------------------------------------- scenarios


def _steps(*keys):
    return [{"node": k, "t": "call"} for k in keys]


def test_valid_scenario_is_clean(env):
    issues = run(env, {"scenarios.json": {"scenarios": [
        {"id": "ok", "title": "A run", "root": MAIN, "steps": _steps(MAIN, BUILD)},
        {"id": "root-only", "title": "Derived", "root": VIEW},
    ]}})
    assert issues == []


def test_scenario_with_too_few_resolvable_steps_is_dropped_loudly(env):
    doc = {"scenarios": [
        {"id": "bad", "title": "Bad", "steps": _steps(MAIN, "svc/nope.py::ghost")},
        {"id": "hint", "title": "Hint", "root": MAIN, "steps": _steps(MAIN, "svc/nope.py::ghost")},
    ]}
    issues = run(env, {"scenarios.json": doc})
    errs = only(issues, validate.ERROR)
    assert {i.path for i in errs if "dropped" in i.message and "resolve" in i.message} == {
        'scenarios["bad"]', 'scenarios["hint"]'}
    assert any("remove `steps`" in i.message for i in errs if i.path == 'scenarios["hint"]')
    assert any("ghost" in i.message for i in errs)  # the step itself is named too


def test_simulate_and_validate_agree_on_which_scenarios_survive(env):
    """Whatever validate calls an error must be exactly what simulate drops."""
    doc = {"scenarios": [
        {"id": "keep", "title": "Keep", "steps": _steps(MAIN, BUILD)},
        {"id": "few", "title": "Few", "steps": _steps(MAIN, "x.py::y")},
        {"id": "no-id-title", "steps": _steps(MAIN, BUILD)},
        {"id": "empty", "title": "Empty"},
        {"id": "bad-root", "title": "Bad root", "root": "svc/nope.py::ghost"},
        {"id": "root-only", "title": "Root only", "root": FETCH},
    ]}
    issues = run(env, {"scenarios.json": doc})
    built = model.build(env.conn, env.cfg)
    kept = {s["id"] for s in built["sim"]["scenarios"]}
    dropped = {"few", "no-id-title", "empty", "bad-root"}
    assert kept == {"keep", "root-only"}
    for sid in dropped:
        reported = [i for i in only(issues, validate.ERROR) if i.path.startswith(f'scenarios["{sid}"]')]
        assert reported, f"{sid} is dropped by simulate but validate said nothing:\n{messages(issues)}"
    for sid in kept:
        assert not [i for i in only(issues, validate.ERROR) if i.path.startswith(f'scenarios["{sid}"]')]


def test_scenario_warnings_for_soft_problems(env):
    doc = {"scenarios": [
        {"id": "dup", "title": "A", "steps": _steps(MAIN, BUILD)},
        {"id": "dup", "title": "B", "steps": [
            {"node": MAIN, "t": "jump"}, {"node": BUILD, "t": "call", "from": "svc/nope.py::ghost"}]},
        {"id": "weak-root", "title": "Root", "root": "svc/nope.py::ghost", "steps": _steps(MAIN, BUILD)},
    ]}
    warns = only(run(env, {"scenarios.json": doc}), validate.WARNING)
    text = messages(warns)
    assert "duplicate id" in text and 'unknown step type "jump"' in text
    assert "loses its caller" in text
    assert any(i.path.endswith(".root") for i in warns)  # a bad root beside valid steps costs nothing


# --------------------------------------------------------------------------- walkthrough


def test_walkthrough_see_keys(env):
    doc = {
        "intro": {"what": "x", "sides": {"back": {"root": "svc"}, "front": {"root": "web"}},
                  "seam": {"see": [MAIN, "svc/nope.py::ghost"]}},
        "categories": [
            {"title": "Reports", "groups": [{"title": "Build", "see": {"core": [BUILD, "styles/app.css"]}}]},
            {"title": "Empty", "groups": [{"title": "No keys", "see": []}]},
        ],
        "folders": {
            "svc": {"purpose": "the service", "categories": ["reports", "missing-cat"], "read_first": "svc/web.py"},
            "nowhere": {"purpose": "not a folder"},
            "svc/data.py": {"note": "no purpose"},
        },
    }
    issues = run(env, {"walkthrough.json": doc})
    errs = only(issues, validate.ERROR)
    assert any('sides["front"]' in i.path for i in errs)
    assert not any('sides["back"]' in i.path for i in errs)
    assert any("seam" in i.path and "ghost" in i.message for i in errs)
    assert any('folders["nowhere"]' == i.path for i in errs)
    assert any('folders["svc/data.py"]' == i.path and "purpose" in i.message for i in errs)
    assert any('categories["empty"]' == i.path for i in errs)
    warns = only(issues, validate.WARNING)
    assert any("styles/app.css" in i.message and "plain text" in i.message for i in warns)
    assert any("missing-cat" in i.message for i in warns)
    # things that are fine stay quiet
    assert not any("read_first" in i.path for i in issues)
    assert not any('category "reports"' in i.message for i in issues)


# --------------------------------------------------------------------------- libraries


def test_libraries_names_and_see(env):
    doc = {"items": {
        "svc": {"here": "the service package", "see": [BUILD]},
        "svc/data": {"here": "a sub-folder is not a module name"},
        "sqllite": {"general": "not imported anywhere"},
        "empty": {},
        "see-path": {"here": "x", "see": ["svc/data.py"]},
    }}
    issues = run(env, {"libraries.json": doc})
    by_path = {i.path: i for i in issues}
    assert 'items["svc"]' not in by_path and 'items["svc"].see' not in by_path
    assert by_path['items["svc/data"]'].severity == validate.WARNING
    assert by_path['items["sqllite"]'].severity == validate.WARNING
    assert by_path['items["empty"]'].severity == validate.ERROR
    assert by_path['items["see-path"].see'].severity == validate.ERROR
    assert "symbol key" in by_path['items["see-path"].see'].message


# --------------------------------------------------------------------------- architecture


def test_architecture_layers_components_and_via(env):
    doc = {
        "layers": {"logic": {"title": "Work"}, "backend": {"title": "no such layer"}},
        "components": {
            "svc": {"layer": "logic"},
            "svc/data.py": {"layer": "data"},
            "svc/nope": {"layer": "logic"},
            "svc/data.py ": {"layer": "middle"},
        },
        "stores": [{"label": "Postgres", "via": ["svc/data.py"]}, {"label": "Redis", "via": ["cache"]}, {"id": "x"}],
    }
    issues = run(env, {"architecture.json": doc})
    errs = only(issues, validate.ERROR)
    assert any('layers["backend"]' == i.path for i in errs)
    assert any('components["svc/nope"]' == i.path for i in errs)
    assert any(i.path.endswith(".layer") and '"middle"' in i.message for i in errs)
    assert any(i.path == "stores[2]" for i in errs)
    assert not any(i.path in ('components["svc"]', 'components["svc/data.py"]') for i in issues)
    assert [i.path for i in only(issues, validate.WARNING)] == ["stores[1].via"]


def test_architecture_folder_that_is_not_a_grouping_key_gets_a_hint(env):
    # `src` holds files but nothing groups on it directly, so build() would ignore it
    files = [{"fi": 0, "path": "src/a/x.py", "symbols": []}, {"fi": 1, "path": "src/b/y.py", "symbols": []}]
    data = {"nodes": [], "files": files, "folders": [], "modules": [{"name": "src"}]}
    (i,) = run(env, {"architecture.json": {"components": {"src": {"layer": "logic"}}}}, data=data, all_keys=set())
    assert i.severity == validate.ERROR and "src/a" in i.message


# --------------------------------------------------------------------------- glossary


def test_glossary_terms_never_used_are_warnings(env):
    files = {
        "walkthrough.json": {
            "intro": {"what": "A widget with a frobnicator inside."},
            "glossary": {"frobnicator": "a thing", "gizmo": "never mentioned"},
        },
    }
    for name, body in files.items():
        (env.cfg.codemap_dir / name).write_text(json.dumps(body), encoding="utf-8")
    data = model.build(env.conn, env.cfg)
    issues = validate.check(data, env.cfg, all_keys=validate.graph_keys(env.conn, data))
    warns = only(issues, validate.WARNING)
    assert [i.path for i in warns] == ['glossary["gizmo"]']


# --------------------------------------------------------------------------- CLI


def _args(env, **kw):
    base = dict(path=str(env.repo.path), json=False, strict=False, no_progress=True)
    return SimpleNamespace(**{**base, **kw})


def test_cli_check_json_and_strict_exit_code(env, capsys):
    (env.cfg.codemap_dir / "explanations.json").write_text(
        json.dumps({"symbols": {"svc/cli.py::mian": {"what": "x"}}}), encoding="utf-8")

    assert cli.cmd_check(_args(env, json=True)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["errors"] == 1
    assert out["authored"] == ["explanations.json"]
    assert out["issues"][0]["severity"] == "error"

    assert cli.cmd_check(_args(env, json=True, strict=True)) == 1
    capsys.readouterr()

    assert cli.cmd_check(_args(env)) == 0
    text = capsys.readouterr().out
    assert "1 error(s)" in text and "explanations.json" in text and "svc/cli.py::main" in text


def test_cli_check_clean_and_nothing_authored(env, capsys):
    assert cli.cmd_check(_args(env)) == 0
    assert "nothing to check" in capsys.readouterr().out

    (env.cfg.codemap_dir / "explanations.json").write_text(
        json.dumps({"symbols": {MAIN: {"what": "Starts the program."}}}), encoding="utf-8")
    assert cli.cmd_check(_args(env, strict=True)) == 0
    assert "no problems found" in capsys.readouterr().out


def test_cli_check_without_an_index_reports_instead_of_crashing(tmp_path, capsys):
    (tmp_path / ".git").mkdir()
    assert cli.cmd_check(SimpleNamespace(path=str(tmp_path), json=True, strict=True, no_progress=True)) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and "codemap scan" in out["issues"][0]["message"]


def test_explore_warns_on_stderr_when_authored_entries_do_not_match(env, capsys, tmp_path):
    (env.cfg.codemap_dir / "explanations.json").write_text(
        json.dumps({"symbols": {"svc/cli.py::mian": {"what": "x"}}}), encoding="utf-8")
    args = SimpleNamespace(
        path=str(env.repo.path), out=str(tmp_path / "out.html"), json=False, emit_brief=False,
        max_symbols=0, open=False, quiet=False, if_enabled=False, no_progress=True,
    )
    assert cli.cmd_explore(args) == 0
    err = capsys.readouterr().err
    assert "1 problem in the authored" in err and "codemap check" in err

    args.quiet = True  # the post-commit hook path stays silent
    assert cli.cmd_explore(args) == 0
    assert "codemap check" not in capsys.readouterr().err


# --------------------------------------------------------------------------- found on real authored content


def test_a_folder_may_name_a_category_by_title_or_id(env):
    """The page matches a folder's `categories` against a category's id *or* title
    (walkthrough-schema.md tells authors to write the title); only a name matching
    neither is a problem. Real authored content used titles and lost every link."""
    doc = {
        "categories": [{"title": "Money movement", "groups": [{"title": "Billing", "see": [BUILD]}]}],
        "folders": {"svc": {"purpose": "p", "categories": ["Money movement", "money-movement", "MONEY movement", "Nope"]}},
    }
    warns = only(run(env, {"walkthrough.json": doc}), validate.WARNING)
    assert [i.message for i in warns] == ['category "Nope" is not defined in `categories`']


def test_a_root_only_scenario_with_a_bad_root_says_it_is_dropped(env):
    doc = {"scenarios": [{"id": "gone", "title": "Gone", "root": "svc/nope.py::ghost"}]}
    (i,) = run(env, {"scenarios.json": doc})
    assert i.severity == validate.ERROR and i.message.startswith("scenario is dropped.")


def test_text_output_folds_a_wall_of_budget_warnings_but_json_keeps_them(env):
    small = model.build(env.conn, env.cfg, max_symbols=1)
    syms = {k: {"what": "x"} for k in (MAIN, FETCH, BUILD, RENDER, VIEW)}
    issues = run(env, {"explanations.json": {"symbols": syms}}, data=small)
    assert len([i for i in issues if "max_symbols budget" in i.message]) >= 4
    text = validate.format_text(issues)
    assert text.count("max_symbols budget") == 1 and "keys were cut" in text and "--json" in text
    assert len(validate.summarize(issues)["issues"]) == len(issues)     # nothing is hidden from the skill

    few = run(env, {"explanations.json": {"symbols": {FETCH: {"what": "x"}}}}, data=small)
    assert "keys were cut" not in validate.format_text(few)            # a couple of them are listed as they are


def test_folder_categories_are_ignored_when_the_folder_has_its_own_see(env):
    doc = {
        "categories": [{"title": "Money movement", "groups": [{"title": "Billing", "see": [BUILD]}]}],
        "folders": {
            "svc": {"purpose": "p", "categories": ["Money movement"], "see": [MAIN]},
            "svc/data.py": {"purpose": "q", "categories": ["Money movement"]},
        },
    }
    warns = only(run(env, {"walkthrough.json": doc}), validate.WARNING)
    assert [i.path for i in warns] == ['folders["svc"].categories'] and "ignored" in warns[0].message

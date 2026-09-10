"""codemap/site/folders.py — pure folder-tree folding over already-shaped
``files``/``file_edges``/``entry_points`` dicts (no git, no SQLite, no
fixtures; see ``model.py::build()`` for the shapes these mirror)."""

from __future__ import annotations

from codemap.site import folders


def mk_file(fi, path, *, loc=10, lang="py", symbols=None, deps=None):
    return {
        "fi": fi,
        "path": path,
        "lang": lang,
        "tier": 1,
        "loc": loc,
        "module": path.split("/")[0] if "/" in path else "(root)",
        "symbols": symbols or [],
        "imports": [],
        "deps": deps or [],
    }


def by_path(result, path):
    return next(r for r in result if r["path"] == path)


# --------------------------------------------------------------- basic shape


def test_basic_shape_and_sort_order():
    files = [
        mk_file(0, "app/main.py", loc=8, symbols=[10]),
        mk_file(1, "app/routes/api.py", loc=20, symbols=[11, 12], deps=[{"name": "flask", "kind": "third_party"}]),
        mk_file(2, "app/routes/web.py", loc=12, symbols=[13], deps=[{"name": "flask", "kind": "third_party"}]),
        mk_file(3, "app/models/user.py", loc=5, symbols=[14]),
        mk_file(4, "app/models/post.py", loc=7, symbols=[15, 16]),
    ]
    result = folders.build(files, [], [])

    assert [r["path"] for r in result] == ["app", "app/models", "app/routes"]

    app = by_path(result, "app")
    assert app["name"] == "app"
    assert app["parent"] is None
    assert app["depth"] == 1
    assert app["files"] == [0]
    assert app["file_count"] == 1
    assert app["total_files"] == 5
    assert app["loc"] == 8
    assert app["symbols"] == [10]
    assert app["symbol_count"] == 1
    assert app["langs"] == ["py"]
    assert app["deps"] == []

    routes = by_path(result, "app/routes")
    assert routes["name"] == "routes"
    assert routes["parent"] == "app"
    assert routes["depth"] == 2
    assert routes["files"] == [1, 2]
    assert routes["file_count"] == 2
    assert routes["total_files"] == 2
    assert routes["loc"] == 32
    assert routes["symbols"] == [11, 12, 13]
    assert routes["symbol_count"] == 3
    assert routes["langs"] == ["py"]
    assert routes["deps"] == [{"name": "flask", "kind": "third_party", "count": 2}]

    models = by_path(result, "app/models")
    assert models["parent"] == "app"
    assert models["depth"] == 2
    assert models["files"] == [3, 4]
    assert models["file_count"] == 2
    assert models["total_files"] == 2
    assert models["loc"] == 12
    assert models["symbols"] == [14, 15, 16]
    assert models["symbol_count"] == 3


# ------------------------------------------------------------- fold rules


def test_single_child_collapse_leaves_no_intermediate_node():
    # a/b/c/file.py alone: no sibling of b under a, no sibling of c under b.
    files = [mk_file(0, "a/b/c/file.py")]
    result = folders.build(files, [], [])

    paths = {r["path"] for r in result}
    parents = {r["parent"] for r in result}
    assert "a/b" not in paths and "a/b/c" not in paths
    assert "a/b" not in parents and "a/b/c" not in parents
    # everything below the permanent depth-1 "a" folded away into it
    assert len(result) == 1
    a = result[0]
    assert a["path"] == "a"
    assert a["files"] == [0]
    assert a["file_count"] == 1
    assert a["total_files"] == 1


def test_sparse_node_fold_merges_one_file_folder_into_parent():
    files = [
        mk_file(0, "a/main.py"),
        mk_file(1, "a/lonely/one.py"),  # exactly 1 file -> sparse-folds into "a"
        mk_file(2, "a/team/x.py"),
        mk_file(3, "a/team/y.py"),
    ]
    result = folders.build(files, [], [])

    paths = {r["path"] for r in result}
    assert "a/lonely" not in paths

    a = by_path(result, "a")
    assert sorted(a["files"]) == [0, 1]
    assert a["file_count"] == 2
    assert a["total_files"] == 4  # own 2 + a/team's 2

    team = by_path(result, "a/team")
    assert team["files"] == [2, 3]
    assert team["file_count"] == 2
    assert team["total_files"] == 2


def test_grouping_folder_with_zero_direct_files_survives():
    files = [
        mk_file(0, "src/components/layout/a.py"),
        mk_file(1, "src/components/layout/b.py"),
        mk_file(2, "src/components/ui/c.py"),
        mk_file(3, "src/components/ui/d.py"),
    ]
    result = folders.build(files, [], [])

    components = by_path(result, "src/components")
    assert components["file_count"] == 0
    assert components["files"] == []
    layout = by_path(result, "src/components/layout")
    ui = by_path(result, "src/components/ui")
    assert components["total_files"] == layout["total_files"] + ui["total_files"] == 4
    assert layout["parent"] == "src/components"
    assert ui["parent"] == "src/components"


def test_depth_clamp_attributes_deep_files_to_depth4_ancestor():
    files = [
        mk_file(0, "a/b/c/d/e/file1.py"),  # 6 segments -> dirname 5 segments
        mk_file(1, "a/b/c/d/g/file2.py"),  # clamps to the same "a/b/c/d"
    ]
    result = folders.build(files, [], [])

    assert all(r["depth"] <= 4 for r in result)
    paths = {r["path"] for r in result}
    assert "a/b" not in paths and "a/b/c" not in paths  # single-child collapsed away

    deep = by_path(result, "a/b/c/d")
    assert deep["depth"] == 4
    assert deep["parent"] == "a"
    assert sorted(deep["files"]) == [0, 1]


def test_root_bucket_never_folds_even_with_one_file():
    files = [mk_file(0, "main.py")]
    result = folders.build(files, [], [])
    assert len(result) == 1
    root = result[0]
    assert root["path"] == "(root)"
    assert root["name"] == "(root)"
    assert root["parent"] is None
    assert root["depth"] == 1
    assert root["files"] == [0]
    assert root["file_count"] == 1
    assert root["total_files"] == 1


def test_depth1_folder_never_folds_even_with_one_file_and_no_children():
    files = [mk_file(0, "lonelytop/file.py")]
    result = folders.build(files, [], [])
    assert len(result) == 1
    top = result[0]
    assert top["path"] == "lonelytop"
    assert top["parent"] is None
    assert top["depth"] == 1
    assert top["files"] == [0]


# ------------------------------------------------------------- invariants


def test_every_file_accounted_for_exactly_once():
    files = [
        mk_file(0, "main.py"),  # (root)
        mk_file(1, "lonelytop/file.py"),  # depth-1 survivor
        mk_file(2, "app/main.py"),
        mk_file(3, "app/routes/api.py"),
        mk_file(4, "app/routes/web.py"),
        mk_file(5, "a/b/c/deep.py"),  # collapses all the way to "a"
    ]
    result = folders.build(files, [], [])

    seen: list[int] = []
    for r in result:
        seen.extend(r["files"])
    assert sorted(seen) == list(range(len(files)))
    assert len(seen) == len(set(seen))
    assert sum(r["file_count"] for r in result) == len(files)


def test_reach_cases():
    files = [
        mk_file(0, "appmain/x.py", symbols=[500]),
        mk_file(1, "routes/x.py", symbols=[501]),
        mk_file(2, "tests/x.py", symbols=[502]),
        mk_file(3, "utils/x.py", symbols=[503]),
        mk_file(4, "lib/x.py", symbols=[504]),
    ]
    file_edges = [{"s": 1, "t": 4}]  # routes imports lib
    entry_points = [{"kind": "main", "detail": "cli", "node": 500}]
    result = folders.build(files, file_edges, entry_points)

    appmain = by_path(result, "appmain")
    assert appmain["reach"] == "root"
    assert appmain["orphan_reason"] is None

    tests_folder = by_path(result, "tests")
    assert tests_folder["reach"] == "root"
    assert tests_folder["orphan_reason"] is None

    lib = by_path(result, "lib")
    assert lib["reach"] == "reached"
    assert lib["orphan_reason"] is None
    assert by_path(result, "routes")["imports"] == ["lib"]
    assert lib["imported_by"] == ["routes"]

    utils = by_path(result, "utils")
    assert utils["reach"] == "orphan"
    assert utils["orphan_reason"]
    assert "can mean" in utils["orphan_reason"]
    assert "unused" not in utils["orphan_reason"].split("can mean")[0]


def test_forty_folder_cap_fires_on_a_wide_tree():
    files = []
    fi = 0
    for idx in range(50):
        for j in range(2):
            files.append(mk_file(fi, f"root_folder/child{idx:02d}/f{j}.py"))
            fi += 1
    assert len(files) == 100

    result = folders.build(files, [], [])
    assert len(result) <= 40

    seen: list[int] = []
    for r in result:
        seen.extend(r["files"])
    assert sorted(seen) == list(range(100))
    assert len(seen) == len(set(seen))


def test_parent_total_files_equals_own_plus_children():
    files = [
        mk_file(0, "src/components/layout/a.py"),
        mk_file(1, "src/components/layout/b.py"),
        mk_file(2, "src/components/ui/c.py"),
        mk_file(3, "src/components/ui/d.py"),
        mk_file(4, "src/index.py"),
        mk_file(5, "src/app.py"),
    ]
    result = folders.build(files, [], [])
    by_p = {r["path"]: r for r in result}

    for r in result:
        parent_path = r["parent"]
        if parent_path is None:
            continue
        assert parent_path in by_p, f"missing parent entry for {r['path']!r}"
        parent = by_p[parent_path]
        children_total = sum(c["total_files"] for c in result if c["parent"] == parent_path)
        assert parent["total_files"] == parent["file_count"] + children_total

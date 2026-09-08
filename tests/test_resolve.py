"""M10: raw import statements -> in-repo file paths (query-time T2 pass)."""

from __future__ import annotations

from codemap import resolve


def test_python_absolute_and_relative_and_external():
    paths = {"app/core.py", "app/util.py", "web/api.ts", "web/loader.ts"}
    out = {
        (r.importer, r.raw): r
        for r in resolve.resolve_imports(
            [
                ("app/core.py", "from app.util import clean"),
                ("app/core.py", "from .util import clean"),
                ("app/core.py", "import requests"),
                ("app/core.py", "from . import util"),
            ],
            paths,
        )
    }
    assert out[("app/core.py", "from app.util import clean")].target == "app/util.py"
    assert out[("app/core.py", "from .util import clean")].target == "app/util.py"
    assert out[("app/core.py", "from . import util")].target == "app/util.py"

    ext = out[("app/core.py", "import requests")]
    assert ext.external is True and ext.target is None


def test_ts_relative_resolution_and_index_files():
    paths = {"web/api.ts", "web/loader.ts", "web/util/index.ts", "src/a.tsx"}
    out = {
        r.raw: r
        for r in resolve.resolve_imports(
            [
                ("web/api.ts", 'import { load } from "./loader";'),
                ("web/api.ts", 'import { x } from "./util";'),
                ("web/api.ts", 'import _ from "lodash";'),
                ("src/a.tsx", 'import { load } from "../web/loader";'),
            ],
            paths,
        )
    }
    assert out['import { load } from "./loader";'].target == "web/loader.ts"
    assert out['import { x } from "./util";'].target == "web/util/index.ts"
    assert out['import { load } from "../web/loader";'].target == "web/loader.ts"
    ext = out['import _ from "lodash";']
    assert ext.external is True and ext.target is None


def test_unresolvable_internal_import_is_not_external():
    r = resolve.resolve_imports(
        [("pkg/a.py", "from pkg.missing import thing")], {"pkg/a.py", "pkg/other.py"}
    )[0]
    assert r.external is False and r.target is None


def test_never_targets_itself():
    r = resolve.resolve_imports(
        [("pkg/a.py", "from pkg.a import helper")], {"pkg/a.py"}
    )[0]
    assert r.target is None


def test_import_kind_classification():
    paths = {"app/core.py", "app/util.py", "web/api.ts"}
    out = {
        r.raw: r
        for r in resolve.resolve_imports(
            [
                ("app/core.py", "import os"),                 # python stdlib
                ("app/core.py", "from os.path import join"),  # python stdlib, dotted
                ("app/core.py", "import requests"),           # third party
                ("app/core.py", "from app.util import x"),    # in-repo absolute
                ("app/core.py", "from .util import x"),       # in-repo relative
                ("web/api.ts", 'import fs from "fs";'),       # node builtin
                ("web/api.ts", 'import x from "node:path";'), # node builtin, prefixed
                ("web/api.ts", 'import _ from "lodash";'),    # third party
                ("web/api.ts", 'import { y } from "./util";'),# in-repo relative
            ],
            paths,
        )
    }
    assert out["import os"].kind == "stdlib"
    assert out["from os.path import join"].kind == "stdlib"
    assert out["import requests"].kind == "third_party"
    assert out["from app.util import x"].kind == "internal"
    assert out["from .util import x"].kind == "internal"
    assert out['import fs from "fs";'].kind == "stdlib"
    assert out['import x from "node:path";'].kind == "stdlib"
    assert out['import _ from "lodash";'].kind == "third_party"
    assert out['import { y } from "./util";'].kind == "internal"

    # kind is additive — the existing external/target contract is unchanged
    assert out["import requests"].external is True
    assert out["from app.util import x"].external is False


# ------------------------------------------------------------ M19: ts aliases


def test_alias_resolves_bare_specifier_to_real_file():
    aliases = [resolve.TsAlias(config_dir="mobile", pattern="@/*", targets=("mobile/src/*",))]
    paths = {"mobile/src/api/client.ts", "mobile/src/app/index.tsx"}
    out = resolve.resolve_imports(
        [("mobile/src/app/index.tsx", 'import { get } from "@/api/client";')],
        paths,
        aliases=aliases,
    )[0]
    assert out.target == "mobile/src/api/client.ts"
    assert out.kind == "internal"
    assert out.external is False


def test_alias_classifies_internal_even_when_target_file_is_unindexed():
    """The whole point of an alias: it names something in this repo, even if
    the exact file (a re-export barrel, say) isn't in the indexed set."""
    aliases = [resolve.TsAlias(config_dir="mobile", pattern="@/*", targets=("mobile/src/*",))]
    out = resolve.resolve_imports(
        [("mobile/src/app/index.tsx", 'import { get } from "@/api/client";')],
        {"mobile/src/app/index.tsx"},  # client.ts not in the indexed set
        aliases=aliases,
    )[0]
    assert out.target is None
    assert out.kind == "internal"
    assert out.external is False


def test_alias_without_wildcard_is_an_exact_match():
    # tsconfig `paths` targets never carry an extension — probed the same way
    # a wildcard capture's expansion is, via _TS_EXTS.
    aliases = [resolve.TsAlias(config_dir="", pattern="theme", targets=("src/theme/index",))]
    paths = {"src/theme/index.ts", "src/app.ts"}
    out = resolve.resolve_imports(
        [("src/app.ts", 'import theme from "theme";')], paths, aliases=aliases
    )[0]
    assert out.target == "src/theme/index.ts"
    assert out.kind == "internal"


def test_alias_scoped_to_its_own_tsconfig_directory():
    """A monorepo alias declared under mobile/tsconfig.json must not apply to
    an importer outside that directory."""
    aliases = [resolve.TsAlias(config_dir="mobile", pattern="@/*", targets=("mobile/src/*",))]
    out = resolve.resolve_imports(
        [("backend/app.ts", 'import { get } from "@/api/client";')],
        {"backend/app.ts", "mobile/src/api/client.ts"},
        aliases=aliases,
    )[0]
    assert out.target is None
    assert out.kind == "third_party"  # falls back to ordinary classification — reads as a package


def test_alias_absent_falls_back_to_ordinary_resolution():
    paths = {"web/api.ts", "web/loader.ts"}
    out = resolve.resolve_imports(
        [("web/api.ts", 'import { load } from "./loader";')], paths
    )[0]
    assert out.target == "web/loader.ts"


def test_strip_jsonc_keeps_string_contents_but_drops_comments_and_trailing_commas():
    text = '''{
  // a comment with // inside a string below
  "compilerOptions": {
    "paths": {
      "@/*": ["./src/*"], /* trailing */
    },
    "baseUrl": "https://not-a-real-url-just-testing-slashes"
  },
}'''
    import json as _json
    cleaned = resolve._strip_jsonc(text)
    data = _json.loads(cleaned)
    assert data["compilerOptions"]["paths"]["@/*"] == ["./src/*"]
    assert data["compilerOptions"]["baseUrl"] == "https://not-a-real-url-just-testing-slashes"


def test_load_ts_aliases_from_worktree(tmp_path):
    (tmp_path / "mobile").mkdir()
    (tmp_path / "mobile" / "tsconfig.json").write_text(
        '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./src/*"]}}}',
        encoding="utf-8",
    )
    from codemap.indexer import WORKTREE_SHA

    aliases = resolve.load_ts_aliases(tmp_path, WORKTREE_SHA)
    assert aliases == [
        resolve.TsAlias(config_dir="mobile", pattern="@/*", targets=("mobile/src/*",))
    ]

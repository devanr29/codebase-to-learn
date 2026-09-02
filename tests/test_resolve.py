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

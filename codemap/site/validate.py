"""Check the skill-authored ``.codemap/*.json`` files against the real graph.

Every loader under ``codemap/site`` fails soft: a bad entry is dropped and the
page simply shows less. That is right for the page but wrong for the author,
who never learns that half of ``scenarios.json`` or a stale ``explanations.json``
key went nowhere. This module re-reads the raw authored files (the loaders'
cleaned output has already lost what we need to report) and compares every
reference with the model ``model.build()`` produced.

An :class:`Issue` is an **error** when the entry is dropped, never attaches, or
points at something that does not exist -- the author must fix it -- and a
**warning** when it still renders but is probably not what was meant.

Pure read path: never writes, never raises on malformed input.
"""

from __future__ import annotations

import difflib
import json
import sqlite3
from dataclasses import asdict, dataclass

from ..config import Config
from . import architecture as _arch
from . import explain as _explain
from . import glossary as _glossary
from . import libraries as _libraries
from . import scenarios as _scenarios
from . import walkthrough as _walkthrough
from .folders import ROOT

ERROR = "error"
WARNING = "warning"

_AUTHORED = (
    _explain.EXPLAIN_FILE,
    _scenarios.SCENARIOS_FILE,
    _walkthrough.WALKTHROUGH_FILE,
    _libraries.LIBRARIES_FILE,
    _arch.ARCHITECTURE_FILE,
    _glossary.GLOSSARY_FILE,
)
_SUGGEST_MAX = 40  # difflib over every key costs O(keys) per issue; cap how many issues pay it
_BUDGET = "max_symbols budget"   # in the message of a key the node budget cut; format_text() folds those


@dataclass(frozen=True)
class Issue:
    file: str
    path: str      # where inside the file, e.g. `symbols["a.py::f"]`
    severity: str  # ERROR | WARNING
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


def _s(v: object) -> str:
    return v.strip() if isinstance(v, str) else ""


def _list(v: object) -> list:
    return v if isinstance(v, list) else []


def _dict(v: object) -> dict:
    return v if isinstance(v, dict) else {}


# --------------------------------------------------------------------------- context


class _Ctx:
    """The graph facts every check needs, computed once, plus the issue sink."""

    def __init__(self, data: dict, all_keys: set[str] | None) -> None:
        self.keys: set[str] = {n["key"] for n in data.get("nodes", [])}
        self.all_keys: set[str] = all_keys if all_keys is not None else self.keys
        self.paths: set[str] = {f["path"] for f in data.get("files", [])}
        self.dep_names: set[str] = {
            d["name"]
            for d in data.get("dependencies", [])
            if d.get("kind") in ("third_party", "stdlib")
        }
        self.module_names: set[str] = {m["name"] for m in data.get("modules", [])}
        in_folder: set[int] = set()
        folder_paths: set[str] = set()
        for fo in data.get("folders", []):
            folder_paths.add(fo["path"])
            in_folder.update(fo.get("files") or [])
        # architecture.build() only accepts a folder key that codemap itself groups
        # on: a folded folder, or the directory of a file no fold claimed
        for f in data.get("files", []):
            if f["fi"] not in in_folder:
                folder_paths.add(f["path"].rsplit("/", 1)[0] if "/" in f["path"] else ROOT)
        self.folder_paths = folder_paths
        self.issues: list[Issue] = []
        self._suggested = 0

    def add(self, file: str, path: str, severity: str, message: str) -> None:
        self.issues.append(Issue(file, path, severity, message))

    def has_prefix(self, prefix: str) -> bool:
        p = prefix.rstrip("/") + "/"
        return any(x.startswith(p) for x in self.paths)

    def suggest(self, key: str) -> str:
        if self._suggested >= _SUGGEST_MAX:
            return ""
        self._suggested += 1
        path = key.split("::", 1)[0]
        pool = [k for k in self.keys if k.startswith(path + "::")] or list(self.keys)
        near = difflib.get_close_matches(key, pool, n=1, cutoff=0.6)
        return f' Did you mean "{near[0]}"?' if near else ""

    def key_problem(self, key: str) -> tuple[str, str] | None:
        """``None`` when ``key`` is a graph node, else ``(severity, why)``."""
        if key in self.keys:
            return None
        if key in self.all_keys:
            return WARNING, (
                f"exists in the code but was cut from the explorer by the {_BUDGET}, "
                "so it will not show"
            )
        if "::" in key:
            return ERROR, "no symbol with that key in the graph." + self.suggest(key)
        if key in self.paths:
            return ERROR, "that is a file path, but a symbol key (`path::Name`) is needed here"
        return ERROR, "no symbol or file with that name in the graph." + self.suggest(key)


def _read(cfg: Config, name: str, ctx: _Ctx) -> object | None:
    """The parsed file, or ``None`` when absent (or unusable, which is reported)."""
    path = cfg.codemap_dir / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        ctx.add(name, "", ERROR, f"not valid JSON, so the whole file is ignored ({e.msg}, line {e.lineno})")
    except (OSError, UnicodeDecodeError) as e:
        ctx.add(name, "", ERROR, f"could not be read, so the whole file is ignored ({e})")
    return None


def _read_quiet(cfg: Config, name: str) -> object | None:
    try:
        return json.loads((cfg.codemap_dir / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _see_keys(v: object) -> list[str]:
    """Every key in either ``see`` shape (flat array, or ``{label: [keys]}``)."""
    if isinstance(v, list):
        return [s.strip() for s in v if isinstance(s, str) and s.strip()]
    if isinstance(v, dict):
        return [
            s.strip()
            for keys in v.values() if isinstance(keys, list)
            for s in keys if isinstance(s, str) and s.strip()
        ]
    return []


# --------------------------------------------------------------------------- checks


def _check_explanations(cfg: Config, ctx: _Ctx) -> None:
    f = _explain.EXPLAIN_FILE
    data = _read(cfg, f, ctx)
    if data is None:
        return
    syms = _dict(data).get("symbols")
    if not isinstance(syms, dict):
        ctx.add(f, "symbols", ERROR, 'expected an object under "symbols"; the whole file is ignored')
        return
    for key, v in syms.items():
        where = f'symbols["{key}"]'
        if not isinstance(v, dict):
            ctx.add(f, where, ERROR, "expected an object with a `what` sentence; entry is dropped")
        elif not _s(v.get("what")):
            ctx.add(f, where, ERROR, "missing `what`, so the entry is dropped")
        elif problem := ctx.key_problem(key):
            ctx.add(f, where, *problem)


def _check_scenarios(cfg: Config, ctx: _Ctx) -> None:
    f = _scenarios.SCENARIOS_FILE
    data = _read(cfg, f, ctx)
    if data is None:
        return
    raw = _dict(data).get("scenarios")
    if not isinstance(raw, list):
        ctx.add(f, "scenarios", ERROR, 'expected an array under "scenarios"; the whole file is ignored')
        return
    seen: dict[str, int] = {}
    for i, sc in enumerate(raw):
        where = f"scenarios[{i}]"
        if not isinstance(sc, dict):
            ctx.add(f, where, ERROR, "expected an object; scenario is dropped")
            continue
        sid = _s(sc.get("id"))
        if sid:
            where = f'scenarios["{sid}"]'
        if not sid or not _s(sc.get("title")):
            ctx.add(f, where, ERROR, "needs both `id` and `title`; scenario is dropped")
            continue
        if sid in seen:
            ctx.add(f, where, WARNING, f"duplicate id (first used by scenarios[{seen[sid]}])")
        seen.setdefault(sid, i)

        step_list = _list(sc.get("steps"))
        root = _s(sc.get("root"))
        if not step_list and not root:
            ctx.add(f, where, ERROR, "has neither `steps` nor a `root`; scenario is dropped")
            continue

        root_ok = False
        if root:
            problem = ctx.key_problem(root)
            if problem:
                if step_list:   # a bad root costs a scenario nothing while it has steps of its own
                    ctx.add(f, f"{where}.root", WARNING, f'"{root}": {problem[1]}')
                else:
                    ctx.add(f, f"{where}.root", ERROR, f'scenario is dropped. "{root}": {problem[1]}')
            else:
                root_ok = True

        good = 0
        for j, st in enumerate(step_list):
            sw = f"{where}.steps[{j}]"
            node = _s(_dict(st).get("node"))
            if not node:
                ctx.add(f, sw, ERROR, "step has no `node`; step is dropped")
                continue
            if problem := ctx.key_problem(node):
                ctx.add(f, sw, ERROR, f'step is dropped. "{node}": {problem[1]}')
                continue
            good += 1
            t = st.get("t")
            if t is not None and t not in _scenarios._VALID_STEP_TYPES:
                ctx.add(f, sw, WARNING, f'unknown step type "{t}", treated as "call"')
            frm = _s(st.get("from"))
            if frm and ctx.key_problem(frm):
                ctx.add(f, f"{sw}.from", WARNING, f'"{frm}" is not in the graph; the step loses its caller')
        if step_list and good < 2:
            ctx.add(
                f, where, ERROR,
                f"only {good} of {len(step_list)} steps resolve and a scenario needs 2, so the whole "
                "scenario is dropped" + (" (remove `steps` to keep it as a root-only entry)" if root_ok else ""),
            )


def _check_walkthrough(cfg: Config, ctx: _Ctx) -> None:
    f = _walkthrough.WALKTHROUGH_FILE
    data = _read(cfg, f, ctx)
    if data is None:
        return
    if not isinstance(data, dict):
        ctx.add(f, "", ERROR, "expected a JSON object; the whole file is ignored")
        return

    def see(where: str, v: object) -> None:
        for k in _see_keys(v):
            problem = ctx.key_problem(k)
            if problem is None or k in ctx.paths:
                continue
            if problem[0] == WARNING or "::" in k:
                ctx.add(f, where, problem[0], f'"{k}": {problem[1]}')
            else:  # an unindexed file (a .css, say) is legitimate: the page shows it as plain text
                ctx.add(f, where, WARNING,
                        f'"{k}" is not an indexed file or symbol, so it renders as plain text, not a link')

    intro = _dict(data.get("intro"))
    for name, side in _dict(intro.get("sides")).items():
        root = _s(_dict(side).get("root"))
        if root and root not in ctx.paths and not ctx.has_prefix(root):
            ctx.add(f, f'intro.sides["{name}"].root', ERROR, f'"{root}" matches no folder or file in the graph')
    see("intro.seam.see", _dict(intro.get("seam")).get("see"))

    cat_ids: set[str] = set()
    cat_titles: set[str] = set()
    for i, cat in enumerate(_list(data.get("categories"))):
        title = _s(_dict(cat).get("title"))
        if not title:
            ctx.add(f, f"categories[{i}]", ERROR, "missing `title`; category is dropped")
            continue
        cid = _s(cat.get("id")) or _walkthrough._slugify(title)
        cat_ids.add(cid)
        cat_titles.add(title.lower())
        where = f'categories["{cid}"]'
        usable = 0
        for j, g in enumerate(_list(cat.get("groups"))):
            gw = f"{where}.groups[{j}]"
            if not _s(_dict(g).get("title")):
                ctx.add(f, gw, ERROR, "group needs a `title`; group is dropped")
            elif not _see_keys(g.get("see")):
                ctx.add(f, gw, ERROR, "group has no usable `see` keys; group is dropped")
            else:
                usable += 1
                see(f"{gw}.see", g.get("see"))
        if not usable:
            ctx.add(f, where, ERROR, "has no usable groups, so the category is dropped")

    for path, entry in _dict(data.get("folders")).items():
        where = f'folders["{path}"]'
        if not _s(_dict(entry).get("purpose")):
            ctx.add(f, where, ERROR, "missing `purpose`, so the folder entry is dropped")
            continue
        p = path.strip().strip("/")
        if p != ROOT and p not in ctx.folder_paths and p not in ctx.paths and not ctx.has_prefix(p):
            ctx.add(f, where, ERROR, f'"{path}" matches no folder in the graph, so the entry never shows')
        for c in _list(entry.get("categories")):   # the page accepts a category's id or its title
            if _s(c) and _s(c) not in cat_ids and _s(c).lower() not in cat_titles:
                ctx.add(f, f"{where}.categories", WARNING, f'category "{_s(c)}" is not defined in `categories`')
        if _list(entry.get("categories")) and _see_keys(entry.get("see")):
            ctx.add(f, f"{where}.categories", WARNING,
                    "ignored: a folder page shows its own `see` when it has one, and only falls back "
                    "to `categories` when it has none")
        see(f"{where}.see", entry.get("see"))
        rf = _s(entry.get("read_first"))
        if rf and rf not in ctx.keys and rf not in ctx.paths:
            ctx.add(f, f"{where}.read_first", WARNING, f'"{rf}" is not an indexed file or symbol')


def _check_libraries(cfg: Config, ctx: _Ctx) -> None:
    f = _libraries.LIBRARIES_FILE
    data = _read(cfg, f, ctx)
    if data is None:
        return
    raw = _dict(data).get("items")
    if not isinstance(raw, dict):
        ctx.add(f, "items", ERROR, 'expected an object under "items"; the whole file is ignored')
        return
    for name, entry in raw.items():
        where = f'items["{name}"]'
        if not isinstance(entry, dict):
            ctx.add(f, where, ERROR, "expected an object; entry is dropped")
            continue
        if not (_s(entry.get("general")) or _s(entry.get("here")) or _see_keys(entry.get("see"))):
            ctx.add(f, where, ERROR, "has none of `general`, `here` or `see`; entry is dropped")
            continue
        n = name.strip()
        if n not in ctx.dep_names and n not in ctx.module_names:
            near = difflib.get_close_matches(n, sorted(ctx.dep_names | ctx.module_names), n=1, cutoff=0.6)
            ctx.add(f, where, WARNING,
                    f'"{n}" is not an imported package or a top-level folder of this repo, so the Packages tab has no page for it.'
                    + (f' Did you mean "{near[0]}"?' if near else ""))
        for k in _see_keys(entry.get("see")):
            if problem := ctx.key_problem(k):
                ctx.add(f, f"{where}.see", problem[0], f'"{k}": {problem[1]}')


def _check_architecture(cfg: Config, ctx: _Ctx) -> None:
    f = _arch.ARCHITECTURE_FILE
    data = _read(cfg, f, ctx)
    if data is None:
        return
    if not isinstance(data, dict):
        ctx.add(f, "", ERROR, "expected a JSON object; the whole file is ignored")
        return
    layer_ids = ", ".join(_arch.LAYER_IDS)
    for lid in _dict(data.get("layers")):
        if lid not in _arch.LAYER_IDS:
            ctx.add(f, f'layers["{lid}"]', ERROR, f"unknown layer; use one of {layer_ids}. The entry is dropped")
    for key, entry in _dict(data.get("components")).items():
        where = f'components["{key}"]'
        k = key.strip().strip("/")
        layer = _dict(entry).get("layer")
        if layer is not None and layer not in _arch.LAYER_IDS:
            ctx.add(f, f"{where}.layer", ERROR, f'"{layer}" is not a layer; use one of {layer_ids}. The layer is ignored')
        if k in ctx.paths or k in ctx.folder_paths:
            continue
        if ctx.has_prefix(k):
            near = sorted(p for p in ctx.folder_paths if p.startswith(k + "/"))[:3]
            ctx.add(f, where, ERROR,
                    f'"{k}" is not a folder codemap groups files by, so this entry never applies'
                    + (f" (try {', '.join(near)})" if near else ""))
        else:
            near = difflib.get_close_matches(k, sorted(ctx.folder_paths | ctx.paths), n=1, cutoff=0.6)
            ctx.add(f, where, ERROR,
                    f'"{k}" matches no file or folder in the graph.' + (f' Did you mean "{near[0]}"?' if near else ""))
    for kind in ("stores", "services"):
        for i, item in enumerate(_list(data.get(kind))):
            where = f"{kind}[{i}]"
            if not _s(_dict(item).get("label")):
                ctx.add(f, where, ERROR, "needs a `label`; entry is dropped")
                continue
            for p in _list(item.get("via")):
                pp = _s(p).strip("/")
                if pp and pp not in ctx.paths and pp not in ctx.folder_paths and not ctx.has_prefix(pp):
                    ctx.add(f, f"{where}.via", WARNING, f'"{pp}" matches no file or folder, so it links to no component')


def _check_glossary(cfg: Config, ctx: _Ctx, data: dict) -> None:
    shown = {t.lower() for t in _dict(_dict(data.get("glossary")).get("terms"))}
    inline = _dict(_dict(_read_quiet(cfg, _walkthrough.WALKTHROUGH_FILE)).get("glossary"))
    for term in inline:
        if _s(term) and _s(term).lower() not in shown:
            ctx.add(_walkthrough.WALKTHROUGH_FILE, f'glossary["{term}"]', WARNING,
                    "never appears in the authored prose, so it never shows as a tooltip")
    for key, entry in _glossary.load(cfg).items():
        if key.lower() not in shown and entry["display"].lower() not in shown:
            ctx.add(_glossary.GLOSSARY_FILE, f'items["{key}"]', WARNING,
                    "never appears in the authored prose, so it never shows as a tooltip")


# --------------------------------------------------------------------------- public


def authored_files(cfg: Config) -> list[str]:
    """The authored files that exist, for the "nothing to check" message."""
    return [n for n in _AUTHORED if (cfg.codemap_dir / n).exists()]


def check(data: dict, cfg: Config, *, all_keys: set[str] | None = None) -> list[Issue]:
    """Issues in the authored files, errors first. ``data`` is ``model.build()``'s
    output; ``all_keys`` is every symbol key in the graph (see :func:`graph_keys`),
    which lets a key cut by the ``max_symbols`` budget be told apart from a typo."""
    if data.get("empty"):
        return []
    ctx = _Ctx(data, all_keys)
    _check_explanations(cfg, ctx)
    _check_scenarios(cfg, ctx)
    _check_walkthrough(cfg, ctx)
    _check_libraries(cfg, ctx)
    _check_architecture(cfg, ctx)
    _check_glossary(cfg, ctx, data)
    order = {ERROR: 0, WARNING: 1}
    return sorted(ctx.issues, key=lambda i: (order[i.severity], i.file, i.path))


def graph_keys(conn: sqlite3.Connection, data: dict) -> set[str]:
    """Every symbol key at the model's commit, including ones the budget cut."""
    rows = conn.execute(
        "SELECT s.key FROM symbol_versions sv JOIN symbols s ON s.id = sv.symbol_id "
        "WHERE sv.commit_sha = ?",
        (data.get("commit"),),
    )
    return {r["key"] for r in rows}


def check_db(conn: sqlite3.Connection, cfg: Config, data: dict) -> list[Issue]:
    return check(data, cfg, all_keys=graph_keys(conn, data))


def summarize(issues: list[Issue]) -> dict:
    errors = sum(1 for i in issues if i.severity == ERROR)
    return {
        "ok": errors == 0,
        "errors": errors,
        "warnings": len(issues) - errors,
        "issues": [i.to_dict() for i in issues],
    }


def format_text(issues: list[Issue]) -> str:
    if not issues:
        return "codemap check: no problems found"
    s = summarize(issues)
    lines = [f"codemap check: {s['errors']} error(s), {s['warnings']} warning(s)"]
    for file in dict.fromkeys(i.file for i in issues):
        lines.append(f"\n{file}")
        mine = [i for i in issues if i.file == file]
        # a repo bigger than the node budget can leave dozens of keys cut; one line, not dozens
        cut = [i for i in mine if i.severity == WARNING and _BUDGET in i.message]
        fold = len(cut) > 3
        for i in mine:
            if fold and i in cut:
                continue
            loc = f"{i.path}: " if i.path else ""
            lines.append(f"  {i.severity:<7} {loc}{i.message}")
        if fold:
            eg = ", ".join(i.path for i in cut[:3])
            lines.append(
                f"  warning {len(cut)} keys were cut by the {_BUDGET} and will not show (e.g. {eg}); "
                "raise `max_symbols` under [explore] in .codemap/config.toml, or drop them. "
                "`--json` lists every one"
            )
    return "\n".join(lines)

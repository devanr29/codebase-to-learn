"""Files + imports -> a layered architecture model for the Architecture tab.

The Graph tab says what calls what, Map ▸ Layers says how deep each file sits
in the import graph. Neither says *"this is the API layer, it runs on Flask,
it talks to Postgres"*. This module does: it puts every indexed file in one
architectural **layer** (routes & entry → views / API → logic → data, plus a
side panel of shared code and tests), groups files into **components**, names
the tech each component uses, and adds the **data stores** and **outside
services** the code talks to — the shape of a hand-drawn architecture diagram,
derived deterministically.

Pure function over pieces ``model.py::build()`` already assembled (``files``,
``file_edges``, ``folders``, ``entry_points``, ``nodes``). The only I/O is the
optional manifest reader the caller injects (``package.json``,
``requirements*.txt``, ``pyproject.toml``, ``go.mod``, ``docker-compose*.yml``)
so infrastructure the code never imports directly — a Postgres container
behind an ORM URL — still shows up.

Placement is a **score, with evidence**, never a verdict: every point a file
earns towards a layer is recorded as a human-readable reason ("folder named
routes/", "HTTP route", "imports flask") and shipped to the page, which shows
it under "Why it's here". A file with no signal at all lands in Logic with
``confidence: "weak"`` and says so. Wrong guesses are corrected by authoring
``.codemap/architecture.json`` (see :func:`load` and
``references/architecture-schema.md``), never by hiding the evidence.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections import Counter
from functools import lru_cache
from pathlib import Path, PurePosixPath

from ..config import Config
from .folders import ROOT, _TEST_LIKE_RE

ARCHITECTURE_FILE = "architecture.json"
_CATALOG_PATH = Path(__file__).with_name("data") / "architecture-catalog.json"

# (id, default title, default one-line description). Order is the diagram's
# top-to-bottom order; `shared` / `tests` are the side panel.
LAYERS: list[tuple[str, str, str]] = [
    ("entry", "Routes & entry", "Where requests, commands and screens first arrive."),
    ("views", "Views & UI", "What the user actually sees — pages, templates, rendered output."),
    ("api", "API", "The shapes other programs talk to — endpoints, schemas, serializers."),
    ("logic", "Logic", "The work itself — rules, services, background jobs."),
    ("data", "Data & models", "What survives a restart — models, database access, storage."),
    ("shared", "Shared & cross-cutting", "Config, helpers and plugins every layer leans on."),
    ("tests", "Tests", "Checks the rest of the code; not part of the running app."),
]
LAYER_IDS = [lid for lid, _, _ in LAYERS]
RANK = {"entry": 0, "views": 1, "api": 1, "logic": 2, "data": 3}
SIDE = {"shared", "tests"}
# tie-break order when two layers score the same
_PRIORITY = ["tests", "entry", "api", "views", "data", "logic", "shared"]

# --------------------------------------------------------------- signals

# directory-segment tokens: nearest segment scores _W_DIR_NEAR, any ancestor _W_DIR_FAR
_DIR_TOKENS: dict[str, set[str]] = {
    "entry": {"routes", "route", "routers", "router", "urls", "controllers", "controller",
              "handlers", "cli", "commands", "cmd", "bin", "entrypoints"},
    "views": {"views", "view", "templates", "template", "components", "component", "pages",
              "screens", "ui", "site", "web", "frontend", "client", "public", "static",
              "assets", "styles", "css", "layouts", "widgets", "renderers", "presenters",
              "mobile"},
    "api": {"api", "apis", "endpoints", "serializers", "schemas", "graphql", "rest", "rpc",
            "proto", "dto", "dtos", "resources", "resolvers"},
    "logic": {"services", "service", "logic", "domain", "core", "usecases", "use_cases",
              "actions", "auth", "tasks", "jobs", "workers", "worker", "hooks", "store",
              "stores", "state", "reducers", "slices", "features", "business", "engine",
              "pipeline", "pipelines"},
    "data": {"models", "model", "db", "database", "databases", "repositories", "repository",
             "dao", "daos", "persistence", "storage", "migrations", "entities", "orm", "sql",
             "prisma"},
    "shared": {"utils", "util", "helpers", "helper", "common", "shared", "config", "configs",
               "settings", "constants", "types", "typings", "interfaces", "middleware",
               "middlewares", "plugins", "extensions", "scripts", "tools", "tooling", "i18n",
               "locales", "logging"},
}
# file-stem tokens (the stem is split on . _ -)
_STEM_TOKENS: dict[str, set[str]] = {
    "entry": {"cli", "main", "__main__", "app", "server", "wsgi", "asgi", "manage", "routes",
              "router", "urls", "controller", "controllers", "commands", "handler", "handlers"},
    "views": {"views", "view", "render", "renderer", "template", "templates", "report", "page",
              "screen", "layout", "component", "ui", "display", "presenter", "formatter"},
    "api": {"api", "serializers", "serializer", "schemas", "schema", "endpoints", "resolvers",
            "graphql", "dto"},
    "logic": {"service", "services", "tasks", "task", "jobs", "worker", "workers", "auth",
              "actions", "logic", "engine", "pipeline", "hooks", "store", "reducer", "slice"},
    "data": {"models", "model", "db", "database", "repository", "repositories", "dao", "orm",
             "migrations", "migration", "entities", "entity", "storage", "persistence",
             "queries"},
    "shared": {"config", "settings", "utils", "util", "helpers", "helper", "constants", "types",
               "conf", "logging", "logger", "middleware", "plugins"},
}
_W_ENTRY = 4.0      # a detected entry point of a decisive kind
_W_MAIN = 2.0       # a bare `__main__` guard — scripts have one too, so weaker than a folder
_W_DIR_NEAR = 3.0
_W_DIR_FAR = 2.0
_W_STEM = 2.5
_W_VENDOR = 1.0     # vendor imports only break ties: sqlite3 in 12 files is a type hint, not a layer
_W_UI_LANG = 1.0
_STRONG = 2.5

_ENTRY_ROLE = {"route": "entry", "controller": "entry", "cli": "entry", "script": "entry",
               "screen": "views", "layout": "views", "task": "logic"}
_ENTRY_TEXT = {"route": "HTTP route", "controller": "HTTP controller", "cli": "CLI command",
               "script": "console script", "main": "runs as a program (__main__)",
               "screen": "UI screen", "layout": "UI layout", "task": "background task"}
_ENTRY_LABELS = set(_ENTRY_TEXT.values())
_ENTRY_EV_RE = re.compile(r"^(\d+)× (.+)$")
_UI_MAIN_RE = re.compile(r"AppRegistry|registerRootComponent|render root")
_UI_LANGS = {"tsx"}
_TEST_STEM_RE = re.compile(r"^(test_.*|.*_test|conftest)$|\.(test|spec)$")

_LANG_LABEL = {"python": "Python", "typescript": "TypeScript", "tsx": "TypeScript",
               "javascript": "JavaScript", "go": "Go", "rust": "Rust", "java": "Java",
               "csharp": "C#", "cpp": "C++", "c": "C", "ruby": "Ruby", "php": "PHP"}

_FALLBACK_WHY = "no clear signal — placed in Logic by default"
_TRIVIAL_LOC = 5
_BARREL_STEMS = {"__init__", "index", "mod"}   # symbol-less package markers / re-export barrels
_PERVASIVE = 0.2       # a package imported by more than this share of non-test files isn't a layer signal
_SPLIT_MAX = 16        # a mixed-role folder's layer group is drawn file-by-file up to this size
_EVIDENCE_MAX = 5
_TECH_MAX = 6
_STACK_VENDORS_MAX = 6

# manifests
_EXCLUDE_DIR_NAMES = frozenset(
    {".git", ".venv", "venv", "node_modules", "vendor", "dist", "build", "target",
     "__pycache__", ".codemap"}
)
_MANIFEST_MAX = 60
_COMPOSE_RE = re.compile(r"^(docker-)?compose([.-][\w.-]+)?\.ya?ml$")
_REQ_RE = re.compile(r"^requirements([.-][\w.-]+)?\.txt$")
_IMAGE_RE = re.compile(r"^\s*image:\s*['\"]?([^\s'\"#]+)", re.MULTILINE)
_REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.\-]*)")
_GOMOD_RE = re.compile(r"^\s*(?:require\s+)?([\w.\-]+\.[\w.\-]+/[\w./\-]+)\s+v\d", re.MULTILINE)


@lru_cache(maxsize=1)
def _catalog() -> dict:
    try:
        data = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    return {
        "packages": data.get("packages") or {},
        "stores": data.get("stores") or {},
        "services": data.get("services") or {},
        "images": data.get("images") or {},
        "aliases": data.get("aliases") or {},
    }


def _lookup(name: str) -> tuple[str, dict] | None:
    """A package token or manifest dependency name -> (catalog key, entry)."""
    cat = _catalog()
    pk = cat["packages"]
    raw = (name or "").strip()
    if not raw:
        return None
    for cand in (raw, raw.lower(), raw.replace("-", "_"), raw.lower().replace("-", "_")):
        if cand in pk:
            return cand, pk[cand]
    alias = cat["aliases"].get(raw.lower())
    if alias and alias in pk:
        return alias, pk[alias]
    return None


# --------------------------------------------------------------- authored overrides


def load(cfg: Config) -> dict | None:
    """Load ``.codemap/architecture.json``. Returns the cleaned override dict or
    ``None`` when the file is absent, unreadable, malformed, or leaves nothing
    usable. Never raises. See ``references/architecture-schema.md``."""
    path = cfg.codemap_dir / ARCHITECTURE_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    return clean_overrides(data)


def _s(v) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


def clean_overrides(data) -> dict | None:
    if not isinstance(data, dict):
        return None
    out: dict = {}
    if _s(data.get("summary")):
        out["summary"] = _s(data["summary"])

    raw_layers = data.get("layers") if isinstance(data.get("layers"), dict) else {}
    layers = {}
    for lid, entry in raw_layers.items():
        if lid not in LAYER_IDS or not isinstance(entry, dict):
            continue
        clean = {k: _s(entry.get(k)) for k in ("title", "body") if _s(entry.get(k))}
        if clean:
            layers[lid] = clean
    if layers:
        out["layers"] = layers

    raw_comps = data.get("components") if isinstance(data.get("components"), dict) else {}
    comps = {}
    for key, entry in raw_comps.items():
        if not _s(key) or not isinstance(entry, dict):
            continue
        clean = {k: _s(entry.get(k)) for k in ("title", "body") if _s(entry.get(k))}
        if entry.get("layer") in LAYER_IDS:
            clean["layer"] = entry["layer"]
        tech = entry.get("tech")
        if isinstance(tech, list):
            labels = [_s(t) for t in tech if _s(t)]
            if labels:
                clean["tech"] = labels
        comps[key.strip().strip("/")] = clean
    if comps:
        out["components"] = comps

    for kind in ("stores", "services"):
        raw_items = data.get(kind) if isinstance(data.get(kind), list) else []
        items = []
        for entry in raw_items:
            if not isinstance(entry, dict) or not _s(entry.get("label")):
                continue
            ident = _s(entry.get("id")) or re.sub(r"[^a-z0-9]+", "-", entry["label"].lower()).strip("-")
            via = [p.strip().strip("/") for p in entry.get("via") or [] if _s(p)] \
                if isinstance(entry.get("via"), list) else []
            item = {"id": ident, "label": _s(entry["label"]), "via": via}
            for k in ("kind", "body"):
                if _s(entry.get(k)):
                    item[k] = _s(entry[k])
            items.append(item)
        if items:
            out[kind] = items
    return out or None


# --------------------------------------------------------------- manifests


def manifest_signals(paths: list[str], read_bytes) -> list[dict]:
    """Declared dependencies / container images, from the manifest files among
    ``paths``: ``[{"name", "kind": "package"|"image", "source"}]``.
    ``read_bytes(path) -> bytes | None``. Unparseable files are skipped."""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(name: str, kind: str, source: str) -> None:
        name = name.strip()
        if name and (name, kind) not in seen:
            seen.add((name, kind))
            out.append({"name": name, "kind": kind, "source": source})

    read = 0
    for path in sorted(paths or []):
        parts = path.split("/")
        if any(p in _EXCLUDE_DIR_NAMES for p in parts[:-1]):
            continue
        base = parts[-1]
        is_compose = bool(_COMPOSE_RE.match(base))
        if not (base in ("package.json", "pyproject.toml", "go.mod") or is_compose or _REQ_RE.match(base)):
            continue
        if read >= _MANIFEST_MAX:
            break
        read += 1
        blob = read_bytes(path)
        if not blob:
            continue
        text = blob.decode("utf-8", "replace")
        if base == "package.json":
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            for field in ("dependencies", "devDependencies"):
                deps = data.get(field)
                if isinstance(deps, dict):
                    for name in deps:
                        add(str(name), "package", path)
        elif base == "pyproject.toml":
            try:
                data = tomllib.loads(text)
            except tomllib.TOMLDecodeError:
                continue
            for spec in (data.get("project") or {}).get("dependencies") or []:
                m = _REQ_NAME_RE.match(str(spec))
                if m:
                    add(m.group(1), "package", path)
            poetry = ((data.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
            if isinstance(poetry, dict):
                for name in poetry:
                    if name.lower() != "python":
                        add(str(name), "package", path)
        elif base == "go.mod":
            for m in _GOMOD_RE.finditer(text):
                add(m.group(1), "package", path)
        elif is_compose:
            for m in _IMAGE_RE.finditer(text):
                add(m.group(1), "image", path)
        else:  # requirements*.txt
            for line in text.splitlines():
                line = line.split("#", 1)[0].strip()
                if not line or line.startswith("-"):
                    continue
                m = _REQ_NAME_RE.match(line)
                if m:
                    add(m.group(1), "package", path)
    return out


def _image_store(image: str) -> str | None:
    name = image.rsplit("/", 1)[-1].split(":", 1)[0].split("@", 1)[0].lower()
    images = _catalog()["images"]
    if name in images:
        return images[name]
    for key in sorted(images, key=len, reverse=True):
        if key in name:
            return images[key]
    return None


# --------------------------------------------------------------- scoring


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[._\-]+", s.lower()) if t} | {s.lower()}


def _is_test(path: str) -> bool:
    pp = PurePosixPath(path)
    stem = pp.name.rsplit(".", 1)[0] if "." in pp.name else pp.name
    parent = path.rsplit("/", 1)[0] if "/" in path else ""
    return bool(_TEST_LIKE_RE.search(parent) or _TEST_STEM_RE.match(stem.lower()))


def _score_file(
    f: dict, entry_kinds: list[tuple[str, str]], pervasive: set[str]
) -> tuple[dict, dict]:
    """-> (score per layer, evidence strings per layer)."""
    score: dict[str, float] = {}
    why: dict[str, list[str]] = {}

    def bump(layer: str, pts: float, text: str) -> None:
        score[layer] = score.get(layer, 0.0) + pts
        why.setdefault(layer, []).append(text)

    path = f["path"]
    pp = PurePosixPath(path)
    stem = pp.name.rsplit(".", 1)[0] if "." in pp.name else pp.name

    if _is_test(path):
        bump("tests", 100.0, "test file")
        return score, why

    # folder segments — nearest first
    segs = list(pp.parent.parts) if str(pp.parent) != "." else []
    best_dir: dict[str, tuple[float, str]] = {}
    for depth, seg in enumerate(reversed(segs)):
        w = _W_DIR_NEAR if depth == 0 else _W_DIR_FAR
        toks = _tokens(seg)
        for layer, words in _DIR_TOKENS.items():
            if toks & words and (layer not in best_dir or w > best_dir[layer][0]):
                best_dir[layer] = (w, f"folder named {seg}/")
    for layer, (w, text) in best_dir.items():
        bump(layer, w, text)

    # file stem
    stoks = _tokens(stem)
    for layer, words in _STEM_TOKENS.items():
        if stoks & words:
            bump(layer, _W_STEM, f"file named {pp.name}")

    if f.get("lang") in _UI_LANGS:
        bump("views", _W_UI_LANG, "JSX/TSX component file")

    # entry points. An HTTP handler is "where a request arrives", but when its
    # own path already says API or views (`api/datasets.py`, `views/pages.py`)
    # that's the layer it belongs in — CKAN's Routes band is the URL map, the
    # handlers themselves live in Views / API below it.
    path_layer = {layer for layer in ("api", "views") if score.get(layer)}
    kinds = Counter()
    for kind, detail in entry_kinds:
        if kind == "main":
            layer = "views" if _UI_MAIN_RE.search(detail or "") else "entry"
            kinds[(layer, "main" if layer == "entry" else "screen")] += 1
        elif kind in ("route", "controller") and path_layer:
            layer = "api" if "api" in path_layer else "views"
            kinds[(layer, kind)] += 1
        elif kind in _ENTRY_ROLE:
            kinds[(_ENTRY_ROLE[kind], kind)] += 1
    for (layer, kind), n in kinds.items():
        label = _ENTRY_TEXT.get(kind, kind)
        bump(layer, _W_MAIN if kind == "main" else _W_ENTRY, label if n == 1 else f"{n}× {label}")

    # vendor imports — a tie-breaker only. Once a path or an entry point has
    # picked a layer, an import can agree but never overrule it (`site/model.py`
    # imports sqlite3 and is still view code), and a package imported all over
    # the repo (`pervasive` — sqlite3 as a type hint in a dozen modules) says
    # nothing about any one file's layer at all.
    vend: dict[str, str] = {}
    for d in f.get("deps") or []:
        if d.get("kind") not in ("third_party", "stdlib") or d["name"] in pervasive:
            continue
        hit = _lookup(d["name"])
        if hit and hit[1].get("role") and hit[1]["role"] not in vend:
            vend[hit[1]["role"]] = d["name"]
    top = max(score.values(), default=0.0)
    tied = {layer for layer, v in score.items() if v == top} if top > 0 else set(vend)
    for layer, pkg in vend.items():
        if layer in tied:
            bump(layer, _W_VENDOR, f"imports {pkg}")

    return score, why


def _pick(score: dict[str, float]) -> str | None:
    if not score:
        return None
    top = max(score.values())
    if top <= 0:
        return None
    return next(layer for layer in _PRIORITY if score.get(layer) == top)


# --------------------------------------------------------------- build


def build(
    files: list[dict],
    file_edges: list[dict],
    folders: list[dict],
    entry_points: list[dict],
    nodes: list[dict],
    *,
    manifest_paths: list[str] | None = None,
    read_bytes=None,
    overrides: dict | None = None,
) -> dict:
    """See the module docstring and ``references/architecture-schema.md``."""
    overrides = overrides or {}
    cat = _catalog()
    by_path = {f["path"]: f for f in files}

    # entry points per file
    entry_by_fi: dict[int, list[tuple[str, str]]] = {}
    entry_nodes_by_fi: dict[int, list[int]] = {}
    fi_of_node = {n["i"]: by_path[n["file"]]["fi"] for n in nodes if n.get("file") in by_path}
    for ep in entry_points:
        ni = ep.get("node")
        if ni is None or ni not in fi_of_node:
            continue
        fi = fi_of_node[ni]
        entry_by_fi.setdefault(fi, []).append((ep.get("kind") or "", ep.get("detail") or ""))
        entry_nodes_by_fi.setdefault(fi, []).append(ni)

    importers = Counter()
    prod_importers = Counter()
    for f in files:
        test = _is_test(f["path"])
        for d in f.get("deps") or []:
            importers[d["name"]] += 1
            if not test:
                prod_importers[d["name"]] += 1
    n_prod = sum(1 for f in files if not _is_test(f["path"]))
    pervasive = {name for name, n in prod_importers.items() if n > max(2, _PERVASIVE * n_prod)}

    # post-fold folder of every file
    folder_of: dict[int, str] = {}
    for fo in folders:
        for fi in fo.get("files") or []:
            folder_of[fi] = fo["path"]
    for f in files:
        folder_of.setdefault(f["fi"], f["path"].rsplit("/", 1)[0] if "/" in f["path"] else ROOT)
    folder_paths = {fo["path"] for fo in folders} | set(folder_of.values())

    # -- per-file layer --------------------------------------------------------
    layer_of: dict[int, str] = {}
    why_of: dict[int, list[str]] = {}
    strong_of: dict[int, bool] = {}
    trivial: set[int] = set()
    for f in files:
        fi = f["fi"]
        score, why = _score_file(f, entry_by_fi.get(fi, []), pervasive)
        layer = _pick(score)
        if layer is None:
            layer_of[fi] = "logic"
            why_of[fi] = [_FALLBACK_WHY]
            strong_of[fi] = False
        else:
            layer_of[fi] = layer
            why_of[fi] = why.get(layer, [])
            strong_of[fi] = score[layer] >= _STRONG
        stem = PurePosixPath(f["path"]).stem
        if not f.get("symbols") and fi not in entry_by_fi and (
            (f.get("loc") or 0) <= _TRIVIAL_LOC or stem in _BARREL_STEMS
        ):
            trivial.add(fi)

    # -- authored placement ----------------------------------------------------
    comp_over = overrides.get("components") or {}
    file_split: set[int] = set()        # a file key: its own component
    folder_whole: set[str] = set()      # a folder key: one component for the folder
    authored_fis: set[int] = set()
    for key, entry in comp_over.items():
        if key in by_path:
            fi = by_path[key]["fi"]
            file_split.add(fi)
            trivial.discard(fi)
            if entry.get("layer"):
                layer_of[fi] = entry["layer"]
                strong_of[fi] = True
                authored_fis.add(fi)
        elif key in folder_paths:
            folder_whole.add(key)
            members = [fi for fi, p in folder_of.items() if p == key and fi not in file_split]
            if entry.get("layer"):
                target = entry["layer"]
            else:
                loc_by = Counter()
                for fi in members:
                    loc_by[layer_of[fi]] += max(1, files[fi].get("loc") or 0)
                target = loc_by.most_common(1)[0][0] if loc_by else "logic"
            for fi in members:
                if fi in file_split:
                    continue
                layer_of[fi] = target
                if entry.get("layer"):
                    strong_of[fi] = True
                    authored_fis.add(fi)
    for fi in authored_fis:
        # the derived reasons stay visible under the authored one, except the
        # "no clear signal" fallback — which the author just supplied
        why_of[fi] = ["placed here by architecture.json"] + [t for t in why_of[fi] if t != _FALLBACK_WHY]

    # -- group into components -------------------------------------------------
    by_folder: dict[str, list[int]] = {}
    for f in files:
        by_folder.setdefault(folder_of[f["fi"]], []).append(f["fi"])

    comp_files: dict[str, list[int]] = {}
    comp_meta: dict[str, dict] = {}
    pending_triv: dict[str, list[int]] = {}

    def comp_id(layer: str, path: str) -> str:
        return f"{layer}:{path}"

    for folder, fis in sorted(by_folder.items()):
        real = [fi for fi in fis if fi not in trivial]
        roles = {layer_of[fi] for fi in real if layer_of[fi] != "tests"}
        mixed = len(roles) >= 2 and folder not in folder_whole
        groups: dict[str, list[int]] = {}
        for fi in real:
            groups.setdefault(layer_of[fi], []).append(fi)
        for layer, members in groups.items():
            split = [fi for fi in members if fi in file_split]
            rest = [fi for fi in members if fi not in file_split]
            per_file = mixed and len(rest) <= _SPLIT_MAX
            for fi in split + (rest if per_file else []):
                cid = comp_id(layer, files[fi]["path"])
                comp_files[cid] = [fi]
                comp_meta[cid] = {"layer": layer, "path": files[fi]["path"], "kind": "file",
                                  "folder": folder, "mixed": mixed}
            if rest and not per_file:
                cid = comp_id(layer, folder)
                comp_files[cid] = rest
                comp_meta[cid] = {"layer": layer, "path": folder, "kind": "folder",
                                  "folder": folder, "mixed": mixed}
        triv = [fi for fi in fis if fi in trivial]
        if triv:
            pending_triv[folder] = triv

    # trivial files ride along with the folder's biggest component — or, for a
    # folder that is nothing but a package marker (a bare `app/__init__.py`),
    # with the biggest component below it, so it never becomes an empty box
    def _size(c: str) -> int:
        return sum(files[x].get("loc") or 0 for x in comp_files[c])

    for folder, triv in pending_triv.items():
        here = [cid for cid, m in comp_meta.items() if m["folder"] == folder]
        if not here:
            prefix = "" if folder == ROOT else folder + "/"
            here = [cid for cid, m in comp_meta.items()
                    if m["folder"].startswith(prefix) and m["folder"] != folder]
        if here:
            host = max(here, key=lambda c: (_size(c), c))
        else:
            host = comp_id(layer_of[triv[0]], folder)
            comp_files[host] = []
            comp_meta[host] = {"layer": layer_of[triv[0]], "path": folder, "kind": "folder",
                               "folder": folder, "mixed": False}
        comp_files[host].extend(triv)

    comp_of: dict[int, str] = {fi: cid for cid, fis in comp_files.items() for fi in fis}

    # -- component details -----------------------------------------------------
    components: list[dict] = []
    stores: dict[str, dict] = {}
    services: dict[str, dict] = {}
    for cid in sorted(comp_files, key=lambda c: (LAYER_IDS.index(comp_meta[c]["layer"]), comp_meta[c]["path"])):
        meta = comp_meta[cid]
        ordered = sorted(comp_files[cid], key=lambda fi: (-(files[fi].get("loc") or 0), files[fi]["path"]))
        # symbol-less `__init__.py` / `index.ts` riders stay mapped (imports resolve
        # through them) but aren't listed as the component's own files — a
        # one-file box shouldn't read "2 files" because of an empty package marker
        fis = [fi for fi in ordered if fi not in trivial] or ordered
        extra = [fi for fi in ordered if fi not in fis]
        loc = sum(files[fi].get("loc") or 0 for fi in fis)
        ev = Counter()
        entry_n = Counter()
        order: list[str] = []
        for fi in fis:
            if fi in trivial:
                continue
            for text in why_of.get(fi, []):
                m = _ENTRY_EV_RE.match(text)
                label = m.group(2) if m else text
                if label in _ENTRY_LABELS:
                    if label not in entry_n:
                        order.append(label)
                    entry_n[label] += int(m.group(1)) if m else 1
                    continue
                if text not in ev:
                    order.append(text)
                ev[text] += 1
        evidence = [
            (t if entry_n[t] == 1 else f"{entry_n[t]}× {t}") if t in entry_n
            else (t if ev[t] == 1 or len(fis) == 1 else f"{t} ({ev[t]} files)")
            for t in order
        ][:_EVIDENCE_MAX]
        strong_loc = sum(max(1, files[fi].get("loc") or 0) for fi in fis if strong_of.get(fi) and fi not in trivial)
        weak_loc = sum(max(1, files[fi].get("loc") or 0) for fi in fis if not strong_of.get(fi) and fi not in trivial)

        tech_count: Counter = Counter()
        tech_pkg: dict[str, str] = {}
        store_ids: set[str] = set()
        service_ids: set[str] = set()
        for fi in fis:
            seen_here: set[str] = set()
            for d in files[fi].get("deps") or []:
                if d.get("kind") not in ("third_party", "stdlib"):
                    continue
                hit = _lookup(d["name"])
                if hit:
                    _, entry = hit
                    label = entry.get("label") or d["name"]
                    if entry.get("store"):
                        store_ids.add(entry["store"])
                        _attach(stores, entry["store"], cat["stores"], d["name"], cid, "import")
                    if entry.get("service"):
                        service_ids.add(entry["service"])
                        _attach(services, entry["service"], cat["services"], d["name"], cid, "import")
                elif d.get("kind") == "third_party":
                    label = d["name"]
                else:
                    continue
                # a pervasive package (sqlite3 as a type hint everywhere) still
                # links this box to its store, but only labels the box whose
                # layer it actually belongs to — otherwise every label reads "SQLite"
                if d["name"] in pervasive and hit and hit[1].get("store") and hit[1].get("role") != meta["layer"]:
                    continue
                if label not in seen_here:
                    seen_here.add(label)
                    tech_count[label] += 1
                    tech_pkg.setdefault(label, d["name"])
        tech = [{"label": lbl, "package": tech_pkg[lbl]}
                for lbl, _ in sorted(tech_count.items(), key=lambda p: (-p[1], p[0]))][:_TECH_MAX]

        path = meta["path"]
        if meta["kind"] == "file":
            title = PurePosixPath(path).name
        elif not meta["mixed"]:
            title = "project root" if path == ROOT else path.rsplit("/", 1)[-1]
        else:
            stems = [PurePosixPath(files[fi]["path"]).stem for fi in fis if fi not in trivial][:2]
            extra = len([fi for fi in fis if fi not in trivial]) - len(stems)
            title = " · ".join(stems) + (f" +{extra}" if extra > 0 else "")

        entries = sorted({ni for fi in fis for ni in entry_nodes_by_fi.get(fi, [])})
        components.append({
            "id": cid,
            "title": title,
            "layer": meta["layer"],
            "path": path,
            "kind": meta["kind"],
            "files": fis,
            "extra_files": extra,
            "loc": loc,
            "tech": tech,
            "evidence": evidence,
            "confidence": "strong" if strong_loc >= weak_loc and strong_loc > 0 else "weak",
            "entries": entries,
            "stores": sorted(store_ids),
            "services": sorted(service_ids),
            "body": None,
            "authored": any(fi in authored_fis for fi in fis),
        })
    comp_by_id = {c["id"]: c for c in components}

    # -- authored titles / bodies / tech --------------------------------------
    for key, entry in comp_over.items():
        targets = [c for c in components if c["path"] == key]
        for c in targets:
            if entry.get("title"):
                c["title"] = entry["title"]
            if entry.get("body"):
                c["body"] = entry["body"]
            if entry.get("tech"):
                known = {t["label"] for t in c["tech"]}
                c["tech"] = [{"label": t, "package": None} for t in entry["tech"] if t not in known] + c["tech"]
            c["authored"] = True

    # -- manifests -------------------------------------------------------------
    declared_only: list[dict] = []
    imported_tokens = set(importers)
    if manifest_paths and read_bytes is not None:
        for sig in manifest_signals(manifest_paths, read_bytes):
            src = sig["source"]
            if sig["kind"] == "image":
                sid = _image_store(sig["name"])
                if sid:
                    _declare(stores, sid, cat["stores"], src)
                continue
            hit = _lookup(sig["name"])
            if not hit:
                continue
            key, entry = hit
            if entry.get("store"):
                _declare(stores, entry["store"], cat["stores"], src)
            if entry.get("service"):
                _declare(services, entry["service"], cat["services"], src)
            if (key not in imported_tokens and sig["name"] not in imported_tokens
                    and not entry.get("store") and not entry.get("service")
                    and (entry.get("stack") or entry.get("role") in ("entry", "views", "api", "data"))):
                label = entry.get("label") or key
                if all(d["label"] != label for d in declared_only):
                    declared_only.append({"label": label, "package": key, "source": src})

    # -- authored stores / services -------------------------------------------
    def resolve_via(via: list[str]) -> list[str]:
        out: list[str] = []
        for p in via:
            for c in components:
                if c["path"] == p or any(files[fi]["path"] == p for fi in c["files"]) \
                        or c["path"].startswith(p + "/"):
                    if c["id"] not in out:
                        out.append(c["id"])
        return out

    for kind, bucket, defaults in (("stores", stores, cat["stores"]), ("services", services, cat["services"])):
        for item in overrides.get(kind) or []:
            cur = bucket.get(item["id"])
            if cur is None:
                base = defaults.get(item["id"]) or {}
                cur = bucket[item["id"]] = {
                    "id": item["id"], "label": base.get("label") or item["label"],
                    "kind": base.get("kind") or "", "via": [], "source": "authored",
                    "declared_in": [], "components": [],
                }
            cur["label"] = item["label"]
            if item.get("kind"):
                cur["kind"] = item["kind"]
            if item.get("body"):
                cur["body"] = item["body"]
            cur["authored"] = True
            for cid in resolve_via(item.get("via") or []):
                if cid not in cur["components"]:
                    cur["components"].append(cid)
                field = "stores" if kind == "stores" else "services"
                if item["id"] not in comp_by_id[cid][field]:
                    comp_by_id[cid][field].append(item["id"])

    # -- links between components ---------------------------------------------
    agg: Counter = Counter()
    for e in file_edges:
        s, t = comp_of.get(e["s"]), comp_of.get(e["t"])
        if s is None or t is None or s == t:
            continue
        agg[(s, t)] += 1
    links = []
    for (s, t), n in sorted(agg.items()):
        ls, lt = comp_by_id[s]["layer"], comp_by_id[t]["layer"]
        if ls in SIDE or lt in SIDE:
            direction = "side"
        elif RANK[ls] < RANK[lt]:
            direction = "down"
        elif RANK[ls] > RANK[lt]:
            direction = "up"
        else:
            direction = "same"
        links.append({"s": s, "t": t, "n": n, "dir": direction})

    # -- actors ----------------------------------------------------------------
    actors: dict[str, dict] = {}
    for fi, kinds in entry_by_fi.items():
        cid = comp_of.get(fi)
        if cid is None or comp_by_id[cid]["layer"] in SIDE:
            continue
        for kind, detail in kinds:
            if kind in ("route", "controller"):
                aid, label, akind = "internet", "Internet", "web"
            elif kind in ("screen", "layout") or (kind == "main" and _UI_MAIN_RE.search(detail)):
                aid, label, akind = "device", "User's screen", "ui"
            elif kind == "task":
                aid, label, akind = "scheduler", "Scheduler", "jobs"
            elif kind in ("cli", "script") or (kind == "main" and comp_by_id[cid]["layer"] == "entry"):
                aid, label, akind = "terminal", "Terminal", "cli"
            else:
                continue
            a = actors.setdefault(aid, {"id": aid, "label": label, "kind": akind, "entries": 0, "components": []})
            a["entries"] += 1
            if cid not in a["components"]:
                a["components"].append(cid)
    actor_order = ["internet", "device", "terminal", "scheduler"]

    # -- layers ----------------------------------------------------------------
    layer_over = overrides.get("layers") or {}
    layers = []
    for lid, title, body in LAYERS:
        o = layer_over.get(lid) or {}
        layers.append({
            "id": lid,
            "title": o.get("title") or title,
            "body": o.get("body") or body,
            "authored": bool(o),
            "components": [c["id"] for c in components if c["layer"] == lid],
        })

    # -- stack summary ---------------------------------------------------------
    stack: list[str] = []
    lang_loc: Counter = Counter()
    for f in files:
        if layer_of.get(f["fi"]) == "tests":
            continue
        label = _LANG_LABEL.get(f.get("lang") or "")
        if label:
            lang_loc[label] += f.get("loc") or 0
    total = sum(lang_loc.values()) or 1
    stack += [lbl for lbl, n in lang_loc.most_common() if n / total >= 0.08]
    vend_rank: Counter = Counter()
    vend_layer: dict[str, int] = {}
    for f in files:
        if layer_of.get(f["fi"]) == "tests":
            continue
        for d in f.get("deps") or []:
            hit = _lookup(d["name"]) if d.get("kind") in ("third_party", "stdlib") else None
            if hit and hit[1].get("stack"):
                lbl = hit[1].get("label") or hit[0]
                vend_rank[lbl] += 1
                vend_layer[lbl] = min(vend_layer.get(lbl, 9), RANK.get(hit[1].get("role") or "", 2))
    for lbl, _ in sorted(vend_rank.items(), key=lambda p: (vend_layer[p[0]], -p[1], p[0]))[:_STACK_VENDORS_MAX]:
        if lbl not in stack:
            stack.append(lbl)
    for bucket in (stores, services):
        for item in bucket.values():
            if item["label"] not in stack:
                stack.append(item["label"])

    return {
        "summary": overrides.get("summary"),
        "stack": stack,
        "actors": [actors[a] for a in actor_order if a in actors],
        "layers": layers,
        "components": components,
        "stores": sorted(stores.values(), key=lambda s: (-len(s["components"]), s["label"])),
        "services": sorted(services.values(), key=lambda s: (-len(s["components"]), s["label"])),
        "links": links,
        "declared_only": declared_only,
        "authored": bool(overrides),
    }


def _attach(bucket: dict, sid: str, defaults: dict, pkg: str, cid: str, source: str) -> None:
    base = defaults.get(sid) or {}
    cur = bucket.setdefault(sid, {
        "id": sid, "label": base.get("label") or sid, "kind": base.get("kind") or "",
        "via": [], "source": source, "declared_in": [], "components": [],
    })
    if pkg not in cur["via"]:
        cur["via"].append(pkg)
    if cid not in cur["components"]:
        cur["components"].append(cid)


def _declare(bucket: dict, sid: str, defaults: dict, source: str) -> None:
    base = defaults.get(sid) or {}
    cur = bucket.setdefault(sid, {
        "id": sid, "label": base.get("label") or sid, "kind": base.get("kind") or "",
        "via": [], "source": "declared", "declared_in": [], "components": [],
    })
    if source not in cur["declared_in"]:
        cur["declared_in"].append(source)

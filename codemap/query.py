"""What ``codemap mcp`` serves: read tools over the index, the **open
questions** static analysis could not settle, and **verified** write tools that
turn an AI reviewer's answers into ``.codemap/*.json``.

Kept free of the MCP SDK on purpose: every tool is a plain method on
:class:`Session` returning a JSON-serializable dict, so the tests (and any
other client) can call them directly. ``mcp_server.py`` only registers them.

The contract with the AI model:

1. Read: ``overview``, ``find_symbol``, ``explain_symbol``, ``calls``,
   ``impact``, ``read_source``, ``search_code``, ``get_authored``.
2. Investigate: ``open_questions`` lists what the graph can't prove -- a call
   matched by name only, a request no route serves, a part of the architecture
   with no layer signal, a workflow with no scenario, a symbol or package with no
   explanation, a config key that hints at an outside system.
3. Answer: ``answer_question`` / ``submit``. Nothing is written unless it
   passes the same checks as ``codemap check``, every symbol key resolves, and
   every ``file:line`` of evidence exists in the index. A rejected write says
   why, so the model can fix it or drop it.

Writes stay under ``.codemap/``. The server never scans, never edits code and
never calls an LLM itself.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path

from . import calls as _calls
from . import config, db
from .impact import AMBIGUOUS, _is_test_path
from .site import architecture as _arch
from .site import explain as _explain
from .site import findings as _findings
from .site import glossary as _glossary
from .site import libraries as _libraries
from .site import scenarios as _scenarios
from .site import validate as _validate
from .site import walkthrough as _walkthrough

MAX_SOURCE_LINES = 400
MAX_SEARCH_HITS = 200

QUESTION_KINDS = (
    "ambiguous_call",      # a call matched by name alone: which candidate is real?
    "unmatched_request",   # a web request no route in the repo serves
    "orphan",              # a function nothing calls and no entry point reaches
    "weak_placement",      # an Architecture box placed with no layer signal
    "wrong_way",           # a lower layer importing a higher one
    "hidden_dependency",   # a config key that hints at a database or outside service
    "unnamed_flow",        # an entry point no scenario covers
    "unexplained_symbol",  # an important symbol with no explanation
    "unexplained_package", # an imported package with no Packages entry
)

SUBMIT_KINDS = ("explanation", "scenario", "library", "walkthrough", "architecture", "glossary")

_FILE_OF = {
    "explanation": _explain.EXPLAIN_FILE,
    "scenario": _scenarios.SCENARIOS_FILE,
    "library": _libraries.LIBRARIES_FILE,
    "walkthrough": _walkthrough.WALKTHROUGH_FILE,
    "architecture": _arch.ARCHITECTURE_FILE,
    "glossary": _glossary.GLOSSARY_FILE,
}

# config keys that usually name a database, a queue or an outside service
_HINT = re.compile(
    r"(DATABASE|_DB\b|^DB_|POSTGRES|MYSQL|MONGO|REDIS|SQLITE|_URL$|_URI$|_HOST$|_DSN$|API_KEY|"
    r"_SECRET|_TOKEN$|BUCKET|QUEUE|KAFKA|RABBIT|AMQP|SMTP|MAIL|S3_|STRIPE|TWILIO|SENTRY|OPENAI|ANTHROPIC)",
    re.I,
)
_PY_ENV = re.compile(r"""os\.(?:environ\.get|getenv)\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']|os\.environ\[\s*["']([A-Za-z_][A-Za-z0-9_]*)["']\s*\]""")


class QueryError(Exception):
    """A caller mistake (unknown key, bad argument). The message is for the model."""


def _today() -> str:
    return date.today().isoformat()


def _s(v: object) -> str:
    return v.strip() if isinstance(v, str) else ""


class Session:
    """One repo, opened read-only; the model and graph are rebuilt only when
    the index or an authored file changes."""

    def __init__(self, root: Path | str | None = None) -> None:
        from .cli import _find_root

        self.root = _find_root(Path(root) if root else None)
        self.cfg = config.load(self.root)
        self._sig: tuple | None = None
        self._cache: dict = {}

    # ------------------------------------------------------------------ plumbing

    def _signature(self) -> tuple:
        parts = []
        for p in [self.cfg.db_path, *(self.cfg.codemap_dir / n for n in (*_FILE_OF.values(), _findings.FINDINGS_FILE))]:
            try:
                st = p.stat()
                parts.append((p.name, st.st_mtime_ns, st.st_size))
            except OSError:
                parts.append((p.name, None, None))
        return tuple(parts)

    def _ensure(self) -> None:
        sig = self._signature()
        if sig == self._sig:
            return
        if not self.cfg.db_path.exists():
            raise QueryError("no index yet — run `codemap scan` in the repo first")
        from .site import model as _model

        conn = db.connect(self.cfg.db_path)
        try:
            data = _model.build(conn, self.cfg, max_symbols=self.cfg.explore.max_symbols,
                                max_snippet_lines=self.cfg.explore.max_snippet_lines, max_source_bytes=0)
            if data.get("empty"):
                raise QueryError("the index is empty — run `codemap scan` first")
            sha = data["commit"]
            symbols = _calls.load_symbols(conn, sha)
            g = _calls.build_graph(conn, self.cfg, sha)
            raw_lines = _calls.call_lines(conn, sha)
        finally:
            conn.close()
        line_text = _model.line_reader(self.cfg, sha)
        _findings.apply_calls(g, _findings.load(self.cfg), line_text)
        self._cache = {
            "data": data, "sha": sha, "symbols": symbols, "g": g, "raw_lines": raw_lines,
            "line_text": line_text, "node_by_key": {n["key"]: n for n in data["nodes"]},
            "paths": {f["path"] for f in data["files"]},
        }
        self._sig = self._signature()

    @property
    def data(self) -> dict:
        self._ensure()
        return self._cache["data"]

    def _c(self, name: str):
        self._ensure()
        return self._cache[name]

    def meta(self) -> dict:
        d = self.data
        changed = 0
        try:
            scanned = self.cfg.db_path.stat().st_mtime
            for f in d["files"]:
                try:
                    if (self.root / f["path"]).stat().st_mtime > scanned:
                        changed += 1
                except OSError:
                    pass
        except OSError:
            pass
        stale = bool(d.get("behind")) or changed > 0
        out = {"index_sha": d["commit_short"], "commits_behind": d.get("behind", 0),
               "files_changed_since_scan": changed, "stale": stale}
        if stale:
            out["hint"] = "the index is behind the code — ask the user to run `codemap scan`"
        return out

    def _resolve(self, target: str) -> _calls.Sym:
        hits = _calls.find(self._c("symbols"), target)
        if not hits:
            raise QueryError(f"no symbol matches {target!r} — use find_symbol to get its `path::Name` key")
        if len(hits) > 1:
            raise QueryError(f"{target!r} matches {len(hits)} symbols: "
                             + ", ".join(s.key for s in hits[:12]) + " — pass one of these keys")
        return hits[0]

    def _where(self, key: str) -> str:
        s = self._c("symbols").get(key)
        return f"{s.file}:{s.start}" if s else key

    def _check_evidence(self, evidence: object, *, required: bool) -> list[dict]:
        ev = _findings.clean_evidence(evidence)
        if required and not ev:
            raise QueryError("evidence is required: a list of {file, line, note?} pointing at the code "
                             "that proves the answer (read it with read_source first)")
        line_text = self._c("line_text")
        for e in ev:
            if e["file"] not in self._c("paths"):
                raise QueryError(f'evidence file "{e["file"]}" is not an indexed file')
            text = line_text(e["file"], e["line"])
            if text is None:
                raise QueryError(f'evidence {e["file"]}:{e["line"]} is past the end of the file')
            if e.get("text") and _findings.norm(e["text"]) not in _findings.norm(text):
                raise QueryError(f'evidence {e["file"]}:{e["line"]} reads {text.strip()!r}, not {e["text"]!r}')
            e["text"] = text.strip()[:300]
        return ev

    # ------------------------------------------------------------------ read tools

    def overview(self) -> dict:
        d = self.data
        arch = d.get("architecture") or {}
        comps = {c["id"]: c for c in arch.get("components", [])}
        wt = d.get("walkthrough") or {}
        nodes = d["nodes"]
        return {
            "root": str(self.root),
            "summary": arch.get("summary") or ((wt.get("intro") or {}).get("what")),
            "stack": arch.get("stack", []),
            "stats": d["stats"],
            "layers": [
                {"id": layer["id"], "title": layer.get("title"),
                 "components": [{"path": comps[c]["path"], "title": comps[c]["title"],
                                 "confidence": comps[c]["confidence"]}
                                for c in layer.get("components", []) if c in comps]}
                for layer in arch.get("layers", [])
            ],
            "stores": [s["label"] for s in arch.get("stores", [])],
            "services": [s["label"] for s in arch.get("services", [])],
            "entry_points": [
                {"kind": e["kind"], "detail": e["detail"],
                 "key": nodes[e["node"]]["key"] if e.get("node") is not None else None}
                for e in d.get("entry_points", [])[:80]
            ],
            "folders": [{"path": f["path"], "files": len(f.get("files") or [])} for f in d.get("folders", [])][:60],
            "authored": _validate.authored_files(self.cfg),
            "open_questions": self.open_questions(limit=0)["counts"],
            **self.meta(),
        }

    def find_symbol(self, query: str, limit: int = 20) -> dict:
        symbols = self._c("symbols")
        hits = _calls.find(symbols, query)
        if not hits:
            q = query.strip().lower()
            hits = sorted((s for s in symbols.values() if q in s.key.lower()), key=lambda s: (len(s.key), s.key))
        node_by_key = self._c("node_by_key")
        return {"matches": [
            {"key": s.key, "kind": s.kind, "where": s.where(), "line": [s.start, s.end],
             "explained": bool((node_by_key.get(s.key) or {}).get("explain")),
             "in_explorer": s.key in node_by_key}
            for s in hits[:max(1, limit)]
        ], "total": len(hits)}

    def _neighbours(self, key: str, out: bool) -> list[dict]:
        g = self._c("g")
        if key not in g:
            return []
        rows = []
        for m in sorted(g.successors(key) if out else g.predecessors(key)):
            src, dst = (key, m) if out else (m, key)
            ed = g.edges[src, dst]
            row = {"key": m, "where": self._where(m), "confidence": ed.get("confidence", AMBIGUOUS)}
            if ed.get("review"):
                row["reviewed"] = ed["review"]
            rows.append(row)
        return rows

    def explain_symbol(self, target: str) -> dict:
        s = self._resolve(target)
        node = self._c("node_by_key").get(s.key) or {}
        src = self.read_source(s.file, s.start, min(s.end, s.start + 120))
        return {
            "key": s.key, "kind": s.kind, "where": s.where(), "line": [s.start, s.end],
            "signature": node.get("sig"), "docstring": node.get("doc"),
            "entry_point": node.get("entry") or [],
            "explanation": node.get("explain"),
            "callers": self._neighbours(s.key, out=False)[:40],
            "callees": self._neighbours(s.key, out=True)[:40],
            "source": src["lines"],
            "in_explorer": bool(node),
        }

    def calls(self, target: str, direction: str = "out", depth: int = 2, include_guesses: bool = True) -> dict:
        if direction not in ("out", "in"):
            raise QueryError('direction must be "out" (what it calls) or "in" (what calls it)')
        s = self._resolve(target)
        symbols = self._c("symbols")
        edges, truncated = _calls.walk(self._c("g"), s.key, direction=direction,
                                       depth=max(1, min(depth, 6)), guesses=include_guesses)
        lines = _calls.edge_lines(edges, symbols, self._c("raw_lines"))
        return _calls.as_dict(symbols, s, edges, truncated, direction=direction, lines=lines)

    def impact(self, target: str, depth: int | None = None) -> dict:
        """Who is affected if ``target`` changes: callers up to ``depth`` hops over
        confident edges, the tests among them, and the shortest path from an entry point."""
        s = self._resolve(target)
        g = self._c("g")
        depth = max(1, min(depth or self.cfg.impact_depth, 8))
        seen, frontier, callers = {s.key}, [s.key], []
        for level in range(1, depth + 1):
            nxt = []
            for n in frontier:
                for m in sorted(g.predecessors(n)) if n in g else []:
                    if m in seen or g.edges[m, n].get("confidence") == AMBIGUOUS:
                        continue
                    seen.add(m)
                    nxt.append(m)
                    callers.append({"key": m, "where": self._where(m), "depth": level})
            frontier = nxt
        tests = [c for c in callers if _is_test_path(c["key"].split("::", 1)[0])]
        entry = None
        for e in self.data.get("entry_points", []):
            if e.get("node") is None:
                continue
            ek = self.data["nodes"][e["node"]]["key"]
            if ek in seen and ek != s.key:
                entry = f'{e["detail"] or e["kind"]} ({ek})'
                break
        return {"key": s.key, "where": s.where(), "depth": depth, "callers": callers,
                "tests": [t["key"] for t in tests], "reached_from_entry_point": entry,
                "note": "name-only (AMBIGUOUS) calls are left out; dynamic calls are invisible to the graph"}

    def read_source(self, path: str, start: int = 1, end: int | None = None) -> dict:
        path = path.strip().replace("\\", "/")
        if path not in self._c("paths"):
            raise QueryError(f'"{path}" is not an indexed file — use find_symbol or overview for paths')
        line_text = self._c("line_text")
        start = max(1, start)
        end = end or start + MAX_SOURCE_LINES - 1
        end = min(end, start + MAX_SOURCE_LINES - 1)
        lines = []
        for n in range(start, end + 1):
            t = line_text(path, n)
            if t is None:
                break
            lines.append(f"{n:>5}  {t}")
        more = line_text(path, end + 1) is not None
        return {"path": path, "start": start, "lines": "\n".join(lines), "more_below": more}

    def search_code(self, pattern: str, path_prefix: str = "", limit: int = 50) -> dict:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            raise QueryError(f"bad regex: {e}") from e
        line_text = self._c("line_text")
        hits = []
        limit = max(1, min(limit, MAX_SEARCH_HITS))
        for path in sorted(self._c("paths")):
            if path_prefix and not path.startswith(path_prefix):
                continue
            n = 1
            while (t := line_text(path, n)) is not None:
                if rx.search(t):
                    hits.append({"where": f"{path}:{n}", "text": t.strip()[:200]})
                    if len(hits) >= limit:
                        return {"hits": hits, "truncated": True}
                n += 1
        return {"hits": hits, "truncated": False}

    def get_authored(self, kind: str, id: str = "") -> dict:
        if kind not in (*SUBMIT_KINDS, "findings"):
            raise QueryError(f"kind must be one of {', '.join((*SUBMIT_KINDS, 'findings'))}")
        name = _findings.FINDINGS_FILE if kind == "findings" else _FILE_OF[kind]
        raw = self._read_raw(name)
        if id:
            if kind == "explanation":
                return {"item": (raw.get("symbols") or {}).get(id)}
            if kind == "scenario":
                return {"item": next((s for s in raw.get("scenarios") or [] if isinstance(s, dict) and s.get("id") == id), None)}
            if kind in ("library", "glossary"):
                return {"item": (raw.get("items") or {}).get(id)}
            if kind == "findings":
                return {"item": next((f for f in raw.get("items") or [] if isinstance(f, dict) and f.get("id") == id), None)}
        return {"file": f".codemap/{name}", "content": raw}

    def check(self) -> dict:
        conn = db.connect(self.cfg.db_path)
        try:
            issues = _validate.check_db(conn, self.cfg, self.data)
        finally:
            conn.close()
        return {**_validate.summarize(issues), "authored": _validate.authored_files(self.cfg)}

    # ------------------------------------------------------------------ open questions

    def _answered(self) -> set[str]:
        ids = set()
        for f in _findings.load(self.cfg):
            ids.add(f["id"].split("!", 1)[0])
        return ids

    def open_questions(self, kind: str = "", limit: int = 25) -> dict:
        """Ranked questions, most useful first. ``limit=0`` returns only counts."""
        if kind and kind not in QUESTION_KINDS:
            raise QueryError(f"kind must be one of {', '.join(QUESTION_KINDS)}")
        builders = {
            "ambiguous_call": self._q_ambiguous,
            "unmatched_request": self._q_requests,
            "orphan": self._q_orphans,
            "weak_placement": self._q_placement,
            "wrong_way": self._q_wrong_way,
            "hidden_dependency": self._q_hidden,
            "unnamed_flow": self._q_flows,
            "unexplained_symbol": self._q_unexplained,
            "unexplained_package": self._q_packages,
        }
        answered = self._answered()
        counts, out = {}, []
        for k in QUESTION_KINDS:
            if kind and k != kind:
                continue
            qs = [q for q in builders[k]() if q["id"] not in answered]
            counts[k] = len(qs)
            out.extend(qs)
        result = {"counts": counts}
        if limit:
            order = {k: i for i, k in enumerate(QUESTION_KINDS)}
            out.sort(key=lambda q: (-q["priority"], order[q["kind"]], q["id"]))
            result["questions"] = out[:limit]
            result["how_to_answer"] = (
                "Investigate with read_source / calls / search_code, then call answer_question(id, answer, "
                "evidence=[{file, line, note}]). If the code does not settle it, answer {\"unknown\": true} "
                "with a note. Never guess."
            )
        return result

    def _fan_in(self, key: str) -> int:
        return (self._c("node_by_key").get(key) or {}).get("fan_in", 0)

    def _is_entry(self, key: str) -> bool:
        return bool((self._c("node_by_key").get(key) or {}).get("entry"))

    def _q_ambiguous(self) -> list[dict]:
        g, symbols, raw_lines = self._c("g"), self._c("symbols"), self._c("raw_lines")
        groups: dict[tuple[str, str], list[str]] = {}
        for src, dst, ed in g.edges(data=True):
            if ed.get("confidence") == AMBIGUOUS and src in symbols and dst in symbols:
                groups.setdefault((src, symbols[dst].name), []).append(dst)
        out = []
        for (src, name), cands in groups.items():
            s = symbols[src]
            line = raw_lines.get((s.sid, name))
            test = _is_test_path(s.file)
            out.append({
                "id": f"call:{src}->{name}", "kind": "ambiguous_call",
                "priority": (1 if test else 3) + min(self._fan_in(src), 20) / 10 + (2 if self._is_entry(src) else 0),
                "question": f"`{s.qual}` calls `{name}(...)`, which matches {len(cands)} symbols by name. "
                            "Which one runs (if any)?",
                "caller": src, "call_site": f"{s.file}:{line}" if line else s.where(),
                "candidates": [{"key": c, "where": self._where(c)} for c in sorted(cands)],
                "answer_shape": {"calls": "<a candidate key>, a list of them when several really run "
                                          "(polymorphism), or null when none is called"},
            })
        return out

    def _q_requests(self) -> list[dict]:
        nodes = self.data["nodes"]
        out = []
        for r in self.data.get("routes", []):
            if r["handlers"] or not r["callers"] or r.get("review"):
                continue
            label = f'{r["method"]} {r["url"]}'
            out.append({
                "id": f'request:{r["method"]} {r["url"]}', "kind": "unmatched_request", "priority": 3,
                "question": f"The code sends `{label}`, but no route in this repo matched it. "
                            "Which handler serves it, or is it an outside service?",
                "callers": [{"key": nodes[c]["key"], "where": self._where(nodes[c]["key"])} for c in r["callers"][:8]],
                "answer_shape": {"handler": "<symbol key>", "or external": "<service name, e.g. Stripe API>"},
            })
        return out

    def _q_orphans(self) -> list[dict]:
        out = []
        for n in self.data["nodes"]:
            if (n["kind"] not in ("function", "method") or n["fan_in"] or n["fan_in_guess"] or n["entry"]
                    or n["name"].startswith("_") or _is_test_path(n["file"])):
                continue
            out.append({
                "id": f'orphan:{n["key"]}', "kind": "orphan", "priority": 1 + min(n["fan_out"], 10) / 10,
                "question": f"Nothing in the graph calls `{n['qual']}` and no entry point reaches it. "
                            "Is it called dynamically (registry, decorator, framework hook, another repo), or unused?",
                "symbol": n["key"], "where": f'{n["file"]}:{n["line"][0]}',
                "answer_shape": {"callers": ["<symbol keys that really call it>"],
                                 "or note": "how it is reached (framework hook, CLI, external) / that it is unused"},
            })
        return out

    def _q_placement(self) -> list[dict]:
        out = []
        for c in (self.data.get("architecture") or {}).get("components", []):
            if c["confidence"] != "weak" or c.get("authored"):
                continue
            out.append({
                "id": f'place:{c["path"]}', "kind": "weak_placement", "priority": 2 + min(c["loc"], 2000) / 1000,
                "question": f'The Architecture tab put `{c["path"]}` in the `{c["layer"]}` layer as a best guess. '
                            "Which layer is it really, and what does it do for the app?",
                "path": c["path"], "current_layer": c["layer"], "evidence_so_far": c.get("evidence", [])[:5],
                "answer_shape": {"layer": "|".join(_arch.LAYER_IDS), "title": "what it does for the app",
                                 "body": "one or two sentences"},
            })
        return out

    def _q_wrong_way(self) -> list[dict]:
        arch = self.data.get("architecture") or {}
        comps = {c["id"]: c for c in arch.get("components", [])}
        out = []
        for ln in arch.get("links", []):
            if ln["dir"] != "up":
                continue
            s, t = comps.get(ln["s"]), comps.get(ln["t"])
            if not s or not t:
                continue
            out.append({
                "id": f'wrongway:{s["path"]}->{t["path"]}', "kind": "wrong_way", "priority": 2,
                "question": f'`{s["path"]}` ({s["layer"]}) imports `{t["path"]}` ({t["layer"]}), against the '
                            "layer order. Is one of them in the wrong layer, or is this a real shortcut?",
                "from": s["path"], "to": t["path"], "imports": ln["n"],
                "answer_shape": {"move": {"path": "<one of the two paths>", "layer": "|".join(_arch.LAYER_IDS)},
                                 "or shortcut": "why the code really does this"},
            })
        return out

    def _q_hidden(self) -> list[dict]:
        keys: dict[str, list[str]] = {}
        for c in (self.data.get("facts") or {}).get("config", []):
            if c.get("key"):
                keys.setdefault(c["key"], []).append(f'{c["file"]}:{c["line"]}')
        line_text = self._c("line_text")
        for path in sorted(self._c("paths")):
            if not path.endswith(".py") or _is_test_path(path):
                continue
            n = 1
            while (t := line_text(path, n)) is not None:
                if "environ" in t or "getenv" in t:
                    for m in _PY_ENV.finditer(t):
                        keys.setdefault(m.group(1) or m.group(2), []).append(f"{path}:{n}")
                n += 1
        out = []
        for key, where in sorted(keys.items()):
            if not _HINT.search(key):
                continue
            out.append({
                "id": f"config:{key}", "kind": "hidden_dependency", "priority": 2,
                "question": f"The code reads the setting `{key}`. Does it point at a database, queue or outside "
                            "service the Architecture tab should show?",
                "read_at": where[:6],
                "answer_shape": {"store or service": {"id": "slug", "label": "name", "kind": "e.g. database|email|payments",
                                                      "via": ["<folder/file paths that use it>"]},
                                 "or none": "note why not"},
            })
        return out

    def _q_flows(self) -> list[dict]:
        covered = set()
        for sc in self._read_raw(_scenarios.SCENARIOS_FILE).get("scenarios") or []:
            if isinstance(sc, dict):
                covered.add(_s(sc.get("root")))
                for st in sc.get("steps") or []:
                    if isinstance(st, dict):
                        covered.add(_s(st.get("node")))
        out, seen = [], set()
        for e in self.data.get("entry_points", []):
            if e.get("node") is None:
                continue
            n = self.data["nodes"][e["node"]]
            if n["key"] in covered or n["key"] in seen or _is_test_path(n["file"]):
                continue
            seen.add(n["key"])
            out.append({
                "id": f'flow:{n["key"]}', "kind": "unnamed_flow", "priority": 2 + min(n["fan_out"], 20) / 10,
                "question": f'`{n["qual"]}` is an entry point ({e["kind"]}: {e["detail"]}) but no scenario '
                            "covers it. What workflow does it start?",
                "entry": n["key"], "where": f'{n["file"]}:{n["line"][0]}',
                "answer_shape": {"scenario": {"id": "slug", "title": "what the user/operator does",
                                              "root": n["key"], "group": "e.g. A request comes in",
                                              "summary": "one line", "trigger": {"surface": "api|ui|terminal|job",
                                                                                 "text": "..."}}},
            })
        return out

    def _q_unexplained(self) -> list[dict]:
        out = []
        for n in self.data["nodes"]:
            if n["explain"] or _is_test_path(n["file"]) or n["kind"] not in ("function", "method", "class"):
                continue
            score = (5 if n["entry"] else 0) + min(n["fan_in"], 15) / 3 + min(n["churn"], 10) / 5
            if score < 1:
                continue
            out.append({
                "id": f'explain:{n["key"]}', "kind": "unexplained_symbol", "priority": 1 + score / 5,
                "question": f'What does `{n["qual"]}` do, for an engineer new to this repo?',
                "symbol": n["key"], "where": f'{n["file"]}:{n["line"][0]}',
                "answer_shape": {"what": "one sentence: responsibility, inputs → outputs",
                                 "why": "optional: side effects, callers, gotchas"},
            })
        return out

    def _q_packages(self) -> list[dict]:
        have = set((self._read_raw(_libraries.LIBRARIES_FILE).get("items") or {}).keys())
        out = []
        for d in self.data.get("dependencies", []):
            if d["kind"] != "third_party" or d["name"] in have:
                continue
            out.append({
                "id": f'package:{d["name"]}', "kind": "unexplained_package", "priority": 1 + min(d["count"], 20) / 10,
                "question": f'What is `{d["name"]}` and what does this repo use it for?',
                "package": d["name"], "imported_by": d["importers"][:8],
                "answer_shape": {"general": "what the package is", "here": "what this repo uses it for",
                                 "see": ["<symbol keys that use it>"]},
            })
        return out

    # ------------------------------------------------------------------ writes

    def _read_raw(self, name: str) -> dict:
        try:
            data = json.loads((self.cfg.codemap_dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_raw(self, name: str, content: dict) -> None:
        self.cfg.codemap_dir.mkdir(parents=True, exist_ok=True)
        path = self.cfg.codemap_dir / name
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes((json.dumps(content, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        os.replace(tmp, path)

    def _validate_file(self, name: str, merged: dict) -> list[dict]:
        """New issues ``merged`` would introduce in ``name``, vs. the file now."""
        conn = db.connect(self.cfg.db_path)
        try:
            keys = _validate.graph_keys(conn, self.data)
        finally:
            conn.close()

        def run(override: dict | None) -> set[tuple]:
            issues = _validate.check(self.data, self.cfg, all_keys=keys, only={name},
                                     overrides={name: override} if override is not None else None)
            return {(i.severity, i.path, i.message) for i in issues}

        before, after = run(None), run(merged)
        return [{"severity": s, "path": p, "message": m} for s, p, m in sorted(after - before)]

    def _commit(self, name: str, merged: dict) -> dict:
        new = self._validate_file(name, merged)
        errors = [i for i in new if i["severity"] == _validate.ERROR]
        if errors:
            return {"ok": False, "rejected": errors,
                    "fix": "nothing was written; correct these and submit again"}
        self._write_raw(name, merged)
        return {"ok": True, "file": f".codemap/{name}", "warnings": new}

    def _add_findings(self, items: list[dict]) -> dict:
        raw = self._read_raw(_findings.FINDINGS_FILE) or {"version": 1, "items": []}
        current = [f for f in raw.get("items") or [] if isinstance(f, dict)]
        new_ids = {i["id"] for i in items}
        stamp = {"index_sha": self._c("sha"), "at": _today()}
        merged = {"version": 1, "items": [f for f in current if f.get("id") not in new_ids]
                  + [{**i, **stamp} for i in items]}
        return self._commit(_findings.FINDINGS_FILE, merged)

    def submit(self, kind: str, item: dict, evidence: list | None = None) -> dict:
        """Add or replace one authored entry. ``item`` shapes:

        - explanation: ``{key, what, why?, terms?}``
        - scenario: a full scenario object (``id``, ``title``, ``root`` and/or ``steps``…)
        - library: ``{name, general?, here?, see?}``
        - walkthrough: a partial walkthrough (``intro`` / ``categories`` / ``folders`` / ``glossary``)
        - architecture: a partial architecture.json (``summary``, ``layers``, ``components``,
          ``stores``, ``services``)
        - glossary: ``{term, definition, display?}``
        """
        if kind not in SUBMIT_KINDS:
            raise QueryError(f"kind must be one of {', '.join(SUBMIT_KINDS)}")
        if not isinstance(item, dict):
            raise QueryError("item must be an object")
        ev = self._check_evidence(evidence, required=False)
        name = _FILE_OF[kind]
        raw = self._read_raw(name)

        if kind == "explanation":
            key = _s(item.get("key"))
            if not key:
                raise QueryError("explanation needs `key` (a symbol key) and `what`")
            self._resolve(key)
            entry = {k: item[k] for k in ("what", "why", "terms") if item.get(k)}
            if ev:
                entry["evidence"] = [f'{e["file"]}:{e["line"]}' for e in ev]
            merged = {**raw, "symbols": {**(raw.get("symbols") or {}), key: entry}}
        elif kind == "scenario":
            sid = _s(item.get("id"))
            if not sid:
                raise QueryError("scenario needs an `id`")
            rest = [s for s in raw.get("scenarios") or [] if not (isinstance(s, dict) and s.get("id") == sid)]
            merged = {**raw, "scenarios": rest + [item]}
            warn = self._scenario_edge_warnings(item)
            res = self._commit(name, merged)
            if res["ok"] and warn:
                res["warnings"] = res["warnings"] + warn
            return res
        elif kind == "library":
            nm = _s(item.get("name"))
            if not nm:
                raise QueryError("library needs `name`")
            entry = {k: item[k] for k in ("general", "here", "see") if item.get(k)}
            merged = {**raw, "items": {**(raw.get("items") or {}), nm: entry}}
        elif kind == "glossary":
            term, definition = _s(item.get("term")), _s(item.get("definition"))
            if not term or not definition:
                raise QueryError("glossary needs `term` and `definition`")
            entry = {"definition": definition, **({"display": item["display"]} if _s(item.get("display")) else {})}
            merged = {**raw, "items": {**(raw.get("items") or {}), term: entry}}
        elif kind == "walkthrough":
            merged = dict(raw)
            if isinstance(item.get("intro"), dict):
                merged["intro"] = {**(raw.get("intro") or {}), **item["intro"]}
            if isinstance(item.get("categories"), list):
                def cid(c):
                    return _s(c.get("id")) or _walkthrough._slugify(_s(c.get("title")))
                new_ids = {cid(c) for c in item["categories"] if isinstance(c, dict)}
                merged["categories"] = [c for c in raw.get("categories") or []
                                        if isinstance(c, dict) and cid(c) not in new_ids] + item["categories"]
            for k in ("folders", "glossary"):
                if isinstance(item.get(k), dict):
                    merged[k] = {**(raw.get(k) or {}), **item[k]}
        else:  # architecture
            merged = dict(raw)
            if _s(item.get("summary")):
                merged["summary"] = item["summary"]
            for k in ("layers", "components"):
                if isinstance(item.get(k), dict):
                    cur = dict(raw.get(k) or {})
                    for path, entry in item[k].items():
                        cur[path] = {**(cur.get(path) or {}), **(entry if isinstance(entry, dict) else {})}
                    merged[k] = cur
            for k in ("stores", "services"):
                if isinstance(item.get(k), list):
                    new_ids = {_s(x.get("id")) for x in item[k] if isinstance(x, dict)}
                    merged[k] = [x for x in raw.get(k) or [] if not (isinstance(x, dict) and _s(x.get("id")) in new_ids)] \
                        + item[k]
        return self._commit(name, merged)

    def _scenario_edge_warnings(self, sc: dict) -> list[dict]:
        g = self._c("g")
        out = []
        for j, st in enumerate(sc.get("steps") or []):
            if not isinstance(st, dict):
                continue
            frm, node = _s(st.get("from")), _s(st.get("node"))
            if frm and node and frm in g and node in g and not g.has_edge(frm, node):
                out.append({"severity": "warning", "path": f"steps[{j}]",
                            "message": f"{frm} -> {node} is not a call edge in the graph; it shows as narration. "
                                       "If it is a real call, confirm it with answer_question first"})
        return out

    def answer_question(self, question_id: str, answer: dict, evidence: list | None = None, note: str = "") -> dict:
        """Settle one open question. See each question's ``answer_shape``.
        ``{"unknown": true}`` + a ``note`` records that the code doesn't settle it."""
        qid = _s(question_id)
        if ":" not in qid:
            raise QueryError("question_id must be an `id` from open_questions")
        prefix, rest = qid.split(":", 1)
        answer = answer if isinstance(answer, dict) else {}
        note = _s(note) or _s(answer.get("note"))

        if answer.get("unknown"):
            if not note:
                raise QueryError("an unknown answer needs a `note` saying what you checked")
            return self._add_findings([{"id": qid, "kind": "unknown", "note": note, "evidence":
                                        self._check_evidence(evidence, required=False)}])

        if prefix == "call":
            src, _, name = rest.rpartition("->")
            self._resolve(src)
            g = self._c("g")
            cands = sorted(d for d in (g.successors(src) if src in g else [])
                           if self._c("symbols")[d].name == name)
            if not cands:
                raise QueryError(f"no open ambiguous call {qid!r} — list open_questions again")
            ev = self._check_evidence(evidence, required=True)
            raw = answer.get("calls")
            # one key, several (polymorphism: each subclass's method can run), or none
            chosen = [] if raw is None else [raw] if isinstance(raw, str) else raw
            if not isinstance(chosen, list) or any(c not in cands for c in chosen):
                raise QueryError(f"`calls` must be one of {cands}, a list of them, or null")
            items = [{"id": f"{qid}!{c}", "kind": "call" if c in chosen else "not_call",
                      "from": src, "to": c, "note": note, "evidence": ev} for c in cands]
            return self._add_findings(items)

        if prefix == "request":
            method, _, url = rest.partition(" ")
            ev = self._check_evidence(evidence, required=True)
            handler, external = _s(answer.get("handler")), _s(answer.get("external"))
            if not handler and not external:
                raise QueryError("answer needs `handler` (a symbol key) or `external` (a service name)")
            if handler:
                handler = self._resolve(handler).key
            item = {"id": qid, "kind": "request", "method": method, "url": url, "note": note, "evidence": ev}
            if handler:
                item["handler"] = handler
            if external:
                item["external"] = external
            return self._add_findings([item])

        if prefix == "orphan":
            target = self._resolve(rest).key
            callers = answer.get("callers") or []
            if callers:
                ev = self._check_evidence(evidence, required=True)
                keys = [self._resolve(_s(c)).key for c in callers]
                return self._add_findings([{"id": qid if i == 0 else f"{qid}!{k}", "kind": "call", "from": k,
                                            "to": target, "note": note, "evidence": ev} for i, k in enumerate(keys)])
            if not note:
                raise QueryError("answer with `callers`, or give a `note` on how it is reached / that it is unused")
            return self._add_findings([{"id": qid, "kind": "note", "note": note,
                                        "evidence": self._check_evidence(evidence, required=False)}])

        if prefix == "place":
            layer = answer.get("layer")
            if layer not in _arch.LAYER_IDS:
                raise QueryError(f"`layer` must be one of {', '.join(_arch.LAYER_IDS)}")
            entry = {"layer": layer, **{k: answer[k] for k in ("title", "body", "tech") if answer.get(k)}}
            return self.submit("architecture", {"components": {rest: entry}}, evidence)

        if prefix == "wrongway":
            move = answer.get("move")
            if isinstance(move, dict):
                if move.get("layer") not in _arch.LAYER_IDS or not _s(move.get("path")):
                    raise QueryError("`move` needs `path` and a valid `layer`")
                res = self.submit("architecture", {"components": {move["path"]: {"layer": move["layer"]}}}, evidence)
            elif _s(answer.get("shortcut")) or note:
                res = {"ok": True}
            else:
                raise QueryError("answer with `move` or `shortcut`")
            if res.get("ok"):
                self._add_findings([{"id": qid, "kind": "note", "note": _s(answer.get("shortcut")) or note,
                                     "evidence": self._check_evidence(evidence, required=False)}])
            return res

        if prefix == "config":
            for k in ("store", "service"):
                if isinstance(answer.get(k), dict):
                    res = self.submit("architecture", {k + "s": [answer[k]]}, evidence)
                    if res.get("ok"):
                        self._add_findings([{"id": qid, "kind": "note", "note": note or f"{k}: {answer[k].get('label')}",
                                             "evidence": self._check_evidence(evidence, required=False)}])
                    return res
            if not note:
                raise QueryError("answer with `store` or `service`, or a `note` saying why it is neither")
            return self._add_findings([{"id": qid, "kind": "note", "note": note,
                                        "evidence": self._check_evidence(evidence, required=False)}])

        if prefix == "flow":
            sc = answer.get("scenario")
            if not isinstance(sc, dict):
                raise QueryError("answer needs `scenario` (see answer_shape)")
            return self.submit("scenario", {"root": rest, **sc}, evidence)

        if prefix == "explain":
            return self.submit("explanation", {"key": rest, **answer}, evidence)

        if prefix == "package":
            return self.submit("library", {"name": rest, **answer}, evidence)

        raise QueryError(f"unknown question id {qid!r}")

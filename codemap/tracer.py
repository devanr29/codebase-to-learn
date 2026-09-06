"""``codemap trace`` -- record a real run of the target codebase (Lane 3 of the
Simulate tab).

Lanes 1 (derived, client-side in ``explore.js``) and 2 (authored,
``.codemap/scenarios.json``) never execute the target code -- this is the one
that does. It runs the given command **in this same process** (a
``sys.setprofile`` call/return tracer only sees frames in the interpreter it's
installed in -- there is no cheap way to attach one to a spawned child), maps
every frame back to a symbol key using the existing index rather than
re-parsing source, and captures stdout/stderr with real timing so the
Simulate terminal stage can replay the actual output at the actual moments.

Nothing here is uploaded anywhere: the trace is a recording of a real run on
the author's own machine, written to ``.codemap/traces/<slug>.json`` (git-
ignored, same as the rest of ``.codemap/``), and only ever read back locally
by ``codemap/site/simulate.py`` when building ``explore.html``.

Python only. A future recorder for another language would write the same
step shape (see ``references/scenarios-schema.md``) to the same directory --
this format is deliberately language-neutral.
"""

from __future__ import annotations

import os
import re
import runpy
import sys
import time
from pathlib import Path

from . import progress as _progress
from .config import Config
from .db import connect, get_meta
from .indexer import WORKTREE_SHA

TRACES_DIR = "traces"
MAX_STEPS = 4000
_MAX_VALUE_LEN = 80
_SECRET_RE = re.compile(r"(secret|password|passwd|token|api[_-]?key|auth)", re.I)
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    s = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return s or "trace"


def _safe_repr(name: str, value: object) -> str:
    if _SECRET_RE.search(name or ""):
        return "<redacted>"
    try:
        r = repr(value)
    except Exception:
        return "<unrepr-able>"
    return r if len(r) <= _MAX_VALUE_LEN else r[:_MAX_VALUE_LEN] + "..."


class _SymbolIndex:
    """``(file, line) -> symbol key`` for the commit the current index was
    built at, resolved by narrowest enclosing ``[start_line, end_line]``."""

    def __init__(self, root: Path, rows) -> None:
        self._root = root
        self._by_file: dict[str, list[tuple[int, int, str]]] = {}
        for r in rows:
            self._by_file.setdefault(r["path"], []).append(
                (r["start_line"], r["end_line"], r["key"])
            )
        for lst in self._by_file.values():
            lst.sort()

    def resolve(self, filename: str, lineno: int) -> str | None:
        try:
            rel = str(Path(filename).resolve().relative_to(self._root)).replace("\\", "/")
        except (ValueError, OSError):
            return None
        best = None
        for start, end, key in self._by_file.get(rel, ()):
            span_end = end or start
            if start <= lineno <= span_end and (best is None or (span_end - start) < (best[1] - best[0])):
                best = (start, span_end, key)
        return best[2] if best else None


def _load_index(cfg: Config) -> _SymbolIndex:
    conn = connect(cfg.db_path)
    try:
        sha = get_meta(conn, "graph_head") or get_meta(conn, "last_indexed_commit") or WORKTREE_SHA
        rows = conn.execute(
            "SELECT f.path AS path, sv.start_line, sv.end_line, s.key "
            "FROM symbol_versions sv JOIN symbols s ON s.id = sv.symbol_id "
            "JOIN files f ON f.id = s.file_id WHERE sv.commit_sha = ?",
            (sha,),
        ).fetchall()
    finally:
        conn.close()
    return _SymbolIndex(cfg.root, rows)


class _CaptureStream:
    """Tee stdout/stderr to the real stream and to a timestamped line buffer,
    so recorded output can be replayed at the moments it actually printed.

    Defines only ``write``/``flush`` -- no ``isatty`` -- by design: any code
    that queries it (e.g. ``progress.choose_mode`` on a nested
    ``codemap trace -- -m codemap scan``) must degrade rather than crash, so
    ``isatty()`` is provided explicitly below and always answers False.
    """

    def __init__(self, stream, kind: str, sink: list[dict], t0: float, *, on_write=None) -> None:
        self._stream, self._kind, self._sink, self._t0 = stream, kind, sink, t0
        self._on_write = on_write
        self._buf = ""

    def write(self, s: str) -> int:
        if s and self._on_write is not None:
            self._on_write()
        self._stream.write(s)
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line:
                self._sink.append({"ts": time.perf_counter() - self._t0, "stream": self._kind, "text": line})
        return len(s)

    def flush(self) -> None:
        self._stream.flush()

    def isatty(self) -> bool:
        return False


def _run_target(argv: list[str]) -> None:
    """``argv`` is what you'd type after ``python`` -- ``-m mod ...`` or
    ``script.py ...`` -- with a leading literal ``python``/``python3``/``py``
    stripped if present, since we always run in-process."""
    argv = list(argv)
    if argv and argv[0] in ("python", "python3", "py"):
        argv = argv[1:]
    if not argv:
        raise ValueError("nothing to run")
    if argv[0] == "-m":
        if len(argv) < 2:
            raise ValueError("`-m` needs a module name")
        modname, rest = argv[1], argv[2:]
        sys.argv = [modname] + rest
        runpy.run_module(modname, run_name="__main__", alter_sys=True)
    else:
        script, rest = argv[0], argv[1:]
        sys.argv = [script] + rest
        runpy.run_path(script, run_name="__main__")


def record(
    cfg: Config,
    argv: list[str],
    *,
    name: str,
    values: bool = False,
    progress: _progress.Reporter | None = None,
) -> dict:
    """Run ``argv`` in-process under a call/return profiler, capturing every
    frame that maps to an indexed symbol plus real stdout/stderr timing.
    Returns the trace dict; the caller decides whether/where to write it."""
    if not argv:
        raise ValueError("nothing to run")
    progress = progress or _progress.NULL
    resolver = _load_index(cfg)

    events: list[dict] = []
    stdout_lines: list[dict] = []
    frame_stack: list[str | None] = []
    truncated = False
    t0 = time.perf_counter()

    def profiler(frame, event, _arg):
        nonlocal truncated
        if event == "call":
            key = resolver.resolve(frame.f_code.co_filename, frame.f_code.co_firstlineno)
            frame_stack.append(key)
            if key is None:
                return
            if len(events) >= MAX_STEPS:
                truncated = True
                return
            step = {
                "t": "call", "key": key,
                "line": frame.f_back.f_lineno if frame.f_back else None,
                "ts": time.perf_counter() - t0,
            }
            if values:
                code = frame.f_code
                names = [n for n in code.co_varnames[: code.co_argcount] if n not in ("self", "cls")]
                if names:
                    step["args"] = {n: _safe_repr(n, frame.f_locals.get(n)) for n in names}
            events.append(step)
        elif event == "return":
            key = frame_stack.pop() if frame_stack else None
            if key is not None and len(events) < MAX_STEPS:
                events.append({"t": "return", "key": key, "ts": time.perf_counter() - t0})

    with progress.phase("run", unit="steps") as phase:
        # the painter polls this once a frame instead of the profiler calling
        # advance() -- `profiler` above runs on every call/return in the
        # traced program and must stay pure dict/list work, not I/O
        phase.bind_counter(lambda: len(events))

        first_write = {"done": False}

        def on_target_write() -> None:
            # the target's own prints are about to hit the real terminal via
            # the tee below -- freeze this phase's line once, permanently, so
            # it never interleaves with a live \r-redrawn frame. One-shot: an
            # interlock on every write would tax the very thing being timed.
            if not first_write["done"]:
                first_write["done"] = True
                phase.suspend_animation()

        old_out, old_err = sys.stdout, sys.stderr
        old_argv = sys.argv
        sys.stdout = _CaptureStream(old_out, "stdout", stdout_lines, t0, on_write=on_target_write)
        sys.stderr = _CaptureStream(old_err, "stderr", stdout_lines, t0, on_write=on_target_write)
        crashed = None
        # a traced target that is itself `codemap` (the documented
        # `codemap trace -- -m codemap explore`) must not build a second
        # reporter bound to the capture-stream tee -- force it silent.
        old_env = os.environ.get("CODEMAP_NO_PROGRESS")
        os.environ["CODEMAP_NO_PROGRESS"] = "1"
        sys.setprofile(profiler)
        try:
            _run_target(argv)
        except SystemExit:
            pass
        except BaseException as e:  # a crashing target still leaves a useful partial trace
            crashed = f"{type(e).__name__}: {e}"
        finally:
            sys.setprofile(None)
            sys.stdout, sys.stderr = old_out, old_err
            sys.argv = old_argv
            if old_env is None:
                os.environ.pop("CODEMAP_NO_PROGRESS", None)
            else:
                os.environ["CODEMAP_NO_PROGRESS"] = old_env

        if truncated:
            phase.set_summary(f"{len(events)} steps recorded, truncated at {MAX_STEPS}")

    steps = _merge(events, stdout_lines)
    trace = {
        "id": _slugify(name),
        "title": name,
        "trigger": {"surface": "terminal", "text": name},
        "source": "recorded",
        "steps": steps[:MAX_STEPS],
    }
    if truncated or len(steps) > MAX_STEPS:
        trace["truncated"] = True
    if crashed:
        trace["crashed"] = crashed
    return trace


def _merge(events: list[dict], stdout_lines: list[dict]) -> list[dict]:
    """Interleave call/return frames with emitted output lines by timestamp,
    anchoring each emit to whatever frame was on top of the stack when it
    printed -- the shape ``simulate.py`` / ``scenarios.py`` both produce."""
    tagged = [dict(e, _kind=e["t"]) for e in events]
    for ln in stdout_lines:
        tagged.append({"_kind": "emit", "ts": ln["ts"], "stream": ln["stream"], "text": ln["text"]})
    tagged.sort(key=lambda e: e["ts"])

    steps: list[dict] = []
    stack: list[str] = []
    first_key = next((e["key"] for e in events if e["t"] == "call"), None)
    for e in tagged:
        if e["_kind"] == "call":
            step = {"node": e["key"], "t": "call", "line": e.get("line"), "ts": round(e["ts"] * 1000)}
            if "args" in e:
                step["args"] = e["args"]
            steps.append(step)
            stack.append(e["key"])
        elif e["_kind"] == "return":
            steps.append({"node": e["key"], "t": "return", "ts": round(e["ts"] * 1000)})
            if stack and stack[-1] == e["key"]:
                stack.pop()
        else:  # emit
            anchor = stack[-1] if stack else first_key
            if anchor is None:
                continue
            steps.append({
                "node": anchor, "t": "emit", "ts": round(e["ts"] * 1000),
                "emit": {"surface": "terminal", "stream": e["stream"], "text": e["text"]},
            })
    return steps


def write(cfg: Config, trace: dict) -> Path:
    out_dir = cfg.codemap_dir / TRACES_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{trace['id']}.json"
    import json

    out_path.write_text(json.dumps(trace, indent=2), encoding="utf-8")
    return out_path


def load_all(cfg: Config) -> list[dict]:
    """Every recorded trace under ``.codemap/traces/`` (Lane 3), or ``[]`` if
    none exist or the directory is missing/unreadable. Never raises."""
    out_dir = cfg.codemap_dir / TRACES_DIR
    if not out_dir.is_dir():
        return []
    import json

    out = []
    for p in sorted(out_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict) and isinstance(data.get("steps"), list) and data.get("id"):
            out.append(data)
    return out

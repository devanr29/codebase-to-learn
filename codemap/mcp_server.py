"""``codemap mcp`` — the MCP server over stdio, with no SDK.

A thin shell: every tool is a method of :class:`codemap.query.Session`, which
holds the logic and the tests. This module speaks the protocol itself --
JSON-RPC 2.0, one message per line on stdin/stdout (the MCP stdio transport) --
the way codebase-memory-mcp's ``src/mcp/mcp.c`` does, so ``codemap mcp`` needs
nothing beyond codemap's own install. It answers ``initialize``, ``ping``,
``tools/list`` and ``tools/call``, lists no resources or prompts, and turns a
:class:`~codemap.query.QueryError` into a readable tool error (``isError``)
rather than a protocol failure.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import sys
import types
import typing
from pathlib import Path
from typing import Any, BinaryIO, Callable

from . import __version__
from .query import QueryError, Session

INSTRUCTIONS = """\
codemap indexes this repository into a symbol-level call graph and renders it as
explore.html, a page that helps engineers new to the codebase understand it.
Static analysis leaves gaps; your job is to close them with evidence.

Workflow:
1. overview — what the repo is, its layers, entry points and how many open questions remain.
2. open_questions — ranked things the graph cannot prove (ambiguous calls, unmatched
   requests, misplaced architecture parts, unnamed workflows, unexplained symbols/packages).
3. For each: investigate with read_source, calls, impact, search_code, explain_symbol.
4. answer_question with evidence [{file, line, note}] pointing at the code that proves it.
   If the code does not settle it, answer {"unknown": true} with a note. Never guess.
5. submit for prose that isn't tied to one question (walkthrough pages, glossary terms,
   architecture summary). Write for an engineer new to the repo: responsibilities,
   inputs and outputs, side effects, callers, gotchas.
6. check, then tell the user to run `codemap explore --open`.

Every write is verified against the index; a rejected write says why and writes nothing.
If a response says the index is stale, ask the user to run `codemap scan` first."""

# newest first; a client asking for one of these gets it back, any other gets the newest
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = (
    -32700, -32600, -32601, -32602, -32603)


# --------------------------------------------------------------------------- tools


class Tool:
    def __init__(self, fn: Callable) -> None:
        self.fn = fn
        self.name = fn.__name__
        self.description = inspect.cleandoc(fn.__doc__ or "")
        self.schema = _input_schema(fn)

    def describe(self) -> dict:
        return {"name": self.name, "description": self.description, "inputSchema": self.schema}


def _json_type(tp: Any) -> dict:
    """A Python annotation as a JSON Schema fragment."""
    origin = typing.get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        return _json_type(args[0]) if len(args) == 1 else {}
    if origin is list or tp is list:
        args = typing.get_args(tp)
        return {"type": "array", "items": _json_type(args[0])} if args else {"type": "array"}
    if origin is dict or tp is dict:
        return {"type": "object"}
    return {str: {"type": "string"}, int: {"type": "integer"}, bool: {"type": "boolean"},
            float: {"type": "number"}}.get(tp, {})


def _input_schema(fn: Callable) -> dict:
    hints = typing.get_type_hints(fn)
    props: dict[str, dict] = {}
    required: list[str] = []
    for name, p in inspect.signature(fn).parameters.items():
        prop = dict(_json_type(hints.get(name, str)))
        if p.default is inspect.Parameter.empty:
            required.append(name)
        elif p.default is not None:
            prop["default"] = p.default
        props[name] = prop
    schema: dict = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


def build_tools(session: Session) -> dict[str, Tool]:
    """Every tool, described for the model by its docstring and signature."""

    def overview() -> dict:
        """The repo at a glance: summary, stack, architecture layers and their parts, entry
        points, folders, which authored files exist, open-question counts, index freshness."""
        return session.overview()

    def find_symbol(query: str, limit: int = 20) -> dict:
        """Find symbols by key (path::Name), qualified name (Class.method), path suffix,
        bare name, or substring. Returns keys to pass to the other tools."""
        return session.find_symbol(query, limit)

    def explain_symbol(key: str) -> dict:
        """One symbol: signature, docstring, entry-point role, current explanation, direct
        callers and callees (with confidence; reviewed edges carry their evidence), source."""
        return session.explain_symbol(key)

    def calls(key: str, direction: str = "out", depth: int = 2, include_guesses: bool = True) -> dict:
        """The call tree from the resolved graph. direction "out" = what it calls, "in" = what
        calls it. Confidence: EXTRACTED (same file / self), INFERRED (import or type evidence),
        AMBIGUOUS (name-only guess)."""
        return session.calls(key, direction, depth, include_guesses)

    def impact(key: str, depth: int = 0) -> dict:
        """What is affected if this symbol changes: callers over confident edges, the tests
        among them, and whether an entry point reaches it."""
        return session.impact(key, depth or None)

    def read_source(path: str, start: int = 1, end: int = 0) -> dict:
        """Numbered lines of an indexed file as codemap indexed it (max 400 per call). Cite
        these line numbers as evidence."""
        return session.read_source(path, start, end or None)

    def search_code(pattern: str, path_prefix: str = "", limit: int = 50) -> dict:
        """Regex search over the indexed files; returns file:line hits."""
        return session.search_code(pattern, path_prefix, limit)

    def get_authored(kind: str, id: str = "") -> dict:
        """Current authored content. kind: explanation, scenario, library, walkthrough,
        architecture, glossary or findings; id narrows to one entry (symbol key, scenario id,
        package name, glossary term, finding id)."""
        return session.get_authored(kind, id)

    def open_questions(kind: str = "", limit: int = 25) -> dict:
        """Ranked questions static analysis could not settle, each with context and the
        answer shape it expects. kind filters to one of: ambiguous_call, unmatched_request,
        orphan, weak_placement, wrong_way, hidden_dependency, unnamed_flow,
        unexplained_symbol, unexplained_package. limit=0 returns counts only."""
        return session.open_questions(kind, limit)

    def answer_question(question_id: str, answer: dict, evidence: list[dict] | None = None,
                        note: str = "") -> dict:
        """Settle one open question with the answer shape it asked for, plus evidence
        [{file, line, note}] from read_source. {"unknown": true} + note when the code doesn't
        settle it. Verified before anything is written; a rejection explains why."""
        return session.answer_question(question_id, answer, evidence, note)

    def submit(kind: str, item: dict, evidence: list[dict] | None = None) -> dict:
        """Add or replace one authored entry, verified like `codemap check` before writing.
        kind / item: explanation {key, what, why?}; scenario {id, title, root, group?, order?,
        summary?, trigger?, steps?}; library {name, general?, here?, see?}; walkthrough
        {intro?, categories?, folders?, glossary?}; architecture {summary?, layers?, components?,
        stores?, services?}; glossary {term, definition, display?}."""
        return session.submit(kind, item, evidence)

    def check() -> dict:
        """Re-check every authored file and finding against the code; lists what would be
        dropped or is stale."""
        return session.check()

    fns = (overview, find_symbol, explain_symbol, calls, impact, read_source, search_code,
           get_authored, open_questions, answer_question, submit, check)
    return {fn.__name__: Tool(fn) for fn in fns}


# --------------------------------------------------------------------------- protocol


class Server:
    """One MCP session over a pair of byte streams."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.session = Session(root)
        self.tools = build_tools(self.session)

    # -- one message -> one reply (or None for a notification) ----------------

    def handle(self, msg: Any) -> dict | None:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            return _error(msg.get("id") if isinstance(msg, dict) else None, INVALID_REQUEST, "Invalid Request")
        method, params = msg["method"], msg.get("params") or {}
        if "id" not in msg:          # a notification: initialized, cancelled, ... -- no reply
            return None
        rid = msg["id"]
        if not isinstance(params, dict):
            return _error(rid, INVALID_PARAMS, "params must be an object")
        if method == "initialize":
            asked = params.get("protocolVersion")
            return _result(rid, {
                "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "codemap", "version": __version__},
                "instructions": INSTRUCTIONS,
            })
        if method == "ping":
            return _result(rid, {})
        if method == "tools/list":
            return _result(rid, {"tools": [t.describe() for t in self.tools.values()]})
        if method == "tools/call":
            return self._call(rid, params)
        # clients probe these whatever the declared capabilities; empty is interoperable
        if method == "resources/list":
            return _result(rid, {"resources": []})
        if method == "resources/templates/list":
            return _result(rid, {"resourceTemplates": []})
        if method == "prompts/list":
            return _result(rid, {"prompts": []})
        return _error(rid, METHOD_NOT_FOUND, f"Method not found: {method}")

    def _call(self, rid: Any, params: dict) -> dict:
        tool = self.tools.get(params.get("name"))
        if tool is None:
            return _error(rid, INVALID_PARAMS, f"Unknown tool: {params.get('name')!r}")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _error(rid, INVALID_PARAMS, "arguments must be an object")
        try:
            bound = inspect.signature(tool.fn).bind(**args)
        except TypeError as e:
            return _result(rid, _tool_error(f"bad arguments for {tool.name}: {e}"))
        try:
            # stdout carries protocol messages only: anything a tool prints goes to stderr
            with contextlib.redirect_stdout(sys.stderr):
                out = tool.fn(*bound.args, **bound.kwargs)
        except QueryError as e:  # the model's mistake: say so as a tool error, not a crash
            return _result(rid, _tool_error(str(e)))
        except Exception as e:  # noqa: BLE001 -- one bad call must not end the session
            return _result(rid, _tool_error(f"internal error in {tool.name}: {type(e).__name__}: {e}"))
        return _result(rid, {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, indent=1)}],
                             "isError": False})

    # -- the stdio loop ---------------------------------------------------------

    def serve(self, inp: BinaryIO, out: BinaryIO) -> None:
        for raw in iter(inp.readline, b""):
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                _send(out, _error(None, PARSE_ERROR, "Parse error"))
                continue
            if isinstance(msg, list):     # a JSON-RPC batch (older protocol revisions)
                replies = [r for r in (self.handle(m) for m in msg) if r is not None]
                if replies:
                    _send(out, replies)
                continue
            reply = self.handle(msg)
            if reply is not None:
                _send(out, reply)


def _result(rid: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _tool_error(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _send(out: BinaryIO, msg: Any) -> None:
    out.write(json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
    out.flush()


def build_server(root: Path | str | None = None) -> Server:
    return Server(root)


def main(root: Path | str | None = None) -> int:
    try:
        server = build_server(root)
    except Exception as e:  # noqa: BLE001 -- e.g. not inside a git repo
        print(f"codemap {__version__}: {e}", file=sys.stderr)
        return 1
    real_out = sys.stdout.buffer
    with contextlib.redirect_stdout(sys.stderr):
        server.serve(sys.stdin.buffer, real_out)
    return 0

"""``codemap mcp`` — the MCP server over stdio.

A thin shell: every tool is a method of :class:`codemap.query.Session`, which
holds the logic and the tests. This module only describes the tools for the
model and turns a :class:`~codemap.query.QueryError` into a readable tool
error. The SDK is the optional ``mcp`` extra (``pip install "codemap[mcp]"``).
"""

from __future__ import annotations

from pathlib import Path

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


def _sdk():
    """``(server class, ToolError)`` from whichever SDK generation is installed."""
    try:  # mcp >= 2
        from mcp.server.mcpserver import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError

        return MCPServer, ToolError
    except ImportError:
        pass
    try:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP
        from mcp.server.fastmcp.exceptions import ToolError

        return FastMCP, ToolError
    except ImportError:
        return None, None


def build_server(root: Path | str | None = None):
    Server, ToolError = _sdk()
    if Server is None:
        raise ImportError('the MCP SDK is not installed — pip install "codemap[mcp]"')
    session = Session(root)
    server = Server("codemap", instructions=INSTRUCTIONS)

    def run(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except QueryError as e:  # the model's mistake: say so as a tool error, not a crash
            raise ToolError(str(e)) from None

    @server.tool()
    def overview() -> dict:
        """The repo at a glance: summary, stack, architecture layers and their parts, entry
        points, folders, which authored files exist, open-question counts, index freshness."""
        return run(session.overview)

    @server.tool()
    def find_symbol(query: str, limit: int = 20) -> dict:
        """Find symbols by key (path::Name), qualified name (Class.method), path suffix,
        bare name, or substring. Returns keys to pass to the other tools."""
        return run(session.find_symbol, query, limit)

    @server.tool()
    def explain_symbol(key: str) -> dict:
        """One symbol: signature, docstring, entry-point role, current explanation, direct
        callers and callees (with confidence; reviewed edges carry their evidence), source."""
        return run(session.explain_symbol, key)

    @server.tool()
    def calls(key: str, direction: str = "out", depth: int = 2, include_guesses: bool = True) -> dict:
        """The call tree from the resolved graph. direction "out" = what it calls, "in" = what
        calls it. Confidence: EXTRACTED (same file / self), INFERRED (import evidence),
        AMBIGUOUS (name-only guess)."""
        return run(session.calls, key, direction, depth, include_guesses)

    @server.tool()
    def impact(key: str, depth: int = 0) -> dict:
        """What is affected if this symbol changes: callers over confident edges, the tests
        among them, and whether an entry point reaches it."""
        return run(session.impact, key, depth or None)

    @server.tool()
    def read_source(path: str, start: int = 1, end: int = 0) -> dict:
        """Numbered lines of an indexed file as codemap indexed it (max 400 per call). Cite
        these line numbers as evidence."""
        return run(session.read_source, path, start, end or None)

    @server.tool()
    def search_code(pattern: str, path_prefix: str = "", limit: int = 50) -> dict:
        """Regex search over the indexed files; returns file:line hits."""
        return run(session.search_code, pattern, path_prefix, limit)

    @server.tool()
    def get_authored(kind: str, id: str = "") -> dict:
        """Current authored content. kind: explanation, scenario, library, walkthrough,
        architecture, glossary or findings; id narrows to one entry (symbol key, scenario id, package name, glossary term, finding id)."""
        return run(session.get_authored, kind, id)

    @server.tool()
    def open_questions(kind: str = "", limit: int = 25) -> dict:
        """Ranked questions static analysis could not settle, each with context and the
        answer shape it expects. kind filters to one of: ambiguous_call, unmatched_request,
        orphan, weak_placement, wrong_way, hidden_dependency, unnamed_flow,
        unexplained_symbol, unexplained_package. limit=0 returns counts only."""
        return run(session.open_questions, kind, limit)

    @server.tool()
    def answer_question(question_id: str, answer: dict, evidence: list[dict] | None = None,
                        note: str = "") -> dict:
        """Settle one open question with the answer shape it asked for, plus evidence
        [{file, line, note}] from read_source. {"unknown": true} + note when the code doesn't
        settle it. Verified before anything is written; a rejection explains why."""
        return run(session.answer_question, question_id, answer, evidence, note)

    @server.tool()
    def submit(kind: str, item: dict, evidence: list[dict] | None = None) -> dict:
        """Add or replace one authored entry, verified like `codemap check` before writing.
        kind / item: explanation {key, what, why?}; scenario {id, title, root, group?, order?,
        summary?, trigger?, steps?}; library {name, general?, here?, see?}; walkthrough
        {intro?, categories?, folders?, glossary?}; architecture {summary?, layers?, components?,
        stores?, services?}; glossary {term, definition, display?}."""
        return run(session.submit, kind, item, evidence)

    @server.tool()
    def check() -> dict:
        """Re-check every authored file and finding against the code; lists what would be
        dropped or is stale."""
        return run(session.check)

    return server


def main(root: Path | str | None = None) -> int:
    try:
        server = build_server(root)
    except ImportError as e:
        import sys

        print(f"codemap {__version__}: {e}", file=sys.stderr)
        return 1
    server.run()
    return 0

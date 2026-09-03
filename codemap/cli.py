"""``codemap`` command-line entry point.

Subcommands: scan, explain, catchup, reviewed, note, install-hook, status.
Only a subset is wired up per milestone; unimplemented ones say so and exit 0
so a post-commit hook can never turn a stubbed command into a failed commit.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, config, db

_COUNT_QUERIES = {
    "commits": "SELECT COUNT(*) AS n FROM commits",
    "files": "SELECT COUNT(*) AS n FROM files",
    "symbols": "SELECT COUNT(*) AS n FROM symbols",
    "refs": "SELECT COUNT(*) AS n FROM refs",
    "changes": "SELECT COUNT(*) AS n FROM changes",
}


def _find_root(start: Path | None = None) -> Path:
    """Walk up from ``start`` to the nearest dir containing ``.codemap`` or ``.git``."""
    cur = (start or Path.cwd()).resolve()
    for candidate in (cur, *cur.parents):
        if (candidate / config.CODEMAP_DIR).is_dir() or (candidate / ".git").is_dir():
            return candidate
    return cur


# --------------------------------------------------------------------------- status


def cmd_status(args: argparse.Namespace) -> int:
    root = _find_root(Path(args.path) if args.path else None)
    fresh = not (root / config.CODEMAP_DIR).exists()
    cfg = config.init(root)  # M0: status bootstraps .codemap/, config.toml, db
    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    try:
        print(f"codemap {__version__}")
        print(f"root:        {root}")
        if fresh:
            print(f"initialized: {cfg.codemap_dir}")
        print(f"config:      {cfg.config_path}")
        schema_version = db.get_meta(conn, "schema_version", "?")
        last_indexed = db.get_meta(conn, "last_indexed_commit")
        last_reviewed = db.get_meta(conn, "last_reviewed_commit")
        graph_head = db.get_meta(conn, "graph_head")

        counts = {
            name: conn.execute(sql).fetchone()["n"]
            for name, sql in _COUNT_QUERIES.items()
        }
        by_tier = conn.execute(
            "SELECT tier, COUNT(*) AS n FROM files GROUP BY tier ORDER BY tier"
        ).fetchall()

        print(f"schema:      v{schema_version}")
        print(f"db:          {cfg.db_path}")
        print(f"last indexed:  {last_indexed or '(never)'}")
        print(f"last reviewed: {last_reviewed or '(never)'}")
        graph_desc = "worktree (uncommitted changes included)" if graph_head == "worktree" else (graph_head or "(none)")
        print(f"graph:         {graph_desc}")
        if sum(counts.values()) == 0:
            print("index:       empty")
        else:
            print("index:       " + ", ".join(f"{n} {name}" for name, n in counts.items()))
            if by_tier:
                tiers = ", ".join(f"T{row['tier']}: {row['n']}" for row in by_tier)
                print(f"files by tier: {tiers}")
        return 0
    finally:
        conn.close()


# ------------------------------------------------------------------- scan / explain


def cmd_scan(args: argparse.Namespace) -> int:
    from . import indexer

    root = _find_root(Path(args.path) if args.path else None)
    cfg = config.init(root)
    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    try:
        stats = indexer.scan(conn, cfg, since=args.since)
        print(
            f"indexed {stats.commits_indexed} commit(s), "
            f"{stats.files_parsed} file(s) parsed, "
            f"{stats.files_skipped} unchanged"
        )
        if stats.errors:
            print(f"{len(stats.errors)} file(s) had parse errors (see below):")
            for path, msg in stats.errors[:20]:
                print(f"  {path}: {msg}")
        return 0
    finally:
        conn.close()


def cmd_explain(args: argparse.Namespace) -> int:
    from . import impact, indexer, report, semdiff

    root = _find_root(Path(args.path) if args.path else None)
    cfg = config.load(root)
    if not cfg.db_path.exists():
        print("no index yet — run `codemap scan` first")
        return 0
    conn = db.connect(cfg.db_path)
    try:
        # "worktree" is the live pseudo-commit (spec M15) — uncommitted changes
        # included. It isn't a git ref, so it bypasses resolve_sha entirely.
        sha = indexer.WORKTREE_SHA if args.rev == "worktree" else indexer.resolve_sha(root, args.rev)
        parent = indexer.parent_sha(conn, sha)
        changes = semdiff.load_changes(conn, sha)
        if not changes and parent is not None:
            # recompute without persisting — explain never writes to the index;
            # analyze() below folds the impact summary in memory, so annotate()'s
            # DB UPDATE (which would match nothing here) is not needed
            changes = semdiff.diff_commits(conn, cfg, parent, sha, persist=False)
        impacts = impact.analyze(conn, cfg, parent, sha, changes) if changes else {}
        print(report.render_commit(conn, cfg, sha, changes=changes, impacts=impacts))
        if args.breakdown:
            print()
            semdiff.print_breakdown(sha, changes, impacts)
        return 0
    finally:
        conn.close()


# ------------------------------------------------------ catchup / reviewed / note


def cmd_catchup(args: argparse.Namespace) -> int:
    from . import digest

    root = _find_root(Path(args.path) if args.path else None)
    cfg = config.load(root)
    if not cfg.db_path.exists():
        print("no index yet — run `codemap scan` first")
        return 0
    conn = db.connect(cfg.db_path)
    try:
        print(digest.catchup(conn, cfg))
        return 0
    finally:
        conn.close()


def cmd_snapshot(args: argparse.Namespace) -> int:
    from . import digest

    root = _find_root(Path(args.path) if args.path else None)
    cfg = config.load(root)
    if not cfg.db_path.exists():
        print("no index yet — run `codemap scan` first")
        return 0
    conn = db.connect(cfg.db_path)
    try:
        body = digest.snapshot(conn, cfg)
        cfg.codemap_dir.mkdir(parents=True, exist_ok=True)
        out = cfg.codemap_dir / "snapshot.md"
        out.write_text(body + "\n", encoding="utf-8")
        print(body)
        print(f"\n(written to {out})")
        return 0
    finally:
        conn.close()


def cmd_explore(args: argparse.Namespace) -> int:
    import json as _json

    from . import db as _db
    from .site import model as _model

    root = _find_root(Path(args.path) if args.path else None)
    cfg = config.load(root)
    if not cfg.db_path.exists():
        print("no index yet — run `codemap scan` first")
        return 0
    if getattr(args, "if_enabled", False) and not cfg.explore.rebuild_on_commit:
        return 0
    conn = _db.connect(cfg.db_path)
    try:
        data = _model.build(
            conn,
            cfg,
            max_symbols=args.max_symbols or cfg.explore.max_symbols,
            max_snippet_lines=cfg.explore.max_snippet_lines,
        )
        if data.get("empty"):
            print("index is empty — run `codemap scan` first")
            return 0
        if getattr(args, "emit_brief", False):
            from .site import brief as _brief

            paths = _brief.emit(conn, cfg, data)
            print(f"wrote {len(paths)} brief(s) under {cfg.codemap_dir / 'briefs'}")
            return 0
        if args.json:
            print(_json.dumps(data, indent=2, sort_keys=True))
            return 0

        from .site import render as _render

        out = Path(args.out) if args.out else (cfg.codemap_dir / "explore.html")
        html = _render.render(data)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        size_mb = len(html.encode("utf-8")) / 1_048_576
        if size_mb > 8:
            print(f"warning: {out.name} is {size_mb:.1f} MB", file=sys.stderr)
        if not args.quiet:
            s = data["stats"]
            print(
                f"wrote {out}  ({s['files']} files, {s['symbols']} symbols, "
                f"{s['edges']} edges)"
            )
        if args.open:
            import webbrowser

            webbrowser.open(out.resolve().as_uri())
        return 0
    finally:
        conn.close()



# --------------------------------------------------------------------------- trace


def cmd_trace(args: argparse.Namespace) -> int:
    """Record a real run of the target codebase for the Simulate tab's Lane 3
    (see ``codemap/tracer.py``). Runs *in this process* -- pass what you'd
    type after ``python``, e.g. ``codemap trace -- -m codemap explore``."""
    from . import tracer as _tracer

    root = _find_root(Path(args.path) if args.path else None)
    cfg = config.load(root)
    if not cfg.db_path.exists():
        print("no index yet -- run `codemap scan` first")
        return 0
    argv = list(args.cmd or [])
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        print("codemap trace: nothing to run -- pass a command after `--`", file=sys.stderr)
        return 2

    name = args.name or " ".join(argv)
    trace = _tracer.record(cfg, argv, name=name, values=args.values)
    out_path = _tracer.write(cfg, trace)
    n_steps = len(trace["steps"])
    msg = f"wrote {out_path}  ({n_steps} step{'' if n_steps == 1 else 's'})"
    if trace.get("truncated"):
        msg += f" -- truncated at {_tracer.MAX_STEPS}"
    if trace.get("crashed"):
        msg += "\n  target raised: " + trace["crashed"]
    print(msg)
    return 0


def cmd_reviewed(args: argparse.Namespace) -> int:
    root = _find_root(Path(args.path) if args.path else None)
    cfg = config.load(root)
    if not cfg.db_path.exists():
        print("no index yet — run `codemap scan` first")
        return 0
    conn = db.connect(cfg.db_path)
    try:
        from . import indexer

        sha = indexer.resolve_sha(root, args.rev or "HEAD")
        db.set_meta(conn, "last_reviewed_commit", sha)
        print(f"marked {sha[:7]} as reviewed")
        return 0
    finally:
        conn.close()


def cmd_note(args: argparse.Namespace) -> int:
    from . import intent

    root = _find_root(Path(args.path) if args.path else None)
    cfg = config.init(root)
    note_path = intent.write_note(cfg, args.text)
    print(f"intent recorded at {note_path} (consumed on the next commit's scan)")
    return 0


def cmd_install_hook(args: argparse.Namespace) -> int:
    from . import intent

    root = _find_root(Path(args.path) if args.path else None)
    return intent.install_hook(root)


# --------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="codemap", description=__doc__.splitlines()[0])
    p.add_argument("--version", action="version", version=f"codemap {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name: str, fn, help_: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--path", help="repo path (default: search up from cwd)")
        sp.set_defaults(func=fn)
        return sp

    add("status", cmd_status, "show index state")

    sp = add("scan", cmd_scan, "index commits into the graph")
    sp.add_argument("--since", help="index from this commit forward (default: last indexed)")

    sp = add("explain", cmd_explain, "print the markdown change entry for a commit")
    sp.add_argument(
        "rev", nargs="?", default="HEAD",
        help="commit, or 'worktree' for uncommitted changes (default: HEAD)",
    )
    sp.add_argument("--breakdown", action="store_true", help="also print the raw change list")

    add("catchup", cmd_catchup, "digest every change since the last-reviewed marker")
    add("snapshot", cmd_snapshot, "render the current architecture snapshot")

    sp = add("explore", cmd_explore, "render the self-contained explore.html surface")
    sp.add_argument("--out", help="output path (default: .codemap/explore.html)")
    sp.add_argument("--json", action="store_true", help="print the graph model as JSON, write nothing")
    sp.add_argument("--emit-brief", action="store_true", help="write module briefs for the course-authoring skill")
    sp.add_argument("--max-symbols", type=int, default=0, help="cap graph nodes (default: config)")
    sp.add_argument("--open", action="store_true", help="open the result in a browser")
    sp.add_argument("--quiet", action="store_true", help="suppress the summary line")
    sp.add_argument("--if-enabled", action="store_true",
                    help="no-op unless [explore] rebuild_on_commit is true (used by the hook)")

    sp = add("trace", cmd_trace, "record a real run for the Simulate tab (Lane 3)")
    sp.add_argument("--name", help="scenario title (default: the command itself)")
    sp.add_argument("--values", action="store_true", help="capture call-argument reprs (truncated; secret-named args redacted)")
    sp.add_argument("cmd", nargs=argparse.REMAINDER, help="what to run, e.g. `-- -m codemap explore`")

    sp = add("reviewed", cmd_reviewed, "advance the last-reviewed marker")
    sp.add_argument("rev", nargs="?", default="HEAD", help="commit (default: HEAD)")

    sp = add("note", cmd_note, "record the intent for the next commit")
    sp.add_argument("text", help="what the next commit is meant to do")

    add("install-hook", cmd_install_hook, "install the post-commit hook")

    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # em dashes in report output
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

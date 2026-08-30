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
        sha = indexer.resolve_sha(root, args.rev)
        parent = indexer.parent_sha(conn, sha)
        changes = semdiff.load_changes(conn, sha)
        if not changes and parent is not None:
            changes = semdiff.diff_commits(conn, cfg, parent, sha, persist=False)
            impact.annotate(conn, cfg, parent, sha, changes)
        impacts = impact.analyze(conn, cfg, parent, sha, changes) if changes else {}
        print(report.render_commit(conn, cfg, sha, impacts=impacts))
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
    sp.add_argument("rev", nargs="?", default="HEAD", help="commit (default: HEAD)")
    sp.add_argument("--breakdown", action="store_true", help="also print the raw change list")

    add("catchup", cmd_catchup, "digest every change since the last-reviewed marker")
    add("snapshot", cmd_snapshot, "render the current architecture snapshot")

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

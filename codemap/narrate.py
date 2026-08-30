"""Optional LLM narrative stage (spec M8).

One function, one call site. The input is the *structured change record* built
from M4-M6 — never the repository. The model may summarize and connect those
facts; it must not introduce any that are not in the record. If the intent was
inferred, the narrative is hedged and marked. Disabled by default.

M0-M7 do not depend on anything here.
"""

from __future__ import annotations

import json
import os
import sqlite3

from . import impact as impact_mod
from . import intent, semdiff
from .config import Config
from .report import _describe  # deterministic one-liners, reused as record facts

_SYSTEM = (
    "You turn a structured code-change record into a short plain-language note "
    "for a developer who did not write the change. Two to four sentences. Use "
    "ONLY facts present in the record — do not invent callers, reasons, file "
    "names, or behavior. If intent.source is 'inferred', do not state why the "
    "change was made; open with 'Likely:' and stay descriptive. No bullet "
    "points, no headings, no restating the commit hash."
)


def build_record(conn: sqlite3.Connection, cfg: Config, sha: str) -> dict:
    """The structured facts M8 is allowed to see. No source code."""
    src, text = intent.load(conn, sha)
    changes = semdiff.load_changes(conn, sha)
    impacts = impact_mod.analyze(
        conn, cfg, _parent(conn, sha), sha, changes
    )

    grouped: dict[str, list[str]] = {semdiff.STRUCTURAL: [], semdiff.BEHAVIORAL: []}
    for c in changes:
        if c.severity in grouped and (d := _describe(c)):
            grouped[c.severity].append(d)

    impact_facts = []
    for key, imp in impacts.items():
        if imp.callers:
            impact_facts.append(
                {
                    "symbol": key.split("::")[-1],
                    "callers": len(imp.callers),
                    "callers_updated_same_commit": imp.n_modified,
                    "entry_path": imp.entry_paths[0] if imp.entry_paths else None,
                    "tier": imp.tier,
                }
            )

    return {
        "intent": {"source": src, "text": text},
        "structural": grouped[semdiff.STRUCTURAL],
        "behavioral": grouped[semdiff.BEHAVIORAL],
        "cosmetic_count": sum(1 for c in changes if c.severity == semdiff.COSMETIC),
        "new_dependencies": sorted(
            {c.details.get("module", c.subject) for c in changes if c.change_type == "dependency_added"}
        ),
        "impact": impact_facts,
    }


def _parent(conn: sqlite3.Connection, sha: str) -> str | None:
    row = conn.execute("SELECT parent_sha FROM commits WHERE sha = ?", (sha,)).fetchone()
    return row["parent_sha"] if row else None


def narrate(conn: sqlite3.Connection, cfg: Config, sha: str) -> str | None:
    """Return a prose narrative, or ``None`` to fall back to the deterministic
    bullet lines (disabled, library missing, no key, or API error)."""
    if not cfg.llm.enabled:
        return None
    try:
        import anthropic
    except ImportError:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None

    record = build_record(conn, cfg, sha)
    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=cfg.llm.model,
            max_tokens=400,
            system=_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(record, indent=2, sort_keys=True)}],
        )
        parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        prose = " ".join(p.strip() for p in parts).strip()
    except Exception:
        return None

    if not prose:
        return None
    if record["intent"]["source"] == "inferred" and not prose.lower().startswith("likely"):
        prose = "Likely: " + prose
    return prose

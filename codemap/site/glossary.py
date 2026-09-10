"""Glossary / tooltip data for the explore.html surface.

Two tables feed the tooltip machinery:

- Two **bundled** JSON files in ``codemap/site/data/`` — ``glossary-packages.json``
  (every well-known package, tool, language and platform this glossary knows
  about) and ``glossary-concepts.json`` (everyday programming vocabulary that
  isn't the name of a specific package, authored by hand). Both ship with the
  ``codemap`` package and are read-only from the CLI's perspective.
- An optional **project-authored** override at ``.codemap/glossary.json``, in
  the same shape, for terms specific to this codebase (or to override a
  bundled definition the project disagrees with).

Both responsibilities below degrade gracefully on any problem -- absent file,
malformed JSON, wrong shape, an item missing a usable definition -- and never
raise, matching the house style of ``codemap/site/libraries.py``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from ..config import Config

GLOSSARY_FILE = "glossary.json"  # the PROJECT-level authored override, at cfg.codemap_dir / this

_DATA_DIR = Path(__file__).with_name("data")
_PACKAGES_FILE = "glossary-packages.json"
_CONCEPTS_FILE = "glossary-concepts.json"


def load(cfg: Config) -> dict[str, dict]:
    """Load ``.codemap/glossary.json`` (project-authored terms). Same schema as
    the bundled files: ``{"items": {key: {"display"?, "definition"}}}``. Returns
    ``{}`` (not ``None`` -- this is merged, not substituted) on any problem:
    absent file, bad JSON, wrong shape, or an item missing a usable definition."""
    path = cfg.codemap_dir / GLOSSARY_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    raw = data.get("items") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}

    items: dict[str, dict] = {}
    for key, entry in raw.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(entry, dict):
            continue
        definition = entry.get("definition")
        if not isinstance(definition, str) or not definition.strip():
            continue
        display = entry.get("display")
        display = display.strip() if isinstance(display, str) and display.strip() else key.strip().title()
        items[key.strip()] = {"display": display, "definition": definition.strip()}
    return items


def _load_bundled_file(name: str) -> dict[str, dict]:
    """Read+validate one bundled ``data/*.json`` glossary file. Never raises."""
    try:
        data = json.loads((_DATA_DIR / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw = data.get("items") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}

    items: dict[str, dict] = {}
    for key, entry in raw.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(entry, dict):
            continue
        definition = entry.get("definition")
        if not isinstance(definition, str) or not definition.strip():
            continue
        display = entry.get("display")
        display = display.strip() if isinstance(display, str) and display.strip() else key.strip()
        clean = {"display": display, "definition": definition.strip()}
        explanation = entry.get("explanation")
        if isinstance(explanation, str) and explanation.strip():
            clean["explanation"] = explanation.strip()
        items[key.strip()] = clean
    return items


@lru_cache(maxsize=None)
def _bundled() -> dict[str, dict]:
    """Load+merge the two bundled JSON files from ``codemap/site/data/``, cached
    (mirrors the ``@lru_cache`` pattern in ``codemap/site/render.py::_asset``).
    Concepts take precedence over packages on key collision (a real one:
    'react' is a package but the concept table might legitimately also want a
    generic 'state' entry that must never be shadowed by a same-named package)."""
    merged = dict(_load_bundled_file(_PACKAGES_FILE))
    merged.update(_load_bundled_file(_CONCEPTS_FILE))
    return merged


def build(
    cfg: Config, prose: list[str], extra_terms: dict[str, str] | None = None
) -> dict | None:
    """Merge project ``glossary.json`` > ``extra_terms`` > bundled concepts >
    bundled packages (each tier wins on key collision over the ones before
    it), then filter to ONLY terms that actually occur (case-insensitive,
    substring is fine here -- exact whole-word matching happens later in JS)
    somewhere in ``prose``. Return ``{"terms": {display: definition}}`` keyed
    by the DISPLAY name (not the lowercase key), or ``None`` if nothing
    matched or ``prose`` is empty. Never raise -- any error in ``cfg``/file
    access degrades to treating the bundled tables as the whole glossary and
    project overrides as absent.

    ``extra_terms`` is a plain ``{term: definition}`` map for terms authored
    inline elsewhere (``.codemap/walkthrough.json`` has its own optional
    ``glossary`` field, separate from the standalone ``.codemap/glossary.json``
    this module otherwise owns) -- it sits between the bundled tables and the
    dedicated project file so the dedicated file still wins a real conflict."""
    if not prose:
        return None
    prose_lower = "\n".join(p for p in prose if isinstance(p, str)).lower()
    if not prose_lower.strip():
        return None

    try:
        project = load(cfg)
    except Exception:
        project = {}

    candidates: dict[str, dict] = {}
    candidates.update(_bundled())
    for key, definition in (extra_terms or {}).items():
        if isinstance(key, str) and key.strip() and isinstance(definition, str) and definition.strip():
            candidates[key.strip()] = {"display": key.strip(), "definition": definition.strip()}
    candidates.update(project)

    terms: dict[str, str] = {}
    for key, entry in candidates.items():
        if key and key.lower() in prose_lower:
            display = entry.get("display") or key
            terms[display] = entry.get("definition", "")

    return {"terms": terms} if terms else None

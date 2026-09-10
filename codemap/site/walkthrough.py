"""Load and validate ``.codemap/walkthrough.json`` — authored content that drives
the Learn tab's walkthrough: a plain-language intro to the project (optionally
split into "sides" such as frontend/backend, plus the seam where they meet), a
set of curated categories grouping symbols by topic, prose for specific
folders, and a small glossary of plain-English terms.

Written by a Claude skill in a later phase of this project, never by codemap
itself. Absent or malformed content is not an error — the Learn tab simply has
no authored walkthrough to show. This loader only validates and cleans; it
never invents content and never resolves ``see`` references against the graph
(that happens client-side, exactly like ``libraries.json``'s own ``see``
entries — unresolvable keys are silently dropped downstream).

Shape:

    {
      "intro": {
        "what": "one or two sentences describing the project",
        "sides": {
          "frontend": {"root": "frontend", "title": "...", "body": "..."}
        },
        "seam": {"note": "...", "see": [...]}
      },
      "categories": [
        {"id": "look", "title": "Website design", "body": "...",
         "groups": [{"title": "Animation", "see": [...]}]}
      ],
      "folders": {
        "frontend/src/components/ui": {
          "title": "...", "purpose": "...", "read_first": "...",
          "note": "...", "categories": ["look"], "see": [...]
        }
      },
      "glossary": {"contact form": "..."}
    }

Every ``see`` value (``intro.seam.see``, ``categories[].groups[].see``,
``folders[<path>].see``) may be given as either a flat array of strings or an
object mapping a group label to an array of strings. Both shapes are
normalized here into one canonical list form so downstream consumers never
need to branch on flat-vs-grouped again — see ``_norm_see``.
"""

from __future__ import annotations

import json
import re

from ..config import Config

WALKTHROUGH_FILE = "walkthrough.json"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _norm_see(v) -> list[dict] | None:
    """Normalize either shape into a canonical list:
       [{"group": None, "keys": [...]}]                       -- from a flat array
       [{"group": "<label>", "keys": [...]}, ...]              -- from an object, insertion order preserved
    Each key is stripped; empty/non-string keys are dropped. A group whose
    `keys` end up empty is dropped. Returns None if nothing survives.
    Anything that isn't a list or a dict (e.g. a string, a number) returns None."""
    if isinstance(v, list):
        keys = [s.strip() for s in v if isinstance(s, str) and s.strip()]
        return [{"group": None, "keys": keys}] if keys else None

    if isinstance(v, dict):
        groups: list[dict] = []
        for label, raw_keys in v.items():
            if not isinstance(label, str):
                continue
            if not isinstance(raw_keys, list):
                continue
            keys = [s.strip() for s in raw_keys if isinstance(s, str) and s.strip()]
            if keys:
                groups.append({"group": label.strip(), "keys": keys})
        return groups if groups else None

    return None


def _slugify(title: str) -> str:
    slug = _SLUG_RE.sub("-", title.strip().lower()).strip("-")
    return slug or "category"


def _clean_intro(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    clean: dict = {}

    what = raw.get("what")
    if isinstance(what, str) and what.strip():
        clean["what"] = what.strip()

    sides_raw = raw.get("sides")
    if isinstance(sides_raw, dict):
        sides: dict = {}
        for name, side in sides_raw.items():
            if not isinstance(name, str) or not name.strip():
                continue
            if not isinstance(side, dict):
                continue
            root = side.get("root")
            if not isinstance(root, str) or not root.strip():
                continue
            side_clean = {"root": root.strip()}
            for key in ("title", "body"):
                v = side.get(key)
                if isinstance(v, str) and v.strip():
                    side_clean[key] = v.strip()
            sides[name.strip()] = side_clean
        if sides:
            clean["sides"] = sides

    seam_raw = raw.get("seam")
    if isinstance(seam_raw, dict):
        seam_clean: dict = {}
        note = seam_raw.get("note")
        if isinstance(note, str) and note.strip():
            seam_clean["note"] = note.strip()
        see = _norm_see(seam_raw.get("see"))
        if see:
            seam_clean["see"] = see
        if seam_clean:
            clean["seam"] = seam_clean

    return clean if clean else None


def _clean_categories(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []

    cleaned: list[dict] = []
    seen_ids: dict[str, int] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        title = title.strip()

        groups_raw = item.get("groups")
        groups: list[dict] = []
        if isinstance(groups_raw, list):
            for g in groups_raw:
                if not isinstance(g, dict):
                    continue
                g_title = g.get("title")
                if not isinstance(g_title, str) or not g_title.strip():
                    continue
                see = _norm_see(g.get("see"))
                if not see:
                    continue
                groups.append({"title": g_title.strip(), "see": see})
        if not groups:
            continue  # categories exist to hold groups

        cid = item.get("id")
        cid = cid.strip() if isinstance(cid, str) and cid.strip() else _slugify(title)

        count = seen_ids.get(cid, 0) + 1
        seen_ids[cid] = count
        final_id = cid if count == 1 else f"{cid}-{count}"

        clean: dict = {"id": final_id, "title": title, "groups": groups}
        body = item.get("body")
        if isinstance(body, str) and body.strip():
            clean["body"] = body.strip()
        cleaned.append(clean)

    return cleaned


def _clean_folders(raw) -> dict[str, dict]:
    if not isinstance(raw, dict):
        return {}

    cleaned: dict[str, dict] = {}
    for path, entry in raw.items():
        if not isinstance(path, str):
            continue
        path = path.strip()
        if not path or not isinstance(entry, dict):
            continue

        purpose = entry.get("purpose")
        if not isinstance(purpose, str) or not purpose.strip():
            continue  # purpose is the one required field

        clean: dict = {"purpose": purpose.strip()}
        for key in ("title", "read_first", "note"):
            v = entry.get(key)
            if isinstance(v, str) and v.strip():
                clean[key] = v.strip()

        categories = entry.get("categories")
        if isinstance(categories, list):
            cats = [c.strip() for c in categories if isinstance(c, str) and c.strip()]
            if cats:
                clean["categories"] = cats

        see = _norm_see(entry.get("see"))
        if see:
            clean["see"] = see

        cleaned[path] = clean

    return cleaned


def _clean_glossary(raw) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}

    cleaned: dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        k2, v2 = k.strip(), v.strip()
        if k2 and v2:
            cleaned[k2] = v2

    return cleaned


def load(cfg: Config) -> dict | None:
    """Return the cleaned walkthrough document, or ``None`` when the file is
    absent, unreadable, malformed, or leaves nothing usable. Never raises."""
    path = cfg.codemap_dir / WALKTHROUGH_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None

    result: dict = {}

    intro = _clean_intro(data.get("intro"))
    if intro:
        result["intro"] = intro

    categories = _clean_categories(data.get("categories"))
    if categories:
        result["categories"] = categories

    folders = _clean_folders(data.get("folders"))
    if folders:
        result["folders"] = folders

    glossary = _clean_glossary(data.get("glossary"))
    if glossary:
        result["glossary"] = glossary

    return result if result else None

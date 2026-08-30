"""Entry-point heuristics (spec M5).

An entry point is where execution enters the codebase from outside: an HTTP
route, a CLI command, a task-queue handler, a ``__main__`` guard, a
``[project.scripts]`` console script. Detection is heuristic and per-language;
adding a framework means adding a pattern here.

Every detected entry point is tied to a symbol so it can anchor a reachability
path in ``impact.py``.
"""

from __future__ import annotations

import re
import tomllib

from . import gitio

# decorator text (already whitespace-normalized) -> (kind, method-or-None)
_HTTP_VERBS = ("get", "post", "put", "patch", "delete", "options", "head")

_PY_ROUTE = re.compile(
    r"@(?P<obj>[\w.]+)\.(?P<attr>route|" + "|".join(_HTTP_VERBS) + r")\b\s*\(?\s*(?P<arg>['\"][^'\"]*['\"])?"
)
_PY_CLI = re.compile(r"@(?P<obj>[\w.]+)\.(command|group)\b")
_PY_TASK = re.compile(r"@(?P<obj>[\w.]+\.)?(shared_task|task)\b")
_PY_FASTAPI_DEP = re.compile(r"@(?P<obj>[\w.]+)\.(websocket)\b")

_TS_ROUTE = re.compile(
    r"@(Get|Post|Put|Patch|Delete|Options|Head|All)\s*\(\s*(?P<arg>['\"][^'\"]*['\"])?"
)
_TS_CONTROLLER = re.compile(r"@Controller\s*\(\s*(?P<arg>['\"][^'\"]*['\"])?")


def from_decorators(decorators: str | None, lang: str) -> tuple[str, str] | None:
    """Return ``(kind, detail)`` if any decorator marks an entry point."""
    if not decorators:
        return None
    for line in decorators.splitlines():
        line = line.strip()
        if lang == "python":
            m = _PY_ROUTE.match(line)
            if m:
                verb = m.group("attr")
                method = "ANY" if verb == "route" else verb.upper()
                path = (m.group("arg") or "").strip("'\"") or "/"
                return "route", f"{method} {path}"
            if _PY_CLI.match(line):
                return "cli", line
            if _PY_TASK.match(line):
                return "task", line
            if _PY_FASTAPI_DEP.match(line):
                return "route", f"WS {line}"
        elif lang in ("typescript", "tsx", "javascript"):
            m = _TS_ROUTE.match(line)
            if m:
                verb = line[1 : line.index("(")].upper()
                path = (m.group("arg") or "").strip("'\"")
                return "route", f"{verb} {path}".strip()
            if _TS_CONTROLLER.match(line):
                return "controller", line
    return None


_SCRIPTS_RE = re.compile(r"(?P<name>[\w.-]+)\s*=\s*['\"](?P<target>[\w.]+):(?P<func>[\w.]+)['\"]")
_DOCKER_RE = re.compile(r"^\s*(CMD|ENTRYPOINT)\s+(.+)$", re.MULTILINE)


def repo_scripts(root, sha: str) -> list[tuple[str, str, str]]:
    """``[project.scripts]`` entries as ``(script_name, module, func)``."""
    blob = gitio.show_bytes(root, sha, "pyproject.toml")
    if not blob:
        return []
    try:
        data = tomllib.loads(blob.decode("utf-8", "replace"))
    except tomllib.TOMLDecodeError:
        return []
    scripts = data.get("project", {}).get("scripts", {})
    out = []
    for name, target in scripts.items():
        if isinstance(target, str) and ":" in target:
            module, func = target.split(":", 1)
            out.append((name, module.strip(), func.strip()))
    return out


def dockerfile_commands(root, sha: str) -> list[str]:
    blob = gitio.show_bytes(root, sha, "Dockerfile")
    if not blob:
        return []
    return [m.group(0).strip() for m in _DOCKER_RE.finditer(blob.decode("utf-8", "replace"))]

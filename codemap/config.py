"""``.codemap/config.toml`` — load with defaults, write a documented template on init.

No stdlib TOML writer exists, so the template is emitted from a constant. Reading
uses ``tomllib`` (Python 3.11+).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CODEMAP_DIR = ".codemap"
CONFIG_NAME = "config.toml"
DB_NAME = "index.db"

# Directories excluded regardless of .gitignore (spec M1).
HARD_EXCLUDES = (
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "target",
    "__pycache__",
    ".codemap",
)

_TEMPLATE = """\
# codemap configuration. See https://github.com/devanr29/codebase-to-learn/tree/main/docs/spec.md.

# Extra ignore patterns (gitignore syntax), applied on top of .gitignore and the
# built-in hard excludes (.git, .venv, node_modules, vendor, dist, build, target, ...).
ignore = []

# Languages to index. Empty = every language with a registered grammar.
languages = []

# Transitive caller depth for impact analysis.
impact_depth = 3

# Full graphs are retained for this many recent commits; older commits keep their
# change records but drop per-symbol version rows.
retention = 50

[llm]
# M8 narrative stage. Deterministic output (M0-M7) is unaffected by this flag.
enabled = false
model = "claude-sonnet-5"

[explore]
# The self-contained explore.html surface (M10-M14). The renderer is
# deterministic and never calls an LLM; teaching content, when present, is
# authored separately into .codemap/libraries.json, explanations.json and
# scenarios.json (see the codebase-to-course skill).
rebuild_on_commit = true
max_symbols = 1500
max_snippet_lines = 40
"""


@dataclass
class LLMConfig:
    enabled: bool = False
    model: str = "claude-sonnet-5"


@dataclass
class ExploreConfig:
    rebuild_on_commit: bool = True
    max_symbols: int = 1500
    max_snippet_lines: int = 40


@dataclass
class Config:
    root: Path
    ignore: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    impact_depth: int = 3
    retention: int = 50
    llm: LLMConfig = field(default_factory=LLMConfig)
    explore: ExploreConfig = field(default_factory=ExploreConfig)

    @property
    def codemap_dir(self) -> Path:
        return self.root / CODEMAP_DIR

    @property
    def config_path(self) -> Path:
        return self.codemap_dir / CONFIG_NAME

    @property
    def db_path(self) -> Path:
        return self.codemap_dir / DB_NAME

    @property
    def changes_dir(self) -> Path:
        return self.codemap_dir / "changes"


def default_template() -> str:
    return _TEMPLATE


def load(root: Path | str) -> Config:
    """Load config for ``root``, falling back to defaults for anything unset."""
    root = Path(root).resolve()
    cfg = Config(root=root)
    path = cfg.config_path
    if not path.exists():
        return cfg

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    cfg.ignore = list(data.get("ignore", []))
    cfg.languages = list(data.get("languages", []))
    cfg.impact_depth = int(data.get("impact_depth", 3))
    cfg.retention = int(data.get("retention", 50))
    llm = data.get("llm", {})
    cfg.llm = LLMConfig(
        enabled=bool(llm.get("enabled", False)),
        model=str(llm.get("model", "claude-sonnet-5")),
    )
    exp = data.get("explore", {})
    cfg.explore = ExploreConfig(
        rebuild_on_commit=bool(exp.get("rebuild_on_commit", True)),
        max_symbols=int(exp.get("max_symbols", 1500)),
        max_snippet_lines=int(exp.get("max_snippet_lines", 40)),
    )
    return cfg


def _ensure_gitignore_entry(root: Path) -> None:
    """If the project has a .gitignore, make sure it ignores .codemap/.

    Skipped entirely when no .gitignore exists — we never create one.
    """
    gitignore_path = root / ".gitignore"
    if not gitignore_path.exists():
        return

    text = gitignore_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if any(line.strip().rstrip("/") == CODEMAP_DIR.rstrip("/") for line in lines):
        return

    needs_newline = len(text) > 0 and not text.endswith("\n")
    with gitignore_path.open("a", encoding="utf-8") as f:
        if needs_newline:
            f.write("\n")
        f.write(f"{CODEMAP_DIR}/\n")


def init(root: Path | str) -> Config:
    """Create ``.codemap/`` and write the config template if absent."""
    cfg = load(root)
    cfg.codemap_dir.mkdir(parents=True, exist_ok=True)
    cfg.changes_dir.mkdir(parents=True, exist_ok=True)
    if not cfg.config_path.exists():
        cfg.config_path.write_text(_TEMPLATE, encoding="utf-8")
    _ensure_gitignore_entry(cfg.root)
    return load(root)

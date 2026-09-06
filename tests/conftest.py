from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.build_repo import FixtureRepo, build_impact_repo, build_repo


@pytest.fixture(autouse=True)
def _no_progress_animation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test goes through the CLI/indexer/model with progress silenced,
    regardless of the developer's terminal -- deterministic and no stray
    output. `tests/test_progress.py` opts back in explicitly by constructing
    reporters with an explicit mode instead of going through env/isatty."""
    monkeypatch.setenv("CODEMAP_NO_PROGRESS", "1")


@pytest.fixture(scope="session")
def fixture_repo(tmp_path_factory: pytest.TempPathFactory) -> FixtureRepo:
    dest = tmp_path_factory.mktemp("codemap_fixture_repo")
    return build_repo(Path(dest))


@pytest.fixture(scope="session")
def fixture_impact_repo(tmp_path_factory: pytest.TempPathFactory) -> FixtureRepo:
    dest = tmp_path_factory.mktemp("codemap_impact_repo")
    return build_impact_repo(Path(dest))

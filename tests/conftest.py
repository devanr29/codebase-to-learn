from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.build_repo import FixtureRepo, build_repo


@pytest.fixture(scope="session")
def fixture_repo(tmp_path_factory: pytest.TempPathFactory) -> FixtureRepo:
    dest = tmp_path_factory.mktemp("codemap_fixture_repo")
    return build_repo(Path(dest))

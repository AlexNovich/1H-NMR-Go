"""Local corpus is explicitly selected, never silently skipped or sent to CI."""
from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption("--local-data-root", default=None,
                     help="Opt in to local confidential corpus checks at this project root")


def pytest_ignore_collect(collection_path, config):
    return collection_path.name == "local" and config.getoption("--local-data-root") is None


@pytest.fixture(scope="session")
def local_root(request):
    value = request.config.getoption("--local-data-root")
    if value is None or not Path(value).is_dir():
        raise pytest.UsageError("Requested local corpus root does not exist")
    root = Path(value)
    if not (root / "local_data/ref_operator").is_dir() or not (root / "local_data/extracted").is_dir():
        raise pytest.UsageError("Requested local corpus layout is missing")
    return root

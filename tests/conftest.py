import subprocess
from pathlib import Path

import pytest

from melog.git import Repo
from melog.store import Store


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture(autouse=True)
def isolated_git_config(tmp_path_factory: pytest.TempPathFactory, monkeypatch):
    """Keep the user's global and system git config out of the tests."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def repo_path(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.name", "Tester")
    git(path, "config", "user.email", "tester@example.com")
    (path / "train.py").write_text("print('train')\n")
    git(path, "add", "train.py")
    git(path, "commit", "-q", "-m", "initial")
    monkeypatch.chdir(path)
    return path


@pytest.fixture
def repo(repo_path: Path) -> Repo:
    return Repo.discover(repo_path)


@pytest.fixture
def store(repo: Repo) -> Store:
    return Store(repo)

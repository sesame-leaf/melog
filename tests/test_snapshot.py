from pathlib import Path

from melog.git import Repo
from melog.snapshot import LARGE_FILE_BYTES, keep, take_snapshot

from .conftest import git


def files_in(repo: Repo, commit: str) -> dict[str, str]:
    names = repo.run("ls-tree", "-r", "--name-only", "-z", commit).split("\0")
    return {n: repo.run("show", f"{commit}:{n}") for n in names if n}


def test_snapshot_captures_work_tree(repo_path: Path, repo: Repo) -> None:
    (repo_path / ".gitignore").write_text("data/\n")
    git(repo_path, "add", ".gitignore")
    git(repo_path, "commit", "-q", "-m", "ignore data")
    (repo_path / "train.py").write_text("print('changed')\n")
    (repo_path / "new.ipynb").write_text("{}\n")
    (repo_path / "data").mkdir()
    (repo_path / "data" / "train.csv").write_text("a,b\n")

    snapshot = take_snapshot(repo, "test")

    assert files_in(repo, snapshot.commit) == {
        ".gitignore": "data/\n",
        "train.py": "print('changed')\n",
        "new.ipynb": "{}\n",
    }
    assert repo.resolve(f"{snapshot.commit}^") == repo.resolve("HEAD")


def test_snapshot_includes_deletions(repo_path: Path, repo: Repo) -> None:
    (repo_path / "train.py").unlink()
    snapshot = take_snapshot(repo, "test")
    assert files_in(repo, snapshot.commit) == {}


def test_snapshot_leaves_user_state_alone(repo_path: Path, repo: Repo) -> None:
    (repo_path / "staged.py").write_text("x = 1\n")
    git(repo_path, "add", "staged.py")
    (repo_path / "staged.py").write_text("x = 2\n")
    (repo_path / "untracked.py").write_text("y = 1\n")
    head = repo.resolve("HEAD")
    status = git(repo_path, "status", "--porcelain")
    staged = git(repo_path, "diff", "--cached")

    snapshot = take_snapshot(repo, "test")

    assert files_in(repo, snapshot.commit)["staged.py"] == "x = 2\n"
    assert repo.resolve("HEAD") == head
    assert git(repo_path, "status", "--porcelain") == status
    assert git(repo_path, "diff", "--cached") == staged
    assert git(repo_path, "log", "--oneline", "--all") == git(
        repo_path, "log", "--oneline"
    )


def test_snapshot_without_commits(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q")
    (tmp_path / "a.py").write_text("a\n")
    repo = Repo.discover(tmp_path)
    snapshot = take_snapshot(repo, "test")
    assert files_in(repo, snapshot.commit) == {"a.py": "a\n"}
    assert repo.resolve(f"{snapshot.commit}^") is None


def test_large_untracked_files_are_reported(repo_path: Path, repo: Repo) -> None:
    with open(repo_path / "big.bin", "wb") as f:
        f.truncate(LARGE_FILE_BYTES + 1)
    (repo_path / "small.txt").write_text("s\n")
    snapshot = take_snapshot(repo, "test")
    assert snapshot.large_files == [("big.bin", LARGE_FILE_BYTES + 1)]


def test_keep(repo: Repo) -> None:
    snapshot = take_snapshot(repo, "test")
    keep(repo, "refs/melog/snapshots/p/bjs-1/start", snapshot.commit)
    assert repo.resolve("refs/melog/snapshots/p/bjs-1/start") == snapshot.commit

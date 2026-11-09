"""Code snapshots: commits that capture a work tree without touching it."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from melog.git import Repo

SNAPSHOT_REF_PREFIX = "refs/melog/snapshots"
LARGE_FILE_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class Snapshot:
    commit: str
    large_files: list[tuple[str, int]] = field(default_factory=list)
    """Untracked files over LARGE_FILE_BYTES that the snapshot includes."""


def snapshot_ref(project: str, exp_id: str, kind: str) -> str:
    return f"{SNAPSHOT_REF_PREFIX}/{project}/{exp_id}/{kind}"


def take_snapshot(repo: Repo, message: str) -> Snapshot:
    """Commit the current state of the work tree without changing anything.

    The snapshot holds tracked files and untracked files that are not ignored,
    including changes that are not committed or staged. Its parent is HEAD.
    """
    head = repo.resolve("HEAD^{commit}")
    large_files = _large_untracked_files(repo)
    with tempfile.TemporaryDirectory(prefix="melog-") as tmp:
        index = Path(tmp) / "index"
        env = {"GIT_INDEX_FILE": str(index)}
        user_index = repo.git_path("index")
        if user_index.exists():
            # Starting from the user's index keeps git's stat cache, which makes
            # 'git add' fast on large work trees. The index itself is not modified.
            shutil.copyfile(user_index, index)
        elif head is not None:
            repo.run("read-tree", head, env=env)
        else:
            repo.run("read-tree", "--empty", env=env)
        repo.run("add", "--all", "--", ".", env=env)
        tree = repo.run("write-tree", env=env).strip()
    parent_args = ["-p", head] if head else []
    commit = repo.run(
        "commit-tree", tree, *parent_args, "-m", message, env=repo.identity_env()
    ).strip()
    return Snapshot(commit=commit, large_files=large_files)


def keep(repo: Repo, ref: str, commit: str) -> None:
    """Point ``ref`` at ``commit`` so that git never garbage-collects it."""
    repo.run("update-ref", ref, commit)


def _large_untracked_files(repo: Repo) -> list[tuple[str, int]]:
    out = repo.run("ls-files", "--others", "--exclude-standard", "-z")
    result = []
    for name in out.split("\0"):
        if not name:
            continue
        try:
            size = (repo.root / name).stat().st_size
        except OSError:
            continue
        if size > LARGE_FILE_BYTES:
            result.append((name, size))
    return result


def checkout_worktree(repo: Repo, commit: str, path: Path) -> None:
    """Check out ``commit`` into a new git worktree at ``path``, detached."""
    repo.run("worktree", "add", "--detach", str(path), commit)

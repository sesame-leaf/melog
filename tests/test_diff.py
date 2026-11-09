import json
from pathlib import Path

import pytest

from melog import experiment
from melog.diff import (
    EntryPointsError,
    NoBaseError,
    NoEndSnapshotError,
    diff_commits,
    diff_pairs,
    entry_point_pairs,
    notebook_code,
    resolve_sides,
)
from melog.experiment import UnknownRevisionError
from melog.git import Repo
from melog.project import create_project
from melog.snapshot import take_snapshot
from melog.store import Store
from melog.workspace import Workspace

from .conftest import git


def notebook(*cells, outputs: str = "", count: int | None = None) -> str:
    """A notebook with the given (cell_type, source) cells."""
    return json.dumps(
        {
            "cells": [
                {
                    "cell_type": kind,
                    "source": source.splitlines(keepends=True),
                    "metadata": {},
                    **(
                        {
                            "outputs": [{"output_type": "stream", "text": outputs}],
                            "execution_count": count,
                        }
                        if kind == "code"
                        else {}
                    ),
                }
                for kind, source in cells
            ],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 5,
        },
        indent=1,
    )


def test_notebook_code() -> None:
    text = notebook(("markdown", "# Title"), ("code", "a = 1\nb = 2"), ("code", "c"))
    assert notebook_code(text) == [
        "# ── code cell 1 ──\n",
        "a = 1\n",
        "b = 2\n",
        "# ── code cell 2 ──\n",
        "c\n",
    ]
    assert notebook_code("not json") is None
    assert notebook_code("{}") is None


def snap(repo: Repo) -> str:
    return take_snapshot(repo, "test").commit


def test_diff_commits_text_files(repo_path: Path, repo: Repo) -> None:
    (repo_path / "old.py").write_text("x = 1\n" * 5)
    base = snap(repo)
    (repo_path / "train.py").write_text("print('new')\n")
    (repo_path / "new.py").write_text("y = 2\n")
    (repo_path / "old.py").rename(repo_path / "moved.py")
    target = snap(repo)

    files = {f.path: f for f in diff_commits(repo, base, target)}
    assert {p: f.status for p, f in files.items()} == {
        "moved.py": "renamed",
        "new.py": "added",
        "train.py": "modified",
    }
    assert files["moved.py"].old_path == "old.py"
    assert "+print('new')" in files["train.py"].text
    assert (files["train.py"].additions, files["train.py"].deletions) == (1, 1)
    assert [f.path for f in diff_commits(repo, base, target, ["new.py"])] == ["new.py"]


def test_diff_commits_notebooks(repo_path: Path, repo: Repo) -> None:
    nb = repo_path / "exp.ipynb"
    nb.write_text(notebook(("code", "lr = 0.1"), outputs="0.80", count=1))
    base = snap(repo)

    # Only outputs and execution counts changed: nothing to report.
    nb.write_text(notebook(("code", "lr = 0.1"), outputs="0.81", count=7))
    assert diff_commits(repo, base, snap(repo)) == []

    nb.write_text(notebook(("code", "lr = 0.05"), outputs="0.82", count=1))
    target = snap(repo)
    [file] = diff_commits(repo, base, target)
    assert "(code cells only)" in file.text
    assert "-lr = 0.1\n+lr = 0.05\n" in file.text
    assert "0.82" not in file.text

    [raw] = diff_commits(repo, base, target, raw=True)
    assert "0.82" in raw.text


def test_broken_notebook_falls_back_to_text(repo_path: Path, repo: Repo) -> None:
    (repo_path / "bad.ipynb").write_text("not json\n")
    base = snap(repo)
    (repo_path / "bad.ipynb").write_text("still not json\n")
    [file] = diff_commits(repo, base, snap(repo))
    assert "+still not json" in file.text


def test_diff_pairs(repo_path: Path, repo: Repo) -> None:
    (repo_path / "bjs-1.ipynb").write_text(notebook(("code", "model = A()")))
    base = snap(repo)
    (repo_path / "bjs-2.ipynb").write_text(notebook(("code", "model = B()")))
    target = snap(repo)
    [file] = diff_pairs(repo, base, target, [("bjs-1.ipynb", "bjs-2.ipynb")], raw=False)
    assert file.old_path == "bjs-1.ipynb"
    assert file.path == "bjs-2.ipynb"
    assert "-model = A()\n+model = B()\n" in file.text


# Choosing what to compare


@pytest.fixture
def setup(repo_path: Path, repo: Repo, store: Store):
    create_project(store, "lgbm")
    ws = Workspace(repo)

    def start(**kwargs):
        return experiment.start(repo, store, ws, "lgbm", "bjs", "t", **kwargs).record

    def finish():
        return experiment.finish(repo, store, ws, "lgbm", None, "done").record

    return start, finish


def test_resolve_default_and_work_tree(
    repo_path: Path, repo: Repo, store: Store, setup
):
    start, finish = setup
    record = start()
    (repo_path / "train.py").write_text("print('wip')\n")
    base, target, _ = resolve_sides(repo, store.view(), "lgbm", record, None)
    assert (base.kind, base.commit) == ("start_snapshot", record.start_snapshot)
    assert target.kind == "work_tree"
    assert "+print('wip')" in diff_commits(repo, base.commit, target.commit)[0].text

    done = finish()
    base, target, _ = resolve_sides(repo, store.view(), "lgbm", done, None)
    assert (target.kind, target.commit) == ("end_snapshot", done.end_snapshot)


def test_resolve_against(repo_path: Path, repo: Repo, store: Store, setup) -> None:
    start, finish = setup
    start()
    first = finish()
    second = start(parents=["bjs-1"])
    view = store.view()

    base, _, other = resolve_sides(repo, view, "lgbm", second, "bjs-1")
    assert (base.kind, base.commit, other.id) == (
        "experiment",
        first.end_snapshot,
        "bjs-1",
    )

    base, _, other = resolve_sides(repo, view, "lgbm", second, "HEAD")
    assert (base.kind, base.commit, other) == ("revision", repo.resolve("HEAD"), None)

    with pytest.raises(UnknownRevisionError):
        resolve_sides(repo, view, "lgbm", second, "nope")
    with pytest.raises(NoEndSnapshotError):
        resolve_sides(repo, view, "lgbm", first, "bjs-2")


def test_resolve_without_start_snapshot(repo: Repo, store: Store, setup) -> None:
    ws = Workspace(repo)
    record = experiment.register(repo, store, ws, "lgbm", "bjs", "retro").record
    with pytest.raises(NoBaseError):
        resolve_sides(repo, store.view(), "lgbm", record, None)


def test_entry_point_pairs(repo: Repo, store: Store, setup) -> None:
    start, finish = setup
    start(entry_points=["a.ipynb"])
    first = finish()
    second = start(entry_points=["b.ipynb"])
    assert entry_point_pairs(first, second) == [("a.ipynb", "b.ipynb")]
    with pytest.raises(EntryPointsError, match="--against"):
        entry_point_pairs(None, second)
    second.entry_points = []
    with pytest.raises(EntryPointsError, match="cannot pair"):
        entry_point_pairs(first, second)


# Checking out


def test_checkout(repo_path: Path, tmp_path: Path, repo: Repo, store: Store, setup):
    start, finish = setup
    start()
    (repo_path / "train.py").write_text("print('uncommitted')\n")
    (repo_path / "new.py").write_text("n\n")
    finish()

    path, commit = experiment.checkout(repo, store.view(), "lgbm", "bjs-1")
    assert path == repo_path.parent / "repo-bjs-1"
    assert (path / "train.py").read_text() == "print('uncommitted')\n"
    assert (path / "new.py").read_text() == "n\n"
    assert Repo.discover(path).resolve("HEAD") == commit
    # The user's own work tree is untouched.
    assert (repo_path / "train.py").read_text() == "print('uncommitted')\n"

    start_path, start_commit = experiment.checkout(
        repo, store.view(), "lgbm", "bjs-1", tmp_path / "start", start=True
    )
    assert (start_path / "train.py").read_text() == "print('train')\n"
    with pytest.raises(experiment.CheckoutPathExistsError):
        experiment.checkout(repo, store.view(), "lgbm", "bjs-1", start_path)
    git(repo_path, "worktree", "remove", "--force", str(path))


def test_checkout_in_progress_needs_start(repo: Repo, store: Store, setup, tmp_path):
    start, _ = setup
    start()
    with pytest.raises(experiment.NoSnapshotError, match="--start"):
        experiment.checkout(repo, store.view(), "lgbm", "bjs-1", tmp_path / "x")
    path, _ = experiment.checkout(
        repo, store.view(), "lgbm", "bjs-1", tmp_path / "x", start=True
    )
    assert (path / "train.py").exists()

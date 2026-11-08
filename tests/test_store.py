from pathlib import Path

import pytest

from melog.git import Repo
from melog.store import ConcurrentUpdateError, Store

from .conftest import git


def test_empty_store_reads_nothing(store: Store) -> None:
    view = store.view()
    assert view.commit is None
    assert view.read("a.txt") is None
    assert view.list_dir() == []


def test_commit_and_read(store: Store) -> None:
    store.commit({"p/a.txt": "hello\n", "p/b.txt": ""}, "add", base=store.view())
    view = store.view()
    assert view.read("p/a.txt") == "hello\n"
    assert view.read("p/b.txt") == ""
    assert view.read("p/missing.txt") is None
    assert view.list_dir() == ["p"]
    assert view.list_dir("p") == ["a.txt", "b.txt"]


def test_commit_keeps_utf8_and_newlines(store: Store) -> None:
    text = "## 동기와 예상\n\nline\r\nend"
    store.commit({"r.md": text}, "add", base=store.view())
    assert store.view().read("r.md") == text


def test_delete_file(store: Store) -> None:
    store.commit({"a.txt": "1", "b.txt": "2"}, "add", base=store.view())
    store.commit({"a.txt": None, "missing.txt": None}, "delete", base=store.view())
    view = store.view()
    assert view.read("a.txt") is None
    assert view.read("b.txt") == "2"


def test_unchanged_commit_is_noop(store: Store) -> None:
    first = store.commit({"a.txt": "1"}, "add", base=store.view())
    assert store.commit({"a.txt": "1"}, "again", base=store.view()) == first


def test_stale_base_is_rejected(store: Store) -> None:
    stale = store.view()
    store.commit({"a.txt": "1"}, "first", base=stale)
    with pytest.raises(ConcurrentUpdateError):
        store.commit({"b.txt": "2"}, "second", base=stale)
    assert store.view().read("b.txt") is None


def test_update_retries_on_concurrent_change(store: Store) -> None:
    calls = 0

    def build(view):
        nonlocal calls
        calls += 1
        if calls == 1:
            # Someone else updates the store while this change is being built.
            store.commit({"other.txt": "x"}, "other", base=store.view())
        return {"mine.txt": "y"}, "mine"

    store.update(build)
    view = store.view()
    assert calls == 2
    assert view.read("other.txt") == "x"
    assert view.read("mine.txt") == "y"


def test_user_work_tree_is_untouched(repo_path: Path, repo: Repo, store: Store) -> None:
    (repo_path / "train.py").write_text("print('changed')\n")
    (repo_path / "new.py").write_text("x = 1\n")
    git(repo_path, "add", "new.py")
    head = repo.resolve("HEAD")
    status = git(repo_path, "status", "--porcelain")

    store.commit({"a.txt": "1"}, "add", base=store.view())

    assert repo.resolve("HEAD") == head
    assert git(repo_path, "status", "--porcelain") == status
    assert git(repo_path, "branch", "--list") == "* main\n"


def test_works_without_git_identity(repo_path: Path, store: Store) -> None:
    git(repo_path, "config", "--unset", "user.name")
    git(repo_path, "config", "--unset", "user.email")
    store.commit({"a.txt": "1"}, "add", base=store.view())
    assert store.view().read("a.txt") == "1"

from dataclasses import dataclass
from pathlib import Path

import pytest

from melog import experiment
from melog import sync as syncing
from melog.git import Repo
from melog.project import create_project
from melog.record import MOTIVATION
from melog.snapshot import snapshot_ref
from melog.store import STORE_REF, Store
from melog.workspace import Workspace

from .conftest import git


def approve(count: int, files: list[str]) -> bool:
    return True


@dataclass
class Clone:
    path: Path
    initials: str

    @property
    def repo(self) -> Repo:
        return Repo.discover(self.path)

    @property
    def store(self) -> Store:
        return Store(self.repo)

    @property
    def ws(self) -> Workspace:
        return Workspace(self.repo)

    def start(self, title: str = "t", **kwargs):
        return experiment.start(
            self.repo, self.store, self.ws, "lgbm", self.initials, title, **kwargs
        ).record

    def finish(self, exp_id=None, **kwargs):
        return experiment.finish(
            self.repo, self.store, self.ws, "lgbm", exp_id, "done", **kwargs
        ).record

    def record(self, exp_id: str):
        return experiment.load_record(self.store.view(), "lgbm", exp_id)

    def edit(self, exp_id: str, **changes):
        base = self.record(exp_id)
        new = experiment.load_record(self.store.view(), "lgbm", exp_id)
        for key, value in changes.items():
            if key == "scores":
                new.scores.update(value)
            elif key in ("memo", "result"):
                new.body["메모" if key == "memo" else "결과"] = value
            else:
                setattr(new, key, value)
        return experiment.edit(self.store, "lgbm", base, new)[0]

    def sync(self, **kwargs):
        kwargs.setdefault("confirm", approve)
        return syncing.sync(self.repo, **kwargs)


@pytest.fixture
def clones(tmp_path: Path, repo_path: Path) -> tuple[Clone, Clone]:
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo_path, "remote", "add", "origin", str(remote))
    git(repo_path, "push", "-q", "origin", "main")
    git(repo_path, "config", "melog.initials", "bjs")
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(remote), str(other))
    git(other, "config", "user.name", "Other")
    git(other, "config", "user.email", "other@example.com")
    git(other, "config", "melog.initials", "mj")
    a, b = Clone(repo_path, "bjs"), Clone(other, "mj")
    create_project(a.store, "lgbm")
    return a, b


def remote_refs(clone: Clone) -> dict[str, str]:
    return syncing._remote_refs(clone.repo, "origin")


def test_first_sync_asks_before_uploading(clones) -> None:
    a, _ = clones
    (a.path / "secret.env").write_text("TOKEN=1\n")
    a.start()
    with pytest.raises(syncing.NotConfirmedError, match="--yes"):
        a.sync(confirm=None)
    seen = []

    def decline(count: int, files: list[str]) -> bool:
        seen.append((count, files))
        return False

    with pytest.raises(syncing.SyncCancelledError):
        a.sync(confirm=decline)
    assert seen == [(1, ["secret.env"])]
    assert remote_refs(a) == {}

    report = a.sync()
    assert report.sent == ["lgbm/bjs-1"]
    assert STORE_REF in remote_refs(a)
    # Later syncs with the same remote do not ask again.
    a.finish()
    a.sync(confirm=None)


def test_records_and_snapshots_reach_other_clones(clones) -> None:
    a, b = clones
    a.start(motivation="why")
    (a.path / "train.py").write_text("print('a')\n")
    done = a.finish(scores={"CV": "0.8"})
    a.sync()

    report = b.sync()
    assert report.received == ["lgbm/bjs-1"]
    assert b.record("bjs-1") == done
    path, _ = experiment.checkout(b.repo, b.store.view(), "lgbm", "bjs-1")
    assert (path / "train.py").read_text() == "print('a')\n"
    # Nothing changed since: both are in sync.
    assert b.sync().received == []
    assert a.sync().received == []


def test_changes_to_different_fields_merge(clones) -> None:
    a, b = clones
    a.start()
    a.finish()
    a.edit("bjs-1", memo="line 1\nline 2\nline 3")
    a.sync()
    b.sync()

    a.edit("bjs-1", verdict="good", memo="LINE 1\nline 2\nline 3")
    a.sync()
    b.edit("bjs-1", scores={"public LB": "0.79"}, memo="line 1\nline 2\nLINE 3")
    report = b.sync()

    assert report.updated == ["lgbm/bjs-1"]
    merged = b.record("bjs-1")
    assert merged.verdict == "good"
    assert merged.scores["public LB"] == "0.79"
    assert merged.section("메모") == "LINE 1\nline 2\nLINE 3"
    a.sync()
    assert a.record("bjs-1") == merged


def test_conflicts_change_nothing_without_a_choice(clones) -> None:
    a, b = clones
    a.start()
    a.finish()
    a.sync()
    b.sync()
    a.edit("bjs-1", verdict="theirs")
    a.sync()
    b.edit("bjs-1", verdict="mine", scores={"CV": "0.7"})
    before = b.store.view().commit

    with pytest.raises(syncing.SyncConflictError) as excinfo:
        b.sync()
    assert [(c.where, c.field) for c in excinfo.value.conflicts] == [
        ("lgbm/bjs-1", "verdict")
    ]
    assert b.store.view().commit == before

    asked = []

    def keep_local(conflict: syncing.Conflict) -> str:
        asked.append((conflict.local, conflict.remote))
        return "local"

    report = b.sync(resolve=keep_local)
    assert asked == [("mine", "theirs")]
    assert report.resolved == [("lgbm/bjs-1", "verdict", "local")]
    assert b.record("bjs-1").verdict == "mine"
    assert b.record("bjs-1").scores["CV"] == "0.7"


def test_prefer_settles_conflicts(clones) -> None:
    a, b = clones
    a.start()
    a.finish()
    a.sync()
    b.sync()
    a.edit("bjs-1", verdict="theirs", memo="a")
    a.sync()
    b.edit("bjs-1", verdict="mine", memo="b")
    b.sync(prefer="remote")
    assert b.record("bjs-1").verdict == "theirs"
    assert b.record("bjs-1").section("메모") == "a"


def test_colliding_ids_rename_the_local_experiment(clones) -> None:
    a, b = clones
    a.sync()
    b.sync()
    # The same person works in both clones without syncing in between.
    git(b.path, "config", "melog.initials", "bjs")
    b.initials = "bjs"
    a.start("from a")
    a_done = a.finish()
    a.sync()

    b.start("from b")
    b.finish()
    b.start("child of b", parents=["bjs-1"])  # still in progress
    report = b.sync()

    assert report.renamed == [("lgbm", "bjs-1", "bjs-3")]
    assert b.record("bjs-1") == a_done
    assert b.record("bjs-3").title == "from b"
    assert b.record("bjs-2").parents == ["bjs-3"]
    view = b.store.view()
    assert (
        b.repo.resolve(snapshot_ref("lgbm", "bjs-3", "end"))
        == b.record("bjs-3").end_snapshot
    )
    assert b.repo.resolve(snapshot_ref("lgbm", "bjs-1", "end")) == a_done.end_snapshot
    assert [x.id for x in experiment.active_here(view, b.ws)] == ["bjs-2"]

    a.sync()
    assert a.record("bjs-3").title == "from b"


def test_renaming_an_experiment_in_progress(clones) -> None:
    a, b = clones
    a.sync()
    b.sync()
    git(b.path, "config", "melog.initials", "bjs")
    b.initials = "bjs"
    a.start()
    a.finish()
    a.sync()
    b.start(motivation="b's idea")
    b.sync()
    # The experiment in progress follows its new id.
    done = b.finish()
    assert done.id == "bjs-2"
    assert done.body[MOTIVATION] == "b's idea"


def test_retries_when_the_remote_moves(clones, monkeypatch) -> None:
    a, b = clones
    a.sync()
    b.sync()
    b.start()
    b.finish()
    original = syncing._merge_stores
    calls = []

    def merge_after_someone_pushed(repo, *args, **kwargs):
        if repo.root == b.path:
            calls.append(1)
            if len(calls) == 1:
                # Another clone pushes after this one fetched.
                a.start("pushed meanwhile")
                a.finish()
                a.sync()
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(syncing, "_merge_stores", merge_after_someone_pushed)
    report = b.sync()
    assert len(calls) == 2
    assert "lgbm/bjs-1" in report.received
    monkeypatch.undo()
    assert a.sync().received == ["lgbm/mj-1"]


def test_unknown_remote(repo: Repo) -> None:
    with pytest.raises(syncing.NoRemoteError, match="remotes: none"):
        syncing.sync(repo, "origin")


def test_report_lists_sent_changes(clones) -> None:
    a, b = clones
    a.start()
    a.finish()
    a.sync()
    b.sync()
    b.edit("bjs-1", verdict="from b")
    report = b.sync()
    assert report.sent == []
    assert report.sent_changes == ["lgbm/bjs-1"]
    assert a.sync().updated == ["lgbm/bjs-1"]

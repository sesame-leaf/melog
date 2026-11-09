from pathlib import Path

import pytest

from melog import experiment
from melog.experiment import (
    AmbiguousActiveError,
    ExperimentNotFoundError,
    InvalidTitleError,
    NotActiveHereError,
    NothingActiveError,
    NotInProgressError,
    ParentNotFoundError,
    UnknownRevisionError,
    WorkspaceBusyError,
)
from melog.git import Repo
from melog.project import create_project
from melog.record import MOTIVATION, InvalidScoreError
from melog.snapshot import snapshot_ref
from melog.store import Store
from melog.workspace import Workspace

from .conftest import git


@pytest.fixture
def ws(repo: Repo) -> Workspace:
    return Workspace(repo)


@pytest.fixture
def lgbm(store: Store) -> str:
    create_project(store, "lgbm")
    return "lgbm"


def start(repo, store, ws, title="baseline", project="lgbm", **kwargs):
    return experiment.start(repo, store, ws, project, "bjs", title, **kwargs).record


def finish(repo, store, ws, exp_id=None, project="lgbm", **kwargs):
    return experiment.finish(repo, store, ws, project, exp_id, "done", **kwargs).record


def test_start_and_finish(repo_path: Path, repo: Repo, store: Store, ws, lgbm) -> None:
    record = start(repo, store, ws, motivation="try it", entry_points=["train.py"])
    assert record.id == "bjs-1"
    assert record.status == "in_progress"
    assert record.scores == {"CV": None, "CV std": None, "public LB": None}
    assert record.body[MOTIVATION] == "try it"
    assert record.started_at is not None
    assert repo.resolve(snapshot_ref("lgbm", "bjs-1", "start")) == record.start_snapshot
    assert [a.id for a in experiment.active_here(store.view(), ws)] == ["bjs-1"]

    (repo_path / "train.py").write_text("print('better')\n")
    done = finish(repo, store, ws, scores={"CV": "0.80"}, verdict=" better ")

    assert done.status == "done"
    assert done.scores["CV"] == "0.80"
    assert done.verdict == "better"
    assert done.ended_at is not None
    assert repo.run("show", f"{done.end_snapshot}:train.py") == "print('better')\n"
    assert repo.resolve(snapshot_ref("lgbm", "bjs-1", "end")) == done.end_snapshot
    assert experiment.load_record(store.view(), "lgbm", "bjs-1") == done
    assert experiment.active_here(store.view(), ws) == []


def test_ids_increase(repo: Repo, store: Store, ws, lgbm) -> None:
    for expected in ["bjs-1", "bjs-2", "bjs-3"]:
        assert start(repo, store, ws).id == expected
        finish(repo, store, ws)


def test_title_is_required(repo: Repo, store: Store, ws, lgbm) -> None:
    with pytest.raises(InvalidTitleError):
        start(repo, store, ws, title="  ")


def test_parents_must_exist(repo: Repo, store: Store, ws, lgbm) -> None:
    with pytest.raises(ParentNotFoundError):
        start(repo, store, ws, parents=["bjs-9"])
    start(repo, store, ws)
    finish(repo, store, ws)
    record = start(repo, store, ws, parents=["bjs-1", "bjs-1"])
    assert record.parents == ["bjs-1"]


def test_one_experiment_per_workspace(repo: Repo, store: Store, ws, lgbm) -> None:
    start(repo, store, ws)
    with pytest.raises(WorkspaceBusyError):
        start(repo, store, ws)


def test_horizontal_projects_allow_several(repo: Repo, store: Store, ws) -> None:
    create_project(store, "nb", horizontal=True)
    create_project(store, "vert")
    start(repo, store, ws, project="nb")
    start(repo, store, ws, project="nb")
    # A non-horizontal experiment would mix with the ones in progress.
    with pytest.raises(WorkspaceBusyError):
        start(repo, store, ws, project="vert")
    with pytest.raises(AmbiguousActiveError):
        finish(repo, store, ws, project="nb")
    assert finish(repo, store, ws, "bjs-2", project="nb").status == "done"


def test_horizontal_blocked_by_vertical_in_progress(
    repo: Repo, store: Store, ws
) -> None:
    create_project(store, "nb", horizontal=True)
    create_project(store, "vert")
    start(repo, store, ws, project="vert")
    with pytest.raises(WorkspaceBusyError):
        start(repo, store, ws, project="nb")


def test_finish_without_anything_in_progress(
    repo: Repo, store: Store, ws, lgbm
) -> None:
    with pytest.raises(NothingActiveError):
        finish(repo, store, ws)
    with pytest.raises(ExperimentNotFoundError):
        finish(repo, store, ws, "bjs-1")


def test_finish_twice(repo: Repo, store: Store, ws, lgbm) -> None:
    start(repo, store, ws)
    finish(repo, store, ws)
    with pytest.raises(NotInProgressError):
        finish(repo, store, ws, "bjs-1")


def test_invalid_score_takes_no_snapshot(repo: Repo, store: Store, ws, lgbm) -> None:
    start(repo, store, ws)
    with pytest.raises(InvalidScoreError):
        finish(repo, store, ws, scores={"CV": "high"})
    assert repo.resolve(snapshot_ref("lgbm", "bjs-1", "end")) is None
    assert finish(repo, store, ws, scores={"CV": "0.8"}).scores["CV"] == "0.8"


def test_abandon(repo: Repo, store: Store, ws, lgbm) -> None:
    start(repo, store, ws)
    record = experiment.finish(
        repo, store, ws, "lgbm", None, "abandoned", verdict="no time"
    ).record
    assert record.status == "abandoned"
    assert record.verdict == "no time"
    assert record.end_snapshot is not None
    assert start(repo, store, ws, parents=["bjs-1"]).parents == ["bjs-1"]


def test_worktrees_are_separate_workspaces(
    repo_path: Path, tmp_path: Path, repo: Repo, store: Store, ws, lgbm
) -> None:
    other_path = tmp_path / "other"
    git(repo_path, "worktree", "add", "-q", "-b", "other", str(other_path))
    other_repo = Repo.discover(other_path)
    other_ws = Workspace(other_repo)
    other_store = Store(other_repo)

    start(repo, store, ws)
    # The other worktree shares the store but not the experiments in progress.
    second = start(other_repo, other_store, other_ws)
    assert second.id == "bjs-2"
    assert [a.id for a in experiment.active_here(store.view(), other_ws)] == ["bjs-2"]

    (other_path / "train.py").write_text("print('other')\n")
    with pytest.raises(NotActiveHereError):
        finish(repo, store, ws, "bjs-2")
    done = finish(other_repo, other_store, other_ws, "bjs-2")
    assert repo.run("show", f"{done.end_snapshot}:train.py") == "print('other')\n"


def test_force_finish_from_another_workspace(
    repo_path: Path, tmp_path: Path, repo: Repo, store: Store, ws, lgbm
) -> None:
    other_path = tmp_path / "other"
    git(repo_path, "worktree", "add", "-q", "-b", "other", str(other_path))
    other_repo = Repo.discover(other_path)
    start(other_repo, Store(other_repo), Workspace(other_repo))
    record = finish(repo, store, ws, "bjs-1", force=True)
    assert record.status == "done"


def test_register(repo_path: Path, repo: Repo, store: Store, ws, lgbm) -> None:
    base = repo.resolve("HEAD")
    (repo_path / "train.py").write_text("print('retro')\n")
    record = experiment.register(
        repo,
        store,
        ws,
        "lgbm",
        "bjs",
        "forgot to start",
        since="HEAD",
        scores={"CV": "0.7"},
        verdict="ok",
        motivation="why",
    ).record
    assert record.id == "bjs-1"
    assert record.status == "done"
    assert record.start_snapshot == base
    assert record.started_at == repo.commit_time(base)
    assert record.scores["CV"] == "0.7"
    assert repo.resolve(snapshot_ref("lgbm", "bjs-1", "start")) == base
    assert repo.run("show", f"{record.end_snapshot}:train.py") == "print('retro')\n"
    assert experiment.active_here(store.view(), ws) == []


def test_register_without_since(repo: Repo, store: Store, ws, lgbm) -> None:
    record = experiment.register(repo, store, ws, "lgbm", "bjs", "retro").record
    assert record.start_snapshot is None
    assert record.started_at is None
    assert repo.resolve(snapshot_ref("lgbm", "bjs-1", "start")) is None


def test_register_unknown_revision(repo: Repo, store: Store, ws, lgbm) -> None:
    with pytest.raises(UnknownRevisionError):
        experiment.register(repo, store, ws, "lgbm", "bjs", "retro", since="nope")


def test_register_refused_while_in_progress(repo: Repo, store: Store, ws, lgbm) -> None:
    start(repo, store, ws)
    with pytest.raises(WorkspaceBusyError):
        experiment.register(repo, store, ws, "lgbm", "bjs", "retro")


def test_list_records_is_chronological(
    repo_path: Path, repo: Repo, store: Store, ws, lgbm, monkeypatch
) -> None:
    for _ in range(3):
        start(repo, store, ws)
        finish(repo, store, ws)
    # An old commit made in another time zone: its time sorts by instant.
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2020-01-01T23:00:00-05:00")
    git(repo_path, "commit", "-q", "--allow-empty", "-m", "old")
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    experiment.register(repo, store, ws, "lgbm", "bjs", "retro", since="HEAD")
    ids = [r.id for r in experiment.list_records(store.view(), "lgbm")]
    assert ids == ["bjs-4", "bjs-1", "bjs-2", "bjs-3"]


# Editing


def edited_copy(record, **changes):
    import copy

    new = copy.deepcopy(record)
    for key, value in changes.items():
        setattr(new, key, value)
    return new


def test_edit_applies_changed_fields(repo: Repo, store: Store, ws, lgbm) -> None:
    base = start(repo, store, ws)
    new = edited_copy(base, verdict="nice")
    new.scores["CV"] = "0.9"
    new.body["변경점"] = "lr"
    record, changed = experiment.edit(store, "lgbm", base, new)
    assert changed == ["verdict", "scores.CV", "body.변경점"]
    assert experiment.load_record(store.view(), "lgbm", base.id) == record
    assert record.verdict == "nice"


def test_edit_without_changes(repo: Repo, store: Store, ws, lgbm) -> None:
    base = start(repo, store, ws)
    head = store.view().commit
    assert experiment.edit(store, "lgbm", base, edited_copy(base)) == (base, [])
    assert store.view().commit == head


def test_edit_keeps_concurrent_changes(repo: Repo, store: Store, ws, lgbm) -> None:
    base = start(repo, store, ws)
    # Someone else sets the public LB while this user edits the verdict.
    other = edited_copy(base)
    other.scores["public LB"] = "0.8"
    experiment.edit(store, "lgbm", base, other)
    record, _ = experiment.edit(store, "lgbm", base, edited_copy(base, verdict="v"))
    assert record.scores["public LB"] == "0.8"
    assert record.verdict == "v"


def test_edit_conflict(repo: Repo, store: Store, ws, lgbm) -> None:
    base = start(repo, store, ws)
    experiment.edit(store, "lgbm", base, edited_copy(base, verdict="theirs"))
    with pytest.raises(experiment.EditConflictError, match="verdict"):
        experiment.edit(store, "lgbm", base, edited_copy(base, verdict="mine"))
    # The same change on both sides is not a conflict.
    experiment.edit(store, "lgbm", base, edited_copy(base, verdict="theirs"))


def test_edit_removes_scores(repo: Repo, store: Store, ws, lgbm) -> None:
    base = start(repo, store, ws)
    new = edited_copy(base)
    del new.scores["CV std"]
    record, changed = experiment.edit(store, "lgbm", base, new)
    assert changed == ["scores.CV std"]
    assert list(record.scores) == ["CV", "public LB"]


def test_edit_validates_parents(repo: Repo, store: Store, ws, lgbm) -> None:
    first = start(repo, store, ws)
    finish(repo, store, ws)
    second = start(repo, store, ws, parents=["bjs-1"])
    with pytest.raises(experiment.InvalidParentError, match="own parent"):
        experiment.edit(store, "lgbm", second, edited_copy(second, parents=["bjs-2"]))
    with pytest.raises(ParentNotFoundError):
        experiment.edit(store, "lgbm", second, edited_copy(second, parents=["bjs-9"]))
    first = experiment.load_record(store.view(), "lgbm", "bjs-1")
    with pytest.raises(experiment.InvalidParentError, match="cycle"):
        experiment.edit(store, "lgbm", first, edited_copy(first, parents=["bjs-2"]))
    with pytest.raises(InvalidTitleError):
        experiment.edit(store, "lgbm", second, edited_copy(second, title=" "))

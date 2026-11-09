"""Recording procedures: start, finish, abandon and register experiments."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from melog.git import Repo
from melog.ids import id_sort_key, next_id, parse_id
from melog.project import Project, load_project
from melog.record import (
    MOTIVATION,
    SECTIONS,
    Origin,
    Record,
    RecordError,
    dump_record,
    find_cycle,
    now,
    parse_record,
    record_path,
    records_dir,
)
from melog.snapshot import (
    SNAPSHOT_REF_PREFIX,
    checkout_worktree,
    keep,
    snapshot_ref,
    take_snapshot,
)
from melog.store import Store, StoreView
from melog.workspace import Active, Workspace


class ExperimentError(Exception):
    """Base class for errors in recording procedures."""


class ExperimentNotFoundError(ExperimentError):
    def __init__(self, project: str, exp_id: str) -> None:
        super().__init__(f"experiment {exp_id!r} does not exist in project {project!r}")


class ParentNotFoundError(ExperimentError):
    def __init__(self, project: str, parents: list[str]) -> None:
        super().__init__(
            f"parent experiments not found in project {project!r}: {', '.join(parents)}"
        )


class InvalidTitleError(ExperimentError):
    def __init__(self) -> None:
        super().__init__("the title must not be empty")


class WorkspaceBusyError(ExperimentError):
    def __init__(self, active: list[Active]) -> None:
        self.active = active
        listed = ", ".join(f"{a.id} ({a.project})" for a in active)
        super().__init__(
            f"this workspace already has an experiment in progress: {listed}. "
            "Finish it with 'melog done' or 'melog abandon' first; an abandoned "
            "experiment can still be the parent of a new one. To run experiments "
            "in parallel, use another workspace such as a git worktree"
        )


class NothingActiveError(ExperimentError):
    def __init__(self) -> None:
        super().__init__(
            "no experiment is in progress in this workspace; pass an id, or record "
            "an experiment you never started with 'melog done --new TITLE'"
        )


class AmbiguousActiveError(ExperimentError):
    def __init__(self, ids: list[str]) -> None:
        self.ids = ids
        super().__init__(
            f"several experiments are in progress in this workspace "
            f"({', '.join(ids)}); pass the id of the one to finish"
        )


class NotInProgressError(ExperimentError):
    def __init__(self, exp_id: str, status: str) -> None:
        super().__init__(f"experiment {exp_id!r} is not in progress (status: {status})")


class NotActiveHereError(ExperimentError):
    def __init__(self, exp_id: str) -> None:
        super().__init__(
            f"experiment {exp_id!r} was started in another workspace; finish it "
            "there, because the end snapshot captures the code of the workspace you "
            "run the command in. Pass --force to finish it here anyway"
        )


class UnknownRevisionError(ExperimentError):
    def __init__(self, rev: str) -> None:
        super().__init__(f"{rev!r} is not a commit in this repository")


@dataclass
class Outcome:
    record: Record
    large_files: list[tuple[str, int]] = field(default_factory=list)


# Reading


def load_record(view: StoreView, project: str, exp_id: str) -> Record:
    text = view.read(record_path(project, exp_id))
    if text is None:
        raise ExperimentNotFoundError(project, exp_id)
    return parse_record(text)


def list_records(view: StoreView, project: str) -> list[Record]:
    """All records of ``project``, oldest first."""
    records = []
    for name in view.list_dir(records_dir(project)):
        if not name.endswith(".md"):
            continue
        try:
            records.append(parse_record(view.read(f"{records_dir(project)}/{name}")))
        except RecordError:
            continue
    return sorted(records, key=_chronological_key)


def _chronological_key(record: Record) -> tuple[float, tuple[str, int, str]]:
    # Compare instants, not strings: times may carry different UTC offsets.
    time = record.started_at or record.ended_at
    instant = datetime.fromisoformat(time).timestamp() if time else 0.0
    return (instant, id_sort_key(record.id))


def active_here(view: StoreView, workspace: Workspace) -> list[Active]:
    """Experiments started in this workspace that are still in progress."""
    result = []
    for entry in workspace.active():
        text = view.read(record_path(entry.project, entry.id))
        if text is None:
            continue
        try:
            if parse_record(text).status == "in_progress":
                result.append(entry)
        except RecordError:
            continue
    return result


# Checks


def _check_title(title: str) -> str:
    title = title.strip()
    if not title:
        raise InvalidTitleError()
    return title


def _check_parents(view: StoreView, project: str, parents: Iterable[str]) -> list[str]:
    parents = list(dict.fromkeys(parents))
    missing = [p for p in parents if view.read(record_path(project, p)) is None]
    if missing:
        raise ParentNotFoundError(project, missing)
    return parents


def _check_workspace(view: StoreView, workspace: Workspace, project: Project) -> None:
    """Allow several experiments at once only if all of them are horizontal."""
    active = active_here(view, workspace)
    if not active:
        return
    all_horizontal = project.horizontal and all(
        load_project(view, a.project).horizontal for a in active
    )
    if not all_horizontal:
        raise WorkspaceBusyError(active)


def _used_ids(repo: Repo, view: StoreView, project: str) -> list[str]:
    ids = [
        name.removesuffix(".md")
        for name in view.list_dir(records_dir(project))
        if name.endswith(".md")
    ]
    # Snapshot refs count too, in case a record was lost after its snapshot was kept.
    prefix = f"{SNAPSHOT_REF_PREFIX}/{project}/"
    ids += [ref.removeprefix(prefix).split("/")[0] for ref in repo.refs(prefix)]
    return [i for i in ids if parse_id(i) is not None]


def _new_record(
    project: Project,
    exp_id: str,
    title: str,
    parents: list[str],
    motivation: str | None,
    entry_points: Iterable[str],
) -> Record:
    record = Record(
        id=exp_id,
        title=title,
        parents=parents,
        entry_points=[p for p in entry_points if p.strip()],
        scores=dict.fromkeys(project.scores),
    )
    if motivation and motivation.strip():
        record.body[MOTIVATION] = motivation.strip()
    return record


def _apply_result(
    record: Record, scores: Mapping[str, str], verdict: str | None
) -> None:
    record.set_scores(scores)
    if verdict is not None and verdict.strip():
        record.verdict = verdict.strip()


# Procedures


def start(
    repo: Repo,
    store: Store,
    workspace: Workspace,
    project_name: str,
    initials: str,
    title: str,
    parents: Iterable[str] = (),
    motivation: str | None = None,
    entry_points: Iterable[str] = (),
) -> Outcome:
    title = _check_title(title)
    view = store.view()
    project = load_project(view, project_name)
    parent_ids = check_new(view, workspace, project_name, parents)

    snapshot = take_snapshot(repo, f"melog snapshot: start of {title}")
    created: list[Record] = []

    def build(view: StoreView):
        exp_id = next_id(initials, _used_ids(repo, view, project_name))
        record = _new_record(
            project, exp_id, title, parent_ids, motivation, entry_points
        )
        record.started_at = now()
        record.start_snapshot = snapshot.commit
        created[:] = [record]
        changes = {record_path(project_name, exp_id): dump_record(record)}
        return changes, f"start {project_name}/{exp_id}"

    store.update(build)
    record = created[0]
    keep(repo, snapshot_ref(project_name, record.id, "start"), snapshot.commit)
    workspace.add(project_name, record.id)
    return Outcome(record, snapshot.large_files)


def resolve_target(
    view: StoreView, workspace: Workspace, project: str, exp_id: str | None
) -> str:
    """The experiment that 'done' or 'abandon' acts on when the id may be omitted."""
    if exp_id is not None:
        return exp_id
    ids = [a.id for a in active_here(view, workspace) if a.project == project]
    if not ids:
        raise NothingActiveError()
    if len(ids) > 1:
        raise AmbiguousActiveError(ids)
    return ids[0]


def check_new(
    view: StoreView, workspace: Workspace, project_name: str, parents: Iterable[str]
) -> list[str]:
    """Check that a new experiment may be recorded here; return its parents."""
    project = load_project(view, project_name)
    parent_ids = _check_parents(view, project_name, parents)
    _check_workspace(view, workspace, project)
    return parent_ids


def prepare_finish(
    view: StoreView,
    workspace: Workspace,
    project_name: str,
    exp_id: str | None,
    force: bool = False,
) -> Record:
    """Find the experiment to finish and check that it may be finished here."""
    exp_id = resolve_target(view, workspace, project_name, exp_id)
    record = load_record(view, project_name, exp_id)
    if record.status != "in_progress":
        raise NotInProgressError(exp_id, record.status)
    if not force and Active(project_name, exp_id) not in workspace.active():
        raise NotActiveHereError(exp_id)
    return record


def finish(
    repo: Repo,
    store: Store,
    workspace: Workspace,
    project_name: str,
    exp_id: str | None,
    status: str,
    scores: Mapping[str, str] | None = None,
    verdict: str | None = None,
    force: bool = False,
) -> Outcome:
    """Mark an experiment in progress as done or abandoned."""
    record = prepare_finish(store.view(), workspace, project_name, exp_id, force)
    exp_id = record.id
    # Validate the scores before taking a snapshot, so a typo costs nothing.
    Record(id=exp_id, title=record.title).set_scores(scores or {})

    snapshot = take_snapshot(repo, f"melog snapshot: end of {record.title}")
    finished: list[Record] = []

    def build(view: StoreView):
        current = load_record(view, project_name, exp_id)
        if current.status != "in_progress":
            raise NotInProgressError(exp_id, current.status)
        current.status = status
        current.ended_at = now()
        current.end_snapshot = snapshot.commit
        _apply_result(current, scores or {}, verdict)
        finished[:] = [current]
        changes = {record_path(project_name, exp_id): dump_record(current)}
        return changes, f"{status} {project_name}/{exp_id}"

    store.update(build)
    keep(repo, snapshot_ref(project_name, exp_id, "end"), snapshot.commit)
    workspace.remove(project_name, exp_id)
    return Outcome(finished[0], snapshot.large_files)


def register(
    repo: Repo,
    store: Store,
    workspace: Workspace,
    project_name: str,
    initials: str,
    title: str,
    parents: Iterable[str] = (),
    motivation: str | None = None,
    entry_points: Iterable[str] = (),
    since: str | None = None,
    scores: Mapping[str, str] | None = None,
    verdict: str | None = None,
) -> Outcome:
    """Record a finished experiment whose start was never declared."""
    title = _check_title(title)
    view = store.view()
    project = load_project(view, project_name)
    parent_ids = check_new(view, workspace, project_name, parents)
    Record(id="check", title=title).set_scores(scores or {})

    start_commit = start_time = None
    if since is not None:
        start_commit = repo.resolve(f"{since}^{{commit}}")
        if start_commit is None:
            raise UnknownRevisionError(since)
        start_time = repo.commit_time(start_commit)

    snapshot = take_snapshot(repo, f"melog snapshot: end of {title}")
    created: list[Record] = []

    def build(view: StoreView):
        exp_id = next_id(initials, _used_ids(repo, view, project_name))
        record = _new_record(
            project, exp_id, title, parent_ids, motivation, entry_points
        )
        record.status = "done"
        record.started_at = start_time
        record.start_snapshot = start_commit
        record.ended_at = now()
        record.end_snapshot = snapshot.commit
        _apply_result(record, scores or {}, verdict)
        created[:] = [record]
        changes = {record_path(project_name, exp_id): dump_record(record)}
        return changes, f"register {project_name}/{exp_id}"

    store.update(build)
    record = created[0]
    if start_commit is not None:
        keep(repo, snapshot_ref(project_name, record.id, "start"), start_commit)
    keep(repo, snapshot_ref(project_name, record.id, "end"), snapshot.commit)
    return Outcome(record, snapshot.large_files)


# Editing


class EditConflictError(ExperimentError):
    def __init__(self, exp_id: str, fields: list[str]) -> None:
        self.fields = fields
        super().__init__(
            f"{exp_id!r} was changed elsewhere while you were editing it, in the same "
            f"places you changed: {', '.join(fields)}"
        )


class InvalidParentError(ExperimentError):
    """A parent would point at the experiment itself or close a cycle."""


def _fields(record: Record) -> dict[str, object]:
    """The user-editable parts of a record, each compared on its own when merging."""
    fields: dict[str, object] = {
        "title": record.title,
        "parents": tuple(record.parents),
        "entry_points": tuple(record.entry_points),
        "verdict": record.verdict,
    }
    for name, value in record.scores.items():
        fields[f"scores.{name}"] = value
    for name in SECTIONS:
        fields[f"body.{name}"] = record.section(name)
    return fields


_MISSING = object()


def _set_field(record: Record, key: str, value: object) -> None:
    if key.startswith("scores."):
        name = key.removeprefix("scores.")
        if value is _MISSING:
            record.scores.pop(name, None)
        else:
            record.scores[name] = value  # type: ignore[assignment]
    elif key.startswith("body."):
        record.body[key.removeprefix("body.")] = value  # type: ignore[assignment]
    elif key in ("parents", "entry_points"):
        setattr(record, key, list(value))  # type: ignore[arg-type]
    else:
        setattr(record, key, value)


def _check_edited(view: StoreView, project: str, record: Record) -> None:
    _check_title(record.title)
    if record.id in record.parents:
        raise InvalidParentError(f"{record.id!r} cannot be its own parent")
    _check_parents(view, project, record.parents)
    parents_of = {r.id: r.parents for r in list_records(view, project)}
    parents_of[record.id] = record.parents
    cycle = find_cycle(parents_of)
    if cycle:
        raise InvalidParentError(
            f"these parents would make a cycle: {' -> '.join(cycle)}"
        )


def edit(
    store: Store, project: str, base: Record, edited: Record
) -> tuple[Record, list[str]]:
    """Apply the user's changes from ``base`` to ``edited`` onto the stored record.

    Only the fields the user changed are applied, so concurrent changes to other
    fields are kept. Returns the stored record and the changed field names.
    """
    before, after = _fields(base), _fields(edited)
    changed = [
        key
        for key in dict.fromkeys([*before, *after])
        if before.get(key, _MISSING) != after.get(key, _MISSING)
    ]
    if not changed:
        return base, []
    _check_edited(store.view(), project, edited)
    result: list[Record] = []

    def build(view: StoreView):
        current = load_record(view, project, base.id)
        now_fields = _fields(current)
        conflicts = [
            key
            for key in changed
            if now_fields.get(key, _MISSING) != before.get(key, _MISSING)
            and now_fields.get(key, _MISSING) != after.get(key, _MISSING)
        ]
        if conflicts:
            raise EditConflictError(base.id, conflicts)
        for key in changed:
            _set_field(current, key, after.get(key, _MISSING))
        _check_edited(view, project, current)
        result[:] = [current]
        changes = {record_path(project, base.id): dump_record(current)}
        return changes, f"edit {project}/{base.id}"

    store.update(build)
    return result[0], changed


# Checking out code


class NoSnapshotError(ExperimentError):
    """The experiment has no snapshot of the requested kind."""


class CheckoutPathExistsError(ExperimentError):
    def __init__(self, path: Path) -> None:
        super().__init__(f"{path} already exists; choose another path")


def checkout(
    repo: Repo,
    view: StoreView,
    project: str,
    exp_id: str,
    path: Path | None = None,
    start: bool = False,
) -> tuple[Path, str]:
    """Check out the code of an experiment into a new worktree.

    Returns the path and the snapshot commit.
    """
    record = load_record(view, project, exp_id)
    commit = record.start_snapshot if start else record.end_snapshot
    if commit is None:
        if not start and record.status == "in_progress":
            raise NoSnapshotError(
                f"{exp_id!r} is still in progress and has no end snapshot; pass "
                "--start to check out the code it started from"
            )
        kind = "start" if start else "end"
        raise NoSnapshotError(f"{exp_id!r} has no {kind} snapshot")
    if path is None:
        path = repo.root.parent / f"{repo.root.name}-{exp_id}"
    path = path.resolve()
    if path.exists():
        raise CheckoutPathExistsError(path)
    checkout_worktree(repo, commit, path)
    return path, commit


# Importing


class ImportRefusedError(ExperimentError):
    """The experiment cannot be imported into this project."""


def _find_import(
    view: StoreView, project: str, source_project: str, source_id: str
) -> Record | None:
    for record in list_records(view, project):
        origin = record.origin
        if origin and (origin.project, origin.id) == (source_project, source_id):
            return record
    return None


def prepare_import(
    view: StoreView, project: str, source_project: str, source_id: str
) -> Record:
    """Check that an experiment may be imported and return the original."""
    if source_project == project:
        raise ImportRefusedError(
            f"{source_id!r} is already in project {project!r}; declare it as a "
            f"parent with --parent {source_id} instead"
        )
    load_project(view, project)
    load_project(view, source_project)
    original = load_record(view, source_project, source_id)
    if original.end_snapshot is None:
        raise ImportRefusedError(
            f"{source_project}/{source_id} is still in progress and has no end "
            "snapshot to import"
        )
    existing = _find_import(view, project, source_project, source_id)
    if existing is not None:
        raise ImportRefusedError(
            f"{source_project}/{source_id} was already imported as "
            f"{existing.id}; use it as a parent with --parent {existing.id}"
        )
    return original


def import_experiment(
    repo: Repo,
    store: Store,
    project_name: str,
    initials: str,
    source_project: str,
    source_id: str,
    title: str | None = None,
    motivation: str | None = None,
) -> Outcome:
    """Record an experiment of another project as a new one in this project."""
    original = prepare_import(store.view(), project_name, source_project, source_id)
    title = _check_title(title if title is not None else original.title)
    created: list[Record] = []

    def build(view: StoreView):
        prepare_import(view, project_name, source_project, source_id)
        project = load_project(view, project_name)
        exp_id = next_id(initials, _used_ids(repo, view, project_name))
        record = _new_record(
            project, exp_id, title, [], motivation, original.entry_points
        )
        record.status = "done"
        record.ended_at = now()
        record.end_snapshot = original.end_snapshot
        record.origin = Origin(source_project, source_id, original.end_snapshot)
        created[:] = [record]
        changes = {record_path(project_name, exp_id): dump_record(record)}
        return (
            changes,
            f"import {source_project}/{source_id} as {project_name}/{exp_id}",
        )

    store.update(build)
    record = created[0]
    keep(repo, snapshot_ref(project_name, record.id, "end"), original.end_snapshot)
    return Outcome(record)

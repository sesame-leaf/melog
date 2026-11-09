"""Sharing experiment records and code snapshots through a git remote.

The store is merged like a git branch: a three-way merge between the local store,
the remote store and their merge base. Records are merged field by field, and
long texts (body sections and overviews) line by line.
"""

from __future__ import annotations

import copy
import re
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from melog.git import GitError, Repo
from melog.ids import next_id, parse_id
from melog.project import Project, ProjectError, dump_settings, parse_settings
from melog.record import (
    MISSING,
    RecordError,
    dump_record,
    parse_record,
    record_fields,
    set_record_field,
)
from melog.snapshot import SNAPSHOT_REF_PREFIX
from melog.store import STORE_REF, ConcurrentUpdateError, Store
from melog.workspace import rename_everywhere

TRACKING_PREFIX = "refs/melog/remotes"
SHARED_KEY = "melog.sharedremote"
MAX_ATTEMPTS = 3

RECORD_PATH = re.compile(r"([^/]+)/records/([^/]+)\.md")
SETTINGS_PATH = re.compile(r"([^/]+)/project\.yaml")
OVERVIEW_PATH = re.compile(r"([^/]+)/overview\.md")


class SyncError(Exception):
    """Synchronizing with a remote failed."""


class NoRemoteError(SyncError):
    def __init__(self, remote: str, remotes: list[str]) -> None:
        known = ", ".join(remotes) if remotes else "none"
        super().__init__(
            f"there is no git remote named {remote!r} (remotes: {known}); "
            "choose one with --remote"
        )


class SyncCancelledError(SyncError):
    def __init__(self) -> None:
        super().__init__("sync cancelled; nothing was uploaded")


class NotConfirmedError(SyncError):
    def __init__(self, remote: str) -> None:
        super().__init__(
            f"this is the first sync with {remote!r} and it uploads code snapshots, "
            "which include files git does not ignore even if they are not committed; "
            "run it in a terminal to review them, or pass --yes"
        )


@dataclass(frozen=True)
class Conflict:
    where: str
    """What conflicts, such as 'lgbm/bjs-4' or 'lgbm overview'."""
    field: str
    local: object
    remote: object


class SyncConflictError(SyncError):
    def __init__(self, conflicts: list[Conflict]) -> None:
        self.conflicts = conflicts
        listed = "; ".join(f"{c.where} {c.field}" for c in conflicts)
        super().__init__(
            f"local and remote changed the same things differently: {listed}. "
            "Nothing was changed. Run it in a terminal to choose, or pass "
            "--prefer local or --prefer remote"
        )


Resolver = Callable[[Conflict], str]
"""Chooses 'local' or 'remote' for a conflict."""

Confirm = Callable[[int, list[str]], bool]
"""Approves the first upload, given the number of snapshots and the new files."""


@dataclass
class SyncReport:
    remote: str
    received: list[str] = field(default_factory=list)
    """Experiments that came from the remote."""
    updated: list[str] = field(default_factory=list)
    """Experiments that the remote changed."""
    sent: list[str] = field(default_factory=list)
    """Experiments that the remote did not have."""
    sent_changes: list[str] = field(default_factory=list)
    """Experiments the remote had, with local changes it did not have."""
    resolved: list[tuple[str, str, str]] = field(default_factory=list)
    """Conflicts as (where, field, the side that was kept)."""
    renamed: list[tuple[str, str, str]] = field(default_factory=list)
    """Local experiments renamed as (project, old id, new id)."""

    def to_dict(self) -> dict[str, object]:
        return {
            "remote": self.remote,
            "received": self.received,
            "updated": self.updated,
            "sent": self.sent,
            "sent_changes": self.sent_changes,
            "resolved": [
                {"where": w, "field": f, "kept": side} for w, f, side in self.resolved
            ],
            "renamed": [
                {"project": p, "old_id": old, "new_id": new}
                for p, old, new in self.renamed
            ],
        }


# Git helpers


def tracking_ref(remote: str, name: str) -> str:
    return f"{TRACKING_PREFIX}/{remote}/{name}"


def _remote_refs(repo: Repo, remote: str) -> dict[str, str]:
    """The melog refs on the remote, mapped to the commits they point at."""
    refs = {}
    for line in repo.run("ls-remote", remote, "refs/melog/*").splitlines():
        oid, _, name = line.partition("\t")
        if name == STORE_REF or name.startswith(f"{SNAPSHOT_REF_PREFIX}/"):
            refs[name] = oid
    return refs


def _local_refs(repo: Repo, prefix: str) -> dict[str, str]:
    out = repo.run("for-each-ref", "--format=%(objectname) %(refname)", prefix)
    refs = {}
    for line in out.splitlines():
        oid, _, name = line.partition(" ")
        refs[name] = oid
    return refs


def _fetch(repo: Repo, remote: str, refs: dict[str, str]) -> None:
    specs = []
    if STORE_REF in refs:
        specs.append(f"+{STORE_REF}:{tracking_ref(remote, 'store')}")
    if any(name.startswith(f"{SNAPSHOT_REF_PREFIX}/") for name in refs):
        specs.append(f"+{SNAPSHOT_REF_PREFIX}/*:{tracking_ref(remote, 'snapshots')}/*")
    if specs:
        repo.run("fetch", "--quiet", "--no-tags", remote, *specs)


def _is_ancestor(repo: Repo, ancestor: str, descendant: str) -> bool:
    out = repo.try_run("merge-base", "--is-ancestor", ancestor, descendant)
    return out is not None


def _tree(repo: Repo, commit: str | None) -> dict[str, str]:
    """Paths in a commit mapped to their blob ids."""
    if commit is None:
        return {}
    entries = {}
    for entry in repo.run("ls-tree", "-r", "-z", commit).split("\0"):
        if not entry:
            continue
        info, _, path = entry.partition("\t")
        entries[path] = info.split()[2]
    return entries


def _blob(repo: Repo, oid: str | None) -> str | None:
    return None if oid is None else repo.run("cat-file", "blob", oid)


def _hash(repo: Repo, text: str | None) -> str | None:
    return (
        None if text is None else repo.run("hash-object", "--stdin", input=text).strip()
    )


def merge_text(repo: Repo, base: str, local: str, remote: str) -> str | None:
    """Merge two edits of a text line by line, or return None if they overlap."""
    with tempfile.TemporaryDirectory(prefix="melog-") as tmp:
        paths = []
        for name, text in (("local", local), ("base", base), ("remote", remote)):
            path = Path(tmp) / name
            path.write_text(text if text.endswith("\n") or not text else text + "\n")
            paths.append(str(path))
        # The exit code is the number of conflicts, so try_run returns None for any.
        return repo.try_run("merge-file", "-p", "-q", *paths)


# The merge


class _Merger:
    """A three-way merge of two store commits, collecting what it did."""

    def __init__(
        self,
        repo: Repo,
        report: SyncReport,
        prefer: str | None,
        resolve: Resolver | None,
    ) -> None:
        self.repo = repo
        self.report = report
        self.prefer = prefer
        self.resolve = resolve
        self.unresolved: list[Conflict] = []

    def choose(self, conflict: Conflict) -> object:
        side = self.prefer
        if side is None and self.resolve is not None:
            side = self.resolve(conflict)
        if side not in ("local", "remote"):
            self.unresolved.append(conflict)
            return conflict.local
        self.report.resolved.append((conflict.where, conflict.field, side))
        return conflict.local if side == "local" else conflict.remote

    def merge_value(
        self, where: str, key: str, base: object, local: object, remote: object
    ) -> object:
        if local == remote:
            return local
        if local == base:
            return remote
        if remote == base:
            return local
        if all(isinstance(v, str) for v in (base, local, remote)):
            merged = merge_text(self.repo, base, local, remote)  # type: ignore[arg-type]
            if merged is not None:
                return merged.strip("\n")
        return self.choose(Conflict(where, key, local, remote))

    def merge_record(
        self, where: str, base: str | None, local: str, remote: str
    ) -> str:
        try:
            base_record = parse_record(base) if base is not None else None
            local_record = parse_record(local)
            remote_record = parse_record(remote)
        except RecordError:
            return self.merge_file(where, base, local, remote)
        before = record_fields(base_record, facts=True) if base_record else {}
        mine = record_fields(local_record, facts=True)
        theirs = record_fields(remote_record, facts=True)
        merged = copy.deepcopy(local_record)
        for key in dict.fromkeys([*mine, *theirs]):
            value = self.merge_value(
                where,
                key,
                before.get(key, MISSING),
                mine.get(key, MISSING),
                theirs.get(key, MISSING),
            )
            set_record_field(merged, key, value)
        return dump_record(merged)

    def merge_settings(
        self, where: str, name: str, base: str | None, local: str, remote: str
    ) -> str:
        try:
            sides = [
                parse_settings(name, t) if t else None for t in (base, local, remote)
            ]
        except ProjectError:
            return self.merge_file(where, base, local, remote)
        scores = [tuple(p.scores) if p else None for p in sides]
        horizontal = [p.horizontal if p else None for p in sides]
        merged = Project(
            name=name,
            scores=list(self.merge_value(where, "scores", *scores)),  # type: ignore[arg-type]
            horizontal=bool(self.merge_value(where, "horizontal", *horizontal)),
        )
        return dump_settings(merged)

    def merge_file(
        self, where: str, base: str | None, local: str | None, remote: str | None
    ) -> str | None:
        return self.merge_value(where, "contents", base, local, remote)  # type: ignore[return-value]


def _label(path: str) -> str:
    if match := RECORD_PATH.fullmatch(path):
        return f"{match.group(1)}/{match.group(2)}"
    if match := OVERVIEW_PATH.fullmatch(path):
        return f"{match.group(1)} overview"
    if match := SETTINGS_PATH.fullmatch(path):
        return f"{match.group(1)} settings"
    return path


def _plan_renames(
    repo: Repo, remote: str, local: dict[str, str], theirs: dict[str, str], base: dict
) -> dict[tuple[str, str], str]:
    """Pick new ids for local experiments whose id the remote also created."""
    renames: dict[tuple[str, str], str] = {}
    used: dict[str, set[str]] = {}
    for path in [*local, *theirs]:
        if match := RECORD_PATH.fullmatch(path):
            used.setdefault(match.group(1), set()).add(match.group(2))
    snapshot_refs = [
        *_local_refs(repo, f"{SNAPSHOT_REF_PREFIX}/"),
        *_local_refs(repo, f"{tracking_ref(remote, 'snapshots')}/"),
    ]
    for ref in snapshot_refs:
        parts = ref.split("/")
        if len(parts) >= 3:
            used.setdefault(parts[-3], set()).add(parts[-2])

    for path in sorted(local):
        match = RECORD_PATH.fullmatch(path)
        if not match or path in base or path not in theirs:
            continue
        if local[path] == theirs[path]:
            continue
        project, old = match.groups()
        parsed = parse_id(old)
        if parsed is None:
            continue
        new = next_id(parsed[0], used[project])
        used[project].add(new)
        renames[(project, old)] = new
    return renames


def _apply_renames(
    repo: Repo,
    local: dict[str, str],
    base: dict[str, str],
    renames: dict[tuple[str, str], str],
) -> dict[str, str | None]:
    """Rewrite the local side of the store for the renames.

    Only records that differ from the merge base can mention an experiment that
    was never shared, so only those are rewritten.
    """
    overrides: dict[str, str | None] = {}
    for path, oid in local.items():
        match = RECORD_PATH.fullmatch(path)
        if not match or base.get(path) == oid:
            continue
        project, exp_id = match.groups()
        record = parse_record(_blob(repo, oid))  # type: ignore[arg-type]
        new_id = renames.get((project, exp_id))
        parents = [renames.get((project, p), p) for p in record.parents]
        if new_id is None and parents == record.parents:
            continue
        record.parents = parents
        if new_id is not None:
            overrides[path] = None
            record.id = new_id
            path = f"{project}/records/{new_id}.md"
        overrides[path] = dump_record(record)
    return overrides


def _merge_stores(
    repo: Repo,
    store: Store,
    remote: str,
    remote_store: str | None,
    report: SyncReport,
    prefer: str | None,
    resolve: Resolver | None,
) -> dict[tuple[str, str], str]:
    """Merge the remote store into the local one. Returns the renames made."""
    view = store.view()
    local_commit = view.commit
    if remote_store is None:
        return {}
    if local_commit is None or _is_ancestor(repo, local_commit, remote_store):
        old = _tree(repo, local_commit)
        new = _tree(repo, remote_store)
        report.received = _records(new, exclude=old)
        report.updated = [_label(p) for p in _changed_records(old, new) if p in old]
        try:
            repo.run("update-ref", STORE_REF, remote_store, local_commit or "")
        except GitError as e:
            raise ConcurrentUpdateError(STORE_REF) from e
        return {}
    if _is_ancestor(repo, remote_store, local_commit):
        return {}

    base_commit = repo.try_run("merge-base", local_commit, remote_store)
    base_commit = base_commit.strip() if base_commit else None
    base = _tree(repo, base_commit)
    local = _tree(repo, local_commit)
    theirs = _tree(repo, remote_store)

    renames = _plan_renames(repo, remote, local, theirs, base)
    overrides = _apply_renames(repo, local, base, renames)
    mine = {p: o for p, o in local.items() if p not in overrides}
    mine.update({p: _hash(repo, t) for p, t in overrides.items() if t is not None})

    merger = _Merger(repo, report, prefer, resolve)
    changes: dict[str, str | None] = {}
    for path in sorted(set(mine) | set(theirs) | set(base)):
        b, m, t = base.get(path), mine.get(path), theirs.get(path)
        if m == t:
            if path in overrides:
                changes[path] = overrides[path]
            continue
        if m == b:
            changes[path] = _blob(repo, t)
            if RECORD_PATH.fullmatch(path):
                (report.updated if path in mine else report.received).append(
                    _label(path)
                )
            continue
        local_text = overrides[path] if path in overrides else _blob(repo, m)
        if t == b:
            if path in overrides:
                changes[path] = local_text
            continue
        changes[path] = _merge_path(
            merger, path, _blob(repo, b), local_text, _blob(repo, t)
        )
        if RECORD_PATH.fullmatch(path):
            report.updated.append(_label(path))
    if merger.unresolved:
        raise SyncConflictError(merger.unresolved)

    store.commit(changes, f"sync with {remote}", base=view, merge_with=remote_store)
    return renames


def _merge_path(
    merger: _Merger, path: str, base: str | None, local: str | None, remote: str | None
) -> str | None:
    where = _label(path)
    if local is not None and remote is not None:
        if RECORD_PATH.fullmatch(path):
            return merger.merge_record(where, base, local, remote)
        if match := SETTINGS_PATH.fullmatch(path):
            return merger.merge_settings(where, match.group(1), base, local, remote)
    return merger.merge_file(where, base, local, remote)


def _records(tree: dict[str, str], exclude: Iterable[str] = ()) -> list[str]:
    skip = set(exclude)
    return [
        _label(p) for p in sorted(tree) if RECORD_PATH.fullmatch(p) and p not in skip
    ]


def _changed_records(old: dict[str, str], new: dict[str, str]) -> list[str]:
    return [p for p in sorted(new) if RECORD_PATH.fullmatch(p) and old.get(p) != new[p]]


# Snapshot refs


def _rename_snapshot_refs(repo: Repo, renames: dict[tuple[str, str], str]) -> None:
    for (project, old), new in renames.items():
        for kind in ("start", "end"):
            old_ref = f"{SNAPSHOT_REF_PREFIX}/{project}/{old}/{kind}"
            oid = repo.resolve(old_ref)
            if oid is None:
                continue
            repo.run("update-ref", f"{SNAPSHOT_REF_PREFIX}/{project}/{new}/{kind}", oid)
            repo.run("update-ref", "-d", old_ref, oid)
        rename_everywhere(repo, project, old, new)


def _adopt_remote_snapshots(repo: Repo, remote: str) -> None:
    """Keep the snapshots the remote has, so that their code stays available."""
    prefix = f"{tracking_ref(remote, 'snapshots')}/"
    local = _local_refs(repo, f"{SNAPSHOT_REF_PREFIX}/")
    for name, oid in _local_refs(repo, prefix).items():
        local_name = f"{SNAPSHOT_REF_PREFIX}/{name.removeprefix(prefix)}"
        if local_name not in local:
            repo.run("update-ref", local_name, oid, "")


def _snapshots_to_send(repo: Repo, remote_refs: dict[str, str]) -> dict[str, str]:
    local = _local_refs(repo, f"{SNAPSHOT_REF_PREFIX}/")
    return {name: oid for name, oid in local.items() if remote_refs.get(name) != oid}


def added_files(repo: Repo, commits: Iterable[str]) -> list[str]:
    """Files that snapshots add to the commit they started from, such as files
    that were never committed."""
    files: set[str] = set()
    for commit in set(commits):
        out = repo.run(
            "diff-tree",
            "-r",
            "--root",
            "--no-commit-id",
            "--name-only",
            "--diff-filter=A",
            "-z",
            commit,
        )
        files.update(name for name in out.split("\0") if name)
    return sorted(files)


def _confirmed(repo: Repo, remote: str) -> bool:
    out = repo.try_run("config", "--get-all", SHARED_KEY)
    return out is not None and remote in out.split()


# Entry point


def sync(
    repo: Repo,
    remote: str = "origin",
    prefer: str | None = None,
    resolve: Resolver | None = None,
    confirm: Confirm | None = None,
) -> SyncReport:
    """Fetch, merge and push the melog store and code snapshots."""
    remotes = repo.run("remote").split()
    if remote not in remotes:
        raise NoRemoteError(remote, remotes)
    store = Store(repo)

    for _ in range(MAX_ATTEMPTS):
        remote_refs = _remote_refs(repo, remote)
        if not _confirmed(repo, remote):
            pending = _snapshots_to_send(repo, remote_refs)
            if pending:
                files = added_files(repo, pending.values())
                if confirm is None:
                    raise NotConfirmedError(remote)
                if not confirm(len(pending), files):
                    raise SyncCancelledError()

        _fetch(repo, remote, remote_refs)
        remote_store = (
            repo.resolve(tracking_ref(remote, "store"))
            if STORE_REF in remote_refs
            else None
        )
        report = SyncReport(remote)
        try:
            renames = _merge_stores(
                repo, store, remote, remote_store, report, prefer, resolve
            )
        except ConcurrentUpdateError:
            continue
        report.renamed = [(p, old, new) for (p, old), new in renames.items()]
        _rename_snapshot_refs(repo, renames)
        _adopt_remote_snapshots(repo, remote)
        # What the push sends, compared with what the remote has now.
        theirs = _tree(repo, remote_store)
        ours = _tree(repo, repo.resolve(STORE_REF))
        report.sent = _records(ours, exclude=theirs)
        report.sent_changes = [
            _label(p) for p in _changed_records(theirs, ours) if p in theirs
        ]

        specs = [
            f"{name}:{name}"
            for name in [STORE_REF, *_snapshots_to_send(repo, remote_refs)]
            if repo.resolve(name) is not None
            and remote_refs.get(name) != repo.resolve(name)
        ]
        if specs:
            try:
                repo.run("push", "--atomic", "--quiet", remote, *specs)
            except GitError as e:
                if _remote_refs(repo, remote).get(STORE_REF) != remote_refs.get(
                    STORE_REF
                ):
                    continue  # someone pushed in the meantime; merge again
                raise SyncError(f"pushing to {remote!r} failed: {e.stderr}") from e
            local_store = repo.resolve(STORE_REF)
            if local_store is not None:
                repo.run("update-ref", tracking_ref(remote, "store"), local_store)
        if not _confirmed(repo, remote):
            repo.run("config", "--add", SHARED_KEY, remote)
        return report
    raise SyncError(f"{remote!r} kept changing while syncing; try again")

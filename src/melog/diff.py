"""Comparing the code of two snapshots. Notebooks are compared by their code cells."""

from __future__ import annotations

import difflib
import json
from collections.abc import Iterable
from dataclasses import dataclass

from melog.experiment import ExperimentError, UnknownRevisionError, load_record
from melog.git import Repo
from melog.ids import parse_id
from melog.record import Record, record_path
from melog.snapshot import take_snapshot
from melog.store import StoreView

NOTEBOOK_SUFFIX = ".ipynb"
STATUS_NAMES = {
    "A": "added",
    "D": "deleted",
    "M": "modified",
    "R": "renamed",
    "C": "copied",
    "T": "type changed",
}


class NoBaseError(ExperimentError):
    def __init__(self, exp_id: str) -> None:
        super().__init__(
            f"{exp_id!r} has no start snapshot to compare with; choose a base with "
            "--against, such as a parent experiment or a commit"
        )


class NoEndSnapshotError(ExperimentError):
    def __init__(self, exp_id: str) -> None:
        super().__init__(f"{exp_id!r} is still in progress and has no end snapshot")


class EntryPointsError(ExperimentError):
    """Entry points cannot be paired for a comparison."""


@dataclass(frozen=True)
class Side:
    """One side of a comparison."""

    kind: str
    """start_snapshot, end_snapshot, work_tree, experiment or revision."""
    label: str
    commit: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "label": self.label, "commit": self.commit}


@dataclass(frozen=True)
class FileDiff:
    path: str
    old_path: str
    status: str
    text: str
    additions: int
    deletions: int

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "old_path": self.old_path,
            "status": self.status,
            "additions": self.additions,
            "deletions": self.deletions,
            "diff": self.text,
        }


def resolve_sides(
    repo: Repo, view: StoreView, project: str, record: Record, against: str | None
) -> tuple[Side, Side, Record | None]:
    """Work out what to compare. Returns the base, the target and the base
    experiment if ``against`` named one."""
    if record.end_snapshot is not None:
        target = Side("end_snapshot", f"{record.id} end", record.end_snapshot)
    else:
        # In progress: compare with the code as it is now, without keeping it.
        snapshot = take_snapshot(repo, f"melog diff: work tree of {record.id}")
        target = Side("work_tree", "work tree", snapshot.commit)

    if against is None:
        if record.start_snapshot is None:
            raise NoBaseError(record.id)
        return (
            Side("start_snapshot", f"{record.id} start", record.start_snapshot),
            (target),
            None,
        )
    if parse_id(against) and view.read(record_path(project, against)) is not None:
        other = load_record(view, project, against)
        if other.end_snapshot is None:
            raise NoEndSnapshotError(against)
        return Side("experiment", f"{against} end", other.end_snapshot), target, other
    commit = repo.resolve(f"{against}^{{commit}}")
    if commit is None:
        raise UnknownRevisionError(against)
    return Side("revision", against, commit), target, None


def entry_point_pairs(base: Record | None, record: Record) -> list[tuple[str, str]]:
    if base is None:
        raise EntryPointsError(
            "--entry-points compares with another experiment; name it with --against"
        )
    if not record.entry_points or len(base.entry_points) != len(record.entry_points):
        raise EntryPointsError(
            f"cannot pair entry points: {base.id} has {len(base.entry_points)} and "
            f"{record.id} has {len(record.entry_points)}; record them with "
            "'melog edit --entry'"
        )
    return list(zip(base.entry_points, record.entry_points, strict=True))


def diff_commits(
    repo: Repo, base: str, target: str, paths: Iterable[str] = (), raw: bool = False
) -> list[FileDiff]:
    """Compare every changed file between two commits."""
    out = repo.run(
        "diff", "--name-status", "-z", "-M", base, target, "--", *paths
    ).split("\0")
    diffs = []
    i = 0
    while i < len(out) and out[i]:
        code = out[i][0]
        if code in "RC":
            old, new = out[i + 1], out[i + 2]
            i += 3
        else:
            old = new = out[i + 1]
            i += 2
        result = _diff_file(
            repo, base, target, old, new, STATUS_NAMES.get(code, code), raw
        )
        if result is not None:
            diffs.append(result)
    return diffs


def diff_pairs(
    repo: Repo, base: str, target: str, pairs: Iterable[tuple[str, str]], raw: bool
) -> list[FileDiff]:
    """Compare files at different paths, such as the notebooks of two experiments."""
    diffs = []
    for old, new in pairs:
        old_text = _blob(repo, base, old)
        new_text = _blob(repo, target, new)
        if old_text is None and new_text is None:
            continue
        status = (
            "added"
            if old_text is None
            else "deleted"
            if new_text is None
            else "modified"
        )
        result = _text_diff(old, new, old_text, new_text, status, raw)
        if result is not None:
            diffs.append(result)
    return diffs


def _blob(repo: Repo, commit: str, path: str) -> str | None:
    return repo.try_run("cat-file", "blob", f"{commit}:{path}")


def _diff_file(
    repo: Repo, base: str, target: str, old: str, new: str, status: str, raw: bool
) -> FileDiff | None:
    if not raw and new.endswith(NOTEBOOK_SUFFIX):
        old_text = None if status == "added" else _blob(repo, base, old)
        new_text = None if status == "deleted" else _blob(repo, target, new)
        notebook = _text_diff(old, new, old_text, new_text, status, raw=False)
        if notebook is not None or _is_notebook_pair(old_text, new_text):
            return notebook
    text = repo.run(
        "diff", "--no-color", "-M", base, target, "--", *dict.fromkeys([old, new])
    )
    if not text:
        return None
    additions, deletions = _count(text)
    return FileDiff(new, old, status, text, additions, deletions)


def _is_notebook_pair(old_text: str | None, new_text: str | None) -> bool:
    return all(t is None or notebook_code(t) is not None for t in (old_text, new_text))


def _text_diff(
    old: str,
    new: str,
    old_text: str | None,
    new_text: str | None,
    status: str,
    raw: bool,
) -> FileDiff | None:
    note = ""
    old_lines = _lines(old_text)
    new_lines = _lines(new_text)
    if not raw and new.endswith(NOTEBOOK_SUFFIX):
        old_code = [] if old_text is None else notebook_code(old_text)
        new_code = [] if new_text is None else notebook_code(new_text)
        if old_code is not None and new_code is not None:
            old_lines, new_lines, note = old_code, new_code, " (code cells only)"
    body = "".join(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile="/dev/null" if old_text is None else f"a/{old}",
            tofile="/dev/null" if new_text is None else f"b/{new}",
        )
    )
    if not body:
        return None
    text = f"diff --melog a/{old} b/{new}{note}\n{body}"
    additions, deletions = _count(body)
    return FileDiff(new, old, status, text, additions, deletions)


def _lines(text: str | None) -> list[str]:
    if text is None:
        return []
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    return lines


def notebook_code(text: str) -> list[str] | None:
    """The source of the code cells of a notebook, or None if it is not one."""
    try:
        notebook = json.loads(text)
        cells = notebook["cells"]
    except (ValueError, KeyError, TypeError):
        return None
    lines: list[str] = []
    number = 0
    for cell in cells:
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        number += 1
        source = cell.get("source", "")
        source = "".join(source) if isinstance(source, list) else str(source)
        lines.append(f"# ── code cell {number} ──\n")
        lines.extend(_lines(source))
    return lines


def _count(text: str) -> tuple[int, int]:
    additions = deletions = 0
    for line in text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return additions, deletions

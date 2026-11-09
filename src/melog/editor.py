"""Editing records and overviews in the user's editor."""

from __future__ import annotations

import copy
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from melog.git import Repo
from melog.record import (
    Record,
    RecordError,
    check_headings,
    dump_body,
    dump_yaml,
    editable_data,
    parse_body,
    parse_scores,
    split_frontmatter,
)

EDITABLE_KEYS = ("title", "parents", "entry_points", "scores", "verdict")
RECORDED_KEYS = (
    "id",
    "status",
    "started_at",
    "ended_at",
    "start_snapshot",
    "end_snapshot",
    "origin",
)


class EditorError(Exception):
    """The editor could not be started or exited with an error."""


def editor_command(repo: Repo) -> str:
    """The editor git would use: GIT_EDITOR, core.editor, VISUAL, EDITOR, then vi."""
    return repo.run("var", "GIT_EDITOR").strip()


def run_editor(repo: Repo, path: Path) -> None:
    command = editor_command(repo)
    if os.name == "nt":
        args: list[str] | str = f'{command} "{path}"'
        shell = True
    else:
        # The same way git starts the editor, so commands with arguments work.
        args = ["sh", "-c", f'{command} "$@"', command, str(path)]
        shell = False
    try:
        proc = subprocess.run(args, shell=shell, check=False)
    except OSError as e:
        raise EditorError(f"cannot start the editor {command!r}: {e}") from e
    if proc.returncode != 0:
        raise EditorError(f"the editor {command!r} exited with code {proc.returncode}")


def write_temp(text: str, prefix: str) -> Path:
    fd, name = tempfile.mkstemp(prefix=f"melog-{prefix}-", suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return Path(name)


def _local_time(value: str | None) -> str:
    if value is None:
        return "-"
    return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M")


def render_record(project: str, record: Record) -> str:
    """The document opened in the editor for ``record``."""
    facts = [
        ("status", record.status),
        ("started", _local_time(record.started_at)),
        ("ended", _local_time(record.ended_at)),
    ]
    if record.origin is not None:
        facts.append(("origin", f"{record.origin.project}/{record.origin.id}"))
    width = max(len(label) for label, _ in facts) + 1
    comments = [
        f"# melog: editing {record.id} in project {project}",
        "# Recorded by melog, cannot be changed here:",
        *(f"#   {label + ':':<{width}} {value}" for label, value in facts),
        "# Save and close the editor to apply. Delete everything to cancel.",
    ]
    frontmatter = dump_yaml(editable_data(record))
    return (
        "---\n"
        + "\n".join(comments)
        + "\n"
        + frontmatter
        + "---\n\n"
        + dump_body(record)
    )


def parse_edited(text: str, base: Record) -> Record | None:
    """Apply an edited document to a copy of ``base``; None means cancelled."""
    if not text.strip():
        return None
    data, body = split_frontmatter(text)

    recorded = [key for key in data if key in RECORDED_KEYS]
    if recorded:
        raise RecordError(
            f"{', '.join(recorded)} {'is' if len(recorded) == 1 else 'are'} recorded "
            "by melog and cannot be changed; remove it from the frontmatter"
        )
    unknown = [str(key) for key in data if key not in EDITABLE_KEYS]
    if unknown:
        raise RecordError(
            f"unknown field {', '.join(map(repr, unknown))}; the fields you can "
            f"edit are {', '.join(EDITABLE_KEYS)}"
        )
    check_headings(body)

    title = data.get("title")
    if title is None or not str(title).strip():
        raise RecordError("the title must not be empty")

    edited = copy.deepcopy(base)
    edited.title = str(title).strip()
    edited.parents = _string_list(data.get("parents"), "parents")
    edited.entry_points = _string_list(data.get("entry_points"), "entry_points")
    edited.scores = parse_scores(data.get("scores"))
    verdict = data.get("verdict")
    edited.verdict = ("" if verdict is None else str(verdict).strip()) or None
    edited.body = parse_body(body)
    return edited


def _string_list(value: object, key: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RecordError(f"{key} must be a list, such as [a, b]")
    items = [str(item).strip() for item in value]
    return [item for item in items if item]

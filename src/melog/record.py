"""Experiment records: Markdown files with a YAML frontmatter."""

from __future__ import annotations

import io
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

from ruamel.yaml import YAML
from ruamel.yaml.constructor import RoundTripConstructor
from ruamel.yaml.error import YAMLError
from ruamel.yaml.representer import RoundTripRepresenter

SECTIONS = ("동기와 예상", "변경점", "결과", "메모")
MOTIVATION = SECTIONS[0]
STATUSES = ("in_progress", "done", "abandoned")

INT_TAG = "tag:yaml.org,2002:int"
FLOAT_TAG = "tag:yaml.org,2002:float"
TIMESTAMP_TAG = "tag:yaml.org,2002:timestamp"

INT_PATTERN = re.compile(r"[-+]?[0-9]+")
FLOAT_PATTERN = re.compile(
    r"[-+]?(?:[0-9]+\.[0-9]*|\.[0-9]+|[0-9]+)(?:[eE][-+]?[0-9]+)?"
)
FRONTMATTER_PATTERN = re.compile(r"---\n(.*?)^---[ \t]*\n?", re.S | re.M)


class RecordError(Exception):
    """An experiment record is malformed."""


class InvalidScoreError(RecordError):
    def __init__(self, name: str, value: str) -> None:
        super().__init__(f"score {name!r} must be a number, got {value!r}")


# YAML scalars whose original text must survive a load and dump unchanged.


class _Tagged(str):
    tag: str

    def __new__(cls, value: str, tag: str) -> _Tagged:
        obj = super().__new__(cls, value)
        obj.tag = tag
        return obj


class _Constructor(RoundTripConstructor):
    pass


class _Representer(RoundTripRepresenter):
    pass


def _raw(tag: str):
    def construct(constructor, node):
        return _Tagged(node.value, tag)

    return construct


for _tag in (INT_TAG, FLOAT_TAG, TIMESTAMP_TAG):
    _Constructor.add_constructor(_tag, _raw(_tag))
_Representer.add_representer(
    _Tagged, lambda representer, data: representer.represent_scalar(data.tag, str(data))
)


def _yaml() -> YAML:
    yaml = YAML()
    yaml.Constructor = _Constructor
    yaml.Representer = _Representer
    yaml.default_flow_style = False
    yaml.width = 4096
    return yaml


def normalize_score(name: str, value: str) -> str:
    """Check that ``value`` is a number and return it in the notation it was given."""
    text = value.strip()
    if not FLOAT_PATTERN.fullmatch(text):
        raise InvalidScoreError(name, value)
    return text


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class Origin:
    project: str
    id: str
    snapshot: str

    def to_dict(self) -> dict[str, str]:
        return {"project": self.project, "id": self.id, "snapshot": self.snapshot}


@dataclass
class Record:
    id: str
    title: str
    status: str = "in_progress"
    started_at: str | None = None
    ended_at: str | None = None
    start_snapshot: str | None = None
    end_snapshot: str | None = None
    origin: Origin | None = None
    parents: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    scores: dict[str, str | None] = field(default_factory=dict)
    """Score names mapped to the value as written, or None while it is empty."""
    verdict: str | None = None
    body: dict[str, str] = field(default_factory=dict)

    def section(self, name: str) -> str:
        return self.body.get(name, "")

    def set_scores(self, scores: Mapping[str, str]) -> None:
        for name, value in scores.items():
            self.scores[name] = normalize_score(name, value)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "start_snapshot": self.start_snapshot,
            "end_snapshot": self.end_snapshot,
            "origin": self.origin.to_dict() if self.origin else None,
            "parents": list(self.parents),
            "entry_points": list(self.entry_points),
            "scores": dict(self.scores),
            "verdict": self.verdict,
            "body": {name: self.section(name) for name in SECTIONS},
        }


def record_path(project: str, exp_id: str) -> str:
    return f"{project}/records/{exp_id}.md"


def records_dir(project: str) -> str:
    return f"{project}/records"


def _score_scalar(value: str | None) -> _Tagged | None:
    if value is None:
        return None
    return _Tagged(value, INT_TAG if INT_PATTERN.fullmatch(value) else FLOAT_TAG)


def _time_scalar(value: str | None) -> _Tagged | None:
    return None if value is None else _Tagged(value, TIMESTAMP_TAG)


def dump_yaml(data: Mapping[str, object]) -> str:
    buf = io.StringIO()
    _yaml().dump(dict(data), buf)
    return buf.getvalue()


def editable_data(record: Record) -> dict[str, object]:
    """The frontmatter fields a user may change, ready to be dumped."""
    return {
        "title": record.title,
        "parents": list(record.parents),
        "entry_points": list(record.entry_points),
        "scores": {name: _score_scalar(v) for name, v in record.scores.items()},
        "verdict": record.verdict,
    }


def dump_body(record: Record) -> str:
    parts = []
    for name in SECTIONS:
        text = record.section(name).strip("\n")
        parts.append(f"## {name}\n" + (f"\n{text}\n" if text else ""))
    return "\n".join(parts)


def dump_record(record: Record) -> str:
    data: dict[str, object] = {
        "id": record.id,
        "title": record.title,
        "status": record.status,
        "started_at": _time_scalar(record.started_at),
        "ended_at": _time_scalar(record.ended_at),
        "start_snapshot": record.start_snapshot,
        "end_snapshot": record.end_snapshot,
    }
    if record.origin is not None:
        data["origin"] = record.origin.to_dict()
    editable = editable_data(record)
    del editable["title"]  # already placed right after the id
    data.update(editable)
    return f"---\n{dump_yaml(data)}---\n\n" + dump_body(record)


def _optional_str(data: Mapping[str, object], key: str) -> str | None:
    value = data.get(key)
    if value is None or value == "":
        return None
    return str(value)


def _str_list(data: Mapping[str, object], key: str) -> list[str]:
    value = data.get(key) or []
    if not isinstance(value, list):
        raise RecordError(f"{key} must be a list")
    return [str(item) for item in value]


def split_frontmatter(text: str) -> tuple[dict, str]:
    """Return the frontmatter as a mapping and the text that follows it."""
    match = FRONTMATTER_PATTERN.match(text)
    if match is None:
        raise RecordError("the document must start with a '---' frontmatter block")
    try:
        data = _yaml().load(match.group(1))
    except YAMLError as e:
        raise RecordError(f"cannot parse frontmatter: {e}") from e
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise RecordError("frontmatter must be a mapping")
    return data, text[match.end() :]


def parse_scores(raw: object) -> dict[str, str | None]:
    raw = raw or {}
    if not isinstance(raw, dict):
        raise RecordError("scores must be a mapping of names to numbers")
    scores: dict[str, str | None] = {}
    for name, value in raw.items():
        scores[str(name)] = (
            None if value is None or value == "" else normalize_score(name, str(value))
        )
    return scores


def check_headings(body: str) -> None:
    """Require each section heading exactly once, so no section is merged away."""
    lines = [line.rstrip() for line in body.splitlines()]
    problems = []
    for name in SECTIONS:
        count = lines.count(f"## {name}")
        if count == 0:
            problems.append(f"missing heading '## {name}'")
        elif count > 1:
            problems.append(f"heading '## {name}' appears {count} times")
    if problems:
        raise RecordError("; ".join(problems))


def parse_body(text: str) -> dict[str, str]:
    """Split the body into the fixed sections. Unknown headings stay in a section."""
    headings = {f"## {name}": name for name in SECTIONS}
    body: dict[str, list[str]] = {name: [] for name in SECTIONS}
    current: str | None = None
    for line in text.splitlines():
        if line.rstrip() in headings:
            current = headings[line.rstrip()]
            continue
        if current is not None:
            body[current].append(line)
    return {name: "\n".join(lines).strip("\n") for name, lines in body.items()}


def parse_record(text: str) -> Record:
    data, body = split_frontmatter(text)

    exp_id = _optional_str(data, "id")
    title = _optional_str(data, "title")
    if exp_id is None or title is None:
        raise RecordError("record needs an id and a title")
    status = _optional_str(data, "status")
    if status not in STATUSES:
        raise RecordError(f"unknown status {status!r}")

    origin = None
    raw_origin = data.get("origin")
    if raw_origin:
        if not isinstance(raw_origin, dict):
            raise RecordError("origin must be a mapping")
        origin = Origin(
            project=str(raw_origin["project"]),
            id=str(raw_origin["id"]),
            snapshot=str(raw_origin["snapshot"]),
        )

    return Record(
        id=exp_id,
        title=title,
        status=status,
        started_at=_optional_str(data, "started_at"),
        ended_at=_optional_str(data, "ended_at"),
        start_snapshot=_optional_str(data, "start_snapshot"),
        end_snapshot=_optional_str(data, "end_snapshot"),
        origin=origin,
        parents=_str_list(data, "parents"),
        entry_points=_str_list(data, "entry_points"),
        scores=parse_scores(data.get("scores")),
        verdict=_optional_str(data, "verdict"),
        body=parse_body(body),
    )


def find_cycle(parents_of: Mapping[str, Iterable[str]]) -> list[str] | None:
    """Return the ids of one parent cycle, or None if the graph has none."""
    visiting: list[str] = []
    done: set[str] = set()

    def visit(node: str) -> list[str] | None:
        if node in done:
            return None
        if node in visiting:
            return [*visiting[visiting.index(node) :], node]
        visiting.append(node)
        for parent in parents_of.get(node, ()):
            cycle = visit(parent)
            if cycle:
                return cycle
        visiting.pop()
        done.add(node)
        return None

    for node in parents_of:
        cycle = visit(node)
        if cycle:
            return cycle
    return None

"""Views of recorded experiments: the lineage and a collected document.

Views only show what was recorded. They never rank experiments, so there is no
"best" marker.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from melog.record import SECTIONS, Record

STATUS_LABELS = {"in_progress": "진행 중", "done": "완료", "abandoned": "중단"}


def display_width(text: str) -> int:
    """Columns the text takes in a terminal; East Asian characters take two."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def pad(text: str, width: int) -> str:
    return text + " " * (width - display_width(text))


def local_time(value: str | None) -> str:
    if value is None:
        return "-"
    return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M")


# Lineage


@dataclass
class Node:
    record: Record
    children: list[Node] = field(default_factory=list)
    other_parents: list[str] = field(default_factory=list)


def build_lineage(records: Iterable[Record]) -> list[Node]:
    """Arrange records into trees, oldest first.

    A record goes under the first of its parents that is shown; its other
    parents are listed on the node. Records without a shown parent are roots.
    """
    records = list(records)
    nodes = {r.id: Node(r) for r in records}
    roots = []
    for record in records:
        node = nodes[record.id]
        shown = [p for p in record.parents if p in nodes and p != record.id]
        if shown:
            nodes[shown[0]].children.append(node)
            node.other_parents = shown[1:]
        else:
            roots.append(node)
    return roots


def score_name(scores: list[str], chosen: str | None) -> str | None:
    """The score shown on lineage nodes: the chosen one, or the first configured."""
    if chosen is not None:
        return chosen
    return scores[0] if scores else None


def render_tree(roots: list[Node], score: str | None) -> list[str]:
    rows: list[tuple[str, Record, str]] = []

    def walk(node: Node, prefix: str, connector: str, seen: set[str]) -> None:
        notes = []
        if node.other_parents:
            notes.append(f"(also from {', '.join(node.other_parents)})")
        origin = node.record.origin
        if origin is not None:
            notes.append(f"(imported from {origin.project}/{origin.id})")
        rows.append((prefix + connector, node.record, " ".join(notes)))
        if node.record.id in seen:  # never loops, even on a broken record
            return
        seen = seen | {node.record.id}
        child_prefix = prefix + (
            "" if not connector else "│  " if connector == "├─ " else "   "
        )
        for i, child in enumerate(node.children):
            last = i == len(node.children) - 1
            walk(child, child_prefix, "└─ " if last else "├─ ", seen)

    for root in roots:
        walk(root, "", "", set())
    if not rows:
        return []

    def score_text(record: Record) -> str:
        if score is None:
            return ""
        return f"{score} {record.scores.get(score) or '-'}"

    id_width = max(display_width(lead + r.id) for lead, r, _ in rows)
    status_width = max(len(r.status) for _, r, _ in rows)
    title_width = max(display_width(r.title) for _, r, _ in rows)
    score_width = max(display_width(score_text(r)) for _, r, _ in rows)
    lines = []
    for lead, record, notes in rows:
        cells = [
            pad(lead + record.id, id_width),
            record.status.ljust(status_width),
            pad(record.title, title_width),
            pad(score_text(record), score_width),
            notes,
        ]
        lines.append("  ".join(cells).rstrip())
    return lines


def _node_id(exp_id: str) -> str:
    return "n_" + re.sub(r"\W", "_", exp_id)


def _mermaid_text(text: str) -> str:
    return text.replace("&", "#amp;").replace('"', "#quot;").replace("<", "#lt;")


def render_mermaid(records: Iterable[Record], score: str | None) -> list[str]:
    records = list(records)
    ids = {r.id for r in records}
    lines = [
        "graph LR",
        "  classDef in_progress stroke-dasharray: 4 3",
        "  classDef abandoned stroke-dasharray: 2 2,color:#888",
    ]
    for r in records:
        detail = r.status
        if score is not None:
            detail += f" · {score} {r.scores.get(score) or '-'}"
        if r.origin is not None:
            detail += f" · from {r.origin.project}/{r.origin.id}"
        label = _mermaid_text(f"{r.id} · {r.title}") + "<br/>" + _mermaid_text(detail)
        lines.append(f'  {_node_id(r.id)}["{label}"]')
    for r in records:
        for parent in r.parents:
            if parent in ids:
                lines.append(f"  {_node_id(parent)} --> {_node_id(r.id)}")
    for status in ("in_progress", "abandoned"):
        members = [_node_id(r.id) for r in records if r.status == status]
        if members:
            lines.append(f"  class {','.join(members)} {status}")
    return lines


# Collected document


def export_markdown(
    project: str,
    overview: str,
    records: list[Record],
    score: str | None,
    diff_stats: dict[str, str] | None = None,
) -> str:
    """One Markdown document with the overview, the lineage and every record."""
    parts = [
        f"# {project} 실험 기록",
        "",
        f"> melog export로 {local_time(_now())}에 생성한 문서입니다.",
        "",
        "## 개요",
        "",
        overview.strip() or "(비어 있음)",
        "",
        "## 계보",
        "",
        "```mermaid",
        *render_mermaid(records, score),
        "```",
        "",
        "## 실험 기록",
    ]
    if not records:
        parts += ["", "(실험 기록이 없습니다)"]
    for record in records:
        parts += ["", f"### {record.id} · {record.title}", ""]
        parts += _fact_table(record)
        for name in SECTIONS:
            parts += ["", f"#### {name}", "", record.section(name).strip() or "-"]
        if diff_stats is not None:
            parts += ["", "#### 코드 변경", "", "```text", diff_stats[record.id], "```"]
    return "\n".join(parts) + "\n"


def _fact_table(record: Record) -> list[str]:
    scores = ", ".join(f"{n} {v or '-'}" for n, v in record.scores.items()) or "-"
    rows = [
        ("상태", STATUS_LABELS.get(record.status, record.status)),
        ("시작", local_time(record.started_at)),
        ("종료", local_time(record.ended_at)),
        ("부모", ", ".join(record.parents) or "-"),
    ]
    if record.origin is not None:
        rows.append(("출처", f"{record.origin.project}/{record.origin.id}"))
    rows += [
        ("진입점", ", ".join(record.entry_points) or "-"),
        ("점수", scores),
        ("판정", record.verdict or "-"),
    ]
    return ["| 항목 | 값 |", "|---|---|"] + [
        f"| {label} | {value.replace('|', '\\|')} |" for label, value in rows
    ]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")

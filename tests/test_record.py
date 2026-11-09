import pytest

from melog.record import (
    MOTIVATION,
    InvalidScoreError,
    Origin,
    Record,
    RecordError,
    dump_record,
    find_cycle,
    parse_body,
    parse_record,
)


def make_record(**kwargs) -> Record:
    defaults = dict(
        id="bjs-1",
        title="LightGBM baseline",
        status="done",
        started_at="2026-11-09T14:30:00+09:00",
        ended_at="2026-11-09T16:05:00+09:00",
        start_snapshot="3f2a9c1e",
        end_snapshot="8b7d4e02",
        parents=["bjs-0"],
        entry_points=["notebooks/baseline.ipynb"],
        scores={"CV": "0.812", "CV std": "0.004", "public LB": None},
        verdict="good enough",
        body={MOTIVATION: "왜 해 보는가\n\n두 번째 문단", "메모": "다음에 해 볼 것"},
    )
    defaults.update(kwargs)
    return Record(**defaults)


def test_round_trip() -> None:
    record = make_record()
    assert parse_record(dump_record(record)) == make_record(
        body={
            "동기와 예상": "왜 해 보는가\n\n두 번째 문단",
            "변경점": "",
            "결과": "",
            "메모": "다음에 해 볼 것",
        }
    )


def test_dump_format() -> None:
    text = dump_record(make_record())
    assert text.startswith("---\nid: bjs-1\ntitle: LightGBM baseline\nstatus: done\n")
    assert "started_at: 2026-11-09T14:30:00+09:00\n" in text
    assert "  CV: 0.812\n" in text
    assert "  public LB:\n" in text
    assert "origin" not in text
    assert text.endswith(
        "---\n\n## 동기와 예상\n\n왜 해 보는가\n\n두 번째 문단\n\n## 변경점\n\n"
        "## 결과\n\n## 메모\n\n다음에 해 볼 것\n"
    )


@pytest.mark.parametrize(
    "value", ["0.80", "1", "-0.5", "1e-3", ".5", "1.0e+3", "1e3", "+2", "10.000"]
)
def test_score_notation_is_preserved(value: str) -> None:
    record = make_record(scores={"CV": value})
    text = dump_record(record)
    assert f"  CV: {value}\n" in text
    assert parse_record(text).scores == {"CV": value}


@pytest.mark.parametrize("value", ["abc", "", "0.8.1", ".inf", "1,000", "nan"])
def test_invalid_scores(value: str) -> None:
    with pytest.raises(InvalidScoreError):
        make_record().set_scores({"CV": value})


def test_set_scores_keeps_order_and_adds_names() -> None:
    record = make_record()
    record.set_scores({"public LB": " 0.805 ", "latency": "12"})
    assert record.scores == {
        "CV": "0.812",
        "CV std": "0.004",
        "public LB": "0.805",
        "latency": "12",
    }


def test_special_characters_in_text() -> None:
    record = make_record(title='class_weight: "balanced" # 1', verdict="a: b")
    parsed = parse_record(dump_record(record))
    assert parsed.title == 'class_weight: "balanced" # 1'
    assert parsed.verdict == "a: b"


def test_origin_round_trip() -> None:
    origin = Origin(project="lgbm", id="bjs-3", snapshot="8b7d4e02")
    record = make_record(origin=origin, start_snapshot=None, started_at=None)
    parsed = parse_record(dump_record(record))
    assert parsed.origin == origin
    assert parsed.start_snapshot is None
    assert parsed.started_at is None


def test_empty_record() -> None:
    record = Record(id="bjs-2", title="t")
    parsed = parse_record(dump_record(record))
    assert parsed.status == "in_progress"
    assert parsed.scores == {}
    assert parsed.parents == []
    assert parsed.verdict is None
    assert parsed.started_at is None


def test_parse_body_keeps_unknown_headings_in_section() -> None:
    body = parse_body(
        "## 변경점\n\n- a\n\n### detail\n\ntext\n## Other\nmore\n\n## 결과\n\nok\n"
    )
    assert body["변경점"] == "- a\n\n### detail\n\ntext\n## Other\nmore"
    assert body["결과"] == "ok"
    assert body["메모"] == ""


@pytest.mark.parametrize(
    "text",
    [
        "no frontmatter",
        "---\n- a\n---\n",
        "---\ntitle: t\nstatus: done\n---\n",
        "---\nid: a\ntitle: t\nstatus: finished\n---\n",
        "---\nid: a\ntitle: t\nstatus: done\nscores:\n  CV: high\n---\n",
        "---\nid: a\ntitle: t\nstatus: done\nparents: bjs-1\n---\n",
    ],
)
def test_malformed_records(text: str) -> None:
    with pytest.raises(RecordError):
        parse_record(text)


def test_find_cycle() -> None:
    assert find_cycle({"a": [], "b": ["a"], "c": ["a", "b"]}) is None
    assert find_cycle({"a": ["c"], "b": ["a"], "c": ["b"]}) == ["a", "c", "b", "a"]
    assert find_cycle({"a": ["a"]}) == ["a", "a"]
    assert find_cycle({"a": ["missing"]}) is None

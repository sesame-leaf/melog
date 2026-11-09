from pathlib import Path

import pytest

from melog.editor import EditorError, parse_edited, render_record, run_editor
from melog.git import Repo
from melog.record import (
    MOTIVATION,
    Origin,
    Record,
    RecordError,
    dump_record,
    parse_record,
)


def make_record() -> Record:
    return Record(
        id="bjs-3",
        title="LightGBM baseline",
        status="done",
        started_at="2026-11-09T14:30:00+09:00",
        ended_at="2026-11-09T16:05:00+09:00",
        start_snapshot="3f2a9c1e",
        end_snapshot="8b7d4e02",
        parents=["bjs-1"],
        entry_points=["train.py"],
        scores={"CV": "0.812", "CV std": "0.004", "public LB": None},
        verdict="good",
        body={MOTIVATION: "why"},
    )


def test_render_shows_facts_as_comments() -> None:
    text = render_record("lgbm", make_record())
    assert text.startswith("---\n# melog: editing bjs-3 in project lgbm\n")
    assert "#   status:  done\n" in text
    assert "\ntitle: LightGBM baseline\n" in text
    assert "  CV: 0.812\n" in text
    for hidden in ("id:", "status: done", "start_snapshot", "end_snapshot"):
        assert f"\n{hidden}" not in text
    assert "## 동기와 예상\n\nwhy\n" in text


def test_render_shows_origin() -> None:
    record = make_record()
    record.origin = Origin("bert", "mj-2", "abc")
    assert "#   origin:  bert/mj-2\n" in render_record("lgbm", record)


def test_unchanged_document_parses_to_same_record() -> None:
    record = make_record()
    edited = parse_edited(render_record("lgbm", record), record)
    expected = make_record()
    expected.body = {"동기와 예상": "why", "변경점": "", "결과": "", "메모": ""}
    assert edited == expected


def test_edits_are_applied() -> None:
    text = render_record("lgbm", make_record())
    text = text.replace("public LB:\n", "public LB: 0.80\n  AUC: 1e-3\n")
    text = text.replace("verdict: good", "verdict:")
    text = text.replace("## 변경점\n", "## 변경점\n\n- lr 0.1 -> 0.05\n")
    # Facts edited in the comments are ignored.
    text = text.replace("#   status:  done", "#   status:  abandoned")
    edited = parse_edited(text, make_record())
    assert edited.status == "done"
    assert edited.scores == {
        "CV": "0.812",
        "CV std": "0.004",
        "public LB": "0.80",
        "AUC": "1e-3",
    }
    assert edited.verdict is None
    assert edited.body["변경점"] == "- lr 0.1 -> 0.05"


def test_empty_document_cancels() -> None:
    assert parse_edited("  \n", make_record()) is None


@pytest.mark.parametrize(
    "change, message",
    [
        (("title: LightGBM baseline", "title: ' '"), "title must not be empty"),
        (("verdict: good", "verdic: good"), "unknown field 'verdic'"),
        (("verdict: good", "verdict: good\nstatus: abandoned"), "status is recorded"),
        (("  CV: 0.812", "  CV: high"), "must be a number"),
        (("parents:\n- bjs-1", "parents: bjs-1"), "parents must be a list"),
        (("## 변경점\n", ""), "missing heading '## 변경점'"),
        (("## 결과\n", "## 결과\n\n## 결과\n"), "appears 2 times"),
        (("---\n# melog", "# melog"), "must start with a '---'"),
    ],
)
def test_invalid_edits(change, message: str) -> None:
    text = render_record("lgbm", make_record())
    assert change[0] in text
    with pytest.raises(RecordError, match=message):
        parse_edited(text.replace(change[0], change[1], 1), make_record())


def test_edited_record_round_trips_through_storage() -> None:
    text = render_record("lgbm", make_record()).replace("good", "'a: b # c'")
    edited = parse_edited(text, make_record())
    assert edited.verdict == "a: b # c"
    assert parse_record(dump_record(edited)) == edited


def test_yaml_syntax_error_is_reported() -> None:
    text = render_record("lgbm", make_record()).replace("good", "a: b")
    with pytest.raises(RecordError, match="cannot parse frontmatter"):
        parse_edited(text, make_record())


def test_run_editor_uses_git_editor(repo: Repo, tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "doc.md"
    path.write_text("verdict: old\n")
    monkeypatch.setenv("GIT_EDITOR", "sed -i -e 's/old/new/'")
    run_editor(repo, path)
    assert path.read_text() == "verdict: new\n"


def test_run_editor_failure(repo: Repo, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GIT_EDITOR", "false")
    with pytest.raises(EditorError, match="exited with code 1"):
        run_editor(repo, tmp_path / "doc.md")

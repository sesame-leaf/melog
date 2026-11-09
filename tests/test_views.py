from melog.record import MOTIVATION, Origin, Record
from melog.views import (
    build_lineage,
    display_width,
    export_markdown,
    render_mermaid,
    render_tree,
    score_name,
)


def rec(exp_id: str, parents=(), status="done", title=None, **kwargs) -> Record:
    return Record(
        id=exp_id,
        title=title or f"title of {exp_id}",
        status=status,
        parents=list(parents),
        **kwargs,
    )


def test_display_width_counts_wide_characters() -> None:
    assert display_width("abc") == 3
    assert display_width("베이스라인") == 10


def test_tree() -> None:
    records = [
        rec("bjs-1", scores={"CV": "0.812"}, title="베이스라인"),
        rec("bjs-2", ["bjs-1"], status="abandoned"),
        rec("mj-1", origin=Origin("bert", "mj-9", "abc")),
        rec("bjs-3", ["bjs-1"], scores={"CV": "0.79"}),
        rec("bjs-4", ["bjs-3", "mj-1"], status="in_progress"),
    ]
    lines = render_tree(build_lineage(records), "CV")
    assert lines == [
        "bjs-1        done         베이스라인      CV 0.812",
        "├─ bjs-2     abandoned    title of bjs-2  CV -",
        "└─ bjs-3     done         title of bjs-3  CV 0.79",
        "   └─ bjs-4  in_progress  title of bjs-4  CV -      (also from mj-1)",
        "mj-1         done         title of mj-1   CV -      (imported from bert/mj-9)",
    ]


def test_tree_without_score_and_with_missing_parent() -> None:
    lines = render_tree(build_lineage([rec("bjs-2", ["bjs-1"])]), None)
    assert lines == ["bjs-2  done  title of bjs-2"]


def test_score_name() -> None:
    assert score_name(["CV", "public LB"], None) == "CV"
    assert score_name(["CV"], "AUC") == "AUC"
    assert score_name([], None) is None


def test_mermaid() -> None:
    records = [
        rec("bjs-1", title='a "quoted" <title>'),
        rec("bjs-2", ["bjs-1", "bjs-9"], status="abandoned"),
    ]
    lines = render_mermaid(records, "CV")
    assert lines[0] == "graph LR"
    assert (
        '  n_bjs_1["bjs-1 · a #quot;quoted#quot; #lt;title><br/>done · CV -"]' in lines
    )
    assert "  n_bjs_1 --> n_bjs_2" in lines
    assert not any("bjs_9" in line for line in lines)
    assert "  class n_bjs_2 abandoned" in lines


def test_no_best_marker() -> None:
    records = [rec("a-1", scores={"CV": "0.9"}), rec("a-2", scores={"CV": "0.1"})]
    text = "\n".join(render_tree(build_lineage(records), "CV"))
    text += "\n".join(render_mermaid(records, "CV"))
    assert "★" not in text
    assert "best" not in text


def test_export_markdown() -> None:
    records = [
        rec(
            "bjs-1",
            scores={"CV": "0.8"},
            verdict="a | b",
            body={MOTIVATION: "why"},
            started_at="2026-11-09T14:30:00+09:00",
        ),
        rec("bjs-2", ["bjs-1"], origin=Origin("bert", "mj-1", "abc")),
    ]
    text = export_markdown(
        "lgbm", "# Titanic", records, "CV", {"bjs-1": "x", "bjs-2": "y"}
    )
    assert text.startswith("# lgbm 실험 기록\n")
    assert "## 개요\n\n# Titanic\n" in text
    assert "```mermaid\ngraph LR\n" in text
    assert "### bjs-1 · title of bjs-1" in text
    assert "| 상태 | 완료 |" in text
    assert "| 판정 | a \\| b |" in text
    assert "| 출처 | bert/mj-1 |" in text
    assert "#### 동기와 예상\n\nwhy\n" in text
    assert "#### 변경점\n\n-\n" in text
    assert "#### 코드 변경\n\n```text\nx\n```" in text
    assert text.index("### bjs-1") < text.index("### bjs-2")


def test_export_markdown_without_records() -> None:
    text = export_markdown("lgbm", "", [], None)
    assert "(비어 있음)" in text
    assert "(실험 기록이 없습니다)" in text
    assert "코드 변경" not in text

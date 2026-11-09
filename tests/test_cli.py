import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from melog.cli import app

from .conftest import git

runner = CliRunner()


def test_init(repo_path: Path) -> None:
    result = runner.invoke(app, ["init", "lgbm"])
    assert result.exit_code == 0, result.output
    assert "Created melog project 'lgbm'." in result.output
    assert "CV, CV std, public LB" in result.output


def test_init_json(repo_path: Path) -> None:
    result = runner.invoke(
        app, ["init", "bert", "--scores", "AUC, public LB", "--horizontal", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "project": {
            "name": "bert",
            "scores": ["AUC", "public LB"],
            "horizontal": True,
        }
    }


def test_init_outside_repository(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "lgbm"])
    assert result.exit_code == 1
    assert "not inside a git repository" in result.output


def test_init_twice(repo_path: Path) -> None:
    runner.invoke(app, ["init", "lgbm"])
    result = runner.invoke(app, ["init", "lgbm"])
    assert result.exit_code == 1
    assert "already exists" in result.output


def test_init_invalid_name(repo_path: Path) -> None:
    result = runner.invoke(app, ["init", "LGBM"])
    assert result.exit_code == 1
    assert "invalid project name" in result.output


def test_project_edit(repo_path: Path) -> None:
    runner.invoke(app, ["init", "lgbm"])
    result = runner.invoke(
        app, ["project", "edit", "--scores", "CV,private LB", "--horizontal", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["project"] == {
        "name": "lgbm",
        "scores": ["CV", "private LB"],
        "horizontal": True,
    }


def test_project_edit_with_several_projects(repo_path: Path) -> None:
    runner.invoke(app, ["init", "lgbm"])
    runner.invoke(app, ["init", "bert"])
    result = runner.invoke(app, ["project", "edit", "--no-horizontal"])
    assert result.exit_code == 1
    assert "choose one with --project" in result.output
    result = runner.invoke(app, ["project", "edit", "-p", "bert", "--horizontal"])
    assert result.exit_code == 0, result.output
    assert "horizontal: yes" in result.output


def test_project_edit_without_project(repo_path: Path) -> None:
    result = runner.invoke(app, ["project", "edit", "--horizontal"])
    assert result.exit_code == 1
    assert "melog init" in result.output


# Recording commands


def invoke(*args: str, input: str | None = None):
    return runner.invoke(app, list(args), input=input)


@pytest.fixture
def project(repo_path: Path, initials: str) -> str:
    assert invoke("init", "lgbm").exit_code == 0
    return "lgbm"


def test_start_done_show_list(repo_path: Path, project: str) -> None:
    result = invoke("start", "LightGBM baseline", "--motivation", "first try")
    assert result.exit_code == 0, result.output
    assert "Started bjs-1: LightGBM baseline (lgbm)" in result.output
    assert "melog done" in result.output

    result = invoke("list")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].split() == [
        "ID",
        "STATUS",
        "TITLE",
        "CV",
        "CV",
        "std",
        "public",
        "LB",
    ]
    assert lines[1].startswith("*")
    assert "in_progress" in lines[1]

    result = invoke(
        "done", "--score", "CV=0.80", "--score", "AUC=0.9", "--verdict", "ok"
    )
    assert result.exit_code == 0, result.output
    assert "Finished bjs-1" in result.output
    assert "CV: 0.80" in result.output
    assert "melog edit bjs-1" in result.output

    result = invoke("show", "bjs-1")
    assert result.exit_code == 0, result.output
    assert result.output.startswith("bjs-1: LightGBM baseline\n")
    assert "status:       done" in result.output
    assert "AUC: 0.9" in result.output
    assert "## 동기와 예상\n\nfirst try" in result.output

    result = invoke("list", "--status", "in_progress")
    assert result.output.strip() == "No in_progress experiments in project 'lgbm'."


def test_json_output(repo_path: Path, project: str) -> None:
    started = json.loads(invoke("start", "a", "--json").output)
    assert started["experiment"]["id"] == "bjs-1"
    assert started["experiment"]["project"] == "lgbm"
    assert started["experiment"]["status"] == "in_progress"
    assert started["large_untracked_files"] == []

    listed = json.loads(invoke("list", "--json").output)
    assert listed["experiments"][0]["in_progress_here"] is True

    done = json.loads(invoke("done", "--score", "CV=1e-3", "--json").output)
    assert done["experiment"]["scores"]["CV"] == "1e-3"
    shown = json.loads(invoke("show", "bjs-1", "--json").output)
    assert shown["experiment"] == done["experiment"]


def test_abandon_and_restart(repo_path: Path, project: str) -> None:
    invoke("start", "a")
    result = invoke("abandon", "--verdict", "no time")
    assert result.exit_code == 0, result.output
    assert "Abandoned bjs-1" in result.output
    assert "--parent bjs-1" in result.output
    result = invoke("start", "b", "--parent", "bjs-1")
    assert result.exit_code == 0, result.output
    assert "parents: bjs-1" in result.output


def test_done_new(repo_path: Path, project: str) -> None:
    result = invoke("done", "--new", "forgot", "--since", "HEAD", "--score", "CV=0.5")
    assert result.exit_code == 0, result.output
    assert "Recorded bjs-1: forgot (lgbm)" in result.output
    shown = json.loads(invoke("show", "bjs-1", "--json").output)["experiment"]
    assert shown["status"] == "done"
    assert shown["start_snapshot"] is not None


@pytest.mark.parametrize(
    "args, message",
    [
        (["done", "bjs-1", "--new", "x"], "cannot be given an id"),
        (["done", "--since", "HEAD"], "can only be used with --new"),
        (["done", "--new", "x", "--force"], "--force cannot be used"),
        (["done", "--score", "CV"], "NAME=VALUE"),
        (["done"], "no experiment is in progress"),
        (["list", "--status", "finished"], "unknown status"),
        (["show", "bjs-9"], "does not exist"),
        (["start", "x", "--parent", "bjs-9"], "parent experiments not found"),
    ],
)
def test_recording_errors(repo_path: Path, project: str, args, message) -> None:
    result = invoke(*args)
    assert result.exit_code != 0
    assert message in result.output


def test_busy_workspace(repo_path: Path, project: str) -> None:
    invoke("start", "a")
    result = invoke("start", "b")
    assert result.exit_code == 1
    assert "already has an experiment in progress: bjs-1 (lgbm)" in result.output


def test_start_requires_title_without_terminal(repo_path: Path, project: str) -> None:
    result = invoke("start")
    assert result.exit_code == 1
    assert "missing the title" in result.output


def test_initials_required_without_terminal(repo_path: Path) -> None:
    invoke("init", "lgbm")
    result = invoke("start", "a")
    assert result.exit_code == 1
    assert "git config --global melog.initials" in result.output


def test_interactive_prompts(repo_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("melog.cli.common.stdin_is_tty", lambda: True)
    invoke("init", "lgbm")
    # Initials (first invalid), title, motivation.
    result = invoke("start", input="BJS\nbjs\nbaseline\nwhy not\n")
    assert result.exit_code == 0, result.output
    assert "invalid initials" in result.output
    assert "Started bjs-1: baseline" in result.output
    # CV (first invalid), CV std skipped, public LB, verdict.
    result = invoke("done", input="high\n0.8\n\n0.79\nfine\n")
    assert result.exit_code == 0, result.output
    assert "must be a number" in result.output
    shown = json.loads(invoke("show", "bjs-1", "--json").output)["experiment"]
    assert shown["scores"] == {"CV": "0.8", "CV std": None, "public LB": "0.79"}
    assert shown["verdict"] == "fine"
    assert shown["body"]["동기와 예상"] == "why not"


def test_done_checks_before_prompting(
    repo_path: Path, project: str, monkeypatch
) -> None:
    monkeypatch.setattr("melog.cli.common.stdin_is_tty", lambda: True)
    result = invoke("done", input="0.8\n")
    assert result.exit_code == 1
    assert "(Enter to skip)" not in result.output


def test_project_inferred_from_experiment_in_progress(
    repo_path: Path, project: str
) -> None:
    invoke("init", "bert")
    assert invoke("start", "a").exit_code == 1  # two projects, nothing in progress
    assert invoke("start", "a", "-p", "bert").exit_code == 0
    result = invoke("done")
    assert result.exit_code == 0, result.output
    assert "(bert)" in result.output


# Editing commands


class FakeEditor:
    """Stands in for the user's editor: each call applies the next edit."""

    def __init__(self, *edits) -> None:
        self.edits = list(edits)
        self.seen: list[str] = []

    def __call__(self, repo, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        self.seen.append(text)
        path.write_text(self.edits.pop(0)(text), encoding="utf-8")


@pytest.fixture
def tty(monkeypatch) -> None:
    monkeypatch.setattr("melog.cli.common.stdin_is_tty", lambda: True)


def record_json(exp_id: str = "bjs-1") -> dict:
    return json.loads(invoke("show", exp_id, "--json").output)["experiment"]


def test_edit_with_options(repo_path: Path, project: str) -> None:
    invoke("start", "a", "--motivation", "")
    invoke("done")
    invoke("start", "b")
    result = invoke(
        "edit",
        "bjs-2",
        "--title",
        "b2",
        "--parent",
        "bjs-1",
        "--entry",
        "x.py",
        "--score",
        "CV=0.80",
        "--score",
        "public LB=",
        "--verdict",
        "ok",
        "--changes",
        "- lr",
        "--json",
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["changed"] == [
        "title",
        "parents",
        "entry_points",
        "verdict",
        "scores.CV",
        "body.변경점",
    ]
    record = record_json("bjs-2")
    assert record["title"] == "b2"
    assert record["parents"] == ["bjs-1"]
    assert record["scores"]["CV"] == "0.80"
    assert record["body"]["변경점"] == "- lr"

    result = invoke("edit", "--clear-parents", "--clear-entries", "--verdict", "")
    assert result.exit_code == 0, result.output
    assert result.output == "Updated bjs-2: parents, entry points, verdict\n"
    assert invoke("edit", "--title", "b2").output == "No changes.\n"


def test_edit_section_from_stdin(repo_path: Path, project: str) -> None:
    invoke("start", "a", "--motivation", "")
    result = invoke("edit", "--result", "-", input="CV 0.81\n\nfold 3 is low\n")
    assert result.exit_code == 0, result.output
    assert record_json()["body"]["결과"] == "CV 0.81\n\nfold 3 is low"


@pytest.mark.parametrize(
    "args, message",
    [
        (["edit", "bjs-1", "--parent", "bjs-1", "--clear-parents"], "cannot be used"),
        (["edit", "bjs-1", "--memo", "-", "--result", "-"], "only one section"),
        (["edit", "bjs-1"], "run in a terminal"),
        (["edit", "--verdict", "x"], "pass the id of the experiment to edit"),
        (["edit", "bjs-1", "--score", "CV=high"], "must be a number"),
        (["edit", "bjs-1", "--parent", "bjs-1"], "own parent"),
        (["edit", "bjs-9", "--verdict", "x"], "does not exist"),
    ],
)
def test_edit_errors(repo_path: Path, project: str, args, message: str) -> None:
    invoke("start", "a", "--motivation", "")
    invoke("done")
    result = invoke(*args)
    assert result.exit_code == 1
    assert message in result.output


def test_edit_in_editor(repo_path: Path, project: str, tty, monkeypatch) -> None:
    invoke("start", "a", "--motivation", "why")
    editor = FakeEditor(
        lambda t: t.replace("verdict:", "verdict: great").replace(
            "## 변경점\n", "## 변경점\n\nmore trees\n"
        )
    )
    monkeypatch.setattr("melog.cli.editing.run_editor", editor)
    result = invoke("edit")
    assert result.exit_code == 0, result.output
    assert result.output == "Updated bjs-1: verdict, 변경점\n"
    assert "# melog: editing bjs-1 in project lgbm" in editor.seen[0]
    record = record_json()
    assert record["verdict"] == "great"
    assert record["body"] == {
        "동기와 예상": "why",
        "변경점": "more trees",
        "결과": "",
        "메모": "",
    }


def test_edit_in_editor_unchanged_and_cancelled(
    repo_path: Path, project: str, tty, monkeypatch
) -> None:
    invoke("start", "a", "--motivation", "")
    monkeypatch.setattr("melog.cli.editing.run_editor", FakeEditor(lambda t: t))
    assert invoke("edit").output == "No changes.\n"
    monkeypatch.setattr("melog.cli.editing.run_editor", FakeEditor(lambda t: ""))
    assert invoke("edit").output == "Edit cancelled.\n"


def test_edit_in_editor_retries_invalid_edits(
    repo_path: Path, project: str, tty, monkeypatch
) -> None:
    invoke("start", "a", "--motivation", "")
    editor = FakeEditor(
        lambda t: t.replace("verdict:", "verdic: typo"),
        lambda t: t.replace("verdic: typo", "verdict: fixed"),
    )
    monkeypatch.setattr("melog.cli.editing.run_editor", editor)
    result = invoke("edit", input="y\n")
    assert result.exit_code == 0, result.output
    assert "unknown field 'verdic'" in result.output
    # The second round reopens what the user typed, not the original record.
    assert "verdic: typo" in editor.seen[1]
    assert record_json()["verdict"] == "fixed"


def test_edit_in_editor_keeps_text_when_giving_up(
    repo_path: Path, project: str, tty, monkeypatch
) -> None:
    invoke("start", "a", "--motivation", "")
    monkeypatch.setattr(
        "melog.cli.editing.run_editor",
        FakeEditor(lambda t: t.replace("verdict:", "x: 1")),
    )
    result = invoke("edit", input="n\n")
    assert result.exit_code == 1
    kept = Path(result.output.split("Your edit is kept in ")[1].strip())
    assert "x: 1" in kept.read_text()
    kept.unlink()


def test_edit_in_editor_conflict(
    repo_path: Path, project: str, tty, monkeypatch
) -> None:
    invoke("start", "a", "--motivation", "")

    def change_meanwhile(text: str) -> str:
        assert invoke("edit", "--verdict", "theirs").exit_code == 0
        return text.replace("verdict:", "verdict: mine")

    monkeypatch.setattr("melog.cli.editing.run_editor", FakeEditor(change_meanwhile))
    result = invoke("edit")
    assert result.exit_code == 1
    assert "was changed elsewhere" in result.output
    kept = Path(result.output.split("Your edit is kept in ")[1].strip())
    assert "verdict: mine" in kept.read_text()
    kept.unlink()
    assert record_json()["verdict"] == "theirs"


def test_project_overview_in_editor(
    repo_path: Path, project: str, tty, monkeypatch
) -> None:
    monkeypatch.setattr(
        "melog.cli.editing.run_editor", FakeEditor(lambda t: "# Titanic\n\n5-fold CV\n")
    )
    result = invoke("project", "edit")
    assert result.exit_code == 0, result.output
    assert result.output == "Updated the overview of 'lgbm'.\n"
    from melog.git import Repo
    from melog.project import load_overview
    from melog.store import Store

    view = Store(Repo.discover(repo_path)).view()
    assert load_overview(view, "lgbm") == "# Titanic\n\n5-fold CV\n"


def test_project_edit_without_terminal(repo_path: Path, project: str) -> None:
    result = invoke("project", "edit")
    assert result.exit_code == 1
    assert "run in a terminal" in result.output


# Code commands


def test_diff_command(repo_path: Path, project: str) -> None:
    invoke("start", "a")
    (repo_path / "train.py").write_text("print('better')\n")
    result = invoke("diff", "bjs-1")
    assert result.exit_code == 0, result.output
    assert result.output.startswith("# bjs-1 start -> work tree\n")
    assert "+print('better')" in result.output

    invoke("done")
    result = invoke("diff", "bjs-1", "--stat")
    assert result.exit_code == 0, result.output
    assert "train.py  +1 -1  (modified)" in result.output
    assert "1 file changed, +1 -1" in result.output

    data = json.loads(invoke("diff", "bjs-1", "--json").output)
    assert data["base"]["kind"] == "start_snapshot"
    assert data["target"]["kind"] == "end_snapshot"
    assert [f["path"] for f in data["files"]] == ["train.py"]

    assert "No differences." in invoke("diff", "bjs-1", "missing.py").output


def test_diff_entry_points(repo_path: Path, project: str) -> None:
    (repo_path / "a.py").write_text("model = 'A'\n")
    invoke("done", "--new", "a", "--entry", "a.py")
    (repo_path / "b.py").write_text("model = 'B'\n")
    invoke("done", "--new", "b", "--entry", "b.py", "--parent", "bjs-1")
    result = invoke("diff", "bjs-2", "--against", "bjs-1", "--entry-points")
    assert result.exit_code == 0, result.output
    assert "-model = 'A'\n+model = 'B'\n" in result.output
    result = invoke("diff", "bjs-2", "--entry-points")
    assert result.exit_code == 1
    assert "--against" in result.output
    assert "start snapshot" in invoke("diff", "bjs-2").output


def test_diff_uses_git_pager(repo_path: Path, project: str, monkeypatch) -> None:
    invoke("start", "a")
    (repo_path / "train.py").write_text("print('paged')\n")
    out = repo_path.parent / "paged.txt"
    monkeypatch.setattr("melog.cli.code_cmds._stdout_is_tty", lambda: True)
    monkeypatch.setenv("GIT_PAGER", f"cat > {out}")
    result = invoke("diff", "bjs-1")
    assert result.exit_code == 0, result.output
    paged = out.read_text()
    assert "print('paged')" in paged
    assert "\x1b[" in paged  # colored for the terminal


def test_checkout_command(repo_path: Path, project: str) -> None:
    invoke("start", "a")
    (repo_path / "train.py").write_text("print('v2')\n")
    invoke("done")
    target = repo_path.parent / "co"
    result = invoke("checkout", "bjs-1", str(target))
    assert result.exit_code == 0, result.output
    assert "Checked out the end of bjs-1 into" in result.output
    assert "git worktree remove" in result.output
    assert (target / "train.py").read_text() == "print('v2')\n"
    result = invoke("checkout", "bjs-1", str(target))
    assert result.exit_code == 1
    assert "already exists" in result.output

    data = json.loads(
        invoke(
            "checkout", "bjs-1", str(repo_path.parent / "co2"), "--start", "--json"
        ).output
    )
    assert data["snapshot"] == "start"
    assert (Path(data["path"]) / "train.py").read_text() == "print('train')\n"


# Importing


def test_import_command(repo_path: Path, project: str) -> None:
    invoke("init", "ens")
    invoke("start", "base", "-p", "lgbm")
    invoke("done", "-p", "lgbm")
    result = invoke("import", "lgbm/bjs-1", "-p", "ens", "--motivation", "stack it")
    assert result.exit_code == 0, result.output
    assert "Imported lgbm/bjs-1 into ens as bjs-1: base" in result.output
    assert "--parent bjs-1" in result.output
    shown = invoke("show", "bjs-1", "-p", "ens")
    assert "origin:       lgbm/bjs-1" in shown.output

    result = invoke("import", "lgbm/bjs-1", "-p", "ens", "--json")
    assert result.exit_code == 1
    assert "already imported" in result.output
    result = invoke("import", "lgbm-bjs-1", "-p", "ens")
    assert result.exit_code == 1
    assert "expected PROJECT/ID" in result.output


def test_import_checks_before_prompting(repo_path: Path, project: str, tty) -> None:
    invoke("init", "ens")
    result = invoke("import", "lgbm/bjs-9", "-p", "ens", input="why\n")
    assert result.exit_code == 1
    assert "does not exist" in result.output
    assert "Motivation" not in result.output


# Collaboration


@pytest.fixture
def remote(tmp_path: Path, repo_path: Path, project: str) -> Path:
    bare = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    git(repo_path, "remote", "add", "origin", str(bare))
    git(repo_path, "push", "-q", "origin", "main")
    return bare


def test_sync_command(repo_path: Path, remote: Path) -> None:
    invoke("start", "a")
    invoke("done")
    result = invoke("sync")
    assert result.exit_code == 1
    assert "--yes" in result.output

    result = invoke("sync", "--yes")
    assert result.exit_code == 0, result.output
    assert result.output == "Synced with origin.\n  sent:     lgbm/bjs-1\n"
    assert invoke("sync").output == "Already in sync with origin.\n"
    data = json.loads(invoke("sync", "--json").output)
    assert data["remote"] == "origin"
    assert data["sent"] == []


def test_sync_asks_on_first_upload(repo_path: Path, remote: Path, tty) -> None:
    (repo_path / "notes.txt").write_text("private\n")
    invoke("start", "a", "--motivation", "")
    result = invoke("sync", input="n\n")
    assert result.exit_code == 1
    assert "notes.txt" in result.output
    assert "Sync cancelled. Nothing was uploaded." in result.output
    result = invoke("sync", input="y\n")
    assert result.exit_code == 0, result.output
    assert "sent:     lgbm/bjs-1" in result.output


def test_sync_conflicts(
    repo_path: Path, tmp_path: Path, remote: Path, monkeypatch
) -> None:
    invoke("start", "a")
    invoke("done")
    invoke("sync", "--yes")
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(remote), str(other))
    git(other, "config", "user.name", "Other")
    git(other, "config", "user.email", "other@example.com")
    monkeypatch.chdir(other)
    assert (
        invoke("sync", "--yes").output
        == "Synced with origin.\n  received: lgbm/bjs-1\n"
    )
    invoke("edit", "bjs-1", "--verdict", "theirs")
    invoke("sync")
    monkeypatch.chdir(repo_path)
    invoke("edit", "bjs-1", "--verdict", "mine")

    result = invoke("sync")
    assert result.exit_code == 1
    assert "lgbm/bjs-1 verdict" in result.output
    assert "--prefer" in result.output

    monkeypatch.setattr("melog.cli.common.stdin_is_tty", lambda: True)
    result = invoke("sync", input="x\nr\n")
    assert result.exit_code == 0, result.output
    assert "Conflict in lgbm/bjs-1, verdict:" in result.output
    assert "local:   mine" in result.output
    assert "remote:  theirs" in result.output
    assert "resolved: lgbm/bjs-1 verdict (kept remote)" in result.output
    assert record_json()["verdict"] == "theirs"


def test_sync_errors(repo_path: Path, project: str) -> None:
    result = invoke("sync")
    assert result.exit_code == 1
    assert "no git remote named 'origin'" in result.output
    result = invoke("sync", "--prefer", "both")
    assert result.exit_code == 1
    assert "--prefer expects" in result.output


# Viewing


def test_lineage_command(repo_path: Path, project: str) -> None:
    assert "No experiments" in invoke("lineage").output
    invoke("done", "--new", "base", "--score", "CV=0.8")
    invoke("done", "--new", "child", "--parent", "bjs-1", "--score", "CV=0.9")
    result = invoke("lineage")
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "bjs-1     done  base   CV 0.8",
        "└─ bjs-2  done  child  CV 0.9",
    ]
    lines = invoke("lineage", "--score", "CV std").output.splitlines()
    assert lines[0].endswith("CV std -")
    mermaid = invoke("lineage", "--mermaid").output
    assert mermaid.startswith("graph LR\n")
    assert "n_bjs_1 --> n_bjs_2" in mermaid
    data = json.loads(invoke("lineage", "--json").output)
    assert [e["parents"] for e in data["experiments"]] == [[], ["bjs-1"]]
    assert data["score"] == "CV"


def test_export_command(repo_path: Path, project: str) -> None:
    invoke("start", "base", "--motivation", "why")
    (repo_path / "train.py").write_text("print('v2')\n")
    invoke("done", "--score", "CV=0.8")
    invoke("start", "wip")
    result = invoke("export")
    assert result.exit_code == 0, result.output
    assert result.output.startswith("# lgbm 실험 기록\n")
    assert "### bjs-1 · base" in result.output
    assert "### bjs-2 · wip" in result.output

    out = repo_path.parent / "export.md"
    result = invoke("export", "--status", "done", "--with-diffs", "-o", str(out))
    assert result.exit_code == 0, result.output
    assert result.output == f"Wrote 1 experiment of 'lgbm' to {out}.\n"
    text = out.read_text()
    assert "### bjs-2" not in text
    assert "train.py  +1 -1  (modified)" in text

    data = json.loads(invoke("export", "--json", "--with-diffs").output)
    assert [e["id"] for e in data["experiments"]] == ["bjs-1", "bjs-2"]
    assert "train.py" in data["experiments"][0]["diff_stat"]
    assert data["project"]["name"] == "lgbm"

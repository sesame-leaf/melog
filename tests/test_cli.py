import json
from pathlib import Path

from typer.testing import CliRunner

from melog.cli import app

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


def test_project_edit_requires_options(repo_path: Path) -> None:
    runner.invoke(app, ["init", "lgbm"])
    result = runner.invoke(app, ["project", "edit"])
    assert result.exit_code == 1
    assert "--scores" in result.output


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

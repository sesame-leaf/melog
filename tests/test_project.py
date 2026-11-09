import pytest

from melog.project import (
    DEFAULT_SCORES,
    AmbiguousProjectError,
    InvalidProjectNameError,
    InvalidSettingsError,
    NoProjectError,
    ProjectExistsError,
    ProjectNotFoundError,
    create_project,
    list_projects,
    load_project,
    overview_path,
    parse_settings,
    resolve_project,
    update_project,
)
from melog.store import Store


def test_create_with_defaults(store: Store) -> None:
    project = create_project(store, "lgbm")
    assert project.scores == list(DEFAULT_SCORES)
    assert project.horizontal is False
    view = store.view()
    assert load_project(view, "lgbm") == project
    assert view.read(overview_path("lgbm")) == ""


def test_create_with_custom_settings(store: Store) -> None:
    create_project(store, "bert", scores=[" AUC ", "public LB"], horizontal=True)
    project = load_project(store.view(), "bert")
    assert project.scores == ["AUC", "public LB"]
    assert project.horizontal is True


@pytest.mark.parametrize("name", ["", "LGBM", "-lgbm", "my model", "a/b", "_x", "é"])
def test_invalid_names(store: Store, name: str) -> None:
    with pytest.raises(InvalidProjectNameError):
        create_project(store, name)


@pytest.mark.parametrize("name", ["lgbm", "bert-v2", "model_1", "0"])
def test_valid_names(store: Store, name: str) -> None:
    create_project(store, name)


def test_duplicate_project(store: Store) -> None:
    create_project(store, "lgbm")
    with pytest.raises(ProjectExistsError):
        create_project(store, "lgbm")


@pytest.mark.parametrize("scores", [["CV", "CV"], ["CV", " "]])
def test_invalid_scores(store: Store, scores: list[str]) -> None:
    with pytest.raises(InvalidSettingsError):
        create_project(store, "lgbm", scores=scores)
    assert list_projects(store.view()) == []


def test_empty_score_list_is_allowed(store: Store) -> None:
    assert create_project(store, "lgbm", scores=[]).scores == []


def test_update_project(store: Store) -> None:
    create_project(store, "lgbm")
    update_project(store, "lgbm", horizontal=True)
    project = update_project(store, "lgbm", scores=["CV", "private LB"])
    assert project.scores == ["CV", "private LB"]
    assert project.horizontal is True
    assert load_project(store.view(), "lgbm") == project


def test_update_missing_project(store: Store) -> None:
    with pytest.raises(ProjectNotFoundError):
        update_project(store, "lgbm", horizontal=True)


def test_resolve_project(store: Store) -> None:
    with pytest.raises(NoProjectError):
        resolve_project(store.view(), None)
    create_project(store, "lgbm")
    assert resolve_project(store.view(), None) == "lgbm"
    create_project(store, "bert")
    with pytest.raises(AmbiguousProjectError) as excinfo:
        resolve_project(store.view(), None)
    assert excinfo.value.names == ["bert", "lgbm"]
    assert resolve_project(store.view(), "bert") == "bert"
    with pytest.raises(ProjectNotFoundError):
        resolve_project(store.view(), "xgb")


@pytest.mark.parametrize(
    "text",
    [
        "[unclosed",
        "- a list\n",
        "name: other\n",
        "name: lgbm\nscores: CV\n",
        "name: lgbm\nhorizontal: maybe\n",
    ],
)
def test_malformed_settings(text: str) -> None:
    with pytest.raises(InvalidSettingsError):
        parse_settings("lgbm", text)

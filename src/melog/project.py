"""melog projects: isolated sets of experiments and their settings."""

from __future__ import annotations

import io
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from melog.store import Store, StoreView

NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]*")
DEFAULT_SCORES = ("CV", "CV std", "public LB")


class ProjectError(Exception):
    """Base class for project errors."""


class InvalidProjectNameError(ProjectError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(
            f"invalid project name {name!r}: use lowercase letters, digits, '-' and "
            "'_', starting with a letter or digit"
        )


class ProjectExistsError(ProjectError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"melog project {name!r} already exists")


class ProjectNotFoundError(ProjectError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"melog project {name!r} does not exist")


class NoProjectError(ProjectError):
    def __init__(self) -> None:
        super().__init__("no melog project exists yet; create one with 'melog init'")


class AmbiguousProjectError(ProjectError):
    def __init__(self, names: list[str]) -> None:
        self.names = names
        super().__init__(
            f"several melog projects exist ({', '.join(names)}); "
            "choose one with --project"
        )


class InvalidSettingsError(ProjectError):
    """Project settings are malformed."""


@dataclass
class Project:
    name: str
    scores: list[str] = field(default_factory=lambda: list(DEFAULT_SCORES))
    horizontal: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "scores": list(self.scores),
            "horizontal": self.horizontal,
        }


def settings_path(name: str) -> str:
    return f"{name}/project.yaml"


def overview_path(name: str) -> str:
    return f"{name}/overview.md"


def validate_name(name: str) -> None:
    if not NAME_PATTERN.fullmatch(name):
        raise InvalidProjectNameError(name)


def normalize_scores(scores: Iterable[str]) -> list[str]:
    """Strip score names and reject empty or duplicate names."""
    result: list[str] = []
    for raw in scores:
        score = raw.strip()
        if not score:
            raise InvalidSettingsError("score names must not be empty")
        if score in result:
            raise InvalidSettingsError(f"duplicate score name {score!r}")
        result.append(score)
    return result


def _yaml() -> YAML:
    yaml = YAML()
    yaml.default_flow_style = False
    return yaml


def dump_settings(project: Project) -> str:
    buf = io.StringIO()
    _yaml().dump(project.to_dict(), buf)
    return buf.getvalue()


def parse_settings(name: str, text: str) -> Project:
    try:
        data = _yaml().load(text)
    except YAMLError as e:
        raise InvalidSettingsError(f"cannot parse settings of {name!r}: {e}") from e
    if not isinstance(data, dict):
        raise InvalidSettingsError(f"settings of {name!r} must be a mapping")
    if data.get("name") != name:
        raise InvalidSettingsError(
            f"settings of {name!r} name a different project: {data.get('name')!r}"
        )
    scores = data.get("scores", [])
    if not isinstance(scores, list) or not all(isinstance(s, str) for s in scores):
        raise InvalidSettingsError(f"scores of {name!r} must be a list of names")
    horizontal = data.get("horizontal", False)
    if not isinstance(horizontal, bool):
        raise InvalidSettingsError(f"horizontal of {name!r} must be true or false")
    return Project(name=name, scores=normalize_scores(scores), horizontal=horizontal)


def list_projects(view: StoreView) -> list[str]:
    return sorted(
        name for name in view.list_dir() if view.read(settings_path(name)) is not None
    )


def load_project(view: StoreView, name: str) -> Project:
    text = view.read(settings_path(name))
    if text is None:
        raise ProjectNotFoundError(name)
    return parse_settings(name, text)


def create_project(
    store: Store,
    name: str,
    scores: Iterable[str] | None = None,
    horizontal: bool = False,
) -> Project:
    validate_name(name)
    project = Project(
        name=name,
        scores=normalize_scores(scores if scores is not None else DEFAULT_SCORES),
        horizontal=horizontal,
    )

    def build(view: StoreView) -> tuple[dict[str, str | None], str]:
        if view.read(settings_path(name)) is not None:
            raise ProjectExistsError(name)
        changes: dict[str, str | None] = {
            settings_path(name): dump_settings(project),
            overview_path(name): "",
        }
        return changes, f"init project {name}"

    store.update(build)
    return project


def update_project(
    store: Store,
    name: str,
    scores: Iterable[str] | None = None,
    horizontal: bool | None = None,
) -> Project:
    new_scores = normalize_scores(scores) if scores is not None else None
    result: list[Project] = []

    def build(view: StoreView) -> tuple[dict[str, str | None], str]:
        project = load_project(view, name)
        if new_scores is not None:
            project.scores = new_scores
        if horizontal is not None:
            project.horizontal = horizontal
        result[:] = [project]
        changes: dict[str, str | None] = {settings_path(name): dump_settings(project)}
        return changes, f"update project {name}"

    store.update(build)
    return result[0]


def resolve_project(
    view: StoreView, name: str | None, active_projects: Iterable[str] = ()
) -> str:
    """Return the project to work on when the user may have omitted --project.

    ``active_projects`` are the projects of the experiments in progress in the
    current workspace; if they all belong to one project, that project is used.
    """
    if name is not None:
        load_project(view, name)
        return name
    names = list_projects(view)
    if not names:
        raise NoProjectError()
    if len(names) == 1:
        return names[0]
    active = sorted({p for p in active_projects if p in names})
    if len(active) == 1:
        return active[0]
    raise AmbiguousProjectError(names)


class OverviewConflictError(ProjectError):
    def __init__(self, name: str) -> None:
        super().__init__(
            f"the overview of {name!r} was changed elsewhere while you were editing it"
        )


def load_overview(view: StoreView, name: str) -> str:
    load_project(view, name)
    return view.read(overview_path(name)) or ""


def save_overview(store: Store, name: str, base: str, text: str) -> None:
    """Replace the overview, unless someone else changed it after ``base`` was read."""

    def build(view: StoreView) -> tuple[dict[str, str | None], str]:
        if load_overview(view, name) != base:
            raise OverviewConflictError(name)
        changes: dict[str, str | None] = {overview_path(name): text}
        return changes, f"edit overview of {name}"

    store.update(build)

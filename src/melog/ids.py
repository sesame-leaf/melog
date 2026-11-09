"""Experiment ids of the form ``{initials}-{sequence}``, such as ``bjs-12``."""

from __future__ import annotations

import re
from collections.abc import Iterable

from melog.git import Repo

INITIALS_KEY = "melog.initials"
INITIALS_PATTERN = re.compile(r"[a-z0-9]+")
ID_PATTERN = re.compile(r"([a-z0-9]+)-([1-9][0-9]*)")


class InvalidInitialsError(Exception):
    def __init__(self, initials: str) -> None:
        self.initials = initials
        super().__init__(
            f"invalid initials {initials!r}: use lowercase letters and digits only"
        )


def validate_initials(initials: str) -> None:
    if not INITIALS_PATTERN.fullmatch(initials):
        raise InvalidInitialsError(initials)


def get_initials(repo: Repo) -> str | None:
    initials = repo.config_get(INITIALS_KEY)
    if initials is not None:
        validate_initials(initials)
    return initials


def set_initials(repo: Repo, initials: str) -> None:
    validate_initials(initials)
    repo.run("config", "--global", INITIALS_KEY, initials)


def parse_id(exp_id: str) -> tuple[str, int] | None:
    match = ID_PATTERN.fullmatch(exp_id)
    if match is None:
        return None
    return match.group(1), int(match.group(2))


def next_id(initials: str, existing: Iterable[str]) -> str:
    """The id after the largest sequence that ``initials`` already used."""
    used = [
        parsed[1]
        for parsed in map(parse_id, existing)
        if parsed is not None and parsed[0] == initials
    ]
    return f"{initials}-{max(used, default=0) + 1}"


def id_sort_key(exp_id: str) -> tuple[str, int, str]:
    """Sort ids by initials, then by sequence as a number, so bjs-2 < bjs-10."""
    parsed = parse_id(exp_id)
    if parsed is None:
        return (exp_id, 0, exp_id)
    return (parsed[0], parsed[1], exp_id)

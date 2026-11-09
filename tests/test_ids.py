import pytest

from melog.git import Repo
from melog.ids import (
    InvalidInitialsError,
    get_initials,
    id_sort_key,
    next_id,
    parse_id,
    set_initials,
)


def test_parse_id() -> None:
    assert parse_id("bjs-12") == ("bjs", 12)
    assert parse_id("a1-1") == ("a1", 1)
    for invalid in ["bjs", "bjs-0", "bjs-01", "BJS-1", "bjs-1a", "-1"]:
        assert parse_id(invalid) is None


def test_next_id() -> None:
    assert next_id("bjs", []) == "bjs-1"
    assert next_id("bjs", ["bjs-1", "bjs-9", "bjs-10", "mj-30", "junk"]) == "bjs-11"
    assert next_id("mj", ["bjs-1", "mj-2"]) == "mj-3"


def test_id_sort_key_compares_numbers() -> None:
    ids = ["bjs-10", "bjs-2", "mj-1", "bjs-1"]
    assert sorted(ids, key=id_sort_key) == ["bjs-1", "bjs-2", "bjs-10", "mj-1"]


def test_initials_in_global_config(repo: Repo) -> None:
    assert get_initials(repo) is None
    set_initials(repo, "bjs")
    assert get_initials(repo) == "bjs"
    assert "melog.initials" not in repo.run("config", "--local", "--list")


@pytest.mark.parametrize("initials", ["", "BJS", "b-j", "b j", "준석"])
def test_invalid_initials(repo: Repo, initials: str) -> None:
    with pytest.raises(InvalidInitialsError):
        set_initials(repo, initials)

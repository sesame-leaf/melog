"""Editing a document in the editor until it can be applied."""

from __future__ import annotations

from collections.abc import Callable
from typing import NoReturn

import typer

from melog.cli.common import (
    FIXABLE_ERRORS,
    KNOWN_ERRORS,
    Context,
)
from melog.editor import (
    EditorError,
    run_editor,
    write_temp,
)


class EditCancelled(Exception):
    """The user emptied the document to cancel the edit."""


UNCHANGED = object()


def edit_until_valid(
    ctx: Context, initial: str, prefix: str, apply: Callable[[str], object]
) -> object:
    """Open ``initial`` in the editor and apply it, letting the user fix mistakes.

    Returns what ``apply`` returned, or UNCHANGED if the user changed nothing or
    cancelled. When the edit cannot be applied, the edited text is kept in a
    temporary file so that nothing the user typed is lost.
    """
    path = write_temp(initial, prefix)

    def give_up(message: str) -> NoReturn:
        typer.echo(f"error: {message}", err=True)
        typer.echo(f"Your edit is kept in {path}", err=True)
        raise typer.Exit(1)

    while True:
        try:
            run_editor(ctx.repo, path)
        except EditorError as e:
            give_up(str(e))
        text = path.read_text(encoding="utf-8")
        if text == initial:
            path.unlink()
            typer.echo("No changes.")
            return UNCHANGED
        try:
            result = apply(text)
        except EditCancelled:
            path.unlink()
            typer.echo("Edit cancelled.")
            return UNCHANGED
        except FIXABLE_ERRORS as e:
            typer.echo(f"error: {e}", err=True)
            if typer.confirm("Edit again?", default=True):
                continue
            give_up("the edit was not applied")
        except KNOWN_ERRORS as e:
            give_up(str(e))
        path.unlink()
        return result


def field_label(key: str) -> str:
    if key.startswith("scores."):
        return f"score '{key.removeprefix('scores.')}'"
    if key.startswith("body."):
        return key.removeprefix("body.")
    return key.replace("_", " ")

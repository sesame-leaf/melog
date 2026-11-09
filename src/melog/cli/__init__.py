"""Command line interface of melog."""

from melog.cli.app import app

# Importing the command modules registers their commands on the app, in the
# order they appear in --help.
from melog.cli import (  # noqa: E402, F401  isort: skip
    project_cmds,
    record_cmds,
    edit_cmds,
    read_cmds,
    code_cmds,
    sync_cmds,
)

__all__ = ["app"]

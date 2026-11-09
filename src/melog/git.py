"""Thin wrapper around the git command line."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

FALLBACK_NAME = "melog"
FALLBACK_EMAIL = "melog@localhost"


class GitError(Exception):
    """A git command failed."""

    def __init__(self, args: list[str], returncode: int, stderr: str) -> None:
        self.command = args
        self.returncode = returncode
        self.stderr = stderr.strip()
        super().__init__(
            f"git {' '.join(args)} failed with exit code {returncode}: {self.stderr}"
        )


class NotARepositoryError(Exception):
    """The given directory is not inside a git work tree."""


def _run(
    args: list[str],
    *,
    cwd: Path,
    input: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    full_env = {**os.environ, **env} if env else None
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            input=input.encode("utf-8") if input is not None else None,
            capture_output=True,
            env=full_env,
            check=False,
        )
    except FileNotFoundError as e:
        raise GitError(args, -1, "git executable not found") from e
    if proc.returncode != 0:
        raise GitError(args, proc.returncode, proc.stderr.decode("utf-8", "replace"))
    return proc.stdout.decode("utf-8", "replace")


@dataclass(frozen=True)
class Repo:
    """A git work tree, identified by its top-level directory."""

    root: Path

    @classmethod
    def discover(cls, path: Path | None = None) -> Repo:
        cwd = path or Path.cwd()
        try:
            out = _run(["rev-parse", "--show-toplevel"], cwd=cwd)
        except GitError as e:
            raise NotARepositoryError(str(cwd)) from e
        return cls(Path(out.strip()))

    def run(
        self,
        *args: str,
        input: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> str:
        """Run a git command and return its stdout."""
        return _run(list(args), cwd=self.root, input=input, env=env)

    def try_run(
        self,
        *args: str,
        input: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> str | None:
        """Run a git command and return its stdout, or None if it fails."""
        try:
            return self.run(*args, input=input, env=env)
        except GitError:
            return None

    def resolve(self, rev: str) -> str | None:
        """Return the object id that ``rev`` points to, or None if it does not exist."""
        out = self.try_run("rev-parse", "--verify", "--quiet", rev)
        return out.strip() if out else None

    def config_get(self, key: str) -> str | None:
        out = self.try_run("config", "--get", key)
        return out.strip() if out else None

    def git_dir(self) -> Path:
        """The git directory of this work tree. Linked worktrees each have their own."""
        return Path(self.run("rev-parse", "--absolute-git-dir").strip())

    def git_path(self, name: str) -> Path:
        """Absolute path of ``name`` inside the git directory, e.g. ``index``."""
        path = Path(self.run("rev-parse", "--git-path", name).strip())
        return path if path.is_absolute() else self.root / path

    def commit_time(self, rev: str) -> str:
        """Committer date of ``rev`` in strict ISO 8601 format."""
        return self.run("show", "-s", "--format=%cI", rev).strip()

    def refs(self, prefix: str) -> list[str]:
        out = self.run("for-each-ref", "--format=%(refname)", prefix)
        return out.split()

    def identity_env(self) -> dict[str, str]:
        """Environment that lets commit-tree work without a configured identity."""
        env: dict[str, str] = {}
        if not self.config_get("user.name"):
            env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = FALLBACK_NAME
        if not self.config_get("user.email"):
            env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = FALLBACK_EMAIL
        return env

"""melog storage: files kept on a dedicated git ref, outside the user's branches.

The store is a chain of commits on ``refs/melog/store``. Files are read and written
through git plumbing with a temporary index, so the user's work tree, index and
branches are never touched.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from melog.git import GitError, Repo

STORE_REF = "refs/melog/store"
MAX_RETRIES = 5

Changes = Mapping[str, str | None]
"""Paths mapped to new file contents, or to None to delete the file."""


class ConcurrentUpdateError(Exception):
    """The store was updated by someone else while a change was being prepared."""


@dataclass(frozen=True)
class StoreView:
    """A read-only view of the store at one commit."""

    repo: Repo
    commit: str | None

    def read(self, path: str) -> str | None:
        """Return the contents of ``path``, or None if it does not exist."""
        if self.commit is None:
            return None
        return self.repo.try_run("cat-file", "blob", f"{self.commit}:{path}")

    def list_dir(self, path: str = "") -> list[str]:
        """Return the names of the entries directly under ``path``."""
        if self.commit is None:
            return []
        treeish = f"{self.commit}:{path}" if path else f"{self.commit}^{{tree}}"
        out = self.repo.try_run("ls-tree", "--name-only", "-z", treeish)
        if not out:
            return []
        return [name for name in out.split("\0") if name]


class Store:
    def __init__(self, repo: Repo, ref: str = STORE_REF) -> None:
        self.repo = repo
        self.ref = ref

    def view(self) -> StoreView:
        return StoreView(self.repo, self.repo.resolve(f"{self.ref}^{{commit}}"))

    def commit(
        self,
        changes: Changes,
        message: str,
        base: StoreView,
        merge_with: str | None = None,
    ) -> str:
        """Apply ``changes`` on top of ``base`` and move the store ref to the result.

        With ``merge_with``, the new commit also has that commit as a parent, which
        records a merge with another history of the store.
        Raises ConcurrentUpdateError if the store no longer points at ``base``.
        Returns the new store commit, or the base commit if nothing changed.
        """
        tree = self._write_tree(changes, base.commit)
        if (
            merge_with is None
            and base.commit is not None
            and tree == self.repo.resolve(f"{base.commit}^{{tree}}")
        ):
            return base.commit

        parent_args = ["-p", base.commit] if base.commit else []
        if merge_with is not None:
            parent_args += ["-p", merge_with]
        new = self.repo.run(
            "commit-tree",
            tree,
            *parent_args,
            "-m",
            message,
            env=self.repo.identity_env(),
        ).strip()
        try:
            # An empty old value makes git refuse if the ref already exists.
            self.repo.run("update-ref", "-m", message, self.ref, new, base.commit or "")
        except GitError as e:
            if self.view().commit != base.commit:
                raise ConcurrentUpdateError(self.ref) from e
            raise
        return new

    def update(self, build: Callable[[StoreView], tuple[Changes, str]]) -> str:
        """Read the store, build changes from it, and commit them.

        ``build`` receives a consistent view and returns the changes and a commit
        message. It is called again on a fresh view if someone else updated the store
        in the meantime.
        """
        for _ in range(MAX_RETRIES):
            view = self.view()
            changes, message = build(view)
            try:
                return self.commit(changes, message, base=view)
            except ConcurrentUpdateError:
                continue
        raise ConcurrentUpdateError(self.ref)

    def _write_tree(self, changes: Changes, base_commit: str | None) -> str:
        with tempfile.TemporaryDirectory(prefix="melog-") as tmp:
            env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
            if base_commit is None:
                self.repo.run("read-tree", "--empty", env=env)
            else:
                self.repo.run("read-tree", base_commit, env=env)
            for path, content in changes.items():
                if content is None:
                    self.repo.run("update-index", "--force-remove", "--", path, env=env)
                    continue
                blob = self.repo.run(
                    "hash-object", "-w", "--stdin", input=content
                ).strip()
                self.repo.run(
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"100644,{blob},{path}",
                    env=env,
                )
            return self.repo.run("write-tree", env=env).strip()

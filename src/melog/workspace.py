"""Experiments in progress in one workspace, kept locally and never shared."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from melog.git import Repo

STATE_FILE = "melog-workspace.json"


@dataclass(frozen=True)
class Active:
    project: str
    id: str


class Workspace:
    def __init__(self, repo: Repo) -> None:
        # Each linked worktree has its own git directory, so the state stays local.
        self.path: Path = repo.git_dir() / STATE_FILE

    def active(self) -> list[Active]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        return [Active(item["project"], item["id"]) for item in data.get("active", [])]

    def add(self, project: str, exp_id: str) -> None:
        items = self.active()
        entry = Active(project, exp_id)
        if entry not in items:
            self._save([*items, entry])

    def remove(self, project: str, exp_id: str) -> None:
        self._save([a for a in self.active() if a != Active(project, exp_id)])

    def _save(self, items: list[Active]) -> None:
        data = {"active": [{"project": a.project, "id": a.id} for a in items]}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)


def rename_everywhere(repo: Repo, project: str, old_id: str, new_id: str) -> None:
    """Rename an experiment in the state of every worktree of this clone."""
    common = Path(repo.run("rev-parse", "--git-common-dir").strip())
    if not common.is_absolute():
        common = repo.root / common
    for path in [common / STATE_FILE, *(common / "worktrees").glob(f"*/{STATE_FILE}")]:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        changed = False
        for item in data.get("active", []):
            if item["project"] == project and item["id"] == old_id:
                item["id"] = new_id
                changed = True
        if changed:
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

"""Read-only inspection of existing workspaces.

Used by the bootstrap agent during exploration: 'what does this user already
have on this machine?' Used by `cobeta workspaces list` for users.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class WorkspaceSummary:
    name: str
    path: Path
    intent: str
    tags: list[str]
    machine: str
    stages: list[str]
    generated_at: Optional[str] = None


def inspect_existing_workspaces(workspaces_root: Path) -> list[WorkspaceSummary]:
    """Return summaries of all workspaces under `workspaces_root`.

    A directory is considered a workspace iff it contains `.cobeta.yaml`.
    """

    root = workspaces_root.expanduser()
    if not root.exists():
        return []

    summaries: list[WorkspaceSummary] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        record = child / ".cobeta.yaml"
        if not record.exists():
            continue
        try:
            data = yaml.safe_load(record.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        spec = data.get("spec", {}) or {}
        summaries.append(
            WorkspaceSummary(
                name=spec.get("name", child.name),
                path=child,
                intent=spec.get("intent", ""),
                tags=list(spec.get("tags") or []),
                machine=spec.get("machine", ""),
                stages=[s.get("id", "") for s in (spec.get("stages") or [])],
                generated_at=data.get("generated_at"),
            )
        )

    return summaries

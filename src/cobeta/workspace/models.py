"""Pydantic models for ICM workspaces.

These are the types the bootstrap agent must produce as its output. The
`generate_workspace` function below accepts a `WorkspaceSpec` and writes
files deterministically — no agent freedom past this point.
"""

from __future__ import annotations

import re
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


_KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_STAGE_ID = re.compile(r"^\d{2}-[a-z0-9]+(-[a-z0-9]+)*$")


class HandoffTarget(str, Enum):
    """Agent CLIs whose memory file we write to so they can pick up the workspace."""

    CLAUDE_CODE = "claude-code"
    CODEX = "codex"
    CURSOR = "cursor"
    OPENCODE = "opencode"


class ContextContract(BaseModel):
    """The Layer-2 contract for one ICM stage.

    `inputs` is a list of free-form rows the agent fills in via the inputs table.
    Each entry is just `(source, location, why)` strings — exactly what ICM's
    stage CONTEXT.md tables look like.
    """

    inputs: list[tuple[str, str, str]] = Field(default_factory=list)
    process: list[str] = Field(default_factory=list, description="Numbered process steps")
    outputs: list[str] = Field(default_factory=list, description="What this stage produces")
    audit: list[str] = Field(default_factory=list, description="Pre-exit checklist items")


class Stage(BaseModel):
    id: str = Field(..., description='Like "01-discover"; numeric prefix encodes order')
    name: str = Field(..., description='Short human label like "discover"')
    purpose: str = Field(..., description="One sentence — what this stage does")
    contract: ContextContract = Field(default_factory=ContextContract)

    @field_validator("id")
    @classmethod
    def _id_format(cls, v: str) -> str:
        if not _STAGE_ID.match(v):
            raise ValueError(
                f'stage id must match "NN-kebab-name" (got {v!r})'
            )
        return v

    @field_validator("name")
    @classmethod
    def _name_format(cls, v: str) -> str:
        if not _KEBAB.match(v):
            raise ValueError(f"stage name must be kebab-case (got {v!r})")
        return v


class WorkspaceSpec(BaseModel):
    """Agent-produced description of the workspace to generate.

    Validated by pydantic before the deterministic generator touches the disk.
    """

    name: str = Field(..., description='Kebab-case workspace name, e.g. "rope-longctx"')
    intent: str = Field(..., description="One sentence — what this workspace is for")
    tags: list[str] = Field(default_factory=list)
    machine: str = Field(..., description="Default execution machine label")
    stages: list[Stage] = Field(..., min_length=1)
    handoffs: list[HandoffTarget] = Field(
        default_factory=lambda: [HandoffTarget.CLAUDE_CODE],
        description="Which agent CLIs should get a handoff file in the workspace",
    )

    @field_validator("name")
    @classmethod
    def _name_kebab(cls, v: str) -> str:
        if not _KEBAB.match(v):
            raise ValueError(f"workspace name must be kebab-case (got {v!r})")
        return v

    @field_validator("tags")
    @classmethod
    def _tags_kebab(cls, v: list[str]) -> list[str]:
        bad = [t for t in v if not _KEBAB.match(t)]
        if bad:
            raise ValueError(f"tags must be kebab-case (got {bad!r})")
        return v

    @model_validator(mode="after")
    def _stage_order(self) -> "WorkspaceSpec":
        ids = [s.id for s in self.stages]
        if ids != sorted(ids):
            raise ValueError(f"stage ids must be sorted ascending; got {ids}")
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate stage ids: {ids}")
        return self


class Workspace(BaseModel):
    """A workspace as it actually exists on disk after generation."""

    spec: WorkspaceSpec
    path: Path
    generated: date = Field(default_factory=date.today)
    viking_memory_uri: str = Field(
        ..., description='e.g. "viking://agent/memories/rope-longctx/"'
    )
    handoff_files: list[Path] = Field(default_factory=list)

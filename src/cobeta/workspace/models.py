"""Pydantic models for ICM workspaces.

In cobeta's ICM model, every folder in a generated workspace is a `Cell` —
a unit with declared purpose, expected contents, and (optionally) declared
inputs and outputs that connect it to other cells. Cells can nest. This
generalizes the original ICM "stages" notion: stages were always linearly
numbered and temporal; cells are content-typed and form an arbitrary tree
with input/output edges between any two nodes.

The generator turns a `WorkspaceSpec` into a deterministic on-disk tree.
The agent's freedom ends at producing a valid spec; the generator and
hooks take over from there.
"""

from __future__ import annotations

import re
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


_KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class HandoffTarget(str, Enum):
    """Agent CLIs whose memory file we write to so they can pick up the workspace."""

    CLAUDE_CODE = "claude-code"
    CODEX = "codex"
    CURSOR = "cursor"
    OPENCODE = "opencode"


class CellInput(BaseModel):
    """Where this cell pulls material from."""

    source: str = Field(
        ...,
        description=(
            'Path or URI. Examples: "../data/processed/", '
            '"viking://resources/concepts/rope", "../sibling-cell/output.md"'
        ),
    )
    why: str = Field(..., description="One sentence explaining why this input matters")


class CellOutput(BaseModel):
    """What this cell is supposed to produce."""

    name: str = Field(..., description="Filename or sub-directory pattern")
    purpose: str = Field(..., description="What this output is for")


class Cell(BaseModel):
    """One folder in the workspace tree.

    Every folder cobeta generates IS a Cell. The cell's CONTEXT.md (if
    `has_context_md`) declares purpose / inputs / outputs / expected_structure
    so the agent CLI working in this folder knows what belongs and what doesn't.
    """

    name: str = Field(
        ...,
        description=(
            "Folder name. kebab-case. Numeric prefix (NN-) optional and only "
            'when ordering inside the parent matters (e.g. "01-intro.tex").'
        ),
    )
    purpose: str = Field(..., description="One-line purpose of this cell")
    expected_structure: str = Field(
        default="",
        description=(
            "Natural-language hint about what's inside. e.g. "
            '"each subdir is exp-NNN-<slug>" or "free-form *.md notes"'
        ),
    )
    inputs: list[CellInput] = Field(default_factory=list)
    outputs: list[CellOutput] = Field(default_factory=list)
    has_context_md: bool = Field(
        default=True,
        description=(
            "False for terminal storage dirs that don't need a contract "
            "(e.g. paper/figures/source-data/)"
        ),
    )
    sub_cells: list["Cell"] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name_format(cls, v: str) -> str:
        if not _KEBAB.match(v):
            raise ValueError(f"cell name must be kebab-case (got {v!r})")
        return v


# Self-reference rebuild for pydantic v2
Cell.model_rebuild()


class MemorySection(BaseModel):
    """One slice of viking memory the workspace plans to use.

    A workspace usually has one section per top-level cell (so that working
    in `paper/` queries `viking://agent/memories/<ws>/cells/paper/` only) plus
    a couple of cross-cutting ones (decisions, timeline).
    """

    uri: str = Field(..., description='Viking URI, e.g. "viking://agent/memories/<ws>/cells/paper/"')
    purpose: str = Field(..., description="What kind of memory belongs here")
    write_pattern: str = Field(
        default="as-needed",
        description=(
            'When to write: "as-needed" | "per-decision" | "per-day" | '
            '"per-experiment" | "per-handoff"'
        ),
    )


class WorkspaceSpec(BaseModel):
    """Agent-produced description of the workspace to generate.

    Validated by pydantic before the deterministic generator touches the disk.
    """

    name: str = Field(..., description='Kebab-case workspace name, e.g. "rope-longctx"')
    intent: str = Field(..., description="One sentence — what this workspace is for")
    tags: list[str] = Field(default_factory=list)
    machine: str = Field(..., description="Default execution machine label")
    cells: list[Cell] = Field(
        default_factory=list,
        description=(
            "Top-level cell tree. Empty = workspace has only CONTEXT.md and no "
            "inner structure (rare; allowed for pure scratchpads)."
        ),
    )
    memory_sections: list[MemorySection] = Field(default_factory=list)
    handoffs: list[HandoffTarget] = Field(
        default_factory=lambda: [HandoffTarget.CLAUDE_CODE]
    )
    rationale: str = Field(
        default="",
        description=(
            "One paragraph: WHY this layout for this project. Should reference "
            "user's existing project conventions (from viking inventory) when "
            "possible. Read by humans during workspace review."
        ),
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
    def _unique_top_cell_names(self) -> "WorkspaceSpec":
        names = [c.name for c in self.cells]
        if len(set(names)) != len(names):
            raise ValueError(f"top-level cell names must be unique; got {names}")
        return self


class Workspace(BaseModel):
    """A workspace as it actually exists on disk after generation."""

    spec: WorkspaceSpec
    path: Path
    generated: date = Field(default_factory=date.today)
    viking_memory_uri: str = Field(
        ..., description='Root URI for this workspace, e.g. "viking://agent/memories/rope-longctx/"'
    )
    handoff_files: list[Path] = Field(default_factory=list)

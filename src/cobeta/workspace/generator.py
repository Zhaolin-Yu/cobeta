"""Deterministic workspace generator.

Given a `WorkspaceSpec` (validated by pydantic), recursively materialize the
cell tree on disk. Each cell becomes a folder with a `CONTEXT.md` (unless
`has_context_md=False`); sub_cells become nested folders.

This is **pure deterministic code**. No LLM. The agent's job ends at producing
a valid spec; this code takes over from there.
"""

from __future__ import annotations

from datetime import date, datetime
from importlib.resources import files
from pathlib import Path
from typing import Optional

import yaml
from jinja2 import Environment, FunctionLoader, StrictUndefined

from .. import __version__ as cobeta_version
from .handoff import write_handoff_files
from .models import Cell, Workspace, WorkspaceSpec


def _load_template(name: str) -> str:
    return (files("cobeta.schemas") / name).read_text(encoding="utf-8")


def _env() -> Environment:
    return Environment(
        loader=FunctionLoader(lambda n: _load_template(n)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )


def _create_cell(parent_dir: Path, cell: Cell, env: Environment) -> None:
    """Create one cell's folder + CONTEXT.md, then recurse into sub-cells."""
    cell_dir = parent_dir / cell.name
    cell_dir.mkdir(parents=True, exist_ok=True)

    if cell.has_context_md:
        ctx = env.get_template("cell_context.md.j2").render(cell=cell)
        (cell_dir / "CONTEXT.md").write_text(ctx, encoding="utf-8")
    else:
        # Non-contract terminal storage — just keep the dir tracked
        (cell_dir / ".gitkeep").write_text("")

    for sub in cell.sub_cells:
        _create_cell(cell_dir, sub, env)


def generate_workspace(
    spec: WorkspaceSpec,
    workspaces_root: Path,
    *,
    overwrite: bool = False,
) -> Workspace:
    """Materialize a WorkspaceSpec on disk under `workspaces_root/<spec.name>/`.

    Raises FileExistsError if the target exists and `overwrite=False`.
    """
    target = workspaces_root.expanduser() / spec.name
    if target.exists() and not overwrite:
        raise FileExistsError(f"workspace path already exists: {target}")

    target.mkdir(parents=True, exist_ok=overwrite)

    viking_memory_uri = f"viking://agent/memories/{spec.name}/"

    env = _env()

    # ---- Workspace-level CONTEXT.md (Layer 1) ----
    workspace_ctx = env.get_template("workspace_context.md.j2").render(
        spec=spec,
        generated=date.today().isoformat(),
        viking_memory_uri=viking_memory_uri,
    )
    (target / "CONTEXT.md").write_text(workspace_ctx, encoding="utf-8")

    # ---- Recursive cell tree ----
    for cell in spec.cells:
        _create_cell(target, cell, env)

    # ---- Audit record ----
    audit = {
        "schema_version": 2,  # bumped: cells replaced stages
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "cobeta_version": cobeta_version,
        "spec": spec.model_dump(mode="json"),
        "viking_memory_uri": viking_memory_uri,
    }
    (target / ".cobeta.yaml").write_text(
        yaml.safe_dump(audit, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )

    # ---- Build the Workspace object ----
    ws = Workspace(
        spec=spec,
        path=target,
        viking_memory_uri=viking_memory_uri,
    )

    # ---- Handoff files (CLAUDE.md, AGENTS.md, …) ----
    handoff_paths = write_handoff_files(ws)
    ws.handoff_files = handoff_paths

    return ws

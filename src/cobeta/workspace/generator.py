"""Deterministic workspace generator.

Given a `WorkspaceSpec` (validated by pydantic) and a target root directory,
produce the full ICM workspace tree on disk: workspace `CONTEXT.md`, per-stage
`CONTEXT.md`, output / references directories, audit record, handoff files.

This is **pure deterministic code**. No LLM. No agent freedom. The agent's job
ends when it produces the WorkspaceSpec; this code takes over from there.
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
from .models import Workspace, WorkspaceSpec


def _load_template(name: str) -> str:
    return (files("cobeta.schemas") / name).read_text(encoding="utf-8")


def _env() -> Environment:
    return Environment(
        loader=FunctionLoader(lambda n: _load_template(n)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )


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

    # ---- Per-stage scaffolding (Layer 2 + dirs for Layer 3/4) ----
    for stage in spec.stages:
        sd = target / "stages" / stage.id
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "references").mkdir(exist_ok=True)
        (sd / "output").mkdir(exist_ok=True)
        (sd / "references" / ".gitkeep").write_text("")
        (sd / "output" / ".gitkeep").write_text("")
        ctx_text = env.get_template("stage_context.md.j2").render(stage=stage)
        (sd / "CONTEXT.md").write_text(ctx_text, encoding="utf-8")

    # ---- Workspace-wide references dir ----
    (target / "references").mkdir(exist_ok=True)
    (target / "references" / ".gitkeep").write_text("")

    # ---- Audit record ----
    audit = {
        "schema_version": 1,
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

"""Verify a generated workspace has the expected cell-tree shape on disk.

Catches regressions in the generator: missing CONTEXT.md at workspace level,
missing per-cell CONTEXT.md, missing audit record, schema mismatches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class WorkspaceValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _validate_cell_tree(parent_path: Path, cells: list[dict], errors: list[str], path_prefix: str = "") -> None:
    """Recursively check that on-disk dirs match the spec's cell tree."""
    for cell in cells:
        name = cell.get("name")
        if not name:
            errors.append(f"{path_prefix}cell missing name in audit record")
            continue
        cell_dir = parent_path / name
        rel = f"{path_prefix}{name}/"
        if not cell_dir.is_dir():
            errors.append(f"missing cell dir: {rel}")
            continue
        if cell.get("has_context_md", True) and not (cell_dir / "CONTEXT.md").exists():
            errors.append(f"missing {rel}CONTEXT.md")
        sub = cell.get("sub_cells") or []
        if sub:
            _validate_cell_tree(cell_dir, sub, errors, path_prefix=rel)


def validate_workspace_on_disk(workspace_path: Path) -> WorkspaceValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    if not workspace_path.is_dir():
        return WorkspaceValidationResult(ok=False, errors=[f"not a directory: {workspace_path}"])

    for req in ("CONTEXT.md", ".cobeta.yaml"):
        if not (workspace_path / req).exists():
            errors.append(f"missing {req}")

    audit_path = workspace_path / ".cobeta.yaml"
    if audit_path.exists():
        try:
            audit = yaml.safe_load(audit_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            errors.append(f".cobeta.yaml unparseable: {e}")
            audit = {}
        for key in ("schema_version", "generated_at", "spec", "viking_memory_uri"):
            if key not in audit:
                errors.append(f".cobeta.yaml missing key: {key}")
        spec = audit.get("spec") or {}
        cells = spec.get("cells") or []
        _validate_cell_tree(workspace_path, cells, errors)

    return WorkspaceValidationResult(ok=not errors, errors=errors, warnings=warnings)

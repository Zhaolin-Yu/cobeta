"""Verify a generated workspace has the expected ICM-shape on disk.

Catches regressions in the generator: missing CONTEXT.md, missing stage dirs,
empty audit record, etc.
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


def validate_workspace_on_disk(workspace_path: Path) -> WorkspaceValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    if not workspace_path.is_dir():
        return WorkspaceValidationResult(ok=False, errors=[f"not a directory: {workspace_path}"])

    # Required top-level files
    for req in ("CONTEXT.md", ".cobeta.yaml"):
        if not (workspace_path / req).exists():
            errors.append(f"missing {req}")

    # Audit record must be parseable and have minimum keys
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
        for stage in spec.get("stages") or []:
            sid = stage.get("id")
            if not sid:
                errors.append("audit spec contains stage without id")
                continue
            sd = workspace_path / "stages" / sid
            if not sd.is_dir():
                errors.append(f"missing stage dir: stages/{sid}")
                continue
            if not (sd / "CONTEXT.md").exists():
                errors.append(f"missing stages/{sid}/CONTEXT.md")
            if not (sd / "output").is_dir():
                errors.append(f"missing stages/{sid}/output/")
            if not (sd / "references").is_dir():
                errors.append(f"missing stages/{sid}/references/")

    # references/ at workspace level
    if not (workspace_path / "references").is_dir():
        warnings.append("workspace-wide references/ dir absent")

    return WorkspaceValidationResult(ok=not errors, errors=errors, warnings=warnings)

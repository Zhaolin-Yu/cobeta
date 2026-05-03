"""Tests for the deterministic workspace generator."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cobeta.workspace import generate_workspace
from cobeta.workspace.models import HandoffTarget, Stage, WorkspaceSpec
from cobeta.hooks import check_handoff_files, validate_workspace_on_disk


def _spec(name="demo", **overrides) -> WorkspaceSpec:
    base = dict(
        name=name,
        intent="smoke-test the generator",
        tags=["wip"],
        machine="aim-patho",
        stages=[
            Stage(id="01-discover", name="discover", purpose="Survey"),
            Stage(id="02-execute", name="execute", purpose="Build"),
            Stage(id="03-integrate", name="integrate", purpose="Promote"),
        ],
        handoffs=[HandoffTarget.CLAUDE_CODE, HandoffTarget.CODEX],
    )
    base.update(overrides)
    return WorkspaceSpec(**base)


def test_generates_full_tree(tmp_path: Path) -> None:
    spec = _spec()
    ws = generate_workspace(spec, tmp_path)

    assert ws.path == tmp_path / "demo"
    assert (ws.path / "CONTEXT.md").exists()
    assert (ws.path / ".cobeta.yaml").exists()
    assert (ws.path / "references" / ".gitkeep").exists()
    for stage_id in ("01-discover", "02-execute", "03-integrate"):
        assert (ws.path / "stages" / stage_id / "CONTEXT.md").exists()
        assert (ws.path / "stages" / stage_id / "output").is_dir()
        assert (ws.path / "stages" / stage_id / "references").is_dir()


def test_handoff_files_written(tmp_path: Path) -> None:
    spec = _spec()
    ws = generate_workspace(spec, tmp_path)
    expected_relpaths = {"CLAUDE.md", "AGENTS.md"}
    assert {p.name for p in ws.handoff_files} == expected_relpaths


def test_audit_record_complete(tmp_path: Path) -> None:
    spec = _spec()
    ws = generate_workspace(spec, tmp_path)
    audit = yaml.safe_load((ws.path / ".cobeta.yaml").read_text())
    assert audit["schema_version"] == 1
    assert audit["spec"]["name"] == "demo"
    assert audit["viking_memory_uri"] == "viking://agent/memories/demo/"
    assert {s["id"] for s in audit["spec"]["stages"]} == {
        "01-discover",
        "02-execute",
        "03-integrate",
    }


def test_validation_passes_after_generation(tmp_path: Path) -> None:
    spec = _spec()
    ws = generate_workspace(spec, tmp_path)
    result = validate_workspace_on_disk(ws.path)
    assert result.ok, result.errors


def test_handoff_check_passes(tmp_path: Path) -> None:
    spec = _spec()
    ws = generate_workspace(spec, tmp_path)
    result = check_handoff_files(ws.path, ws.handoff_files)
    assert result.ok, (result.missing_files, result.incomplete_files)


def test_collision_raises(tmp_path: Path) -> None:
    spec = _spec()
    generate_workspace(spec, tmp_path)
    with pytest.raises(FileExistsError):
        generate_workspace(spec, tmp_path)


def test_overwrite_works(tmp_path: Path) -> None:
    spec = _spec()
    generate_workspace(spec, tmp_path)
    ws2 = generate_workspace(spec, tmp_path, overwrite=True)
    assert ws2.path.exists()


def test_kebab_validation() -> None:
    with pytest.raises(Exception):
        WorkspaceSpec(
            name="Bad_Name",
            intent="x",
            tags=[],
            machine="m",
            stages=[Stage(id="01-x", name="x", purpose="x")],
        )


def test_stage_id_validation() -> None:
    with pytest.raises(Exception):
        Stage(id="1-bad", name="x", purpose="x")


def test_stage_order_validation() -> None:
    with pytest.raises(Exception):
        WorkspaceSpec(
            name="ok-name",
            intent="x",
            tags=[],
            machine="m",
            stages=[
                Stage(id="02-b", name="b", purpose="x"),
                Stage(id="01-a", name="a", purpose="x"),
            ],
        )

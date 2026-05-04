"""Tests for the deterministic workspace generator (Cell-tree model)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cobeta.workspace import generate_workspace
from cobeta.workspace.models import (
    Cell,
    CellInput,
    CellOutput,
    HandoffTarget,
    MemorySection,
    WorkspaceSpec,
)
from cobeta.hooks import check_handoff_files, validate_workspace_on_disk


def _spec(name="demo", **overrides) -> WorkspaceSpec:
    base = dict(
        name=name,
        intent="smoke-test the generator",
        tags=["wip"],
        machine="aim-patho",
        cells=[
            Cell(name="src", purpose="code"),
            Cell(name="notes", purpose="free-form thinking"),
            Cell(
                name="paper",
                purpose="writeup",
                sub_cells=[
                    Cell(name="figures", purpose="generated plots"),
                    Cell(name="sections", purpose="ordered chapters",
                         expected_structure="NN-name.tex"),
                ],
            ),
        ],
        memory_sections=[
            MemorySection(uri="viking://agent/memories/demo/cells/src/", purpose="src notes"),
            MemorySection(uri="viking://agent/memories/demo/decisions/", purpose="cross-cell decisions"),
        ],
        handoffs=[HandoffTarget.CLAUDE_CODE, HandoffTarget.CODEX],
        rationale="standard layout for a paper-with-code project",
    )
    base.update(overrides)
    return WorkspaceSpec(**base)


def test_generates_full_tree(tmp_path: Path) -> None:
    spec = _spec()
    ws = generate_workspace(spec, tmp_path)

    assert ws.path == tmp_path / "demo"
    assert (ws.path / "CONTEXT.md").exists()
    assert (ws.path / ".cobeta.yaml").exists()
    # Top-level cells
    for cell_name in ("src", "notes", "paper"):
        assert (ws.path / cell_name).is_dir()
        assert (ws.path / cell_name / "CONTEXT.md").exists()
    # Nested sub-cells
    assert (ws.path / "paper" / "figures" / "CONTEXT.md").exists()
    assert (ws.path / "paper" / "sections" / "CONTEXT.md").exists()


def test_handoff_files_written(tmp_path: Path) -> None:
    spec = _spec()
    ws = generate_workspace(spec, tmp_path)
    assert {p.name for p in ws.handoff_files} == {"CLAUDE.md", "AGENTS.md"}


def test_audit_record_complete(tmp_path: Path) -> None:
    spec = _spec()
    ws = generate_workspace(spec, tmp_path)
    audit = yaml.safe_load((ws.path / ".cobeta.yaml").read_text())
    assert audit["schema_version"] == 2  # cells schema
    assert audit["spec"]["name"] == "demo"
    assert audit["viking_memory_uri"] == "viking://agent/memories/demo/"
    top_names = {c["name"] for c in audit["spec"]["cells"]}
    assert top_names == {"src", "notes", "paper"}
    paper_cell = next(c for c in audit["spec"]["cells"] if c["name"] == "paper")
    sub_names = {c["name"] for c in paper_cell["sub_cells"]}
    assert sub_names == {"figures", "sections"}


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


def test_empty_cells_workspace(tmp_path: Path) -> None:
    """A workspace with no cells (pure scratchpad) should still generate."""
    spec = _spec(cells=[], memory_sections=[])
    ws = generate_workspace(spec, tmp_path)
    assert (ws.path / "CONTEXT.md").exists()
    # No cell dirs
    assert not (ws.path / "src").exists()


def test_terminal_storage_cell_no_context_md(tmp_path: Path) -> None:
    spec = _spec(cells=[
        Cell(name="data", purpose="datasets",
             sub_cells=[Cell(name="raw", purpose="raw dumps", has_context_md=False)]),
    ], memory_sections=[])
    ws = generate_workspace(spec, tmp_path)
    assert (ws.path / "data" / "CONTEXT.md").exists()
    assert not (ws.path / "data" / "raw" / "CONTEXT.md").exists()
    assert (ws.path / "data" / "raw" / ".gitkeep").exists()


def test_inputs_outputs_render_in_cell_context(tmp_path: Path) -> None:
    spec = _spec(cells=[
        Cell(
            name="paper",
            purpose="writeup",
            inputs=[CellInput(source="../experiments/", why="results table")],
            outputs=[CellOutput(name="main.pdf", purpose="final deliverable")],
        ),
    ], memory_sections=[])
    ws = generate_workspace(spec, tmp_path)
    text = (ws.path / "paper" / "CONTEXT.md").read_text()
    assert "../experiments/" in text
    assert "main.pdf" in text


def test_kebab_validation() -> None:
    with pytest.raises(Exception):
        WorkspaceSpec(
            name="Bad_Name",
            intent="x",
            tags=[],
            machine="m",
            cells=[Cell(name="x", purpose="x")],
        )


def test_cell_name_validation() -> None:
    with pytest.raises(Exception):
        Cell(name="Bad_Name", purpose="x")


def test_unique_top_cell_names() -> None:
    with pytest.raises(Exception):
        WorkspaceSpec(
            name="ok",
            intent="x",
            tags=[],
            machine="m",
            cells=[
                Cell(name="dup", purpose="a"),
                Cell(name="dup", purpose="b"),
            ],
        )


def test_detect_installed_handoff_targets_runs() -> None:
    from cobeta.workspace.handoff import detect_installed_handoff_targets
    result = detect_installed_handoff_targets()
    assert isinstance(result, list)
    assert all(isinstance(t, HandoffTarget) for t in result)

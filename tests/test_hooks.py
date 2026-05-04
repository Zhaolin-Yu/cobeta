"""Tests for the validation hooks (Cell-tree model)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cobeta.hooks import check_handoff_files, lint_tags, validate_workspace_on_disk
from cobeta.memory import VikingClient
from cobeta.workspace import generate_workspace
from cobeta.workspace.models import Cell, HandoffTarget, WorkspaceSpec


def _spec(name="demo", tags=None, handoffs=None, cells=None) -> WorkspaceSpec:
    return WorkspaceSpec(
        name=name,
        intent="x",
        tags=tags or [],
        machine="aim-patho",
        cells=cells if cells is not None else [Cell(name="notes", purpose="thinking")],
        handoffs=handoffs or [HandoffTarget.CLAUDE_CODE],
    )


def test_validate_detects_missing_workspace_context(tmp_path: Path) -> None:
    spec = _spec()
    ws = generate_workspace(spec, tmp_path)
    (ws.path / "CONTEXT.md").unlink()
    result = validate_workspace_on_disk(ws.path)
    assert not result.ok
    assert any("CONTEXT.md" in e for e in result.errors)


def test_validate_detects_missing_cell_dir(tmp_path: Path) -> None:
    spec = _spec(cells=[Cell(name="notes", purpose="x"), Cell(name="src", purpose="y")])
    ws = generate_workspace(spec, tmp_path)
    import shutil
    shutil.rmtree(ws.path / "src")
    result = validate_workspace_on_disk(ws.path)
    assert not result.ok
    assert any("src" in e for e in result.errors)


def test_validate_detects_missing_nested_cell_context(tmp_path: Path) -> None:
    spec = _spec(cells=[
        Cell(name="paper", purpose="writeup",
             sub_cells=[Cell(name="figures", purpose="plots")]),
    ])
    ws = generate_workspace(spec, tmp_path)
    (ws.path / "paper" / "figures" / "CONTEXT.md").unlink()
    result = validate_workspace_on_disk(ws.path)
    assert not result.ok
    assert any("figures" in e for e in result.errors)


def test_handoff_check_detects_truncated_file(tmp_path: Path) -> None:
    spec = _spec()
    ws = generate_workspace(spec, tmp_path)
    (ws.path / "CLAUDE.md").write_text("oops, lost the content")
    result = check_handoff_files(ws.path, ws.handoff_files)
    assert not result.ok
    assert result.incomplete_files


def test_lint_tags_with_empty_vocab_flags_used_tags(tmp_path: Path) -> None:
    """When viking has no tag vocabulary stored, every used tag is undeclared."""
    spec = _spec(tags=["unknown-tag"])
    client = VikingClient(base_url="http://nowhere:9999", allow_stub=True, stub_dir=tmp_path / "viking-stub")
    result = lint_tags(spec, client)
    client.close()
    assert not result.ok
    assert "unknown-tag" in result.undeclared


def test_lint_tags_passes_when_declared(tmp_path: Path) -> None:
    spec = _spec(tags=["good-tag"])
    client = VikingClient(base_url="http://nowhere:9999", allow_stub=True, stub_dir=tmp_path / "viking-stub")
    client.write(
        "viking://meta/tags.yaml",
        "tags:\n  good-tag:\n    description: A test tag\n",
    )
    result = lint_tags(spec, client)
    client.close()
    assert result.ok, result.undeclared


def test_seeded_tags_idempotent(tmp_path: Path) -> None:
    """Setup wizard's tag-scaffold should not overwrite a curated vocabulary."""
    import yaml as _yaml
    from cobeta.config import (
        LLMProviderConfig,
        NodeConfig,
        NodeRole,
        VikingConfig,
    )
    from cobeta.setup.wizard import _seed_tags_yaml
    from rich.console import Console

    cfg = NodeConfig(
        role=NodeRole.CENTRAL,
        central_hostname="x",
        viking=VikingConfig(host="nowhere", port=9999, stub_dir=tmp_path / "stub"),
        llm=LLMProviderConfig(provider="none"),
        machine_label="x",
        workspaces_root=tmp_path / "ws",
    )
    from cobeta.memory import viking_client_for
    client = viking_client_for(cfg)
    client.write("viking://meta/tags.yaml", "tags:\n  custom-tag:\n    description: keep me\n")
    client.close()

    _seed_tags_yaml(cfg, Console(quiet=True))

    client = viking_client_for(cfg)
    doc = client.cat("viking://meta/tags.yaml", level="L2")
    client.close()
    data = _yaml.safe_load(doc.full)
    assert "custom-tag" in data["tags"]
    assert "wip" not in data["tags"]


def test_seeded_tags_writes_when_empty(tmp_path: Path) -> None:
    import yaml as _yaml
    from cobeta.config import (
        LLMProviderConfig,
        NodeConfig,
        NodeRole,
        VikingConfig,
    )
    from cobeta.setup.wizard import _seed_tags_yaml
    from cobeta.memory import viking_client_for
    from rich.console import Console

    cfg = NodeConfig(
        role=NodeRole.CENTRAL,
        central_hostname="x",
        viking=VikingConfig(host="nowhere", port=9999, stub_dir=tmp_path / "stub"),
        llm=LLMProviderConfig(provider="none"),
        machine_label="x",
        workspaces_root=tmp_path / "ws",
    )
    _seed_tags_yaml(cfg, Console(quiet=True))

    client = viking_client_for(cfg)
    doc = client.cat("viking://meta/tags.yaml", level="L2")
    client.close()
    data = _yaml.safe_load(doc.full)
    assert {"wip", "experiment", "reference", "shared"} <= set(data["tags"].keys())

"""Tests for the validation hooks."""

from __future__ import annotations

from pathlib import Path

import pytest

from cobeta.hooks import check_handoff_files, lint_tags, validate_workspace_on_disk
from cobeta.memory import VikingClient
from cobeta.workspace import generate_workspace
from cobeta.workspace.models import HandoffTarget, Stage, WorkspaceSpec


def _spec(name="demo", tags=None, handoffs=None) -> WorkspaceSpec:
    return WorkspaceSpec(
        name=name,
        intent="x",
        tags=tags or [],
        machine="aim-patho",
        stages=[Stage(id="01-go", name="go", purpose="do it")],
        handoffs=handoffs or [HandoffTarget.CLAUDE_CODE],
    )


def test_validate_detects_missing_context(tmp_path: Path) -> None:
    spec = _spec()
    ws = generate_workspace(spec, tmp_path)
    (ws.path / "CONTEXT.md").unlink()
    result = validate_workspace_on_disk(ws.path)
    assert not result.ok
    assert any("CONTEXT.md" in e for e in result.errors)


def test_validate_detects_missing_stage_dir(tmp_path: Path) -> None:
    spec = _spec()
    ws = generate_workspace(spec, tmp_path)
    import shutil
    shutil.rmtree(ws.path / "stages" / "01-go")
    result = validate_workspace_on_disk(ws.path)
    assert not result.ok
    assert any("stages/01-go" in e for e in result.errors)


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
    # Stub viking with empty store
    client = VikingClient(base_url="http://nowhere:9999", allow_stub=True, stub_dir=tmp_path / "viking-stub")
    result = lint_tags(spec, client)
    client.close()
    assert not result.ok
    assert "unknown-tag" in result.undeclared


def test_lint_tags_passes_when_declared(tmp_path: Path) -> None:
    """Write tags.yaml into the stub, then lint should pass."""

    spec = _spec(tags=["good-tag"])
    client = VikingClient(base_url="http://nowhere:9999", allow_stub=True, stub_dir=tmp_path / "viking-stub")
    client.write(
        "viking://meta/tags.yaml",
        "tags:\n  good-tag:\n    description: A test tag\n",
    )
    result = lint_tags(spec, client)
    client.close()
    assert result.ok, result.undeclared

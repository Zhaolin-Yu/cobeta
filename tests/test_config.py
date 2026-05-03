"""Tests for config loading/saving."""

from __future__ import annotations

from pathlib import Path

import pytest

from cobeta.config import (
    LLMProviderConfig,
    NodeConfig,
    NodeRole,
    VikingConfig,
    load_node_config,
    save_node_config,
)


def _cfg(**overrides) -> NodeConfig:
    base = dict(
        role=NodeRole.CENTRAL,
        central_hostname="aim-patho",
        viking=VikingConfig(host="localhost", port=7799),
        llm=LLMProviderConfig(provider="anthropic", model="claude-sonnet-4-6"),
        machine_label="aim-patho",
    )
    base.update(overrides)
    return NodeConfig(**base)


def test_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    cfg = _cfg(workspaces_root=tmp_path / "ws")
    save_node_config(cfg, p)
    loaded = load_node_config(p)
    assert loaded.role is NodeRole.CENTRAL
    assert loaded.central_hostname == "aim-patho"
    assert loaded.viking.base_url == "http://localhost:7799"
    assert loaded.llm.provider == "anthropic"


def test_node_role_inferred_from_central(tmp_path: Path) -> None:
    cfg = _cfg(role=NodeRole.NODE, viking=VikingConfig(host="aim-patho", port=7799))
    assert not cfg.is_central


def test_workspaces_root_expansion() -> None:
    cfg = _cfg(workspaces_root="~/cobeta")
    assert "~" not in str(cfg.workspaces_root)


def test_missing_config_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_node_config(tmp_path / "does-not-exist.yaml")

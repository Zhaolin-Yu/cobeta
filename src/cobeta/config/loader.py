"""Locate, load, and persist the per-node config file."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from .models import NodeConfig


def default_config_path() -> Path:
    """`$COBETA_CONFIG` if set, else `~/.cobeta/config.yaml`."""
    env = os.environ.get("COBETA_CONFIG")
    if env:
        return Path(env).expanduser()
    return Path("~/.cobeta/config.yaml").expanduser()


def load_node_config(path: Path | None = None) -> NodeConfig:
    p = path or default_config_path()
    if not p.exists():
        raise FileNotFoundError(
            f"No cobeta config at {p}. Run `cobeta install` to create one."
        )
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return NodeConfig.model_validate(data)


def save_node_config(cfg: NodeConfig, path: Path | None = None) -> Path:
    p = path or default_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = cfg.model_dump(mode="json")
    # paths back to strings for YAML readability
    data["workspaces_root"] = str(cfg.workspaces_root)
    with p.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)
    return p

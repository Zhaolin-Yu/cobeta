"""Pydantic models for per-node configuration.

A "node" is one cobeta installation on one machine. Every machine that runs
cobeta has exactly one ~/.cobeta/config.yaml describing its role and how it
reaches the central node.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class NodeRole(str, Enum):
    CENTRAL = "central"
    NODE = "node"


class VikingConfig(BaseModel):
    """How this node reaches OpenViking.

    On the central node, `host` is `localhost` and `port` is whatever the local
    server binds to. On a non-central node, `host` is the central node's
    Tailscale hostname.
    """

    host: str = Field(..., description="Hostname or IP where viking server listens")
    port: int = Field(default=7799, ge=1, le=65535)
    timeout_s: float = Field(default=10.0, gt=0)
    stub_dir: Path = Field(
        default=Path("~/.cobeta/viking-stub").expanduser(),
        description=(
            "Where the local-stub fallback stores its JSON when the real viking is "
            "unreachable. Per-node, never synced. Override for tests or alternate users."
        ),
    )

    @field_validator("stub_dir", mode="before")
    @classmethod
    def _expand_stub(cls, v):
        if isinstance(v, str):
            return Path(v).expanduser()
        if isinstance(v, Path):
            return v.expanduser()
        return v

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


class LLMProviderConfig(BaseModel):
    """How this node talks to an LLM. cobeta requires this — the bootstrap
    agent and the LLM scanner are the framework's primary surfaces.

    The canonical/required configuration is `openai-compatible`: a base URL
    and an env-var-housed API key. Any provider speaking the OpenAI chat
    completions schema works (OpenAI, MiMo, vLLM, Together, Groq, Ollama,
    LM Studio, OpenRouter, etc.).

    `anthropic` and `none` are kept as escape hatches for advanced users but
    the setup wizard does not offer them. `none` mode disables LLM-driven
    bootstrap and the LLM scanner; only `--interactive` bootstrap and
    `--heuristic` scan still work.
    """

    provider: Literal["anthropic", "openai", "openai-compatible", "none"] = "openai-compatible"
    model: Optional[str] = None
    api_key_env: str = Field(
        default="OPENAI_API_KEY",
        description="Name of the env var holding the API key. Never store the key in config.",
    )
    base_url: Optional[str] = Field(
        default=None,
        description=(
            "API base URL ending in /v1. REQUIRED for openai-compatible (your "
            "provider's endpoint, e.g. https://api.openai.com/v1, "
            "https://token-plan-sgp.xiaomimimo.com/v1, http://localhost:11434/v1 for ollama)."
        ),
    )

    @field_validator("model")
    @classmethod
    def _model_default(cls, v: Optional[str], info) -> Optional[str]:
        provider = info.data.get("provider", "openai-compatible")
        if v is None and provider == "anthropic":
            return "claude-sonnet-4-6"
        if v is None and provider in ("openai", "openai-compatible"):
            return "gpt-4o-mini"
        return v


class NodeConfig(BaseModel):
    """The whole ~/.cobeta/config.yaml schema for one node."""

    version: int = 1
    role: NodeRole
    central_hostname: str = Field(
        ...,
        description=(
            "Tailscale hostname of the central node. On the central node itself "
            "this should still be the externally-reachable hostname (peers use it)."
        ),
    )
    viking: VikingConfig
    llm: LLMProviderConfig = Field(default_factory=LLMProviderConfig)
    workspaces_root: Path = Field(
        default=Path("~/cobeta-workspaces").expanduser(),
        description="Where this node creates workspaces by default. Distinct from the framework checkout.",
    )
    machine_label: str = Field(
        ...,
        description="Short human-friendly label for this machine, e.g. 'aim-patho' or 'laptop-xps'.",
    )

    @field_validator("workspaces_root", mode="before")
    @classmethod
    def _expand(cls, v):
        if isinstance(v, str):
            return Path(v).expanduser()
        if isinstance(v, Path):
            return v.expanduser()
        return v

    @property
    def is_central(self) -> bool:
        return self.role is NodeRole.CENTRAL

"""Bootstrap entry points: interactive (no LLM) and Agent-driven (LLM)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import click

from ..config import NodeConfig
from ..llm.base import LLMProvider
from ..memory import VikingClient
from ..workspace import (
    Workspace,
    WorkspaceSpec,
    generate_workspace,
    inspect_existing_workspaces,
)
from ..workspace.models import HandoffTarget, Stage
from .agent import Agent, AgentResult
from .builtin_tools import bootstrap_toolset
from .prompts import BOOTSTRAP_SYSTEM_PROMPT


@dataclass
class BootstrapResult:
    workspace: Optional[Workspace]
    error: Optional[str] = None


# ---------- helpers shared by both modes ----------


_KEBAB_PUNCT = re.compile(r"[^a-z0-9]+")
_SLUG_STOPWORDS = {
    "a", "an", "the", "to", "for", "with", "from", "into", "and", "or", "of", "in", "on",
    "build", "make", "create", "do", "use", "using", "via", "by", "that", "this",
    "we", "i", "my", "our", "your", "their", "is", "are", "be", "been", "was",
    "new", "old", "some", "any", "all", "more", "less",
}


def _slugify(text: str, *, max_words: int = 4, max_chars: int = 32) -> str:
    raw = _KEBAB_PUNCT.sub(" ", text.lower()).strip()
    words = [w for w in raw.split() if w and w not in _SLUG_STOPWORDS and not w.isdigit()]
    if not words:
        words = raw.split() or ["workspace"]
    chosen: list[str] = []
    total = 0
    for w in words[:max_words]:
        if total + len(w) + len(chosen) > max_chars:
            break
        chosen.append(w)
        total += len(w)
    return "-".join(chosen) or "workspace"


def _ask(prompt: str, default: Optional[str] = None) -> str:
    if default is not None:
        return click.prompt(prompt, default=default)
    return click.prompt(prompt)


def _ask_bool(prompt: str, default: bool = True) -> bool:
    return click.confirm(prompt, default=default)


# ---------- interactive (no-LLM) mode ----------


def bootstrap_interactive(
    cfg: NodeConfig,
    viking: VikingClient,
    intent_seed: Optional[str] = None,
) -> BootstrapResult:
    """Step the user through workspace creation via plain CLI prompts."""
    import json

    click.secho("\ncobeta bootstrap — interactive mode", fg="cyan", bold=True)
    click.echo("(No LLM required. Type Ctrl-C to abort.)\n")

    intent = intent_seed or _ask("In one sentence, what is this workspace for?")
    click.echo("")

    suggested = _slugify(intent.split(".")[0])
    name = _ask(f"Workspace name (kebab-case)", default=suggested)
    machine = _ask("Default execution machine", default=cfg.machine_label)
    tags_raw = _ask("Tags (comma-separated, kebab-case; blank for none)", default="")
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()]

    existing = inspect_existing_workspaces(cfg.workspaces_root)
    if existing:
        click.echo("\nExisting workspaces on this machine (for reference):")
        for s in existing[-5:]:
            click.echo(f"  - {s.name:<24} stages={','.join(s.stages)}")
        click.echo("")

    click.echo("Stage definition. Default 3 stages: discover → execute → integrate.")
    if _ask_bool("Use the default 3-stage breakdown?", default=True):
        stages = [
            Stage(id="01-discover", name="discover", purpose="Survey, gather inputs, define success"),
            Stage(id="02-execute", name="execute", purpose="Do the actual work"),
            Stage(
                id="03-integrate",
                name="integrate",
                purpose="Promote outputs, record learnings to viking",
            ),
        ]
    else:
        stages = []
        n = int(_ask("How many stages?", default="3"))
        for i in range(1, n + 1):
            stage_name = _ask(f"Stage {i:02d} name (kebab-case)")
            purpose = _ask(f"Stage {i:02d} purpose (one sentence)")
            stages.append(Stage(id=f"{i:02d}-{stage_name}", name=stage_name, purpose=purpose))

    available_handoffs = [
        HandoffTarget.CLAUDE_CODE,
        HandoffTarget.CODEX,
        HandoffTarget.CURSOR,
        HandoffTarget.OPENCODE,
    ]
    click.echo("\nHandoff files write a directory description into the agent CLI's memory file.")
    chosen: list[HandoffTarget] = []
    for h in available_handoffs:
        if _ask_bool(f"  generate handoff for {h.value}?", default=(h == HandoffTarget.CLAUDE_CODE)):
            chosen.append(h)
    if not chosen:
        click.echo("(no handoffs selected — workspace will still work but no agent will know about it)")

    try:
        spec = WorkspaceSpec(
            name=name,
            intent=intent,
            tags=tags,
            machine=machine,
            stages=stages,
            handoffs=chosen or [HandoffTarget.CLAUDE_CODE],
        )
    except Exception as e:
        return BootstrapResult(workspace=None, error=f"spec validation failed: {e}")

    click.echo("\nFinal spec:")
    click.echo(json.dumps(spec.model_dump(mode="json"), indent=2, ensure_ascii=False))
    if not _ask_bool("\nLooks good — generate?", default=True):
        return BootstrapResult(workspace=None, error="user aborted")

    from ..hooks import lint_tags
    lint = lint_tags(spec, viking)
    if not lint.ok:
        click.secho(
            f"\nWarning: tags not declared in viking://meta/tags.yaml: {lint.undeclared}",
            fg="yellow",
        )
        if not _ask_bool("Generate anyway?", default=False):
            return BootstrapResult(workspace=None, error="tag_lint blocked")

    try:
        ws = generate_workspace(spec, cfg.workspaces_root)
    except Exception as e:
        return BootstrapResult(workspace=None, error=str(e))

    return BootstrapResult(workspace=ws)


# ---------- LLM-driven mode (Agent class) ----------


def bootstrap_with_llm(
    cfg: NodeConfig,
    viking: VikingClient,
    llm: LLMProvider,
    intent_seed: Optional[str] = None,
    max_turns: int = 20,
) -> BootstrapResult:
    """Run an Agent-class-driven bootstrap loop."""

    tools = bootstrap_toolset(
        cfg=cfg,
        viking=viking,
        ask_fn=lambda q: click.prompt(q, default="", show_default=False),
    )
    agent = Agent(
        model=llm,
        tools=tools,
        instructions=BOOTSTRAP_SYSTEM_PROMPT,
        max_turns=max_turns,
        echo_text=True,
    )
    seed_msg = (
        intent_seed
        if intent_seed
        else "Help me create a new workspace. Ask me a few questions."
    )
    result: AgentResult = agent.run(seed_msg)

    if not result.completed or result.terminal_payload is None:
        return BootstrapResult(
            workspace=None,
            error=result.error or "bootstrap did not complete",
        )

    payload = result.terminal_payload
    workspace_path = Path(payload["path"])

    # Reload from disk so we return a Workspace object
    import yaml
    audit = yaml.safe_load((workspace_path / ".cobeta.yaml").read_text(encoding="utf-8")) or {}
    spec = WorkspaceSpec.model_validate(audit.get("spec") or {})
    from ..workspace.handoff import _TARGET_MAP as _HANDOFF_MAP
    handoff_files = []
    for h in spec.handoffs:
        rel = _HANDOFF_MAP[h][1]
        p = workspace_path / rel
        if p.exists():
            handoff_files.append(p)
    ws = Workspace(
        spec=spec,
        path=workspace_path,
        viking_memory_uri=audit.get(
            "viking_memory_uri", f"viking://agent/memories/{spec.name}/"
        ),
        handoff_files=handoff_files,
    )
    return BootstrapResult(workspace=ws)

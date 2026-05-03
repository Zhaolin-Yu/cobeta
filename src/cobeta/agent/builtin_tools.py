"""Built-in tools the bootstrap agent uses.

Each tool is a self-contained class. Adding a new tool = adding one class
plus listing it in the agent's `tools=[...]` argument. No central dispatcher
to update.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from ..config import NodeConfig
from ..hooks import check_handoff_files, lint_tags, validate_workspace_on_disk
from ..memory import VikingClient
from ..workspace import (
    Workspace,
    WorkspaceSpec,
    generate_workspace,
    inspect_existing_workspaces,
)
from ..workspace.models import HandoffTarget, Stage
from .tool import Tool, ToolPermission


# ---------- read-only viking tools ----------


class VikingFindTool(Tool):
    name = "viking_find"
    description = "Semantic search over viking content. Read-only."
    permission = ToolPermission.READ
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "uri_prefix": {"type": "string", "default": "viking://"},
            "k": {"type": "integer", "default": 5},
        },
        "required": ["query"],
    }

    def __init__(self, viking: VikingClient):
        self.viking = viking

    def execute(self, query: str, uri_prefix: str = "viking://", k: int = 5) -> str:
        docs = self.viking.find(query, uri_prefix=uri_prefix, k=k)
        return json.dumps(
            [{"uri": d.uri, "abstract": d.abstract, "overview": d.overview[:500]} for d in docs]
        )


class VikingTreeTool(Tool):
    name = "viking_tree"
    description = "List viking URIs under a prefix. Read-only."
    permission = ToolPermission.READ
    input_schema = {
        "type": "object",
        "properties": {
            "uri": {"type": "string"},
            "depth": {"type": "integer", "default": 1},
        },
        "required": ["uri"],
    }

    def __init__(self, viking: VikingClient):
        self.viking = viking

    def execute(self, uri: str, depth: int = 1) -> str:
        return json.dumps(self.viking.tree(uri, depth=depth))


class VikingCatTool(Tool):
    name = "viking_cat"
    description = "Read content at a viking URI. Levels: L0 (abstract), L1 (overview), L2 (full)."
    permission = ToolPermission.READ
    input_schema = {
        "type": "object",
        "properties": {
            "uri": {"type": "string"},
            "level": {"type": "string", "enum": ["L0", "L1", "L2"], "default": "L1"},
        },
        "required": ["uri"],
    }

    def __init__(self, viking: VikingClient):
        self.viking = viking

    def execute(self, uri: str, level: str = "L1") -> str:
        doc = self.viking.cat(uri, level=level)
        if doc is None:
            return json.dumps({"error": f"no document at {uri}"})
        return json.dumps(
            {
                "uri": doc.uri,
                "abstract": doc.abstract,
                "overview": doc.overview,
                "full": doc.full if level == "L2" else None,
                "metadata": doc.metadata,
            }
        )


# ---------- existing-workspace inspection ----------


class ListExistingWorkspacesTool(Tool):
    name = "list_existing_workspaces"
    description = "List workspaces already present on this machine."
    permission = ToolPermission.READ
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, cfg: NodeConfig):
        self.cfg = cfg

    def execute(self) -> str:
        summaries = inspect_existing_workspaces(self.cfg.workspaces_root)
        return json.dumps(
            [
                {
                    "name": s.name,
                    "intent": s.intent,
                    "tags": s.tags,
                    "stages": s.stages,
                    "machine": s.machine,
                    "generated_at": s.generated_at,
                }
                for s in summaries
            ]
        )


# ---------- user interaction ----------


class AskUserTool(Tool):
    name = "ask_user"
    description = "Ask the human a question. They reply, you get the text back."
    permission = ToolPermission.USER_INPUT
    input_schema = {
        "type": "object",
        "properties": {"question": {"type": "string"}},
        "required": ["question"],
    }

    def __init__(self, ask_fn: Callable[[str], str]):
        self.ask_fn = ask_fn

    def execute(self, question: str) -> str:
        reply = self.ask_fn(question)
        return json.dumps({"reply": reply})


# ---------- terminal action ----------


class GenerateWorkspaceTool(Tool):
    name = "generate_workspace"
    description = (
        "Generate the workspace from a fully-specified WorkspaceSpec. "
        "Validates via pydantic and runs all post-generation hooks. "
        "On hook failure, returns errors to you so you can fix and retry."
    )
    permission = ToolPermission.TERMINAL
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "intent": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "machine": {"type": "string"},
            "stages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "purpose": {"type": "string"},
                    },
                    "required": ["id", "name", "purpose"],
                },
                "minItems": 1,
            },
            "handoffs": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["claude-code", "codex", "cursor", "opencode"],
                },
                "default": ["claude-code"],
            },
        },
        "required": ["name", "intent", "machine", "stages"],
    }

    def __init__(self, cfg: NodeConfig, viking: VikingClient):
        self.cfg = cfg
        self.viking = viking

    def execute(
        self,
        name: str,
        intent: str,
        machine: str,
        stages: list[dict[str, str]],
        tags: list[str] | None = None,
        handoffs: list[str] | None = None,
    ) -> str:
        try:
            spec = WorkspaceSpec(
                name=name,
                intent=intent,
                tags=tags or [],
                machine=machine,
                stages=[
                    Stage(id=s["id"], name=s["name"], purpose=s["purpose"]) for s in stages
                ],
                handoffs=[HandoffTarget(h) for h in (handoffs or ["claude-code"])],
            )
        except Exception as e:
            return json.dumps({"error": f"spec validation failed: {e}"})

        # Pre-flight: tag lint
        lint = lint_tags(spec, self.viking)
        if not lint.ok:
            return json.dumps(
                {
                    "error": "tag_lint failed",
                    "undeclared_tags": lint.undeclared,
                    "hint": (
                        "Either drop these tags from the spec or ask the user to add them "
                        "to viking://meta/tags.yaml first."
                    ),
                }
            )

        try:
            ws = generate_workspace(spec, self.cfg.workspaces_root)
        except Exception as e:
            return json.dumps({"error": f"generation failed: {e}"})

        # Post-flight hooks
        wsv = validate_workspace_on_disk(ws.path)
        hsv = check_handoff_files(ws.path, ws.handoff_files)
        if not wsv.ok or not hsv.ok:
            return json.dumps(
                {
                    "error": "post-generation hooks failed",
                    "validation_errors": wsv.errors,
                    "missing_handoffs": [str(p) for p in hsv.missing_files],
                    "incomplete_handoffs": [
                        {"path": str(p), "missing_keywords": kw}
                        for p, kw in hsv.incomplete_files
                    ],
                }
            )

        return json.dumps(
            {
                "ok": True,
                "path": str(ws.path),
                "viking_memory": ws.viking_memory_uri,
                "handoff_files": [str(p) for p in ws.handoff_files],
                "stages": [s.id for s in ws.spec.stages],
            }
        )


# ---------- preset bundles ----------


def bootstrap_toolset(
    cfg: NodeConfig, viking: VikingClient, ask_fn: Callable[[str], str]
) -> list[Tool]:
    """Return the canonical bootstrap-mode tool list.

    Read-only access to viking + filesystem inventory + ask_user + terminal
    generate_workspace. No write access to viking.
    """
    return [
        VikingFindTool(viking),
        VikingTreeTool(viking),
        VikingCatTool(viking),
        ListExistingWorkspacesTool(cfg),
        AskUserTool(ask_fn),
        GenerateWorkspaceTool(cfg, viking),
    ]

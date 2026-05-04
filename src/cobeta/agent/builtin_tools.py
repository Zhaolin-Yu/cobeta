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
from ..workspace.models import Cell, CellInput, CellOutput, HandoffTarget, MemorySection
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
                    "machine": s.machine,
                    "generated_at": s.generated_at,
                }
                for s in summaries
            ]
        )


class ReadUserInventoryTool(Tool):
    """Read per-project structural fingerprints from viking://user/inventory/projects/.

    The bootstrap agent calls this FIRST to learn what folder layouts the user
    typically uses across their existing projects, then proposes a layout for
    the new workspace that's consistent with their conventions.
    """

    name = "read_user_inventory"
    description = (
        "Read per-project structural fingerprints (top-level dirs, languages, "
        "deps, descriptions) from past scans. Use this BEFORE proposing a "
        "workspace layout to learn the user's conventions."
    )
    permission = ToolPermission.READ
    input_schema = {
        "type": "object",
        "properties": {
            "filter_lang": {
                "type": "string",
                "description": 'Optional: only return projects of this language (e.g. "python").',
            },
        },
    }

    def __init__(self, viking: VikingClient):
        self.viking = viking

    def execute(self, filter_lang: str = "") -> str:
        entries = self.viking.tree("viking://user/inventory/projects/", depth=2)
        out: list[dict] = []
        for uri in entries:
            doc = self.viking.cat(uri, level="L2")
            if doc is None or not doc.full:
                continue
            try:
                import yaml as _yaml
                data = _yaml.safe_load(doc.full) or {}
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            if filter_lang and filter_lang not in (data.get("languages") or []):
                continue
            out.append({"uri": uri, **data})
        return json.dumps(out)


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


# JSON-schema definition for a Cell, used inside generate_workspace.
# Pulled out for readability; matches the pydantic Cell model.
_CELL_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "kebab-case folder name"},
        "purpose": {"type": "string"},
        "expected_structure": {"type": "string", "default": ""},
        "inputs": {
            "type": "array",
            "default": [],
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["source", "why"],
            },
        },
        "outputs": {
            "type": "array",
            "default": [],
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "purpose": {"type": "string"},
                },
                "required": ["name", "purpose"],
            },
        },
        "has_context_md": {"type": "boolean", "default": True},
        "sub_cells": {
            "type": "array",
            "default": [],
            # Recursive: same shape as parent Cell. JSON schema doesn't easily
            # express recursion; LLMs handle this fine when the description
            # says "same shape as parent".
            "items": {"type": "object"},
            "description": "Sub-cells with the same Cell schema as the parent.",
        },
    },
    "required": ["name", "purpose"],
}


class GenerateWorkspaceTool(Tool):
    name = "generate_workspace"
    description = (
        "Generate the workspace from a fully-specified WorkspaceSpec. "
        "Pydantic-validates the spec, runs all post-generation hooks. "
        "On hook failure, returns errors to you so you can fix and retry. "
        "The 'cells' field is the workspace's folder tree — every folder "
        "is an ICM cell with its own purpose / inputs / outputs."
    )
    permission = ToolPermission.TERMINAL
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "intent": {"type": "string"},
            "rationale": {
                "type": "string",
                "description": (
                    "One paragraph: WHY this layout. Reference user's existing "
                    "project conventions (read via read_user_inventory) when "
                    "applicable."
                ),
                "default": "",
            },
            "tags": {"type": "array", "items": {"type": "string"}},
            "machine": {"type": "string"},
            "cells": {
                "type": "array",
                "items": _CELL_JSON_SCHEMA,
                "description": (
                    "Top-level folders. Each is a Cell. Cells can nest via "
                    "sub_cells (same schema). Use short content-typed names "
                    "(paper, data, src) — not numbered stages — unless the "
                    "user explicitly wants a linear pipeline."
                ),
                "default": [],
            },
            "memory_sections": {
                "type": "array",
                "default": [],
                "items": {
                    "type": "object",
                    "properties": {
                        "uri": {"type": "string"},
                        "purpose": {"type": "string"},
                        "write_pattern": {"type": "string", "default": "as-needed"},
                    },
                    "required": ["uri", "purpose"],
                },
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
        "required": ["name", "intent", "machine"],
    }

    def __init__(self, cfg: NodeConfig, viking: VikingClient):
        self.cfg = cfg
        self.viking = viking

    def execute(
        self,
        name: str,
        intent: str,
        machine: str,
        rationale: str = "",
        tags: list[str] | None = None,
        cells: list[dict] | None = None,
        memory_sections: list[dict] | None = None,
        handoffs: list[str] | None = None,
    ) -> str:
        try:
            spec = WorkspaceSpec(
                name=name,
                intent=intent,
                tags=tags or [],
                machine=machine,
                cells=[_dict_to_cell(c) for c in (cells or [])],
                memory_sections=[
                    MemorySection(**m) for m in (memory_sections or [])
                ],
                handoffs=[HandoffTarget(h) for h in (handoffs or ["claude-code"])],
                rationale=rationale,
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

        # Flatten the cell tree into a list of paths for the report
        def _flatten(cell: Cell, prefix: str = "") -> list[str]:
            here = f"{prefix}{cell.name}/"
            return [here] + [p for sub in cell.sub_cells for p in _flatten(sub, here)]

        all_cells: list[str] = []
        for c in ws.spec.cells:
            all_cells.extend(_flatten(c))

        return json.dumps(
            {
                "ok": True,
                "path": str(ws.path),
                "viking_memory": ws.viking_memory_uri,
                "handoff_files": [str(p) for p in ws.handoff_files],
                "cells": all_cells,
            }
        )


def _dict_to_cell(d: dict) -> Cell:
    """Recursively convert a dict (from agent JSON) into a Cell."""
    sub = [_dict_to_cell(s) for s in (d.get("sub_cells") or [])]
    return Cell(
        name=d["name"],
        purpose=d["purpose"],
        expected_structure=d.get("expected_structure", ""),
        inputs=[CellInput(**i) for i in (d.get("inputs") or [])],
        outputs=[CellOutput(**o) for o in (d.get("outputs") or [])],
        has_context_md=d.get("has_context_md", True),
        sub_cells=sub,
    )


# ---------- preset bundles ----------


def bootstrap_toolset(
    cfg: NodeConfig, viking: VikingClient, ask_fn: Callable[[str], str]
) -> list[Tool]:
    """Return the canonical bootstrap-mode tool list.

    Read-only access to viking + per-project inventory + filesystem inventory +
    ask_user + terminal generate_workspace. No write access to viking.
    """
    return [
        VikingFindTool(viking),
        VikingTreeTool(viking),
        VikingCatTool(viking),
        ListExistingWorkspacesTool(cfg),
        ReadUserInventoryTool(viking),
        AskUserTool(ask_fn),
        GenerateWorkspaceTool(cfg, viking),
    ]

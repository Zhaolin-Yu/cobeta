"""Tools the LLM scanner agent uses.

These wrap read-only filesystem operations + structured output collection.
The path-safety guard refuses any access outside the user-specified roots.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..agent.tool import Tool, ToolPermission


def _within_roots(p: Path, roots: list[Path]) -> bool:
    """True iff `p` resolves to inside one of `roots`."""
    try:
        rp = p.resolve()
    except (OSError, RuntimeError):
        return False
    for r in roots:
        rr = r.resolve()
        if rp == rr or rr in rp.parents:
            return True
    return False


class ListDirTool(Tool):
    name = "list_dir"
    description = (
        "List immediate contents of a directory (one level only). Returns "
        "files and subdirs separately, sorted alphabetically. Hidden entries "
        "are included so you can see .git/, .cobeta.yaml, etc."
    )
    permission = ToolPermission.READ
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    def __init__(self, allowed_roots: list[Path]):
        self.allowed_roots = [r.expanduser() for r in allowed_roots]

    def execute(self, path: str) -> str:
        p = Path(path).expanduser()
        if not _within_roots(p, self.allowed_roots):
            return json.dumps({"error": f"path outside allowed roots: {p}"})
        if not p.is_dir():
            return json.dumps({"error": f"not a directory: {p}"})
        try:
            files: list[str] = []
            dirs: list[str] = []
            with os.scandir(p) as it:
                for entry in it:
                    if entry.is_dir(follow_symlinks=False):
                        dirs.append(entry.name)
                    elif entry.is_file(follow_symlinks=False):
                        files.append(entry.name)
            return json.dumps(
                {"path": str(p), "files": sorted(files)[:200], "dirs": sorted(dirs)[:200]}
            )
        except (OSError, PermissionError) as e:
            return json.dumps({"error": str(e)})


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read first N bytes of a text file. Use for README.md, pyproject.toml, "
        "package.json, Cargo.toml, .git/config, .cobeta.yaml, and similar "
        "self-description files. Refuses files >1MB. Default max_bytes 8192."
    )
    permission = ToolPermission.READ
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "max_bytes": {"type": "integer", "default": 8192},
        },
        "required": ["path"],
    }

    def __init__(self, allowed_roots: list[Path], max_per_file: int = 32768):
        self.allowed_roots = [r.expanduser() for r in allowed_roots]
        self.max_per_file = max_per_file

    def execute(self, path: str, max_bytes: int = 8192) -> str:
        p = Path(path).expanduser()
        if not _within_roots(p, self.allowed_roots):
            return json.dumps({"error": f"path outside allowed roots: {p}"})
        if not p.is_file():
            return json.dumps({"error": f"not a file: {p}"})
        try:
            sz = p.stat().st_size
            if sz > 1024 * 1024:
                return json.dumps({"error": f"file > 1 MB ({sz}); refusing to read"})
            n = min(max_bytes, self.max_per_file)
            with p.open("r", encoding="utf-8", errors="replace") as f:
                content = f.read(n)
            return json.dumps({"path": str(p), "content": content, "size": sz, "truncated": sz > n})
        except (OSError, UnicodeError) as e:
            return json.dumps({"error": str(e)})


class SubmitProjectFingerprintTool(Tool):
    name = "submit_project_fingerprint"
    description = (
        "Record what you learned about ONE project. Call once per project-like "
        "directory you found. Accumulates into a list shown to you on submit_scan_report."
    )
    permission = ToolPermission.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute path to the project root"},
            "name": {"type": "string", "description": "Project name (from pyproject/package.json/dir)"},
            "description": {"type": "string", "description": "What this project IS, in one sentence"},
            "languages": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Languages used (python, rust, js, go, ...)",
            },
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Author-declared keywords from pyproject/package.json",
            },
            "dependencies": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Top-level dependencies",
            },
            "top_level_dirs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Names of immediate sub-dirs (skip .git, node_modules, .venv, etc.)",
            },
            "git_remote": {"type": "string", "description": "Origin URL with credentials stripped"},
            "notes": {
                "type": "string",
                "description": "Free-form observations about purpose, structure, anything noteworthy",
            },
        },
        "required": ["path", "name"],
    }

    def __init__(self, accumulator: list[dict[str, Any]]):
        self.accumulator = accumulator

    def execute(self, **kwargs: Any) -> str:
        self.accumulator.append(kwargs)
        return json.dumps({"recorded": kwargs.get("name"), "total_so_far": len(self.accumulator)})


class SubmitScanReportTool(Tool):
    name = "submit_scan_report"
    description = (
        "Submit the final scan report. Call ONCE after all per-project fingerprints. "
        "Terminates the scan."
    )
    permission = ToolPermission.TERMINAL
    input_schema = {
        "type": "object",
        "properties": {
            "suggested_tags": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tag": {"type": "string"},
                        "rationale": {"type": "string"},
                    },
                    "required": ["tag", "rationale"],
                },
                "description": "Curated tag vocabulary suggestions, kebab-case",
            },
            "layout_patterns": {
                "type": "object",
                "description": (
                    "Cross-project layout observations. Group projects by language/type and list "
                    'their dominant top-level dirs. Example: {"python_projects": ["src", "tests", "docs"], '
                    '"ml_projects": ["data", "experiments", "notebooks"]}'
                ),
                "additionalProperties": {"type": "array", "items": {"type": "string"}},
            },
            "inventory_summary": {
                "type": "string",
                "description": "One-paragraph human-readable summary of what was found",
            },
        },
        "required": ["suggested_tags", "inventory_summary"],
    }

    def execute(
        self,
        suggested_tags: list[dict[str, str]],
        inventory_summary: str,
        layout_patterns: dict[str, list[str]] | None = None,
    ) -> str:
        return json.dumps(
            {
                "ok": True,
                "suggested_tags": {t["tag"]: t["rationale"] for t in suggested_tags},
                "inventory_summary": inventory_summary,
                "layout_patterns": layout_patterns or {},
            }
        )

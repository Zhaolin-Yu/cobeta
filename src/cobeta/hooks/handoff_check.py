"""Verify the handoff files were written and contain the required keys.

This is the safety check that the deterministic handoff renderer didn't
silently emit broken or empty handoff files (which would leave the user with
a workspace that no agent CLI can pick up correctly).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass
class HandoffCheckResult:
    ok: bool
    missing_files: list[Path]
    incomplete_files: list[tuple[Path, list[str]]]  # (path, missing_keywords)


# Minimal keywords each handoff file MUST contain to count as valid.
_REQUIRED_KEYWORDS = {
    "CLAUDE.md": ["cobeta workspace", "Folder layout", "Memory plan"],
    "AGENTS.md": ["cobeta workspace", "Folder purpose", "Memory"],
    ".cursor/rules/cobeta.mdc": ["cobeta workspace"],
    ".opencode/opencode.json": ["cobeta_workspace", "cells", "viking_memory_root"],
}


def check_handoff_files(workspace_path: Path, expected: Iterable[Path]) -> HandoffCheckResult:
    missing: list[Path] = []
    incomplete: list[tuple[Path, list[str]]] = []

    for p in expected:
        if not p.exists():
            missing.append(p)
            continue
        # Match against the relative form regardless of OS path separator
        rel = str(p.relative_to(workspace_path)).replace("\\", "/")
        keywords = _REQUIRED_KEYWORDS.get(rel)
        if not keywords:
            continue
        text = p.read_text(encoding="utf-8")
        missing_kw = [k for k in keywords if k not in text]
        if missing_kw:
            incomplete.append((p, missing_kw))

    return HandoffCheckResult(
        ok=not missing and not incomplete,
        missing_files=missing,
        incomplete_files=incomplete,
    )

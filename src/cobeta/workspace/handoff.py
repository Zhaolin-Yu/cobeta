"""Render handoff files into a generated workspace.

A "handoff file" is the small markdown / json file we drop into the workspace
that tells whatever agent CLI the user opens (Claude Code, Codex, Cursor,
opencode) what the directory means: what each folder is for, what tags apply,
where the viking memory lives, what the stages are.

After cobeta writes these, our framework gets out of the way. The user's agent
CLI takes over.
"""

from __future__ import annotations

import shutil
from importlib.resources import files
from pathlib import Path

from jinja2 import Environment, FunctionLoader, StrictUndefined

from .. import __version__ as cobeta_version
from .models import HandoffTarget, Workspace


# Map a HandoffTarget to the binary name we'd find on PATH if the user has it.
_BINARY_FOR_TARGET: dict[HandoffTarget, str] = {
    HandoffTarget.CLAUDE_CODE: "claude",
    HandoffTarget.CODEX: "codex",
    HandoffTarget.CURSOR: "cursor",
    HandoffTarget.OPENCODE: "opencode",
}


def detect_installed_handoff_targets() -> list[HandoffTarget]:
    """Return the HandoffTargets whose CLI is installed on PATH.

    Used to default-check sensible handoffs at workspace generation time so
    the user isn't asked about CLIs they don't have.
    """
    return [t for t, binary in _BINARY_FOR_TARGET.items() if shutil.which(binary)]


# (HandoffTarget) → (template_filename, output_path_relative_to_workspace)
_TARGET_MAP: dict[HandoffTarget, tuple[str, str]] = {
    HandoffTarget.CLAUDE_CODE: ("handoff_claude.md.j2", "CLAUDE.md"),
    HandoffTarget.CODEX: ("handoff_codex.md.j2", "AGENTS.md"),
    HandoffTarget.CURSOR: ("handoff_cursor.mdc.j2", ".cursor/rules/cobeta.mdc"),
    HandoffTarget.OPENCODE: ("handoff_opencode.json.j2", ".opencode/opencode.json"),
}


def _load_template(name: str) -> str:
    return (files("cobeta.schemas") / name).read_text(encoding="utf-8")


def _env() -> Environment:
    return Environment(
        loader=FunctionLoader(lambda n: _load_template(n)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )


def write_handoff_files(ws: Workspace) -> list[Path]:
    """Render and write one handoff file per HandoffTarget in the workspace spec.

    Returns the list of file paths written. Idempotent — overwrites existing
    handoff files at the same paths.
    """

    env = _env()
    written: list[Path] = []
    ctx = {
        "spec": ws.spec,
        "viking_memory_uri": ws.viking_memory_uri,
        "cobeta_version": cobeta_version,
        "generated": ws.generated.isoformat(),
    }

    for target in ws.spec.handoffs:
        tpl_name, out_rel = _TARGET_MAP[target]
        tpl = env.get_template(tpl_name)
        text = tpl.render(**ctx)
        out_path = ws.path / out_rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        written.append(out_path)

    return written

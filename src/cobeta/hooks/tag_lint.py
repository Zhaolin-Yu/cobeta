"""Verify that every tag used by a WorkspaceSpec is declared in the controlled
vocabulary stored in viking://meta/tags.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..memory import VikingClient
from ..workspace.models import WorkspaceSpec


@dataclass
class TagLintResult:
    ok: bool
    undeclared: list[str]
    declared: list[str]


def _load_vocab(viking: VikingClient) -> set[str]:
    """Load tag vocabulary from viking://meta/tags.yaml.

    The schema is intentionally simple: a YAML doc with a top-level `tags:`
    mapping where each key is a tag and the value is a description (string)
    or a small object with `description` and optional `aliases`.
    """
    import yaml  # local import to avoid a hard dep at module-load time

    doc = viking.cat("viking://meta/tags.yaml", level="L2")
    if doc is None or not doc.full:
        return set()
    try:
        data = yaml.safe_load(doc.full) or {}
    except yaml.YAMLError:
        return set()
    declared = set()
    for tag, body in (data.get("tags") or {}).items():
        declared.add(tag)
        if isinstance(body, dict):
            for alias in body.get("aliases", []) or []:
                declared.add(alias)
    return declared


def lint_tags(spec: WorkspaceSpec, viking: VikingClient) -> TagLintResult:
    declared = _load_vocab(viking)
    used = set(spec.tags)
    undeclared = sorted(used - declared)
    return TagLintResult(
        ok=not undeclared,
        undeclared=undeclared,
        declared=sorted(used & declared),
    )

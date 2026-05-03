"""Deterministic post-generation validation hooks.

These are NOT agent calls. They are pure code that runs after the workspace
generator writes files. If a hook fails, the workspace generation is rolled
back and the error is returned to the caller (typically the bootstrap agent,
which can then ask the user to fix the issue).
"""

from .handoff_check import check_handoff_files
from .tag_lint import lint_tags
from .workspace_validate import validate_workspace_on_disk

__all__ = [
    "check_handoff_files",
    "lint_tags",
    "validate_workspace_on_disk",
]

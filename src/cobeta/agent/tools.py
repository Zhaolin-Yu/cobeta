"""Deprecated. The old monolithic tools module has been split into
`cobeta.agent.tool` (base class) and `cobeta.agent.builtin_tools`
(concrete tools). This shim re-exports the new names so older imports
keep working until external callers update.

Prefer importing from `cobeta.agent` directly.
"""

from __future__ import annotations

from .builtin_tools import (
    AskUserTool,
    GenerateWorkspaceTool,
    ListExistingWorkspacesTool,
    VikingCatTool,
    VikingFindTool,
    VikingTreeTool,
    bootstrap_toolset,
)
from .tool import Tool, ToolPermission

__all__ = [
    "AskUserTool",
    "GenerateWorkspaceTool",
    "ListExistingWorkspacesTool",
    "Tool",
    "ToolPermission",
    "VikingCatTool",
    "VikingFindTool",
    "VikingTreeTool",
    "bootstrap_toolset",
]

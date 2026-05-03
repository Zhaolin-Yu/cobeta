"""Tool abstraction.

Tools are classes that the agent calls. Each tool declares its name, schema,
permission semantics, and execute method. The Agent class composes a list of
tools and dispatches by name.

Inspired by agno's tools-as-objects pattern: tools are first-class, each one
declares what it can and can't do, and the agent doesn't need to know how the
tool is implemented — only its contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any


class ToolPermission(str, Enum):
    """Permission category for a tool. Used for prompts, logging, auditability.

    The Agent does NOT enforce permissions at runtime — enforcement is handled
    by which tools are passed to which Agent (e.g., the bootstrap agent gets
    READ tools only). The permission is metadata that documents intent.
    """

    READ = "read"          # No side effects; always safe
    WRITE = "write"        # Mutates external state (viking, filesystem outside workspace)
    USER_INPUT = "user-input"  # Routes to a human prompt; semantically a read of the human
    TERMINAL = "terminal"  # End-of-loop action (e.g., generate_workspace)


class Tool(ABC):
    """Base class for cobeta agent tools.

    Subclasses set class-level `name`, `description`, `permission`, and
    `input_schema` (JSON-schema dict), and implement `execute(**kwargs) -> str`.
    """

    name: str = ""
    description: str = ""
    permission: ToolPermission = ToolPermission.READ
    input_schema: dict[str, Any] = {}

    @abstractmethod
    def execute(self, **kwargs: Any) -> str:
        """Run the tool. Returns a JSON-serializable string the LLM will see."""
        ...

    def to_schema(self) -> dict[str, Any]:
        """Render this tool as the schema dict the LLMProvider expects."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def __repr__(self) -> str:
        return f"<Tool {self.name} ({self.permission.value})>"

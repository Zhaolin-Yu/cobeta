"""Provider-agnostic LLM interface for the bootstrap agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Protocol


@dataclass
class LLMMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_calls: list["ToolCall"] = field(default_factory=list)
    tool_call_id: str | None = None  # for role="tool"


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    call_id: str
    content: str  # JSON-serializable string the model will see
    is_error: bool = False


class LLMProvider(Protocol):
    """Minimal provider interface. Implementations wrap a vendor SDK."""

    def chat(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
    ) -> LLMMessage:
        """Send `messages`. Returns the assistant turn (may include tool_calls)."""
        ...

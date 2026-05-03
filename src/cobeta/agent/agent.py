"""Agent class — composes an LLM provider with a list of tools.

The shape mirrors agno: instantiate `Agent(model=..., tools=[...], instructions=...)`
then call `.run(seed)`. Each iteration:

1. Send the conversation to the model (with tool schemas).
2. Receive the model's reply (text, tool_calls, or both).
3. If tool_calls: dispatch each one against the matching `Tool` instance.
4. If TERMINAL tool succeeded → end.
5. If no tool_calls → treat the model's text as a question to the user, prompt,
   feed the reply back, continue.
6. After `max_turns`, give up.

Cobeta's bootstrap is a single-purpose agent with one terminal action
(generate_workspace). For longer agentic loops, swap the terminal-detection
predicate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Optional

import click

from ..llm.base import LLMMessage, LLMProvider
from .tool import Tool, ToolPermission


@dataclass
class AgentResult:
    """Outcome of running an Agent."""

    completed: bool
    terminal_payload: Optional[dict] = None  # parsed JSON from the terminal tool
    error: Optional[str] = None
    final_messages: list[LLMMessage] = field(default_factory=list)


@dataclass
class Agent:
    """An LLM-driven loop with tool-use.

    Attributes:
        model: LLMProvider implementation (Anthropic, OpenAI-compatible, ...)
        tools: tool instances — one per tool name. Names must be unique.
        instructions: system prompt
        max_turns: hard cap on assistant turns
        echo_text: if True, print the assistant's text content to stdout each turn
        user_prompt_fn: how to prompt the user when the model emits text-only
            (default: click.prompt). Override for non-CLI environments.
    """

    model: LLMProvider
    tools: list[Tool]
    instructions: str
    max_turns: int = 20
    echo_text: bool = True
    user_prompt_fn: Callable[[str], str] = field(
        default_factory=lambda: lambda q: click.prompt(q, default="", show_default=False)
    )

    def __post_init__(self) -> None:
        names = [t.name for t in self.tools]
        if len(set(names)) != len(names):
            raise ValueError(f"tool names must be unique; got {names}")
        self._tool_index: dict[str, Tool] = {t.name: t for t in self.tools}

    @property
    def tool_schemas(self) -> list[dict]:
        return [t.to_schema() for t in self.tools]

    def run(self, seed: str) -> AgentResult:
        """Execute the loop. Returns when a TERMINAL tool succeeds, the user
        aborts, or `max_turns` is reached.
        """
        messages: list[LLMMessage] = [
            LLMMessage(role="system", content=self.instructions),
            LLMMessage(role="user", content=seed),
        ]

        for _ in range(self.max_turns):
            reply = self.model.chat(messages, tools=self.tool_schemas)
            messages.append(reply)

            if self.echo_text and reply.content:
                click.echo(reply.content)

            if not reply.tool_calls:
                # Model emitted text but didn't call a tool. Treat as a
                # turn waiting for human input.
                user_reply = self.user_prompt_fn("\n> ")
                low = user_reply.strip().lower()
                if low in ("/quit", "/abort", "/cancel"):
                    return AgentResult(
                        completed=False,
                        error="user aborted",
                        final_messages=messages,
                    )
                if not user_reply.strip():
                    user_reply = (
                        "(no further input — please proceed; either call a terminal "
                        "tool or ask a more specific question)"
                    )
                messages.append(LLMMessage(role="user", content=user_reply))
                continue

            # Dispatch each tool call, append results
            terminal_payload: Optional[dict] = None
            for tc in reply.tool_calls:
                tool = self._tool_index.get(tc.name)
                if tool is None:
                    result = json.dumps({"error": f"unknown tool: {tc.name}"})
                else:
                    try:
                        result = tool.execute(**tc.arguments)
                    except Exception as e:
                        result = json.dumps({"error": str(e)})

                messages.append(
                    LLMMessage(role="tool", content=result, tool_call_id=tc.id)
                )

                # Detect terminal success
                if tool is not None and tool.permission is ToolPermission.TERMINAL:
                    try:
                        parsed = json.loads(result)
                    except json.JSONDecodeError:
                        parsed = None
                    if parsed and parsed.get("ok"):
                        terminal_payload = parsed

            if terminal_payload is not None:
                return AgentResult(
                    completed=True,
                    terminal_payload=terminal_payload,
                    final_messages=messages,
                )

        return AgentResult(
            completed=False,
            error=f"reached max_turns ({self.max_turns}) without completing",
            final_messages=messages,
        )

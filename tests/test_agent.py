"""Tests for the Agent class and Tool abstraction."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from cobeta.agent import Agent, AgentResult, Tool, ToolPermission
from cobeta.llm.base import LLMMessage, ToolCall


# ---------- a fake LLM that scripts replies ----------


@dataclass
class FakeLLM:
    """Plays back a fixed sequence of replies. Records all chat() calls."""

    replies: list[LLMMessage]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def chat(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
    ) -> LLMMessage:
        self.calls.append(
            {"messages": [m for m in messages], "tools": tools or [], "max_tokens": max_tokens}
        )
        if not self.replies:
            raise RuntimeError("FakeLLM ran out of scripted replies")
        return self.replies.pop(0)


# ---------- toy tools for testing ----------


class EchoTool(Tool):
    name = "echo"
    description = "Echo a string back."
    permission = ToolPermission.READ
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    def execute(self, text: str) -> str:
        return json.dumps({"echoed": text})


class FinishTool(Tool):
    name = "finish"
    description = "Terminal action."
    permission = ToolPermission.TERMINAL
    input_schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }

    def execute(self, value: str) -> str:
        return json.dumps({"ok": True, "value": value})


class FailingFinishTool(Tool):
    name = "finish"
    description = "Terminal action that returns ok=False."
    permission = ToolPermission.TERMINAL
    input_schema = {"type": "object", "properties": {}}

    def execute(self) -> str:
        return json.dumps({"ok": False, "error": "nope"})


# ---------- tests ----------


def test_tool_to_schema_includes_required_fields() -> None:
    t = EchoTool()
    schema = t.to_schema()
    assert schema["name"] == "echo"
    assert "description" in schema
    assert "input_schema" in schema


def test_agent_unique_tool_names_required() -> None:
    with pytest.raises(ValueError):
        Agent(
            model=FakeLLM(replies=[]),
            tools=[EchoTool(), EchoTool()],
            instructions="x",
        )


def test_agent_completes_on_terminal_tool() -> None:
    """Single turn: model calls finish(value=hello), agent stops with payload."""
    fake = FakeLLM(
        replies=[
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="c1", name="finish", arguments={"value": "hello"})],
            )
        ]
    )
    agent = Agent(
        model=fake,
        tools=[FinishTool()],
        instructions="test",
        echo_text=False,
    )
    result = agent.run("seed")
    assert result.completed
    assert result.terminal_payload == {"ok": True, "value": "hello"}


def test_agent_chains_tool_calls_then_finishes() -> None:
    """Two turns: echo, then finish."""
    fake = FakeLLM(
        replies=[
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "first"})],
            ),
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="c2", name="finish", arguments={"value": "done"})],
            ),
        ]
    )
    agent = Agent(
        model=fake,
        tools=[EchoTool(), FinishTool()],
        instructions="test",
        echo_text=False,
    )
    result = agent.run("seed")
    assert result.completed
    assert result.terminal_payload == {"ok": True, "value": "done"}
    # The tool result for the echo should have been appended before the second model call
    second_call_messages = fake.calls[1]["messages"]
    tool_msgs = [m for m in second_call_messages if m.role == "tool"]
    assert any("first" in m.content for m in tool_msgs)


def test_agent_terminal_failure_keeps_looping() -> None:
    """When a TERMINAL tool returns ok=False, agent should not exit on it.

    Instead it gets the error string back and can retry. Here we send the
    failing terminal followed by a successful one.
    """
    fake = FakeLLM(
        replies=[
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="c1", name="finish", arguments={})],
            ),
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="c2", name="finish_ok", arguments={"value": "v"})],
            ),
        ]
    )
    agent = Agent(
        model=fake,
        tools=[FailingFinishTool(), _RenamedFinishTool()],
        instructions="test",
        echo_text=False,
    )
    result = agent.run("seed")
    assert result.completed, result.error
    assert result.terminal_payload["ok"] is True


class _RenamedFinishTool(FinishTool):
    name = "finish_ok"


def test_agent_user_input_loop() -> None:
    """When the model emits text without tool_calls, the agent should call
    user_prompt_fn and feed the reply back as a user message.
    """
    fake = FakeLLM(
        replies=[
            LLMMessage(role="assistant", content="What's the value?"),
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="c1", name="finish", arguments={"value": "from-user"})],
            ),
        ]
    )
    user_replies = iter(["from-user"])
    agent = Agent(
        model=fake,
        tools=[FinishTool()],
        instructions="test",
        echo_text=False,
        user_prompt_fn=lambda q: next(user_replies),
    )
    result = agent.run("seed")
    assert result.completed
    # Verify user reply landed in the messages history
    second_call_messages = fake.calls[1]["messages"]
    assert any(m.role == "user" and "from-user" in m.content for m in second_call_messages)


def test_agent_max_turns_exhausted() -> None:
    """If the model never calls a terminal tool, agent stops at max_turns."""
    fake = FakeLLM(
        replies=[
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id=f"c{i}", name="echo", arguments={"text": str(i)})],
            )
            for i in range(5)
        ]
    )
    agent = Agent(
        model=fake,
        tools=[EchoTool()],
        instructions="test",
        max_turns=3,
        echo_text=False,
    )
    result = agent.run("seed")
    assert not result.completed
    assert "max_turns" in (result.error or "")


def test_agent_unknown_tool_returns_error_to_model() -> None:
    """If the model calls a tool that doesn't exist, agent feeds back an error."""
    fake = FakeLLM(
        replies=[
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="c1", name="nonexistent", arguments={})],
            ),
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="c2", name="finish", arguments={"value": "v"})],
            ),
        ]
    )
    agent = Agent(
        model=fake,
        tools=[FinishTool()],
        instructions="test",
        echo_text=False,
    )
    result = agent.run("seed")
    assert result.completed
    second_call_messages = fake.calls[1]["messages"]
    tool_msgs = [m for m in second_call_messages if m.role == "tool"]
    assert any("unknown tool" in m.content for m in tool_msgs)

"""Anthropic provider for the bootstrap agent. Soft dep on the `anthropic` SDK."""

from __future__ import annotations

import json
import os
from typing import Any

from .base import LLMMessage, ToolCall


class AnthropicProvider:
    def __init__(self, model: str | None = None, api_key: str | None = None):
        try:
            import anthropic
        except ImportError as e:
            raise RuntimeError(
                "anthropic SDK not installed. `pip install 'cobeta[anthropic]'`."
            ) from e

        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Either set the env var or pass api_key=..."
            )

        self._client = anthropic.Anthropic(api_key=key)
        self.model = model or "claude-sonnet-4-6"

    def chat(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
    ) -> LLMMessage:
        # Translate our generic messages into Anthropic's schema
        system_parts = [m.content for m in messages if m.role == "system"]
        system_prompt = "\n\n".join(system_parts) if system_parts else None

        anth_messages: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                continue
            if m.role == "tool":
                anth_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.tool_call_id,
                                "content": m.content,
                            }
                        ],
                    }
                )
                continue
            if m.role == "assistant" and m.tool_calls:
                content_blocks: list[dict[str, Any]] = []
                if m.content:
                    content_blocks.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    content_blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        }
                    )
                anth_messages.append({"role": "assistant", "content": content_blocks})
                continue
            anth_messages.append({"role": m.role, "content": m.content})

        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": anth_messages,
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if tools:
            kwargs["tools"] = tools

        resp = self._client.messages.create(**kwargs)

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
                )

        return LLMMessage(
            role="assistant",
            content="\n".join(text_parts),
            tool_calls=tool_calls,
        )

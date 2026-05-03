"""OpenAI-compatible provider.

Supports:
- Vanilla OpenAI (base_url unset)
- Any OpenAI-protocol-compatible endpoint via `base_url` (e.g. MiMo, vLLM,
  Together, Groq, …) — set via `LLMProviderConfig.base_url` or the
  `OPENAI_BASE_URL` env var
"""

from __future__ import annotations

import json
import os
from typing import Any

from .base import LLMMessage, ToolCall


class OpenAICompatibleProvider:
    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
    ):
        try:
            import openai
        except ImportError as e:
            raise RuntimeError(
                "openai SDK not installed. `pip install 'cobeta[openai]'`."
            ) from e

        key = api_key or os.environ.get(api_key_env)
        if not key:
            raise RuntimeError(
                f"${api_key_env} not set. Either set the env var or pass api_key=..."
            )

        url = base_url or os.environ.get("OPENAI_BASE_URL")
        kwargs: dict[str, Any] = {"api_key": key}
        if url:
            kwargs["base_url"] = url
        self._client = openai.OpenAI(**kwargs)
        self.model = model or "gpt-4o-mini"

    def chat(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
    ) -> LLMMessage:
        # Translate to OpenAI chat schema
        oa_messages: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                oa_messages.append({"role": "system", "content": m.content})
                continue
            if m.role == "tool":
                oa_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": m.tool_call_id,
                        "content": m.content,
                    }
                )
                continue
            if m.role == "assistant" and m.tool_calls:
                oa_messages.append(
                    {
                        "role": "assistant",
                        "content": m.content or None,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.name,
                                    "arguments": json.dumps(tc.arguments),
                                },
                            }
                            for tc in m.tool_calls
                        ],
                    }
                )
                continue
            oa_messages.append({"role": m.role, "content": m.content})

        oa_tools = None
        if tools:
            oa_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema") or t.get("parameters") or {},
                    },
                }
                for t in tools
            ]

        kwargs = {
            "model": self.model,
            "messages": oa_messages,
            "max_tokens": max_tokens,
        }
        if oa_tools:
            kwargs["tools"] = oa_tools

        resp = self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        content = choice.message.content or ""
        tool_calls: list[ToolCall] = []
        for tc in (choice.message.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                args = {"_raw": tc.function.arguments}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

        return LLMMessage(role="assistant", content=content, tool_calls=tool_calls)

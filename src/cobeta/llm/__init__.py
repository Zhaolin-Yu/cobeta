from .base import LLMMessage, LLMProvider, ToolCall, ToolResult


def get_provider(provider: str, *, model: str | None = None, base_url: str | None = None, api_key_env: str | None = None) -> LLMProvider:
    """Factory that returns the right provider class for `provider`."""
    if provider == "anthropic":
        from .anthropic_client import AnthropicProvider
        return AnthropicProvider(model=model)
    if provider in ("openai", "openai-compatible"):
        from .openai_client import OpenAICompatibleProvider
        kwargs = {"model": model}
        if base_url:
            kwargs["base_url"] = base_url
        if api_key_env:
            kwargs["api_key_env"] = api_key_env
        return OpenAICompatibleProvider(**kwargs)
    raise ValueError(f"unknown provider: {provider!r}")


__all__ = ["LLMMessage", "LLMProvider", "ToolCall", "ToolResult", "get_provider"]

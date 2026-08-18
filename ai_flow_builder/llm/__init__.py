"""
ai_flow_builder/llm — the model provider seam.

Only ONE capability is needed from a provider: given a system prompt, a user
prompt and a schema, return data matching that schema. Everything else the tool
does — mapping steps, reusing locators, emitting flows, linting — is
deterministic and knows nothing about models.

Keeping the interface that narrow is what makes switching providers safe. A
provider cannot break a flow, because nothing it produces reaches a flow without
passing nlp/parser.py and the runner's dispatch table first. A weaker provider
yields more steps marked NEEDS_CLARIFICATION; it cannot yield a broken flow.

Selection is configuration, not code:

    LLM_PROVIDER=anthropic          # which adapter
    LLM_MODEL=claude-opus-5         # optional per-provider override

Credentials never appear here. Each adapter resolves its own from the
environment, so a shared corporate credential, a per-user login, and an internal
gateway are all the same code path.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class ProviderError(RuntimeError):
    """A provider could not produce a usable result."""


class ProviderRefused(ProviderError):
    """The provider declined the request on policy grounds — not a bug."""


class ProviderNotConfigured(ProviderError):
    """Named provider exists but has no adapter or no credential."""


@dataclass
class Completion:
    """What every provider returns, whatever its native response shape."""

    data: dict                       # validated against the requested schema
    provider: str = ""
    model: str = ""
    usage: dict = field(default_factory=dict)


@runtime_checkable
class LLMProvider(Protocol):
    """The entire contract. Adding a provider means implementing this."""

    name: str
    model: str

    def complete_structured(self, *, system: str, user: str, schema) -> Completion:
        """Return data matching `schema` (a pydantic BaseModel subclass)."""
        ...


#: Adapters that exist today. Each value is an import path resolved lazily, so a
#: missing optional SDK only breaks the provider that needs it.
_ADAPTERS: dict[str, str] = {
    "anthropic": "ai_flow_builder.llm.anthropic_provider:AnthropicProvider",
}

#: Providers the interface is designed for but which have no adapter yet. Named
#: explicitly so an unimplemented choice fails with a useful message instead of
#: looking like a typo.
_PLANNED: dict[str, str] = {
    "openai":   "OpenAI / ChatGPT — structured outputs via response_format json_schema",
    "gemini":   "Google Gemini — response_schema on generate_content",
    "grok":     "xAI Grok — OpenAI-compatible endpoint",
    "deepseek": "DeepSeek — OpenAI-compatible endpoint",
}

DEFAULT_PROVIDER = "anthropic"


def available() -> list[str]:
    return sorted(_ADAPTERS)


def planned() -> dict[str, str]:
    return dict(_PLANNED)


def get_provider(name: str | None = None, model: str | None = None) -> LLMProvider:
    """Resolve the configured provider. `name` overrides LLM_PROVIDER."""
    key = (name or os.getenv("LLM_PROVIDER") or DEFAULT_PROVIDER).strip().lower()

    if key not in _ADAPTERS:
        if key in _PLANNED:
            raise ProviderNotConfigured(
                f"Provider '{key}' is planned but has no adapter yet "
                f"({_PLANNED[key]}). Implement ai_flow_builder/llm/{key}_provider.py "
                f"against the LLMProvider protocol — nothing else has to change. "
                f"Available now: {', '.join(available())}."
            )
        raise ProviderNotConfigured(
            f"Unknown provider '{key}'. Available: {', '.join(available())}; "
            f"planned: {', '.join(sorted(_PLANNED))}."
        )

    module_path, _, cls_name = _ADAPTERS[key].partition(":")
    import importlib

    cls = getattr(importlib.import_module(module_path), cls_name)
    return cls(model=model or os.getenv("LLM_MODEL") or None)

"""
ai_flow_builder/llm/anthropic_provider.py — Claude adapter.

Credential resolution is deliberately left to the SDK, which tries, in order: an
explicit key, ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, an `ant auth login`
profile, workload identity federation, then the default profile on disk.

That one behaviour covers every deployment shape without a code change:

  shared corporate key   ANTHROPIC_API_KEY set once, wherever the tool runs.
  per-user sign-in       each operator runs `ant auth login`; nothing is shared.
  internal gateway       ANTHROPIC_BASE_URL points at a service that holds the
                         real key and injects it. Clients never see the secret,
                         which is the only arrangement where "the admin enters it
                         once" is literally true rather than a key emailed round.

Passing a key in code would defeat all three, so this never does.
"""
from __future__ import annotations

import os

from ai_flow_builder.llm import (Completion, ProviderError,
                                 ProviderNotConfigured, ProviderRefused)

DEFAULT_MODEL = "claude-opus-5"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or DEFAULT_MODEL

    #: Every credential source the SDK itself consults. This check exists only to
    #: turn "no credential anywhere" into an actionable message, so it must not be
    #: STRICTER than the SDK — omitting workload identity federation rejected
    #: exactly the deployments (CI, containers) the module docstring promises to
    #: support, and the config directory is relocatable via ANTHROPIC_CONFIG_DIR.
    _CREDENTIAL_ENV = (
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE",
        "ANTHROPIC_BASE_URL",                       # gateway injects the key
        "ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_ORGANIZATION_ID",
        "ANTHROPIC_SERVICE_ACCOUNT_ID", "ANTHROPIC_IDENTITY_TOKEN",
        "ANTHROPIC_IDENTITY_TOKEN_FILE",
    )

    @classmethod
    def _has_credential(cls) -> bool:
        if any(os.getenv(name) for name in cls._CREDENTIAL_ENV):
            return True
        config_dir = os.getenv("ANTHROPIC_CONFIG_DIR") or os.path.join(
            os.path.expanduser("~"), ".config", "anthropic")
        return os.path.isdir(config_dir)

    def _client(self):
        if not self._has_credential():
            raise ProviderNotConfigured(
                "No Anthropic credential found. Any one of these works:\n"
                "  shared key   export ANTHROPIC_API_KEY=sk-ant-...\n"
                "  per-user     ant auth login      (stores ~/.config/anthropic/)\n"
                "  gateway      export ANTHROPIC_BASE_URL=https://<internal-proxy>\n\n"
                "A set ANTHROPIC_API_KEY SHADOWS a signed-in profile — if usage "
                "bills to the wrong account, unset it before debugging further."
            )
        import anthropic

        return anthropic.Anthropic()

    def complete_structured(self, *, system: str, user: str, schema) -> Completion:
        res = self._client().messages.parse(
            model=self.model,
            max_tokens=16000,
            # Drafting testcases is multi-step reasoning: read the request, split
            # scenarios, pick locators, decide what stays a variable.
            thinking={"type": "adaptive"},
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
        )
        if res.stop_reason == "refusal":
            raise ProviderRefused(
                "Anthropic declined this request "
                f"({getattr(res.stop_details, 'category', 'unspecified')})."
            )
        if res.stop_reason == "max_tokens":
            # The response was cut off, so no complete object was parsed and
            # parsed_output is absent. Say that plainly instead of surfacing an
            # AttributeError from deep inside the SDK.
            raise ProviderError(
                "The model hit its output limit before finishing. Ask for fewer "
                "testcases, or shorten the request."
            )
        parsed = getattr(res, "parsed_output", None)
        if parsed is None:
            raise ProviderError(
                f"No structured output was returned (stop_reason={res.stop_reason!r})."
            )
        return Completion(
            data=parsed.model_dump(),
            provider=self.name,
            model=res.model,
            usage={"input_tokens": res.usage.input_tokens,
                   "output_tokens": res.usage.output_tokens},
        )

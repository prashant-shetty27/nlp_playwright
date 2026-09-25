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

import logging

_log = logging.getLogger(__name__)

import os

from pydantic import ValidationError
from ai_flow_builder.llm import (Completion, ProviderError,
                                 ProviderNotConfigured, ProviderRefused,
                                 ProviderUnavailable)

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
        import anthropic

        # SDK exceptions must not escape this adapter. An unhandled 529 from the
        # provider surfaced as a 500 with a stack trace, which tells the operator
        # nothing actionable — the useful message is "overloaded, try again".
        # (The SDK already retries 429/5xx twice; reaching here means those failed.)
        try:
            return self._parse(system=system, user=user, schema=schema)
        except ValidationError as e:
            # What exactly did not fit, in the log — the operator sees a one-line
            # error, whoever fixes the schema needs the field names.
            try:
                _log.warning("Structured output rejected: %s",
                             "; ".join(f"{'.'.join(str(x) for x in err.get('loc', ()))}: "
                                       f"{err.get('msg')}" for err in e.errors()[:6]))
            except Exception:  # noqa: BLE001
                pass
            # Structured output normally guarantees well-formed JSON, but a
            # malformed body does get through (seen in the wild: a trailing
            # comma). That surfaced as a pydantic traceback and an HTTP 500.
            # One retry costs a few seconds and usually lands; two failures in a
            # row is a real problem and says so in words.
            try:
                return self._parse(system=system, user=user, schema=schema)
            except ValidationError as again:
                raise ProviderError(
                    "The model returned output that did not match the expected "
                    f"shape, twice: {str(again)[:160]}. Try again, or reduce the "
                    "number of testcases requested."
                ) from e
        except anthropic.RateLimitError as e:
            retry = e.response.headers.get("retry-after") if e.response else None
            raise ProviderUnavailable(
                "Rate limited by Anthropic"
                + (f"; retry after {retry}s" if retry else ""),
                retry_after=int(retry) if retry and retry.isdigit() else None,
            ) from e
        except anthropic.APIStatusError as e:
            if e.status_code in (429, 500, 502, 503, 529):
                raise ProviderUnavailable(
                    f"Anthropic is temporarily unavailable (HTTP {e.status_code}). "
                    f"This is transient — try again in a moment."
                ) from e
            raise ProviderError(
                f"Anthropic rejected the request (HTTP {e.status_code}): "
                f"{getattr(e, 'message', str(e))[:200]}"
            ) from e
        except anthropic.APIConnectionError as e:
            raise ProviderUnavailable(
                f"Could not reach Anthropic: {str(e)[:160]}"
            ) from e
        except (ValueError, TypeError) as e:
            # SDK-side refusals (argument checks) — say what it said.
            raise ProviderError(f"Drafting could not start: {str(e)[:200]}") from e

    def _parse(self, *, system: str, user: str, schema) -> Completion:
        res = self._client().messages.parse(
            model=self.model,
            # 16k was cut off by a full ticket draft (17 cases × ~8 steps plus
            # thinking, which shares this budget). 64k is what current models
            # allow and the honest ceiling for "as many cases as the spec needs".
            max_tokens=64000,
            # An explicit timeout: without one the SDK refuses a non-streaming
            # call that could run over ten minutes ("Streaming is required…")
            # and the draft never even left the building. A full ticket draft
            # takes 3–6 minutes; 30 minutes is the ceiling, not the expectation.
            timeout=1800.0,
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

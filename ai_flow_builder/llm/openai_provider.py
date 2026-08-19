"""
ai_flow_builder/llm/openai_provider.py — OpenAI / ChatGPT adapter.

Implements the same LLMProvider contract as the Anthropic adapter, so switching
is configuration:

    LLM_PROVIDER=openai
    LLM_MODEL=gpt-5.1                 # optional

Everything downstream is unchanged. A provider cannot break a flow, because its
output still has to survive nlp/parser.py and the runner's dispatch table before
it reaches one — a weaker provider yields more steps marked NEEDS_CLARIFICATION,
never a broken flow.

Written against the installed SDK's real surface (openai 3.2.0, verified by
introspection, not from memory):

    client.chat.completions.parse(model=…, messages=[…], response_format=<Model>)
      → completion.choices[0].message.parsed   (a validated Pydantic instance)

`chat.completions.parse` is used rather than `responses.parse` because it takes a
`messages` list with a system role, which maps directly onto the system/user split
the contract already defines. Reasoning depth is set with `reasoning_effort`,
the closest equivalent to Anthropic's adaptive thinking; it is passed only when
the model accepts it, since sending it to a non-reasoning model is a 400.

Credentials are never named in code — the SDK resolves OPENAI_API_KEY, and
OPENAI_BASE_URL points at a gateway or an OpenAI-compatible endpoint, which is
how Grok and DeepSeek can reuse this adapter.
"""
from __future__ import annotations

import os

from ai_flow_builder.llm import (Completion, ProviderError,
                                 ProviderNotConfigured, ProviderRefused,
                                 ProviderUnavailable)

DEFAULT_MODEL = "gpt-5.1"

#: Models that accept `reasoning_effort`. Sending it to a model without reasoning
#: support is rejected outright, so it is opt-in by prefix rather than always-on.
_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def _is_quota_exhausted(exc) -> bool:
    """
    True when a 429 is an exhausted quota rather than real rate limiting.

    The SDK does not always populate `exc.body`, so the raw response text is
    checked as well — relying on the parsed body alone silently misread this as
    transient during testing.
    """
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error") or {}
        if "insufficient_quota" in (str(err.get("type")) + str(err.get("code"))):
            return True
    resp = getattr(exc, "response", None)
    text = ""
    try:
        text = resp.text if resp is not None else ""
    except Exception:  # noqa: BLE001
        pass
    return "insufficient_quota" in (text + str(exc)).lower()


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.getenv("OPENAI_MODEL") or DEFAULT_MODEL

    @staticmethod
    def _has_credential() -> bool:
        # OPENAI_BASE_URL alone counts: a gateway may inject the key on egress,
        # which is the arrangement where one admin holds the secret and no client
        # ever sees it.
        return bool(os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_BASE_URL"))

    def _client(self):
        if not self._has_credential():
            raise ProviderNotConfigured(
                "No OpenAI credential found. Either:\n"
                "  export OPENAI_API_KEY=sk-...\n"
                "  or point at a gateway:  export OPENAI_BASE_URL=https://<proxy>/v1\n"
                "OPENAI_BASE_URL also lets this adapter serve any OpenAI-compatible\n"
                "endpoint (Grok, DeepSeek, a local model server)."
            )
        import openai

        return openai.OpenAI()

    def _supports_reasoning(self) -> bool:
        return self.model.lower().startswith(_REASONING_PREFIXES)

    def complete_structured(self, *, system: str, user: str, schema) -> Completion:
        import openai

        kwargs: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "response_format": schema,
        }
        if self._supports_reasoning():
            kwargs["reasoning_effort"] = os.getenv("OPENAI_REASONING_EFFORT", "medium")

        try:
            completion = self._client().chat.completions.parse(**kwargs)
        except openai.LengthFinishReasonError as e:
            # Ran out of output budget mid-object, so nothing parsed. Same failure
            # the Anthropic adapter reports on max_tokens, phrased the same way.
            raise ProviderError(
                "The model hit its output limit before finishing. Ask for fewer "
                "testcases, or shorten the request."
            ) from e
        except openai.ContentFilterFinishReasonError as e:
            raise ProviderRefused(
                "OpenAI's content filter declined this request."
            ) from e
        except openai.RateLimitError as e:
            # A 429 from OpenAI means two very different things. Genuine rate
            # limiting clears on its own; `insufficient_quota` means the key has no
            # billing credit and will NEVER clear by waiting. Treating the second
            # as transient makes a --watch loop spin forever and tells the operator
            # to be patient when they need to add a payment method.
            if _is_quota_exhausted(e):
                raise ProviderNotConfigured(
                    "This OpenAI key has no remaining quota — OpenAI returned "
                    "`insufficient_quota`. Retrying will not help: add credit or a "
                    "payment method to the account, or use a key from a funded "
                    "project. (Check the account, not the code: the request itself "
                    "was accepted and correctly formed.)"
                ) from e
            raise ProviderUnavailable("Rate limited by OpenAI — transient.") from e
        except openai.APITimeoutError as e:
            raise ProviderUnavailable(f"OpenAI timed out: {str(e)[:120]}") from e
        except openai.APIConnectionError as e:
            raise ProviderUnavailable(f"Could not reach OpenAI: {str(e)[:140]}") from e
        except openai.AuthenticationError as e:
            raise ProviderNotConfigured(
                f"OpenAI rejected the credential: {str(e)[:140]}"
            ) from e
        except openai.APIStatusError as e:
            if e.status_code in (429, 500, 502, 503, 529):
                raise ProviderUnavailable(
                    f"OpenAI is temporarily unavailable (HTTP {e.status_code}). "
                    f"This is transient — try again in a moment."
                ) from e
            raise ProviderError(
                f"OpenAI rejected the request (HTTP {e.status_code}): {str(e)[:180]}"
            ) from e

        choice = completion.choices[0]
        if getattr(choice.message, "refusal", None):
            raise ProviderRefused(f"OpenAI declined: {choice.message.refusal[:160]}")
        parsed = getattr(choice.message, "parsed", None)
        if parsed is None:
            raise ProviderError(
                f"No structured output returned (finish_reason="
                f"{choice.finish_reason!r})."
            )

        usage = completion.usage
        return Completion(
            data=parsed.model_dump(),
            provider=self.name,
            model=completion.model,
            usage={"input_tokens": getattr(usage, "prompt_tokens", 0),
                   "output_tokens": getattr(usage, "completion_tokens", 0)},
        )

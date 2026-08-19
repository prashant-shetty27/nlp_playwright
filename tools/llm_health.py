#!/usr/bin/env python3
"""
tools/llm_health.py — is the model provider reachable right now?

    python tools/llm_health.py            # one cheap live call
    python tools/llm_health.py --watch    # re-check every 30s until healthy

Exit codes make it usable in a script or a CI gate:
    0  healthy
    1  transient (overloaded / rate-limited / unreachable) — retry later
    2  configuration or request problem — retrying will not help

Deliberately makes a REAL request, in the SAME SHAPE the tool uses: adaptive
thinking plus a structured-output schema. A trivial one-token call is not a valid
proxy — it was observed succeeding while the real drafting request was still
returning 529, because overload is capacity-shaped and a large thinking request
needs far more of it than a one-token reply. A check that passes while the
feature fails is worse than no check.

The schema is tiny, so the call is cheap; what it verifies is the code path, not
the size of the job.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def check() -> tuple[int, str]:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(BASE_DIR, ".env"))

    from ai_flow_builder.llm import (ProviderError, ProviderNotConfigured,
                                     ProviderUnavailable, get_provider)

    try:
        provider = get_provider()
    except ProviderNotConfigured as e:
        return 2, f"not configured: {e}"

    from pydantic import BaseModel

    class Probe(BaseModel):
        ok: bool

    started = time.time()
    try:
        import anthropic

        # Same call the drafting path makes: structured output + adaptive thinking.
        res = provider.complete_structured(
            system="Reply with ok=true.",
            user="Health probe. Set ok to true.",
            schema=Probe,
        )
        ms = int((time.time() - started) * 1000)
        return 0, (f"healthy — {res.provider}/{res.model} answered in {ms}ms "
                   f"(structured output + adaptive thinking OK, "
                   f"{res.usage.get('output_tokens', '?')} output tokens)")
    except ProviderUnavailable as e:
        return 1, f"{e} — transient"
    except ProviderError as e:
        return 2, str(e)[:160]
    except ProviderNotConfigured as e:
        return 2, f"not configured: {e}"
    except Exception as e:  # noqa: BLE001
        import anthropic

        if isinstance(e, anthropic.RateLimitError):
            return 1, "rate limited (HTTP 429) — transient"
        if isinstance(e, anthropic.APIStatusError):
            transient = e.status_code in (429, 500, 502, 503, 529)
            return (1 if transient else 2), (
                f"HTTP {e.status_code} — "
                f"{'transient, retry later' if transient else 'not transient'}")
        if isinstance(e, anthropic.APIConnectionError):
            return 1, f"cannot reach the provider: {str(e)[:120]}"
        return 2, f"{type(e).__name__}: {str(e)[:140]}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--watch", action="store_true",
                    help="re-check until healthy (or Ctrl-C)")
    ap.add_argument("--every", type=int, default=30, help="seconds between checks")
    args = ap.parse_args()

    while True:
        code, msg = check()
        mark = {0: "OK  ", 1: "WAIT", 2: "FAIL"}[code]
        print(f"[{mark}] {time.strftime('%H:%M:%S')}  {msg}", flush=True)
        if code == 0 or not args.watch or code == 2:
            return code
        time.sleep(max(5, args.every))


if __name__ == "__main__":
    sys.exit(main())

"""
locators/healing_memory.py — remember a heal instead of re-deriving it every run.

The self-healer already finds a replacement when a stored selector stops
matching, and it only accepts one that clears two gates (an ML distance
threshold and a rule-based cross-check). But the result was used for that one
step and thrown away. So the same page cost the same DOM scrape and the same ML
pass on every run, and a locator that had been quietly broken for weeks stayed
broken in the database — the tests passed, and nobody learned that the selector
needed updating.

What is stored, and when
-----------------------
A confident heal is written back as an ALTERNATE, not as a replacement. The
operator's own selector stays exactly where it is. The alternate is tried after
it, so behaviour does not change on the next run — the heal simply becomes free
instead of being recomputed.

Promotion to primary happens only after the original has failed and the
alternate has succeeded `PROMOTE_AFTER` times. One lucky match on a page that
happened to render oddly is not evidence; a repeated one is. Every record keeps
its score, its count and when it was last used, so a promotion can be explained
and undone.

Nothing here raises. A locator that cannot be written back is logged and the run
continues — healing is an optimisation, and failing to remember it must never
fail the test that was otherwise passing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

#: Successful uses of an alternate, after the original failed, before it becomes
#: the primary selector.
PROMOTE_AFTER = 3

#: A heal below this ML confidence is used for the step but never written down.
#: The healer's own gate is what decides safety for one action; persisting is a
#: longer-lived claim and deserves a stricter bar.
REMEMBER_MIN_SCORE = 0.55


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def enabled() -> bool:
    """
    Whether a heal may be written back at all.

    Off switch first, because this is the one part of healing that CHANGES the
    locator database rather than just reading it. Set HEAL_MEMORY=off in .env and
    healing behaves exactly as it did before: it still heals a step at run time,
    it simply forgets afterwards. Nothing else in the system depends on it.
    """
    import os

    return (os.getenv("HEAL_MEMORY", "on") or "on").strip().lower() not in (
        "0", "off", "false", "no")


def record_heal(locator_name: str, healed_selector: str, *,
                score: float = 0.0, original_failed: bool = True,
                dna: dict | None = None) -> dict:
    """
    Remember that `healed_selector` worked for `locator_name`.

    Returns a small report of what was done, so a caller can log or surface it:
        {"stored": bool, "promoted": bool, "uses": int, "reason": str}
    """
    report = {"stored": False, "promoted": False, "uses": 0, "reason": ""}
    if not enabled():
        report["reason"] = "HEAL_MEMORY is off"
        return report
    if not locator_name or not healed_selector:
        report["reason"] = "nothing to record"
        return report
    if score and score < REMEMBER_MIN_SCORE:
        report["reason"] = f"score {score:.2f} below {REMEMBER_MIN_SCORE}"
        return report

    try:
        from locators.manager import load_locators, save_locators
        from locators.sources import owner

        entry = owner(locator_name, "website")
        if entry is None:
            report["reason"] = f"{locator_name!r} is not in the database"
            return report

        data = load_locators()
        page = entry.group
        # Only the writable database is ever modified. A recorded element belongs
        # to the spy; rewriting it here would put the recording and the database
        # permanently out of step.
        if page not in data or locator_name not in data[page]:
            report["reason"] = (f"{locator_name!r} lives in {entry.source_id}, "
                                f"which is not writable")
            return report

        rec = data[page][locator_name]
        if not isinstance(rec, dict):
            rec = {"custom_xpath": str(rec)}

        healed = rec.get("_healed")
        if isinstance(healed, dict) and healed.get("selector") == healed_selector:
            healed["uses"] = int(healed.get("uses", 0)) + (1 if original_failed else 0)
            healed["last_seen"] = _now()
            healed["score"] = max(float(healed.get("score", 0)), float(score or 0))
        else:
            healed = {"selector": healed_selector, "score": float(score or 0),
                      "uses": 1 if original_failed else 0,
                      "first_seen": _now(), "last_seen": _now()}
        rec["_healed"] = healed
        report["uses"] = healed["uses"]

        # Offer it to the runtime as a fallback straight away: resolution tries
        # the operator's selector first, so this changes nothing until that one
        # stops matching — at which point the answer is already known.
        selectors = rec.get("selectors")
        if not isinstance(selectors, list):
            selectors = []
        kinds = {s.get("value") for s in selectors if isinstance(s, dict)}
        if healed_selector not in kinds:
            selectors.append({"type": "xpath" if healed_selector.startswith("//") else "css",
                              "value": healed_selector, "source": "self-healed"})
            rec["selectors"] = selectors

        if healed["uses"] >= PROMOTE_AFTER:
            previous = rec.get("custom_xpath")
            if previous and previous != healed_selector:
                rec["_superseded"] = {"selector": previous, "replaced_at": _now(),
                                      "after_uses": healed["uses"]}
            rec["custom_xpath"] = healed_selector
            healed["uses"] = 0            # start counting again from the new primary
            report["promoted"] = True
            logger.info("🏥 Promoted healed selector for %r to primary: %s",
                        locator_name, healed_selector)

        if dna:
            rec.setdefault("dna", dna)

        data[page][locator_name] = rec
        save_locators(data)
        report["stored"] = True
        report["reason"] = "recorded"
        return report
    except Exception as e:  # noqa: BLE001 — remembering must never fail a run
        logger.warning("Could not record heal for %r: %s", locator_name, e)
        report["reason"] = f"{type(e).__name__}: {e}"
        return report

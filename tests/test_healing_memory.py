"""
tests/test_healing_memory.py — a heal may be remembered, but never carelessly.

This is the only part of self-healing that WRITES to the locator database, so it
is the part most able to do lasting damage: a wrong selector promoted to primary
would break every test using that element, quietly, on the next run.

The guarantees asserted here are what make it safe to leave on:

  * a low-confidence match is not written down at all;
  * a confident one is stored as an ALTERNATE — the operator's own selector stays
    primary, so the next run behaves exactly as this one did;
  * promotion needs the original to have failed repeatedly, not once;
  * the replaced selector is kept, so a promotion can be explained and undone;
  * a read-only source is refused — rewriting the spy's recording would leave the
    recording and the database permanently disagreeing;
  * HEAL_MEMORY=off disables the whole thing;
  * nothing here ever raises, because failing to remember must never fail a test
    that was otherwise passing.

Run: python tests/test_healing_memory.py
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from locators.healing_memory import (PROMOTE_AFTER,  # noqa: E402
                                     REMEMBER_MIN_SCORE, enabled, record_heal)
from locators.manager import load_locators, save_locators  # noqa: E402

_passed = 0
_failed = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}   {detail}")


# The switch is set HERE rather than inherited. config.settings loads .env on
# import, so with HEAL_MEMORY=off in the project's own .env this whole file
# silently asserted the disabled behaviour and reported 24 passes for the wrong
# reason. A test that reads ambient configuration is testing the machine it runs
# on, not the code.
_ENV_BEFORE = os.environ.get("HEAL_MEMORY")
os.environ["HEAL_MEMORY"] = "on"

GROUP, NAME = "heal_test_page", "heal_test_el"
ORIGINAL = "//button[@id='original']"
HEALED = "//button[@id='moved']"

_before = copy.deepcopy(load_locators())
try:
    data = load_locators()
    data.setdefault(GROUP, {})[NAME] = {"custom_xpath": ORIGINAL}
    save_locators(data)

    # ═══════════════════════════════════════════════════════════════════════
    print("\n[1] A WEAK MATCH IS NOT WRITTEN DOWN")

    r = record_heal(NAME, HEALED, score=REMEMBER_MIN_SCORE - 0.2)
    check("a low-confidence heal is refused", not r["stored"], str(r))
    check("and says why", "below" in r["reason"], r["reason"])
    check("the database is untouched",
          load_locators()[GROUP][NAME] == {"custom_xpath": ORIGINAL})

    # ═══════════════════════════════════════════════════════════════════════
    print("\n[2] A CONFIDENT MATCH BECOMES AN ALTERNATE, NOT A REPLACEMENT")

    r = record_heal(NAME, HEALED, score=0.9)
    rec = load_locators()[GROUP][NAME]
    check("it is stored", r["stored"], str(r))
    check("THE OPERATOR'S SELECTOR IS STILL PRIMARY — behaviour cannot change",
          rec["custom_xpath"] == ORIGINAL, rec.get("custom_xpath"))
    check("the healed one is offered as a fallback",
          any(s.get("value") == HEALED for s in rec.get("selectors", [])),
          str(rec.get("selectors")))
    check("the fallback records where it came from",
          any(s.get("source") == "self-healed" for s in rec.get("selectors", [])))
    check("the score is kept for later judgement",
          rec["_healed"]["score"] >= 0.9, str(rec.get("_healed")))

    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n[3] PROMOTION NEEDS {PROMOTE_AFTER} CONFIRMATIONS, NOT ONE")

    for i in range(PROMOTE_AFTER - 2):
        r = record_heal(NAME, HEALED, score=0.9)
        check(f"still not promoted after {i + 2} use(s)", not r["promoted"], str(r))
    r = record_heal(NAME, HEALED, score=0.9)
    rec = load_locators()[GROUP][NAME]
    check(f"promoted on use {PROMOTE_AFTER}", r["promoted"], str(r))
    check("the healed selector is now primary", rec["custom_xpath"] == HEALED)
    check("THE ORIGINAL IS KEPT so the change can be undone",
          rec.get("_superseded", {}).get("selector") == ORIGINAL,
          str(rec.get("_superseded")))
    check("the counter resets, so the new primary earns its own evidence",
          rec["_healed"]["uses"] == 0, str(rec["_healed"]))

    # ═══════════════════════════════════════════════════════════════════════
    print("\n[4] WHAT IT REFUSES TO TOUCH")

    r = record_heal("login_with_otp", "//x", score=0.99)
    check("a spy-recorded element is refused", not r["stored"], str(r))
    check("and explains that it is not writable", "not writable" in r["reason"],
          r["reason"])
    check("an unknown element is refused",
          not record_heal("no_such_element_at_all", "//x", score=0.99)["stored"])
    check("an empty selector is refused", not record_heal(NAME, "", score=0.99)["stored"])
    check("an empty name is refused", not record_heal("", "//x", score=0.99)["stored"])

    # ═══════════════════════════════════════════════════════════════════════
    print("\n[5] THE OFF SWITCH, AND THE PROMISE NEVER TO RAISE")

    os.environ["HEAL_MEMORY"] = "off"
    try:
        check("HEAL_MEMORY=off disables it", not enabled())
        r = record_heal(NAME, "//another", score=0.99)
        check("nothing is written while off", not r["stored"], str(r))
        check("and it says so", "off" in r["reason"], r["reason"])
    finally:
        os.environ["HEAL_MEMORY"] = "on"
    check("setting it back on re-enables it", enabled())

    # A corrupt record must degrade, not explode — healing is an optimisation.
    data = load_locators()
    data[GROUP][NAME] = "a plain string, not a record"
    save_locators(data)
    try:
        r = record_heal(NAME, HEALED, score=0.9)
        check("a malformed record does not raise", True)
    except Exception as e:  # noqa: BLE001
        check("a malformed record does not raise", False, f"{type(e).__name__}: {e}")
finally:
    save_locators(_before)
    check("the database is restored exactly", load_locators() == _before)
    if _ENV_BEFORE is None:
        os.environ.pop("HEAL_MEMORY", None)
    else:
        os.environ["HEAL_MEMORY"] = _ENV_BEFORE

print("\n" + "=" * 60)
print(f"PASSED: {_passed}  |  FAILED: {_failed}")
print("=" * 60)
sys.exit(1 if _failed else 0)

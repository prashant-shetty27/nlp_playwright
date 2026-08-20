# Deferred — agreed to park, not forgotten

## 1. Self-healing write-back — BUILT, DISABLED pending real-run testing
**Status:** off. `HEAL_MEMORY=off` in `.env`.

Healing itself still runs during execution and still rescues a step. What is
switched off is only the part that SAVES a confident match back to the locator
database.

To turn on: set `HEAL_MEMORY=on` in `.env`. Nothing else changes.

What to watch when testing it:
- a heal is stored as an ALTERNATE first; the original selector stays primary,
  so the first runs should show no behaviour change at all;
- promotion to primary needs the original to fail and the alternate to succeed
  3 times (`PROMOTE_AFTER` in `locators/healing_memory.py`);
- the replaced selector is kept under `_superseded` — check it is there before
  trusting a promotion;
- spy-recorded elements are never touched, only the manual database.

Covered by `tests/test_healing_memory.py` (24 assertions), but never yet
exercised against a real failing page — which is exactly why it is off.

## 2. Folders and view/edit roles — NOT STARTED, needs a decision
Wanted: test cases (and elements, step groups, test data) organised in folders
like "Prashant - B2B" / "Manisha - Core"; everyone can VIEW anything; only the
owner and an admin can EDIT; admin can also approve rename/change requests.

**Blocked on identity.** There is no login, so nothing can attribute a test case
to a person or check who is asking. Two ways forward:

  a. Lightweight — a name chosen at startup, stored as an `owner` field. Fast,
     folders work immediately, but edit rights are a convention, not enforced.
  b. Real accounts — a session and a role. Slower, and genuinely enforces
     view-vs-edit and the admin approval queue that was asked for.

The folder structure is straightforward either way; the identity underneath it
is the decision.

Related, already built and usable meanwhile:
- duplicate/ambiguity checking at save time (name, selector, underscore-twin,
  variables) — `locators/validation.py`
- a "request a change" option is already offered as one of the choices when a
  name clashes; it currently has nowhere to send the request. That is the
  admin queue, and it belongs with (2).

## 3. Not built from earlier lists
- Clubbed steps — collapsible "Purpose is to…" bands over a run of steps,
  expanded by default, hideable and removable. Distinct from step groups.
  Prerequisite for the JIRA flow below.
- JIRA ticket review — read a ticket, advise manual vs spreadsheet vs prompt,
  then generate with those purpose bands.
- Testsigma import, batches 2+ — see section 4 below. Batch 1 (alerts, cookies,
  upload, window-by-title, parent frame) is done.

## 4. Testsigma command coverage — ANALYSED, NOT STARTED
Analysed 2026-08-20. Nothing implemented yet; the numbers below come from
`python3 tools/testsigma_gap.py`, which re-derives them from the exported
catalogue and from the runner's own dispatch table. Re-run it rather than
trusting these figures — both sides move.

**Where it stands.** 581 catalogue rows in scope once Android and iOS are
dropped (the user's call, for now). Folding duplicates away leaves **400 unique
(keyword, grammar) pairs**: 89 already dispatchable, **311 missing**.

Treat 311 as an UPPER BOUND. The join is a hand-written mapping of meaning
(`HAVE` in the tool) because neither the keyword nor the grammar text resembles
our wording, and anything absent from it counts as missing even if some command
of ours turns out to cover it. Each confirmed equivalence moves into `HAVE` and
the number drops honestly.

**311 is not 311 pieces of work.** They collapse into families:

     124  one-offs — element state, value comparisons, dropdown assertions,
          waits on URL / title / alert
      43  wait until <kind> with <attribute> is visible
      23  control flow — loops and conditionals
      17  web tables — cells, rows, columns
      15  select in a list identified by <attribute>
      13  mobile-only — OUT OF SCOPE, drop from any count
      13  check a checkbox/radio found by <attribute>
      11  files, spreadsheets and downloads
      10  verify a button/link/element/image by its text
       8  click a button/link/element by its text
     7/6/6/5/4/3/3  keyboard, sessions, cookies, scroll-by-offset,
          sort-order, execute-js, multi-select

The top families (43 + 15 + 13 + 10 + 8 = 89) are ONE mechanism with a lookup
table: find an element of kind K whose attribute A equals V, then act on it.
Building that mechanism once covers roughly a third of the gap.

**Proposed batch order** — biggest coverage per unit of work first, each batch
usable on its own:
  1. the by-attribute finder mechanism (unlocks the five families above)
  2. element state and value assertions, from the one-offs
  3. web tables
  4. files and downloads

**Two decisions needed before writing any of it:**
  a. **Salesforce.** Largest single block (241 rows) and mostly duplicates of
     the WebApplication grammars, already folded away by the dedupe. A few are
     genuinely Salesforce-specific (`loginAs`, button groups). In or out?
  b. **Control flow (23) and sessions (6).** `forloop`, `breakLoop`,
     `IfVerifyElement`, `createDriver`/`switchDriver` are not step commands —
     they are interpreter features, and need changes to the execution model
     rather than new entries in a dispatch table. Belongs in its own piece of
     work, not mixed into a command batch.

**Catalogue traps**, all observed, none guessable from the data:
  - `keyword` is not unique — `verifyTextContains` has six ids. Key on
    (keyword, normalised grammar); treat keyword as one-to-many.
  - Filter on each row's own `applicationType`; a WebApplication query also
    returns Salesforce rows.
  - `isAndroidSupported` / `isIosSupported` are false on all 779 offered rows.
    They carry no information — filtering on them empties the result.
  - The 112 legacy templates have no grammar and no API path to recover one.

Source: `~/ai-automation-engineer/artifacts/testsigma-nlp-templates.json`.

## 5. Housekeeping, agreed but deferred
- **Split `ui/pages/platform/test_cases.py`** (600+ lines). It holds the editor,
  selection, bulk actions, the review panel, rename, delete and three dialogs.
  The review panel and the dialogs are separable. Deferred because it is pure
  churn with real regression risk, and UI changes can only be verified by
  driving a browser — better done fresh, with the browser checks written before
  the move rather than after.
- **Dead-code review.** `demo_features.py` is imported by nothing.
  `control_panel.py` by one thing. `engine.py`, `recorder_ui.py` and
  `ui_builder.py` are still live but overlap with newer modules. Wants a proper
  pass, not a guess.

## Settled, no action needed
- **Anthropic model** stays on `claude-sonnet-5`. It has drafted every testcase
  since the switch, harvested values correctly and written sensible assertions,
  and it is cheaper and faster. Only revisit if drafting quality drops on a
  complicated ticket.
- **OpenAI key** left in `.env` as-is (no quota, unused).
- **Five flows failing lint** — the user's own in-progress testcases. Ignored on
  purpose; the Review panel shows what each needs.
- **Old run reports** — `python tools/prune_reports.py --apply` archives anything
  over 90 days into a zip under `data/logs/archive/` and only then deletes it.
  72 were archived on 2026-08-19.

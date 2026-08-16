---
name: testcase-authoring
description: Author or update codeless test assets for this framework — .flow scripts, suite JSON, plan JSON, and codeless action JSON — from a requirement, ticket, PRD or bug report. Use whenever asked to write test cases, add regression coverage, convert acceptance criteria into automation, or fix a failing flow. Portable across Claude Code and Claude Cowork.
---

# Authoring codeless test assets

You are writing test assets for an NLP/codeless automation framework. The assets are plain
text files, so they can be authored anywhere — but they are executed by a real runner with a
narrow grammar and a fixed locator database. **Anything you invent that is not in the
catalog will fail at run time.** The rule below is not optional.

## The one rule

> Never hand over a test asset you have not validated.
>
> ```sh
> python tools/flow_lint.py <files…>          # exit 0 = safe to run
> ```

The linter parses every step with the framework's real parser, resolves every locator name
against the real locator databases, and checks every command against the dispatch table of
the runner that will execute it. It needs no browser, no device and no network — so there
is never a reason to skip it.

## Step 1 — Load the catalog before writing anything

```sh
python tools/flow_lint.py --catalog
```

This is the ground truth. It gives you:

| Key | What it tells you |
| --- | --- |
| `command_support.web_runner` / `.appium_runner` | which commands each runner can execute |
| `command_support.web_only` / `.appium_only` | the commands that exist on **one** platform only |
| `locators.web_by_page` | every web locator name, grouped by page |
| `locators.appium_by_screen` | every iOS/Android locator, grouped by screen |
| `sites` | valid aliases for `open <alias>` |
| `codeless_actions` | valid `action` strings for codeless JSON |
| `known_grammar_traps` | steps that parse into the *wrong* command |

**You may only use locator names that appear in the catalog.** If a requirement needs an
element that is not recorded yet, do not invent a name and do not write a raw XPath — say
so, and list exactly which elements must be recorded first with the recorder/spy.

## Step 2 — Pick the platform, then respect its command set

The two runners are not interchangeable. The most common failure in generated flows is
using an Appium-only command in a web flow.

- **Appium-only** (iOS/Android): `tap`, `tap text`, `verify element exists`,
  `wait for element`, `press enter`, `dismiss alerts`, `hide keyboard`, `swipe left/right`,
  `long press`, `double tap`, and every `… if visible` / `… if exists` conditional.
- **Web-only**: `search for`, `refresh`, `wait for result page load`, `go forward`,
  `verify element <el> contains "…"`, `verify element <el> has text "…"`,
  `store attribute … of …`, `store value of …`, `store count of …`.

### Existence assertions differ by platform

| Platform | How to assert an element is present |
| --- | --- |
| iOS / Android | `verify element exists <locator>` |
| Web | `store text of <locator> as <var>` — resolves the locator, reads it, raises if absent, and exercises the self-healer. Or `verify element <locator> contains "<text>"`. |

`store count of <el> as <var>` does **not** fail when the count is zero — it stores `"0"`.
Never use it alone as an existence assertion.

## Step 3 — Write the flow

`.flow` files are line-oriented: one step per line, `#` starts a comment, blank lines are
ignored. There are **no loops, no conditionals and no branching** in the DSL. Data-driven
cases are written as explicit repeated blocks.

Structure every case with a header and arrange/act/assert comment bands:

```
# ============================================================================
# TC-WEB-002  Category search → results page
# ----------------------------------------------------------------------------
# Objective : What regression this catches, in one sentence.
# Platform  : web          Priority : P0
# Suite     : suites/web_regression_suite.json
# Params    : base_site, search_term
# ============================================================================

# ── Arrange ─────────────────────────────────────────────────────────────────
open ${base_site}
wait for result page load

# ── Act ─────────────────────────────────────────────────────────────────────
search for ${search_term}
wait for result page load

# ── Assert ──────────────────────────────────────────────────────────────────
verify element heading_search_title contains "${search_term}"
store text of business_name as first_business_name
take screenshot as tc002_verified
```

### Variables

- `${name}` resolves from suite `parameters` or from any earlier step that ends `as <name>`.
- A variable must be **defined before the line that uses it**. The linter enforces this.
- Prefer suite parameters over hardcoded literals — it is what makes a flow reusable across
  environments.

### Assertion guidance

- `verify text "X"` is a *contains* match anywhere on the page — cheap and robust.
- `verify exact text "X"` requires an element whose full text equals X — brittle; use only
  for stable labels.
- Prefer asserting on **element-scoped** text (`verify element <el> contains "…"`) over
  page-wide text when you care about *where* the text appears.
- Do not pin exact copy for empty states, error banners or promotional text — these change
  often. Assert on page survival and echoed user input instead.

## Step 4 — Wire it into a suite and plan

A flow is not runnable on its own if it uses `${params}`. Add it to a suite:

```json
{
  "suite_name": "…",
  "platform": "web",
  "scripts": ["flows/web/your_new_case.flow"],
  "desired_capabilities": { "browser": "chromium", "headless": false },
  "parameters": [{ "name": "search_term", "type": "string", "value": "Restaurants" }]
}
```

Then reference the suite from a plan's `selected_suites`. The linter follows
plan → suite → flow, so validating the plan validates the whole tree.

## Step 5 — Validate, then report honestly

```sh
python tools/flow_lint.py plans/<your_plan>.json
```

Fix every **error**. Read every **warning** and either fix it or explain why it is
acceptable. When you hand the work over, state:

1. what you created or changed,
2. the linter result (`N files checked — no issues`),
3. **which elements still need recording**, if any,
4. that the flows are statically valid but have **not been executed** — passing the linter
   proves the assets are well-formed, not that the application behaves correctly.

Never claim a test passes unless a run actually happened and you saw the report.

## Codeless JSON flows

The second format is a JSON array of `{action, parameters}` executed via `ACTION_REGISTRY`:

```json
[
  { "action": "Open Site", "parameters": { "target_url_or_key": "justdial" } },
  { "action": "Store Element Text",
    "parameters": { "locator": "business_name", "save_to_variable_name": "top_business" } }
]
```

Action names and parameter names must match the registry exactly — get them from
`--catalog`, and let the linter check the parameter names against each action's signature.

## Running

```sh
python plan_runner.py plans/web_regression_plan.json     # plan → suites → flows
python runner.py flows/web/your_case.flow                # single NLP flow
python runner.py .flow_files/your_case.json              # single codeless flow
```

Running drives a real browser or device. Do not run without the user asking.

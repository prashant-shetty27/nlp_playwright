# `.flow` grammar reference

The authoritative source is [`nlp/parser.py`](../nlp/parser.py); this document mirrors it.
For a machine-readable version that also includes your current locator inventory, run:

```sh
python tools/flow_lint.py --catalog
```

**File format.** One step per line. `#` starts a comment. Blank lines are ignored.
There are no loops, conditionals or branching — repeat blocks explicitly.

**Platform column.** `W` = executable by the web runner (`runner.py`), `A` = executable by
the Appium runner (`runner_appium.py`). A command marked only `A` will raise
`Unknown command type` in a web flow, and vice versa. `tools/flow_lint.py` enforces this.

---

## Navigation

| Step | Cmd | W | A |
| --- | --- | :-: | :-: |
| `open <alias\|url>` | `open` | ✅ | ✅ |
| `go to url <url>` / `navigate to <url>` / `browse to <url>` / `visit <url>` / `open url <url>` | `open` | ✅ | ✅ |
| `refresh` / `refresh page` / `reload` / `reload page` | `refresh` | ✅ | — |
| `press back` / `go back` | `press_back` | ✅ | ✅ |
| `go forward` / `browser forward` | `go_forward` | ✅ | — |
| `press home` | `press_home` | — | ✅ |

`<alias>` must be a key in [`config/sites.json`](../config/sites.json).

## Search & waiting

| Step | Cmd | W | A |
| --- | --- | :-: | :-: |
| `search for <text>` | `search` | ✅ | — |
| `wait <N> seconds` | `wait` | ✅ | ✅ |
| `wait for result page load` / `wait for results` / `results load` | `wait_for_result_page_load` | ✅ | — |
| `wait for element <el>` / `wait until element <el>` / `wait until <el> visible` | `wait_for_element` | — | ✅ |

`search for` takes an **unquoted** argument — everything after `search for ` is the term.

## Interaction

| Step | Cmd | W | A |
| --- | --- | :-: | :-: |
| `click <el>` / `click on <el>` / `click element <el>` | `click` | ✅ | ✅ |
| `tap <el>` / `tap on <el>` | `tap` | — | ✅ |
| `tap text "<label>"` | `tap_text` | — | ✅ |
| `double tap <el>` / `double click <el>` | `double_tap` | — | ✅ |
| `long press <el>` / `long tap <el>` / `hold <el>` | `long_press` | — | ✅ |
| `type "<text>" into <el>` / `fill "<text>" in <el>` | `fill` | ✅ | ✅ |
| `type "<text>"` (into the focused element) | `type_text` | — | ✅ |
| `press enter` / `press return` / `press search` | `press_enter` | — | ✅ |
| `hide keyboard` / `dismiss keyboard` | `hide_keyboard` | — | ✅ |
| `dismiss alerts` / `accept alerts` / `handle alerts` / `clear alerts` | `dismiss_alerts` | — | ✅ |
| `dismiss rating` / `skip rating` / `dismiss play rating` | `dismiss_play_rating` | — | ✅ |

### Conditional variants — Appium only

Both families skip silently when the element is absent.

| Step | Cmd |
| --- | --- |
| `click if exists <el>` / `tap if exists <el>` | `click_if_exists` |
| `type if exists "<text>" into <el>` | `fill_if_exists` |
| `tap if visible <el> [wait <N> seconds]` | `tap_if_visible` |
| `type if visible "<text>" into <el> [wait <N> seconds]` | `fill_if_visible` |
| `verify if visible <el> [wait <N> seconds]` | `verify_if_visible` |
| `double tap if visible <el> [wait <N> seconds]` | `double_tap_if_visible` |
| `long press if visible <el> [wait <N> seconds]` | `long_press_if_visible` |
| `store text if visible from <el> as <var> [wait <N> seconds]` | `store_text_if_visible` |

There is **no conditional equivalent on web.** A web flow that must tolerate an optional
popup has to be restructured, not guarded.

## Scrolling

| Step | Cmd | W | A |
| --- | --- | :-: | :-: |
| `scroll down` / `scroll up` | `scroll` | ✅ | ✅ |
| `scroll down <N> times` (N × 500px) | `scroll` | ✅ | ✅ |
| `scroll down <N>` (raw pixels) | `scroll` | ✅ | ✅ |
| `scroll to <el>` / `scroll to element <el>` | `scroll_to` | ✅ | ✅ |
| `scroll until text "<text>" visible[, scroll count <N>][, scroll wait <S>]` | `scroll_until_text_visible` | ✅ | ✅ |
| `swipe left` / `swipe right` | `swipe_left` / `swipe_right` | — | ✅ |

Defaults for `scroll until text`: count 10, wait 1s.

## Assertions

| Step | Cmd | W | A |
| --- | --- | :-: | :-: |
| `verify text "<text>"` — contains, anywhere on page | `verify_text` | ✅ | ✅ |
| `verify exact text "<text>"` — element text equals | `verify_exact_text` | ✅ | ✅ |
| `verify texts "<a>", "<b>", …` — each contained anywhere | `verify_multiple_texts` | ✅ | ✅ |
| `verify text "<text>" in <el>` | `verify_element_contains` | ✅ | — |
| `verify element <el> contains "<text>"` | `verify_element_contains` | ✅ | — |
| `verify element <el> has text "<text>"` | `verify_element_exact` | ✅ | — |
| `verify element exists <el>` / `assert element exists <el>` | `verify_element_exists` | — | ✅ |
| `verify element not exists <el>` | `verify_element_not_exists` | — | ✅ |
| `verify <var> contains "<text>"` / `verify stored <var> contains "<text>"` | `verify_var_contains` | ✅ | ✅ |
| `verify image "<path>" [threshold <N>%]` | `verify_image` | ⚠️ | — |

⚠️ `verify image` is registered in the web dispatch table as `lambda: None` — the step
**always passes without comparing anything.** Do not use it as a real assertion.

### Asserting existence on web

The web runner has no `verify element exists`. Use instead:

```
store text of <el> as <var>          # raises if absent; also exercises the self-healer
verify element <el> contains "<text>"  # existence + content in one step
```

`store count of <el> as <var>` stores `"0"` for a missing element rather than failing, so
it is a metric, not an assertion.

## Storing values

| Step | Cmd | W | A |
| --- | --- | :-: | :-: |
| `store text of <el> as <var>` / `store text from <el> as <var>` | `extract_text` | ✅ | ✅ |
| `store page url as <var>` | `extract_url` | ✅ | ✅ |
| `store page title as <var>` | `extract_title` | ✅ | ✅ |
| `store attribute <attr> of <el> as <var>` | `extract_attribute` | ✅ | — |
| `store value of <el> as <var>` | `extract_input` | ✅ | — |
| `store count of <el> as <var>` | `extract_count` | ✅ | — |
| `store "<value>" as <var>` | `create_variable` | ✅ | ✅ |
| `create variable <var> with value "<value>"` | `create_variable` | ✅ | ✅ |
| `calculate <a> <+ - * /> <b> as <var>` | `math` | ✅ | ✅ |

Reference a stored value as `${var}`. It must be defined on an **earlier line**, or supplied
as a suite parameter.

## Generated data

| Step | Cmd |
| --- | --- |
| `generate fake <kind> as <var>` | `generate_fake` |
| `generate random number <min> <max> as <var>` | `random_number` |
| `generate random string <length> as <var>` | `random_string` |

`<kind>`: `name`, `first name`, `last name`, `email`, `phone`, `uuid`, `number`, `address`,
`city`, `country`, `company`, `password`, `username`, `url`, `date`, `text`, `paragraph`,
`postcode`, `credit card`. Both runners support these.

## Date & time

| Step | Cmd |
| --- | --- |
| `get today as <var>` / `get current date as <var>` | `get_date` |
| `get timestamp as <var>` / `get now as <var>` | `get_date` |
| `get date +<N> days as <var>` / `get date -<N> days as <var>` | `get_date` |
| `format date "<value>" as "<pattern>" into <var>` | `format_date` |

## API & data files

| Step | Cmd |
| --- | --- |
| `api get "<url>" as <var>` | `api_get` |
| `api post "<url>" with body '<json>' as <var>` | `api_post` |
| `store json <var> path <a.b.0.c> as <var2>` | `extract_json` |
| `read excel "<file>" row <N> col <N\|A> as <var>` | `read_excel_cell` |
| `read excel "<file>" row <N> as <var>` | `read_excel_row` |
| `read csv "<file>" row <N> col <N\|name> as <var>` | `read_csv_cell` |

Paths are relative to the project root. Row indexing skips the header line — `row 1` is the
first data row. Both runners support these.

## Tabs, windows, iframes

| Step | Cmd | W | A |
| --- | --- | :-: | :-: |
| `switch to tab <N>` / `focus tab <N>` / `go to window <N>` | `switch_tab` | ✅ | ✅ |
| `close tab` / `close tab <N>` | `close_tab` | ✅ | ✅ |
| `close all tabs` | `close_all_tabs` | ✅ | ✅ |
| `list tabs` | `list_tabs` | ✅ | ✅ |
| `open new tab` | `open_new_tab` | ⚠️ | ⚠️ |
| `switch to iframe "<selector>"` / `enter frame <name>` | `switch_iframe` | ✅ | ✅ |
| `exit iframe` / `switch to main frame` / `switch to default content` | `exit_iframe` | ✅ | ✅ |

⚠️ See the grammar traps below — `open new tab` is unreachable.

## JavaScript actions

`js click <el>` · `js scroll to <el>` · `js scroll <down\|up\|top\|bottom> [N]` ·
`js type "<text>" into <el>` · `js set value "<text>" on <el>` · `js focus <el>` ·
`js submit <el>` · `js dispatch <event> on <el>`

On web these resolve through the locator database. On Appium they are passed to
`document.querySelector`, so they only work in a webview with a **CSS selector**.

## Screenshots & reusables

| Step | Cmd | W | A |
| --- | --- | :-: | :-: |
| `screenshot` / `take screenshot` / `capture screenshot` | `screenshot` | ✅ | ✅ |
| `take screenshot as <label>` | `screenshot` | ✅ | ✅ |
| `call <name>` — inline-expands a saved reusable step group | `call_reusable` | ✅ | ✅ |

Reuse a label and the earlier image is overwritten; the linter warns (W005).

---

## Grammar traps

These parse successfully into the **wrong** command, because an earlier regex in
`nlp/parser.py` shadows the intended rule. The linter flags all three (W001, W004).

| You write | You get | Use instead |
| --- | --- | --- |
| `open new tab` | `open(target="new tab")` — tries to navigate to a site alias named `new tab`; the `open_new_tab` rule is unreachable | — (no working web syntax) |
| `click text "Go"` | `click(target='text "Go"')` — treated as a locator name | `tap text "Go"` (Appium only) |
| `verify image "x.png"` | dispatches to `lambda: None` on web — always passes | a text or element assertion |

The cause in each case is rule ordering: the generic `open ` prefix rule at
`nlp/parser.py:32` and the generic `click ` prefix rule at `nlp/parser.py:194` run before
their more specific counterparts at `nlp/parser.py:451` and `nlp/parser.py:200`.

## Locator names

A locator argument is a **name from the locator database**, never a raw XPath or CSS
selector. Resolution differs by platform:

- **Web** — flat scan across every page group in `data/recorded_elements.json`, then
  `data/locators_manual.json`. First match wins, so names are effectively global.
- **Appium** — scoped to the `ios` / `android` / `hybrid` section only, matching flat names
  first, then one level of screen groups. An `[index]` suffix (`result_card[last]`,
  `row[2nd]`) is stripped before lookup.

List everything currently available:

```sh
python tools/flow_lint.py --catalog | python -m json.tool | less
```

New elements must be recorded with the recorder/spy before a flow can reference them.

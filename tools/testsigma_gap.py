#!/usr/bin/env python3
"""
tools/testsigma_gap.py — what Testsigma offers that this project cannot yet run.

    python3 tools/testsigma_gap.py                    # summary + families
    python3 tools/testsigma_gap.py --list             # every missing pair
    python3 tools/testsigma_gap.py --json out.json    # machine-readable

Reads the exported catalogue (see CATALOGUE below), drops the platforms we are
not covering yet, folds duplicates away, and joins what remains against the
commands the web runner actually dispatches.

Three things about the catalogue that will produce wrong answers if ignored —
all of them observed, none of them guessable from the data:

  * `keyword` is NOT unique. `verifyTextContains` appears six times with
    different ids: versioned duplicates, and the same grammar re-offered per
    application type. The unit of work is (keyword, normalised grammar), never
    the keyword alone.
  * A row's own `applicationType` is the only trustworthy one. A WebApplication
    query also returns Salesforce rows, so filter on the record.
  * `isAndroidSupported` / `isIosSupported` are false on every row. They carry
    no information; filtering on them silently empties the result.

The join is a hand-written mapping of MEANING (HAVE below). Neither the keyword
nor the grammar text resembles our wording, so nothing automatic would be
honest. It is deliberately conservative: a keyword absent from HAVE is reported
missing even if some command of ours turns out to cover it. Read the count as an
upper bound, and move keywords into HAVE as each one is confirmed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Exported from the Testsigma instance; see the metadata block inside it.
CATALOGUE = os.path.expanduser(
    "~/ai-automation-engineer/artifacts/testsigma-nlp-templates.json")

#: Parked deliberately — the web side is the one being covered first.
SKIP_TYPES = {"AndroidNative", "IOSNative"}


def normalise(grammar: str) -> str:
    """
    Collapse a grammar to its SHAPE, so versioned duplicates fold together.

    `${test-data}`, `${test-data1}` and any other `${...}` are the same slot as
    far as "do we support this sentence" goes; so are `#{ui-identifier}` and
    `@{attribute}`. Without this, 581 records look like 581 pieces of work when
    183 of them are the same sentences re-offered per application type.
    """
    s = (grammar or "").strip().lower()
    s = re.sub(r"\$\{[^}]+\}", "${d}", s)
    s = re.sub(r"#\{[^}]+\}", "#{e}", s)
    s = re.sub(r"@\{[^}]+\}", "@{a}", s)
    return re.sub(r"\s+", " ", s).strip(" .")


#: Testsigma keyword -> the command this project dispatches for it.
#: Add an entry only once the equivalence has actually been checked.
HAVE = {
    "click": "click", "doubleClick": "js_click", "rightClick": "js_dispatch",
    "clickIfPresent": "click_if_visible", "clickJavascript": "js_click",
    "submitForm": "js_submit", "enter": "fill", "enterJavascript": "js_set_value",
    "enterKeyBoardActions": "fill", "enterOTP": "enter_otp", "clear": "fill",
    "clearJavascript": "js_set_value", "navigateTo": "open",
    "reloadCurrentPage": "refresh", "navigateForward": "go_forward",
    "navigateBack": "press_back", "think": "wait", "mouseOver": "js_dispatch",
    "scroll": "scroll", "scrollTo": "scroll_to",
    "storeTitle": "extract_title", "storeCurrentURL": "extract_url",
    "storeText": "extract_text", "storeValue": "extract_input",
    "storeAttribute": "extract_attribute", "storeElementsCount": "extract_count",
    "deleteAllCookies": "delete_all_cookies", "deleteCookie": "delete_cookie",
    "getValueOfCookie": "verify_cookie", "acceptAlert": "accept_alert",
    "dismissAlert": "dismiss_alert", "verifyAlertPresent": "verify_alert_present",
    "verifyAlertAbsent": "verify_alert_present", "verifyAlertText": "verify_alert_text",
    "verifyTextPresent": "verify_page_contains",
    "verifyMobileTextPresent": "verify_page_contains",
    "verifyTextAbsent": "verify_page_not_contains",
    "verifyTitle": "verify_title_exact", "verifyTitleContains": "verify_title_contains",
    "verifyText": "verify_element_exact", "verifyTextContains": "verify_element_contains",
    "verifyElementPresent": "verify_element_visible",
    "verifyElementAbsent": "verify_element_not_exists",
    "verifyElementIsNotDisplayed": "verify_element_not_visible",
    "verifyNoText": "verify_element_not_contains",
    "waitUntilElementIsVisible": "wait_until_visible",
    "waitUntilElementIsNotVisible": "verify_element_not_visible",
    "waitUntilTextIsDisplayed": "verify_page_contains",
    "waitUntilPageloadIsCompleted": "wait_for_result_page_load",
    "switchToFrame": "switch_iframe", "switchToFrameUsingName": "switch_iframe",
    "switchToFrameByIndex": "switch_iframe", "switchToParentFrame": "parent_frame",
    "switchToDefaultContents": "parent_frame", "switchToWindowByIndex": "switch_tab",
    "switchToWindowByTitle": "switch_window_title", "closeCurrentWindow": "close_tab",
    "closeAllWindows": "close_all_tabs", "closeWindowUsingIndex": "close_tab",
    "activeWindow": "close_all_tabs", "newTab": "open_new_tab",
    "getWindows": "list_tabs", "upload": "upload_file",
    "executeJavaScript": "js_dispatch", "screenShotWithURL": "screenshot",
    "pageScreenShot": "screenshot", "fullPageScreenShotWithURL": "screenshot",
    "fullPageScreenShotUsingJS": "screenshot",
    "verifyImageWithAltTextPresent": "verify_image",
}

#: How the missing keywords group into things that share ONE mechanism. The
#: order matters — the first pattern that matches wins.
FAMILIES = [
    (r"^waitUntil\w+With(Text|Title|Label|Placeholder|PartialText|PartialLabel|AltText)"
     r"IsVisible$", "wait until <kind> with <attribute> is visible"),
    (r"^listBoxWith(Title|Label|Text|PartialLabel|PartialText)"
     r"By(Value|Text|Index|VisbleText)$", "select in a list identified by <attribute>"),
    (r"^check(Checkbox|RadioButton)With"
     r"(Index|Title|Text|Label|PartialText|PartialLabel|TextFollowing)$",
     "check a checkbox/radio found by <attribute>"),
    (r"^click(Button|Link|Element)With(Text|Title|TextContaining|PartialText)$",
     "click a button/link/element by its text"),
    (r"^verify(Button|Link|Element|Image)With"
     r"(Text|Title|PartialText|AltText|AltTitle)Present$",
     "verify a button/link/element/image by its text"),
    (r"^selectMultipleOptionBy(Value|VisibleText|Index)$", "select several options"),
    (r"^(storeRowNumbers?|storeColumnNumbers?|storeColumnName|clickCell|clickRow|"
     r"enterInTableCell|selectValueInTableCell|mouseOverOnTableCell|verifyRowCount|"
     r"verifyColumnCount|verifyTextPresentInTable|verifyElementInTable)$",
     "web tables — cells, rows, columns"),
    (r"^(compareFiles|compareCellValue|storeCellValue|writeValueIntoCell|storePageCount|"
     r"storeFile|storeFileNameFromUrl|getDownloadedFilePathUsingFileName|"
     r"getLatestDownloadedFilePath|splitString|waitUntilDownloadIsComplete)$",
     "files, spreadsheets and downloads"),
    (r"^(forloop|breakLoop|continueLoop|IfVerifyElement|IfTestCaseResult|"
     r"IfIterationResult|IfTestSuiteResult|verifyElement)$",
     "control flow — loops and conditionals"),
    (r"^(createDriver|switchDriver|quitDriver|loginAs|launchApp|closeApp)$",
     "sessions / app lifecycle"),
    (r"^(setOrientationAs\w+|verifyOrientationIs\w+|zoomInOutOnScreen|"
     r"SwipeTestDataInTheScreen|hideKeyboard|tapCoordinates|navigateHome|TapInBrowser|"
     r"pressTestDataKey|verifyAppIsInstalled|longPressOnElement)$",
     "mobile-only — OUT OF SCOPE for the web batches"),
    (r"^(checkAscendingOrder|checkDescendingOrder|checkNumbersIn\w+Order)$",
     "sort-order assertions"),
    (r"^press(Enter|Tab|Escape|Shiftdelete|Key)$", "keyboard keys"),
    (r"^(storeAllCookies|storeCookieNamed|storeCookieValue|getAllCookies|"
     r"getAllCookieNames|deleteLocalStorage)$", "cookies and local storage"),
    (r"^(scroll(ToOffset|ElementToOffset|InsideElementToTop|InsideElementToBottom)|"
     r"moveElementToOffset)$", "scrolling by offset"),
    (r"^executeJavaScript", "execute javascript variants"),
]


def load_pairs() -> dict:
    """{(keyword, normalised grammar): record} for the platforms in scope."""
    if not os.path.exists(CATALOGUE):
        sys.exit(f"Catalogue not found: {CATALOGUE}")
    rows = [t for t in json.load(open(CATALOGUE))["templates"]
            if t["applicationType"] not in SKIP_TYPES]
    pairs: dict = {}
    for t in rows:
        pairs.setdefault((t["keyword"], normalise(t["grammar"])), t)
    return pairs, rows


def our_commands() -> set:
    """What the web runner dispatches, derived from its own dispatch table."""
    out = subprocess.run([sys.executable, "tools/flow_lint.py", "--catalog"],
                         cwd=BASE_DIR, capture_output=True, text=True)
    return set(json.loads(out.stdout)["command_support"]["web_runner"])


def family_of(keyword: str) -> str:
    for pattern, label in FAMILIES:
        if re.match(pattern, keyword):
            return label
    return "(one-off)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--list", action="store_true", help="print every missing pair")
    ap.add_argument("--json", metavar="PATH", help="write the missing set as JSON")
    args = ap.parse_args()

    pairs, rows = load_pairs()
    ours = our_commands()
    covered, missing = [], []
    for (keyword, grammar), rec in sorted(pairs.items()):
        command = HAVE.get(keyword)
        (covered if command in ours else missing).append((keyword, grammar, rec))

    print(f"catalogue rows in scope (Android/iOS dropped) : {len(rows)}")
    print(f"unique (keyword, grammar) pairs               : {len(pairs)}")
    print(f"  already dispatchable                        : {len(covered)}")
    print(f"  missing (upper bound — see HAVE)            : {len(missing)}\n")

    buckets = defaultdict(list)
    for keyword, grammar, _ in missing:
        buckets[family_of(keyword)].append((keyword, grammar))
    for label, items in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(items):3}  {label}")

    if args.list:
        print()
        for label, items in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
            print(f"\n── {label} ({len(items)})")
            for keyword, grammar in sorted(items):
                print(f"   {keyword:46} {grammar}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"missing": [
                {"keyword": k, "grammar": g, "id": r["id"],
                 "applicationType": r["applicationType"],
                 "action": r.get("action"), "family": family_of(k)}
                for k, g, r in missing]}, f, indent=1)
        print(f"\nwritten: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
ai_flow_builder/prompt_source.py — turn a written request into testcases.

Pass 1 of two. The operator describes what they want tested in plain language;
Claude turns that into testcase steps in the SAME shape the spreadsheet provides,
so everything downstream — the mapper, locator reuse, per-step statuses, flow
emission, linting — is the code path that already exists and is already tested.

Deliberately NOT generating .flow statements here. Prose is reviewable: the
operator reads the testcase before a single step is generated, and can correct
the intent rather than debugging a flow that encoded the wrong intent correctly.

Two things keep this honest:

  the model is given the REAL vocabulary — the mapper's step shapes and the
  locator names actually on file — rather than inventing its own, so its output
  lands inside what the deterministic mapper can already handle.

  values the operator wrote in their request (URLs, mobile numbers) are
  extracted rather than invented. Nothing is filled in with a plausible-looking
  placeholder; anything absent is reported as an input to collect.
"""
from __future__ import annotations

import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


# The step shapes ai_flow_builder/mapper.py can already map deterministically.
# Giving the model this list is what keeps pass 2 mostly on the mapper's rails
# instead of leaning on a second model call for every step.
STEP_SHAPES = [
    "Navigate to <url-or-variable>",
    "Click on element <ElementName>",
    "Enter <value> in element <ElementName>",
    "Verify that element <ElementName> is displayed",
    "Verify that element <ElementName> is not displayed",
    "Verify that element <ElementName> is not present in DOM",
    "Verify that the text of element <ElementName> is <expected>",
    "Wait until element <ElementName> is displayed",
    "Store the count of elements <ElementName> in variable <VarName>",
    "Swipe left on element <ElementName>",
    "Swipe right on element <ElementName>",
    "Clear browser cookies",
]

def step_vocabulary() -> list[str]:
    """
    Every step the runtime can execute, as the sentence a tester writes.

    Read from the live keyword map, so a step type added to the parser is known
    to the drafter the same day. The old fixed list of twelve shapes was why a
    ticket with an API check drafted "no step shape exists for API calls" while
    `api get "…" as <var>` had been in the grammar for months.
    """
    shapes: list[str] = []
    try:
        from nlp.keywords import KEYWORD_MAP

        for entry in KEYWORD_MAP.values():
            t = (entry or {}).get("template")
            if not t:
                continue
            # A step whose meaning is not obvious from its words carries a
            # one-line description; without it the model wrote assumptions
            # about what a step checks instead of using it with confidence.
            line = f"{t}   — {entry['help']}" if entry.get("help") else t
            if line not in shapes:
                shapes.append(line)
    except Exception:  # noqa: BLE001
        pass
    return shapes or list(STEP_SHAPES)


SYSTEM = """You turn a tester's written request — and the Jira ticket(s) it names — into \
structured, END-TO-END testcases for an existing codeless automation framework.

WRITE EVERY STEP IN THE FRAMEWORK'S OWN GRAMMAR. Each line below is one executable \
step; {{locator}} is an element name, {{text}} / {{url}} / {{number}} a value, \
{{variable}} a runtime variable name (write it without ${{}} when it is being \
DEFINED with "as", and as ${{name}} when it is being USED):

{shapes}

Legacy manual shapes are also accepted, but prefer the grammar above:

{legacy}

Steps that make a test DEBUGGABLE, use them:
- `# <why this block exists>` — a comment line; write one before each logical \
block (setup / action / assertion) so a failure is read in context.
- `store text of <locator> as <var>`, `store count of <locator> as <var>`, \
`store attribute <attr> of <locator> as <var>`, `store regex "<pattern>" from page \
url as <var>` — capture what the page shows BEFORE asserting on it, so the report \
carries the actual value.
- `api get "<url>" as <var>` + `store json <var> path a.b.0.c as <var2>` + \
`verify stored <ui_var> contains "${{api_var}}"` — when the ticket names an API, \
compare UI against API rather than against a typed expectation.
- `verify stored <var> is greater than <n>`, `verify stored <var> is not "<v>"`.

ELEMENTS ALREADY RECORDED on this platform, grouped by the page/section they were \
recorded on. REUSE these names verbatim whenever a step refers to one of them — a \
recorded element runs today, a newly named one has to be recorded first. Only \
invent a new name (snake_case, describing what it is) for something genuinely not \
listed:

{locators}

TEST DATA ALREADY SAVED (name → what it holds). Reference these as ${{name}} instead \
of inventing a new variable for the same thing — a saved value needs no typing:

{testdata}

EXISTING TEST CASES on this platform that touch the same feature. Their steps show \
which elements and patterns already work (auth, API calls, ordering checks). Reuse \
their element names and step patterns; do not duplicate a case that already exists \
— reference it in `covers` as "existing: <name>" and move on:

{existing}

Reusable step groups already saved on this platform. When the request describes a \
sequence one of these already covers — opening the site and dismissing the login \
popup, signing in, and so on — emit the single step `call <name>` instead of \
rewriting its steps. Reusing one keeps the sequence defined in a single place, so \
a change to it is one edit rather than one per testcase:

{stepgroups}

Rules that matter:

- EVERY concrete value is a ${{variable}} in the steps. Never write a literal URL, \
phone number, OTP, password, email or ID into a step, even when the request states \
it plainly. Generated flows are committed to source control and read by people who \
are not entitled to the test data, so a real mobile number written into a step is a \
leak; it also pins the flow to one environment. Write \
`Enter ${{test_mobile}} in element MobileNumberInput`, never the digits.
- A value the request DOES state goes in values_found, keyed by the same variable \
name you used in the steps — record it there in full. values_found is a separate \
channel handed back to the operator to confirm; it is NOT written into the flow \
file, so putting the stated mobile number or URL there loses nothing and leaks \
nothing. Omitting it does lose something: the operator has to type a value they \
already gave you.
- NAME a variable after WHAT it holds, never after its role in one testcase. A URL \
is `url_<category>_<city>` for devx (e.g. url_carpet_wholesalers_mumbai) or \
`live_url_<category>_<city>` for www; one variable per distinct URL, and a URL the \
tester gave for ONE purpose (e.g. "use this for Ask For Price only") is used only by \
the testcases for that purpose. Never a generic `product_category_url` / `page_url` \
shared by unrelated testcases — the operator cannot tell what value belongs in it. \
When a Test Data entry already holds the value (the TEST DATA list above), use that \
name as-is instead of inventing a new one.
- A value the request does NOT state goes in inputs_needed. Never invent one to \
fill a gap — a plausible-looking invented value is worse than an obvious gap, \
because it looks correct and fails later.
- Expected TEXT the user should see (button labels, messages, headings) is not a \
value in this sense — write those literally, since they are what the assertion is \
checking.
- Split distinct scenarios into separate testcases. One testcase is one flow.
- expected describes what proves the case passed, not what the tester does.
- Keep steps atomic — one action or one assertion each.
- Do not add setup, teardown or cleanup steps the request did not ask for.

WHEN A JIRA BRIEF IS ATTACHED, it is the specification — the tester's sentence is \
only the pointer to it. Then:
- Cover EVERY acceptance criterion / rule in the story with at least one Positive \
testcase, and every rule that can be violated with a Negative one (wrong order, \
missing element, wrong heading for the other category, zero results, bad input).
- Every linked Defect / Concern is a regression testcase of its own: reproduce the \
scenario the defect describes and assert the fixed behaviour.
- Use the API / FE / data sub-tasks for endpoints, field names, headings and \
parameters — write those literally, they are what is being checked.
- Comments carry test URLs, staging notes and environment caveats — use the URLs as \
values_found, and put caveats into assumptions.
- Put the ticket key and the criterion each testcase covers in `covers`, e.g. \
["GJDT-22686 AC3"] or ["GJDT-22699"], so coverage can be traced back.
- Read the whole hierarchy before writing: a testcase for a priority rule must \
capture the ORDER (positions, cities, prices) and assert on it, not merely that \
the section is displayed.
- Prefer several focused testcases over one long one; each testcase must stand \
alone (opens its own page).

ASK, DO NOT ASSUME. Whenever a step depends on something neither the request nor \
the ticket states — whether a flow needs login / mobile / OTP, which test account \
or number to use, where an OTP can be read from, which environment or URL, what \
"correct" looks like for an unspecified rule — put a concrete question in \
`questions` (with why it matters and an example answer) AND still write the \
testcase with your best reading, marked in `assumptions`. The tester answers the \
questions and the draft is redone with the answers appended to the request — so a \
question must be answerable in one line.

When the tester's request supplies an OTP source, use it instead of assuming: an \
OTP API is `api get "<url with ${{test_mobile}}>" as otpResp`, then \
`store json otpResp path <path to the message text> as otpText`, then \
`store regex "(\\d{{4,8}})" from otpText as otp`, then \
`enter otp "${{otp}}" into <otp locator>` (or `type` for a single field). The \
built-in `fetch otp for "${{mobile}}" as otp` reads the QA portal instead.
A request may contain a section "Answers from the tester:" — those override any \
assumption and must not be asked again.
"""


def _models():
    from typing import Any

    from pydantic import BaseModel, Field

    # Every field the model might reasonably leave out or type differently
    # (a number for a priority, a list for a precondition) is tolerated, then
    # normalised in code. A draft that is 95% right must not be thrown away
    # over a schema nit — that cost a full model call, twice, and returned
    # "output did not match the expected shape" to the operator.
    class Testcase(BaseModel):
        testcase_id: str = Field(default="", description="Short stable id, e.g. TC_PROMPT_01")
        title: str = Field(default="", description="One line describing what is verified")
        preconditions: list[str] = Field(default_factory=list)
        steps: list[str] = Field(default_factory=list, description="Ordered steps, one action each")
        expected: str = Field(default="", description="What proves this testcase passed")
        priority: str = Field(default="Medium", description="Critical|High|Medium|Low")
        classification: str = Field(default="Positive", description="Positive|Negative")
        covers: list[str] = Field(default_factory=list,
                                  description="Ticket keys / acceptance criteria this testcase verifies")

    class InputNeeded(BaseModel):
        name: str = Field(default="", description="Variable name used in the steps, no ${} wrapper")
        kind: str = Field(default="text", description="url|mobile|otp|email|text|number")
        why: str = Field(default="", description="What this value is for")

    class Question(BaseModel):
        question: str = Field(default="", description="One concrete question for the tester")
        why: str = Field(default="", description="Which testcase(s) change depending on the answer")
        example: str = Field(default="", description="An example of an acceptable answer")

    class Draft(BaseModel):
        testcases: list[Testcase]
        keep: list[str] = Field(
            default_factory=list,
            description="When a previous draft was given: ids of its testcases to keep UNCHANGED "
                        "(they are not repeated in `testcases`)",
        )
        questions: list[Question] = Field(
            default_factory=list,
            description="Things to ASK the tester rather than assume — answers change the steps",
        )
        inputs_needed: list[InputNeeded] = Field(
            default_factory=list,
            description="Values the request did not supply and must be collected",
        )
        values_found: dict[str, Any] = Field(
            default_factory=dict,
            description="Values taken verbatim from the request, variable name -> value",
        )
        assumptions: list[str] = Field(
            default_factory=list,
            description="Anything inferred that the request did not state outright",
        )
        unclear: list[str] = Field(
            default_factory=list,
            description="Parts of the request too ambiguous to turn into steps",
        )

    return Draft


# Same for URLs — a trailing . , ; ) ends the sentence, not the address.
#: A URL with OR without a scheme. Requiring "https://" meant a prompt that
#: said "www.justdial.com" — which is how people actually write it — harvested
#: nothing, and the generated flow then referenced ${justdial_url} with no value
#: behind it.
_URL = re.compile(
    r"(?:https?://[^\s<>\"')\]]*[^\s<>\"')\].,;:]"
    r"|(?:www\.)[\w.-]+\.[A-Za-z]{2,}(?:/[^\s<>\"')\]]*)?"
    r"|(?<![\w.@])[\w-]+\.(?:com|in|net|org|io|co)(?:\.[A-Za-z]{2,})?(?:/[^\s<>\"')\]]*)?)")

#: "search for baldev engineering" — the thing being searched for. A search term
#: is one of the commonest values in a prompt and had no kind at all, so it could
#: never be harvested no matter how plainly it was written.
_SEARCH_TERM = re.compile(
    r"search(?:ing)?\s+(?:for\s+)?[\"']?([A-Za-z0-9][A-Za-z0-9 &._-]{2,48}?)[\"']?"
    r"(?=\s*(?:$|\n|,|\.|&|and\b|then\b|select\b|in\b|on\b))",
    re.I | re.M)
_MOBILE = re.compile(r"(?<!\d)(\d{10})(?!\d)")
_OTP = re.compile(r"(?<!\d)(\d{4,8})(?!\d)")
# Trailing sentence punctuation is not part of the address.
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]*[\w]")


def harvest_values(prompt: str, draft: dict) -> dict:
    """
    Recover the values the operator actually wrote, and bind them to the
    variables the model used.

    Matching is by KIND, not by position: a variable whose name looks like a URL
    takes the URL from the prompt, one that looks like a mobile takes the
    10-digit number. That survives the model naming things differently from run
    to run, which name-matching would not.

    Only values literally present in the prompt are ever returned — nothing is
    inferred, defaulted or generated.
    """
    referenced: list[str] = []
    for tc in draft.get("testcases", []):
        for step in tc.get("steps", []):
            referenced += re.findall(r"\$\{([A-Za-z0-9_]+)\}", step)
    seen, ordered = set(), []
    for name in referenced:
        if name not in seen:
            seen.add(name)
            ordered.append(name)

    urls = [u for u in _URL.findall(prompt)
            if "/browse/" not in u and "jira" not in u.lower()      # the ticket is not a value
            and "docs.google.com" not in u.lower()]                 # nor the sheet it came in
    emails = _EMAIL.findall(prompt)
    # An API endpoint is a URL, but not the page under test: kept apart so a
    # variable called api_base never takes the search URL, or vice versa.
    api_urls = [u for u in urls if re.search(r"api|\.php\?|/rest/|/v\d/", u, re.I)]
    urls = [u for u in urls if u not in api_urls]

    # Numbers are scanned only in the text OUTSIDE any URL or email. A product
    # URL like .../jdm-1113060-ent-4-23785 is full of digit runs that look
    # exactly like OTPs; harvesting one and presenting it as the operator's OTP
    # would be inventing a value while appearing to have found it — the precise
    # failure this function exists to prevent.
    residue = prompt
    for token in urls + emails:
        residue = residue.replace(token, " ")

    mobiles = _MOBILE.findall(residue)
    otps = [o for o in _OTP.findall(residue) if o not in mobiles and len(o) != 10]

    searches = [m.strip() for m in _SEARCH_TERM.findall(prompt) if m.strip()]
    pools = {"url": list(urls), "api": list(api_urls), "email": list(emails),
             "mobile": list(mobiles), "otp": list(otps),
             "search": searches}

    def kind_of(var: str) -> str:
        low = var.lower()
        if any(k in low for k in ("api", "endpoint")):
            return "api"
        if any(k in low for k in ("url", "link", "page")):
            return "url"
        if any(k in low for k in ("mobile", "phone", "msisdn")):
            return "mobile"
        if "otp" in low:
            return "otp"
        if "email" in low or "mail" in low:
            return "email"
        if any(k in low for k in ("search", "term", "query", "keyword")):
            return "search"
        return ""

    found: dict[str, str] = {}
    for var in ordered:
        kind = kind_of(var)
        if kind and pools.get(kind):
            found[var] = pools[kind].pop(0)
    return found


#: ${var} as written into a drafted step.
from nlp.variables import REFERENCE_RE as _VAR_IN_STEP  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════════
# Canonical parameter names
# ═══════════════════════════════════════════════════════════════════════════════
#
# The model names variables freely: the same prompt drafted twice produced
# "${test_url}" one time and "${page_url}" the next. Both are internally
# consistent, so nothing breaks — but two flows for the same thing then demand
# differently-named inputs, and a suite cannot supply one value to both.
#
# Rather than ask the model to be consistent (it cannot promise that), the names
# are normalised HERE, in code, after drafting. Deterministic, testable, and
# independent of which provider or model produced the draft.
#
# Add a synonym to this table and every future draft picks it up; the canonical
# names on the right are the vocabulary a suite writes against.
_CANONICAL_VARIABLES = {
    "url":         ("url", "page_url", "test_url", "site_url", "target_url",
                    "product_url", "pdp_url", "landing_url", "base_url"),
    "mobile":      ("mobile", "test_mobile", "mobile_number", "mobile_no", "phone",
                    "phone_number", "contact_number", "msisdn"),
    "otp":         ("otp", "test_otp", "otp_code", "verification_code", "one_time_password"),
    "email":       ("email", "test_email", "email_id", "email_address", "user_email"),
    "password":    ("password", "test_password", "passwd", "pwd", "user_password"),
    "username":    ("username", "test_username", "user_name", "login_id", "user_id"),
    "search_term": ("search_term", "search_text", "search_query", "query", "keyword",
                    "search_keyword"),
    "city":        ("city", "test_city", "city_name", "location"),
    "pincode":     ("pincode", "pin_code", "postal_code", "zip_code"),
}

#: synonym -> canonical, built once.
_SYNONYM_TO_CANONICAL = {
    syn: canonical
    for canonical, synonyms in _CANONICAL_VARIABLES.items()
    for syn in synonyms
}


def canonical_name_for(name: str) -> str:
    """The canonical parameter name for `name`, or `name` unchanged if unknown."""
    return _SYNONYM_TO_CANONICAL.get((name or "").strip().lower(), name)


def canonicalise_variables(draft: dict) -> dict:
    """
    Rename drafted variables to the canonical vocabulary, in place.

    Two rules, both deterministic:

      * a recognised synonym becomes its canonical name;
      * if two DIFFERENT source names would collapse onto one canonical name,
        the first (in step order) keeps it and later ones get a numeric suffix,
        so two distinct URLs stay two distinct parameters rather than silently
        becoming one.

    Unrecognised names are left exactly as the model wrote them — inventing a
    canonical form for something not in the table would be guessing.
    """
    renames: dict[str, str] = {}
    taken: set[str] = set()

    def claim(original: str) -> str:
        if original in renames:
            return renames[original]
        canonical = canonical_name_for(original)
        if canonical in taken and canonical != original:
            n = 2
            while f"{canonical}_{n}" in taken:
                n += 1
            canonical = f"{canonical}_{n}"
        elif canonical in taken:
            canonical = original          # keep its own name rather than collide
        taken.add(canonical)
        renames[original] = canonical
        return canonical

    # Step order decides who wins a canonical name, so the result does not depend
    # on dict ordering.
    for tc in draft.get("testcases", []) or []:
        for step in tc.get("steps", []) or []:
            for var in _VAR_IN_STEP.findall(step or ""):
                claim(var)
    for var in list((draft.get("values_found") or {}).keys()):
        claim(var)

    if not any(k != v for k, v in renames.items()):
        return draft                      # nothing to do

    def rewrite(text: str) -> str:
        return _VAR_IN_STEP.sub(
            lambda m: "${" + renames.get(m.group(1), m.group(1)) + "}", text or "")

    for tc in draft.get("testcases", []) or []:
        tc["steps"] = [rewrite(s) for s in tc.get("steps", []) or []]
        if tc.get("expected"):
            tc["expected"] = rewrite(tc["expected"])
    draft["values_found"] = {renames.get(k, k): v
                             for k, v in (draft.get("values_found") or {}).items()}
    for item in draft.get("inputs_needed", []) or []:
        if isinstance(item, dict) and item.get("name") in renames:
            item["name"] = renames[item["name"]]
    draft["renamed_variables"] = {k: v for k, v in renames.items() if k != v}
    return draft


def _suggest_groups(draft: dict, platform: str) -> list[dict]:
    """
    Sequences in the draft that an existing step group already covers.

    The model is told about the groups, but it does not always take the hint —
    so the draft is checked afterwards too. Reported, never applied: replacing
    three written steps with a `call` changes what the testcase says, and that
    is the author's decision.
    """
    try:
        from core.reusable_steps import describe as _describe_groups

        groups = _describe_groups(platform)
    except Exception:  # noqa: BLE001
        return []

    out: list[dict] = []
    for tc in draft.get("testcases", []) or []:
        steps = [str(s).strip().lower() for s in tc.get("steps", []) or []]
        for g in groups:
            gs = [str(s).strip().lower() for s in g.get("steps", [])]
            if not gs or len(gs) > len(steps):
                continue
            for i in range(len(steps) - len(gs) + 1):
                if steps[i:i + len(gs)] == gs:
                    out.append({"testcase_id": tc.get("testcase_id", ""),
                                "group": g["name"], "at_step": i + 1,
                                "replaces": len(gs),
                                "call": f"call {g['name']}"})
                    break
    return out


def _locators_block(platform: str, known: list[str]) -> str:
    """Recorded elements grouped by page, so the model sees WHERE each lives."""
    try:
        from locators.sources import entries

        by_group: dict[str, list[str]] = {}
        for e in entries(platform):
            by_group.setdefault(e.group, []).append(e.name)
        if by_group:
            lines = []
            for g in sorted(by_group):
                names = sorted(set(by_group[g]))
                lines.append(f"  [{g}] " + ", ".join(names[:60])
                             + (f" … (+{len(names) - 60})" if len(names) > 60 else ""))
            return "\n".join(lines[:80])
    except Exception:  # noqa: BLE001
        pass
    return ("\n".join(f"  {n}" for n in known[:300]) if known
            else "  (none on file yet — name elements descriptively)")


def _testdata_block() -> str:
    """Saved values by name, masked — enough to reuse, never enough to leak."""
    try:
        from execution.test_data import get_all

        rows = get_all("")
    except Exception:  # noqa: BLE001
        return "  (none)"
    def shown(n: str, v: dict) -> str:
        # A URL or a category id is only useful to the model in full; a
        # number or anything credential-shaped stays masked.
        if v.get("is_secret") or any(k in n.lower() for k in ("mobile", "phone", "otp", "pin")):
            return v.get("display", "")
        val = str(v.get("value", ""))
        return val if len(val) <= 160 else val[:157] + "…"

    lines = [f"  {n} → {shown(n, v)}" for n, v in sorted(rows.items())
             if not str(n).startswith("_")]
    return "\n".join(lines[:120]) or "  (none)"


def _existing_cases_block(platform: str, prompt: str, brief: str, limit: int = 3) -> str:
    """
    The saved test cases most related to this request, with their steps.

    Relevance is word overlap between the case's name+steps and the request +
    ticket brief — crude, but it reliably surfaces "the case that already
    tests this carousel". Only a few are shown, in full, because their VALUE
    is the concrete element names and step patterns, not the list.
    """
    try:
        from config import settings

        flows_dir = settings.FLOWS_DIR
        words = set(re.findall(r"[a-z]{4,}", (prompt + " " + brief).lower()))
        scored = []
        for fn in os.listdir(flows_dir):
            if not fn.endswith(".flow") or fn.startswith("_"):
                continue
            path = os.path.join(flows_dir, fn)
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            head = text[:200].lower()
            if platform and f"# platform: {platform}" not in head:
                continue
            body = set(re.findall(r"[a-z]{4,}", (fn + " " + text).lower()))
            score = len(words & body)
            if score:
                scored.append((score, fn[:-5], text))
        scored.sort(reverse=True)
        out = []
        for _score, name, text in scored[:limit]:
            steps = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
            out.append(f"  == {name} ({len(steps)} steps)")
            out += [f"     {ln}" for ln in steps[:40]]
            if len(steps) > 40:
                out.append(f"     … (+{len(steps) - 40} more)")
        return "\n".join(out) or "  (none related)"
    except Exception:  # noqa: BLE001
        return "  (none related)"


def _extend_block(flow_name: str) -> str:
    """A saved test case, in full, that the drafted steps will be appended to."""
    from config import settings

    path = os.path.join(settings.FLOWS_DIR, f"{flow_name}.flow")
    with open(path, "r", encoding="utf-8") as f:
        lines = [ln.rstrip() for ln in f.read().splitlines() if ln.strip()]
    steps = [ln for ln in lines if not ln.startswith("#")]
    out = [f"===== EXISTING TEST CASE TO EXTEND: {flow_name} ({len(steps)} steps) =====",
           "(new steps run after the last line below, in the same browser session)"]
    out += [f"    {ln}" for ln in lines]
    return "\n".join(out)


def _previous_block(previous: dict) -> str:
    """The earlier draft, compact, for an incremental re-draft."""
    out = ["===== PREVIOUS DRAFT (already reviewed by the tester) ====="]
    for tc in previous.get("testcases") or []:
        out.append(f"[{tc.get('testcase_id')}] ({tc.get('classification','')}) {tc.get('title','')}")
        for st in tc.get("steps") or []:
            out.append(f"    {st}")
    return "\n".join(out)


def merge_incremental(previous: dict, delta: dict) -> dict:
    """
    Previous cases the model kept + the cases it returned (new or changed).
    Order: previous order for kept/changed, new ones appended — so the list
    the tester already looked at does not shuffle under them.
    """
    keep = set(delta.get("keep") or [])
    returned = {tc.get("testcase_id"): tc for tc in delta.get("testcases") or []}
    merged: list[dict] = []
    for tc in previous.get("testcases") or []:
        tid = tc.get("testcase_id")
        if tid in returned:
            merged.append(returned.pop(tid))
        elif tid in keep:
            merged.append(tc)
    merged += list(returned.values())
    delta["testcases"] = merged
    delta["kept_ids"] = sorted(keep)
    return delta


def draft_testcases(prompt: str, platform: str = "website",
                    max_testcases: int = 10, provider: str | None = None,
                    model: str | None = None, previous: dict | None = None,
                    attachments: list[dict] | None = None,
                    extend_flow: str | None = None) -> dict:
    """Pass 1 — a written request becomes reviewable testcases.

    ``extend_flow``: the name of a saved test case the drafted steps will be
    APPENDED to (e.g. the Call-Now lead check added months after the case was
    written). The model sees that case in full and drafts only the addition.
    """
    from ai_flow_builder.catalogue import load as load_catalogue
    from nlp.platforms import normalise

    platform = normalise(platform)
    cat = load_catalogue(platform)
    known = sorted(cat.locators)

    from ai_flow_builder.llm import get_provider

    Draft = _models()
    # Saved step groups are offered to the model so a repeated sequence becomes
    # `call <name>` rather than being written out again. Without this the model
    # cannot know they exist and every testcase re-states the same opening steps.
    try:
        from core.reusable_steps import describe as _describe_groups

        groups = _describe_groups(platform)
    except Exception:  # noqa: BLE001
        groups = []
    group_lines = "\n".join(
        f"  call {g['name']}   — {'; '.join(g['steps'][:4])}"
        + ("…" if len(g["steps"]) > 4 else "")
        for g in groups[:40]
    ) or "  (none saved yet)"

    # The ticket(s) the prompt names, read in full. A failure to read one is
    # surfaced to the operator, not silently drafted around — a draft that
    # looks complete but never saw the acceptance criteria is the worst outcome.
    briefs, brief_text, jira_meta = [], "", []
    try:
        from ai_flow_builder import jira_source

        keys = jira_source.find_keys(prompt)
        if keys:
            briefs = jira_source.fetch_briefs_for(prompt)
            brief_text = "\n\n".join(b.as_text() for b in briefs)
            jira_meta = [b.as_dict() for b in briefs]
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"The prompt names a Jira ticket but it could not be read: {e}") from e

    # Spreadsheets: attached by the tester (already read into text by the
    # caller) or linked as Google Sheets in the prompt. Their rows are the
    # team's own cases and become the baseline the draft must cover.
    sheet_texts: list[str] = [a.get("text", "") for a in (attachments or []) if a.get("text")]
    sheet_problems: list[str] = []
    try:
        from ai_flow_builder.sheet_source import sheets_from_prompt

        # Links in the ticket's comments count too — that is where the QA
        # round's case sheet gets posted.
        comment_text = "\n".join(c[2] for b in briefs for c in b.comments) if briefs else ""
        found, sheet_problems = sheets_from_prompt(prompt + "\n" + comment_text)
        sheet_texts += found
        for b in briefs:                         # spreadsheets attached to the ticket
            sheet_texts += list(getattr(b, "sheets", []) or [])
    except Exception as e:  # noqa: BLE001
        sheet_problems.append(str(e))

    system = SYSTEM.format(
        shapes="\n".join(f"  {s}" for s in step_vocabulary()),
        legacy="\n".join(f"  {s}" for s in STEP_SHAPES),
        locators=_locators_block(platform, known),
        testdata=_testdata_block(),
        existing=_existing_cases_block(platform, prompt, brief_text),
        stepgroups=group_lines,
    )

    extend_block = ""
    if extend_flow:
        extend_block = _extend_block(extend_flow)

    if extend_flow:
        task = (f"The tester is EXTENDING the saved test case `{extend_flow}` (shown in "
                f"full below). Produce ONLY the additional testcases whose steps will be "
                f"appended AFTER its last step — same browser session, same page state, "
                f"same ${{variables}} and locator names. Never repeat its existing steps, "
                f"never re-open a URL it already opened unless the addition needs a "
                f"different page. Hard ceiling {max_testcases}.")
    elif previous and previous.get("testcases"):
        # Incremental: the tester has seen the earlier cases. Only what the
        # new information adds or changes is asked for; the rest is kept by
        # id. This is what keeps a re-draft inside the output budget, and it
        # keeps the list stable for the person reviewing it.
        task = (f"Produce ONLY the testcases that the new information (the tester's "
                f"answers / added details) ADDS or CHANGES, in `testcases`. List every "
                f"previous testcase that stays correct in `keep` by id — do not repeat "
                f"them. A changed testcase keeps its id. Hard ceiling {max_testcases}.")
    else:
        task = (f"Produce as many testcases as the specification needs to be fully "
                f"covered — every criterion, every negative, every linked defect — "
                f"and no padding (hard ceiling {max_testcases}).")
    completion = get_provider(provider, model).complete_structured(
        system=system,
        user=(f"Platform under test: {platform}\n{task}\n\n"
              f"The tester's request:\n\n{prompt}"
              + (f"\n\n===== JIRA (fetched, authoritative) =====\n{brief_text}"
                 if brief_text else "")
              + (("\n\n===== MANUAL TEST CASES / SHEETS SUPPLIED BY THE TESTER =====\n"
                  "Every row that is a testcase must be covered by a drafted testcase "
                  "(reference its id/title in `covers`); add the automation-only checks "
                  "(API, tracker, ordering) the sheet does not have.\n\n"
                  + "\n\n".join(sheet_texts)) if sheet_texts else "")
              + (f"\n\n{_previous_block(previous)}" if previous and previous.get("testcases") else "")
              + (f"\n\n{extend_block}" if extend_block else "")),
        schema=Draft,
    )

    d = completion.data
    if previous and previous.get("testcases"):
        d = merge_incremental(previous, d)
    for i, tc in enumerate(d.get("testcases") or [], 1):
        tc["testcase_id"] = tc.get("testcase_id") or f"TC_PROMPT_{i:02d}"
        tc["steps"] = [str(x) for x in (tc.get("steps") or []) if str(x).strip()]
        tc["preconditions"] = [str(x) for x in (tc.get("preconditions") or [])]
        tc["covers"] = [str(x) for x in (tc.get("covers") or [])]
    d["values_found"] = {k: str(v) for k, v in (d.get("values_found") or {}).items()}
    d["questions"] = [q for q in (d.get("questions") or []) if (q or {}).get("question")]
    d["sheets"] = [t.splitlines()[0] for t in sheet_texts]      # the headings, for the UI
    d["sheet_problems"] = sheet_problems
    if sheet_problems:
        d.setdefault("unclear", []).extend(sheet_problems)
    # Values are recovered from the prompt in code rather than relied upon from
    # the model. Asking for them was unreliable — the instruction not to write
    # secrets into steps was read as a reason to withhold them everywhere, so the
    # values the operator had already supplied came back empty and they would
    # have had to type them again. Extraction is deterministic and cannot regress.
    # Values are harvested from the prompt AND the ticket's comments (that is
    # where test URLs get posted), but never from the ticket's own link.
    corpus = prompt + "\n" + "\n".join(
        c[2] for b in briefs for c in b.comments) if briefs else prompt
    corpus += "\n" + "\n".join(sheet_texts)        # URLs in the QA sheet count too
    d["values_found"] = harvest_values(corpus, d)
    # Every page URL seen anywhere (prompt, comments, sheets), so the values
    # dialog can offer them by name — "cng_service_url" matched to the URL
    # whose path says CNG — instead of leaving the box empty.
    d["candidate_urls"] = sorted({
        u for u in _URL.findall(corpus)
        if "/browse/" not in u and "docs.google.com" not in u.lower()
        and not re.search(r"api|\.php\?|/rest/", u, re.I)})[:60]
    d["jira"] = jira_meta
    # Naming is normalised after harvesting so the values and the steps that
    # reference them are renamed together and cannot drift apart.
    canonicalise_variables(d)
    d["group_suggestions"] = _suggest_groups(d, platform)
    d["provider"] = completion.provider
    d["model"] = completion.model
    d["usage"] = completion.usage
    return d


class PromptSource:
    """
    Adapter presenting drafted testcases as tabs, exactly like the xlsx reader.

    Implementing the same read_tabs() contract is what lets a prompt reach the
    generator, the mapper and the emitter without any of them knowing a model was
    involved.
    """

    kind = "prompt"

    def __init__(self, draft: dict, prompt: str = "") -> None:
        self.draft = draft
        self.prompt = prompt

    def describe(self) -> dict:
        return {
            "kind": "prompt",
            "title": (self.prompt.strip().splitlines() or ["(prompt)"])[0][:80],
            "location": "operator prompt",
            "access": f"drafted by {self.draft.get('provider', '?')}/"
                      f"{self.draft.get('model', '?')}",
            "modified_utc": "n/a",
        }

    #: EXACTLY the workbook's own header row. ai_flow_builder/bundle.py matches
    #: columns against these names, so inventing friendlier ones ("Title",
    #: "Steps") silently produces zero testcases — the tab parses, the header is
    #: found, and every row is discarded for want of a recognised step column.
    HEADER = ["Test Case ID", "Module / Suite", "Test Case Name", "Priority",
              "Test Type", "Prerequisite", "Step No", "Test Step (Testsigma NLP)",
              "Test Data", "Expected Result"]

    def read_tabs(self) -> dict[str, list[list]]:
        rows = [list(self.HEADER)]
        for tc in self.draft.get("testcases", []):
            steps = tc.get("steps") or []
            for i, step in enumerate(steps, 1):
                first = i == 1        # continuation rows repeat nothing but the step
                rows.append([
                    tc.get("testcase_id", "") if first else "",
                    "Prompt" if first else "",
                    tc.get("title", "") if first else "",
                    tc.get("priority", "") if first else "",
                    tc.get("classification", "") if first else "",
                    "; ".join(tc.get("preconditions") or []) if first else "",
                    i, step, "",
                    tc.get("expected", "") if first else "",
                ])

        var_rows = [["Variable", "Description", "Sample / Where to source", "Owner"]]
        for name, value in (self.draft.get("values_found") or {}).items():
            var_rows.append([name, "supplied in the request", value, "operator"])
        for need in self.draft.get("inputs_needed") or []:
            var_rows.append([need["name"], need.get("why", ""),
                             f"to be supplied ({need.get('kind','text')})", "operator"])
        return {"Test Cases": rows, "Test Data & Variables": var_rows}

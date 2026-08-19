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

SYSTEM = """You turn a tester's written request into structured testcases for an \
existing codeless automation framework.

Write each step using ONE of these exact shapes wherever the step fits one. These \
are the shapes the framework maps deterministically — a step outside them still \
gets handled, but costs an extra model call and is more likely to need human \
correction:

{shapes}

Element names are PascalCase nouns naming what is on screen (AskMorePhotosCTA, \
MobileNumberInput). These locator names already exist and should be reused \
verbatim when the step refers to one of them:

{locators}

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
"""


def _models():
    from pydantic import BaseModel, Field

    class Testcase(BaseModel):
        testcase_id: str = Field(description="Short stable id, e.g. TC_PROMPT_01")
        title: str = Field(description="One line describing what is verified")
        preconditions: list[str] = Field(default_factory=list)
        steps: list[str] = Field(description="Ordered steps, one action each")
        expected: str = Field(description="What proves this testcase passed")
        priority: str = Field(default="Medium", description="Critical|High|Medium|Low")
        classification: str = Field(default="Positive", description="Positive|Negative")

    class InputNeeded(BaseModel):
        name: str = Field(description="Variable name used in the steps, no ${} wrapper")
        kind: str = Field(description="url|mobile|otp|email|text|number")
        why: str = Field(description="What this value is for")

    class Draft(BaseModel):
        testcases: list[Testcase]
        inputs_needed: list[InputNeeded] = Field(
            default_factory=list,
            description="Values the request did not supply and must be collected",
        )
        values_found: dict[str, str] = Field(
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

    urls = _URL.findall(prompt)
    emails = _EMAIL.findall(prompt)

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
    pools = {"url": list(urls), "email": list(emails),
             "mobile": list(mobiles), "otp": list(otps),
             "search": searches}

    def kind_of(var: str) -> str:
        low = var.lower()
        if any(k in low for k in ("url", "link", "page", "endpoint")):
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


def draft_testcases(prompt: str, platform: str = "website",
                    max_testcases: int = 10, provider: str | None = None,
                    model: str | None = None) -> dict:
    """Pass 1 — a written request becomes reviewable testcases."""
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

    system = SYSTEM.format(
        shapes="\n".join(f"  {s}" for s in STEP_SHAPES),
        locators=("\n".join(f"  {n}" for n in known[:200]) if known
                  else "  (none on file yet — name elements descriptively)"),
        stepgroups=group_lines,
    )

    completion = get_provider(provider, model).complete_structured(
        system=system,
        user=(f"Platform under test: {platform}\n"
              f"Produce at most {max_testcases} testcases.\n\n"
              f"The tester's request:\n\n{prompt}"),
        schema=Draft,
    )

    d = completion.data
    # Values are recovered from the prompt in code rather than relied upon from
    # the model. Asking for them was unreliable — the instruction not to write
    # secrets into steps was read as a reason to withhold them everywhere, so the
    # values the operator had already supplied came back empty and they would
    # have had to type them again. Extraction is deterministic and cannot regress.
    d["values_found"] = harvest_values(prompt, d)
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

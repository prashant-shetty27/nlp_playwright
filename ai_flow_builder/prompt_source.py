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
_URL = re.compile(r"https?://[^\s<>\"')\]]*[^\s<>\"')\].,;:]")
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

    pools = {"url": list(urls), "email": list(emails),
             "mobile": list(mobiles), "otp": list(otps)}

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
        return ""

    found: dict[str, str] = {}
    for var in ordered:
        kind = kind_of(var)
        if kind and pools.get(kind):
            found[var] = pools[kind].pop(0)
    return found


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
    system = SYSTEM.format(
        shapes="\n".join(f"  {s}" for s in STEP_SHAPES),
        locators=("\n".join(f"  {n}" for n in known[:200]) if known
                  else "  (none on file yet — name elements descriptively)"),
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

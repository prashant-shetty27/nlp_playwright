"""
nlp/variables.py — the one definition of what a variable reference looks like.

Why this module exists
----------------------
Five modules each carried their own regex for "${something}", and they did not
agree:

    nlp/variable_manager.resolve_variables   \\$\\{([^}]+)\\}            (the runtime)
    VariableManager.save                     strips [^a-zA-Z0-9_]    (on write only!)
    ai_flow_builder/bundle                   [A-Za-z0-9_.\\-]+
    ai_flow_builder/prompt_source            [A-Za-z_][A-Za-z0-9_]*
    tools/flow_lint                          [A-Za-z_][A-Za-z0-9_]*

Two consequences, both silent:

  * The linter could not SEE ${product.id} or ${user-name}, which the runtime
    resolves happily. Its entire job is reporting variables used before they are
    defined, and it was blind to every name containing a dot or a hyphen — such
    a flow passed the gate and then failed mid-run.

  * VariableManager.save() stripped punctuation from the name it stored but
    resolve_parameters() looked names up verbatim. `store … as user-name`
    therefore saved "username", and the very next `${user-name}` raised
    "not found in runtime memory". The name was silently rewritten on the way
    in and never rewritten on the way out.

So the grammar is declared once, here, and the two rules are:

  REFERENCE_RE  what a reference may look like — deliberately permissive,
                because it must match what the runtime actually resolves. The
                linter uses it so that anything executable is also checkable.

  normalise()   applied on BOTH sides of storage, so a name cannot mean one
                thing when written and another when read. Punctuation becomes
                an underscore rather than vanishing, matching how locator names
                are normalised (search-box -> search_box).
"""
from __future__ import annotations

import re

#: A reference as the runtime resolves it: anything up to the closing brace.
#: nlp/variable_manager.resolve_variables uses exactly this shape, so the
#: linter matching it is what makes "checked" and "runnable" the same set.
REFERENCE_RE = re.compile(r"\$\{([^}]+)\}")

#: A name that needs no normalisation: a plain identifier.
SAFE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def referenced_names(text: str) -> list[str]:
    """Every variable referenced in `text`, in order of first appearance."""
    seen: list[str] = []
    for name in REFERENCE_RE.findall(text or ""):
        if name not in seen:
            seen.append(name)
    return seen


def is_safe_name(name: str) -> bool:
    """Whether `name` is already a plain identifier needing no rewrite."""
    return bool(SAFE_NAME_RE.match(name or ""))


def normalise(raw: str) -> str:
    """
    The storage form of a variable name.

    Must be applied on both save and lookup — that symmetry is the whole point.
    Punctuation becomes "_" instead of being deleted, so "user-name" and
    "user.name" both become "user_name" rather than collapsing to "username",
    which is unreadable and collides with a genuinely different name.

    An empty or unusable name returns "" so callers can reject it explicitly
    rather than storing something under a blank key.
    """
    name = (raw or "").strip()
    if not name:
        return ""
    name = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_")
    if not name:
        return ""
    if name[0].isdigit():
        name = f"var_{name}"
    return name


def declared_params(header_lines: list[str]) -> set[str]:
    """
    Names listed on a leading "# Params :" comment.

    Whitespace-separated, so a name containing a space cannot be declared — the
    runtime would accept such a name, but no header could express it. That is a
    limitation of the header format, not a silent drop: such a name simply is
    not declarable and the linter will report it as undefined.
    """
    out: set[str] = set()
    pattern = re.compile(r"^#\s*Params\s*:\s*(.*)$", re.I)
    for raw in header_lines:
        line = (raw or "").strip()
        if not line:
            continue
        if not line.startswith("#"):
            break                       # the header ends at the first statement
        mo = pattern.match(line)
        if not mo:
            continue
        for token in mo.group(1).replace(",", " ").split():
            name = token.strip("${}")
            if name and name.lower() != "none":
                out.add(name)
    return out

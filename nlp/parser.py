"""
nlp/parser.py
Single unified NLP/DSL step parser (merged from root command_parser.py and dsl/parser.py).
Supports all NLP step syntax used in .flow files.
"""
import re
from nlp.command import Command


def _swipe_span(words: str) -> str:
    w = " ".join(words.lower().split())
    # "swipe left" is the gesture that moves a carousel to the NEXT slide
    # (finger travels right→left), as on a phone.
    return {"up": "bottom_top", "down": "top_bottom",
            "left": "right_left", "right": "left_right"}.get(w, w.replace(" to ", "_"))


def parse_step(step: str) -> Command:
    from execution.step_flags import strip as _strip_flags
    s = _strip_flags(step).strip()       # "[ignore 5s] click X" parses as "click X"

    # Block lines — if / else if / else / end if, for each row, repeat …,
    # stop loop, skip to next row. They are decided by execution/control_flow,
    # never dispatched as actions; parsing them here lets the editor, the
    # linter and the step check accept them like any other step.
    from execution.control_flow import classify as _classify_block
    _blk = _classify_block(s)
    if _blk:
        return Command(type="block", text=_blk[0],
                       values=[a for a in _blk[1] if a])

    # Calendar / date steps — select date "next working day" in checkin_date,
    # store date next weekend as d, verify date in checkin is a weekend …
    from nlp.date_rules import match as _match_date
    _dt = _match_date(s)
    if _dt is not None:
        return _dt

    # =============================
    # EVERYDAY WEB ACTIONS & CHECKS (hover, right click, drag, checkboxes, URL,
    # attributes, counts, key combinations). Early, because several of these
    # were being swallowed by broader rules below — "verify url contains …"
    # was read as a check on a variable called "url".
    # =============================
    _E = r'(?:the\s+)?(?:element\s+)?'
    m = re.match(r'^(?:hover|mouse\s*over|move\s+(?:the\s+)?mouse\s+(?:to|over|on))\s+(?:over\s+|on\s+)?'
                 + _E + r'(\S+)$', s, re.I)
    if m:
        return Command(type="hover", target=m.group(1))
    m = re.match(r'^(?:right[\s-]*click|context\s+click)\s+(?:on\s+)?' + _E + r'(\S+)$', s, re.I)
    if m:
        return Command(type="right_click", target=m.group(1))
    m = re.match(r'^drag\s+(?:and\s+drop\s+)?' + _E + r'(\S+)\s+(?:to|onto|on|into|over)\s+'
                 + _E + r'(\S+)$', s, re.I)
    if m:
        return Command(type="drag_drop", target=m.group(1), values=[m.group(2)])
    m = re.match(r'^(check|tick|uncheck|untick)\s+(?:the\s+)?(?:checkbox\s+|radio\s+(?:button\s+)?)?'
                 r'(?:element\s+)?([A-Za-z_][\w.\-]*)$', s, re.I)
    if m:
        on = m.group(1).lower() in ("check", "tick")
        return Command(type="check" if on else "uncheck", target=m.group(2))
    m = re.match(r'^verify\s+(?:that\s+)?' + _E + r'(\S+)\s+is\s+(not\s+)?'
                 r'(checked|ticked|selected|enabled|disabled|editable|read[\s-]?only)$', s, re.I)
    if m:
        return Command(type="verify_state", target=m.group(1),
                       values=[m.group(3).lower().replace("-", " "), "not" if m.group(2) else ""])

    _TOP = (r'(does\s+not\s+contain|doesn\'t\s+contain|contains|is\s+not|is|equals|'
            r'starts\s+with|ends\s+with|matches)')
    m = re.match(r'^verify\s+(?:that\s+)?(?:the\s+)?(?:current\s+|page\s+|browser\s+)?(?:url|address)\s+'
                 + _TOP + r'\s+"(.*)"$', s, re.I)
    if m:
        return Command(type="verify_url", values=[re.sub(r"\s+", " ", m.group(1).lower())], text=m.group(2))
    m = re.match(r'^wait\s+(?:for\s+|until\s+)?(?:the\s+)?(?:current\s+|page\s+)?(?:url|address)\s+'
                 r'(?:to\s+)?(does\s+not\s+contain|doesn\'t\s+contain|not\s+contain|contains?|containing|is|to\s+be|be|starts\s+with|ends\s+with)\s+"(.*?)"'
                 r'(?:\s+(?:within|for)\s+(\d+)\s*(?:s|sec|secs|seconds?))?$', s, re.I)
    if m:
        op = m.group(1).lower()
        op = ("does not contain" if "not" in op or "n't" in op else
              "contains" if op.startswith("contain") else ("is" if op in ("is", "to be", "be") else op))
        return Command(type="wait_for_url", values=[op], text=m.group(2),
                       wait=float(m.group(3)) if m.group(3) else None)
    m = re.match(r'^store\s+(?:the\s+)?(?:current\s+|page\s+)?(?:url|address)\s+as\s+(\S+)$', s, re.I)
    if m:
        return Command(type="extract_url", variable_name=m.group(1))

    _AOP = (r'(does\s+not\s+contain|doesn\'t\s+contain|contains|is\s+not\s+empty|is\s+empty|is\s+not|is|'
            r'equals|starts\s+with|ends\s+with|matches)')
    m = re.match(r'^verify\s+(?:the\s+)?(?:attribute\s+(\S+)|(value|placeholder|title\s+attribute|href|src|alt))'
                 r'\s+of\s+' + _E + r'(\S+)\s+' + _AOP + r'(?:\s+"(.*)")?$', s, re.I)
    if m:
        attr = (m.group(1) or m.group(2)).lower().replace(" attribute", "")
        op = re.sub(r"\s+", " ", m.group(4).lower())
        if m.group(5) is None and op not in ("is empty", "is not empty"):
            raise ValueError('Give the expected value in quotes, e.g. verify value of name_field is "Ravi"')
        return Command(type="verify_attribute", target=m.group(3), attribute=attr,
                       values=[op], text=m.group(5) or "")
    m = re.match(r'^verify\s+' + _E + r'(\S+)\s+has\s+(?:the\s+)?(value|placeholder|attribute\s+(\S+))\s+"(.*)"$', s, re.I)
    if m:
        attr = (m.group(3) or m.group(2)).lower()
        return Command(type="verify_attribute", target=m.group(1), attribute=attr, values=["is"], text=m.group(4))

    _COP = r'(?:(is\s+not|is|more\s+than|greater\s+than|less\s+than|fewer\s+than|at\s+least|at\s+most)\s+)?'
    m = (re.match(r'^verify\s+(?:the\s+)?(?:count|number)\s+of\s+' + _E + r'(\S+)\s+(?:is\s+)?' + _COP + r'(\d+)$', s, re.I)
         or re.match(r'^verify\s+' + _E + r'(\S+)\s+count\s+(?:is\s+)?' + _COP + r'(\d+)$', s, re.I))
    if m:
        op = re.sub(r"\s+", " ", (m.group(2) or "is").lower())
        if m.group(1).lower() in ("tab", "tabs", "window", "windows", "open_tabs"):
            if op != "is":
                raise ValueError("Tab count takes an exact number, e.g. verify 2 tabs are open")
            return Command(type="verify_tab_count", count=int(m.group(3)))
        return Command(type="verify_count", target=m.group(1), values=[op], count=int(m.group(3)))

    # press key Control+A | press ctrl+a | press keys cmd+shift+k | press key F5
    m = re.match(r'^press\s+(?:the\s+)?(?:keys?\s+)?((?:ctrl|control|cmd|command|meta|alt|option|shift|win|windows)'
                 r'(?:\s*\+\s*\S+)+)$', s, re.I) or re.match(r'^press\s+keys?\s+(\S+(?:\s*\+\s*\S+)+|F\d{1,2})$', s, re.I)
    if m:
        return Command(type="press_key", text=m.group(1).replace(" ", ""))

    # =============================
    # TEXT MATCHING — page level and element level
    #
    # "contains" and "exact" existed; the rest did not, on either this framework
    # or Testsigma (its 292 web templates have no starts-with, ends-with or
    # regex at all). They matter for the things people actually assert: an order
    # id whose prefix is fixed and whose suffix is not, a price whose currency
    # symbol is the stable part, a message whose wording varies.
    #
    # These rules come BEFORE the "verify stored {variable} contains" rule, which
    # was matching "verify page contains …" and treating the word `page` as a
    # variable name — the step parsed, then failed at run time looking for a
    # variable nobody had defined.
    # =============================
    _PAGE_MATCH = {
        "contains": "verify_page_contains",
        "has": "verify_page_contains",
        "does not contain": "verify_page_not_contains",
        "doesn't contain": "verify_page_not_contains",
        "starts with": "verify_page_starts",
        "begins with": "verify_page_starts",
        "ends with": "verify_page_ends",
        "matches": "verify_page_matches",
        "matches regex": "verify_page_matches",
    }
    _pm = re.match(
        r'^verify\s+(?:the\s+)?page\s+(does\s+not\s+contain|doesn\'t\s+contain|'
        r'matches\s+regex|starts\s+with|begins\s+with|ends\s+with|contains|has|matches)'
        r'\s+"([^"]*)"$', s, re.I)
    if _pm:
        op = re.sub(r"\s+", " ", _pm.group(1).strip().lower())
        return Command(type=_PAGE_MATCH[op], text=_pm.group(2))

    _pt = re.match(r'^verify\s+page\s+title\s+(contains|is|equals)\s+"([^"]*)"$',
                   s, re.I)
    if _pt:
        return Command(type=("verify_title_exact"
                             if _pt.group(1).lower() in ("is", "equals")
                             else "verify_title_contains"),
                       text=_pt.group(2))

    _ELEM_MATCH = {
        "starts with": "verify_element_starts",
        "begins with": "verify_element_starts",
        "ends with": "verify_element_ends",
        "matches": "verify_element_matches",
        "matches regex": "verify_element_matches",
        "does not contain": "verify_element_not_contains",
        "doesn't contain": "verify_element_not_contains",
    }
    _em = re.match(
        r'^verify\s+element\s+([A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]*\])?)\s+'
        r'(does\s+not\s+contain|doesn\'t\s+contain|matches\s+regex|starts\s+with|'
        r'begins\s+with|ends\s+with|matches)\s+"([^"]*)"$', s, re.I)
    if _em:
        op = re.sub(r"\s+", " ", _em.group(2).strip().lower())
        return Command(type=_ELEM_MATCH[op], target=_em.group(1), text=_em.group(3))

    # =============================
    # BROWSER ALERTS  (Testsigma parity — the commonest cause of a stalled run)
    #   accept alert / dismiss alert
    #   verify alert is present
    #   verify alert text "…"
    #   type "…" into alert
    # A browser alert is drawn by the BROWSER, so no locator reaches it and a run
    # simply hangs. These are the four things anyone actually needs to do with one.
    # =============================
    if re.fullmatch(r"(accept|ok)\s+alert", s, re.I):
        return Command(type="accept_alert")
    if re.fullmatch(r"(dismiss|cancel)\s+alert", s, re.I):
        return Command(type="dismiss_alert")
    if re.fullmatch(r"verify\s+alert\s+(is\s+)?(present|displayed|visible)", s, re.I):
        return Command(type="verify_alert_present")
    _al = re.match(r'^verify\s+alert\s+text\s+"([^"]*)"$', s, re.I)
    if _al:
        return Command(type="verify_alert_text", text=_al.group(1))
    # type "<text>" into focused field — keys go to whatever has focus (Testsigma
    # "Enter data … on focused element"); adds to what the field already holds.
    _foc = re.match(r'^type\s+"(.*)"\s+into\s+(?:the\s+)?focused\s+(?:field|element)$', s, re.I)
    if _foc:
        return Command(type="type_focused", text=_foc.group(1))
    _alt = re.match(r'^type\s+"([^"]*)"\s+into\s+alert$', s, re.I)
    if _alt:
        return Command(type="type_into_alert", text=_alt.group(1))

    # =============================
    # COOKIES
    #   delete cookie "name" / delete all cookies / verify cookie "name" exists
    # =============================
    if re.fullmatch(r"delete\s+all\s+cookies", s, re.I):
        return Command(type="delete_all_cookies")
    _ck = re.match(r'^delete\s+cookie\s+"([^"]+)"$', s, re.I)
    if _ck:
        return Command(type="delete_cookie", text=_ck.group(1))
    _ckv = re.match(r'^verify\s+cookie\s+"([^"]+)"\s+exists$', s, re.I)
    if _ckv:
        return Command(type="verify_cookie", text=_ckv.group(1))

    # =============================
    # FILE UPLOAD / WINDOW BY TITLE / PARENT FRAME
    # =============================
    _up = re.match(r'^upload\s+file\s+"([^"]+)"\s+(?:to|using)\s+([a-z_][a-z0-9_]*)$',
                   s, re.I)
    if _up:
        return Command(type="upload_file", text=_up.group(1), target=_up.group(2))
    _win = re.match(r'^switch\s+to\s+window\s+(?:with\s+)?title\s+"([^"]+)"$', s, re.I)
    if _win:
        return Command(type="switch_window_title", text=_win.group(1))
    if re.fullmatch(r"switch\s+to\s+parent\s+frame", s, re.I):
        return Command(type="parent_frame")

    # =============================
    # BROWSER PERMISSION, PER PERMISSION
    #   allow browser permission geolocation
    #   deny browser permission notifications
    #   allow browser permission camera once
    # Run Center sets a policy for the whole run; this is for the case where one
    # test needs geolocation granted and notifications refused. "once" grants for
    # the current page only, which is what a real prompt's "Allow this time" does.
    # =============================
    _perm = re.match(
        r"^(allow|deny|grant|block)\s+browser\s+permission\s+([a-z][a-z0-9_-]*)"
        r"(\s+once|\s+this\s+time)?$", s, re.I)
    if _perm:
        decision = _perm.group(1).lower()
        return Command(
            type="browser_permission",
            target=_perm.group(2).strip().lower(),
            text=("once" if _perm.group(3) else
                  ("allow" if decision in ("allow", "grant") else "deny")),
        )

    # =============================
    # NAVIGATE / GO TO URL
    # Accepted aliases (all produce type="open"):
    #   go to url <url>        — recorder default
    #   navigate to <url>      — NLP variant
    #   browse to <url>        — mobile/hybrid variant
    #   visit <url>            — short form
    #   open url <url>         — explicit "open url" form
    # =============================
    m = re.match(
        r'^(?:go\s+to\s+url|navigate\s+to|browse\s+to|visit|open\s+url)\s+(\S+)$',
        s, re.I
    )
    if m:
        return Command(type="open", target=m.group(1).strip())

    # =============================
    # OPEN A TAB — must be decided BEFORE the catch-all `open <target>` below,
    # which is greedy enough to swallow anything following the word "open".
    # `open new tab` was being read as a navigation to a site literally named
    # "new tab", so the open_new_tab handler in both runners was unreachable.
    # =============================
    if re.fullmatch(r"open\s+(?:a\s+)?new\s+(?:tab|window)", s, re.I):
        return Command(type="open_new_tab")

    # Open a URL IN a new tab, in one step. Without this it takes two — a blank
    # tab, then a navigation — and the pair only works if you remember that the
    # blank tab has already stolen the focus.
    #   open <url> in a new tab | open <url> in new window
    #   go to url <url> in a new tab | navigate to <url> in a new tab
    m = re.match(r'^(?:open(?:\s+url)?|go\s+to\s+url|navigate\s+to|browse\s+to|visit)'
                 r'\s+(\S+)\s+in\s+(?:a\s+)?new\s+(?:tab|window)$', s, re.I)
    if m:
        return Command(type="open_in_new_tab", target=m.group(1).strip())

    # =============================
    # OPEN  (site alias or bare URL, e.g. "open justdial" or "open https://…")
    # =============================
    if s.lower().startswith("open "):
        return Command(type="open", target=s[5:].strip())

    # =============================
    # SEARCH
    # =============================
    if s.lower().startswith("search for "):
        return Command(type="search", text=s[11:].strip())

    # =============================
    # WAIT (Specific)
    # =============================
    if s.lower() in ["wait for result page load", "results load", "wait for results"]:
        return Command(type="wait_for_result_page_load")

    # =============================
    # WAIT (Seconds)
    # =============================
    m = re.fullmatch(r"wait\s+(\d+(\.\d+)?)\s*seconds?", s, re.I)
    if m:
        return Command(type="wait", wait=float(m.group(1)))

    # =============================
    # SCROLL UNTIL TEXT VISIBLE
    # =============================
    if s.lower().startswith("scroll until text"):
        text = re.search(r'"(.*?)"', s)
        if not text:
            raise ValueError("Scroll requires quoted text")

        count = re.search(r"scroll count\s*(\d+)", s, re.I)
        wait = re.search(r"scroll wait\s*(\d+(\.\d+)?)", s, re.I)

        return Command(
            type="scroll_until_text_visible",
            text=text.group(1),
            count=int(count.group(1)) if count else 10,
            wait=float(wait.group(1)) if wait else 1,
        )

    # =============================
    # SAVE PAGE SOURCE  (debugging aid: dumps the live DOM to data/logs/)
    # =============================
    _sps = re.match(r'^save\s+page\s+(?:source|html)\s+as\s+"?([^"]+)"?$', s, re.I)
    if _sps:
        return Command(type="save_page_source", text=_sps.group(1).strip())

    # =============================
    # SCROLL UNTIL ELEMENT VISIBLE
    # scroll until element <locator> visible, scroll by <px> pixels [vertically|horizontally|up|left],
    #                                          scroll count <n>, scroll wait <sec>
    # Scrolls the page by a fixed number of pixels (chosen by the author) until the
    # named element is inside the viewport. Text-based variant is above; this one
    # is for elements that have no unique text (a button repeated per listing).
    # =============================
    _sue = re.match(r'^scroll\s+until\s+element\s+(\S+)\s+(?:is\s+)?visible\b(.*)$', s, re.I)
    if _sue:
        rest = _sue.group(2) or ""
        px = re.search(r"scroll\s+by\s+(\d+)", rest, re.I)
        count = re.search(r"scroll\s+count\s*(\d+)", rest, re.I)
        wait = re.search(r"scroll\s+wait\s*(\d+(?:\.\d+)?)", rest, re.I)
        direction = "down"
        if re.search(r"\bhorizontal(?:ly)?\b|\bright\b", rest, re.I):
            direction = "right"
        if re.search(r"\bleft\b", rest, re.I):
            direction = "left"
        if re.search(r"\bup\b", rest, re.I):
            direction = "up"
        return Command(
            type="scroll_until_element_visible",
            target=_sue.group(1).strip(),
            # A LIST, not a string: the runner reads values[0], and on "500"
            # that is the character "5" — the step scrolled 5px per attempt.
            values=[str(int(px.group(1)) if px else 500)],   # pixels per scroll
            text=direction,
            count=int(count.group(1)) if count else 10,
            wait=float(wait.group(1)) if wait else 1,
        )

    # =============================
    # VERIFY IMAGE (from dsl/parser.py)
    # =============================
    if s.lower().startswith("verify image"):
        match = re.search(r'"(.*?)"', s)
        if not match:
            raise ValueError(f"Invalid verify image syntax: {s}")
        image_path = match.group(1)

        thresh_match = re.search(r"threshold\s+(\d+)%", s, re.IGNORECASE)
        threshold = float(thresh_match.group(1)) / 100.0 if thresh_match else 0.5

        return Command(type="verify_image", image_path=image_path, threshold=threshold)

    # =============================
    # VERIFY EXACT TEXT (full element text must match completely)
    # verify exact text "Sort by"
    # =============================
    if re.match(r'^verify\s+exact\s+text\s+', s, re.I):
        text = re.search(r'"(.*?)"', s)
        if not text:
            raise ValueError("Verify exact text requires quoted text")
        return Command(type="verify_exact_text", text=text.group(1))

    # =============================
    # VERIFY TEXT  (singular — partial/contains match, must NOT match "verify texts ...")
    # =============================
    if re.match(r'^verify\s+text\s+', s, re.I):
        text = re.search(r'"(.*?)"', s)
        if not text:
            raise ValueError("Verify requires quoted text")

        # verify text "X" in element_name  →  element-level contains check
        # "in element foo" and "in page" were both mis-read: the first fell
        # through to a page-level check (silently ignoring the element), the
        # second looked for an element called "page".
        in_el = re.search(r'"\s+in\s+(?:element\s+)?(\w+)\s*$', s, re.I)
        if in_el and in_el.group(1).lower() in ("page", "screen"):
            in_el = None
        if in_el:
            return Command(
                type="verify_element_contains",
                target=in_el.group(1).strip(),
                text=text.group(1),
            )

        scroll = re.search(r",\s*(\d+)\s*$", s)

        return Command(
            type="verify_text",
            text=text.group(1),
            count=int(scroll.group(1)) if scroll else None,
        )

    # =============================
    # REFRESH PAGE
    # =============================
    if s.lower() in ["refresh", "refresh page", "reload", "reload page"]:
        return Command(type="refresh")

    # ================================================================
    # IF VISIBLE — conditional actions (skip silently if not present)
    # Syntax: <action> if visible <target> [wait <N> seconds]
    # ================================================================
    _IF_VIS = r'\s+if\s+(?:visible|present)\s+'
    _WAIT_SFX = r'(?:\s+wait\s+(\d+(?:\.\d+)?)\s+seconds?)?$'

    # tap/click if visible <target> [wait N seconds]
    m = re.match(r'^(?:click|tap)' + _IF_VIS + r'(?:on\s+)?(\S+)' + _WAIT_SFX, s, re.I)
    if m:
        return Command(type="tap_if_visible", target=m.group(1).strip(),
                       wait=float(m.group(2)) if m.group(2) else None)

    # type/fill if visible "text" into <target> [wait N seconds]
    m = re.match(r'^(?:type|fill)' + _IF_VIS + r'"(.*?)"\s+(?:into|in)\s+(\S+)' + _WAIT_SFX, s, re.I)
    if m:
        return Command(type="fill_if_visible", text=m.group(1), target=m.group(2).strip(),
                       wait=float(m.group(3)) if m.group(3) else None)

    # verify if visible <target> [wait N seconds]
    m = re.match(r'^verify' + _IF_VIS + r'(?:element\s+)?(?:exists\s+)?(\S+)' + _WAIT_SFX, s, re.I)
    if m:
        return Command(type="verify_if_visible", target=m.group(1).strip(),
                       wait=float(m.group(2)) if m.group(2) else None)

    # double tap if visible <target> [wait N seconds]
    m = re.match(r'^double\s+(?:tap|click)' + _IF_VIS + r'(\S+)' + _WAIT_SFX, s, re.I)
    if m:
        return Command(type="double_tap_if_visible", target=m.group(1).strip(),
                       wait=float(m.group(2)) if m.group(2) else None)

    # long press if visible <target> [wait N seconds]
    m = re.match(r'^long\s+press' + _IF_VIS + r'(\S+)' + _WAIT_SFX, s, re.I)
    if m:
        return Command(type="long_press_if_visible", target=m.group(1).strip(),
                       wait=float(m.group(2)) if m.group(2) else None)

    # store text if visible from <target> as <var> [wait N seconds]
    m = re.match(r'^store\s+text' + _IF_VIS + r'(?:from\s+)?(\S+)\s+as\s+(\S+)' + _WAIT_SFX, s, re.I)
    if m:
        return Command(type="store_text_if_visible", target=m.group(1).strip(),
                       variable_name=m.group(2).strip(),
                       wait=float(m.group(3)) if m.group(3) else None)

    # =============================
    # OPTIONAL CLICK/TAP COMMAND
    # click if exists <target>
    # tap if exists <target>
    # =============================
    m = re.match(r'^(?:click|tap)\s+if\s+exists\s+(?:on\s+)?(.*)$', s, re.I)
    if m:
        target = (m.group(1) or "").strip()
        return Command(type="click_if_exists", target=target)

    # =============================
    # OPTIONAL TYPE/FILL COMMAND
    # type if exists "text" into <target>
    # fill if exists "text" in <target>
    # =============================
    m = re.match(r'^(?:type|fill)\s+if\s+exists\s+"(.*?)"\s+(?:into|in)\s+(.*)$', s, re.I)
    if m:
        text_to_type, target = m.groups()
        return Command(type="fill_if_exists", text=text_to_type, target=target.strip())

    # =============================
    # LAYOUT CHECKS — where an element sits on / inside another one.
    # Written for design changes ("the 360° icon must sit at the top-right of
    # every photo"): plain words in the step, the geometry lives in the runner.
    #   verify element <icon> is inside <image>
    #   verify element <icon> is inside every <image>
    #   verify element <icon> is at the same place in every <image>
    #   verify element <icon> is the same size as <other>
    #   store position of <icon> in <image> as <var>
    # =============================
    m = re.match(r'^verify\s+element\s+(\S+)\s+is\s+inside\s+every\s+(?:element\s+)?(\S+)$', s, re.I)
    if m:
        return Command(type="verify_inside_every", target=m.group(1), values=[m.group(2)])
    m = re.match(r'^verify\s+element\s+(\S+)\s+is\s+inside\s+(?:element\s+)?(\S+)$', s, re.I)
    if m:
        return Command(type="verify_inside", target=m.group(1), values=[m.group(2)])
    m = re.match(r'^verify\s+element\s+(\S+)\s+is\s+(?:at|in)\s+the\s+same\s+(?:place|position)\s+(?:in|on)\s+every\s+(?:element\s+)?(\S+)$', s, re.I)
    if m:
        return Command(type="verify_same_place_every", target=m.group(1), values=[m.group(2)])
    m = re.match(r'^verify\s+element\s+(\S+)\s+is\s+the\s+same\s+size\s+as\s+(?:element\s+)?(\S+)$', s, re.I)
    if m:
        return Command(type="verify_same_size", target=m.group(1), values=[m.group(2)])
    m = re.match(r'^store\s+position\s+of\s+(\S+)\s+(?:in|on)\s+(?:element\s+)?(\S+)\s+as\s+(\S+)$', s, re.I)
    if m:
        return Command(type="store_position", target=m.group(1), values=[m.group(2)], variable_name=m.group(3))

    # =============================
    # CLICK COMMAND
    # =============================
    # TAP TEXT COMMAND — tap by visible label/text without needing a pre-recorded locator
    # tap text "Search"  |  click text "Go"   (before plain click, which swallowed it)
    m = re.match(r'^(?:tap|click)\s+text\s+"(.*?)"$', s, re.I)
    if m:
        return Command(type="tap_text", text=m.group(1))

    if re.match(r'^click\s+if\s+', s, re.I):
        # "click if visible a b" / "click if visible x wait 2" fell through to a
        # plain click with the whole tail as the element name.
        raise ValueError("'click if visible' takes one element name, e.g. "
                         "click if visible close_popup [wait 2 seconds]")

    if s.lower().startswith("click "):
        target = re.sub(r"^click\s+(on\s+)?(element\s+)?", "", s, flags=re.IGNORECASE).strip()
        return Command(type="click", target=target)

    # DOUBLE TAP COMMAND — must be before plain "tap" rule
    # double tap <element>  |  double click <element>
    m = re.match(r'^double\s+(?:tap|click)\s+(.*)', s, re.I)
    if m:
        return Command(type="double_tap", target=m.group(1).strip())

    # LONG PRESS COMMAND — must be before plain "tap" rule
    # long press <element>  |  long tap <element>  |  hold <element>
    m = re.match(r'^(?:long\s+(?:press|tap)|hold)\s+(.*)', s, re.I)
    if m:
        return Command(type="long_press", target=m.group(1).strip())

    # TAP COMMAND (alias of click)
    if s.lower().startswith("tap "):
        target = re.sub(r"^tap\s+(on\s+)?(element\s+)?", "", s, flags=re.IGNORECASE).strip()
        return Command(type="tap", target=target)

    # =============================
    # FILL / TYPE COMMAND
    # =============================
    # Matches: type "hello" into search_box  OR  fill "hello" in search_box
    if s.lower().startswith("type ") or s.lower().startswith("fill "):
        pattern = r"^(?:type|fill)\s+\"(.*?)\"\s+(?:into|in)\s+(.*)"
        match = re.search(pattern, s, re.IGNORECASE)
        if match:
            text_to_type, target = match.groups()
            return Command(type="fill", text=text_to_type, target=target.strip())
        # type "text"  (no element — types into the currently focused element)
        m2 = re.match(r'^(?:type|fill)\s+"(.*?)"$', s, re.I)
        if m2:
            return Command(type="type_text", text=m2.group(1))

    # =============================
    # SCREENSHOT
    # screenshot | take screenshot | capture screenshot | take screenshot as <label>
    # =============================
    if re.match(r'^(?:take\s+|capture\s+)?screenshot', s, re.I):
        label_match = re.search(r'\bas\s+(\w+)\b', s, re.I)
        return Command(type="screenshot", target=label_match.group(1) if label_match else "capture")

    # =============================
    # VERTICAL SCROLL
    # scroll down | scroll up | scroll down N times | scroll down N
    # =============================
    m = re.match(r'^scroll\s+(down|up)(?:\s+(\d+)\s+times?|\s+(\d+))?$', s, re.I)
    if m:
        direction = m.group(1).lower()
        times  = int(m.group(2)) if m.group(2) else None   # "3 times"
        pixels = int(m.group(3)) if m.group(3) else None   # raw pixel override
        amount = (times * 500) if times else (pixels if pixels else 500)
        if direction == "up":
            amount = -amount
        return Command(type="scroll", count=amount)

    # =============================
    # VERIFY ELEMENT IS VISIBLE  (proper Playwright visibility assertion)
    # verify element <locator> is visible
    # =============================
    m = re.match(r'^verify\s+element\s+(\S+)\s+is\s+visible$', s, re.I)
    if m:
        return Command(type="verify_element_visible", target=m.group(1))

    # =============================
    # WAIT UNTIL ELEMENT TEXT IS NOT  (condition-based; waits for text to change)
    # wait until element <locator> text is not "<value>"
    # Must precede the visibility and generic wait rules below.
    # =============================
    m = re.match(r'^wait\s+until\s+element\s+(\S+)\s+text\s+is\s+not\s+"(.*?)"$', s, re.I)
    if m:
        return Command(type="wait_until_text_not", target=m.group(1), text=m.group(2))

    # =============================
    # WAIT UNTIL ELEMENT IS VISIBLE  (condition-based wait, not a sleep)
    # wait until element <locator> is visible
    # Must precede the generic `wait for|until <target>` rule below.
    # =============================
    # … is visible up to 40 seconds   (longer than the default action timeout —
    # a popup that the site opens on a timer, as Testsigma's own 30 s wait allows)
    m = re.match(r'^wait\s+until\s+element\s+(\S+)\s+is\s+visible'
                 r'(?:\s+(?:up\s+to|within|for)\s+(\d+)\s+seconds?)?$', s, re.I)
    if m:
        return Command(type="wait_until_visible", target=m.group(1),
                       wait=int(m.group(2)) if m.group(2) else None)

    # =============================
    # ENTER OTP — distribute an N-digit code across N ordered inputs
    # enter otp "<value>" into <locator>
    # =============================
    # =============================
    # FETCH OTP FROM THE QA PORTAL
    #   fetch otp for "<mobile>" as <var>
    #   fetch otp for "<mobile>" as <var> after "<previous>"
    # The `after` form polls until the portal shows something DIFFERENT, which is
    # the only way to prove the code is new — the portal exposes no timestamp.
    # Must precede the `enter otp` rule so "otp" is not captured by it.
    # =============================
    m = re.match(r'^fetch\s+otp\s+for\s+"(.*?)"\s+as\s+(\S+)'
                 r'(?:\s+after\s+"(.*?)")?$', s, re.I)
    if m:
        return Command(type="fetch_otp", text=m.group(1), variable_name=m.group(2),
                       target=m.group(3) or "")

    m = re.match(r'^enter\s+otp\s+"(.*?)"\s+(?:into|in)\s+(\S+)$', s, re.I)
    if m:
        return Command(type="enter_otp", text=m.group(1), target=m.group(2).strip())

    # =============================
    # VERIFY ELEMENT EXISTS
    # verify element exists <locator>  |  assert element exists <locator>
    # =============================
    m = re.match(r'^(?:verify|assert)\s+element\s+exists\s+(\S+)$', s, re.I)
    if m:
        return Command(type="verify_element_exists", target=m.group(1))

    # =============================
    # VERIFY ELEMENT NOT EXISTS
    # verify element not exists <locator>
    # =============================
    m = re.match(r'^(?:verify|assert)\s+element\s+not\s+exists\s+(\S+)$', s, re.I)
    if m:
        return Command(type="verify_element_not_exists", target=m.group(1))

    # verify element <locator> is not present   (DOM absence — reads naturally
    # alongside "verify element <locator> is visible")
    m = re.match(r'^(?:verify|assert)\s+element\s+(\S+)\s+is\s+not\s+present$', s, re.I)
    if m:
        return Command(type="verify_element_not_exists", target=m.group(1))

    # verify element <locator> is not visible   (absent OR present-but-hidden)
    m = re.match(r'^(?:verify|assert)\s+element\s+(\S+)\s+is\s+not\s+visible$', s, re.I)
    if m:
        return Command(type="verify_element_not_visible", target=m.group(1))

    # =============================
    # VERIFY ELEMENT EXACT TEXT
    # verify element <locator> has text "<text>"
    # =============================
    m = re.match(r'^verify\s+element\s+(\S+)\s+has\s+text\s+"(.*?)"$', s, re.I)
    if m:
        locator, text = m.groups()
        return Command(type="verify_element_exact", target=locator, text=text)

    # =============================
    # VERIFY ELEMENT CONTAINS TEXT
    # verify element <locator> contains "<text>"
    # =============================
    m = re.match(r'^verify\s+element\s+(\S+)\s+contains\s+"(.*?)"$', s, re.I)
    if m:
        locator, text = m.groups()
        return Command(type="verify_element_contains", target=locator, text=text)

    # =============================
    # VERIFY MULTIPLE GLOBAL TEXTS
    # verify texts "a", "b", "c"
    # =============================
    if re.match(r'^verify\s+texts?\s+', s, re.I):
        payload = re.sub(r'^verify\s+texts?\s+', '', s, flags=re.I).strip()
        # Build a comma-separated bare string: "a", "b" → a,b
        parts = re.findall(r'"(.*?)"', payload)
        texts = ",".join(parts) if parts else payload
        return Command(type="verify_multiple_texts", text=texts)

    # =============================
    # STORE TEXT OF/FROM <locator> AS <var>   (extract element inner text)
    # "store text of X as Y"   — original syntax
    # "store text from X as Y" — recorder-generated syntax (both accepted)
    # =============================
    m = re.match(r'^store\s+text\s+(?:of|from)\s+(\S+)\s+as\s+(\S+)$', s, re.I)
    if m:
        locator, var_name = m.groups()
        return Command(type="extract_text", target=locator, variable_name=var_name)

    # =============================
    # STORE PAGE URL / TITLE
    # store lowercase of "<text|${var}>" as <var>   |  uppercase | trimmed
    m = re.match(r'^store\s+(lowercase|uppercase|trimmed)\s+of\s+"?([^"]+?)"?\s+as\s+(\S+)$', s, re.I)
    if m:
        return Command(type="transform_text", text=m.group(2).strip(), values=[m.group(1).lower()],
                       variable_name=m.group(3))

    # store regex "<pattern>" from <var|page url> as <var>
    m = re.match(r'^store\s+regex\s+"(.*?)"\s+from\s+(page\s+url|url|\S+)\s+as\s+(\S+)$', s, re.I)
    if m:
        return Command(type="extract_regex", text=m.group(1), target=m.group(2).strip(),
                       variable_name=m.group(3))

    # store page url as <var>  |  store page title as <var>
    # =============================
    m = re.match(r'^store\s+page\s+url\s+as\s+(\S+)$', s, re.I)
    if m:
        return Command(type="extract_url", variable_name=m.group(1))

    m = re.match(r'^store\s+page\s+title\s+as\s+(\S+)$', s, re.I)
    if m:
        return Command(type="extract_title", variable_name=m.group(1))

    # =============================
    # STORE ATTRIBUTE <attr> OF <locator> AS <var>
    # =============================
    m = re.match(r'^store\s+attribute\s+(\S+)\s+of\s+(\S+)\s+as\s+(\S+)$', s, re.I)
    if m:
        attr, locator, var_name = m.groups()
        return Command(type="extract_attribute", target=locator, attribute=attr, variable_name=var_name)

    # =============================
    # STORE VALUE OF <locator> AS <var>   (input element value)
    # =============================
    m = re.match(r'^store\s+value\s+of\s+(\S+)\s+as\s+(\S+)$', s, re.I)
    if m:
        locator, var_name = m.groups()
        return Command(type="extract_input", target=locator, variable_name=var_name)

    # =============================
    # STORE COUNT OF <locator> AS <var>
    # =============================
    m = re.match(r'^store\s+count\s+of\s+(\S+)\s+as\s+(\S+)$', s, re.I)
    if m:
        locator, var_name = m.groups()
        return Command(type="extract_count", target=locator, variable_name=var_name)

    # =============================
    # CREATE / STORE LITERAL VARIABLE
    # store "value" as <var>  |  create variable <var> with value "value"
    # Must come AFTER all other "store X of Y" patterns above.
    # =============================
    m = re.match(r'^store\s+"(.*?)"\s+as\s+(\S+)$', s, re.I)
    if m:
        value, var_name = m.groups()
        return Command(type="create_variable", target=var_name, text=value)

    m = re.match(r'^create\s+variable\s+(\S+)\s+(?:with\s+value\s+)?"(.*?)"$', s, re.I)
    if m:
        var_name, value = m.groups()
        return Command(type="create_variable", target=var_name, text=value)

    # =============================
    # MATH / CALCULATE
    # calculate <n1> +|-|*|/ <n2> as <var>
    # =============================
    m = re.match(r'^calculate\s+(\S+)\s+([+\-*/])\s+(\S+)\s+as\s+(\S+)$', s, re.I)
    if m:
        num1, op, num2, var_name = m.groups()
        return Command(type="math", target=num1, text=op, values=[num2], variable_name=var_name)

    # 18 percent of ${price} | 18% of price
    m = re.match(r'^calculate\s+(\S+?)\s*(?:%|percent|per\s*cent)\s+of\s+(\S+)\s+as\s+(\S+)$', s, re.I)
    if m:
        return Command(type="percent_of", text=m.group(1), values=[m.group(2)], variable_name=m.group(3))
    # Any sum: calculate (${a} + ${b}) * 2 as total | calculate ${price} % 7 as r | calculate a + b + c as d
    m = re.match(r'^calculate\s+(.+?)\s+as\s+(\S+)$', s, re.I)
    if m and re.search(r"[-+*/%^()]", m.group(1)):
        return Command(type="calc_expr", text=m.group(1).strip(), variable_name=m.group(2))
    # round ${price} to 2 decimals as p | round up price as p | round price down as p
    m = re.match(r'^round\s+(?:(up|down)\s+)?(\S+)(?:\s+(up|down))?(?:\s+to\s+(\d+)\s+'
                 r'(?:decimals?|decimal\s+places?|places?))?(?:\s+(up|down))?\s+as\s+(\S+)$', s, re.I)
    if m:
        mode = (m.group(1) or m.group(3) or m.group(5) or "").lower()
        return Command(type="round_value", text=m.group(2), count=int(m.group(4) or 0),
                       values=[mode], variable_name=m.group(6))
    # increase counter by 1 | increment counter | decrease stock by 2
    m = re.match(r'^(increase|increment|decrease|decrement|reduce)\s+(?:the\s+)?(?:value\s+of\s+)?'
                 r'(\$\{[^}]+\}|[A-Za-z_]\w*)(?:\s+by\s+(\S+))?$', s, re.I)
    if m:
        var = re.sub(r"^\$\{|\}$", "", m.group(2))
        amount = m.group(3) or "1"
        if m.group(1).lower() in ("decrease", "decrement", "reduce"):
            amount = amount[1:] if amount.startswith("-") else "-" + amount
        return Command(type="adjust_var", target=var, text=amount)
    # store length of "${name}" as n | store length of name as n
    m = re.match(r'^store\s+(?:the\s+)?length\s+of\s+(.+?)\s+as\s+(\S+)$', s, re.I)
    if m:
        return Command(type="store_length", text=m.group(1).strip(), variable_name=m.group(2))

    # =============================
    # VERIFY STORED VARIABLE IS NOT  (negative exact-match on a stored value)
    # verify stored <var> is not "<value>"
    # Must precede the `contains` rule below.
    # =============================
    m = re.match(r'^verify\s+(?:stored\s+)?(?:variable\s+)?(\S+)\s+is\s+not\s+"(.*?)"$', s, re.I)
    if m:
        var_name, value = m.groups()
        return Command(type="verify_var_not_equals", target=var_name, text=value)

    # =============================
    # VERIFY STORED VARIABLE EQUALS  (exact match, Testsigma "Verify … is equal to")
    # verify stored <var> equals "<value>"  |  verify stored <var> is "<value>"
    #                                        |  verify stored <var> is equal to "<value>"
    # =============================
    m = re.match(r'^verify\s+stored\s+(?:variable\s+)?(\S+)\s+(?:equals|is\s+equal\s+to|is)\s+"(.*?)"(\s+ignoring\s+case)?$', s, re.I)
    if m:
        var_name, value, ic = m.groups()
        return Command(type="verify_var_equals", target=var_name, text=value, values=["ignore_case"] if ic else [])

    # =============================
    # VERIFY STORED VARIABLE CONTAINS
    # verify <var> contains "<partial>"  |  verify stored <var> contains "<partial>"
    # =============================
    m = re.match(r'^verify\s+(?:stored\s+)?(?:variable\s+)?(\S+)\s+contains\s+"(.*?)"(\s+ignoring\s+case)?$', s, re.I)
    if m:
        var_name, partial, ic = m.groups()
        return Command(type="verify_var_contains", target=var_name, text=partial, values=["ignore_case"] if ic else [])

    # =============================
    # ALERT / PERMISSION HANDLING
    # =============================
    if re.match(r'^(?:dismiss|accept|handle|clear)\s+alerts?$', s, re.I):
        return Command(type="dismiss_alerts")

    # =============================
    # GOOGLE PLAY RATING POPUP
    # dismiss rating | dismiss play rating | skip rating | dismiss rating popup
    # =============================
    if re.match(r'^(?:dismiss|skip|close|handle)\s+(?:play\s+)?rating(?:\s+popup)?$', s, re.I):
        return Command(type="dismiss_play_rating")

    # =============================
    # Plain Testsigma-style steps (used by the Testsigma importer too)
    # wait until element <loc> is not visible
    # select option "<value or label>" in <loc>
    # clear <loc>
    # run javascript "<one line of JS>"
    # scroll element <loc> horizontally by <N>
    # remove "<chars>" from "<text or ${var}>" and store as <var>
    # =============================
    m = re.match(r'^wait\s+until\s+(?:element\s+)?(\S+)\s+is\s+not\s+(?:visible|displayed|present)$', s, re.I)
    if m:
        return Command(type="wait_until_not_visible", target=m.group(1))
    m = re.match(r'^select\s+option\s+"(.*?)"\s+in\s+(\S+)$', s, re.I)
    if m:
        return Command(type="select_option", text=m.group(1), target=m.group(2))
    m = re.match(r'^clear\s+(?:text\s+(?:in|of)\s+)?(?!alerts?$)(\S+)$', s, re.I)
    if m:
        return Command(type="clear_field", target=m.group(1))
    # store javascript "<expression>" as <var>  — Testsigma "Store the value
    # returned by JavaScript". The expression's result (string/number/JSON)
    # lands in the variable.
    m = re.match(r'^store\s+(?:javascript|js)\s+"(.+)"\s+as\s+(\S+)$', s, re.I)
    if m:
        return Command(type="store_javascript", text=m.group(1), variable_name=m.group(2))
    m = re.match(r'^run\s+javascript\s+"(.+)"$', s, re.I)
    if m:
        return Command(type="run_javascript", text=m.group(1))
    m = re.match(r'^scroll\s+element\s+(\S+)\s+horizontally\s+by\s+(-?\d+)(?:\s+pixels?)?$', s, re.I)
    if m:
        return Command(type="scroll_element_x", target=m.group(1), text=m.group(2))
    m = re.match(r'^remove\s+"(.*?)"\s+from\s+"(.*?)"\s+and\s+store\s+as\s+(\S+)$', s, re.I)
    if m:
        return Command(type="remove_text", values=[m.group(1)], text=m.group(2), variable_name=m.group(3))

    # WAIT FOR ELEMENT — explicit visibility wait (no scrolling)
    # wait for element <name>  |  wait until element <name>  |  wait until <name> visible
    m = re.match(r'^wait\s+(?:for|until)\s+(?:element\s+)?(\S+)(?:\s+(?:visible|appears?))?$', s, re.I)
    if m:
        return Command(type="wait_for_element", target=m.group(1).strip())

    # =============================
    # SCROLL TO ELEMENT
    # scroll to <name>  |  scroll to element <name>
    # =============================
    m = re.match(r'^scroll\s+to\s+(?:element\s+)?(\S+)$', s, re.I)
    if m:
        return Command(type="scroll_to", target=m.group(1).strip())

    # =============================
    # DEVICE CONTROLS
    # =============================
    if re.match(r'^press\s+back$', s, re.I) or s.lower() == "go back":
        return Command(type="press_back")
    if re.match(r'^go\s+forward$', s, re.I) or re.match(r'^browser\s+forward$', s, re.I):
        return Command(type="go_forward")
    # press key Space | press key Tab | press key Escape … (one keyboard key on the page)
    m = re.match(r'^press\s+key\s+(\w+)$', s, re.I)
    if m:
        return Command(type="press_key", text=m.group(1).capitalize())
    if re.match(r'^press\s+home$', s, re.I):
        return Command(type="press_home")
    if re.match(r'^press\s+(?:enter|return|search)$', s, re.I):
        return Command(type="press_enter")
    if re.match(r'^(?:hide|dismiss)\s+keyboard$', s, re.I):
        return Command(type="hide_keyboard")
    # "Wait until the current page is loaded completely" (Testsigma)
    if re.match(r'^wait\s+(?:for\s+|until\s+)?(?:the\s+)?(?:current\s+)?page\s+(?:to\s+)?(?:is\s+)?load(?:ed)?(?:\s+completely)?$', s, re.I):
        return Command(type="wait_page_load")

    # =============================
    # SWIPE
    # =============================
    # Finger swipe (Testsigma wording):
    #   swipe bottom to top [in the screen] [3 times] [for duration 2 seconds]
    #   swipe up | swipe down | swipe bottom to middle | swipe middle to top …
    #   swipe bottom to top until element <loc> is visible[, max 15 times][, wait 1]
    _SW = r'(bottom\s+to\s+top|top\s+to\s+bottom|bottom\s+to\s+middle|middle\s+to\s+top|' \
          r'top\s+to\s+middle|middle\s+to\s+bottom|right\s+to\s+left|left\s+to\s+right|up|down|left|right)'
    #   … , closing <loc> [and|or|, <loc>] [, closing <loc>]  — popups that MAY open
    #   while swiping; each is closed only if it shows, so the list is "any of".
    _CL = r'[A-Za-z_][A-Za-z0-9_]*'
    m = re.match(r'^swipe\s+' + _SW + r'(?:\s+in\s+the\s+screen)?\s+until\s+(?:element\s+)?(\S+)\s+is\s+visible'
                 r'((?:\s*,?\s*closing\s+' + _CL + r'(?:\s*(?:,|\s+and|\s+or)\s*(?!closing\b|max\b|wait\b)' + _CL + r')*)*)'
                 r'(?:\s*,?\s*max\s+(\d+)\s+times?)?(?:\s*,?\s*wait\s+(\d+(?:\.\d+)?))?\s*$', s, re.I)
    if m:
        closers = [c for c in re.split(r'\s*,\s*|\s+and\s+|\s+or\s+|\s*closing\s+',
                                       m.group(3) or "", flags=re.I) if c]
        return Command(type="swipe_until_visible", target=m.group(2), values=[_swipe_span(m.group(1))] + closers,
                       count=int(m.group(4) or 15), wait=float(m.group(5) or 1))
    m = re.match(r'^swipe\s+' + _SW + r'(?:\s+in\s+the\s+screen)?(?:\s+(\d+)\s+times?)?'
                 r'(?:\s+for\s+duration\s+(\d+(?:\.\d+)?)\s+seconds?)?$', s, re.I)
    if m:
        return Command(type="swipe_screen", values=[_swipe_span(m.group(1))],
                       count=int(m.group(2) or 1), wait=float(m.group(3)) if m.group(3) else None)
    if re.match(r'^swipe\s+left$', s, re.I):
        return Command(type="swipe_left")
    if re.match(r'^swipe\s+right$', s, re.I):
        return Command(type="swipe_right")

    # =============================
    # TAB / WINDOW MANAGEMENT
    # switch to tab 2 | focus tab 0 | go to window 1
    # close tab | close tab 2 | close all tabs
    # open new tab | list tabs
    # =============================
    m = re.match(r'^(?:switch\s+to\s+(?:tab|window)|focus\s+(?:tab|window)|go\s+to\s+(?:tab|window))\s+(\d+)$', s, re.I)
    if m:
        return Command(type="switch_tab", count=int(m.group(1)))

    # By what the tab shows — its address or its title — which stays true
    # however many tabs are open:
    #   switch to tab with url containing "/photos"   | switch to window whose url contains "x"
    #   switch to tab with title "Eden Enterprises"
    m = re.match(r'^(?:switch\s+to|focus|go\s+to)\s+(?:the\s+)?(?:tab|window)\s+(?:with|whose)\s+'
                 r'(url|address|title)\s+(?:that\s+)?(?:contains?|containing|like|as|is)?\s*"([^"]+)"$', s, re.I)
    if m:
        if m.group(1).lower() == "title":
            return Command(type="switch_window_title", text=m.group(2))
        return Command(type="switch_tab_url", text=m.group(2))

    # wait for new tab | wait for a new tab to open (and switch to it) [within N seconds]
    m = re.match(r'^wait\s+(?:for\s+)?(?:a\s+|the\s+)?new\s+(?:tab|window|popup)'
                 r'(?:\s+to\s+open)?(?:\s+and\s+switch\s+to\s+it)?'
                 r'(?:\s+(?:within|for)\s+(\d+)\s*(?:s|sec|secs|seconds?))?$', s, re.I)
    if m:
        return Command(type="wait_new_tab", wait=float(m.group(1)) if m.group(1) else None)

    # verify 2 tabs are open | verify tab count is 2 | verify number of tabs is 2
    m = (re.match(r'^verify\s+(\d+)\s+(?:tabs?|windows?)\s+(?:are|is)\s+open$', s, re.I)
         or re.match(r'^verify\s+(?:the\s+)?(?:tab|window)\s+count\s+is\s+(\d+)$', s, re.I)
         or re.match(r'^verify\s+(?:the\s+)?number\s+of\s+(?:tabs|windows)\s+is\s+(\d+)$', s, re.I))
    if m:
        return Command(type="verify_tab_count", count=int(m.group(1)))

    # A tab named by its RELATIONSHIP rather than its number. An index is only
    # knowable if you have counted what is open, and the count changes the
    # moment a click opens a popup — which is exactly when you need to switch.
    #   switch to parent tab | switch to child tab | switch to current tab
    #   switch to first tab  | switch to last tab  | switch to new tab
    m = re.match(r'^(?:switch\s+to|focus|go\s+to)\s+(?:the\s+)?'
                 r'(parent|child|current|first|last|new|newest|previous)\s+'
                 r'(?:tab|window)$', s, re.I)
    if m:
        return Command(type="switch_tab", text=m.group(1).lower())

    m = re.match(r'^close\s+(?:tab|window)\s+(\d+)$', s, re.I)
    if m:
        return Command(type="close_tab", count=int(m.group(1)))

    if re.match(r'^close\s+(?:current\s+)?(?:tab|window)$', s, re.I):
        return Command(type="close_tab")

    if re.match(r'^close\s+all\s+(?:tabs?|windows?)$', s, re.I):
        return Command(type="close_all_tabs")

    if re.match(r'^open\s+new\s+tab$', s, re.I):
        return Command(type="open_new_tab")

    if re.match(r'^list\s+(?:tabs?|windows?)$', s, re.I):
        return Command(type="list_tabs")

    # =============================
    # IFRAME / FRAME SWITCHING
    # switch to iframe "selector" | switch to frame <name> | enter iframe <xpath>
    # exit iframe | exit frame | switch to main frame | switch to default content
    # =============================
    # By position where the test is now, counting from 0 like tabs:
    #   switch to iframe 0 | switch to first iframe | switch to 2nd iframe | switch to last iframe
    _ORD = {"first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4, "last": -1}
    m = re.match(r'^(?:switch\s+to|enter|go\s+into)\s+(?:the\s+)?'
                 r'(first|second|third|fourth|fifth|last|\d+(?:st|nd|rd|th))\s+(?:iframe|frame)$', s, re.I)
    if m:
        w = m.group(1).lower()
        idx = _ORD[w] if w in _ORD else int(re.match(r"\d+", w).group(0)) - 1
        return Command(type="switch_iframe_index", count=idx)
    m = re.match(r'^(?:switch\s+to|enter|go\s+into)\s+(?:iframe|frame)\s+(?:number\s+|index\s+)?(\d+)$', s, re.I)
    if m:
        return Command(type="switch_iframe_index", count=int(m.group(1)))

    # By element name (from Elements) or by XPath / CSS:
    #   switch to iframe payment_frame | switch to iframe "//iframe[@id='pay']"
    m = re.match(r'^(?:switch\s+to|enter|go\s+into)\s+(?:the\s+)?(?:iframe|frame)\s+(.+)$', s, re.I)
    if m:
        return Command(type="switch_iframe", target=m.group(1).strip().strip('"').strip("'"))

    if re.match(r'^(?:exit\s+(?:iframe|frame)|switch\s+to\s+(?:main\s+frame|default\s+content))$', s, re.I):
        return Command(type="exit_iframe")

    # =============================
    # FAKE DATA GENERATION
    # generate fake name as var_name
    # generate fake email as var_name  … (name/email/phone/uuid/number/address/
    #   city/country/company/password/username/url/date/text/paragraph/postcode/
    #   first name/last name/credit card)
    # =============================
    m = re.match(
        r'^generate\s+fake\s+'
        r'(name|first\s+name|last\s+name|email|phone|uuid|number|address|city|country'
        r'|company|password|username|url|date|text|paragraph|postcode|credit\s+card)'
        r'\s+as\s+(\S+)$',
        s, re.I
    )
    if m:
        return Command(type="generate_fake",
                       text=m.group(1).lower().strip(),
                       variable_name=m.group(2))

    # generate random number <min> <max> as <var>
    m = re.match(r'^generate\s+random\s+number\s+(\S+)\s+(\S+)\s+as\s+(\S+)$', s, re.I)
    if m:
        return Command(type="random_number",
                       target=m.group(1), text=m.group(2),
                       variable_name=m.group(3))

    # generate random string <length> as <var>
    m = re.match(r'^generate\s+random\s+string\s+(\d+)\s+as\s+(\S+)$', s, re.I)
    if m:
        return Command(type="random_string",
                       count=int(m.group(1)),
                       variable_name=m.group(2))

    # =============================
    # DATE / TIME
    # get today as <var>  |  get current date as <var>
    # get timestamp as <var>  |  get now as <var>
    # get date +7 days as <var>  |  get date -1 day as <var>
    # format date "${var}" as "DD/MM/YYYY" into <var>
    # =============================
    m = re.match(r'^get\s+(?:current\s+)?(?:today|date)\s+as\s+(\S+)$', s, re.I)
    if m:
        return Command(type="get_date", text="today", variable_name=m.group(1))

    m = re.match(r'^get\s+(?:current\s+)?(?:timestamp|time|now)\s+as\s+(\S+)$', s, re.I)
    if m:
        return Command(type="get_date", text="timestamp", variable_name=m.group(1))

    m = re.match(r'^get\s+date\s+([+-]\d+)\s+days?\s+as\s+(\S+)$', s, re.I)
    if m:
        return Command(type="get_date",
                       text=f"offset:{m.group(1)}",
                       variable_name=m.group(2))

    m = re.match(r'^format\s+date\s+"(.*?)"\s+as\s+"(.*?)"\s+into\s+(\S+)$', s, re.I)
    if m:
        return Command(type="format_date",
                       text=m.group(1), values=[m.group(2)],
                       variable_name=m.group(3))

    # =============================
    # HTTP / API CALLS
    # api get "url" as <var>
    # api post "url" with body '{"k":"v"}' as <var>
    # store json <var> path data.0.name as <var2>
    # =============================
    m = re.match(r'^api\s+get\s+"(.*?)"\s+as\s+(\S+)$', s, re.I)
    if m:
        return Command(type="api_get", text=m.group(1), variable_name=m.group(2))

    m = re.match(r'^api\s+post\s+"(.*?)"\s+with\s+body\s+\'(.*?)\'\s+as\s+(\S+)$', s, re.I)
    if not m:
        m = re.match(r'^api\s+post\s+"(.*?)"\s+with\s+body\s+"(.*?)"\s+as\s+(\S+)$', s, re.I)
    if m:
        return Command(type="api_post",
                       text=m.group(1), target=m.group(2),
                       variable_name=m.group(3))

    # network capture
    if re.match(r'^start\s+capturing\s+network\s+requests?$', s, re.I):
        return Command(type="net_capture_start")
    m = re.match(r'^verify\s+network\s+request\s+containing\s+"(.*?)"\s+was\s+(not\s+)?sent$', s, re.I)
    if m:
        return Command(type="net_verify", text=m.group(1), values=["not" if m.group(2) else "sent"])
    m = re.match(r'^store\s+network\s+request\s+containing\s+"(.*?)"\s+as\s+(\S+)$', s, re.I)
    if m:
        return Command(type="net_store", text=m.group(1), variable_name=m.group(2))

    # verify recommended products order in <var> for city "<city>"      (API JSON)
    # verify recommended products carousel order on page for city "<city>"  (rendered)
    # The `for city "…"` clause is optional: left out, the search city is
    # taken from the API response / the page URL of the search that was opened.
    m = re.match(r'^verify\s+recommended\s+products\s+order\s+in\s+(\S+)(?:\s+for\s+city\s+"(.*?)")?$', s, re.I)
    if m:
        return Command(type="verify_recommended_order_api", target=m.group(1), text=m.group(2) or "")
    m = re.match(r'^verify\s+recommended\s+products\s+prefer\s+search\s+city\s+on\s+page(?:\s+for\s+city\s+"(.*?)")?$', s, re.I)
    if m:
        return Command(type="verify_recommended_prefer_city", text=m.group(1) or "")
    m = re.match(r'^store\s+position\s+of\s+recommended\s+product\s+"(.*?)"\s+on\s+page\s+as\s+(\S+)$', s, re.I)
    if m:
        return Command(type="store_recommended_position", text=m.group(1), variable_name=m.group(2))
    m = re.match(r'^verify\s+(?:stored\s+)?(?:variable\s+)?(\S+)\s+is\s+(greater\s+than|less\s+than|at\s+least|at\s+most)\s+"?([^"]+?)"?$', s, re.I)
    if m:
        return Command(type="verify_var_compare", target=m.group(1),
                       text=re.sub(r"\s+", " ", m.group(2).lower()), values=[m.group(3).strip()])
    m = re.match(r'^verify\s+recommended\s+products\s+carousel\s+order\s+on\s+page(?:\s+for\s+city\s+"(.*?)")?$', s, re.I)
    if m:
        return Command(type="verify_recommended_order_page", text=m.group(1) or "")

    m = re.match(r'^store\s+json\s+(\S+)\s+path\s+(\S+)\s+as\s+(\S+)$', s, re.I)
    if m:
        return Command(type="extract_json",
                       target=m.group(1), text=m.group(2),
                       variable_name=m.group(3))

    # =============================
    # EXCEL / CSV
    # read excel "file.xlsx" row 1 col 2 as <var>
    # read excel "file.xlsx" row 1 col A as <var>
    # read excel row "file.xlsx" row 3 as <var>      (full row → list)
    # read csv "file.csv" row 1 col email as <var>
    # read csv "file.csv" row 2 col 3 as <var>
    # =============================
    m = re.match(
        r'^read\s+excel\s+"(.*?)"\s+row\s+(\d+)\s+col\s+(\S+)\s+as\s+(\S+)$', s, re.I
    )
    if m:
        col_raw = m.group(3)
        col = int(col_raw) if col_raw.isdigit() else col_raw
        return Command(type="read_excel_cell",
                       text=m.group(1), target=str(m.group(2)),
                       values=[str(col)], variable_name=m.group(4))

    m = re.match(
        r'^read\s+excel\s+"(.*?)"\s+row\s+(\d+)\s+as\s+(\S+)$', s, re.I
    )
    if m:
        return Command(type="read_excel_row",
                       text=m.group(1), target=m.group(2),
                       variable_name=m.group(3))

    m = re.match(
        r'^read\s+csv\s+"(.*?)"\s+row\s+(\d+)\s+col\s+(\S+)\s+as\s+(\S+)$', s, re.I
    )
    if m:
        col_raw = m.group(3)
        col = int(col_raw) if col_raw.isdigit() else col_raw
        return Command(type="read_csv_cell",
                       text=m.group(1), target=str(m.group(2)),
                       values=[str(col)], variable_name=m.group(4))

    # =============================
    # JAVASCRIPT ACTIONS
    # js click <element>
    # js scroll to <element>
    # js scroll down [N]  |  js scroll up [N]  |  js scroll top  |  js scroll bottom
    # js type "text" into <element>
    # js set value "text" on <element>
    # js focus <element>
    # js submit <element>
    # js dispatch <event> on <element>
    # =============================
    m = re.match(r'^js\s+click\s+(\S+)$', s, re.I)
    if m:
        return Command(type="js_click", target=m.group(1).strip())

    m = re.match(r'^js\s+scroll\s+to\s+(\S+)$', s, re.I)
    if m:
        return Command(type="js_scroll_to", target=m.group(1).strip())

    m = re.match(r'^js\s+scroll\s+(down|up|top|bottom)(?:\s+(\d+))?$', s, re.I)
    if m:
        return Command(type="js_scroll",
                       text=m.group(1).lower(),
                       count=int(m.group(2)) if m.group(2) else 300)

    m = re.match(r'^js\s+type\s+"(.*?)"\s+into\s+(\S+)$', s, re.I)
    if m:
        return Command(type="js_type", text=m.group(1), target=m.group(2).strip())

    m = re.match(r'^js\s+set\s+value\s+"(.*?)"\s+on\s+(\S+)$', s, re.I)
    if m:
        return Command(type="js_set_value", text=m.group(1), target=m.group(2).strip())

    m = re.match(r'^js\s+focus\s+(\S+)$', s, re.I)
    if m:
        return Command(type="js_focus", target=m.group(1).strip())

    m = re.match(r'^js\s+submit\s+(\S+)$', s, re.I)
    if m:
        return Command(type="js_submit", target=m.group(1).strip())

    m = re.match(r'^js\s+dispatch\s+(\S+)\s+on\s+(\S+)$', s, re.I)
    if m:
        return Command(type="js_dispatch", text=m.group(1), target=m.group(2).strip())

    # =============================
    # CALL REUSABLE STEPS
    # call <name>  — inline-expands a saved reusable step group at runtime
    # =============================
    # The rest of the line, not the first word: a group name may contain
    # spaces, so `\S+` silently truncated `call my login flow` to `my` and the
    # step failed with "not found" naming something nobody had typed.
    m = re.match(r'^call\s+(.+)$', s, re.I)
    if m:
        return Command(type="call_reusable", target=m.group(1).strip())

    # =============================
    # TERMINAL FALLBACK
    # =============================
    # =============================
    # COMPARE ANY TWO VALUES (last, so every more specific "verify …" wins first)
    #   verify ${testdata1} equals ${variabledata1}   | verify ${a} = ${b}
    #   verify price is greater than 1000             | verify ${total} >= ${limit}
    #   verify ${city} starts with "Mum" ignoring case| verify ${otp} is a number
    #   verify ${note} is empty / is not empty        | verify ${id} matches "^\d+$"
    # Each side: ${value}, a stored name, "text" or a number (₹1,200 reads as 1200).
    # =============================
    m = _match_compare(s)
    if m:
        return m
    raise ValueError(f"Unknown command: {step}")


_OPND = r'("[^"]*"|\'[^\']*\'|\$\{[^}]+\}|[₹$]?-?\d[\d,]*(?:\.\d+)?%?|[A-Za-z_][\w.]*)'


def _match_compare(s: str):
    from execution.value_ops import OPS, _NO_RIGHT
    ops = sorted(OPS, key=len, reverse=True)
    alt = "|".join(re.escape(o).replace(r"\ ", r"\s+") for o in ops)
    m = re.match(r'^verify\s+(?:that\s+)?(?:stored\s+)?(?:variable\s+|value\s+)?' + _OPND
                 + r'\s*(' + alt + r')(?:\s*' + _OPND + r')?(\s+ignoring\s+case)?\s*$', s, re.I)
    if not m:
        return None
    left, op, right, ic = m.group(1), re.sub(r"\s+", " ", m.group(2).lower()), m.group(3), m.group(4)
    kind = OPS[op]
    if kind in _NO_RIGHT and right:
        return None
    if kind not in _NO_RIGHT and not right:
        return None
    return Command(type="compare_values", target=left, text=op,
                   values=[right or "", "ignore_case" if ic else ""])

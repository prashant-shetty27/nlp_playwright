"""
nlp/parser.py
Single unified NLP/DSL step parser (merged from root command_parser.py and dsl/parser.py).
Supports all NLP step syntax used in .flow files.
"""
import re
from nlp.command import Command


def parse_step(step: str) -> Command:
    s = step.strip()

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
        in_el = re.search(r'\bin\s+(\w+)\s*$', s, re.I)
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
    # CLICK COMMAND
    # =============================
    if s.lower().startswith("click "):
        target = re.sub(r"^click\s+(on\s+)?(element\s+)?", "", s, flags=re.IGNORECASE).strip()
        return Command(type="click", target=target)

    # TAP TEXT COMMAND — tap by visible label/text without needing a pre-recorded locator
    # tap text "Search"  |  click text "Go"
    m = re.match(r'^(?:tap|click)\s+text\s+"(.*?)"$', s, re.I)
    if m:
        return Command(type="tap_text", text=m.group(1))

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
        return Command(type="store_text", target=locator, variable_name=var_name)

    # =============================
    # STORE PAGE URL / TITLE
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

    # =============================
    # VERIFY STORED VARIABLE CONTAINS
    # verify <var> contains "<partial>"  |  verify stored <var> contains "<partial>"
    # =============================
    m = re.match(r'^verify\s+(?:stored\s+)?(?:variable\s+)?(\S+)\s+contains\s+"(.*?)"$', s, re.I)
    if m:
        var_name, partial = m.groups()
        return Command(type="verify_var_contains", target=var_name, text=partial)

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
    if re.match(r'^press\s+home$', s, re.I):
        return Command(type="press_home")
    if re.match(r'^press\s+(?:enter|return|search)$', s, re.I):
        return Command(type="press_enter")
    if re.match(r'^(?:hide|dismiss)\s+keyboard$', s, re.I):
        return Command(type="hide_keyboard")

    # =============================
    # SWIPE
    # =============================
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
    m = re.match(r'^(?:switch\s+to|enter)\s+(?:iframe|frame)\s+(.+)$', s, re.I)
    if m:
        return Command(type="switch_iframe", target=m.group(1).strip().strip('"'))

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
    # TERMINAL FALLBACK
    # =============================
    raise ValueError(f"Unknown command: {step}")

"""
ui/api_client.py — the UI's only route to the backend.

NOT in the original spec: it assumed each page would shell out to runner.py.
That predates the HTTP API, and going back to subprocesses now would bypass
platform validation, browser-engine validation, secret masking in logs, the
bounded run store, and per-step statuses — all of which live in the API layer.

Calls travel over ASGI in-process rather than a TCP port: no second server to
start, no port to configure, but every request still passes through the real
FastAPI routing, Pydantic validation and error handling. A contract broken in the
API therefore breaks here too, which is the point — the UI cannot drift into
calling internals directly.

Every function is async, for two reasons: httpx's ASGI transport is async-only,
and a blocking call inside a NiceGUI handler stalls the event loop for EVERY
connected browser, not just the one that made it. Generation and execution take
seconds to minutes, so that would be a frozen page rather than a slow one.
"""
from __future__ import annotations

import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

#: Long enough for a model call to draft testcases; short enough that a wedged
#: request surfaces as an error the operator can act on rather than a dead page.
DEFAULT_TIMEOUT_S = float(os.getenv("UI_API_TIMEOUT_S", "180"))


class ApiError(RuntimeError):
    """A request failed. `detail` is what the API said, fit to show a person."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


def _client():
    import httpx

    from api.app import app

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://api",
        timeout=DEFAULT_TIMEOUT_S,
    )


async def _call(method: str, path: str, **kw):
    async with _client() as c:
        r = await c.request(method, path, **kw)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except Exception:  # noqa: BLE001
            detail = r.text[:300]
        if isinstance(detail, list):        # Pydantic validation errors
            detail = "; ".join(
                f"{'.'.join(str(x) for x in d.get('loc', [])[1:])}: {d.get('msg')}"
                for d in detail
            )
        raise ApiError(r.status_code, str(detail or r.text[:300]))
    return r.json() if r.content else {}


# ── Reference data ───────────────────────────────────────────────────────────
async def platforms(include_disabled: bool = True) -> list[dict]:
    return (await _call("GET", "/nlp/platforms",
                 params={"include_disabled": include_disabled}))["platforms"]


async def devices(curated_only: bool = True) -> list[dict]:
    return (await _call("GET", "/nlp/devices", params={"curated_only": curated_only}))["devices"]


async def suggest(partial: str, platform: str, limit: int = 12) -> list[dict]:
    """Type-ahead. Returns {phrase, action, template} filtered to this platform."""
    if not partial.strip():
        return []
    return await _call("POST", "/nlp/suggest",
                 json={"partial": partial, "platform": platform, "limit": limit})


async def parse_step(step: str) -> dict:
    return await _call("POST", "/nlp/parse", json={"step": step})


#: Segmentation is a pure function of the step text, so a result is kept for
#: the life of the process; the editor re-renders the same rows many times.
_SEGMENT_CACHE: dict[str, dict] = {}


async def segment_step(step: str) -> dict:
    """Split a step into fixed words and separately-editable values."""
    hit = _SEGMENT_CACHE.get(step)
    if hit is not None:
        return hit
    res = await _call("POST", "/nlp/segment", json={"step": step})
    if len(_SEGMENT_CACHE) > 5000:
        _SEGMENT_CACHE.clear()
    _SEGMENT_CACHE[step] = res
    return res


async def prefetch_segments(steps: list[str]) -> None:
    """Segment a whole test case in one request, warming segment_step's cache."""
    todo = sorted({st for st in steps if st and st not in _SEGMENT_CACHE})
    if not todo:
        return
    try:
        res = await _call("POST", "/nlp/segment-batch", json={"steps": todo})
    except ApiError:
        return
    _SEGMENT_CACHE.update(res.get("segments") or {})


async def step_variables(steps: list[str], environment: str = "") -> dict:
    """
    Where each ${variable} in a test gets its value.

    {"defined": {name: step_no}, "stored": [names], "unresolved": [names]} —
    see POST /nlp/variables. Which command DEFINES a variable is grammar, so it
    is answered there rather than re-derived here.
    """
    return await _call("POST", "/nlp/variables",
                       json={"steps": steps, "environment": environment})


async def locators(page: str | None = None) -> dict:
    return await _call("GET", f"/locators/{page}" if page else "/locators")


async def locators_for(platform: str = "website") -> dict:
    """{group: {name: record}} for one platform, each record tagged with its source."""
    return await _call("GET", "/locators", params={"platform": platform})


async def locator_conflicts(platform: str = "website") -> dict:
    r = await _call("GET", "/locators/conflicts", params={"platform": platform})
    return r.get("conflicts", {})


async def check_locator(name: str, page: str, xpath: str,
                        platform: str = "website") -> dict:
    return await _call("POST", "/locators/check",
                       json={"name": name, "page": page, "xpath": xpath,
                             "platform": platform})


async def check_locator_full(name: str, page: str, xpath: str,
                             platform: str = "website", step: str = "") -> dict:
    """Duplicate check plus name suggestions derived from the selector."""
    return await _call("POST", "/locators/check",
                       json={"name": name, "page": page, "xpath": xpath,
                             "platform": platform, "step": step})


async def resync_snippets() -> dict:
    """Rewrite the editor snippet file so a new element is suggestible at once."""
    return await _call("POST", "/locators/resync-snippets")


async def rename_locator(page: str, name: str, new_name: str,
                         apply: bool = False) -> dict:
    return await _call("POST", f"/locators/{page}/{name}/rename",
                       json={"new_name": new_name, "apply": apply})


async def delete_locator(page: str, name: str) -> dict:
    return await _call("DELETE", f"/locators/{page}/{name}")


async def locator_names() -> list[str]:
    """
    Flat list of every known locator name.

    The endpoint returns {name: "PAGE ➔ name"} for dropdown display, so the keys
    are the names. Read from the response rather than assumed — an earlier guess
    of {"names": [...]} silently produced an empty list, which in a picker looks
    exactly like "this project has no locators yet".
    """
    return sorted((await _call("GET", "/locators/dropdown/names")).keys())


async def locator_labels(platform: str = "") -> dict[str, str]:
    """
    {name: "PAGE ➔ name"} — for showing which group a locator belongs to.

    Pass a platform to get only the names that platform's runner can resolve.
    Anything judging whether a step will RUN needs the scoped map: the full one
    spans every platform, so an Android element looks present in a Website test.
    """
    params = {"platform": platform} if platform else None
    return await _call("GET", "/locators/dropdown/names", params=params)


async def add_locator(page: str, name: str, xpath: str, dna: dict | None = None,
                      force: bool = False) -> dict:
    return await _call("POST", "/locators",
                       json={"page": page, "name": name, "xpath": xpath,
                             "dna": dna or {}, "force": force})


# ── Sources ──────────────────────────────────────────────────────────────────
async def testdata_suggest(partial: str, environment: str = "",
                           limit: int = 12) -> list[dict]:
    """Global / per-environment values matching a partial, by name or by value."""
    res = await _call("GET", "/testdata/suggest",
                      params={"partial": partial, "environment": environment, "limit": limit})
    return res.get("suggestions", [])


async def set_testdata(name: str, value: str, scope: str = "global",
                       environment: str = "", force: bool = False,
                       updating: bool = False) -> dict:
    """Save a value. force=True overwrites a name the store already has;
    updating=True says the name is known to exist and only its value changes."""
    return await _call("PUT", "/testdata", json={"name": name, "value": value,
                                                 "scope": scope,
                                                 "environment": environment,
                                                 "updating": updating,
                                                 "force": force})


async def delete_testdata(name: str, scope: str = "global",
                          environment: str = "") -> dict:
    return await _call("DELETE", f"/testdata/{name}",
                       params={"scope": scope, "environment": environment})


async def rename_testdata(name: str, new_name: str, scope: str = "global",
                          environment: str = "", apply: bool = False) -> dict:
    """Rename a value and every ${reference} to it. apply=False previews."""
    return await _call("POST", f"/testdata/{name}/rename",
                       json={"new_name": new_name, "scope": scope,
                             "environment": environment, "apply": apply})


async def testdata(environment: str = "") -> dict:
    return await _call("GET", "/testdata", params={"environment": environment})


async def upload_source(filename: str, data: bytes, uploaded_by: str = "") -> dict:
    return await _call("POST", "/sources/upload",
                 files={"file": (filename, data)},
                 params={"uploaded_by": uploaded_by})


async def draft_from_prompt(prompt: str, platform: str, max_testcases: int = 50,
                      provider: str = "", model: str = "", redraft: bool = False,
                      attachments: list[dict] | None = None,
                      extend_flow: str = "") -> dict:
    return await _call("POST", "/sources/prompt",
                 json={"prompt": prompt, "platform": platform,
                       "max_testcases": max_testcases,
                       "provider": provider, "model": model, "redraft": redraft,
                       "attachments": attachments or [],
                       "extend_flow": extend_flow or ""})


async def list_sources() -> list[dict]:
    return (await _call("GET", "/sources"))["sources"]


async def get_source(source_id: str) -> dict:
    return await _call("GET", f"/sources/{source_id}")


async def delete_source(source_id: str) -> dict:
    return await _call("DELETE", f"/sources/{source_id}")


# ── Generation ───────────────────────────────────────────────────────────────
async def generate(source_id: str, testcase_ids: list[str], platform: str, *,
             flow_name: str = "", persist: bool = False, out_path: str = "",
             overwrite: bool = False, variable_bindings: dict | None = None) -> dict:
    return await _call("POST", "/generate", json={
        "source_id": source_id, "testcase_ids": testcase_ids, "platform": platform,
        "flow_name": flow_name, "persist": persist, "out_path": out_path,
        "overwrite": overwrite, "variable_bindings": variable_bindings or {},
    })


# ── Projects (saved flows) ───────────────────────────────────────────────────
async def list_projects(platform: str = "") -> list[dict]:
    r = await _call("GET", "/projects", params={"platform": platform} if platform else None)
    return r.get("projects", r if isinstance(r, list) else [])


async def get_project(name: str) -> dict:
    return await _call("GET", f"/projects/{name}")


async def save_project(name: str, steps: list[str], platform: str = "") -> dict:
    """Create or update — the UI should not have to know which."""
    body = {"steps": steps, "platform": platform}
    try:
        return await _call("PUT", f"/projects/{name}", json=body)
    except ApiError as e:
        if e.status != 404:
            raise
        return await _call("POST", "/projects",
                           json={"name": name, "steps": steps, "platform": platform})


# ── Execution ────────────────────────────────────────────────────────────────
async def check_flow_name(name: str) -> dict:
    """What a typed test-case name will be saved as."""
    return await _call("GET", f"/projects/check-name/{name}")


async def delete_project(name: str) -> dict:
    """Delete a test case. Elements and test data are untouched."""
    return await _call("DELETE", f"/projects/{name}")


async def rename_project(name: str, new_name: str, apply: bool = False) -> dict:
    """Preview a rename (apply=False) or carry it out, references included."""
    return await _call("POST", f"/projects/{name}/rename",
                       json={"new_name": new_name, "apply": apply})


async def step_groups(platform: str = "") -> list[dict]:
    r = await _call("GET", "/stepgroups", params={"platform": platform})
    return r.get("groups", [])


async def save_step_group(name: str, steps: list[str], platform: str = "",
                          overwrite: bool = False) -> dict:
    return await _call("POST", "/stepgroups",
                       json={"name": name, "steps": steps,
                             "platform": platform, "overwrite": overwrite})


async def clone_step_group(name: str, new_name: str = "") -> dict:
    return await _call("POST", f"/stepgroups/{name}/clone", json={"new_name": new_name})


async def edit_step_group(name: str, steps: list[str]) -> dict:
    return await _call("PUT", f"/stepgroups/{name}", json={"steps": steps})


async def rename_step_group(name: str, new_name: str) -> dict:
    return await _call("POST", f"/stepgroups/{name}/rename", json={"new_name": new_name})


async def delete_step_group(name: str) -> dict:
    return await _call("DELETE", f"/stepgroups/{name}")


async def review_steps(steps: list[str], platform: str = "website",
                       environment: str = "", flow_name: str = "") -> dict:
    """Deterministic review of a test — findings with ready fixes."""
    return await _call("POST", "/review", json={"steps": steps, "platform": platform,
                                                "environment": environment,
                                                "flow_name": flow_name})


async def review_assist(kind: str, steps: list[str], platform: str = "website",
                        step_index: int = 0, flow_name: str = "") -> dict:
    """Ask for proposals on a finding no rule can fix."""
    return await _call("POST", "/review/assist",
                       json={"kind": kind, "steps": steps, "platform": platform,
                             "step_index": step_index, "flow_name": flow_name})


async def run(project: str, platform: str, *, headless: bool = True,
        device_name: str = "", browser: str = "",
        parameters: dict | None = None,
        secret_parameters: list[str] | None = None,
        browser_permissions: str = "",
        stop_on_failure: bool = True,
        screenshot_mode: str = "all",
        screenshot_context: int = 5) -> dict:
    return await _call("POST", "/tests/run", json={
        "project": project, "platform": platform, "headless": headless,
        "device_name": device_name, "browser": browser,
        "parameters": parameters or {},
        "secret_parameters": secret_parameters or [],
        "browser_permissions": browser_permissions,
        "stop_on_failure": stop_on_failure,
        # all | key | failure | off — see reporting/step_capture.py
        "screenshot_mode": screenshot_mode,
        "screenshot_context": screenshot_context,
    })


async def last_setup(flow: str) -> dict:
    """How this flow was launched last time — {} if never."""
    return await _call("GET", f"/tests/last-setup/{flow}")


async def run_result(run_id: str) -> dict:
    return await _call("GET", f"/tests/results/{run_id}")


async def run_history(limit: int = 50) -> list[dict]:
    """Past runs with their summaries. /tests/results returns bare filenames."""
    r = await _call("GET", "/tests/history", params={"limit": limit})
    return r.get("runs", [])


# ── Server ───────────────────────────────────────────────────────────────────
async def system_info() -> dict:
    """Where this process serves, and whether a run is in flight."""
    return await _call("GET", "/system/info")


async def restart_server(force: bool = False) -> dict:
    """
    Ask the server to replace itself.

    Returns as soon as the restart is SCHEDULED — the response has to arrive
    while the process can still send it. The caller then polls /health.
    """
    return await _call("POST", "/system/restart", json={"force": force})

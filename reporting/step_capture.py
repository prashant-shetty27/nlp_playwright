"""
reporting/step_capture.py — a screenshot per step, without filling the disk.

Why this is a policy and not one line of Playwright
---------------------------------------------------
A screenshot at every step is what makes a failed run debuggable: you see the
page as the step saw it, rather than reasoning backwards from an error message.
The cost is storage, and at the wrong settings it is ruinous — the screenshots
already on disk average 1.79 MB each as full-page PNGs, so a 20-step run would
write 36 MB and fifty runs a day would be 54 GB a month.

Four decisions bring that to roughly 4 MB a run, with no loss of usefulness:

  JPEG, not PNG          ~10x smaller for a screenshot of a page. PNG is for
                         images you will edit; this one is looked at once.
  viewport, not full page A full-page shot of a long results page is several
                         screens of content nobody scrolls to in a report.
  a mode, not always     see MODES below — most runs pass, and nobody opens the
                         screenshots of a run that passed.
  a per-run cap          a 500-step flow must not write 500 files, and being
                         told it was capped beats discovering the gap later.

MODES
-----
  all       every step. What Testsigma does. Best for debugging, most storage.
  key       verifications, assertions, and any step that changed the page. The
            useful middle: it captures what a test is CHECKING and every new
            screen it reaches, and skips the fifth click in a row on one form.
  failure   keep only the failing step and the `context` steps before it. Every
            step is still captured — you cannot know a step mattered until the
            next one fails — but only the tail is kept, and the rest is deleted
            when the run ends. Cheapest, and it keeps exactly the frames anyone
            actually looks at.
  off       none.
"""
from __future__ import annotations

import logging
import os
import re
from collections import deque

logger = logging.getLogger(__name__)

MODES = ("all", "key", "failure", "off")

#: Quality 60 is where a page screenshot stops shrinking usefully; below it text
#: starts to smear, which is the one thing a debugging screenshot must keep.
JPEG_QUALITY = int(os.getenv("STEP_SHOT_QUALITY", "60"))

#: Files per run. A runaway flow should not be able to fill a disk.
MAX_PER_RUN = int(os.getenv("STEP_SHOT_MAX", "400"))

#: Steps that are worth a frame in `key` mode even when the page has not moved.
_CHECKS = re.compile(r"^\s*(verify|assert|store|extract|wait until)\b", re.I)


# ── Element highlight ────────────────────────────────────────────────────────
_HL_JS = """
([sel, label, failed, limit]) => {
  const isXPath = /^[(\/.]/.test(sel);
  let nodes = [];
  try {
    if (isXPath) {
      const r = document.evaluate(sel, document, null,
                                  XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
      for (let i = 0; i < r.snapshotLength && i < limit; i++) nodes.push(r.snapshotItem(i));
    } else {
      nodes = Array.from(document.querySelectorAll(sel)).slice(0, limit);
    }
  } catch (e) { nodes = []; }
  nodes = nodes.filter(n => n && n.nodeType === 1);
  const colour = failed ? '#DC2626' : '#F59E0B';
  const badge = document.createElement('div');
  badge.className = '__ca_hl_badge';
  badge.textContent = (nodes.length ? '' : 'NOT FOUND: ') + label
                      + (nodes.length > 1 ? '  (' + nodes.length + ' matches)' : '');
  badge.style.cssText = 'position:fixed;top:6px;left:6px;z-index:2147483647;'
    + 'background:' + (nodes.length ? colour : '#DC2626') + ';color:#fff;'
    + 'font:600 12px/1.3 -apple-system,Segoe UI,Roboto,sans-serif;'
    + 'padding:3px 8px;border-radius:4px;box-shadow:0 1px 4px rgba(0,0,0,.35);'
    + 'max-width:90vw;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'
    + 'pointer-events:none';
  document.documentElement.appendChild(badge);
  nodes.forEach(n => {
    n.setAttribute('data-ca-hl', '1');
    n.__caOutline = n.style.outline; n.__caOffset = n.style.outlineOffset;
    n.style.outline = '3px solid ' + colour;
    n.style.outlineOffset = '1px';
  });
  return nodes.length;
}
"""
_UNHL_JS = """
() => {
  document.querySelectorAll('.__ca_hl_badge').forEach(b => b.remove());
  document.querySelectorAll('[data-ca-hl]').forEach(n => {
    n.style.outline = n.__caOutline || ''; n.style.outlineOffset = n.__caOffset || '';
    n.removeAttribute('data-ca-hl');
  });
}
"""


def _step_locator(step: str) -> tuple[str, str]:
    """(locator name, resolved selector) for the element `step` acts on, or ("", "")."""
    try:
        from nlp.fields import TARGET_IS_LOCATOR
        from nlp.parser import parse_step

        cmd = parse_step(step)
        if cmd.type not in TARGET_IS_LOCATOR:
            return "", ""
        name = str(getattr(cmd, "target", "") or "").strip()
        if not name:
            return "", ""
        from execution.action_service import _resolve_to_selector

        return name, str(_resolve_to_selector(name) or "")
    except Exception:  # noqa: BLE001 — a picture must never fail a step
        return "", ""


def _highlight(page, step: str, failed: bool) -> bool:
    """Outline the step's element(s) and drop a badge. True when anything was added."""
    name, selector = _step_locator(step)
    if not name or not selector:
        return False
    try:
        page.evaluate(_HL_JS, [selector, name, bool(failed), 25])
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug("Highlight skipped for '%s': %s", name, e)
        return False


def _unhighlight(page) -> None:
    try:
        page.evaluate(_UNHL_JS)
    except Exception:  # noqa: BLE001
        pass


class StepCapture:
    """
    Screenshots for one run, taken and kept according to `mode`.

    Never raises into the run: a screenshot is a diagnostic, and failing to take
    one must not turn a passing step into a failing one.
    """

    def __init__(self, run_id: str, mode: str = "all", context: int = 5) -> None:
        from config.settings import SCREENSHOTS_DIR

        self.mode = mode if mode in MODES else "all"
        #: How many steps BEFORE a failure to keep, in `failure` mode.
        self.context = max(0, int(context or 0))
        self.root = SCREENSHOTS_DIR
        self.rel_dir = os.path.join("runs", run_id)
        self.dir = os.path.join(SCREENSHOTS_DIR, self.rel_dir)
        #: Frames KEPT, which is what the cap limits. Counting frames taken
        #: would make `failure` mode stop capturing on a long run long before
        #: the failure it exists to photograph — the ring buffer already bounds
        #: what that mode keeps to context + 1.
        self.kept_count = 0
        self.capped = False
        self._last_url = ""
        #: (entry, path) for shots not yet known to be worth keeping. Only used
        #: by `failure` mode; holds context + 1 so the failing step's own frame
        #: is in the window too.
        self._pending: deque = deque(maxlen=self.context + 1)
        self._kept: list[str] = []

    # ── the one call the run loop makes ──────────────────────────────────────
    def after_step(self, page, entry: dict, step: str,
                   report_row: dict | None = None) -> None:
        """
        Record this step's frame, if the mode wants it.

        `entry` is the step's dict in the run log and `report_row` its dict in
        the saved report. BOTH are needed, and this is not redundancy: in
        `failure` mode a frame is not known to be wanted until a LATER step
        fails, by which time both rows have already been written. Holding
        references lets the promotion reach back and fill them in — without it
        the context frames existed on disk with nothing in the report pointing
        at them.
        """
        if self.mode == "off" or page is None:
            return
        failed = entry.get("status") == "failed"
        try:
            if self.mode == "failure":
                self._failure_mode(page, entry, report_row, failed)
                return
            if self.mode == "key" and not failed and not self._is_key(page, step):
                return
            path = self._shoot(page, entry.get("line", self.kept_count + 1), step=step,
                               failed=entry.get("status") == "failed")
            if path:
                self._keep(entry, report_row, path)
        except Exception as e:  # noqa: BLE001 — never fail a step over a picture
            logger.debug("Step screenshot skipped: %s", e)

    def finish(self) -> None:
        """Delete anything the run turned out not to need."""
        for _entry, _row, path in self._pending:
            self._discard(self._abs(path))
        self._pending.clear()
        # A run that kept nothing should not leave an empty directory behind for
        # the pruner and the reports page to wonder about.
        try:
            if not self._kept and os.path.isdir(self.dir):
                os.rmdir(self.dir)
        except OSError:
            pass

    # ── modes ───────────────────────────────────────────────────────────────
    def _failure_mode(self, page, entry: dict, report_row: dict | None,
                      failed: bool) -> None:
        """
        Keep a rolling window, and commit it only when something breaks.

        The window has to be captured as the run goes: by the time a step fails,
        the pages its predecessors were looking at are gone. So every step is
        photographed and all but the last few are thrown away.
        """
        path = self._shoot(page, entry.get("line", self.kept_count + 1), step=step,
                               failed=entry.get("status") == "failed")
        if not path:
            return
        # Anything falling out of the window is now certainly not wanted.
        if len(self._pending) == self._pending.maxlen and self._pending:
            _old_entry, _old_row, old_path = self._pending[0]
            self._discard(self._abs(old_path))
        self._pending.append((entry, report_row, path))
        if failed:
            for kept_entry, kept_row, kept_path in self._pending:
                self._keep(kept_entry, kept_row, kept_path)
            self._pending.clear()

    def _is_key(self, page, step: str) -> bool:
        """A check, or a step that landed somewhere new."""
        if _CHECKS.match(step or ""):
            return True
        try:
            url = page.url
        except Exception:  # noqa: BLE001
            return False
        if url and url != self._last_url:
            self._last_url = url
            return True
        return False

    # ── files ───────────────────────────────────────────────────────────────
    def _shoot(self, page, index: int, step: str = "", failed: bool = False) -> str:
        # `failure` mode is bounded by its ring buffer (context + 1 frames), so
        # the cap does not apply to it — applying it would silence exactly the
        # frames it exists to keep.
        if self.mode != "failure" and self.kept_count >= MAX_PER_RUN:
            if not self.capped:
                self.capped = True
                logger.warning("📸 Screenshot cap reached (%d) — later steps have "
                               "no frame.", MAX_PER_RUN)
            return ""
        os.makedirs(self.dir, exist_ok=True)
        path = os.path.join(self.dir, f"step_{int(index):04d}.jpg")
        # The ACTIVE page, not the one the run started with: a step that switched
        # tab must photograph the tab it is now on.
        target = page
        try:
            from execution.action_service import _TEST_SESSION

            target = _TEST_SESSION.active_page or page
        except Exception:  # noqa: BLE001
            pass
        # The element the step acted on is outlined in the frame, with its name
        # on a badge, so nobody has to work out from a bare screenshot WHICH
        # button was clicked or WHICH text was verified. Removed again right
        # after the frame, so the page is untouched for the next step.
        marked = _highlight(target, step, failed)
        try:
            target.screenshot(path=path, type="jpeg", quality=JPEG_QUALITY)
        finally:
            if marked:
                _unhighlight(target)
        # Relative to data/screenshots. The report stores this, so a run stays
        # readable if the data directory is moved or copied to another machine,
        # and the UI can serve it from one static mount without path juggling.
        return os.path.join(self.rel_dir, os.path.basename(path))

    def _keep(self, entry: dict, report_row: dict | None, rel_path: str) -> None:
        """Mark a frame as wanted — the only place kept_count moves."""
        entry["screenshot"] = rel_path
        if report_row is not None:
            report_row["screenshot"] = rel_path
        self._kept.append(rel_path)
        self.kept_count += 1

    def _abs(self, rel_path: str) -> str:
        return os.path.join(self.root, rel_path)

    @staticmethod
    def _discard(path: str) -> None:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    # ── for the report ──────────────────────────────────────────────────────
    def summary(self) -> dict:
        return {"mode": self.mode, "context": self.context,
                "kept": len(self._kept), "capped": self.capped,
                "dir": self.rel_dir if self._kept else ""}

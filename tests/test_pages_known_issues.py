"""core/pages.py and core/known_issues.py."""
import json

from core import known_issues as K
from core import pages as P


def test_page_detect_prefers_most_specific(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "PATH", str(tmp_path / "pages.json"))
    P._cache.update(mtime=0.0, data={})
    (tmp_path / "pages.json").write_text(json.dumps({"pages": {
        "prp": {"detect": {"url": ["/jdmart/[^/]+/[^/]+/jdm-\\d+"]}, "popups": ["gvs_skip"], "landmark": ["prp_1st"]},
        "pdp": {"detect": {"url": ["/jdmart/.*/pid-\\d+"]}, "popups": ["gbp_skip"], "landmark": ["pdp_img"]},
        "home": {"detect": {"url": ["^https?://[^/]*justdial\\.com/jdmart/?$"]}}}}))
    assert P.detect("https://www.justdial.com/jdmart/Mumbai/Tyre/jdm-11?x=1") == "prp"
    assert P.detect("https://www.justdial.com/jdmart/Thane/Bearing/pid-801/022") == "pdp"
    assert P.detect("https://www.justdial.com/jdmart") == "home"
    assert P.detect("https://example.com/") == ""
    assert P.popups("pdp") == ["gbp_skip"] and P.landmarks("prp") == ["prp_1st"]


def test_known_issue_matching(tmp_path, monkeypatch):
    monkeypatch.setattr(K, "PATH", str(tmp_path / "ki.json"))
    K.save([
        {"id": "KI-001", "kind": "product-defect", "title": "no alert",
         "match": {"test_case": "^GJDT22788_RC", "error": "couldn.{0,3}t find any results"}},
        {"id": "KI-002", "kind": "site-change", "title": "old", "expires": "2000-01-01",
         "match": {"test_case": "^X"}},
        {"id": "KI-003", "kind": "flaky", "title": "closed", "closed": True, "match": {"test_case": ".*"}},
    ])
    items = [
        {"test_case": "GJDT22788_RC04_x", "status": "failed",
         "first_failure": "line 18: verify ${bot_reply} matches — ${bot_reply} is 'We couldn\\'t find any results for \"rat glue pad\"'"},
        {"test_case": "GJDT22788_RC04_x", "status": "failed", "first_failure": "line 3: click a — 'a' not visible"},
        {"test_case": "X_case", "status": "failed", "first_failure": "line 1: open — boom"},
        {"test_case": "Y", "status": "passed"},
    ]
    assert K.annotate(items) == 1
    assert items[0]["known"]["id"] == "KI-001"
    assert "known" not in items[1] and "known" not in items[2] and "known" not in items[3]


def test_cluster_groups_by_cause():
    from core import run_insights as R
    items = [
        {"status": "failed", "test_case": "RC04", "first_failure": "line 1: verify x — ${bot_reply} is 'We couldn\\'t find any results for \"a\"' — expected"},
        {"status": "failed", "test_case": "RC05", "first_failure": "line 1: verify x — ${bot_reply} is \"Humein 'glue' ke liye koi results nahi mile\" — expected"},
        {"status": "failed", "test_case": "NC03", "first_failure": "line 2: call g — 'b2b_assist_login_gate' (//div) was still visible after 15000ms"},
        {"status": "failed", "test_case": "PRP1", "first_failure": "line 3: call g — 'prp_eden_card' did not appear after 60 swipe(s)"},
        {"status": "failed", "test_case": "API", "first_failure": "line 4: call g — HTTPConnectionPool(host='192.168.8.27', port=8082): Max retries exceeded"},
        {"status": "failed", "test_case": "K", "first_failure": "line 5: x — y", "known": {"id": "KI-1", "title": "t", "kind": "data"}},
        {"status": "passed", "test_case": "ok"},
    ]
    cl = R.cluster(items)
    by = {c["title"]: c for c in cl}
    assert any(c["count"] == 2 and c["kind"] == "product-defect" for c in cl)          # both reply variants together
    assert any("login gate" in c["title"] and c["kind"] == "test-problem" for c in cl)
    assert any("prp_eden_card" in c["title"] for c in cl)
    assert any(c["kind"] == "environment" and "192.168.8.27" in c["title"] for c in cl)
    assert any(c["title"].startswith("Known:") and c["kind"] == "data" for c in cl)
    assert sum(c["count"] for c in cl) == 6

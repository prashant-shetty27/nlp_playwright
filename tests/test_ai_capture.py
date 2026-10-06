"""locators/ai_capture.py — record an unrecorded element from the live page."""
import json

from locators import ai_capture as A


class _Loc:
    def __init__(self, n, visible=True):
        self._n, self._vis = n, visible
        self.first = self

    def count(self):
        return self._n

    def nth(self, k):
        return self

    def is_visible(self, timeout=0):
        return self._vis

    def evaluate(self, js):
        return {"tagName": "div", "className": "photos_tab_imgcontainer"}


class _Page:
    """Fake page: one snapshot, and a locator that 'matches' only the good xpath."""
    def __init__(self):
        self.nodes = [
            {"i": 0, "tag": "h2", "cls": ["dtlpg_tabh1"], "attrs": {}, "text": "Photos", "kids": 0,
             "box": [0, 100, 300, 30], "path": "/html/body/div[1]/h2[1]"},
            {"i": 1, "tag": "div", "cls": ["photos_tab_imgcontainer"], "attrs": {}, "text": "", "kids": 2,
             "box": [0, 140, 200, 200], "path": "/html/body/div[1]/div[1]"},
            {"i": 2, "tag": "div", "cls": ["glyimg__img3dicn"], "attrs": {}, "text": "", "kids": 0,
             "box": [170, 144, 23, 23], "path": "/html/body/div[1]/div[1]/div[1]"},
        ]

    def evaluate(self, js, arg=None):
        return self.nodes

    def locator(self, sel):
        return _Loc(1) if "img3dicn" in sel else _Loc(0)


def test_capture_records_validated_xpath(tmp_path, monkeypatch):
    path = tmp_path / "locators_manual.json"
    path.write_text("{}")
    from config import settings
    monkeypatch.setattr(settings, "MANUAL_LOCATORS_FILE", str(path))

    class Prov:
        def complete_structured(self, *, system, user, schema):
            assert "pdp_photo_tile_360_icon" in user and "glyimg__img3dicn" in user
            class C:  # noqa: D401
                data = {"found": True, "xpath": "//div[contains(@class,'glyimg__img3dicn')]",
                        "node_index": 2, "reason": "the 23px 360 badge inside the photo tile"}
            return C()

    import ai_flow_builder.llm as llm
    monkeypatch.setattr(llm, "get_provider", lambda *a, **k: Prov())
    A.enable("website", "tc_bwn1471_01")
    A.set_step("verify element pdp_photo_tile_360_icon is visible")
    got = A.capture(_Page(), "pdp_photo_tile_360_icon")
    A.disable()
    assert got and got[0] == "//div[contains(@class,'glyimg__img3dicn')]"
    saved = json.loads(path.read_text())
    grp = saved["ai_bwn1471_website"]
    assert grp["_platform"] == "website"
    assert grp["pdp_photo_tile_360_icon"]["custom_xpath"].endswith("img3dicn')]")
    assert grp["pdp_photo_tile_360_icon"]["_recorded_from"]["test_case"] == "tc_bwn1471_01"


def test_capture_gives_up_when_model_says_not_on_page(tmp_path, monkeypatch):
    class Prov:
        def complete_structured(self, *, system, user, schema):
            class C:
                data = {"found": False, "xpath": "", "node_index": -1, "reason": "gallery not open"}
            return C()

    import ai_flow_builder.llm as llm
    monkeypatch.setattr(llm, "get_provider", lambda *a, **k: Prov())
    A.enable("website", "x")
    assert A.capture(_Page(), "gallery_back_button") is None
    assert A.capture(_Page(), "gallery_back_button") is None      # not asked twice
    A.disable()
    assert not A.active()


def test_group_name():
    assert A._group_for("tc_bwn1471_01", "website") == "ai_bwn1471_website"
    assert A._group_for("PDP_Need_Assistance_Banner", "mobilesite") == "ai_pdp_need_assistance_banner_mobilesite"

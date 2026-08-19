import sys
import types

nicegui = types.ModuleType("nicegui")
nicegui.ui = object()
sys.modules.setdefault("nicegui", nicegui)

from ui.pages.platform.test_cases import _locator_detail_index, _selector_from_record


def test_selector_from_record_prefers_custom_xpath_then_selectors() -> None:
    assert _selector_from_record({"custom_xpath": "//button[@id='go']"}) == \
        "//button[@id='go']"
    assert _selector_from_record({
        "selectors": [{"type": "css", "value": "button.primary"}],
    }) == "button.primary"
    assert _selector_from_record(None) == ""


def test_locator_detail_index_flattens_groups() -> None:
    groups = {
        "login_page": {
            "mobile_number_input": {"custom_xpath": "//input[@name='mobile']"},
        },
        "otp_page": {
            "otp_submit_button": {
                "selectors": [{"type": "css", "value": "button.submit"}],
            },
        },
    }

    assert _locator_detail_index(groups) == {
        "mobile_number_input": {
            "group": "login_page",
            "selector": "//input[@name='mobile']",
        },
        "otp_submit_button": {
            "group": "otp_page",
            "selector": "button.submit",
        },
    }

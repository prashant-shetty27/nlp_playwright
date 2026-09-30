"""${otp}: static OTP per platform for test numbers, OTP portal for any other."""
import pytest

from execution import action_service as svc


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TEST_MOBILES", "9000000001,9000000002")
    monkeypatch.setenv("STATIC_OTP_WEB", "111111")
    monkeypatch.setenv("STATIC_OTP_TOUCH", "222222")
    monkeypatch.setattr(svc, "LAST_MOBILE", "")
    monkeypatch.setattr(svc, "RUN_PLATFORM", "")
    svc.RUNTIME_VARIABLES.pop("otp", None)
    yield
    svc.RUNTIME_VARIABLES.pop("otp", None)


def test_test_number_on_website_uses_web_otp():
    svc.RUN_PLATFORM = "website"
    svc.note_typed_value("9000000001")
    assert svc.resolve_otp(None) == "111111"
    assert svc.RUNTIME_VARIABLES["otp"] == "111111"


def test_test_number_on_mobile_site_uses_touch_otp():
    svc.RUN_PLATFORM = "mobilesite"
    svc.note_typed_value("9000000002")
    assert svc.resolve_otp(None) == "222222"


def test_other_number_fetches_from_portal(monkeypatch):
    calls = []

    def fake_fetch(page, mobile, name, *a, **k):
        calls.append((mobile, name))
        svc.RUNTIME_VARIABLES[name] = "999999"

    monkeypatch.setattr(svc, "fetch_otp_from_portal", fake_fetch)
    svc.RUN_PLATFORM = "mobilesite"
    svc.note_typed_value("9876543210")
    assert svc.resolve_otp(None) == "999999"
    assert calls == [("9876543210", "otp")]


def test_non_mobile_text_is_ignored():
    svc.note_typed_value("hello")
    svc.note_typed_value("12345")
    assert svc.LAST_MOBILE == ""


def test_missing_static_otp_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("STATIC_OTP_WEB")
    svc.RUN_PLATFORM = "website"
    with pytest.raises(Exception, match="STATIC_OTP_WEB"):
        svc.resolve_otp(None)

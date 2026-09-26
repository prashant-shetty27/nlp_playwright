"""GJDT-22686 priority buckets: DS > search city (incl. Mumbai region) > priced."""
import pytest

from execution.action_service import _rp_check_order, _rp_has_video, _rp_same_city


def item(name, city, ds, price):
    return {"name": name, "city": city, "ds": ds, "price": price}


def test_mumbai_region_counts_as_same_city():
    assert _rp_same_city("Thane", "Mumbai")
    assert _rp_same_city("navi mumbai", "mumbai")
    assert not _rp_same_city("Delhi", "Mumbai")


def test_video_field_marks_ds():
    assert _rp_has_video({"ai_video": "x.mp4"})
    assert not _rp_has_video({"ai_video": ""})


def test_correct_order_passes():
    _rp_check_order([item("a", "Delhi", True, True), item("b", "Mumbai", False, True),
                     item("c", "Mumbai", False, False), item("d", "Pune", False, True)],
                    "Mumbai", "page")


def test_priced_after_unpriced_in_same_bucket_fails():
    with pytest.raises(Exception, match="(?i)priority"):
        _rp_check_order([item("a", "Mumbai", False, False), item("b", "Mumbai", False, True)],
                        "Mumbai", "page")


def test_empty_list_fails():
    with pytest.raises(Exception):
        _rp_check_order([], "Mumbai", "page")

import pytest

import core.run_configs as rc


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "PATH", str(tmp_path / "rc.json"))


def test_personal_shared_and_module_scope():
    rc.save("Live Samsung", "mobilesite", "prashant", {"site_env": "live", "junk": 1})
    rc.save("Private", "mobilesite", "manisha", {})
    team = rc.save("Team", "mobilesite", "manisha", {"site_env": "prot3"}, shared=True)
    names = [c["name"] for c in rc.visible("mobilesite", "prashant")]
    assert names == ["Live Samsung", "Team"]
    assert rc.visible("website", "prashant") == []
    assert "junk" not in rc.visible("mobilesite", "prashant")[0]["settings"]
    with pytest.raises(rc.RunConfigError):
        rc.save("x", "mobilesite", "prashant", {}, cid=team["id"])
    with pytest.raises(rc.RunConfigError):
        rc.delete(team["id"], "prashant")
    rc.delete(team["id"], "prashant", is_admin=True)
    assert [c["name"] for c in rc.visible("mobilesite", "prashant")] == ["Live Samsung"]


def test_same_name_updates_instead_of_duplicating():
    a = rc.save("Mine", "website", "p", {"headless": True})
    b = rc.save("mine", "website", "p", {"headless": False})
    assert a["id"] == b["id"] and len(rc.visible("website", "p")) == 1
    assert rc.visible("website", "p")[0]["settings"]["headless"] is False

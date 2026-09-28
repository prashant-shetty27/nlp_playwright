"""core/folders — test-case folders are labels; nothing touches flows/."""
import pytest

import core.folders as F


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "FOLDERS_FILE", str(tmp_path / "folders.json"))


def test_create_makes_parents_and_reuses_spelling():
    assert F.create("Prashant/B2B/PDP") == "Prashant/B2B/PDP"
    assert F.create("prashant/Core/NCT") == "Prashant/Core/NCT"
    assert F.get()["folders"] == ["Prashant", "Prashant/B2B", "Prashant/B2B/PDP",
                                  "Prashant/Core", "Prashant/Core/NCT"]


def test_rename_moves_children_and_tests():
    F.assign(["A"], "Prashant/B2B/PDP")
    assert F.rename("Prashant/B2B", "B2B Touch") == "Prashant/B2B Touch"
    assert F.folder_of("A") == "Prashant/B2B Touch/PDP"


def test_delete_keeps_tests_in_parent():
    F.assign(["A"], "Prashant/B2B/PDP")
    res = F.delete("Prashant/B2B")
    assert res["to"] == "Prashant" and F.folder_of("A") == "Prashant"
    F.delete("Prashant")
    assert F.folder_of("A") == ""


def test_test_case_rename_and_delete_follow():
    F.assign(["A"], "X")
    F.on_rename("A", "B")
    assert F.folder_of("B") == "X" and F.folder_of("A") == ""
    F.on_delete("B")
    assert F.folder_of("B") == ""


@pytest.mark.parametrize("bad", ["", "a/../b", "/", "x" * 41])
def test_bad_names(bad):
    with pytest.raises(F.FolderError):
        F.create(bad)

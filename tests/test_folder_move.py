"""Moving a folder takes its sub-folders and test cases with it."""
import json

import pytest

from core import folders


@pytest.fixture(autouse=True)
def _file(tmp_path, monkeypatch):
    f = tmp_path / "f.json"
    f.write_text(json.dumps({"folders": ["Catalogue", "Catalogue/Sub", "Prashant", "Prashant/B2B"],
                             "assign": {"t1": "Catalogue", "t2": "Catalogue/Sub", "t3": "Prashant"}}))
    monkeypatch.setattr(folders, "FOLDERS_FILE", str(f))


def test_move_into_other_folder():
    assert folders.move("Catalogue", "Prashant") == "Prashant/Catalogue"
    d = folders.get()
    assert "Prashant/Catalogue/Sub" in d["folders"] and "Catalogue" not in d["folders"]
    assert d["assign"] == {"t1": "Prashant/Catalogue", "t2": "Prashant/Catalogue/Sub", "t3": "Prashant"}


def test_move_back_to_top_level():
    folders.move("Catalogue", "Prashant")
    assert folders.move("Prashant/Catalogue", "") == "Catalogue"


def test_cannot_move_into_itself_or_onto_existing():
    with pytest.raises(folders.FolderError, match="inside itself"):
        folders.move("Catalogue", "Catalogue/Sub")
    folders.create("Prashant/Catalogue")
    with pytest.raises(folders.FolderError, match="already exists"):
        folders.move("Catalogue", "Prashant")

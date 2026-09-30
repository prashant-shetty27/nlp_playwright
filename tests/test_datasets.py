"""Data sets: upload parsing (CSV / Excel), names, rows, errors."""
import io

import openpyxl
import pytest

from core import datasets


@pytest.fixture(autouse=True)
def _tmpdir(tmp_path, monkeypatch):
    monkeypatch.setattr(datasets, "DIR", str(tmp_path))


def test_csv_upload_columns_and_rows():
    t = datasets.parse_upload("City List.csv", b"City,Mobile Number\nMumbai,9000000001\nPune,9000000002\n")
    assert list(t) == ["city_list"]
    h, rows = t["city_list"]
    datasets.save("city_list", h, rows, user="prashant")
    got = datasets.rows("city_list")
    assert got[1] == {"city": "Pune", "mobile_number": "9000000002", "row_number": "2"}
    info = datasets.info("city_list")
    assert info["columns"] == ["city", "mobile_number"] and info["row_count"] == 2
    assert info["created_by"] == "prashant"


def test_excel_numbers_stay_text_and_sheets_split():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Mumbai"
    ws.append(["mobile", "price"])
    ws.append([9000000001, 120.0])
    ws2 = wb.create_sheet("Pune")
    ws2.append(["mobile"])
    ws2.append([9000000002])
    buf = io.BytesIO()
    wb.save(buf)
    t = datasets.parse_upload("login.xlsx", buf.getvalue())
    assert set(t) == {"login_mumbai", "login_pune"}
    assert t["login_mumbai"][1] == [["9000000001", "120"]]


@pytest.mark.parametrize("content,msg", [
    (b"", "empty"),
    (b"city,,x\n1,2,3\n", "heading"),
    (b"City,city\n1,2\n", "same name"),
])
def test_bad_tables(content, msg):
    with pytest.raises(datasets.DatasetError, match=msg):
        datasets.parse_upload("x.csv", content)


def test_unknown_name_lists_known():
    datasets.save("a", ["x"], [["1"]])
    with pytest.raises(datasets.DatasetError, match="Uploaded: a"):
        datasets.rows("nope")

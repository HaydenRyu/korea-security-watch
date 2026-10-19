import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ucdp_month as u  # noqa: E402

HTML = ('<a href="https://ucdp.uu.se/downloads/candidateged/GEDEvent_v26_0_8.csv">x</a>'
        '<a href="https://ucdp.uu.se/downloads/candidateged/GEDEvent_v26_01_26_06.csv">cum</a>'
        '<a href="https://ucdp.uu.se/downloads/candidateged/GEDEvent_v26_0_7.csv">x</a>'
        '<a href="https://ucdp.uu.se/downloads/candidateged/GEDEvent_v25_0_12.csv">x</a>')
CSV = ("country,region,date_start,best\n"
       "Ukraine,Europe,2026-08-03,10\nRussia (Soviet Union),Europe,2026-08-04 00:00:00.000,5\n"
       "Sudan,Africa,2026-08-10,7\nSudan,Africa,2026-07-30,100\nMali,Africa,2026-08-01,bad\n")


def test_versions_sorted_and_cumulative_ignored():
    v = u.list_versions(HTML)
    assert [(y, m) for y, m, _ in v] == [(25, 12), (26, 7), (26, 8)]
    assert u.month_of(26, 8) == "2026-08"


def test_month_totals_merges_russia_ukraine_and_filters_month():
    t = u.month_totals(CSV, "2026-08")
    assert t["russia_ukraine"]["best"] == 15 and t["russia_ukraine"]["events"] == 2
    assert t["Sudan"]["best"] == 7 and t["Mali"]["best"] == 0


def test_build_rows_and_prev():
    cur = u.month_totals(CSV, "2026-08")
    prev = {"Sudan": {"best": 3, "events": 1}}
    doc = u.build(cur, prev, "2026-08", "2026-07", "v26_0_8", "https://x", "now")
    rows = {r["key"]: r for r in doc["rows"]}
    assert doc["rows"][0]["key"] == "russia_ukraine" and rows["russia_ukraine"]["name"] == "러-우 전쟁"
    assert rows["Sudan"]["prev_best"] == 3 and rows["russia_ukraine"]["prev_best"] == 0
    assert u.build(cur, None, "2026-08", None, "v", "u", "n")["rows"][0]["prev_best"] is None
    assert "CC BY 4.0" in doc["license"]

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import obs_link as o  # noqa: E402


def pt(lat, lon, t, north=False, border=True, **kw):
    return {"lat": lat, "lon": lon, "t": t, "frp": 2.0, "sat": "NOAA20", "mdl_km": 1.0, "border": border,
            "north": north, "kind": "wildfire"} | kw


# 10/28 실측(DMZ 남측 한 지점, 3회 통과 7점) 축약 + 같은 창의 북측 농경지 1점
REAL = [
    pt(38.164, 126.973, "2026-10-28T03:47:00+00:00", sat="SNPP", mdl_km=0.9),
    pt(38.171, 126.975, "2026-10-28T03:47:00+00:00", sat="SNPP", mdl_km=0.9, frp=4.68),
    pt(38.167, 126.972, "2026-10-28T04:29:00+00:00", mdl_km=0.7, frp=7.33),
    pt(38.166, 126.977, "2026-10-28T05:06:00+00:00", sat="NOAA21", mdl_km=1.1, frp=22.46),
    pt(38.161, 126.975, "2026-10-28T06:50:00+00:00", sat="MODIS", mdl_km=1.2, mil="군사 시설(이름 없음)(OSM 750m 이내)"),
    pt(37.917, 126.547, "2026-10-28T04:29:00+00:00", north=True, mdl_km=10.8, kind="farm"),
    pt(36.002, 129.399, "2026-10-28T04:29:00+00:00", border=False, kind="fixed"),  # 포항 — 접경 아님
]
DRILLS = {"fetched_at": "2026-10-28T06:00:00+00:00",
          "sources": {"cheorwon": {"status": "ok"}, "paju": {"status": "error"}},
          "posts": [{"source": "cheorwon", "title": "10월 5주차 포탄사격", "url": "https://x/1", "unit": "제5포병여단",
                     "places": ["문혜리 사격장 → 용화동 피탄지"], "days": ["2026-10-28", "2026-10-30"]},
                    {"source": "cheorwon", "title": "11월 1주차", "url": "https://x/2", "unit": None,
                     "places": [], "days": ["2026-11-03"]}]}


def doc(points, at="2026-10-28T07:10:00+00:00"):
    return {"fetched_at": at, "window_h": 48, "points": points}


def test_passes_merge_into_one_candidate():
    d = o.build(doc(REAL), None, None)
    assert d["count"] == 2                                   # 접경 6점 → 사건 후보 2(비접경 제외)
    c = next(x for x in d["candidates"] if x["side"] == "south")
    assert (c["n"], c["passes"]) == (5, 4)
    assert c["sats"] == ["MODIS", "NOAA20", "NOAA21", "SNPP"]
    assert c["first_t"] == "2026-10-28T03:47:00+00:00" and c["last_t"] == "2026-10-28T06:50:00+00:00"
    assert c["mdl_km_min"] == 0.7 and c["frp_max"] == 22.46
    assert c["mil"] == ["군사 시설(이름 없음)(OSM 750m 이내)"]
    assert c["kst_days"] == ["2026-10-28"]


def test_far_points_stay_separate():
    a, b = pt(38.0, 127.0, "2026-10-28T03:00:00+00:00"), pt(38.0, 127.02, "2026-10-28T03:00:00+00:00")
    assert o.build(doc([a, b]), None, None)["count"] == 2    # 0.02° ≈ 4칸 — 이웃 아님


def test_schedule_is_date_only_and_south_only():
    d = o.build(doc(REAL), DRILLS, None)
    s = next(x for x in d["candidates"] if x["side"] == "south")["schedule"]
    assert s["checked"] and s["place_checked"] is False       # 위치는 대조하지 않는다
    assert [p["url"] for p in s["same_day"]] == ["https://x/1"] and s["same_day"][0]["days"] == ["2026-10-28"]
    assert s["sources_ok"] == ["cheorwon"]                    # 실패한 소스는 대조에 없었다는 표시
    n = next(x for x in d["candidates"] if x["side"] == "north")["schedule"]
    assert n["checked"] is False and n["same_day"] == []      # 남측 공지로 북측 열점을 설명하지 않는다


def test_schedule_unchecked_without_drills_file():
    s = o.build(doc(REAL), None, None)["candidates"][0]["schedule"]
    assert s["checked"] is False and "없음" in s["why"]


def test_kst_day_crosses_midnight():
    d = o.build(doc([pt(38.0, 127.0, "2026-11-05T16:56:00+00:00")]), None, None)
    assert d["candidates"][0]["kst_days"] == ["2026-11-06"]   # UTC 16:56 = KST 다음 날 01:56


def test_side_unknown_when_annotation_missing():
    p = pt(38.0, 127.0, "2026-10-28T03:00:00+00:00")
    del p["north"], p["kind"]
    c = o.build(doc([p]), DRILLS, None)["candidates"][0]
    assert c["side"] == "unknown" and c["schedule"]["checked"] and c["kinds"] == {"none": 1}


def test_first_seen_inherits_by_cell_overlap():
    first = o.build(doc(REAL[:2], at="2026-10-28T04:10:00+00:00"), None, None)
    # 이틀 뒤: 앞 점이 창에서 빠져 id 는 바뀌지만 같은 격자의 뒤 점이 남았다
    later = o.build(doc([pt(38.164, 126.973, "2026-10-29T16:00:00+00:00")], at="2026-10-30T05:10:00+00:00"), None, first)
    assert later["candidates"][0]["id"] != first["candidates"][0]["id"]
    assert later["candidates"][0]["first_seen"] == "2026-10-28T04:10:00+00:00"
    fresh = o.build(doc([pt(38.4, 127.3, "2026-10-29T16:00:00+00:00")], at="2026-10-30T05:10:00+00:00"), None, first)
    assert fresh["candidates"][0]["first_seen"] == "2026-10-30T05:10:00+00:00"


def test_same_input_same_output(tmp_path):
    f, dr, out = tmp_path / "firms.json", tmp_path / "drills.json", tmp_path / "obs.json"
    f.write_text(json.dumps(doc(REAL)))
    dr.write_text(json.dumps(DRILLS))
    argv = ["x", "--firms", str(f), "--drills", str(dr), "--out", str(out)]
    assert o.main(argv) == 0
    a = out.read_text()
    assert o.main(argv) == 0 and out.read_text() == a         # 벽시계 없음 — 재실행해도 바이트 동일(빈 커밋 방지)


def test_missing_firms_keeps_previous(tmp_path):
    out = tmp_path / "obs.json"
    out.write_text("{}")
    assert o.main(["x", "--firms", str(tmp_path / "none.json"), "--out", str(out)]) == 0
    assert out.read_text() == "{}"

"""긴장 국면(ladder)·배경 요인(factors) — 등급과 무관한 참고 블록. 깨진 입력에도 회차를 막지 않는지 본다."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import diff_state as d  # noqa: E402

OPENED = ["https://www.yna.co.kr/a", "https://www.reuters.com/b", "https://news.yna.co.kr/c"]
T0 = "2026-10-18T05:00:00+00:00"
CAT = {"ids": {"targeted_threat": 1, "lethal_clash": 2, "ultimatum": 3, "embassy_drawdown": 4}, "names": {0: "평상", 1: "수사 격화", 2: "국지 도발", 3: "대치·동원", 4: "전쟁 준비"}, "total": 4}


def lrun(rows, **extra):
    return {"checked_at": T0, "opened": OPENED, "ladder": rows, **extra}


def src(*urls):
    return [{"url": u, "publisher": "p"} for u in urls]


def test_catalog_reads_data_file():
    cat = d.ladder_catalog()
    assert cat["total"] == 16 and cat["names"][0] == "평상" and max(cat["ids"].values()) == 4


def test_ladder_corroborated_sets_stage_single_only_checking():
    doc = d.update_ladder(lrun([
        {"id": "lethal_clash", "status": "corroborated", "summary": "s", "sources": src(OPENED[0], OPENED[1])},
        {"id": "ultimatum", "status": "single", "summary": "s", "sources": src(OPENED[0])}]), None, T0, CAT)
    assert doc["stage"] == 2 and doc["stage_name"] == "국지 도발"
    assert doc["met"] == ["lethal_clash"] and doc["checking"] == ["ultimatum"] and doc["total"] == 4


def test_ladder_same_site_corroborated_downgraded():
    # yna.co.kr 하위 도메인 둘 = 한 곳 → single
    doc = d.update_ladder(lrun([{"id": "lethal_clash", "status": "corroborated", "summary": "s",
                                 "sources": src(OPENED[0], OPENED[2])}]), None, T0, CAT)
    assert doc["stage"] == 0 and doc["checking"] == ["lethal_clash"]


def test_ladder_drops_unopened_unknown_and_malformed():
    rows = [{"id": "lethal_clash", "status": "corroborated", "summary": "s", "sources": src("https://x.example/1", "https://y.example/2")},
            {"id": "nope", "status": "corroborated", "summary": "s", "sources": src(OPENED[0], OPENED[1])},
            {"id": "ultimatum", "status": "maybe", "summary": "s", "sources": src(OPENED[0])},
            "junk", None, 3]
    doc = d.update_ladder(lrun(rows), None, T0, CAT)
    assert doc["stage"] == 0 and doc["met"] == [] and doc["checking"] == []
    for bad in ("x", None, {"a": 1}, 7):
        out = d.update_ladder(lrun(bad), None, T0, CAT)
        assert out["stage"] == 0


def test_ladder_holds_72h_then_expires():
    r = lrun([{"id": "ultimatum", "status": "corroborated", "summary": "s", "sources": src(OPENED[0], OPENED[1])}])
    doc = d.update_ladder(r, None, T0, CAT)
    held = d.update_ladder({}, doc, "2026-10-21T04:00:00+00:00", CAT)  # 71h
    assert held["stage"] == 3
    gone = d.update_ladder({}, held, "2026-10-21T06:00:00+00:00", CAT)  # 73h
    assert gone["stage"] == 0 and gone["history"][-1]["to"] == 0 and len(gone["history"]) == 2


def test_ladder_ongoing_until_and_ended():
    r = lrun([{"id": "ultimatum", "status": "corroborated", "summary": "s", "ongoing": True,
               "until": "2026-10-20T08:00:00+00:00", "sources": src(OPENED[0], OPENED[1])}])
    doc = d.update_ladder(r, None, T0, CAT)
    assert d.update_ladder({}, doc, "2026-10-21T07:00:00+00:00", CAT)["stage"] == 3  # 시한+23h
    assert d.update_ladder({}, doc, "2026-10-21T09:00:00+00:00", CAT)["stage"] == 0  # 시한+25h
    ended = d.update_ladder(lrun([{"id": "ultimatum", "ended": True, "summary": "합의"}]), doc, "2026-10-18T06:00:00+00:00", CAT)
    assert ended["stage"] == 0 and "ultimatum" in ended["history"][-1]["why"]


def test_ladder_deescalation_clears_all():
    doc = d.update_ladder(lrun([{"id": "lethal_clash", "status": "corroborated", "summary": "s", "sources": src(OPENED[0], OPENED[1])}]), None, T0, CAT)
    out = d.update_ladder({"checked_at": T0, "opened": OPENED, "deescalation": "8/25 합의"}, doc, "2026-10-18T06:00:00+00:00", CAT)
    assert out["stage"] == 0 and out["history"][-1]["why"].startswith("해제 근거")


def test_ladder_single_then_corroborated_merges_sources():
    doc = d.update_ladder(lrun([{"id": "lethal_clash", "status": "single", "summary": "s", "sources": src(OPENED[0])}]), None, T0, CAT)
    doc = d.update_ladder(lrun([{"id": "lethal_clash", "status": "single", "summary": "s2", "sources": src(OPENED[1])}]), doc, "2026-10-18T06:00:00+00:00", CAT)
    ind = doc["indicators"]["lethal_clash"]
    assert ind["status"] == "single" and len(ind["sources"]) == 2 and ind["first_seen"] == T0  # 루틴 판정은 그대로, 출처는 누적
    doc = d.update_ladder(lrun([{"id": "lethal_clash", "status": "corroborated", "summary": "s3", "sources": src(OPENED[0], OPENED[1])}]), doc, "2026-10-18T07:00:00+00:00", CAT)
    assert doc["stage"] == 2


def test_ladder_missing_catalog_keeps_previous():
    prev = {"stage": 3, "indicators": {"ultimatum": {"status": "corroborated"}}}
    out = d.update_ladder(lrun([]), prev, T0, {"ids": {}})
    assert out["stage"] == 3 and out["error"] == "catalog"


def frun(f):
    return {"checked_at": T0, "opened": OPENED, "factors": f}


def test_factors_rules_and_carry():
    f = {"nk_domestic": {"level": "긴장", "summary": "당 회의 강대강", "sources": src(OPENED[0])},
         "inter_korean": {"level": "긴장", "summary": "출처 없음"},                       # 긴장인데 근거 없음 → 버림
         "great_powers": {"level": "보통"},                                              # 보통은 근거 없이도 됨
         "public_opinion": {"level": "매우 긴장", "summary": "s", "sources": src(OPENED[0])},  # level 오타 → 버림
         "unknown": {"level": "긴장"}}
    doc = d.update_factors(frun(f), None, T0)
    assert set(doc["domains"]) == {"nk_domestic", "great_powers"} and doc["at"] == T0
    later = "2026-10-18T11:00:00+00:00"
    doc2 = d.update_factors(frun({"nk_domestic": {"level": "보통", "changed": "노선 완화 보도"}}), doc, later)
    assert doc2["domains"]["great_powers"]["at"] == T0  # 빠진 영역은 잇는다
    assert doc2["domains"]["nk_domestic"]["since"] == later and doc2["history"][-1]["from"] == "긴장"
    doc3 = d.update_factors({}, doc2, "2026-10-18T12:00:00+00:00")
    assert doc3["at"] == later and doc3["domains"] == doc2["domains"]


def test_factors_unopened_source_dropped():
    doc = d.update_factors(frun({"nk_domestic": {"level": "긴장", "summary": "s", "sources": src("https://evil.example/x")}}), None, T0)
    assert doc["domains"] == {}


def test_factors_and_ladder_never_change_grade_or_latest():
    base = {"checked_at": T0, "items": {k: {"status": "none"} for k in d.ITEMS}, "overall_summary": "s",
            "coverage": {k: ["q"] for k in d.ITEMS}, "opened": OPENED}
    l0, _, o0 = d.step(json.loads(json.dumps(base)), None, [])
    r = {**json.loads(json.dumps(base)), "ladder": [{"id": "embassy_drawdown", "status": "corroborated", "summary": "s",
                                                     "sources": src(OPENED[0], OPENED[1])}],
         "factors": {"nk_domestic": {"level": "긴장", "summary": "s", "sources": src(OPENED[0])}}}
    l1, _, o1 = d.step(r, None, [])
    assert l1["grade"] == l0["grade"] == 0 and o0 is None and o1 is None
    assert "ladder" not in l1 and "factors" not in l1


def test_main_writes_state_and_tolerates_garbage():
    with tempfile.TemporaryDirectory() as tmp:
        old = d.STATE
        d.STATE = Path(tmp)
        try:
            (d.STATE / "ladder.json").write_text("{not json")
            (d.STATE / "factors.json").write_text("[1,2")
            run = {"items": {k: {"status": "none"} for k in d.ITEMS}, "overall_summary": "s",
                   "coverage": {k: ["q"] for k in d.ITEMS}, "opened": OPENED,
                   "ladder": "garbage", "factors": ["garbage"]}
            p = d.STATE / "run.json"
            p.write_text(json.dumps(run))
            assert d.main(["x", str(p)]) == 0
            lad = json.loads((d.STATE / "ladder.json").read_text())
            fac = json.loads((d.STATE / "factors.json").read_text())
            assert lad["stage"] == 0 and lad["total"] == 16 and fac["domains"] == {} and fac["at"] is None
            ev = json.loads((d.STATE / "events.jsonl").read_text().splitlines()[-1])
            assert ev["status"] == "ok" and ev["ladder"]["stage"] == 0 and ev["factors"]["kept"] == 0
            # 판정 불가 회차도 막지 않는다
            p.write_text(json.dumps({"status": "unverified", "reason": "검색 실패"}))
            assert d.main(["x", str(p)]) == 0
        finally:
            d.STATE = old

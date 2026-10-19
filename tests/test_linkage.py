"""연동 위기(linkage) — 대만 해협 훈련 여부·한반도 연결. 등급과 무관한 참고 블록이고, 확인·유지 규칙은 긴장 국면과 같다."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import diff_state as d  # noqa: E402

OPENED = ["https://www.yna.co.kr/a", "https://www.reuters.com/b", "https://news.yna.co.kr/c", "https://apnews.com/d"]
T0 = "2026-10-18T05:00:00+00:00"
T1 = "2026-10-19T05:00:00+00:00"   # +24h
T4 = "2026-10-21T06:00:00+00:00"   # +73h — CRISIS_FLOOR_H(72) 뒤


def run(lk, **extra):
    return {"checked_at": T0, "opened": OPENED, "linkage": lk, **extra}


def src(*urls):
    return [{"url": u, "publisher": "p"} for u in urls]


def named(*urls, **kw):
    return {"taiwan": {"state": "named", "name": "Strait Thunder-2026A", "start": "2026-10-18", "summary": "동부전구 발표",
                       "sources": src(*urls), **kw}}


def test_named_two_sites_corroborated_sets_last_named():
    doc = d.update_linkage(run(named(OPENED[0], OPENED[1])), None, T0)
    assert doc["taiwan"]["status"] == "corroborated" and doc["taiwan"]["state"] == "named"
    assert doc["last_named"]["name"] == "Strait Thunder-2026A" and doc["judged_at"] == T0


def test_named_same_site_is_single_and_not_last_named():
    # yna.co.kr 하위 도메인 둘 = 한 곳 → single(화면은 「확인 중」), 마지막 명명 훈련으로 기록하지 않는다
    doc = d.update_linkage(run(named(OPENED[0], OPENED[2])), None, T0)
    assert doc["taiwan"]["status"] == "single" and "last_named" not in doc


def test_single_then_second_site_upgrades():
    a = d.update_linkage(run(named(OPENED[0])), None, T0)
    b = d.update_linkage(run(named(OPENED[1])), a, T1)
    assert b["taiwan"]["status"] == "corroborated" and b["taiwan"]["first_seen"] == T0 and len(b["taiwan"]["sources"]) == 2


def test_carry_when_routine_omits_and_expire_after_floor():
    a = d.update_linkage(run(named(OPENED[0], OPENED[1])), None, T0)
    b = d.update_linkage({}, a, T1)  # 안 냄 → 잇는다
    assert b["taiwan"]["state"] == "named" and b["judged_at"] == T0
    c = d.update_linkage({}, b, T4)  # 72시간 뒤 → 내린다, 마지막 명명 훈련은 남는다
    assert c["taiwan"] is None and c["last_named"]["name"] == "Strait Thunder-2026A"


def test_none_needs_source_to_clear():
    a = d.update_linkage(run(named(OPENED[0], OPENED[1])), None, T0)
    b = d.update_linkage(run({"taiwan": {"state": "none", "summary": "없음"}}), a, T1)
    assert b["taiwan"]["state"] == "named"  # 근거 없는 「없음」은 직전 값을 잇는다
    c = d.update_linkage(run({"taiwan": {"ended": True, "summary": "圆满完成", "sources": src(OPENED[3])}}), b, T1)
    assert c["taiwan"] is None and c["last_named"]["name"] == "Strait Thunder-2026A"


def test_unopened_source_and_bad_state_dropped():
    doc = d.update_linkage(run({"taiwan": {"state": "named", "summary": "s", "sources": src("https://x.com/1")},
                                "korea": [{"summary": "s", "sources": src("https://x.com/2")}]}), None, T0)
    assert doc["taiwan"] is None and doc["korea"] == []
    doc = d.update_linkage(run({"taiwan": {"state": "war", "summary": "s", "sources": src(OPENED[0])}}), None, T0)
    assert doc["taiwan"] is None


def test_korea_links_merge_by_same_as_and_url_and_expire():
    a = d.update_linkage(run({"korea": [{"summary": "북한군 추가 파병 공식 발표", "date": "2026-10-18", "sources": src(OPENED[0])}]}), None, T0)
    k = a["korea"][0]
    assert k["status"] == "single" and k["date"] == "2026-10-18"
    b = d.update_linkage(run({"korea": [{"summary": "같은 사건", "same_as": k["id"], "sources": src(OPENED[1])}]}), a, T1)
    assert len(b["korea"]) == 1 and b["korea"][0]["status"] == "corroborated" and b["korea"][0]["last_seen"] == T1
    c = d.update_linkage(run({"korea": [{"summary": "재인용", "sources": src(OPENED[1])}]}), b, T1)
    assert len(c["korea"]) == 1  # 같은 URL = 같은 사건
    e = d.update_linkage({}, c, "2026-10-22T06:00:00+00:00")
    assert e["korea"] == []


def test_korea_links_capped():
    rows = [{"summary": f"s{i}", "sources": src(f"https://site{i}.com/x")} for i in range(9)]
    r = {"checked_at": T0, "opened": [f"https://site{i}.com/x" for i in range(9)], "linkage": {"korea": rows}}
    assert len(d.update_linkage(r, None, T0)["korea"]) == d.KOREA_LINKS_MAX


def test_garbage_never_raises():
    for bad in ("x", ["x"], {"taiwan": "x", "korea": "x"}, {"korea": [None, 1, {"summary": 3}]}, None):
        doc = d.update_linkage({"opened": OPENED, "linkage": bad}, {"taiwan": "broken", "korea": "broken"}, T0)
        assert doc["taiwan"] is None and doc["korea"] == []
    assert d.update_linkage(None, None, T0)["korea"] == []


def test_step_does_not_leak_linkage_into_latest_or_grade():
    r = {"items": {k: {"status": "none"} for k in d.ITEMS}, "overall_summary": "s",
         "coverage": {k: ["q"] for k in d.ITEMS}, "opened": OPENED, "checked_at": T0, "linkage": named(OPENED[0], OPENED[1])}
    latest, _, outbox = d.step(r, None, [])
    assert latest["grade"] == 0 and outbox is None and "linkage" not in latest


def test_main_writes_linkage_state():
    with tempfile.TemporaryDirectory() as tmp:
        old = d.STATE
        d.STATE = Path(tmp)
        try:
            (d.STATE / "linkage.json").write_text("{not json")
            r = {"items": {k: {"status": "none"} for k in d.ITEMS}, "overall_summary": "s",
                 "coverage": {k: ["q"] for k in d.ITEMS}, "opened": OPENED,
                 "linkage": {"korea": [{"summary": "s", "sources": src(OPENED[0], OPENED[1])}]}}
            p = d.STATE / "run.json"
            p.write_text(json.dumps(r))
            assert d.main(["x", str(p)]) == 0
            lk = json.loads((d.STATE / "linkage.json").read_text())
            assert lk["korea"][0]["status"] == "corroborated" and lk["taiwan"] is None
            ev = json.loads((d.STATE / "events.jsonl").read_text().splitlines()[-1])
            assert ev["linkage"] == {"taiwan": "none", "korea": 1}
            p.write_text(json.dumps({"status": "unverified", "reason": "검색 실패"}))
            assert d.main(["x", str(p)]) == 0
            assert len(json.loads((d.STATE / "linkage.json").read_text())["korea"]) == 1  # 판정 불가 회차도 잇는다
        finally:
            d.STATE = old


def test_data_file_is_canonical_and_consistent():
    doc = json.loads((ROOT / "data" / "linkage.json").read_text())
    assert [g["key"] for g in doc["groups"]] == ["tw_named", "tw_major", "ru_nk"]
    ids = {e["id"] for e in doc["events"]}
    for g in doc["groups"]:
        assert set(g["events"]) <= ids
        for w in ("w7", "w14"):
            r = g[w]
            assert 0 <= r["hit"] <= r["events"] == len(r["windows"]) and r["verdict"] in ("more", "usual", "less")
            assert r["hit"] == sum(1 for x in r["windows"] if x["launch_days"])
    for e in doc["events"]:
        assert e["sources"] and all(s["url"].startswith("http") for s in e["sources"])
    # 간접 인용은 표시가 붙어 있어야 한다(2006 공동성명·국가안보전략연구원)
    ind = [x for x in doc["readings"] if x.get("indirect")]
    assert len(ind) == 2 and all(x["url"].startswith("https://") for x in doc["readings"])
    assert all(len(x["quote"].split()) <= 15 for x in doc["readings"])

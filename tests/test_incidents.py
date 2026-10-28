"""사건 기록(incidents) — 6하원칙 기록, 직접/간접 판정 게이트, 병합·보관. 등급과 무관한 참고 블록."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import diff_state as d  # noqa: E402

POLICE = "https://www.police.go.kr/news/1"      # A 등급 사이트(sources.json)
YNA = "https://www.yna.co.kr/view/2"
YNA2 = "https://www.yna.co.kr/view/3"
OPENED = [POLICE, YNA, YNA2]
T0 = "2026-10-28T03:00:00+00:00"
T1 = "2026-10-29T03:00:00+00:00"
T40 = "2026-12-07T03:00:00+00:00"   # +40일


def inc(**kw):
    row = {"tier": "indirect", "category": "총기", "what": "해안 초소 인근에서 소총 실탄 발견",
           "who": "해병대 수색대", "how": "정찰 중 발견", "why": "미상", "link": "북한제 여부 감식 중",
           "when": {"occurred": "2026-10-27", "announced": "2026-10-28"},
           "where": {"place": "강화 교동도", "lat": 37.78, "lon": 126.27},
           "sources": [{"url": YNA, "publisher": "연합뉴스", "tier": "B"}]}
    row.update(kw)
    return row


def run(*rows):
    return {"checked_at": T0, "opened": OPENED, "incidents": list(rows)}


def test_direct_without_attribution_is_downgraded():
    out = d.clean_incidents(run(inc(tier="direct")))
    assert out[0]["tier"] == "indirect" and out[0]["downgraded"]


def test_direct_with_a_tier_attribution_kept():
    r = inc(tier="direct", attributed_by={"name": "경찰청", "tier": "A", "what": "보도자료"},
            sources=[{"url": POLICE, "publisher": "경찰청", "tier": "A"}])
    out = d.clean_incidents(run(r))
    assert out[0]["tier"] == "direct" and out[0]["attributed_by"]["name"] == "경찰청"


def test_unopened_source_and_missing_fields_dropped():
    assert d.clean_incidents(run(inc(sources=[{"url": "https://x.example/9"}]))) == []
    assert d.clean_incidents(run(inc(what=""))) == []
    assert d.clean_incidents(run(inc(when={}))) == []


def test_names_masked_but_roles_kept():
    out = d.clean_incidents(run(inc(who="홍길동(34) 등 2명, 남성(41)")))
    assert "홍길동" not in out[0]["who"] and "남성(41)" in out[0]["who"]


def test_out_of_range_coords_dropped():
    out = d.clean_incidents(run(inc(where={"place": "해외", "lat": 10.0, "lon": 100.0})))
    assert "lat" not in out[0]["where"]


def test_merge_same_event_and_resolve():
    doc = d.update_incidents(run(inc()), None, T0)
    assert len(doc["items"]) == 1
    doc = d.update_incidents({"opened": OPENED, "incidents": [inc(sources=[{"url": YNA2}], tier="resolved")]}, doc, T1)
    assert len(doc["items"]) == 1 and doc["items"][0]["tier"] == "resolved" and len(doc["items"][0]["sources"]) == 2


def test_direct_not_downgraded_by_later_indirect_report():
    r = inc(tier="direct", attributed_by={"name": "경찰청", "tier": "A"}, sources=[{"url": POLICE, "tier": "A"}])
    doc = d.update_incidents(run(r), None, T0)
    doc = d.update_incidents(run(inc(sources=[{"url": POLICE}])), doc, T1)
    assert doc["items"][0]["tier"] == "direct"


def test_indirect_expires_after_30_days_direct_kept():
    r = inc(tier="direct", category="마약", where={"place": "부산항"}, attributed_by={"name": "경찰청", "tier": "A"},
            sources=[{"url": POLICE, "tier": "A"}])
    doc = d.update_incidents(run(inc(), r), None, T0)
    doc = d.update_incidents({}, doc, T40)
    assert [x["tier"] for x in doc["items"]] == ["direct"]


def test_no_incidents_key_keeps_previous():
    doc = d.update_incidents(run(inc()), None, T0)
    doc2 = d.update_incidents({}, doc, T1)
    assert len(doc2["items"]) == 1 and doc2["judged_at"] == T0

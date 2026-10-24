"""귀속(attribution) 정규화 — 10/20: 귀속 미확인 해킹은 항목에 남되 등급에 안 쓴다(routine 강제)."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from diff_state import normalize_attribution, compute_grade


def test_unknown_forces_routine():
    items = {"electronic_cyber": {"status": "corroborated", "routine": False, "attribution": "Unknown",
                                  "sources": [{"url": "https://a.kr/1"}, {"url": "https://b.kr/2"}]}}
    normalize_attribution(items)
    it = items["electronic_cyber"]
    assert it["attribution"] == "unknown" and it["routine"] is True and it["routine_forced"] == "attribution unknown"


def test_nk_keeps_routine_as_given():
    items = {"electronic_cyber": {"status": "corroborated", "routine": False, "attribution": "nk", "sources": []}}
    normalize_attribution(items)
    assert items["electronic_cyber"]["routine"] is False and "routine_forced" not in items["electronic_cyber"]


def test_bad_value_dropped_and_none_untouched():
    items = {"electronic_cyber": {"status": "none", "attribution": "unknown"},
             "nuclear": {"status": "single", "attribution": "martian", "sources": [{"url": "https://a.kr/1"}]}}
    normalize_attribution(items)
    assert items["electronic_cyber"].get("routine") is None  # 징후 없음은 손대지 않는다
    assert "attribution" not in items["nuclear"]


def _hyb(**kw):
    base = {"status": "single", "routine": False, "summary": "s",
            "sources": [{"name": "경찰청", "tier": "A", "url": "https://www.police.go.kr/x"}]}
    base.update(kw)
    return {"hybrid_ops": base}


def test_hybrid_without_attribution_is_demoted_to_background(monkeypatch=None):
    import diff_state as d
    d._A_SITES = {"police.go.kr"}
    items = _hyb()
    d.normalize_attributed_by(items)
    it = items["hybrid_ops"]
    assert it["status"] == "none" and it["attribution_dropped"] and it["summary"].startswith("귀속 미확정 — ")
    assert "hybrid_ops" not in d.active_keys(items)


def test_hybrid_with_A_attribution_and_A_source_stays():
    import diff_state as d
    d._A_SITES = {"police.go.kr"}
    items = _hyb(attributed_by={"name": "경찰청", "tier": "A", "what": "구속 송치 보도자료", "extra": 1}, category="간첩")
    d.normalize_attributed_by(items)
    it = items["hybrid_ops"]
    assert it["status"] == "single" and it["attributed_by"] == {"name": "경찰청", "tier": "A", "what": "구속 송치 보도자료"}
    assert it["category"] == "간첩" and "hybrid_ops" in d.active_keys(items)


def test_hybrid_attribution_needs_A_source_from_official_site():
    import diff_state as d
    d._A_SITES = {"police.go.kr"}
    items = _hyb(attributed_by={"name": "경찰청", "tier": "A"},
                 sources=[{"name": "연합뉴스", "tier": "A", "url": "https://www.yna.co.kr/x"}])  # 언론이 A 를 자칭해도 공식 사이트가 아니다
    d.normalize_attributed_by(items)
    assert items["hybrid_ops"]["status"] == "none"
    items = _hyb(attributed_by={"name": "경찰청", "tier": "B"})
    d.normalize_attributed_by(items)
    assert items["hybrid_ops"]["status"] == "none" and "attributed_by" not in items["hybrid_ops"]


def test_other_items_keep_status_but_drop_bad_attributed_by_and_category():
    import diff_state as d
    d._A_SITES = {"police.go.kr"}
    items = {"electronic_cyber": {"status": "single", "sources": [{"tier": "B", "url": "https://www.yna.co.kr/1"}],
                                  "attributed_by": {"name": "FBI", "tier": "A"}, "category": "외계"}}
    d.normalize_attributed_by(items)
    it = items["electronic_cyber"]
    assert it["status"] == "single" and "attributed_by" not in it and "category" not in it


def test_real_sources_json_lists_official_sites():
    import diff_state as d
    d._A_SITES = None
    s = d.a_tier_sites()
    assert "police.go.kr" in s and "fbi.gov" in s and "npa.go.jp" in s


def test_new_hybrid_categories_kept_and_still_gated():
    import diff_state as d
    d._A_SITES = {"police.go.kr", "kcg.go.kr"}
    for cat, src in (("제재 위반", "https://www.kcg.go.kr/x"), ("영향력 공작", "https://www.police.go.kr/y")):
        items = _hyb(attributed_by={"name": "기관", "tier": "A"}, category=cat,
                     sources=[{"name": "기관", "tier": "A", "url": src}])
        d.normalize_attributed_by(items)
        assert items["hybrid_ops"]["category"] == cat and items["hybrid_ops"]["status"] == "single"
    items = _hyb(category="영향력 공작", sources=[{"name": "언론", "tier": "B", "url": "https://www.yna.co.kr/z"}])
    d.normalize_attributed_by(items)  # 해외 시위 자금 「의혹」 보도만 있으면 배경
    assert items["hybrid_ops"]["status"] == "none"

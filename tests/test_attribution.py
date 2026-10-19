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

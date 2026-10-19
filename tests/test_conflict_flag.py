"""「상충」 표시(10/20): 코드 게이트 — 징후 없음·사이트 2곳 미만·사유 없음이면 지운다. 등급은 안 읽는다."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import diff_state as ds  # noqa: E402

A = {"name": "연합", "tier": "B", "url": "https://www.yna.co.kr/a"}
B = {"name": "합참", "tier": "A", "url": "https://www.jcs.mil.kr/b"}
A2 = {"name": "연합2", "tier": "B", "url": "https://www.yna.co.kr/c"}


def test_conflict_kept_only_with_two_sites_and_reason():
    items = {"border_clash": {"status": "single", "conflict": True, "conflict_what": "합참이 교전 보도를 부인", "sources": [A, B]}}
    assert ds.normalize_conflict(items)["border_clash"]["conflict"] is True


def test_conflict_dropped_cases():
    none = {"status": "none", "conflict": True, "conflict_what": "x"}
    one_site = {"status": "corroborated", "conflict": True, "conflict_what": "x", "sources": [A, A2]}
    no_why = {"status": "single", "conflict": True, "sources": [A, B]}
    out = ds.normalize_conflict({"a": none, "b": one_site, "c": no_why})
    assert [out[k]["conflict"] for k in "abc"] == [False, False, False]
    assert out["a"]["conflict_dropped"] == "징후 없음" and out["b"]["conflict_dropped"] == "서로 다른 사이트 2곳 미만" and out["c"]["conflict_dropped"] == "conflict_what 없음"


def test_conflict_does_not_change_grade():
    base = {k: {"status": "none"} for k in ds.ITEMS}
    base["border_clash"] = {"status": "single", "routine": False, "sources": [A, B]}
    g0 = ds.compute_grade(base)
    base["border_clash"].update(conflict=True, conflict_what="합참 부인")
    assert ds.compute_grade(base) == g0

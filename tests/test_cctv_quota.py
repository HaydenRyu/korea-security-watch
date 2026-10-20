"""ITS CCTV 월 호출 예산 — 10/20 실측 인증키 한도 1,000/월(4001 문구). 캐시 주기와 장부가 그 안에 머무는지."""
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cctv_snap as cs


def test_cache_periods_fit_monthly_cap():
    worst_per_day = 24 * 60 * 60 / cs.MIN_REFETCH          # 재조회 최소 간격마다 1회(도로 종류 1)
    assert worst_per_day * 31 <= cs.ITS_BUDGET, (worst_per_day * 31, cs.ITS_BUDGET)
    assert cs.URL_TTL < 2 * 3600                             # 재생 주소는 2시간 만료
    assert cs.ITS_BUDGET < cs.ITS_MONTHLY_CAP


def test_call_ledger_counts_and_rolls_over_by_month():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "calls.json"
        assert cs.calls_this_month(p) == 0
        assert cs.note_call(p) == 1 and cs.note_call(p) == 2 and cs.calls_this_month(p) == 2
        p.write_text(json.dumps({"month": "1999-01", "n": 999}))   # 지난달 장부는 0 으로
        assert cs.calls_this_month(p) == 0 and cs.note_call(p) == 1
        p.write_text("garbage")
        assert cs.calls_this_month(p) == 0

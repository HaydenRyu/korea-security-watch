"""ITS CCTV 월 호출 예산 — 10/20 실측 인증키 한도 1,000/월(4001 문구). 캐시 주기와 장부가 그 안에 머무는지."""
import json, sys, tempfile, urllib.error
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


def test_probe_resumes_when_server_limit_lifted():
    """한도 초과 표시 중 하루 1회 두드려서, 풀렸으면 표시를 지우고 알린다. 장부가 상한이면 두드리지 않는다."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        saved = (cs.QUOTA_MARK, cs.PROBE_MARK, cs.CALL_LOG, cs.URL_CACHE, cs.area_urls, cs.ntfy)
        sent, calls = [], []
        cs.QUOTA_MARK, cs.PROBE_MARK, cs.CALL_LOG, cs.URL_CACHE = d / "q", d / "p", d / "c.json", d / "u.json"
        cs.ntfy = lambda t, m, p: sent.append(t) or True
        try:
            cs.QUOTA_MARK.write_text("2099-01-01")
            # 1) 서버가 아직 막음(4001) → "still", 표시 유지, 오늘은 다시 두드리지 않음
            def blocked(key, cams):
                calls.append(1)
                raise urllib.error.HTTPError("u", 401, "x", {}, None)
            cs.area_urls = blocked
            assert cs.probe_quota("k", []) == "still" and cs.QUOTA_MARK.exists() and len(calls) == 1
            assert cs.probe_quota("k", []) is None and len(calls) == 1          # 하루 1회
            # 2) 하루 지남 + 서버가 풀림 → "resumed", 표시 삭제, 주소 캐시 저장, 알림 1회
            import os
            os.utime(cs.PROBE_MARK, (1, 1))
            cs.area_urls = lambda key, cams: calls.append(1) or {"its": [{"cctvname": "a", "cctvurl": "http://x", "coordx": "1", "coordy": "1"}]}
            assert cs.probe_quota("k", []) == "resumed"
            assert not cs.QUOTA_MARK.exists() and cs.URL_CACHE.exists() and sent == ["CCTV 재개"]
            # 3) 우리 장부가 상한 → 두드리지 않음
            cs.QUOTA_MARK.write_text("2099-01-01"); os.utime(cs.PROBE_MARK, (1, 1))
            cs.CALL_LOG.write_text(json.dumps({"month": cs._month(), "n": cs.ITS_BUDGET}))
            n = len(calls)
            assert cs.probe_quota("k", []) is None and len(calls) == n
        finally:
            cs.QUOTA_MARK, cs.PROBE_MARK, cs.CALL_LOG, cs.URL_CACHE, cs.area_urls, cs.ntfy = saved

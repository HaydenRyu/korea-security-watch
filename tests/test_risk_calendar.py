import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import risk_calendar as rc  # noqa: E402


def test_poisson_binomial_matches_binomial():
    dist = rc.poisson_binomial([0.5, 0.5, 0.5])
    assert [round(x, 6) for x in dist] == [0.125, 0.375, 0.375, 0.125]
    assert abs(sum(rc.poisson_binomial([0.1, 0.7, 0.33, 0.9])) - 1) < 1e-12


def test_poisson_binomial_mixed_probs():
    # P(X=0)=0.2*0.6, P(X=2)=0.8*0.4
    dist = rc.poisson_binomial([0.8, 0.4])
    assert abs(dist[0] - 0.12) < 1e-12 and abs(dist[2] - 0.32) < 1e-12 and abs(dist[1] - 0.56) < 1e-12


def test_verdict_tails():
    ps = [0.3] * 20  # 평소 6
    assert rc.verdict(ps, 6)[0] == "usual"
    assert rc.verdict(ps, 12)[0] == "more"
    assert rc.verdict(ps, 1)[0] == "less"
    v, ge, le = rc.verdict(ps, 6)
    assert 0 < ge <= 1 and 0 < le <= 1 and ge + le > 1  # 두 꼬리가 hit 을 같이 포함


def test_relaunch_rates_and_last():
    days = {date(2020, 1, 1), date(2020, 1, 5), date(2020, 1, 20), date(2020, 3, 1), date(2021, 1, 1)}
    r = rc.relaunch(days, range(2020, 2021), today=date(2021, 1, 10))
    # 2020 발사 4일: 다음까지 4·15·41·306일
    assert r["rate"]["7"] == {"n": 4, "hit": 1, "pct": 25}
    assert r["rate"]["14"]["hit"] == 1 and r["rate"]["30"]["hit"] == 2
    assert r["last"] == "2021-01-01" and r["since"] == 9


def test_relaunch_excludes_unfinished_window():
    # 마지막 발사 뒤 아직 30일이 안 지났으면 30일 분모에서 뺀다(7일은 지났으니 넣는다)
    r = rc.relaunch({date(2020, 12, 20)}, range(2020, 2021), today=date(2021, 1, 5))
    assert r["rate"]["7"]["n"] == 1 and r["rate"]["7"]["hit"] == 0
    assert r["rate"]["30"]["n"] == 0 and r["rate"]["30"]["pct"] is None


def test_season_expected_sums_to_total():
    days = {date(2020, 3, 1), date(2020, 3, 2), date(2021, 7, 1)}
    s = rc.season(days, range(2020, 2022))
    assert s[2]["days"] == 2 and s[2]["years"] == 1 and s[6]["years"] == 1
    assert abs(sum(r["expected"] for r in s) - 3) <= 12 * 0.05  # 달마다 소수 1자리 반올림

def test_signal_check_counts_only_finished_windows():
    today = date(2026, 12, 1)
    mk = lambda kind, fs, **o: {"kind": kind, "first_seen": fs + "T03:00:00+00:00", "expires": "2026-11-01", **o}
    doc = {"expired": [mk("nav_warning", "2026-10-01"), mk("nav_warning", "2026-10-10"), mk("nk_threat", "2026-10-01"),
                       mk("nk_threat", "2026-11-25")],  # 마지막은 14일이 안 지났다
           "active": [mk("site_activity", "2026-09-01"), mk("site_activity", "2026-11-30", expires="2026-12-20")]}
    launch = {date(2026, 10, 5), date(2026, 10, 15)}  # 10/1 신호는 4일 뒤, 10/10 신호는 5일 뒤 발사
    r = rc.signal_check(doc, launch, today)
    assert r["kinds"]["nav_warning"] == {"n": 2, "hit": 2}
    assert r["kinds"]["nk_threat"] == {"n": 1, "hit": 1}
    assert r["kinds"]["site_activity"] == {"n": 1, "hit": 0}  # 만료된 active 만, 발사 없음
    assert rc.signal_check({"expired": [mk("nav_warning", "2026-10-01")]}, {date(2026, 10, 16)}, today)["kinds"]["nav_warning"]["hit"] == 0  # 15일째는 밖
    assert rc.signal_check(None, set(), today)["kinds"] == {} and rc.signal_check({"expired": [{"kind": "x"}]}, set(), today)["kinds"] == {}



if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
    print("ok")


def test_signal_check_skips_unfinished_db_and_reports_baseline():
    today = date(2026, 11, 30)
    doc = {"expired": [{"kind": "nk_threat", "first_seen": "2026-11-01"}, {"kind": "nk_threat", "first_seen": "2026-10-01"}]}
    r = rc.signal_check(doc, {date(2026, 10, 5)}, today, data_last=date(2026, 11, 10), base_years=range(2026, 2027))
    assert r["kinds"]["nk_threat"] == {"n": 1, "hit": 1}  # 11-01 신호는 DB(11-10)가 14일을 다 덮지 못해 보류
    assert 0 < r["baseline"] < 1


def test_event_ko_specific_before_general():
    assert rc.event_ko("Short-range Ballistic Missile Launch") == "단거리 탄도미사일 발사"
    assert rc.event_ko("Submarine Launched Cruise Missile Launch") == "잠수함 발사 순항미사일"
    assert rc.event_ko("Multiple Launch Rocket System (MLRS) Test") == "다연장로켓 시험"
    assert rc.event_ko("Ballistic Missile Launch") == "탄도미사일 발사"
    assert rc.event_ko("Something Else") is None and rc.event_ko(None) is None


def _ds(*iso):
    return sorted(date.fromisoformat(x) for x in iso)


def test_hit_is_after_start_within_h():
    ds = _ds("2026-01-10")
    assert rc._hit(ds, date(2026, 1, 3), 7) and not rc._hit(ds, date(2026, 1, 2), 7)
    assert not rc._hit(ds, date(2026, 1, 10), 7)  # 시작일 당일은 안 센다(다음 날부터)


def test_year_rate_counts_only_finished_windows():
    ds = _ds("2025-12-31")
    k, n = rc.year_rate(ds, date(2026, 1, 1), 7)
    assert n == 359 and k == 2  # 끝이 1/1 이하인 기간만 — 12/31 을 품는 건 12/24·12/25 시작 2개


def test_track_record_bins_and_reports_actual():
    ds = sorted(date(2010, 1, 1) + timedelta(d) for d in range(0, 6000, 10))  # 열흘마다 발사 → 7일 기간 70%
    tr = rc.track_record(ds, 0.7, 7, 2012, date(2015, 6, 1))
    assert (tr["lo"], tr["hi"]) == (0.6, 0.8) and tr["n"] > 1000 and 0.6 <= tr["actual"] <= 0.8


def test_ledger_appends_once_per_day_and_scores_after_window():
    ds = _ds("2026-10-20")
    rows = rc.update_ledger([], {7: 0.4, 30: 0.9}, date(2026, 10, 15), ds, date(2026, 10, 15))
    rows = rc.update_ledger(rows, {7: 0.5, 30: 0.9}, date(2026, 10, 15), ds, date(2026, 10, 15))
    assert len(rows) == 2 and rows[0]["p"] == 0.4 and all(r["hit"] is None for r in rows)
    rows = rc.update_ledger(rows, {7: 0.4, 30: 0.9}, date(2026, 10, 23), ds, date(2026, 10, 22))
    r7 = next(r for r in rows if r["date"] == "2026-10-15" and r["days"] == 7)
    assert r7["hit"] is True  # 10/16~10/22 안 10/20 발사, DB 가 10/22 까지라 채점
    assert next(r for r in rows if r["date"] == "2026-10-15" and r["days"] == 30)["hit"] is None


def test_ledger_summary_brier():
    s = rc.ledger_summary([{"date": "2026-10-01", "days": 7, "p": 0.8, "hit": True},
                           {"date": "2026-10-02", "days": 7, "p": 0.2, "hit": False},
                           {"date": "2026-10-03", "days": 7, "p": 0.5, "hit": None}])
    assert s == {"since": "2026-10-01", "scored": 2, "brier": 0.04}

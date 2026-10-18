import sys
from datetime import date
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

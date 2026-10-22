"""발사 확률 백테스트 — 화면 숫자(지난 1년 빈도)를 무엇이 이기는지 판정한다. 수동 실행, 아무 파일도 쓰지 않는다.

사용: python3 scripts/forecast_backtest.py
  연도별 전진 검증(그해는 그 전 자료로만 학습) · 대상 = 기준일 다음 날부터 H일 안 발사(CSIS ∪ CNS, risk_calendar 와 같은 정의)
  비교: base = 지난 1년 빈도(화면 숫자, risk_calendar.year_rate) · model = 빈도·경과일·계절·훈련/기념일 로지스틱(최근 8년 학습)
        · cal = base 를 단조 보정(PAV, 최근 8년)
  판정 기준(10/22): 전체와 최근 3년 모두 base 보다 Brier 가 낮아야 화면 숫자를 바꾼다.
  10/22 결과: model 7일 +3.7%·30일 +6.1% 이지만 2023~25 30일 −22~−35%, cal 은 −5%·−2% → 둘 다 미통과, base 유지.
  임박 신호(state/signals.json)는 10/19부터 쌓여 과거 기록이 없다 — 종류별 20건이 쌓이면 특징으로 넣어 다시 돌린다.
"""
from __future__ import annotations

import bisect
import json
import math
import sys
from datetime import date, timedelta

import risk_calendar as rc

TEST_YEARS = range(rc.PROB_TEST_FROM, 2026)
TRAIN_FROM = 2006
TRAIN_SPAN = 8  # 최근 N년만 학습 — 2006~2013 저빈도 시기를 같은 무게로 배우면 2016년 이후를 못 따라간다
RECENT = {2023, 2024, 2025}


def _logit(p: float) -> float:
    p = min(max(p, 1e-3), 1 - 1e-3)
    return math.log(p / (1 - p))


def features(ds: list[date], anchors: list[tuple[str, date, date]], t: date, h: int, month_p: float) -> list[float]:
    k, n = rc.year_rate(ds, t, h)
    i = bisect.bisect_right(ds, t)
    gap = (t - ds[i - 1]).days if i else 3650
    n30 = i - bisect.bisect_right(ds, t - timedelta(30))
    a, b = t + timedelta(1), t + timedelta(h)
    ex = any(k_ == "exercise" and s <= b and e >= a for k_, s, e in anchors)
    an = any(k_ == "anniversary" and s <= b and e >= a for k_, s, e in anchors)
    return [1.0, _logit((k + .5) / (n + 1)), math.log1p(n30), math.log1p(gap), float(ex), float(an), _logit(month_p)]


def fit(X: list[list[float]], y: list[int], l2: float = 1.0) -> list[float]:
    """L2 로지스틱(뉴턴법, 순수 파이썬)."""
    k = len(X[0]); w = [0.0] * k
    for _ in range(50):
        g = [0.0] * k; H = [[0.0] * k for _ in range(k)]
        for x, v in zip(X, y):
            p = 1 / (1 + math.exp(-max(-30, min(30, sum(a * b for a, b in zip(w, x))))))
            for i in range(k):
                g[i] += (p - v) * x[i]
                for j in range(k):
                    H[i][j] += p * (1 - p) * x[i] * x[j]
        for i in range(1, k):
            g[i] += l2 * w[i]; H[i][i] += l2
        A = [r[:] + [gi] for r, gi in zip(H, g)]
        for c in range(k):
            pv = max(range(c, k), key=lambda r: abs(A[r][c])); A[c], A[pv] = A[pv], A[c]
            for r in range(k):
                if r != c and A[c][c]:
                    f = A[r][c] / A[c][c]; A[r] = [a - f * b for a, b in zip(A[r], A[c])]
        d = [A[i][k] / A[i][i] for i in range(k)]
        w = [a - b for a, b in zip(w, d)]
        if max(map(abs, d)) < 1e-7:
            break
    return w


def pav(xs: list[float], ys: list[int]) -> list[tuple[float, float]]:
    blocks: list[list[float]] = []
    for x, v in sorted(zip(xs, ys)):
        blocks.append([v, 1, x])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            s, n, xm = blocks.pop(); blocks[-1][0] += s; blocks[-1][1] += n; blocks[-1][2] = xm
    return [(b[2], b[0] / b[1]) for b in blocks]


def brier(rows: list[tuple[float, int]]) -> float:
    return sum((p - v) ** 2 for p, v in rows) / len(rows)


def run(ds: list[date], anchors, last: date, h: int) -> dict[str, list[tuple[date, float, int]]]:
    out: dict[str, list] = {"base": [], "model": [], "cal": []}
    for Y in TEST_YEARS:
        lo = max(TRAIN_FROM, Y - TRAIN_SPAN)
        tr_days = [date(lo, 1, 1) + timedelta(i) for i in range((date(Y, 1, 1) - date(lo, 1, 1)).days - h)]
        te_days = [t for t in (date(Y, 1, 1) + timedelta(i) for i in range(365)) if t + timedelta(h) <= last]
        yrs = range(lo, Y)
        mp = {m: (sum(1 for d in ds if d.year in yrs and d.month == m) + 1) / (sum(rc.month_len(y, m) for y in yrs) + 30)
              for m in range(1, 13)}
        X = [features(ds, anchors, t, h, mp[t.month]) for t in tr_days]
        y = [int(rc._hit(ds, t, h)) for t in tr_days]
        w = fit(X, y)
        base_tr = [rc.year_rate(ds, t, h) for t in tr_days]
        cal = pav([k / n for k, n in base_tr], y)
        for t in te_days:
            k, n = rc.year_rate(ds, t, h)
            v = int(rc._hit(ds, t, h))
            out["base"].append((t, k / n, v))
            z = sum(a * b for a, b in zip(w, features(ds, anchors, t, h, mp[t.month])))
            out["model"].append((t, 1 / (1 + math.exp(-z)), v))
            out["cal"].append((t, next((c for xm, c in cal if k / n <= xm), cal[-1][1]), v))
    return out


def main() -> int:
    doc = json.loads(rc.DOC.read_text())
    csis = rc.csis_events(rc.get(rc.CSIS).decode("utf-8", "replace"))
    ds = sorted(rc.launch_days(csis, doc.get("cns") or {}))
    last = date.fromisoformat(max(e["date"] for e in csis))
    anchors = [(a.get("kind"), s, e) for a in doc["anchors"] for v in rc.occurrences(a, range(2000, last.year + 1)).values()
               if v for s, e in [v]]
    for h in rc.PROB_HORIZONS:
        r = run(ds, anchors, last, h)
        b_all = brier([(p, v) for _, p, v in r["base"]])
        b_rec = brier([(p, v) for t, p, v in r["base"] if t.year in RECENT])
        print(f"\n== H={h}일 · n={len(r['base'])} · 실제 비율 {sum(v for *_, v in r['base']) / len(r['base']):.2f}")
        for k in ("model", "cal"):
            a = brier([(p, v) for _, p, v in r[k]]); c = brier([(p, v) for t, p, v in r[k] if t.year in RECENT])
            ok = a < b_all and c < b_rec
            print(f"  {k:5s} base 대비 전체 {1 - a / b_all:+.1%} · 2023~25 {1 - c / b_rec:+.1%} → {'통과' if ok else '미통과'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

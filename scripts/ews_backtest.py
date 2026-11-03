#!/usr/bin/env python3
"""EWS 재생 — 지금 산식으로 과거 회차를 다시 계산해 공식 단계와 맞는지, 단계 상승을 앞서는지 본다. 수동 실행, 아무 파일도 쓰지 않는다.

사용: python3 scripts/ews_backtest.py
  입력: state/latest.json 의 git 이력(회차별 실물). 얕은 클론이면 회차가 모자란다.
  판정 기준(PM 가설, forecast_backtest 의 신호 20건과 같은 수): 경계 진입 20건 전에는 「앞선다」고 쓰지 않는다.
  11/03 결과: 373회차 구간·단계 불일치 0. 주의 단계 D ≤5.8 은 228회차 중 24시간 안 경계 진입 0, D ≥7.7 은 64회차 중 30.
  그러나 경계 진입이 10/26·10/27 2건뿐이고 회차끼리 이어져 있어 근거가 못 된다.
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import diff_state as d  # noqa: E402
import ews  # noqa: E402

WINDOW = timedelta(hours=24)
MIN_ENTRIES = 20


def rounds() -> list[dict]:
    def git(*a):
        return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True).stdout
    out, seen = [], set()
    for h in reversed(git("log", "--format=%h", "--", "state/latest.json").split()):
        try:
            l = json.loads(git("show", f"{h}:state/latest.json"))
        except ValueError:
            continue
        items, at = l.get("items"), l.get("checked_at")
        if not isinstance(items, dict) or "grade" not in l or not at or at in seen:
            continue
        seen.add(at)
        for k in d.ITEMS:  # 항목이 늘기 전 회차
            items.setdefault(k, {"status": "none"})
        out.append({"at": at, "grade": l["grade"], "d": ews.score_d(items)})
    return out


def _t(r: dict) -> datetime:
    return datetime.fromisoformat(r["at"].replace("Z", "+00:00"))


def entries(rs: list[dict], grade: int = 2) -> list[str]:
    return [b["at"] for a, b in zip(rs, rs[1:]) if a["grade"] < grade <= b["grade"]]


def lead_table(rs: list[dict], grade: int, target: int, window: timedelta = WINDOW) -> dict:
    """grade 단계 회차를 D 값별로 세고, window 안에 target 단계에 든 회차를 함께 센다. {D: [회차, 그중 진입]}"""
    tab: dict = {}
    for i, r in enumerate(rs):
        if r["grade"] != grade:
            continue
        row = tab.setdefault(round(r["d"], 1), [0, 0])
        row[0] += 1
        row[1] += any(x["grade"] >= target and timedelta(0) < _t(x) - _t(r) <= window for x in rs[i + 1:])
    return dict(sorted(tab.items()))


def main() -> int:
    rs = rounds()
    bad = Counter()
    for r in rs:
        low, high, _ = ews.GRADE_SCALE[r["grade"]]
        v = ews.place(r["d"], r["grade"])
        bad[not low <= v <= high] += 1
    print(f"회차 {len(rs)} · 구간·단계 불일치 {bad[True]}")
    for g in sorted({r["grade"] for r in rs}):
        vals = [ews.place(r["d"], g) for r in rs if r["grade"] == g]
        print(f"단계 {g}: 지수 {min(vals)}~{max(vals)}")
    print("주의 단계 D별 [회차, 24시간 안 경계 진입 회차]", lead_table(rs, 1, 2))
    got = entries(rs)
    print(f"경계 진입 {len(got)}건 {[a[:16] for a in got]}")
    print("판정:", "표본 충족 — 다시 판단" if len(got) >= MIN_ENTRIES else f"표본 부족({len(got)}/{MIN_ENTRIES}) — 앞선다고 쓰지 않는다")
    return 0


if __name__ == "__main__":
    sys.exit(main())

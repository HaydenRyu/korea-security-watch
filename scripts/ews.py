#!/usr/bin/env python3
"""조기경보 지수(EWS) — 한반도 직접(D)과 세계 연동(E)을 합친 참고 지수. 공식 등급·알림과 무관하다.

  python3 scripts/ews.py [run.json]

  run.json 을 주면 run.linkage.theaters 를 state/theaters.json 에 먼저 쌓는다(루틴 2-2단계).
  diff_state.update_linkage 는 정해진 키만 다시 쓰므로 theaters 를 state/linkage.json 에 둘 수 없다.
  실패해도 rc 0 — 참고 지수가 회차를 막으면 안 된다. 실패는 stdout 의 ::warning:: 과 state/ews.json 의 error 로 남긴다.

  근거 없는 전역은 「평온」이 아니라 미관측이다. 미관측이 많거나 판정이 묵으면 0점 대신 보류한다.

  공식 단계와 어긋나지 않으면서 단계 안의 차이를 보여 준다.
  - α=1: E 는 표시만 하고 지수에 합치지 않는다. 대만 해협·러-우 사건 앞뒤 북한 발사는 과거 검정 6건 모두 「평소 수준」이다
    (data/linkage.json). α 는 Phase 3 백테스트를 통과한 뒤에만 낮춘다.
  - 단계 안 위치: 지수 구간은 공식 단계가 정하고, 값은 그 단계에서 D 가 가질 수 있는 최댓값 대비 위치다.
    D 그대로는 10개 항목 평균이라 공식 경계에서도 25 안팎이었다. 등급이 안 보는 정례 징후가 여기서 차이를 만든다.
    최댓값은 등급 규칙에서 나온 값이고 과거 사건에 맞춘 값이 아니다. 이 위치가 단계 상승을 앞서는지는 scripts/ews_backtest.py 로 본다.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
STATE = ROOT / "state"
WEIGHTS = ROOT / "data" / "ews_weights.json"

THEATERS = ("taiwan", "ukraine", "scs_ecs", "mideast", "us_posture")
CHANNELS = ("c1_us_gap", "c2_nk_ru_cn", "c3_simul", "c4_sea_energy", "c5_diplomacy")
CORE_ITEMS = {"nuclear", "troop_movement", "gov_warning"}  # 항목 점수 ×2
GRADE_POINT = {"none": 0, "single": 1, "corroborated": 2}
BANDS = ((75, "고위험 징후"), (50, "경계 징후"), (25, "관찰"), (0, "안정"))
D_POINTS_MAX = 26
# 공식 단계별 (구간 하한, 상한, 그 단계에서 가능한 D 점수 최대). 점수 최대는 항목 상태 5^10 전수 열거 결과(compute_grade 기준).
GRADE_SCALE = {0: (0, 24, 26), 1: (25, 49, 13), 2: (50, 74, 20), 3: (75, 100, 26)}
SOURCES_MAX = 5
SUMMARY_LEN = 200
ERROR_LEN = 200


def _dt(s) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _round(x: float) -> int:
    return math.floor(x + 0.5)


def band(ews: int) -> str:
    return next(name for low, name in BANDS if ews >= low)


def place(raw: float, grade) -> int:
    """반올림 전 값 → 공식 단계 구간 안의 지수. 단계를 모르면 값 그대로."""
    if grade not in GRADE_SCALE:
        return _round(raw)
    low, high, points = GRADE_SCALE[grade]
    return _round(low + (high - low) * min(raw / (points / D_POINTS_MAX * 100), 1))


def score_d(items: dict) -> float:
    """D = 항목 점수 합 / 최대 × 100. 등급점 × (routine 0.5) × (핵심 2). 항목이 빠지면 KeyError — 0점으로 넘기지 않는다."""
    import diff_state as d
    total = maximum = 0.0
    for key in d.ITEMS:
        core = 2 if key in CORE_ITEMS else 1
        it = items[key]
        total += GRADE_POINT[it["status"]] * (0.5 if d.is_routine(it) else 1) * core
        maximum += 2 * core
    return total / maximum * 100


def _observed(row, t_now: datetime, stale: timedelta) -> bool:
    if not isinstance(row, dict) or not any(isinstance(s, dict) and s.get("url") for s in row.get("sources") or []):
        return False
    at = _dt(row.get("judged_at"))  # 이번 회차에 안 내서 이어받은 전역이 묵으면 근거로 치지 않는다
    return at is None or t_now - at <= stale


def compute(latest: dict, doc: dict, weights: dict, now: str, last_good: dict | None = None) -> dict:
    t_now = _dt(now)
    stale = timedelta(hours=weights["stale_hours"])
    w, alpha = weights["weights"], weights["alpha"]
    d_raw = score_d(latest["items"])
    rows = doc.get("theaters") if isinstance(doc.get("theaters"), dict) else {}
    observed = {t: _observed(rows.get(t), t_now, stale) for t in THEATERS}

    judged_at, latest_at = doc.get("judged_at"), latest.get("checked_at")
    judged_dt, latest_dt = _dt(judged_at), _dt(latest_at)
    n_observed = sum(observed.values())
    world_short = (judged_dt is None or t_now - judged_dt > stale or n_observed < weights["min_observed_theaters"])
    latest_stale = latest_dt is None or t_now - latest_dt > stale
    withheld = latest_stale or (alpha < 1 and world_short)  # α=1 이면 세계 연동 공백이 지수를 막지 않는다

    denom = sum(w[t][c] for t in THEATERS for c in CHANNELS)

    def part(t: str, c: str) -> float:
        if not observed[t]:
            return 0.0
        row = rows[t]
        return row["state"] / 3 * w[t][c] if c in (row.get("channels") or []) else 0.0

    e_raw = sum(part(t, c) for t in THEATERS for c in CHANNELS) / denom * 100
    theaters = []
    for t in THEATERS:
        row = rows.get(t) if isinstance(rows.get(t), dict) else {}
        theaters.append({"key": t, "observed": observed[t], "state": row.get("state") if observed[t] else None,
                         "contribution": None if world_short else _round(sum(part(t, c) for c in CHANNELS) / denom * 100),
                         "channels": (row.get("channels") or []) if observed[t] else [],
                         "summary": row.get("summary", "") if observed[t] else "",
                         "sources": (row.get("sources") or []) if observed[t] else []})
    channels = [{"key": c, "observed": any(observed[t] and c in (rows[t].get("channels") or []) for t in THEATERS),
                 "value": None if world_short else _round(sum(part(t, c) for t in THEATERS) / denom * 100)}
                for c in CHANNELS]

    out = {"version": 1, "computed_at": now, "weights_version": weights["version"], "status": "withheld" if withheld else "ok",
           "ews": None, "band": None, "d": _round(d_raw), "e": None if world_short else _round(e_raw), "alpha": alpha,
           "grade": latest.get("grade"), "grade_label": latest.get("grade_label"),
           "coverage": {"observed_theaters": n_observed, "linkage_judged_at": judged_at, "latest_updated_at": latest_at},
           "theaters": theaters, "channels": channels, "last_good": last_good}
    if not withheld:
        ews = place(alpha * d_raw + (1 - alpha) * (0.0 if world_short else e_raw), latest.get("grade"))  # 반올림 전 값으로 합친다
        out.update(ews=ews, band=band(ews))
        out["last_good"] = {"ews": ews, "band": out["band"], "computed_at": now}
    return out


def error_doc(err: str, now: str, weights_version, last_good: dict | None) -> dict:
    return {"version": 1, "computed_at": now, "weights_version": weights_version, "status": "error", "ews": None, "band": None,
            "d": None, "e": None, "coverage": None, "theaters": [], "channels": [], "last_good": last_good,
            "error": err[:ERROR_LEN]}


def ingest(run: dict, doc: dict | None, now: str) -> dict:
    """run.linkage.theaters → state/theaters.json. 연 원문 없는 전역·모르는 값은 그것만 버리고, 안 낸 전역은 직전 값을 잇는다."""
    import diff_state as d
    doc = doc if isinstance(doc, dict) else {}
    rows = dict(doc.get("theaters")) if isinstance(doc.get("theaters"), dict) else {}
    raw = (run.get("linkage") or {}).get("theaters") if isinstance(run.get("linkage"), dict) else None
    opened, got = d._opened(run), False
    for t in THEATERS if isinstance(raw, dict) else ():
        row = raw.get(t)
        if not isinstance(row, dict) or type(row.get("state")) is not int or not 0 <= row["state"] <= 3:
            continue
        srcs = [{f: s[f] for f in ("name", "tier", "url", "published_at") if s.get(f)}
                for s in row.get("sources") or [] if isinstance(s, dict) and s.get("url") in opened][:SOURCES_MAX]
        if not srcs:  # state 0 도 근거가 있어야 관측이다
            continue
        summary = row.get("summary").strip()[:SUMMARY_LEN] if isinstance(row.get("summary"), str) else ""
        rows[t] = {"state": row["state"], "channels": [c for c in CHANNELS if c in (row.get("channels") or [])],
                   "sources": srcs, "summary": summary, "judged_at": now}
        got = True
    return {"schema_version": 2, "judged_at": now if got else doc.get("judged_at"), "theaters": rows}


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    last_good, weights_version = None, None
    try:
        last_good = _load(STATE / "ews.json").get("last_good")
    except Exception:  # noqa: BLE001 — 직전 값이 없거나 깨져도 이번 회차는 계산한다
        pass
    try:
        weights = _load(WEIGHTS)
        weights_version = weights["version"]
        theaters_path = STATE / "theaters.json"
        doc = _load(theaters_path) if theaters_path.exists() else {}
        if len(argv) > 1:
            doc = ingest(_load(Path(argv[1])), doc, now)
            _write(theaters_path, doc)
        out = compute(_load(STATE / "latest.json"), doc, weights, now, last_good)
    except Exception as e:  # noqa: BLE001 — 회차를 막지 않는다
        err = f"{type(e).__name__}: {e}"
        out = error_doc(err, now, weights_version, last_good)
        print(f"::warning::ews {err[:ERROR_LEN]}")
    try:
        _write(STATE / "ews.json", out)
    except OSError as e:
        print(f"::warning::ews {str(e)[:ERROR_LEN]}")
    print(json.dumps({k: out.get(k) for k in ("status", "ews", "band", "d", "e")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

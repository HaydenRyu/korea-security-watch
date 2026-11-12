#!/usr/bin/env python3
"""접경 열점을 사건 후보로 묶고 공개 사격 공지와 날짜를 대조한다 → state/obs_candidates.json.

판정 루틴(prompts/monitor.md 5절 0)의 보도 대조 입력이다. 등급·알림과 무관하고 원인을 정하지 않는다.
- 묶음: 접경(border) 열점을 firms 격자(CELL_DEG) 이웃 8칸 연결로 묶는다 — firms 의 「같은 열원」 기준과 같다.
  위성 통과마다 따로 찍힌 점이 한 사건으로 모인다(픽셀 수 ≠ 사건 수).
- 일정: 남측 점이 있는 후보만 육상 사격 공지(state/drills_kr.json)와 KST 날짜를 맞춘다. 남측 지자체 공지는 북측
  열점의 설명 후보가 아니다. 공지 장소는 텍스트 지명이라 위치는 대조하지 않는다(place_checked false) —
  same_day 는 「같은 날 공지가 있었다」일 뿐 원인이 아니다.
- 시각은 입력 파일의 fetched_at 만 쓴다(벽시계 없음) — 입력이 같으면 산출도 같아 빈 커밋이 생기지 않는다.
- first_seen = 그 후보를 처음 수집한 firms 회차. 격자가 겹치는 직전 후보에서 승계한다(48시간 창에서 앞 점이
  빠져 id 가 바뀌어도 유지).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import firms

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
OUT = STATE / "obs_candidates.json"
KST = timezone(timedelta(hours=9))
NOTE = ("접경 열점을 이웃 격자로 묶은 사건 후보. 원인을 정하지 않는다 — schedule.same_day 는 같은 날 공개 사격 공지가 "
        "있었다는 뜻이고 위치는 대조하지 않았다. 열점이 없다는 것은 사격·화재가 없었다는 근거가 아니다(위성 통과 시각·구름).")
POST_KEYS = ("source", "title", "url", "unit", "places")


def clusters(points: list[dict]) -> list[list[dict]]:
    """접경 점을 격자 이웃 연결 성분으로 묶는다. 반환 = 묶음별 점 목록(시각 오름차순), 묶음은 첫 시각 순."""
    by_cell: dict[str, list[dict]] = {}
    for p in points:
        if p.get("border"):
            by_cell.setdefault(firms.cell_key(p["lat"], p["lon"]), []).append(p)
    seen: set[str] = set()
    out = []
    for start in sorted(by_cell):
        if start in seen:
            continue
        seen.add(start)
        stack, group = [start], []
        while stack:
            k = stack.pop()
            group += by_cell[k]
            for n in firms._neighbors(k):
                if n in by_cell and n not in seen:
                    seen.add(n)
                    stack.append(n)
        out.append(sorted(group, key=lambda p: (p["t"], p["lat"], p["lon"])))
    return sorted(out, key=lambda g: (g[0]["t"], g[0]["lat"], g[0]["lon"]))


def side(group: list[dict]) -> str:
    """north 는 firms.annotate 가 붙인다 — 주석 실패 회차(키 없음)는 unknown."""
    vals = {p.get("north") for p in group}
    if None in vals:
        return "unknown"
    return "mixed" if len(vals) == 2 else ("north" if True in vals else "south")


def kst_days(group: list[dict]) -> list[str]:
    return sorted({datetime.fromisoformat(p["t"]).astimezone(KST).date().isoformat() for p in group})


def schedule(days: list[str], sd: str, drills: dict | None) -> dict:
    if sd == "north":
        return {"checked": False, "why": "북측 열점 — 남측 지자체 공지 대조 대상이 아니다", "same_day": []}
    if not drills or not isinstance(drills.get("posts"), list):
        return {"checked": False, "why": "사격 공지 파일 없음", "same_day": []}
    same = []
    for p in drills["posts"]:
        hit = sorted(set(days) & set(p.get("days") or []))
        if hit:
            same.append({k: p.get(k) for k in POST_KEYS} | {"days": hit})
    same.sort(key=lambda x: (x["days"][0], x.get("source") or "", x.get("url") or ""))
    return {"checked": True, "place_checked": False,
            "sources_ok": sorted(k for k, s in (drills.get("sources") or {}).items() if (s or {}).get("status") == "ok"),
            "same_day": same}


def first_seen(cells: set[str], prev: dict | None, default: str) -> str:
    olds = [c.get("first_seen") for c in (prev or {}).get("candidates") or []
            if c.get("first_seen") and cells & set(c.get("cells") or [])]
    return min(olds + [default])


def build(fdoc: dict, drills: dict | None, prev: dict | None) -> dict:
    at = fdoc["fetched_at"]
    cands = []
    for g in clusters(fdoc.get("points") or []):
        cells = sorted({firms.cell_key(p["lat"], p["lon"]) for p in g})
        sd = side(g)
        days = kst_days(g)
        kinds: dict[str, int] = {}
        for p in g:
            kinds[p.get("kind") or "none"] = kinds.get(p.get("kind") or "none", 0) + 1
        cands.append({
            "id": f"{firms.cell_key(g[0]['lat'], g[0]['lon'])}@{g[0]['t']}",
            "first_seen": first_seen(set(cells), prev, at),
            "first_t": g[0]["t"], "last_t": g[-1]["t"], "kst_days": days,
            "n": len(g), "passes": len({p["t"] for p in g}), "sats": sorted({p.get("sat") or "" for p in g}),
            "lat": round(sum(p["lat"] for p in g) / len(g), 3), "lon": round(sum(p["lon"] for p in g) / len(g), 3),
            "side": sd, "mdl_km_min": min(p["mdl_km"] for p in g), "frp_max": max(p.get("frp") or 0 for p in g),
            "kinds": dict(sorted(kinds.items())), "mil": sorted({p["mil"] for p in g if p.get("mil")}),
            "cells": cells, "schedule": schedule(days, sd, drills),
        })
    return {"version": 1, "computed_at": at, "firms_fetched_at": at,
            "drills_fetched_at": (drills or {}).get("fetched_at"), "window_h": fdoc.get("window_h"),
            "note": NOTE, "count": len(cands), "candidates": cands}


def _load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def main(argv: list[str]) -> int:
    def arg(name: str, default: Path) -> Path:
        return Path(argv[argv.index(name) + 1]) if name in argv else default

    out = arg("--out", OUT)
    fdoc = _load(arg("--firms", STATE / "firms.json"))
    if not fdoc or not fdoc.get("fetched_at"):
        print("state/firms.json 없음 — 건너뜀(직전 값 유지)")
        return 0
    doc = build(fdoc, _load(arg("--drills", STATE / "drills_kr.json")), _load(out))
    out.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps({"count": doc["count"], "same_day": sum(bool(c["schedule"]["same_day"]) for c in doc["candidates"]),
                      "computed_at": doc["computed_at"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

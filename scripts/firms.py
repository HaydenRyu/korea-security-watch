"""NASA FIRMS 열점(VIIRS 3기) → state/firms.json

  FIRMS_KEY=<MAP_KEY> python3 scripts/firms.py [--out state/firms.json]

- 범위: 한반도 + 인접(124~131E, 33~43N), 최근 48시간
- 접경 = 군사분계선(data/dmz.json mdl, OSM)에서 BORDER_KM 이내. 점마다 mdl_km(군사분계선까지 거리)를 남긴다.
  10/19 이전 근사 상자(37.6~38.9N)는 평양 남동 30km(MDL 약 90km)까지 「접경」으로 찍었다(hayden 지적).
- 열점은 들불·산불·공장 열원도 잡는다. 등급 판정의 단독 근거로 쓰지 않는다(prompts/monitor.md).
- 실패 시 기존 파일을 건드리지 않고 rc 3. 키가 없으면 rc 0(건너뜀).
- 유형 추정(10/19): 점마다 kind(fixed 상시 열원 추정 · burn 산불·소각 추정 · unknown 미상)와 근거(kind_why)를 붙인다.
  근거 = 같은 격자(0.005° ≈ 550m, 이웃 8칸 포함) 감지 일수(state/firms_cells.json 누적, CELL_KEEP_DAYS 보관)
  + OSM 시설(Overpass, 시설 경계 OSM_RADIUS_M 이내 · 격자별 OSM_TTL_DAYS 캐시 · 회차당 OSM_MAX_PER_RUN 칸).
  북측(data/nk_boundary.json, Natural Earth) 은 OSM 시설 자료가 희박해 「시설 없음」을 근거로 쓰지 않는다.
  추정이다 — 단정 표현을 쓰지 않는다. Overpass 실패는 유형만 비우고 열점 갱신은 그대로 한다.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{src}/124,33,131,43/2"
SOURCES = ["VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT"]
WINDOW_H = 48
# 접경지역 지원 특별법 시행령 지정 기준 「민통선과의 거리 20km 이내」(2024 개정안 · 뉴시스 2024-12-12)를 MDL 기준으로 근사.
# 민통선은 MDL 남쪽에 있어 법정 범위보다 좁다(보수적) — 북측에도 같은 거리를 쓴다
BORDER_KM = 20
_MDL = None
_NK = None

# ── 유형 추정 ────────────────────────────────────────────────────────────────
CELLS = ROOT / "state" / "firms_cells.json"
CELL_DEG = 0.005          # ≈ 550m(위도). VIIRS 375m 화소 + 지오로케이션 흔들림 — 이웃 8칸까지 같은 열원으로 본다
CELL_KEEP_DAYS = 14       # 지속성 판정에 쓰는 이력 길이(FIRMS area API 는 최대 10일 — 그보다 길게 남긴다)
FIXED_MIN_DAYS = 2        # 시설 근처에서 이 일수 이상이면 신뢰 「중」
REPEAT_MIN_DAYS = 3       # 시설 없이 이 일수 이상 반복되면 산불·소각으로 보지 않는다(→ 미상)
OSM_RADIUS_M = 750        # 시설 경계까지 거리. Overpass around 는 way 선분 기준 = 경계 거리. 375m 화소 2개 폭
OSM_TTL_DAYS = 30
OSM_MAX_PER_RUN = 20      # 공용 Overpass 부하 배려(10/19 실측: 25지점 한 질의는 502)
OSM_BUDGET_S = 120        # 회차당 Overpass 총 시간 상한 — firms 잡 timeout 5분 안에 끝낸다
OVERPASS = "https://overpass-api.de/api/interpreter"
UA = "korea-security-watch/1.0 (personal OSINT dashboard; github actions)"
KIND_LABEL = {"fixed": "상시 열원 추정", "burn": "산불·소각 추정", "unknown": "미상"}


def nk_polygons() -> list[list[list[tuple[float, float]]]]:
    global _NK
    if _NK is None:
        doc = json.loads((ROOT / "data" / "nk_boundary.json").read_text())
        _NK = [[[(float(a), float(b)) for a, b in ring] for ring in poly] for poly in doc["polygons"]]
    return _NK


def _in_ring(lat: float, lon: float, ring) -> bool:
    inside = False
    for (a1, b1), (a2, b2) in zip(ring, ring[1:] + ring[:1]):
        if (a1 > lat) != (a2 > lat) and lon < b1 + (lat - a1) * (b2 - b1) / (a2 - a1):
            inside = not inside
    return inside


def in_nk(lat: float, lon: float) -> bool:
    """북한 영역(Natural Earth 10m) 안인가 — 위경도 상자 금지(10/19: 랴오닝·지린 점이 북측으로 찍혔다)"""
    for poly in nk_polygons():
        if _in_ring(lat, lon, poly[0]) and not any(_in_ring(lat, lon, h) for h in poly[1:]):
            return True
    return False


def cell_key(lat: float, lon: float) -> str:
    return f"{round(lat / CELL_DEG)}:{round(lon / CELL_DEG)}"


def _neighbors(key: str) -> list[str]:
    i, j = (int(x) for x in key.split(":"))
    return [f"{i + a}:{j + b}" for a in (-1, 0, 1) for b in (-1, 0, 1)]


def update_cells(cells: dict, points: list[dict], today: str) -> dict:
    """감지 날짜(UTC)를 격자에 누적하고 CELL_KEEP_DAYS 밖 날짜·빈 격자를 지운다. cells 를 제자리 갱신해 돌려준다."""
    for p in points:
        c = cells.setdefault(cell_key(p["lat"], p["lon"]), {"lat": p["lat"], "lon": p["lon"], "dates": []})
        d = p["t"][:10]
        if d not in c["dates"]:
            c["dates"] = sorted(c["dates"] + [d])
    floor = (datetime.fromisoformat(today) - timedelta(days=CELL_KEEP_DAYS - 1)).date().isoformat()
    for k in list(cells):
        cells[k]["dates"] = [d for d in cells[k]["dates"] if d >= floor]
        if not cells[k]["dates"]:
            del cells[k]
    return cells


def days_seen(cells: dict, key: str) -> int:
    return len({d for n in _neighbors(key) for d in cells.get(n, {}).get("dates", [])})


def osm_facilities(lat: float, lon: float) -> list[str]:
    """반경 OSM_RADIUS_M 안 산업·발전 시설 이름(없으면 태그값). 실패는 예외로 올린다(호출부가 캐시 미기록)."""
    a = f"around:{OSM_RADIUS_M},{lat},{lon}"
    q = (f"[out:json][timeout:25];(nwr({a})[landuse=industrial];nwr({a})[power=plant];"
         f'nwr({a})[man_made~"^(works|kiln|flare|chimney)$"];);out tags 20;')
    req = urllib.request.Request(OVERPASS, data=urllib.parse.urlencode({"data": q}).encode(), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        els = json.load(r)["elements"]
    names = []
    for e in els:
        t = e.get("tags", {})
        n = t.get("name:ko") or t.get("name") or t.get("name:en") or t.get("power") or t.get("man_made") or t.get("landuse")
        if n and n not in names:
            names.append(n)
    return names[:5]


def enrich_osm(cells: dict, now: datetime, lookup=osm_facilities, limit: int = OSM_MAX_PER_RUN) -> int:
    """OSM 미조회·만료 격자를 최근 감지 순으로 limit 칸까지 조회. 첫 실패에서 멈춘다(그 칸은 미조회로 남음)."""
    stale = (now - timedelta(days=OSM_TTL_DAYS)).isoformat()
    todo = sorted((k for k, c in cells.items() if c.get("osm_checked", "") < stale),
                  key=lambda k: cells[k]["dates"][-1], reverse=True)
    done, t0 = 0, time.monotonic()
    for k in todo[:limit]:
        if time.monotonic() - t0 > OSM_BUDGET_S:
            print("overpass 시간 상한 — 다음 회차로")
            break
        try:
            cells[k]["osm"] = lookup(cells[k]["lat"], cells[k]["lon"])
        except Exception as e:  # noqa: BLE001 — Overpass 혼잡(429·502·504)·불통
            print(f"overpass 중단: {e}")
            break
        cells[k]["osm_checked"] = now.isoformat()
        done += 1
        time.sleep(1)
    return done


def classify(days: int, osm: list[str] | None, north: bool) -> tuple[str, str, list[str]]:
    """(kind, 신뢰 중·낮음, 근거[]) — 추정 라벨. osm None = 아직 조회 안 함"""
    why = [f"같은 자리 {days}일 감지(최근 {CELL_KEEP_DAYS}일)"]
    if osm:
        why.append(f"OSM 시설 {OSM_RADIUS_M}m 이내: {', '.join(osm[:3])}")
        return "fixed", ("중" if days >= FIXED_MIN_DAYS else "낮음"), why
    if osm is None:
        return "unknown", "낮음", why + ["OSM 시설 미조회"]
    if north:
        return "unknown", "낮음", why + ["북측은 OSM 시설 자료가 희박해 「시설 없음」으로 보지 않음"]
    if days >= REPEAT_MIN_DAYS:
        return "unknown", "낮음", why + [f"OSM 시설 없음 · {REPEAT_MIN_DAYS}일 이상 반복 — 산불·소각으로 보기 어려움"]
    return "burn", "낮음", why + [f"OSM 시설 {OSM_RADIUS_M}m 이내 없음"]


def annotate(points: list[dict], cells: dict) -> None:
    """점에 kind·kind_label·kind_conf·kind_why·days_seen·north·osm 를 붙인다(제자리)."""
    for p in points:
        k = cell_key(p["lat"], p["lon"])
        c = cells.get(k, {})
        osm = c.get("osm") if "osm_checked" in c else None
        north = in_nk(p["lat"], p["lon"])
        n = days_seen(cells, k)
        kind, conf, why = classify(n, osm, north)
        p.update({"days_seen": n, "north": north, "osm": osm or [], "kind": kind,
                  "kind_label": KIND_LABEL[kind], "kind_conf": conf, "kind_why": why})


def mdl_lines() -> list[list[tuple[float, float]]]:
    global _MDL
    if _MDL is None:
        _MDL = [[(float(a), float(b)) for a, b in line] for line in json.loads((ROOT / "data" / "dmz.json").read_text())["mdl"]]
    return _MDL


def mdl_km(lat: float, lon: float) -> float:
    """군사분계선(꺾은선)까지 최단 거리 km — 등장방형 근사(한반도 폭에서 오차 1% 미만)"""
    kx, ky = 111.32 * math.cos(math.radians(lat)), 110.57
    best = float("inf")
    for line in mdl_lines():
        for (a1, b1), (a2, b2) in zip(line, line[1:]):
            x1, y1, x2, y2 = (b1 - lon) * kx, (a1 - lat) * ky, (b2 - lon) * kx, (a2 - lat) * ky
            dx, dy = x2 - x1, y2 - y1
            t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / (dx * dx + dy * dy)))
            best = min(best, math.hypot(x1 + t * dx, y1 + t * dy))
    return best


def in_border(lat: float, lon: float) -> bool:
    return mdl_km(lat, lon) <= BORDER_KM


def fetch(key: str, src: str) -> list[dict]:
    for attempt in range(3):  # 러너 네트워크 일시 불통(Errno 101) 실측 — 짧게 재시도
        try:
            with urllib.request.urlopen(API.format(key=key, src=src), timeout=40) as r:
                text = r.read().decode()
            break
        except OSError:
            if attempt == 2:
                raise
            time.sleep(10)
    if not text.startswith("latitude"):
        raise RuntimeError(f"{src}: unexpected response {text[:120]!r}")
    return list(csv.DictReader(io.StringIO(text)))


def main(argv: list[str]) -> int:
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else ROOT / "state" / "firms.json"
    key = os.environ.get("FIRMS_KEY")
    if not key:
        print("FIRMS_KEY 없음 — 건너뜀")
        return 0
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=WINDOW_H)
    points = []
    try:
        for src in SOURCES:
            for r in fetch(key, src):
                t = datetime.strptime(f"{r['acq_date']} {int(r['acq_time']):04d}", "%Y-%m-%d %H%M").replace(tzinfo=timezone.utc)
                if t < cutoff:
                    continue
                lat, lon = float(r["latitude"]), float(r["longitude"])
                points.append({"lat": round(lat, 3), "lon": round(lon, 3), "t": t.isoformat(),
                               "frp": float(r.get("frp") or 0), "conf": r.get("confidence", ""),
                               "sat": src.split("_")[1], "mdl_km": round(mdl_km(lat, lon), 1),
                               "border": in_border(lat, lon)})
    except Exception as e:  # noqa: BLE001 — 실패는 rc 3 으로 알리고 기존 파일 유지
        print(f"FIRMS 실패: {e}")
        return 3
    points.sort(key=lambda p: p["t"], reverse=True)
    cells_path = Path(argv[argv.index("--cells") + 1]) if "--cells" in argv else CELLS
    cells = typed(points, cells_path, now, osm="--no-osm" not in argv)
    doc = {"fetched_at": now.isoformat(), "window_h": WINDOW_H, "sources": SOURCES,
           "border_km": BORDER_KM, "count": len(points), "border_count": sum(p["border"] for p in points),
           "kind_note": "kind 는 추정(감지 일수·OSM 시설 근거)이다. 단독 판정 근거로 쓰지 않는다",
           "kind_count": {k: sum(p.get("kind") == k for p in points) for k in KIND_LABEL},
           "points": points[:3000]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n")
    if cells is not None:
        cells_path.write_text(json.dumps({"updated_at": now.isoformat(), "cell_deg": CELL_DEG,
                                          "keep_days": CELL_KEEP_DAYS, "cells": cells},
                                         ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps({k: doc[k] for k in ("count", "border_count", "kind_count", "fetched_at")}, ensure_ascii=False))
    return 0


def typed(points: list[dict], cells_path: Path, now: datetime, osm: bool = True) -> dict | None:
    """격자 이력 누적 + OSM 보강 + 점 주석. 실패해도 열점 갱신은 막지 않는다(None = 이력 파일 미갱신)."""
    try:
        cells = json.loads(cells_path.read_text())["cells"] if cells_path.exists() else {}
        update_cells(cells, points, now.date().isoformat())
        if osm:
            print(f"overpass {enrich_osm(cells, now)}칸 조회")
        annotate(points, cells)
        return cells
    except Exception as e:  # noqa: BLE001
        print(f"유형 추정 실패(열점은 갱신): {e}")
        return None


if __name__ == "__main__":
    sys.exit(main(sys.argv))

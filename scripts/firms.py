"""NASA FIRMS 열점(VIIRS 3기) → state/firms.json

  FIRMS_KEY=<MAP_KEY> python3 scripts/firms.py [--out state/firms.json]

- 범위: 한반도 + 인접(124~131E, 33~43N), 최근 48시간
- 접경 = 군사분계선(data/dmz.json mdl, OSM)에서 BORDER_KM 이내. 점마다 mdl_km(군사분계선까지 거리)를 남긴다.
  10/19 이전 근사 상자(37.6~38.9N)는 평양 남동 30km(MDL 약 90km)까지 「접경」으로 찍었다(hayden 지적).
- 열점은 들불·산불·공장 열원도 잡는다. 등급 판정의 단독 근거로 쓰지 않는다(prompts/monitor.md).
- 실패 시 기존 파일을 건드리지 않고 rc 3. 키가 없으면 rc 0(건너뜀).
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import sys
import time
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
    doc = {"fetched_at": now.isoformat(), "window_h": WINDOW_H, "sources": SOURCES,
           "border_km": BORDER_KM, "count": len(points), "border_count": sum(p["border"] for p in points),
           "points": points[:3000]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps({k: doc[k] for k in ("count", "border_count", "fetched_at")}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

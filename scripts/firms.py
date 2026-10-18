"""NASA FIRMS 열점(VIIRS 3기) → state/firms.json

  FIRMS_KEY=<MAP_KEY> python3 scripts/firms.py [--out state/firms.json]

- 범위: 한반도 + 인접(124~131E, 33~43N), 최근 48시간
- 접경 구역은 근사 상자(37.6~38.9N, 125.9~128.7E)다. 정확한 군사분계선이 아니다.
- 열점은 들불·산불·공장 열원도 잡는다. 등급 판정의 단독 근거로 쓰지 않는다(prompts/monitor.md).
- 실패 시 기존 파일을 건드리지 않고 rc 3. 키가 없으면 rc 0(건너뜀).
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{src}/124,33,131,43/2"
SOURCES = ["VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT"]
WINDOW_H = 48
BORDER = (37.6, 38.9, 125.9, 128.7)  # lat_min, lat_max, lon_min, lon_max (근사)


def in_border(lat: float, lon: float) -> bool:
    return BORDER[0] <= lat <= BORDER[1] and BORDER[2] <= lon <= BORDER[3]


def fetch(key: str, src: str) -> list[dict]:
    with urllib.request.urlopen(API.format(key=key, src=src), timeout=40) as r:
        text = r.read().decode()
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
                               "sat": src.split("_")[1], "border": in_border(lat, lon)})
    except Exception as e:  # noqa: BLE001 — 실패는 rc 3 으로 알리고 기존 파일 유지
        print(f"FIRMS 실패: {e}")
        return 3
    points.sort(key=lambda p: p["t"], reverse=True)
    doc = {"fetched_at": now.isoformat(), "window_h": WINDOW_H, "sources": SOURCES,
           "border_box": BORDER, "count": len(points), "border_count": sum(p["border"] for p in points),
           "points": points[:3000]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps({k: doc[k] for k in ("count", "border_count", "fetched_at")}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

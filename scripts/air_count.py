"""한반도 상공 항공 지표 → state/air.json (+ 이력 state/air.jsonl)

  python3 scripts/air_count.py      # 키 불필요(adsb.lol 공개 API), GitHub Actions 매시간

쓸모: 위험이 커지면 항공사가 정부 발표보다 먼저 한국 운항을 줄이거나 우회한다. 그래서 「지금 대수」를 지난 7일
같은 시각(UTC 시) 중앙값과 비교한다. 군용기는 대부분 신호를 끄므로 「신호를 켠 군용기」만 기종과 함께 적는다.
주의: adsb.lol 은 자원봉사 수신기 망이라 수신기가 꺼져도 대수가 준다 — 등급 판단에 쓰지 않는 참고 지표다.
"""
from __future__ import annotations

import json
import statistics
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AREA = "https://api.adsb.lol/v2/lat/37.0/lon/127.5/dist/250"   # 남한 전역·북한 남부·서해·동해 일부
MIL = "https://api.adsb.lol/v2/mil"
BOX = (32.5, 43.5, 123.0, 132.5)    # 군용기 표시 범위 lat/lon
SOUTH = (33.0, 38.6, 124.5, 130.0)  # 남한 상공(근사) — 운항 지표의 본 값
KEEP_DAYS = 15
MIN_SAMPLES = 3                     # 같은 시각 표본이 이만큼 쌓여야 비교한다
UA = {"User-Agent": "jis-air-count/1 (github.com/HaydenRyu/korea-security-watch)"}
# 신호를 켜고 다니는 미군 정찰·지휘·급유기 — 보이면 이름을 붙여 보여준다(ICAO 기종 코드)
NAMED = {"RC35": "RC-135 정찰기", "E3TF": "E-3 조기경보기", "E3CF": "E-3 조기경보기", "E6": "E-6 지휘기", "P8": "P-8 초계기",
         "K35R": "KC-135 급유기", "KC46": "KC-46 급유기", "K46": "KC-46 급유기", "U2": "U-2 정찰기", "Q4": "RQ-4 무인정찰기",
         "GLEX": "정찰용 비즈니스젯 가능", "C17": "C-17 수송기", "C5M": "C-5 수송기", "C130": "C-130 수송기", "C30J": "C-130J 수송기"}


def get(url: str) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return json.loads(r.read())


def inside(a: dict, box: tuple) -> bool:
    lat, lon = a.get("lat"), a.get("lon")
    return lat is not None and lon is not None and box[0] <= lat <= box[1] and box[2] <= lon <= box[3]


def main() -> int:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        area, mil = get(AREA)["ac"], get(MIL)["ac"]
    except (OSError, ValueError, KeyError) as e:
        print(f"adsb.lol 실패 — 기존 파일 유지: {e}")
        return 0
    airborne = [a for a in area if a.get("alt_baro") not in (None, "ground")]
    south = sum(1 for a in airborne if inside(a, SOUTH))
    mil_here = [{"type": a.get("t") or "?", "name": NAMED.get(a.get("t") or ""), "call": (a.get("flight") or "").strip(),
                 "lat": round(a["lat"], 2), "lon": round(a["lon"], 2)} for a in mil if inside(a, BOX)]

    hist_p = ROOT / "state" / "air.jsonl"
    hist = [json.loads(l) for l in hist_p.read_text().splitlines() if l.strip()] if hist_p.exists() else []
    cut = now - timedelta(days=KEEP_DAYS)
    hist = [h for h in hist if datetime.fromisoformat(h["at"]) > cut]
    week = now - timedelta(days=7, minutes=30)
    same = [h["south"] for h in hist if datetime.fromisoformat(h["at"]) > week
            and datetime.fromisoformat(h["at"]).hour == now.hour]
    base = statistics.median(same) if len(same) >= MIN_SAMPLES else None
    hist.append({"at": now.isoformat(), "south": south, "area": len(airborne), "mil": len(mil_here)})
    hist_p.write_text("".join(json.dumps(h) + "\n" for h in hist))

    doc = {"at": now.isoformat(), "south": south, "area": len(airborne), "baseline": base,
           "ratio": round(south / base, 2) if base else None, "samples": len(same),
           "mil": mil_here, "source": "adsb.lol (ADS-B 공개 수신망)"}
    (ROOT / "state" / "air.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({k: doc[k] for k in ("south", "area", "baseline", "ratio", "samples")} | {"mil": len(mil_here)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""국가교통정보센터 CCTV 목록 → data/cctv.json (접경 권역별 최북단 카메라 이름·좌표만)

  ITS_KEY=<인증키> python3 scripts/its_cctv.py

영상 주소는 저장하지 않는다 — 재생 주소의 서명에 인증키가 들어 있어 공개 페이지에 노출된다.
대시보드는 카메라 위치만 보여주고, 재생은 국가교통정보센터 공식 지도로 보낸다.
카메라 위치는 거의 바뀌지 않으므로 수동 갱신(월 1회 정도)이면 충분하다.
"""
from __future__ import annotations

import json
import math
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ("https://openapi.its.go.kr:9443/cctvInfo?apiKey={key}&type={t}&cctvType=4"
       "&minX=126.2&maxX=128.7&minY=37.7&maxY=38.9&getType=json")  # 남쪽 끝 37.7 = 강화 북단(한강 하구 접경) · 동해안 속초(38.2)까지 들어온다
# 서→동 경도 구간(접경을 빈틈없이 덮기 위한 분할, 행정구역 아님) · 구간마다 가장 북쪽 카메라 n 대
# 경기북부는 군 단위로 잘게(강화·김포·파주 서부=자유로·임진각·파주 적성=국도37·연천) — 넓게 묶으면 위도가 높은 철원이 연천 몫을 가져간다
# 10/18 hayden 「지점 수 자체를 늘리기(접경·해안 도로)」 → 구간당 대수를 늘리고 서로 3km 이상 떨어진 것만(접경을 더 촘촘히).
# 동해안(128.3~)은 고성 북단~속초까지 해안 국도7을 따라 내려오게 대수를 크게. 한 회차 ITS 호출 수는 그대로(도로 종류별 1회)
REGIONS = [(126.2, 126.53, 4), (126.53, 126.66, 2), (126.66, 126.8, 4), (126.8, 127.0, 4), (127.0, 127.16, 3),
           (127.16, 127.25, 3), (127.25, 127.6, 4), (127.6, 128.0, 4), (128.0, 128.3, 4), (128.3, 128.7, 6)]
MIN_GAP_KM = 3


def km(a: dict, b: dict) -> float:
    dy = (a["lat"] - b["lat"]) * 111
    dx = (a["lon"] - b["lon"]) * 111 * math.cos(math.radians(a["lat"]))
    return math.hypot(dx, dy)


def main() -> int:
    key = os.environ.get("ITS_KEY")
    if not key:
        print("ITS_KEY 없음")
        return 2
    cams = []
    for t in ("its", "ex"):  # its = 국도, ex = 고속도로
        with urllib.request.urlopen(API.format(key=key, t=t), timeout=40) as r:
            data = json.loads(r.read())["response"].get("data") or []
        data = data if isinstance(data, list) else [data]  # 1건이면 dict, 0건이면 키 없음
        cams += [{"name": d["cctvname"].strip(), "lat": round(float(d["coordy"]), 4),
                  "lon": round(float(d["coordx"]), 4), "road": t} for d in data]
    picked = []
    for lo, hi, n in REGIONS:
        inside = sorted((c for c in cams if lo <= c["lon"] < hi), key=lambda c: -c["lat"])
        took = []
        for c in inside:  # 북쪽부터, 이미 고른 카메라와 MIN_GAP_KM 안이면 건너뛴다(같은 교차로 2대 중복 방지)
            if len(took) >= n:
                break
            if all(km(c, t) >= MIN_GAP_KM for t in took):
                took.append(c)
        picked += took
    doc = {"fetched_at": datetime.now(timezone.utc).isoformat(), "source": "국가교통정보센터",
           "play_url": "https://www.its.go.kr/map/cctv", "cameras": picked}
    out = ROOT / "data" / "cctv.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({"total": len(cams), "picked": len(picked)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

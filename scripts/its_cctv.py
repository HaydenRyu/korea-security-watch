"""국가교통정보센터 CCTV 목록 → data/cctv.json (접경 권역별 최북단 카메라 이름·좌표만)

  ITS_KEY=<인증키> python3 scripts/its_cctv.py

영상 주소는 저장하지 않는다 — 재생 주소의 서명에 인증키가 들어 있어 공개 페이지에 노출된다.
대시보드는 카메라 위치만 보여주고, 재생은 국가교통정보센터 공식 지도로 보낸다.
카메라 위치는 거의 바뀌지 않으므로 수동 갱신(월 1회 정도)이면 충분하다.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ("https://openapi.its.go.kr:9443/cctvInfo?apiKey={key}&type={t}&cctvType=4"
       "&minX=126.2&maxX=128.7&minY=37.7&maxY=38.9&getType=json")  # 남쪽 끝 37.7 = 강화 북단(한강 하구 접경)
# 서→동 경도 구간(접경을 고르게 덮기 위한 분할, 행정구역 아님). 구간마다 가장 북쪽 카메라 PER_REGION 대
REGIONS = [(126.2, 126.6), (126.6, 126.8), (126.8, 126.95),  # 강화·김포 / 파주 서부(자유로·임진각) / 파주 적성
            (126.95, 127.25), (127.25, 127.6), (127.6, 128.0), (128.0, 128.3), (128.3, 128.7)]
PER_REGION = 2


def main() -> int:
    key = os.environ.get("ITS_KEY")
    if not key:
        print("ITS_KEY 없음")
        return 2
    cams = []
    for t in ("its", "ex"):  # its = 국도, ex = 고속도로
        with urllib.request.urlopen(API.format(key=key, t=t), timeout=40) as r:
            data = json.loads(r.read())["response"]["data"]
        cams += [{"name": d["cctvname"].strip(), "lat": round(float(d["coordy"]), 4),
                  "lon": round(float(d["coordx"]), 4), "road": t} for d in data]
    picked = []
    for lo, hi in REGIONS:
        inside = sorted((c for c in cams if lo <= c["lon"] < hi), key=lambda c: -c["lat"])
        picked += inside[:PER_REGION]
    doc = {"fetched_at": datetime.now(timezone.utc).isoformat(), "source": "국가교통정보센터",
           "play_url": "https://www.its.go.kr/map/cctv", "cameras": picked}
    out = ROOT / "data" / "cctv.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({"total": len(cams), "picked": len(picked)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

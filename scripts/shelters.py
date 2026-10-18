"""국민재난안전포털 민방위 대피소 → data/shelters.json (세곡·자곡·율현동 + 자곡동 반경 RADIUS_M 안, 거리순)

  python3 scripts/shelters.py

포털 대피소 지도가 쓰는 화면용 엔드포인트(키 불필요)라 예고 없이 바뀔 수 있다 — 실패하면 기존 파일을 둔다.
반경 2km 가 걸치는 시군구 전량을 받아 거리로 거른다. 수용인원(useCanStfCnt)은 대부분 면적(㎡)과 같은 값이라
인원으로 단정할 수 없어 싣지 않는다(2026-10-18 실측: 5개 구 609건 중 다른 값 22건).
"""
from __future__ import annotations

import json
import math
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = "https://www.safekorea.go.kr/safekorea-kor/flsm/flsm/facilityDataList.do"
CENTER = (37.474, 127.103)  # 자곡동
RADIUS_M = 2000
DONGS = ("세곡동", "자곡동", "율현동")  # 주소에 이 동이 있으면 반경 밖이어도 넣는다(2026-10-18 hayden)
SGG = {"11680": "강남구", "11710": "송파구", "11650": "서초구", "41131": "성남 수정구", "41133": "성남 중원구"}


def dist_m(a: float, b: float, c: float, d: float) -> float:
    p1, p2 = math.radians(a), math.radians(c)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(d - b) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def fetch(sgg: str) -> list[dict]:
    body = urllib.parse.urlencode({"tableNm": "TFK_CIVIL_EMR_FACL_TEMP", "tableKorNm": "민방위대피소",
                                   "sggCd": sgg, "page": 1, "size": 500}).encode()
    req = urllib.request.Request(URL, data=body, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=40) as r:
        d = json.loads(r.read())
    rows = d.get("list") or []
    if len(rows) != d.get("totalCnt"):
        raise RuntimeError(f"{sgg}: {len(rows)}/{d.get('totalCnt')}건 — 일부만 받음")
    return rows


def main() -> int:
    out, seen = [], set()
    for sgg in SGG:
        for r in fetch(sgg):
            try:
                lat = r["facilLade"] + r["facilLami"] / 60 + r["facilLase"] / 3600
                lon = r["facilLode"] + r["facilLomi"] / 60 + r["facilLose"] / 3600
            except (KeyError, TypeError):
                continue
            m = dist_m(*CENTER, lat, lon)
            jibun, road = r.get("facilAddr") or "", r.get("facilRdAddr") or ""
            # 법정동은 지번 주소가 정확하다(도로명 괄호의 동은 건물명일 때가 있다)
            dong = next((d for d in DONGS if f" {d} " in f" {jibun} "), None) or next((d for d in DONGS if d in road), None)
            if (m <= RADIUS_M or dong) and r.get("facilCode") not in seen:
                seen.add(r.get("facilCode"))
                out.append({"name": r["facilNm"].strip(), "addr": (r.get("facilRdAddr") or r.get("facilAddr") or "").strip(),
                            "lat": round(lat, 6), "lon": round(lon, 6), "area_m2": r.get("facilPow"),
                            "open": r.get("opnYn") == "Y", "dist_m": round(m), "dong": dong})
    out.sort(key=lambda x: x["dist_m"])
    doc = {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": "국민재난안전포털 민방위 대피소",
           "center": {"name": "자곡동", "lat": CENTER[0], "lon": CENTER[1]}, "radius_m": RADIUS_M, "dongs": list(DONGS),
           "portal": "https://www.safekorea.go.kr/safekorea-kor/flsm/flsm/facilitiesSafteyMap.do?menuSn=2&title=민방위대피소",
           "shelters": out}
    (ROOT / "data" / "shelters.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({"count": len(out), "nearest_m": out[0]["dist_m"] if out else None}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""항행경보 → 발사 예고 후보 JSON

상태(2026-10-19): 워크플로 미연결 — 일본 해상보안청 海しる(MSIL) 개별 키를 받은 뒤 firms.yml 에 붙인다.
  국내 KHOA 경보(억제 입력)는 scripts/navwarn_kr.py 가 firms.yml 에서 매시 state/navwarn_kr.json 으로 받는다.
NGA 공개 API 는 2024-05-10 이후 갱신이 없어 실시간 소스가 아니다(회귀 기준 자료로만 쓴다: tests/fixtures).
조사 경위 = _sea_sat_research.md

입력(오프라인 파일만 읽는다 — 네트워크 호출 없음):
  --nga  <NGA msi broadcast-warn JSON>   (msi.nga.mil /api/publications/broadcast-warn 응답 형식)
  --msil <海しる navigational-warnings GeoJSON>  (api.msil.go.jp/navigational-warnings/v2/MapServer/{n}/query 응답)
  --khoa <KHOA getNavigationalWarningInfo XML>   (apis.data.go.kr/1192136/NavigationalWarning 응답)
  --kr   <state/navwarn_kr.json>                 (scripts/navwarn_kr.py 산출 — 한국군 사격훈련 오탐 억제, 없으면 억제 없이 동작)
출력: JSON 배열 [{id, source, issued, window_from, window_to, areas, points, text_excerpt, url,
                  korea_related, launch_related, exercise_related, dprk_pattern, flags}]

판정
- korea_related : 경보 좌표 중 하나라도 KOREA_BOX(한반도·황해·동해·대한해협) 안
- launch_related: 로켓·미사일·위성·우주잔해 키워드 (영·일·한)
- exercise_related: 사격·훈련 키워드 — launch 와 별 축(오탐 주범)
- dprk_pattern  : launch_related ∧ 황해 낙하구역(YELLOW_BOX) ∧ 필리핀 동방 낙하구역(LUZON_E_BOX) 동시 존재
                   = 2023-05/08/11 북한 위성 발사 통보(NAVAREA XI 187/23·288/23·408/23)의 공통 형상.
  경보 본문에는 「북한」이 나오지 않는다(발신 당국 미기재) — 형상으로만 식별된다.
- kr_exercise_overlap / dprk_candidate (--kr 를 줄 때):
    발사 관련 ∧ 한반도 경보의 한반도 구역마다 같은 시간·같은 구역(다각형 겹침)의 한국군 사격·훈련 경보(KHOA)가 있으면
    그 구역은 한국군 훈련으로 설명된다 → 한반도 구역 전부가 설명되면 dprk_candidate=False(오탐 억제).
    경보 기간을 못 읽은 경보는 시간 대조가 안 되므로 억제하지 않는다(kr_check="time_unknown").
    --kr 가 없거나 KHOA 자료가 없으면 dprk_candidate = dprk_pattern 그대로(kr_check="no_input").
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# (lat_min, lat_max, lon_min, lon_max)
KOREA_BOX = (32.0, 43.5, 119.5, 134.0)
YELLOW_BOX = (32.5, 37.5, 121.5, 125.5)     # 2023 북한 1단 낙하구역 A·B(35-36N/123E, 33-34N/123E) 포함
LUZON_E_BOX = (9.0, 17.0, 126.5, 132.0)     # 2023 북한 2단 낙하구역 C(11-15N/128-130E) 포함

LAUNCH_RE = re.compile(r"ROCKET|MISSILE|SATELLITE|SPACE\s+DEBRIS|SPACE\s+LAUNCH|ロケット|ミサイル|人工衛星|宇宙|落下物|"
                       r"로켓|미사일|위성|발사체|낙하물", re.I)
EXERCISE_RE = re.compile(r"GUNNERY|FIRING|LIVE\s+FIRE|MILITARY\s+EXERCISE|NAVAL\s+EXERCISE|射撃|演習|訓練|사격|훈련", re.I)
MON = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}


def norm(t: str) -> str:
    t = unicodedata.normalize("NFKC", t or "")
    return t.replace("−", "-").replace("–", "-").replace("ー", "-")


COORD_EN = re.compile(r"(\d{1,2})-(\d{1,2}(?:\.\d+)?)([NS])\s+(\d{1,3})-(\d{1,2}(?:\.\d+)?)([EW])")
COORD_JA = re.compile(r"北緯\s*(\d{1,2})度\s*(\d{1,2}(?:\.\d+)?)分(?:\s*(\d{1,2}(?:\.\d+)?)秒)?\s*東経\s*(\d{1,3})度\s*(\d{1,2}(?:\.\d+)?)分(?:\s*(\d{1,2}(?:\.\d+)?)秒)?")
COORD_KO = re.compile(r"(\d{1,2})-(\d{1,2})-(\d{1,2}(?:\.\d+)?)\s*([NS])\s*,?\s*(\d{1,3})-(\d{1,2})-(\d{1,2}(?:\.\d+)?)\s*([EW])")


def coords(text: str) -> list[tuple[float, float]]:
    t = norm(text)
    out = []
    for m in COORD_KO.finditer(t):
        la = int(m[1]) + int(m[2]) / 60 + float(m[3]) / 3600
        lo = int(m[5]) + int(m[6]) / 60 + float(m[7]) / 3600
        out.append((la * (-1 if m[4] == "S" else 1), lo * (-1 if m[8] == "W" else 1)))
    if not out:
        for m in COORD_EN.finditer(t):
            la = int(m[1]) + float(m[2]) / 60
            lo = int(m[4]) + float(m[5]) / 60
            out.append((la * (-1 if m[3] == "S" else 1), lo * (-1 if m[6] == "W" else 1)))
    for m in COORD_JA.finditer(t):
        la = int(m[1]) + float(m[2]) / 60 + float(m[3] or 0) / 3600
        lo = int(m[4]) + float(m[5]) / 60 + float(m[6] or 0) / 3600
        out.append((la, lo))
    return [(round(a, 4), round(b, 4)) for a, b in out]


def areas(text: str) -> list[list[tuple[float, float]]]:
    """「A. … B. … C. …」 구역 단위로 묶는다(없으면 전체 1구역)."""
    t = norm(text)
    parts = re.split(r"\n\s*[A-Z]\.\s", t)
    got = [coords(p) for p in parts[1:]] if len(parts) > 1 else []
    got = [g for g in got if g]
    return got or ([coords(t)] if coords(t) else [])


def in_box(p, box) -> bool:
    return box[0] <= p[0] <= box[1] and box[2] <= p[1] <= box[3]


def parse_window(text: str, issued: datetime | None) -> tuple[str | None, str | None]:
    """'211500Z TO 301500Z NOV' · '301500Z MAY TO 101500Z JUN' · '250854Z TO 251029Z MAY' (DTG, UTC)."""
    t = norm(text)
    m = re.search(r"(\d{2})(\d{2})(\d{2})Z(?:\s+([A-Z]{3}))?(?:\s+(\d{2,4}))?\s+TO\s+(\d{2})(\d{2})(\d{2})Z\s+([A-Z]{3})(?:\s+(\d{2,4}))?", t)
    if not m:
        return None, None
    year = issued.year if issued else datetime.now(timezone.utc).year

    def mk(d, h, mi, mon, yr):
        y = int(yr) + (2000 if yr and len(yr) == 2 else 0) if yr else year
        return datetime(y, MON[mon], int(d), int(h), int(mi), tzinfo=timezone.utc)
    try:
        b = mk(m[6], m[7], m[8], m[9], m[10])
        a = mk(m[1], m[2], m[3], m[4] or m[9], m[5] or m[10])
        if a > b:  # 'TO' 가 연말을 넘는 경우
            a = a.replace(year=a.year - 1)
        return a.isoformat(), b.isoformat()
    except (KeyError, ValueError):
        return None, None


def dtg(s: str) -> datetime | None:
    m = re.match(r"(\d{2})(\d{2})(\d{2})Z ([A-Z]{3}) (\d{4})", s or "")
    if not m:
        return None
    return datetime(int(m[5]), MON[m[4]], int(m[1]), int(m[2]), int(m[3]), tzinfo=timezone.utc)


def classify(rec: dict, text: str) -> dict:
    ar = areas(text)
    pts = [p for a in ar for p in a]
    launch = bool(LAUNCH_RE.search(norm(text)))
    exercise = bool(EXERCISE_RE.search(norm(text)))
    yellow = any(in_box(p, YELLOW_BOX) for p in pts)
    luzon = any(in_box(p, LUZON_E_BOX) for p in pts)
    rec.update({
        "areas": ar, "points": len(pts),
        "korea_related": any(in_box(p, KOREA_BOX) for p in pts),
        "launch_related": launch, "exercise_related": exercise,
        "dprk_pattern": launch and yellow and luzon,
        "flags": [f for f, v in (("yellow_sea_area", yellow), ("luzon_east_area", luzon)) if v],
        "text_excerpt": re.sub(r"\s+", " ", norm(re.sub(r"<[^>]+>", " ", text)))[:400],
    })
    return rec


def _in_poly(p, poly) -> bool:
    """(lat, lon) 이 다각형 안인가 — 반직선 교차."""
    y, x, inside = p[0], p[1], False
    for i in range(len(poly)):
        (y1, x1), (y2, x2) = poly[i - 1], poly[i]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _cross(a, b, c, d) -> bool:
    def o(p, q, r):
        v = (q[1] - p[1]) * (r[0] - p[0]) - (q[0] - p[0]) * (r[1] - p[1])
        return (v > 0) - (v < 0)
    return o(a, b, c) * o(a, b, d) < 0 and o(c, d, a) * o(c, d, b) < 0


def shapes_overlap(a, b) -> bool:
    """두 구역(꼭짓점 목록, 1~2점이면 점·선)이 겹치는가 — bbox → 꼭짓점 포함 → 변 교차."""
    if not a or not b:
        return False
    if max(p[0] for p in a) < min(p[0] for p in b) or max(p[0] for p in b) < min(p[0] for p in a) or \
       max(p[1] for p in a) < min(p[1] for p in b) or max(p[1] for p in b) < min(p[1] for p in a):
        return False
    if len(b) >= 3 and any(_in_poly(p, b) for p in a):
        return True
    if len(a) >= 3 and any(_in_poly(p, a) for p in b):
        return True
    ea = list(zip(a, a[1:] + a[:1])) if len(a) >= 3 else list(zip(a, a[1:]))
    eb = list(zip(b, b[1:] + b[:1])) if len(b) >= 3 else list(zip(b, b[1:]))
    return any(_cross(p, q, r, s) for p, q in ea for r, s in eb)


def kr_exercises(kr: dict | None) -> list[dict]:
    """state/navwarn_kr.json → 사격·훈련 경보 구역 [{doc_num, area, name, polygon, from, to}] (기간별 1행)."""
    out = []
    for d in (kr or {}).get("warnings", []):
        if not d.get("exercise"):
            continue
        for a in d.get("areas", []):
            poly = [tuple(p) for p in a.get("polygon", [])]
            for w in a.get("windows", []):
                if poly:
                    out.append({"doc_num": d.get("doc_num"), "area": a.get("area"), "name": a.get("name"),
                                "polygon": poly, "from": w["from"], "to": w["to"]})
    return out


def apply_kr(recs: list[dict], kr: dict | None) -> list[dict]:
    """같은 시간·구역 한국군 사격훈련으로 설명되는 발사 경보를 「북한 발사 통보 후보」에서 뺀다(제자리)."""
    ex = kr_exercises(kr)
    src = "no_input" if not kr or not kr.get("warnings") and kr.get("status") != "ok" else \
        ("ok" if kr.get("status") == "ok" else f"stale:{kr.get('status')}")
    for r in recs:
        r["kr_exercise_overlap"], r["kr_check"] = [], src
        r["dprk_candidate"] = bool(r.get("dprk_pattern"))
        if src == "no_input" or not (r.get("launch_related") and r.get("korea_related")):
            continue
        if not (r.get("window_from") and r.get("window_to")):
            r["kr_check"] = "time_unknown"
            continue
        rf, rt = datetime.fromisoformat(r["window_from"]), datetime.fromisoformat(r["window_to"])
        kareas = [a for a in r.get("areas", []) if any(in_box(p, KOREA_BOX) for p in a)]
        explained = 0
        for a in kareas:
            hits = [e for e in ex if shapes_overlap([tuple(p) for p in a], e["polygon"])
                    and datetime.fromisoformat(e["from"]) <= rt and rf <= datetime.fromisoformat(e["to"])]
            r["kr_exercise_overlap"] += [f"{e['doc_num']}/{e['area']} {e['name']}" for e in hits]
            explained += bool(hits)
        if kareas and explained == len(kareas):
            r["dprk_candidate"] = False
    return recs


def _load(path: str):
    with (gzip.open(path, "rt", encoding="utf-8") if str(path).endswith(".gz") else open(path, encoding="utf-8")) as fh:
        return json.load(fh)


def from_nga(path: str) -> list[dict]:
    out = []
    for x in _load(path)["broadcast-warn"]:
        iss = dtg(x.get("issueDate", ""))
        wf, wt = parse_window(x["text"], iss)
        rec = {"id": f"{x['navArea']}-{x['msgNumber']}/{x['msgYear']}", "source": "NGA",
               "relay_of": (x.get("authority") or "").strip(), "issued": iss.isoformat() if iss else None,
               "window_from": wf, "window_to": wt,
               "url": "https://msi.nga.mil/NavWarnings"}
        out.append(classify(rec, x["text"]))
    return out


def from_msil(path: str) -> list[dict]:
    out = []
    for f in _load(path).get("features", []):
        p = f.get("properties", {})
        desc = p.get("description", "")
        m = re.search(r"日本航行警報</a>：(\S+)|番号(\d+)", desc)
        iss = re.search(r"発表日時：(\d{4})年(\d{2})月(\d{2})日(\d{2})時", desc)
        issued = (datetime(int(iss[1]), int(iss[2]), int(iss[3]), int(iss[4])).isoformat() + "+09:00") if iss else None
        geom = f.get("geometry") or {}
        rec = {"id": f"JCG-{(m[1] or m[2]) if m else p.get('name')}", "source": "JCG/MSIL",
               "issued": issued, "window_from": None, "window_to": None,  # 일문 기간 표기는 별도 파서 필요(TODO)
               "url": "https://www.msil.go.jp/", "name": p.get("name")}
        rec = classify(rec, p.get("name", "") + "\n" + desc)
        if not rec["points"] and geom.get("coordinates"):  # 본문 좌표가 없으면 지오메트리 사용
            cs = geom["coordinates"]
            flat = [cs] if geom["type"] == "Point" else (cs if geom["type"] == "LineString" else [c for ring in cs for c in ring])
            pts = [(round(c[1], 4), round(c[0], 4)) for c in flat]
            rec["areas"], rec["points"] = [pts], len(pts)
            rec["korea_related"] = any(in_box(q, KOREA_BOX) for q in pts)
        out.append(rec)
    return out


def from_khoa(path: str) -> list[dict]:
    out = []
    for it in ET.parse(path).getroot().iter("item"):
        g = {c.tag: (c.text or "") for c in it}
        text = " ".join(g.get(k, "") for k in ("title", "content", "basic", "noti_cat", "app_cat"))
        rec = {"id": f"KHOA-{g.get('doc_num')}", "source": "KHOA", "issued": g.get("date") or None,
               "window_from": None, "window_to": None, "url": "https://www.khoa.go.kr/",
               "note": "현황 API 에는 좌표가 없다 — 상세 API(doc_num·area) 2차 호출 필요"}
        out.append(classify(rec, text))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nga", action="append", default=[])
    ap.add_argument("--msil", action="append", default=[])
    ap.add_argument("--khoa", action="append", default=[])
    ap.add_argument("--kr", help="state/navwarn_kr.json — 없거나 못 읽으면 억제 없이 동작")
    ap.add_argument("--only", choices=["launch", "korea", "dprk", "candidate", "all"], default="launch")
    a = ap.parse_args(argv)
    recs = [r for p in a.nga for r in from_nga(p)] + [r for p in a.msil for r in from_msil(p)] + \
           [r for p in a.khoa for r in from_khoa(p)]
    kr = None
    if a.kr:
        try:
            kr = _load(a.kr)
        except (OSError, ValueError):
            kr = None
    apply_kr(recs, kr)
    keep = {"launch": lambda r: r["launch_related"], "korea": lambda r: r["korea_related"],
            "dprk": lambda r: r["dprk_pattern"],
            "candidate": lambda r: r["dprk_candidate"], "all": lambda r: True}[a.only]
    json.dump([r for r in recs if keep(r)], sys.stdout, ensure_ascii=False, indent=1, default=str)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

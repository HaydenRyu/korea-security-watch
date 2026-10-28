"""NASA FIRMS 열점(VIIRS 3기) → state/firms.json

  FIRMS_KEY=<MAP_KEY> python3 scripts/firms.py [--out state/firms.json]

- 범위: 한반도 + 인접(124~131E, 33~43N), 최근 48시간
- 접경 = 군사분계선(data/dmz.json mdl, OSM)에서 BORDER_KM 이내. 점마다 mdl_km(군사분계선까지 거리)를 남긴다.
  10/19 이전 근사 상자(37.6~38.9N)는 평양 남동 30km(MDL 약 90km)까지 「접경」으로 찍었다(hayden 지적).
- 열점은 들불·산불·공장 열원도 잡는다. 등급 판정의 단독 근거로 쓰지 않는다(prompts/monitor.md).
- 실패 시 기존 파일을 건드리지 않고 rc 3. 키가 없으면 rc 0(건너뜀).
- 유형 추정(10/19): 점마다 kind(fixed 상시 열원 추정 · burn 산불·소각 추정 · unknown 미상)와 근거(kind_why)를 붙인다.
  근거 = 같은 격자(0.005° ≈ 550m, 이웃 8칸 포함) 감지 일수(state/firms_cells.json 누적, CELL_KEEP_DAYS 보관)
  + OSM 시설(Overpass, 시설 경계 OSM_RADIUS_M 이내 · 격자별 OSM_TTL_DAYS 캐시 · OSM_BATCH 칸 묶음 질의 · 회차 OSM_BUDGET_S 초).
  북측(data/nk_boundary.json, Natural Earth) 은 OSM 시설 자료가 희박해 「시설 없음」을 근거로 쓰지 않는다.
  추정이다 — 단정 표현을 쓰지 않는다. Overpass 실패는 유형만 비우고 열점 갱신은 그대로 한다.
- 토지피복(10/28): 격자마다 ESA WorldCover 2021(10m, CC BY 4.0) 최다 분류를 1회 조회해 캐시한다(정적 자료 — 재조회 없음).
  공개 AWS 버킷 COG 를 HTTP Range 로 읽는다(가입·키 없음, 표준 라이브러리만). 회차당 LC_MAX_PER_RUN 칸 · LC_BUDGET_S 초.
  산불·소각을 농경지 소각(farm)·산불(wildfire)·시가지 열원(urban)으로 나누고, 북측 미상도 피복으로 추정한다(신뢰 낮음).
- 군사 시설 인근 표지(10/28): 같은 Overpass 질의에 landuse=military·military=* 를 더하고(요청 추가 없음),
  data/places.json 의 북측 주요 시설(NK_SITES)과 거리를 잰다. 표지(mil)일 뿐 유형·등급을 바꾸지 않는다 — 보도 대조용.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import struct
import sys
import time
import urllib.parse
import urllib.request
import zlib
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{src}/124,33,131,43/2"
SOURCES = ["VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "MODIS_NRT"]  # MODIS = Terra·Aqua 1km, 통과 시각이 VIIRS 와 달라 하루 2회 추가(10/23)
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
OSM_BATCH = 40            # 한 질의에 묶는 칸 수. 10/19 실측(overpass-api.de): 4칸 28초·30칸 45초·60칸 53초 — 고정 대기가 크다.
                          # 60칸은 GHA 에서 게이트웨이 504 → 40칸, 실패 시 반으로 나눠 재시도
OSM_MIN_SPLIT = 10        # 이 이하 묶음이 실패하면 그 회차 조회를 멈춘다
OSM_BATCH_TIMEOUT_S = 50  # 묶음 1회 서버 timeout
OSM_BUDGET_S = 120        # 회차당 Overpass 총 시간 상한 — 새 묶음은 경과 ≤ 70초일 때만 시작
OVERPASS = "https://overpass-api.de/api/interpreter"
UA = "korea-security-watch/1.0 (personal OSINT dashboard; github actions)"
# burn = 산불·소각 추정이되 토지피복을 못 읽은 경우(10/28 이전 의미 그대로). 새 유형은 피복 근거가 있을 때만
KIND_LABEL = {"fixed": "상시 열원 추정", "urban": "시가지 열원 추정", "farm": "농경지 소각 추정",
              "wildfire": "산불 추정", "burn": "산불·소각 추정", "unknown": "미상"}
NK_URBAN_LABEL = "시설 열원 추정(북측·토지피복)"   # 북측 시가지 반복 열원 — OSM 시설 근거가 없어 라벨을 따로 둔다
# 이름 없는 OSM 시설은 질의 태그값만 남는다(split_batch) — 화면·근거 문구에는 우리말로(10/19 hayden 「권장대로」).
# 키 = 질의하는 태그값 전부(landuse=industrial · power=plant · man_made=works|kiln|flare|chimney) + 태그도 없을 때의 「시설」
OSM_TAG_KO = {"industrial": "공업 지역(이름 없음)", "plant": "발전소(이름 없음)", "works": "공장(이름 없음)",
              "kiln": "가마(이름 없음)", "flare": "플레어 굴뚝(이름 없음)", "chimney": "굴뚝(이름 없음)", "시설": "시설(이름 없음)"}

# ── 토지피복(ESA WorldCover 2021 v200) ──────────────────────────────────────────
# AWS Open Data 공개 버킷(s3://esa-worldcover) — 3°×3° 타일, 좌하단 위경도 이름, 36000² uint8 · 1024² 타일 · DEFLATE.
# 10/28 실측: 당진제철 50 · 평양 50 · 동창리 10 · 풍계리 10(북측 타일 N39E123·N39E129 정상 응답)
WC_URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
WC_SRC = "ESA WorldCover 2021 (CC BY 4.0)"
LC_WIN_PX = 20            # ±20화소(10m) ≈ 410m 창의 최다 분류 — VIIRS 375m 화소 1개 폭. 한 화소만 보면 길·논두렁에 흔들린다
LC_MAX_PER_RUN = 60       # 회차당 새로 읽는 칸 상한(칸당 타일 1개 ≈ 15~90KB)
LC_BUDGET_S = 60          # 회차당 피복 조회 시간 상한
LC_KO = {10: "수목", 20: "관목", 30: "초지", 40: "농경지", 50: "시가지", 60: "나지", 70: "눈·얼음",
         80: "수역", 90: "습지", 95: "맹그로브", 100: "이끼·지의류"}
LC_VEG = {10, 20, 30}     # 산불 추정
LC_CROP, LC_BUILT = 40, 50

# ── 군사 시설 인근 표지 ────────────────────────────────────────────────────────
# 북측 주요 시설 — 좌표는 data/places.json(소수 2자리 대표점)에서 읽는다. 이름이 없으면 건너뛴다.
NK_SITES = {"풍계리": "핵실험장", "영변": "핵시설", "동창리": "서해위성발사장", "신포": "잠수함 기지"}
# 대표점 반올림(±0.7km) + 단지 폭 수 km — 단지 끝까지 닿도록 6km. 표지일 뿐이라 넓게 잡아도 판정은 안 바뀐다
NK_SITE_KM = 6
MIL_TAG_KO = "군사 시설(이름 없음)"
_SITES = None


def facility_labels(osm: list[str]) -> list[str]:
    """캐시의 시설 목록(이름 또는 태그값) → 표시 문구. 이름은 그대로, 태그값은 우리말, 같은 문구는 하나로."""
    out = []
    for v in osm:
        t = OSM_TAG_KO.get(v, v)
        if t not in out:
            out.append(t)
    return out


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


def _batch_query(items: list[tuple[str, float, float]]) -> str:
    """칸마다 around 검색 → make 로 칸 키를 붙인 요약 원소 1개. 응답이 칸 단위라 결과를 다시 나눌 때 거리 계산이 필요 없다."""
    parts = []
    for k, lat, lon in items:
        a = f"around:{OSM_RADIUS_M},{lat},{lon}"
        parts.append(f'(nwr({a})[landuse=industrial];nwr({a})[power=plant];nwr({a})[man_made~"^(works|kiln|flare|chimney)$"];);'
                     f'make c cell="{k}",n=count(nwr),ko=set(t["name:ko"]),nm=set(t["name"]),en=set(t["name:en"]),'
                     f'pw=set(t["power"]),mm=set(t["man_made"]),lu=set(t["landuse"]);out;')
        # 군사 시설(10/28) — 같은 요청 안에서 칸마다 요약 원소 m 하나 더(요청 수 불변)
        parts.append(f'(nwr({a})[landuse=military];nwr({a})[military];);'
                     f'make m cell="{k}",n=count(nwr),ko=set(t["name:ko"]),nm=set(t["name"]),en=set(t["name:en"]);out;')
    return f"[out:json][timeout:{OSM_BATCH_TIMEOUT_S}];" + "".join(parts)


def split_batch(elements: list[dict], keys: list[str]) -> dict[str, list[str]]:
    """묶음 응답 → {칸 키: 시설 이름[]}. 응답에 없는 칸은 결과에 넣지 않는다(= 미조회로 남아 다음 회차 재조회)."""
    want, out = set(keys), {}
    for e in elements:
        tg = e.get("tags", {})
        k = tg.get("cell")
        if k not in want or e.get("type") == "m":   # m = 군사 시설 요약(split_mil)
            continue
        if tg.get("n", "0") in ("", "0"):
            out[k] = []
            continue
        names = []
        for f in ("ko", "nm", "en"):
            for n in (tg.get(f) or "").split(";"):
                if n and n not in names:
                    names.append(n)
        if not names:  # 이름 없는 시설 — 태그값으로(industrial·plant·works 등)
            names = [v for f in ("pw", "mm", "lu") for v in (tg.get(f) or "").split(";") if v][:1] or ["시설"]
        out[k] = names[:5]
    return out


def split_mil(elements: list[dict], keys: list[str]) -> dict[str, list[str]]:
    """묶음 응답의 군사 시설 요약(type m) → {칸 키: 이름[]}. 이름 없으면 「군사 시설(이름 없음)」, n=0 은 []."""
    want, out = set(keys), {}
    for e in elements:
        tg = e.get("tags", {})
        k = tg.get("cell")
        if e.get("type") != "m" or k not in want:
            continue
        if tg.get("n", "0") in ("", "0"):
            out[k] = []
            continue
        names = []
        for f in ("ko", "nm", "en"):
            for n in (tg.get(f) or "").split(";"):
                if n and n not in names:
                    names.append(n)
        out[k] = names[:3] or [MIL_TAG_KO]
    return out


def osm_batch(items: list[tuple[str, float, float]]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """칸 묶음 1회 질의 → (시설, 군사 시설). 실패는 예외로 올린다(호출부가 캐시 미기록)."""
    q = _batch_query(items)
    req = urllib.request.Request(OVERPASS, data=urllib.parse.urlencode({"data": q}).encode(), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=OSM_BATCH_TIMEOUT_S + 15) as r:
        els, keys = json.load(r)["elements"], [k for k, _, _ in items]
    return split_batch(els, keys), split_mil(els, keys)


def enrich_osm(cells: dict, now: datetime, lookup=None, priority: set[str] | None = None,
               batch: int = OSM_BATCH) -> int:
    """OSM 미조회·만료 칸을 묶음(batch 칸) 단위로 조회해 칸별로 캐시한다.
    순서 = 이번 회차 열점이 있는 칸 → 최근 감지 순, 묶음 안은 위치순(같은 지역끼리). 새 묶음은 경과 시간이
    OSM_BUDGET_S − OSM_BATCH_TIMEOUT_S 이하일 때만 시작한다. 묶음이 실패하면 반으로 나눠 한 번 더(혼잡 504 대응),
    OSM_MIN_SPLIT 칸 이하 묶음이 실패하면 멈춘다 — 그 칸들은 미조회로 남고 열점 갱신은 계속된다."""
    lookup = lookup or osm_batch
    stale = (now - timedelta(days=OSM_TTL_DAYS)).isoformat()
    pri = priority or set()
    # mil 키가 없는 칸 = 10/28 이전 조회분(군사 시설 미조회) — TTL 안이어도 한 번 다시 묻는다(회차 상한 안에서)
    todo = sorted((k for k, c in cells.items() if c.get("osm_checked", "") < stale or "mil" not in c),
                  key=lambda k: (k in pri, cells[k]["dates"][-1]), reverse=True)
    queue = [sorted(todo[i:i + batch], key=lambda k: (cells[k]["lat"], cells[k]["lon"])) for i in range(0, len(todo), batch)]
    done, t0 = 0, time.monotonic()
    while queue:
        if time.monotonic() - t0 > OSM_BUDGET_S - OSM_BATCH_TIMEOUT_S:
            print(f"overpass 시간 상한 — 남은 {sum(map(len, queue))}칸 다음 회차로")
            break
        chunk = queue.pop(0)
        try:
            got = lookup([(k, cells[k]["lat"], cells[k]["lon"]) for k in chunk])
        except Exception as e:  # noqa: BLE001 — Overpass 혼잡(429·502·504)·불통
            if len(chunk) > OSM_MIN_SPLIT:
                h = len(chunk) // 2
                print(f"overpass {len(chunk)}칸 묶음 실패({e}) — 반으로 나눠 재시도")
                queue[:0] = [chunk[:h], chunk[h:]]
                continue
            print(f"overpass 중단({len(chunk)}칸 묶음): {e}")
            break
        fac, mil = got if isinstance(got, tuple) else (got, {})
        for k, names in fac.items():
            if k in cells:
                cells[k]["osm"] = names
                cells[k]["mil"] = mil.get(k, [])
                cells[k]["osm_checked"] = now.isoformat()
                done += 1
    return done


_WC_HDR: dict = {}
_WC_TILE: dict = {}
_WC_TILE_MAX = 48         # 회차 안 내부 타일(압축 해제 1MB) 메모리 캐시 상한


def _http_range(url: str, a: int, n: int) -> bytes:
    req = urllib.request.Request(url, headers={"Range": f"bytes={a}-{a + n - 1}", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        if r.status != 206:   # Range 무시(200)면 수백 MB 전체가 온다 — 읽지 않고 실패로
            raise RuntimeError(f"range 미지원 응답 {r.status}")
        b = r.read()
    if len(b) != n:
        raise RuntimeError(f"range {a}+{n}: {len(b)}바이트")
    return b


def wc_tile_name(lat: float, lon: float) -> tuple[str, int, int]:
    la0, lo0 = math.floor(lat / 3) * 3, math.floor(lon / 3) * 3
    return f"{'N' if la0 >= 0 else 'S'}{abs(la0):02d}{'E' if lo0 >= 0 else 'W'}{abs(lo0):03d}", la0, lo0


def _wc_header(tile: str, la0: int, lo0: int) -> dict:
    """COG 첫 IFD(전체 해상도)만 읽는다. 형식이 예상과 다르면 예외 — 잘못 읽은 값을 분류로 쓰지 않는다."""
    if tile in _WC_HDR:
        return _WC_HDR[tile]
    url = WC_URL.format(tile=tile)
    h = _http_range(url, 0, 65536)
    if h[:4] != b"II*\x00":
        raise RuntimeError(f"{tile}: TIFF 아님")
    off = struct.unpack_from("<I", h, 4)[0]
    size, fmt = {1: (1, "B"), 3: (2, "H"), 4: (4, "I"), 12: (8, "d")}, {}
    for i in range(struct.unpack_from("<H", h, off)[0]):
        tag, typ, cnt, val = struct.unpack_from("<HHII", h, off + 2 + i * 12)
        if typ not in size:
            continue
        n = size[typ][0] * cnt
        raw = h[off + 10 + i * 12: off + 10 + i * 12 + n] if n <= 4 else (h[val:val + n] if val + n <= len(h) else _http_range(url, val, n))
        fmt[tag] = struct.unpack(f"<{cnt}{size[typ][1]}", raw)
    hd = {"url": url, "W": fmt[256][0], "H": fmt[257][0], "tw": fmt[322][0], "th": fmt[323][0], "off": fmt[324], "cnt": fmt[325]}
    tie, sc = fmt.get(33922, ()), fmt.get(33550, ())
    if (fmt[259][0], fmt[258][0], fmt.get(317, (1,))[0]) != (8, 8, 1) or tie[3:5] != (lo0, la0 + 3) \
            or abs(sc[0] * hd["W"] - 3) > 1e-6 or abs(sc[1] * hd["H"] - 3) > 1e-6:
        raise RuntimeError(f"{tile}: 예상 밖 형식(압축 {fmt[259]}·기준점 {tie[3:5]})")
    _WC_HDR[tile] = hd
    return hd


def wc_class(lat: float, lon: float) -> tuple[int, int]:
    """(최다 분류 코드, 창 안 비율 %) — 점 주변 ±LC_WIN_PX 화소. 창은 내부 타일(1024²) 경계에서 자른다."""
    tile, la0, lo0 = wc_tile_name(lat, lon)
    hd = _wc_header(tile, la0, lo0)
    W, H, tw, th = hd["W"], hd["H"], hd["tw"], hd["th"]
    x, y = min(int((lon - lo0) / 3 * W), W - 1), min(int((la0 + 3 - lat) / 3 * H), H - 1)
    idx = (y // th) * -(-W // tw) + x // tw
    key = (tile, idx)
    if key not in _WC_TILE:
        if len(_WC_TILE) >= _WC_TILE_MAX:
            _WC_TILE.pop(next(iter(_WC_TILE)))
        _WC_TILE[key] = zlib.decompress(_http_range(hd["url"], hd["off"][idx], hd["cnt"][idx]))
    data, cx, cy = _WC_TILE[key], x % tw, y % th
    hist = Counter()
    x0, x1 = max(0, cx - LC_WIN_PX), min(tw, cx + LC_WIN_PX + 1)
    for r in range(max(0, cy - LC_WIN_PX), min(th, cy + LC_WIN_PX + 1)):
        hist.update(data[r * tw + x0: r * tw + x1])
    hist.pop(0, None)                         # 0 = 자료 없음(타일 여백)
    if not hist:
        raise RuntimeError("자료 없음 화소뿐")
    code, n = hist.most_common(1)[0]
    return code, round(100 * n / sum(hist.values()))


def enrich_lc(cells: dict, now: datetime, lookup=None, priority: set[str] | None = None,
              limit: int = LC_MAX_PER_RUN, budget_s: float = LC_BUDGET_S) -> int:
    """토지피복 미조회 칸을 회차당 limit 칸까지 조회해 캐시한다(정적 자료 — 한 번 읽은 칸은 다시 묻지 않는다).
    순서 = 이번 회차 열점 칸 → OSM 시설 없는 칸(토지피복이 유형을 바꾸는 칸) → 최근 감지 순. 시설 칸은 이미 「상시」라 뒤로 미룬다
    (10/28 첫 회차가 시설 칸 74개에 상한을 써 산불·소각 136점이 미조회로 남았다). 실패한 칸은 캐시하지 않는다. 3연속 실패면 멈춘다."""
    lookup = lookup or wc_class
    pri = priority or set()
    todo = sorted((k for k, c in cells.items() if "lc_checked" not in c),
                  key=lambda k: (k in pri, not cells[k].get("osm"), cells[k]["dates"][-1]), reverse=True)[:limit]
    done, fails, t0 = 0, 0, time.monotonic()
    for k in todo:
        if time.monotonic() - t0 > budget_s:
            print("토지피복 시간 상한 — 남은 칸 다음 회차로")
            break
        try:
            code, pct = lookup(cells[k]["lat"], cells[k]["lon"])
        except Exception as e:  # noqa: BLE001 — 버킷 불통·형식 이상
            fails += 1
            print(f"토지피복 조회 실패({k}): {e}")
            if fails >= 3:
                break
            continue
        fails = 0
        cells[k].update({"lc": code, "lc_pct": pct, "lc_checked": now.isoformat()})
        done += 1
    return done


def lc_text(lc: tuple[int, int]) -> str:
    code, pct = lc
    return f"토지피복 {LC_KO.get(code, f'분류 {code}')} {pct}%(반경 약 200m, ESA WorldCover 2021)"


def classify(days: int, osm: list[str] | None, north: bool,
             lc: tuple[int, int] | None = None) -> tuple[str, str, list[str]]:
    """(kind, 신뢰 중·낮음, 근거[]) — 추정 라벨. osm None = 아직 조회 안 함, lc None = 토지피복 미조회(분류로 쓰지 않음)"""
    why = [f"같은 자리 {days}일 감지(최근 {CELL_KEEP_DAYS}일)"]
    if osm:
        why.append(f"OSM 시설 {OSM_RADIUS_M}m 이내: {', '.join(osm[:3])}")
        return "fixed", ("중" if days >= FIXED_MIN_DAYS else "낮음"), why + ([lc_text(lc)] if lc else [])
    if osm is None:
        return "unknown", "낮음", why + ["OSM 시설 미조회"]
    lw, code = ([lc_text(lc)], lc[0]) if lc else (["토지피복 미조회"], None)
    once = days < REPEAT_MIN_DAYS
    if north:
        why.append("북측은 OSM 시설 자료가 희박해 「시설 없음」으로 보지 않음")
        if code == LC_BUILT and not once:
            return "urban", "낮음", why + lw + [f"시가지에서 {REPEAT_MIN_DAYS}일 이상 반복 — 시설 열원 가능성"]
        if once and code == LC_CROP:
            return "farm", "낮음", why + lw
        if once and code in LC_VEG:
            return "wildfire", "낮음", why + lw
        return "unknown", "낮음", why + lw
    why.append(f"OSM 시설 {OSM_RADIUS_M}m 이내 없음")
    if code == LC_BUILT:   # 지도에 없는 공장·소각장 등 — 반복 여부와 무관하게 시가지 열원으로
        return "urban", "낮음", why + lw
    if not once:
        return "unknown", "낮음", why + lw + [f"{REPEAT_MIN_DAYS}일 이상 반복 — 산불·소각으로 보기 어려움"]
    if code == LC_CROP:
        return "farm", "낮음", why + lw
    if code in LC_VEG:
        return "wildfire", "낮음", why + lw
    return "burn", "낮음", why + lw


def nk_sites() -> list[tuple[str, float, float]]:
    """[(표시 이름, lat, lon)] — data/places.json 북측 항목 중 NK_SITES 이름만"""
    global _SITES
    if _SITES is None:
        places = json.loads((ROOT / "data" / "places.json").read_text())["places"]
        _SITES = [(f"{nm}({NK_SITES[nm]})", float(a), float(b)) for c, nm, a, b in places if c == "북한" and nm in NK_SITES]
    return _SITES


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return math.hypot((lon2 - lon1) * 111.32 * math.cos(math.radians(lat1)), (lat2 - lat1) * 110.57)


def mil_near(lat: float, lon: float, osm_mil: list[str] | None) -> list[str]:
    """군사 시설 인근 근거 문구[] — OSM 군사 시설(OSM_RADIUS_M) + 북측 주요 시설(NK_SITE_KM)"""
    out = [f"{n}(OSM {OSM_RADIUS_M}m 이내)" for n in (osm_mil or [])[:2]]
    for nm, a, b in nk_sites():
        d = _km(lat, lon, a, b)
        if d <= NK_SITE_KM:
            out.append(f"{nm} 약 {max(1, round(d))}km")
    return out


def annotate(points: list[dict], cells: dict) -> None:
    """점에 kind·kind_label·kind_conf·kind_why·days_seen·north·osm·lc(·mil) 를 붙인다(제자리).
    mil 은 표지만 — kind·신뢰도를 바꾸지 않는다."""
    for p in points:
        k = cell_key(p["lat"], p["lon"])
        c = cells.get(k, {})
        osm = c.get("osm") if "osm_checked" in c else None
        lc = (c["lc"], c.get("lc_pct", 0)) if "lc_checked" in c and c.get("lc") is not None else None
        north = in_nk(p["lat"], p["lon"])
        n = days_seen(cells, k)
        osm = None if osm is None else facility_labels(osm)
        kind, conf, why = classify(n, osm, north, lc)
        label = NK_URBAN_LABEL if kind == "urban" and north else KIND_LABEL[kind]
        p.update({"days_seen": n, "north": north, "osm": osm or [], "lc": lc[0] if lc else None, "kind": kind,
                  "kind_label": label, "kind_conf": conf, "kind_why": why})
        mil = mil_near(p["lat"], p["lon"], c.get("mil"))
        if mil:
            p["mil"] = ", ".join(mil)
            why.append(f"군사 시설 인근: {p['mil']} — 보도 대조 필요")
        else:
            p.pop("mil", None)


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


def sat_label(src: str) -> str:
    """FIRMS 소스명 → 위성 표기. VIIRS_NOAA20_NRT → NOAA20, MODIS_NRT → MODIS(Terra·Aqua 는 CSV satellite 열이 갈라 주지만 표기는 하나로)."""
    return "MODIS" if src.startswith("MODIS") else src.split("_")[1]


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
                               "sat": sat_label(src), "mdl_km": round(mdl_km(lat, lon), 1),
                               "border": in_border(lat, lon)})
    except Exception as e:  # noqa: BLE001 — 실패는 rc 3 으로 알리고 기존 파일 유지
        print(f"FIRMS 실패: {e}")
        return 3
    points.sort(key=lambda p: p["t"], reverse=True)
    cells_path = Path(argv[argv.index("--cells") + 1]) if "--cells" in argv else CELLS
    cells = typed(points, cells_path, now, osm="--no-osm" not in argv, lc="--no-lc" not in argv)
    doc = {"fetched_at": now.isoformat(), "window_h": WINDOW_H, "sources": SOURCES,
           "border_km": BORDER_KM, "count": len(points), "border_count": sum(p["border"] for p in points),
           "kind_note": "kind 는 추정(감지 일수·OSM 시설·토지피복 근거)이다. 단독 판정 근거로 쓰지 않는다. "
                        "mil 은 군사 시설 인근 표지(보도 대조용)이며 유형·등급을 바꾸지 않는다",
           "kind_sources": ["© OpenStreetMap contributors (ODbL)", WC_SRC],
           "kind_count": {k: sum(p.get("kind") == k for p in points) for k in KIND_LABEL},
           "mil_count": sum("mil" in p for p in points),
           "points": points[:3000]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n")
    if cells is not None:
        cells_path.write_text(json.dumps({"updated_at": now.isoformat(), "cell_deg": CELL_DEG,
                                          "keep_days": CELL_KEEP_DAYS, "cells": cells},
                                         ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps({k: doc[k] for k in ("count", "border_count", "kind_count", "fetched_at")}, ensure_ascii=False))
    return 0


def typed(points: list[dict], cells_path: Path, now: datetime, osm: bool = True, lc: bool = True) -> dict | None:
    """격자 이력 누적 + OSM·토지피복 보강 + 점 주석. 실패해도 열점 갱신은 막지 않는다(None = 이력 파일 미갱신)."""
    try:
        cells = json.loads(cells_path.read_text())["cells"] if cells_path.exists() else {}
        update_cells(cells, points, now.date().isoformat())
        pri = {cell_key(p['lat'], p['lon']) for p in points}
        if osm:
            print(f"overpass {enrich_osm(cells, now, priority=pri)}칸 조회")
        if lc:
            print(f"토지피복 {enrich_lc(cells, now, priority=pri)}칸 조회")
        annotate(points, cells)
        return cells
    except Exception as e:  # noqa: BLE001
        print(f"유형 추정 실패(열점은 갱신): {e}")
        return None


if __name__ == "__main__":
    sys.exit(main(sys.argv))

"""국내 항행경보(국립해양조사원 KHOA, data.go.kr) → state/navwarn_kr.json

용도: 한국군 해상사격훈련 경보를 받아 「북한 발사 통보 후보」 오탐을 억제한다(navwarn_parse --kr).
     북한 발사 경보를 KHOA 가 낸 사례는 찾지 못했다(2023 위성 발사 통보는 일본 경유) — 이 자료는 억제·참고용이다.

API(키 = env DATA_GO_KR_KEY, data.go.kr 「일반 인증키(Decoding)」 — 다른 경로로 받지 않는다)
  현황  https://apis.data.go.kr/1192136/NavigationalWarning/getNavigationalWarningInfo   (date=YYYYMMDD)
  상세  https://apis.data.go.kr/1192136/NavigationalWarning/getNavigationalWarningDetailInfo (doc_num)
        → 구역(area)·지역명·꼭짓점(34-18-00.0N,128-00-00.0E, …)·알림일·알림시간(KST)
  구역  https://apis.data.go.kr/1192136/Seatn/getSeatnInfo — 고정 해상사격훈련구역(주 1회 갱신)
  명세 = data.go.kr 15057792(현황·상세) · 15058193(구역), 2026-10-19 확인. www.khoa.go.kr 은 robots 전면 금지라 API 만 쓴다.

비밀 유지: 키·키가 들어간 URL 은 로그·산출물·에러 문구 어디에도 남기지 않는다(redact — tests/test_navwarn_kr.py).

상태(status)
  ok           정상(경보 0건이면 warnings=[] — 「0건」은 이 상태에서만 뜻이 있다)
  key_missing  env 에 키 없음
  key_inactive 게이트웨이가 키를 거부(SERVICE_KEY_IS_NOT_REGISTERED_ERROR·SERVICE_ACCESS_DENIED_ERROR 등).
               새 키는 활성화까지 1시간쯤 걸린다 — 데이터 0건과 섞지 않는다
  quota        일 호출 한도 초과
  error        통신·형식 오류
  ok 가 아니면 직전 경보·구역은 그대로 두고 status 만 바꾼다(last_ok_at 으로 신선도 판단). rc 3.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from navwarn_parse import coords  # noqa: E402  — KHOA 좌표 형식(34-18-00.0N) 파서 공유

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "state" / "navwarn_kr.json"
BASE = "https://apis.data.go.kr/1192136"
EP_LIST = BASE + "/NavigationalWarning/getNavigationalWarningInfo"
EP_DETAIL = BASE + "/NavigationalWarning/getNavigationalWarningDetailInfo"
EP_ZONE = BASE + "/Seatn/getSeatnInfo"
SRC_PAGE = "https://www.data.go.kr/data/15057792/openapi.do"
ZONE_PAGE = "https://www.data.go.kr/data/15058193/openapi.do"
KST = timezone(timedelta(hours=9))
LOOKBACK_DAYS = 7        # 현황은 날짜별 조회 — 며칠 전에 낸 경보가 오늘 유효할 수 있다
CACHE_DAYS = 14          # 상세(문서번호별)는 바뀌지 않으므로 캐시 — 매시 재조회하지 않는다
ZONE_REFRESH_DAYS = 7
NO_WINDOW_KEEP_DAYS = 7  # 알림일을 못 읽은 문서는 처음 본 뒤 7일만 유효로 둔다
ROWS = 100
TIMEOUT = 30             # 10/19 GHA 실측: 20초 timed out 1회 — 해외 러너→data.go.kr 지연
RETRIES = 3              # 통신 오류·시간초과·SERVICETIMEOUT(05)만 재시도(키·한도 오류는 즉시)
BUDGET_S = 180           # 회차 상한 — 넘으면 받은 상세까지 캐시하고 다음 회차에 이어 받는다(firms 잡 8분 안)
KEY_ERRORS = ("SERVICE_KEY_IS_NOT_REGISTERED", "SERVICE_ACCESS_DENIED", "SERVICE_KEY_IS_NULL",
              "UNREGISTERED_IP", "DEADLINE_HAS_EXPIRED")
QUOTA_ERRORS = ("LIMITED_NUMBER_OF_SERVICE_REQUESTS",)


class ApiError(Exception):
    def __init__(self, status: str, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def redact(text: str, key: str | None) -> str:
    """키 원문·URL 인코딩형과 serviceKey=… 파라미터를 지운다."""
    t = str(text)
    if key:
        for v in {key, urllib.parse.quote(key, safe=""), urllib.parse.quote_plus(key), urllib.parse.quote(key)}:
            if v:
                t = t.replace(v, "***")
    return re.sub(r"(?i)(service_?key=)[^&\s\"'<>]+", r"\1***", t)


def _http(url: str, params: dict, key: str) -> str:
    """통신 오류·시간초과·게이트웨이 SERVICETIMEOUT(05)만 재시도. 오류 문구에 오퍼레이션·인자(키 제외)를 붙인다."""
    where = url.rsplit("/", 1)[1] + "(" + ",".join(f"{k}={v}" for k, v in params.items() if k in ("date", "doc_num", "pageNo")) + ")"
    for i in range(RETRIES):
        try:
            body = _http1(url, params, key)
            parse_response(body)  # 게이트웨이 오류면 여기서 ApiError
            return body
        except ApiError as e:
            retry = e.status == "error" and not e.detail.startswith("HTTP") and ("code 05" in e.detail or "Error" in e.detail or "timed out" in e.detail)
            if not retry or i == RETRIES - 1:
                raise ApiError(e.status, f"{where}: {e.detail}") from None
            time.sleep(5)
    raise ApiError("error", f"{where}: 재시도 소진")


def _http1(url: str, params: dict, key: str) -> str:
    q = urllib.parse.urlencode({"serviceKey": key, **params})
    req = urllib.request.Request(f"{url}?{q}", headers={"User-Agent": "korea-security-watch/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:  # 401·403 도 본문에 오류 코드가 온다
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        if body.strip():
            return body
        raise ApiError("error", f"HTTP {e.code}") from None
    except Exception as e:  # noqa: BLE001 — 메시지는 호출부에서 redact
        raise ApiError("error", f"{type(e).__name__}: {e}") from None


def parse_response(body: str) -> tuple[list[dict], int]:
    """(items, totalCount). 게이트웨이 오류·결과코드 오류는 ApiError. NODATA(03) = 정상 0건."""
    b = body.strip()
    if b.startswith("{"):
        d = json.loads(b)
        h = d.get("OpenAPI_ServiceResponse", {}).get("cmmMsgHeader")
        if h:
            _raise_gateway(h.get("errMsg", ""), str(h.get("returnReasonCode", "")))
        r = d.get("response", d)
        hd, bd = r.get("header", {}), r.get("body", {})
        _check_result(str(hd.get("resultCode", "")), str(hd.get("resultMsg", "")))
        it = (bd.get("items") or {})
        it = it.get("item", []) if isinstance(it, dict) else it
        it = [it] if isinstance(it, dict) else (it or [])
        return [{k: ("" if v is None else str(v)) for k, v in x.items()} for x in it], int(bd.get("totalCount") or 0)
    root = _xml(b)
    if root.tag == "OpenAPI_ServiceResponse":
        _raise_gateway(root.findtext(".//errMsg") or "", root.findtext(".//returnReasonCode") or "")
    _check_result(root.findtext(".//header/resultCode") or "", root.findtext(".//header/resultMsg") or "")
    items = [{c.tag: (c.text or "").strip() for c in it} for it in root.iter("item")]
    return items, int(root.findtext(".//totalCount") or 0)


_BARE_AMP = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)")
_BARE_LT = re.compile(r"<(?![A-Za-z_/?!])")  # 내용 속 「<3000ft」 같은 맨 부등호
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _xml(b: str):
    """KHOA 본문 XML 은 내용 속 「&」·제어문자를 이스케이프하지 않은 채 오기도 한다(10/19 실측: 정상 응답이 ParseError)
    → 실패하면 맨 「&」를 &amp; 로·제어문자를 지우고 한 번 더. 그래도 안 되면 오류 위치 주변을 남긴다."""
    try:
        return ET.fromstring(b)
    except ET.ParseError:
        pass
    fixed = _CTRL.sub(" ", _BARE_LT.sub("&lt;", _BARE_AMP.sub("&amp;", b)))
    try:
        return ET.fromstring(fixed)
    except ET.ParseError as e:
        line, col = e.position
        ln = fixed.splitlines()[line - 1] if 0 < line <= len(fixed.splitlines()) else ""
        raise ApiError("error", f"응답 XML 해석 실패({e}, 본문 {len(b)}자, 끝 「{b[-40:]}」): …{ln[max(0, col - 80):col + 80]}…") from None


def _raise_gateway(msg: str, code: str):
    m = (msg or "").upper()
    if any(k in m for k in KEY_ERRORS) or code in ("20", "30", "31", "32"):
        raise ApiError("key_inactive", f"{msg} (code {code})")
    if any(k in m for k in QUOTA_ERRORS) or code == "22":
        raise ApiError("quota", f"{msg} (code {code})")
    raise ApiError("error", f"{msg} (code {code})")


def _check_result(code: str, msg: str):
    if code in ("", "00", "0", "03"):  # 03 = NODATA_ERROR → 정상 0건
        return
    if code in ("20", "22", "30", "31", "32"):
        _raise_gateway(msg, code)
    raise ApiError("error", f"resultCode {code} {msg}")


def fetch_all(get, url: str, params: dict, rows: int = ROWS) -> list[dict]:
    """쪽 넘김. 응답이 중간에 잘려 오면(10/19 실측: 구역 100행 = 19,415자에서 「</ite」로 끊김) 쪽 크기를 줄여 처음부터."""
    out, page = [], 1
    while True:
        try:
            items, total = parse_response(get(url, {**params, "numOfRows": rows, "pageNo": page}))
        except ApiError as e:
            if "XML 해석 실패" in e.detail and rows > 5:
                return fetch_all(get, url, params, max(5, rows // 4))
            raise
        out += items
        if not items or len(out) >= total or page >= 100:
            return out
        page += 1


# ── 해석 ─────────────────────────────────────────────────────
def _split(s: str) -> list[str]:
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def windows(alarm_date: str, alarm_time: str) -> list[dict]:
    """알림일 '2018-05-14,2018-05-15,' + 알림시간 '08:00 ~ 22:00,…' (KST) → [{from,to}] UTC ISO.
    시간이 날짜보다 적으면 마지막 시간을 이어 쓴다. 끝이 시작보다 이르면 다음 날로 넘긴다."""
    ds, ts = _split(alarm_date), _split(alarm_time)
    out = []
    for i, d in enumerate(ds):
        try:
            day = datetime.strptime(re.sub(r"[^\d]", "", d)[:8], "%Y%m%d").replace(tzinfo=KST)
        except ValueError:
            continue
        t = ts[i] if i < len(ts) else (ts[-1] if ts else "00:00 ~ 24:00")
        m = re.match(r"\s*(\d{1,2}):(\d{2})\s*[~∼-]\s*(\d{1,2}):(\d{2})", t)
        h1, m1, h2, m2 = (int(x) for x in m.groups()) if m else (0, 0, 24, 0)
        a, b = day + timedelta(hours=h1, minutes=m1), day + timedelta(hours=h2, minutes=m2)
        if b <= a:
            b += timedelta(days=1)
        out.append({"from": a.astimezone(timezone.utc).isoformat(), "to": b.astimezone(timezone.utc).isoformat()})
    return out


def circle(center, shape: str) -> tuple[list, float | None]:
    """「좌표를 중심으로 반경 4NM」 → 16각형 근사(10/19 실측: 유효 경보 구역 90곳 중 21곳이 원형). 반경을 못 읽으면 점 그대로."""
    m = re.search(r"반경\s*(\d+(?:\.\d+)?)\s*(NM|해리|M|m|KM|km)", shape or "")
    if not m or not center:
        return [list(center)] if center else [], None
    r_km = float(m[1]) * {"NM": 1.852, "해리": 1.852, "KM": 1, "km": 1, "M": .001, "m": .001}[m[2]]
    la, lo = center
    pts = [[round(la + r_km / 110.57 * math.cos(math.radians(a)), 4),
            round(lo + r_km / (111.32 * math.cos(math.radians(la))) * math.sin(math.radians(a)), 4)] for a in range(0, 360, 22)]
    return pts[:16], round(r_km, 2)


def area_of(x: dict) -> dict:
    poly = [list(p) for p in coords(x.get("position", ""))]
    radius = None
    if len(poly) == 1:
        poly, radius = circle(poly[0], x.get("position_desc", ""))
    return {"area": x.get("area", ""), "name": x.get("position_nm", ""), "polygon": poly, "radius_km": radius,
            "shape": x.get("position_desc", ""), "windows": windows(x.get("alarm_date", ""), x.get("alarm_time", "")),
            "chart": x.get("sea_pos", "")}


def doc_of(x: dict, areas: list[dict], seen: str) -> dict:
    return {"doc_num": x.get("doc_num", ""), "doc_type": x.get("doc_type", ""), "unit": x.get("gov_cd", ""),
            "noti_cat": x.get("noti_cat", ""), "app_cat": x.get("app_cat", ""), "title": x.get("title", ""),
            "basis": x.get("basic", ""), "content": x.get("content", "")[:500], "areas": areas,
            "first_seen": seen, "source": "국립해양조사원 항행경보(공공데이터포털)", "url": SRC_PAGE}


KR_UNIT = re.compile(r"합동참모본부|합참|해군|해병|육군|공군|해양경찰|국방과학연구소|국방부|사령부|함대|군단|사단|전단")
FOREIGN = re.compile(r"일본|해상보안청|중국|러시아|미국|북한|외국|JCG|NAVAREA", re.I)


def is_exercise(doc: dict) -> bool:
    """한국군·해경의 사격·훈련 경보만 True — 오탐 억제(navwarn_parse.apply_kr)에 쓰인다.
    KHOA 는 외국 경보도 「해상사격」 분류로 중계한다(10/19 실측: 26-332 「일본 해상보안청 항행경보 알림」, 사격부대 공란).
    중계 경보가 억제에 쓰이면 실제 북한 발사 통보를 지울 수 있다 → 발령 부대가 한국 기관이고 외국 표기가 없을 때만."""
    text = " ".join(str(doc.get(k, "")) for k in ("noti_cat", "doc_type", "title"))
    who = str(doc.get("unit", ""))
    return bool(re.search(r"사격|훈련", text)) and bool(KR_UNIT.search(who)) and not FOREIGN.search(who + " " + text)


def is_foreign_relay(doc: dict) -> bool:
    """외국 기관 경보의 중계 — 발령 부대가 한국 기관이 아니고(공란 포함) 제목·분류·근거에 외국 표기가 있을 때.
    지도(index.html drawNavwarn)가 이 값으로 「외국 경보 중계」를 표시한다 — 화면 쪽에 판정 사본을 두지 않는다(10/19).
    발령 부대 공란 + 외국 표기 없음은 중계가 아니라 「발령 기관 미기재」(False)."""
    who = str(doc.get("unit", ""))
    text = " ".join(str(doc.get(k, "")) for k in ("noti_cat", "doc_type", "title", "basis"))
    return not KR_UNIT.search(who) and bool(FOREIGN.search(who + " " + text))


def valid_now(doc: dict, now: datetime) -> tuple[bool, bool]:
    """(유효 = 끝나지 않음, 진행 중)"""
    ws = [w for a in doc.get("areas", []) for w in a.get("windows", [])]
    if not ws:
        fs = datetime.fromisoformat(doc.get("first_seen") or now.isoformat())
        return now - fs <= timedelta(days=NO_WINDOW_KEEP_DAYS), False
    live = [w for w in ws if datetime.fromisoformat(w["to"]) >= now]
    return bool(live), any(datetime.fromisoformat(w["from"]) <= now for w in live)


def zone_of(x: dict) -> dict:
    c = coords(f"{x.get('centerZoneY', '')} {x.get('centerZoneX', '')}".replace(" N", "N").replace(" E", "E"))
    poly, radius = [list(p) for p in coords(x.get("zoneCrdnt", ""))], None
    if len(poly) <= 1 and (poly or c):
        poly, radius = circle(poly[0] if poly else list(c[0]), x.get("zoneDesc", ""))
    return {"code": x.get("posCd", ""), "name": x.get("crdnt", ""), "kind": x.get("tnzone", ""),
            "polygon": poly, "radius_km": radius, "center": list(c[0]) if c else None,
            "shape": x.get("zoneDesc", ""), "altitude": x.get("hight", ""), "revised": x.get("reviym", "")}


# ── 실행 ─────────────────────────────────────────────────────
def run(key: str | None, prev: dict, now: datetime, get=None) -> tuple[dict, int]:
    doc = {"source": "국립해양조사원(KHOA) 항행경보 · 해상사격훈련구역 — 공공데이터포털 API",
           "source_pages": [SRC_PAGE, ZONE_PAGE], "fetched_at": now.isoformat(),
           "status": "ok", "status_detail": "", "last_ok_at": prev.get("last_ok_at"),
           "warnings": prev.get("warnings", []), "counts": prev.get("counts", {}),
           "zones": prev.get("zones", {"fetched_at": None, "status": None, "items": []}),
           "cache": prev.get("cache", {})}
    if not key:
        doc.update(status="key_missing", status_detail="env DATA_GO_KR_KEY 없음")
        return doc, 3
    get = get or (lambda url, params: _http(url, params, key))
    t0 = time.monotonic()
    cache = dict(doc["cache"])
    try:
        # 1) 현황: 최근 LOOKBACK_DAYS 일 문서번호
        listed = {}
        for i in range(LOOKBACK_DAYS):
            day = (now.astimezone(KST) - timedelta(days=i)).strftime("%Y%m%d")
            for x in fetch_all(get, EP_LIST, {"date": day}):
                if x.get("doc_num"):
                    listed.setdefault(x["doc_num"], x)
        # 2) 상세: 캐시에 없는 문서만
        cache = {k: v for k, v in cache.items()
                 if now - datetime.fromisoformat(v["first_seen"]) <= timedelta(days=CACHE_DAYS)}
        new = 0
        for dn, x in listed.items():
            if dn in cache and cache[dn].get("areas") and all("radius_km" in a for a in cache[dn]["areas"]):
                continue  # radius_km 없는 캐시 = 원형 구역 다각형화(10/19) 이전 판 → 한 번 다시 받는다
            if time.monotonic() - t0 > BUDGET_S:
                raise ApiError("error", f"시간 상한 {BUDGET_S}초 — 상세 {new}건까지 받음, 나머지는 다음 회차")
            det = fetch_all(get, EP_DETAIL, {"doc_num": dn})
            cache[dn] = doc_of(x, [area_of(a) for a in det], cache.get(dn, {}).get("first_seen") or now.isoformat())
            new += 1
        cur = []
        for d in cache.values():
            ok, active = valid_now(d, now)
            if ok:
                cur.append({k: v for k, v in d.items()} | {"active_now": active, "exercise": is_exercise(d), "foreign_relay": is_foreign_relay(d)})
        cur.sort(key=lambda d: (not d["active_now"], d["doc_num"]))
        kinds = {}
        for d in cur:
            kinds[d["noti_cat"] or "미상"] = kinds.get(d["noti_cat"] or "미상", 0) + 1
        doc.update(cache=cache, warnings=cur, last_ok_at=now.isoformat(),
                   counts={"listed_docs": len(listed), "new_details": new, "valid": len(cur),
                           "active_now": sum(d["active_now"] for d in cur), "by_kind": kinds})
    except ApiError as e:  # 받은 상세는 캐시에 남긴다(다음 회차가 이어 받는다). 경보 목록·last_ok_at 은 그대로
        doc.update(status=e.status, status_detail=redact(e.detail, key), cache=cache)
        return doc, 3
    except Exception as e:  # noqa: BLE001
        doc.update(status="error", status_detail=redact(f"{type(e).__name__}: {e}", key), cache=cache)
        return doc, 3
    # 3) 고정 구역: 주 1회. 실패해도 경보는 유지(zones.status 로 드러낸다)
    z = doc["zones"]
    stale = not z.get("fetched_at") or now - datetime.fromisoformat(z["fetched_at"]) > timedelta(days=ZONE_REFRESH_DAYS)
    stale = stale or any("radius_km" not in i for i in z.get("items", []))  # 원형 다각형화 이전 판
    if stale or z.get("status") != "ok":
        try:
            items = [zone_of(x) for x in fetch_all(get, EP_ZONE, {})]
            doc["zones"] = {"fetched_at": now.isoformat(), "status": "ok", "items": items}
        except ApiError as e:
            doc["zones"] = {**z, "status": e.status, "status_detail": redact(e.detail, key)}
        except Exception as e:  # noqa: BLE001
            doc["zones"] = {**z, "status": "error", "status_detail": redact(f"{type(e).__name__}: {e}", key)}
    return doc, 0


def main(argv=None) -> int:
    out = Path((argv or sys.argv[1:] or [str(OUT)])[0])
    key = (os.environ.get("DATA_GO_KR_KEY") or "").strip() or None
    try:
        prev = json.loads(out.read_text())
    except Exception:
        prev = {}
    doc, rc = run(key, prev, datetime.now(timezone.utc))
    text = redact(json.dumps(doc, ensure_ascii=False, indent=1), key)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n")
    c = doc.get("counts", {})
    msg = (f"navwarn_kr: status={doc['status']}"
           + (f" ({doc['status_detail']})" if doc["status_detail"] else "")
           + (f" · 문서 {c.get('listed_docs', 0)} · 신규 상세 {c.get('new_details', 0)} · 유효 {c.get('valid', 0)}"
              f" · 진행 중 {c.get('active_now', 0)} · 종류 {c.get('by_kind', {})}" if doc["status"] == "ok" else "")
           + f" · 고정 구역 {len(doc['zones'].get('items', []))}({doc['zones'].get('status')})")
    print(redact(msg, key))
    if rc:
        print(redact(f"::warning title=navwarn_kr::{doc['status']} — {doc['status_detail']}", key))
    return rc


if __name__ == "__main__":
    sys.exit(main())

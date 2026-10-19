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
import os
import re
import sys
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
TIMEOUT = 20
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
    try:
        root = ET.fromstring(b)
    except ET.ParseError:
        raise ApiError("error", "응답 형식 오류(XML·JSON 아님): " + re.sub(r"\s+", " ", b)[:120]) from None
    if root.tag == "OpenAPI_ServiceResponse":
        _raise_gateway(root.findtext(".//errMsg") or "", root.findtext(".//returnReasonCode") or "")
    _check_result(root.findtext(".//header/resultCode") or "", root.findtext(".//header/resultMsg") or "")
    items = [{c.tag: (c.text or "").strip() for c in it} for it in root.iter("item")]
    return items, int(root.findtext(".//totalCount") or 0)


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


def fetch_all(get, url: str, params: dict) -> list[dict]:
    out, page = [], 1
    while True:
        items, total = parse_response(get(url, {**params, "numOfRows": ROWS, "pageNo": page}))
        out += items
        if not items or len(out) >= total or page >= 20:
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


def area_of(x: dict) -> dict:
    poly = [list(p) for p in coords(x.get("position", ""))]
    return {"area": x.get("area", ""), "name": x.get("position_nm", ""), "polygon": poly,
            "shape": x.get("position_desc", ""), "windows": windows(x.get("alarm_date", ""), x.get("alarm_time", "")),
            "chart": x.get("sea_pos", "")}


def doc_of(x: dict, areas: list[dict], seen: str) -> dict:
    return {"doc_num": x.get("doc_num", ""), "doc_type": x.get("doc_type", ""), "unit": x.get("gov_cd", ""),
            "noti_cat": x.get("noti_cat", ""), "app_cat": x.get("app_cat", ""), "title": x.get("title", ""),
            "basis": x.get("basic", ""), "content": x.get("content", "")[:500], "areas": areas,
            "first_seen": seen, "source": "국립해양조사원 항행경보(공공데이터포털)", "url": SRC_PAGE}


def is_exercise(doc: dict) -> bool:
    return bool(re.search(r"사격|훈련", " ".join(str(doc.get(k, "")) for k in ("noti_cat", "doc_type", "title"))))


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
    return {"code": x.get("posCd", ""), "name": x.get("crdnt", ""), "kind": x.get("tnzone", ""),
            "polygon": [list(p) for p in coords(x.get("zoneCrdnt", ""))], "center": list(c[0]) if c else None,
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
    try:
        # 1) 현황: 최근 LOOKBACK_DAYS 일 문서번호
        listed = {}
        for i in range(LOOKBACK_DAYS):
            day = (now.astimezone(KST) - timedelta(days=i)).strftime("%Y%m%d")
            for x in fetch_all(get, EP_LIST, {"date": day}):
                if x.get("doc_num"):
                    listed.setdefault(x["doc_num"], x)
        # 2) 상세: 캐시에 없는 문서만
        cache = {k: v for k, v in doc["cache"].items()
                 if now - datetime.fromisoformat(v["first_seen"]) <= timedelta(days=CACHE_DAYS)}
        new = 0
        for dn, x in listed.items():
            if dn in cache and cache[dn].get("areas"):
                continue
            det = fetch_all(get, EP_DETAIL, {"doc_num": dn})
            cache[dn] = doc_of(x, [area_of(a) for a in det], cache.get(dn, {}).get("first_seen") or now.isoformat())
            new += 1
        cur = []
        for d in cache.values():
            ok, active = valid_now(d, now)
            if ok:
                cur.append({k: v for k, v in d.items()} | {"active_now": active, "exercise": is_exercise(d)})
        cur.sort(key=lambda d: (not d["active_now"], d["doc_num"]))
        kinds = {}
        for d in cur:
            kinds[d["noti_cat"] or "미상"] = kinds.get(d["noti_cat"] or "미상", 0) + 1
        doc.update(cache=cache, warnings=cur, last_ok_at=now.isoformat(),
                   counts={"listed_docs": len(listed), "new_details": new, "valid": len(cur),
                           "active_now": sum(d["active_now"] for d in cur), "by_kind": kinds})
    except ApiError as e:
        doc.update(status=e.status, status_detail=redact(e.detail, key))
        return doc, 3
    except Exception as e:  # noqa: BLE001
        doc.update(status="error", status_detail=redact(f"{type(e).__name__}: {e}", key))
        return doc, 3
    # 3) 고정 구역: 주 1회. 실패해도 경보는 유지(zones.status 로 드러낸다)
    z = doc["zones"]
    stale = not z.get("fetched_at") or now - datetime.fromisoformat(z["fetched_at"]) > timedelta(days=ZONE_REFRESH_DAYS)
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

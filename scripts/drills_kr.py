#!/usr/bin/env python3
"""접경 지자체 육상 사격훈련 공지 → state/drills_kr.json

용도: 「포성이 들린다」 류 보도를 정례 사격으로 볼 근거(참고 자료). 등급·알림에는 쓰지 않는다.
출처는 일반인이 쓰는 공개 게시판만 — robots.txt 가 막지 않고 본문이 글로 있는 곳(10/21 접경 10개 시군 조사).
  철원  공지사항 RSS(본문 전체 포함)  https://www.cwg.go.kr/rssBbsNtt.do?bbsNo=24
  고성  공지사항 검색(POST, 검색어 「사격」) → 글 본문  https://www.gwgs.go.kr/prog/bbsArticle/BBSMSTR_000000000412/
  파주  새소식(재난안전) 검색 → 글 본문 + HWP 첨부(월간 포병훈련장 사격일정 표)  https://www.paju.go.kr/user/board/BD_board.list.do?bbsCd=2001
  화천  공지사항 제목 검색 → PDF 첨부(월간 포탄사격 계획 표)  https://www.ihc.go.kr/www/selectBbsNttList.do?bbsNo=11&key=2338
제외: 김포·연천(robots 가 일반 수집 전면 차단) · 옹진·강화·인제(최근 게시 없음) · 양구(연 1회 xlsx, 사이트 검색으로 글을 못 찾음 — 12월 말 게시 때 재검토).
첨부 해석 의존성: olefile(HWP 5.x OLE) · pypdf(PDF) — firms.yml 이 설치한다. 없으면 그 첨부만 건너뛰고 본문만 쓴다.

  python3 scripts/drills_kr.py          # firms.yml 매시 — 직전 성공 MIN_INTERVAL_H 안이면 받지 않는다

상태(시군별 sources.<id>.status): ok · error. 실패한 시군은 직전 글을 그대로 두고 status 만 바꾼다. 하나라도 실패면 rc 3.
"""
from __future__ import annotations

import html
import json
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "state" / "drills_kr.json"
KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (korea-security-watch; public notice reader)"
TIMEOUT = 40              # 철원은 간헐적으로 30초 넘게 응답이 없다(10/21 실측)
RETRY_WAITS = (10, 30)
MIN_INTERVAL_H = 6        # 공지는 주 1~2회 — 매시 받지 않는다
KEEP_DAYS = 45            # 훈련 마지막 날(없으면 게시일)이 이보다 오래면 버린다
BODY_MAX = 4000          # 첨부 표(파주 HWP·화천 PDF)는 1,000~2,000자 — 자르면 뒤쪽 날짜가 빠진다
KEYWORD = re.compile(r"사격")

CW_RSS = "https://www.cwg.go.kr/rssBbsNtt.do?bbsNo=24"
CW_PAGE = "https://www.cwg.go.kr/www/selectBbsNttList.do?bbsNo=24&key=206"
GS_BASE = "https://www.gwgs.go.kr/prog/bbsArticle/BBSMSTR_000000000412/"
GS_PAGE = GS_BASE + "list.do"
SOURCES = {
    "cheorwon": {"name": "철원군", "page": CW_PAGE},
    "goseong": {"name": "고성군", "page": GS_PAGE},
    "paju": {"name": "파주시", "page": "https://www.paju.go.kr/user/board/BD_board.list.do?bbsCd=2001"},
    "hwacheon": {"name": "화천군", "page": "https://www.ihc.go.kr/www/selectBbsNttList.do?bbsNo=11&key=2338"},
}
PJ = "https://www.paju.go.kr"
HC = "https://www.ihc.go.kr/www/"
TITLE_KEEP = re.compile(r"사격|훈련")  # 파주는 「제1보병사단 8월 주요 훈련 일정」처럼 사격이 제목에 없는 훈련 예고도 같은 게시판에 올린다


# TLS(10/21 실측) — 인증서 검증은 끄지 않는다.
#  철원: 서버가 중간 인증서 대신 루트를 보낸다(openssl 「unable to verify the first certificate」). macOS curl 은 AIA 로 받아
#        통과하지만 파이썬은 실패 → DigiCert 공식 배포본(cacerts.digicert.com/RapidSSLTLSRSACAG1.crt, 2027-11-02 만료)을 레포에 두고 보탠다.
#  고성: 구형 암호만 받는다(SSLV3_ALERT_HANDSHAKE_FAILURE) → 이 호스트만 SECLEVEL=1·레거시 재협상 허용.
CERT_DIR = ROOT / "data" / "certs"


def _ctx(url: str) -> ssl.SSLContext:
    c = ssl.create_default_context()
    host = urllib.parse.urlparse(url).hostname or ""
    if host.endswith("cwg.go.kr"):
        c.load_verify_locations(str(CERT_DIR / "rapidssl_tls_rsa_ca_g1.pem"))
    if host.endswith("gwgs.go.kr"):
        c.set_ciphers("DEFAULT:@SECLEVEL=1")
        c.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0)
    return c


def _http(url: str, data: dict | None = None) -> str:
    return _fetch(url, data).decode("utf-8", "replace")


def _fetch(url: str, data: dict | None = None) -> bytes:
    body = urllib.parse.urlencode(data).encode() if data else None
    last = None
    for wait in (0, *RETRY_WAITS):
        if wait:
            time.sleep(wait)
        try:
            req = urllib.request.Request(url, data=body, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx(url)) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001 — 통신 오류·시간초과는 재시도
            last = e
    raise RuntimeError(f"{type(last).__name__}: {str(last)[:120]}")


def text_of(fragment: str) -> str:
    """HTML 조각 → 줄바꿈 보존 평문."""
    t = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", fragment)
    t = html.unescape(re.sub(r"<[^>]+>", "", t)).replace("\xa0", " ")
    t = t.replace("（", "(").replace("）", ")")
    lines = [re.sub(r"[ \t]+", " ", l).strip() for l in t.splitlines()]
    return "\n".join(l for l in lines if l).strip()


# 날짜 토큰 — 「2026. 10. 19.」·「'26.10.22.(목)」·「26. 10. 15.(목)」·연도 없는 「8. 11.(화)」(게시 연도로 보충).
# 연도 없는 표기는 요일 괄호가 붙은 것만 날짜로 본다(「붙임 1.」·버전 번호 오탐 방지). 문의 전화번호(031) 539-…)는 걸리지 않는다.
_DATE = re.compile(r"(?<![\d.])(?:(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})"
                   r"|(\d{2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})\s*\.?\s*(?=\()"
                   r"|(\d{1,2})\s*\.\s*(\d{1,2})\s*\.?\s*(?=\([월화수목금토일 ]*\)))"
                   r"\.?\s*(?:\([월화수목금토일 ]*\))?")
RANGE_MAX_DAYS = 62  # 「8. 4.(화) ~ 8. 27.(목)」 처럼 기간으로 공지한 훈련은 그 사이 날을 모두 훈련일로 본다


def drill_dates(body: str, year: int | None = None) -> list[str]:
    """본문의 훈련 날짜(YYYY-MM-DD). 「A ~ B」 기간은 펼친다. year = 연도 없는 표기에 쓸 게시 연도."""
    toks = []
    for m in _DATE.finditer(body):
        g = m.groups()
        y, mo, d = (g[0], g[1], g[2]) if g[0] else (("20" + g[3]), g[4], g[5]) if g[3] else (year, g[6], g[7])
        if y is None:
            continue
        try:
            toks.append((m.start(), m.end(), date(int(y), int(mo), int(d))))
        except ValueError:
            continue
    out = {t[2] for t in toks}
    for (s0, e0, d0), (s1, e1, d1) in zip(toks, toks[1:]):
        if re.fullmatch(r"\s*[~∼～\-]\s*", body[e0:s1]) and 0 < (d1 - d0).days <= RANGE_MAX_DAYS:
            out.update(d0 + timedelta(days=i) for i in range((d1 - d0).days + 1))
    return sorted(x.isoformat() for x in out)


def unit_of(title: str, body: str) -> str:
    m = re.search(r"제\s*\d+\s*(?:포병여단|보병여단|보병사단|사단|여단|부대)|육군\s*제\s*\d+\s*부대", title + " " + body)
    return re.sub(r"\s+", "", m.group(0)) if m else ""


_PLACE = re.compile(r"^(?:-\s*(.+)|.*훈련장소\s*:\s*(.+))$", re.M)
_ARMS = re.compile(r"\d+\s*mm\s*[가-힣]*\s*\d*\s*여?\s*발|\d+\s*여\s*발|개인화기|박격포|자주포")


def derive(title: str, body: str, posted: str | None = None) -> dict:
    """화면 표시용 요약 — 날짜·장소·화기. 판정은 여기 한 곳(상황판은 받은 값만 그린다)."""
    days = drill_dates(body, int(posted[:4]) if posted else None)
    places = [re.sub(r"\s+", " ", (a or b).strip()) for a, b in _PLACE.findall(body)]
    places = [x for x in places if not re.fullmatch(r"[가-힣]+\s*:.*", x)]
    places = list(dict.fromkeys(x for x in places if not drill_dates(x) and not re.search(r"\d{1,2}:\d{2}", x)))  # 「- 2026. 9. 8.(화) 08:30」 은 장소가 아니다
    if not places:  # 첨부 표(파주 HWP·화천 PDF)는 「- 」·「훈련장소 :」 줄이 없다 — 사격장·훈련장 이름만 모은다
        # 띄어쓰기는 공백 한 칸만(줄바꿈을 넘으면 표 머리글 「구 분 / 훈련장명」이 「분 훈련장」으로 붙는다).
        # 일반 명사·머리글(「포병훈련장 사격일정」·「사격부대 사격장」·「박격포 표적지」)은 장소가 아니다
        places = list(dict.fromkeys(x for x in re.findall(r"[가-힣0-9]+ ?(?:사격장|훈련장|피탄지|표적지)", body)
                                    if not re.match(r"(포병|사격|사격부대|분|구분|박격포) ?(사격장|훈련장|피탄지|표적지)$", x)))
    arms = list(dict.fromkeys(re.sub(r"\s+", "", m) for m in _ARMS.findall(title + "\n" + body)))
    return {"unit": unit_of(title, body), "first": days[0] if days else None, "last": days[-1] if days else None,
            "days": days, "places": places[:4], "arms": arms[:3]}


def post(src: str, title: str, url: str, posted: str, body: str) -> dict:
    body = body[:BODY_MAX]
    return {"source": src, "title": title.strip(), "url": url, "posted": posted, "body": body} | derive(title, body, posted)


# ── 철원: RSS 한 번에 본문까지 ─────────────────────────────────
def parse_cheorwon(xml: str) -> list[dict]:
    out = []
    for it in re.findall(r"<item>(.*?)</item>", xml, re.S):
        def f(tag: str) -> str:
            m = re.search(rf"<{tag}>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</{tag}>", it, re.S)
            return m.group(1).strip() if m else ""
        title = html.unescape(f("title")).replace("＇", "'")
        if not KEYWORD.search(title):
            continue
        try:
            posted = datetime.strptime(f("pubDate"), "%a, %d %b %Y %H:%M:%S %z").astimezone(KST).date().isoformat()
        except ValueError:
            posted = None
        out.append(post("cheorwon", title, f("link"), posted, text_of(html.unescape(f("description")))))
    return out


def fetch_cheorwon(get=_http) -> list[dict]:
    return parse_cheorwon(get(CW_RSS))


# ── 고성: 검색 목록(POST) → 새 글만 본문 ───────────────────────
def parse_goseong_list(page: str) -> list[tuple[str, str]]:
    rows = re.findall(r"fn_search_detail\('([A-Za-z0-9]+)'\);\s*return false;\">([^<]*)</a>", page)
    return [(nid, html.unescape(t).strip()) for nid, t in rows if KEYWORD.search(html.unescape(t))]


def parse_goseong_view(page: str) -> tuple[str | None, str]:
    m = re.search(r"<i>등록일</i>\s*(\d{4}-\d{2}-\d{2})", page)
    i = page.find("bbs--view--content")
    j = page.find("kogl--mask", i) if i >= 0 else -1
    j = page.rfind("<", i, j) if j > i else j  # 「<div class="kogl--mask」 의 여는 꺾쇠에서 자른다(태그 조각이 본문에 남지 않게)
    body = text_of(page[page.find(">", i) + 1:j]) if i >= 0 and j > i else ""
    return (m.group(1) if m else None), body


def fetch_goseong(cached: dict[str, dict], get=_http) -> list[dict]:
    rows = parse_goseong_list(get(GS_PAGE, {"searchCondition": "all", "searchKeyword": "사격", "pageIndex": "1"}))
    out = []
    for nid, title in rows:
        url = GS_BASE + "view.do?nttId=" + nid
        if url in cached:  # 본문은 거의 안 바뀐다 — 목록에 남아 있는 동안 캐시
            out.append(cached[url])
            continue
        posted, body = parse_goseong_view(get(url))
        out.append(post("goseong", title, url, posted, body))
    return out


# ── 첨부: HWP 5.x(OLE + zlib 레코드) · PDF ─────────────────────
def hwp_text(data: bytes) -> str:
    """HWP 5.x 본문 문단 텍스트(HWPTAG_PARA_TEXT=67). 표 칸은 한 줄씩 나온다. 인라인·확장 컨트롤 문자(8 WCHAR)는 건너뛴다."""
    import io
    import struct
    import zlib
    import olefile
    ole = olefile.OleFileIO(io.BytesIO(data))
    compressed = bool(struct.unpack("<I", ole.openstream("FileHeader").read()[36:40])[0] & 1)
    out = []
    for sec in sorted((e for e in ole.listdir() if e[0] == "BodyText"), key=lambda e: int(e[1][7:])):
        b = ole.openstream(sec).read()
        b = zlib.decompress(b, -15) if compressed else b
        i = 0
        while i + 4 <= len(b):
            h = struct.unpack("<I", b[i:i + 4])[0]
            i += 4
            tag, size = h & 0x3FF, (h >> 20) & 0xFFF
            if size == 0xFFF:
                size = struct.unpack("<I", b[i:i + 4])[0]
                i += 4
            if tag == 67:
                t, j, line = b[i:i + size].decode("utf-16le", "replace"), 0, []
                while j < len(t):
                    c = ord(t[j])
                    if c < 32:
                        j += 8 if c in (1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23) else 1
                        continue
                    line.append(t[j])
                    j += 1
                out.append("".join(line).strip())
            i += size
    return "\n".join(x for x in out if x)


def hwpx_text(data: bytes) -> str:
    """HWPX(zip + OWPML) 본문 — Contents/section*.xml 의 문단(hp:p)마다 글자(hp:t)를 잇는다. 표준 라이브러리만."""
    import io
    import zipfile
    out = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for n in sorted(x for x in z.namelist() if re.match(r"Contents/section\d+\.xml$", x)):
            x = z.read(n).decode("utf-8", "replace")
            for para in re.findall(r"<hp:p\b.*?</hp:p>", x, re.S):
                t = "".join(html.unescape(m) for m in re.findall(r"<hp:t\b[^>]*>(.*?)</hp:t>", para, re.S))
                if t.strip():
                    out.append(re.sub(r"<[^>]+>", "", t).strip())
    return "\n".join(out)


def pdf_text(data: bytes) -> str:
    import io
    import pypdf
    return "\n".join(p.extract_text() or "" for p in pypdf.PdfReader(io.BytesIO(data)).pages)


def attach_text(name: str, data: bytes) -> str:
    """첨부 1건 → 평문. 해석기가 없거나 깨진 파일이면 빈 문자열(본문만 쓴다)."""
    try:
        if name.lower().endswith(".hwp"):
            return hwp_text(data)
        if name.lower().endswith(".hwpx"):
            return hwpx_text(data)
        if name.lower().endswith(".pdf"):
            return pdf_text(data)
    except Exception:  # noqa: BLE001
        return ""
    return ""


# ── 파주: 새소식 검색 → 본문 + HWP ─────────────────────────────
def parse_paju_list(page: str) -> list[tuple[str, str]]:
    rows = re.findall(r"jsView\('2001',\s*'(\d+)'[^)]*\)[^>]*>(.*?)</a>", page, re.S)
    out = []
    for seq, t in rows:
        title = text_of(t).split("\n")[0].strip()
        if TITLE_KEEP.search(title):
            out.append((seq, title))
    return out


def parse_paju_view(page: str) -> tuple[str | None, str, list[tuple[str, str]]]:
    m = re.search(r"작성일</span>\s*(\d{4})/(\d{2})/(\d{2})", page)
    i = page.find('class="article-conetnt"')  # 사이트 표기 그대로(conetnt)
    body = text_of(page[page.find(">", i) + 1:page.find("</div>", i)]) if i >= 0 else ""
    files = [(html.unescape(n).strip(), u) for u, n in
             re.findall(r'href="(/component/file/ND_fileDownload\.do\?id=[^"]+)"[^>]*>([^<]+)</a>', page)]
    return (f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None), body, files


def fetch_paju(cached: dict[str, dict], get=_http, getb=_fetch) -> list[dict]:
    rows = parse_paju_list(get(PJ + "/user/board/BD_board.list.do?" + urllib.parse.urlencode({"bbsCd": "2001", "q_searchVal": "사격"})))
    out = []
    for seq, title in rows:
        url = f"{PJ}/user/board/BD_board.view.do?bbsCd=2001&seq={seq}"
        if url in cached:
            out.append(cached[url])
            continue
        posted, body, files = parse_paju_view(get(url))
        att = "\n".join(attach_text(n, getb(PJ + u)) for n, u in files if n.lower().endswith((".hwp", ".hwpx")))
        out.append(post("paju", title, url, posted, "\n".join(x for x in (body, att) if x)))
    return out


# ── 화천: 공지 제목 검색 → PDF ────────────────────────────────
def parse_hwacheon_list(page: str) -> list[tuple[str, str]]:
    rows = re.findall(r'href="\./selectBbsNttView\.do\?[^"]*nttNo=(\d+)[^"]*"[^>]*>(.*?)</a>', page, re.S)
    out = []
    for n, t in rows:
        title = re.sub(r"\s*핫이슈\s*$", "", text_of(t).replace("\n", " ")).strip()
        if KEYWORD.search(title):
            out.append((n, title))
    return out


def parse_hwacheon_view(page: str) -> tuple[str | None, str, list[tuple[str, str]]]:
    m = re.search(r"작성일\s*<strong>(\d{4}-\d{2}-\d{2})", page)
    i = page.find('class="bbs_content"')
    body = text_of(page[page.find(">", i) + 1:page.find("</td>", i)]) if i >= 0 else ""
    files = [(html.unescape(nm).strip(), u) for u, nm in
             re.findall(r'href="/www/(downloadBbsFile\.do\?atchmnflNo=\d+)"[^>]*>(?:\s|<[^>]+>)*([^<]+)', page)]
    return (m.group(1) if m else None), body, files


def fetch_hwacheon(cached: dict[str, dict], get=_http, getb=_fetch) -> list[dict]:
    q = urllib.parse.urlencode({"bbsNo": "11", "key": "2338", "searchCnd": "SJ", "searchKrwd": "사격"})
    out = []
    for n, title in parse_hwacheon_list(get(HC + "selectBbsNttList.do?" + q))[:6]:  # 월 1~2건 — 최근 6건이면 45일을 덮는다
        url = f"{HC}selectBbsNttView.do?key=2338&bbsNo=11&nttNo={n}"
        if url in cached:
            out.append(cached[url])
            continue
        posted, body, files = parse_hwacheon_view(get(url))
        att = "\n".join(attach_text(nm, getb(HC + u)) for nm, u in files if nm.lower().endswith(".pdf"))
        out.append(post("hwacheon", title, url, posted, "\n".join(x for x in (body, att) if x)))
    return out


# ── 실행 ─────────────────────────────────────────────────────
def keep(p: dict, today: date) -> bool:
    ref = p.get("last") or p.get("posted")
    return bool(ref) and date.fromisoformat(ref) >= today - timedelta(days=KEEP_DAYS)


def run(prev: dict, now: datetime, fetchers: dict | None = None) -> tuple[dict, int]:
    today = now.astimezone(KST).date()
    srcs = {k: dict(prev.get("sources", {}).get(k, {}), **v) for k, v in SOURCES.items()}
    old = {k: [p for p in prev.get("posts", []) if p.get("source") == k] for k in SOURCES}
    fetchers = fetchers or {"cheorwon": lambda cache: fetch_cheorwon(),
                            "goseong": lambda cache: fetch_goseong(cache),
                            "paju": lambda cache: fetch_paju(cache),
                            "hwacheon": lambda cache: fetch_hwacheon(cache)}
    posts, rc = [], 0
    for k in SOURCES:
        try:
            got = fetchers[k]({p["url"]: p for p in old[k]})
            srcs[k].update(status="ok", status_detail="", last_ok_at=now.isoformat(), count=len(got))
        except Exception as e:  # noqa: BLE001 — 한 시군 실패가 다른 시군을 막지 않는다
            got, rc = old[k], 3
            srcs[k].update(status="error", status_detail=str(e)[:160])
        posts += got
    for p in posts:  # 캐시된 글도 요약 규칙을 지금 판으로 다시 적용
        p.update(derive(p.get("title", ""), p.get("body", ""), p.get("posted")))
    posts = [p for p in posts if keep(p, today)]
    for p in posts:  # 표시용 — 오늘이 공지된 사격일이면 진행 중(첫날~마지막 날 사이의 빈 날은 아니다)
        p["active_now"] = today.isoformat() in (p.get("days") or [])
    posts.sort(key=lambda p: (not p["active_now"], -(date.fromisoformat(p["last"] or p["posted"] or "2000-01-01").toordinal())))
    doc = {"source": "접경 지자체 공지(철원 RSS · 고성 공지 · 파주 새소식+HWP · 화천 공지+PDF) — 군 사격훈련 주민 안내",
           "note": "참고 자료 · 등급 판정과 무관. 일정은 부대 사정·기상으로 바뀔 수 있다.",
           "fetched_at": now.isoformat(), "sources": srcs, "posts": posts}
    return doc, rc


def due(prev: dict, now: datetime) -> bool:
    oks = [s.get("last_ok_at") for s in prev.get("sources", {}).values()]
    if not prev or any(s.get("status") != "ok" for s in prev.get("sources", {}).values()) or not all(oks):
        return True
    return now - min(datetime.fromisoformat(t) for t in oks) >= timedelta(hours=MIN_INTERVAL_H)


def main() -> int:
    prev = json.loads(OUT.read_text()) if OUT.exists() else {}
    now = datetime.now(timezone.utc)
    if not due(prev, now):
        print("drills_kr: 직전 성공이 %d시간 안 — 건너뜀" % MIN_INTERVAL_H)
        return 0
    doc, rc = run(prev, now)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({k: {"status": v["status"], "count": v.get("count")} for k, v in doc["sources"].items()}
                     | {"posts": len(doc["posts"])}, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    sys.exit(main())

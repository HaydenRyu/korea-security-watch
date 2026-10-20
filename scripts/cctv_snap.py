"""접경 CCTV 화면을 한 장씩 찍어 중계 Worker 에 올린다 (한국 IP 호스트에서 5분마다).
1시간에 한 번은 「라이브 방송」 국내 채널의 현재 영상 ID 도 같이 올린다(live_ids).
항공기 위치(air_snapshot)도 같이 올린다 — adsb.lol 이 Cloudflare 출구를 막아 Worker 가 직접 못 받는다(10/18 503).
GitHub 예약 실행(cron)이 자주 빠지므로 마지막 실행이 오래된 워크플로는 여기서 깨운다(kick_workflows).

  python3 scripts/cctv_snap.py            # ~/.config/ksw/its_key · cctv_upload 사용
  python3 scripts/cctv_snap.py --dry      # 올리지 않고 결과만 출력

교통정보센터는 해외 IP 를 막는다(GitHub 러너·Cloudflare 실측 Timeout) — 그래서 이 스크립트는 한국 호스트에서 돈다.
재생 주소에는 인증키가 서명으로 들어 있으므로 사진만 올리고 주소는 어디에도 남기지 않는다.
"""
from __future__ import annotations

import base64
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONF = Path.home() / ".config" / "ksw"
RELAY = "https://jis-cctv.jis-probe.workers.dev/upload"
API = ("https://openapi.its.go.kr:9443/cctvInfo?apiKey={key}&type={t}&cctvType=4"
       "&minX={x0}&maxX={x1}&minY={y0}&maxY={y1}&getType=json")
GRAB_TIMEOUT = 20   # 카메라 1대 상한(초)
BUDGET = 150        # 회차 전체 상한(초) — 5분 주기의 절반
LIVE_EVERY = 3600   # 라이브 ID 재조회 간격(초)
LIVE_MARK = Path.home() / "Library" / "Caches" / "jis-live-at"   # 마지막 조회 시각(파일 mtime)
# 10/18 실측: firms(:45 매시)는 하루 1회만, weekly(일 11:00Z)는 아예 안 돌았다 → 주간 요약 알림 누락
KICK = {"firms.yml": ("hourly", 70 * 60), "watchdog.yml": ("hourly", 7 * 3600), "weekly.yml": ("sun11z", 20 * 60)}
# 받은 재생 주소 재사용 — 5분마다 2~4회 조회(월 1.7만~3.5만)는 한도 해제 뒤 다시 바닥난다(10/18).
# 주소(인증키 서명 포함)는 이 맥 캐시에만 둔다(600). 유효 시간은 실측 전 — 11/1 뒤 성공률로 URL_TTL 을 조정한다
URL_CACHE = Path.home() / "Library" / "Caches" / "jis-its-urls.json"
# 10/20 실측(4001 문구): 인증키 월 호출 한도 1,000 — 30분 TTL(월 1,440+) 로도 넘친다. 100분 TTL(주소는 2시간 만료) · 재조회 60분 → 최악 24회/일 ≈ 744/월
URL_TTL = 100 * 60       # 캐시 주소를 쓰는 최대 나이(초)
MIN_REFETCH = 60 * 60    # 실패 재조회 사이 최소 간격(초) — 재생 서버 403 이 잦아도 조회를 몰아 쓰지 않는다
ITS_MONTHLY_CAP = 1000   # 인증키 상세정보의 월 호출 한도(hayden 10/20 확인) — 증설하면 여기만 올린다
ITS_BUDGET = int(ITS_MONTHLY_CAP * 0.9)   # 우리 쪽 상한 — 서버 4001 을 받기 전에 멈춘다(실패 호출도 센다)
CALL_LOG = Path.home() / "Library" / "Caches" / "jis-its-calls.json"   # {"month": "YYYY-MM", "n": 호출 수}
QUOTA_MARK = Path.home() / "Library" / "Caches" / "jis-its-quota"   # 월 호출 한도 초과(4001) — 다음 달 1일까지 CCTV 조회 중단
KICK_MARK = Path.home() / "Library" / "Caches" / "jis-kick"   # 워크플로별 마지막 깨운 시각(파일 mtime)
REPO = "HaydenRyu/korea-security-watch"
# 공역 지도(10/20): 기체 모양·사진·최근 궤적 복원(adsb.lol iframe 을 버리며 삼각형만 남았었다) + 대만 해협 작은 창
TW_AREA = "https://api.adsb.lol/v2/lat/24.5/lon/120.5/dist/250"   # 대만 해협 — 한반도와 같은 「민항 통항」 지표용(중국군 기체는 ADS-B 를 안 켠다)
TRAIL_CACHE = Path.home() / "Library" / "Caches" / "jis-air-trail.json"   # hex → [[ts, lat, lon], …] 최근 35분
TRAIL_KEEP = 35 * 60
PHOTO_CACHE = Path.home() / "Library" / "Caches" / "jis-plane-photos.json"  # hex → {"p": {src,link,by}|None, "at": ts}
PHOTO_API = "https://api.planespotters.net/pub/photos/hex/{hex}"   # 공개 API — 서버 UA 에 연락처 URL 필수(없으면 403)
PHOTO_UA = "jis-watch/1.0 (+https://haydenryu.github.io/korea-security-watch/)"
PHOTO_TTL, PHOTO_NEG_TTL, PHOTO_MAX_NEW = 30 * 86400, 7 * 86400, 15   # 있음 30일·없음 7일 캐시, 회차당 새 조회 15건


def live_ids() -> dict | None:
    """data/live.json 채널마다 지금 라이브 영상 ID.
    국내(pat 있음) = /streams 에서 「지금 라이브」 + 제목 패턴 · 그 밖 = /live 가 가리키는 라이브 영상
    (embed/live_stream?channel= 은 ABC·Bloomberg 처럼 막히는 채널이 있다 — 10/18 실측, 영상 ID 로는 재생).
    유튜브가 Cloudflare 출구 IP 에는 429 를 줘서(10/18 실측) Worker 가 못 하고 이 호스트가 한다. 한 시간에 한 번."""
    try:
        if time.time() - LIVE_MARK.stat().st_mtime < LIVE_EVERY:
            return None
    except OSError:
        pass
    out = {}
    doc = json.loads((ROOT / "data" / "live.json").read_text())
    for c in [c for t in doc["tabs"] for c in doc[t["id"]]]:
        if not c.get("pat"):
            try:
                req = urllib.request.Request(f"https://www.youtube.com/channel/{c['ch']}/live",
                                             headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en"})
                h = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")
            except OSError:
                continue
            v = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([\w-]{11})"', h)
            if v and '"isLive":true' in h and '"playableInEmbed":true' in h:
                out[c["ch"]] = v.group(1)
            continue
        try:
            req = urllib.request.Request(f"https://www.youtube.com/channel/{c['ch']}/streams",
                                         headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ko-KR"})
            h = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")
        except OSError:
            continue
        ids = list(re.finditer(r'"contentId":"([\w-]{11})"', h))
        for k, m in enumerate(ids):
            chunk = h[m.start():ids[k + 1].start() if k + 1 < len(ids) else m.start() + 20000]
            t = re.search(r'"title":\{"content":"([^"]+)"', chunk)
            if "BADGE_STYLE_LIVE" in chunk and t and c["pat"] in t.group(1):
                out[c["ch"]] = m.group(1)
                break
    LIVE_MARK.parent.mkdir(parents=True, exist_ok=True)
    LIVE_MARK.touch()
    return out or None


def _due(kind: str, arg: int, last: datetime | None, now: datetime) -> bool:
    if kind == "hourly":   # arg = 허용 공백(초)
        return last is None or (now - last).total_seconds() > arg
    slot = (now - timedelta(days=(now.weekday() + 1) % 7)).replace(hour=11, minute=0, second=0, microsecond=0)
    if slot > now:         # 일요일 11:00Z 이전이면 지난주 슬롯
        slot -= timedelta(days=7)
    return (now - slot).total_seconds() > arg and (last is None or last < slot)


def quota_blocked() -> str | None:
    """한도 초과 표시가 이번 달 것이면 해제일(다음 달 1일, KST) 문자열."""
    if not QUOTA_MARK.exists():
        return None
    until = QUOTA_MARK.read_text().strip()
    return until if datetime.now(timezone(timedelta(hours=9))).date().isoformat() < until else None


def _month() -> str:
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m")


def calls_this_month(path: Path = CALL_LOG) -> int:
    try:
        d = json.loads(path.read_text())
        return int(d["n"]) if d.get("month") == _month() else 0
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def note_call(path: Path = CALL_LOG) -> int:
    """ITS 호출 1회를 이번 달 장부에 더한다(성공·실패 무관 — 서버는 둘 다 센다고 본다). 달이 바뀌면 0부터."""
    n = calls_this_month(path) + 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"month": _month(), "n": n}))
    return n


def block_quota() -> str:
    d = datetime.now(timezone(timedelta(hours=9))).date().replace(day=1)
    until = (d.replace(year=d.year + 1, month=1) if d.month == 12 else d.replace(month=d.month + 1)).isoformat()
    QUOTA_MARK.parent.mkdir(parents=True, exist_ok=True)
    QUOTA_MARK.write_text(until)
    return until


def kick_workflows() -> dict:
    """예약 시각을 놓친 워크플로를 workflow_dispatch. gh 가 없거나 실패하면 조용히 넘어간다(사진이 본업)."""
    gh = shutil.which("gh") or next((p for p in ("/opt/homebrew/bin/gh", "/usr/local/bin/gh") if Path(p).exists()), None)
    if not gh:
        return {"gh": "missing"}
    now, out = datetime.now(timezone.utc), {}
    KICK_MARK.mkdir(parents=True, exist_ok=True)
    for wf, (kind, arg) in KICK.items():
        mark = KICK_MARK / wf
        if mark.exists() and time.time() - mark.stat().st_mtime < 15 * 60:
            continue   # 깨운 직후 실행이 목록에 뜨기까지의 중복 방지
        try:
            r = subprocess.run([gh, "run", "list", "-R", REPO, "-w", wf, "-L", "1", "--json", "createdAt"],
                               capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                out[wf] = "list-fail"
                continue
            runs = json.loads(r.stdout or "[]")
            last = datetime.fromisoformat(runs[0]["createdAt"].replace("Z", "+00:00")) if runs else None
            if not _due(kind, arg, last, now):
                continue
            r = subprocess.run([gh, "workflow", "run", wf, "-R", REPO], capture_output=True, text=True, timeout=30)
            mark.touch()
            out[wf] = "kicked" if r.returncode == 0 else f"fail:{r.stderr.strip()[:80]}"
        except (subprocess.SubprocessError, OSError, ValueError, KeyError, IndexError) as e:
            out[wf] = f"err:{type(e).__name__}"
    return out


STALE_MARK = Path.home() / "Library" / "Caches" / "jis-stale-alert"   # 알린 정지의 마지막 확인 시각(정지 1건당 1회)
STALE_MIN = 100   # 루틴은 매시 :07 — 1시간 40분 넘게 새 확인이 없으면 정지로 본다


def stale_alert() -> str | None:
    """감시 루틴 정지를 본인 주제로 알린다(정지 1건당 1회, 재개 시 1회). GHA watchdog 은 6시간 주기라
    10/18 밤 4시간 정지를 못 잡았다 — 이 맥은 5분마다 돈다. 실패는 조용히 넘어간다(사진이 본업)."""
    try:
        with urllib.request.urlopen(f"https://haydenryu.github.io/korea-security-watch/state/latest.json?t={int(time.time())}",
                                    timeout=20) as r:
            doc = json.loads(r.read())
        # 「보류」 회차(내림 히스테리시스)는 checked_at 을 그대로 두고 held.at 만 쓴다 — 둘 중 늦은 쪽이 마지막 확인
        # (10/19 00:08 보류 회차를 못 보고 01:00 대에 정지 오경보)
        last = max([doc["checked_at"], (doc.get("held") or {}).get("at") or doc["checked_at"]], key=datetime.fromisoformat)
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() / 60
        sent = STALE_MARK.read_text().strip() if STALE_MARK.exists() else ""
        if age >= STALE_MIN and sent != last:
            msg = ("안보 감시 정지", f"마지막 확인 {int(age // 60)}시간 {int(age % 60)}분 전. 루틴이 멈췄습니다(사용량 한도·실행 오류). 상황판 등급이 갱신되지 않습니다.", 4)
        elif age < STALE_MIN and sent and sent != "ok":
            msg = ("안보 감시 재개", "감시 루틴이 다시 돌고 있습니다.", 2)
        else:
            return None
        topic = (CONF / "ntfy_me").read_text().strip()
        req = urllib.request.Request("https://ntfy.sh", headers={"Content-Type": "application/json"}, method="POST",
                                     data=json.dumps({"topic": topic, "title": msg[0], "message": msg[1], "priority": msg[2],
                                                      "click": "https://haydenryu.github.io/korea-security-watch/"}, ensure_ascii=False).encode())
        urllib.request.urlopen(req, timeout=20).close()
        STALE_MARK.parent.mkdir(parents=True, exist_ok=True)
        STALE_MARK.write_text(last if msg[2] == 4 else "ok")
        return msg[0]
    except (OSError, ValueError, KeyError):
        return None


# 민항기 등록국 = ICAO 24비트 주소 국가 할당 블록(ICAO Annex 10 Vol.III 부록) — 항공사 국적과 대개 같지만 리스기는 다를 수 있다
ICAO_CC = [("KR", 0x718000, 0x71FFFF), ("KP", 0x720000, 0x727FFF), ("CN", 0x780000, 0x7BFFFF),
           ("JP", 0x840000, 0x87FFFF), ("TW", 0x899000, 0x8993FF), ("US", 0xA00000, 0xAFFFFF), ("RU", 0x100000, 0x1FFFFF)]


def icao_cc(hexid: str | None) -> str | None:
    try:
        n = int((hexid or "").lstrip("~"), 16)
    except ValueError:
        return None
    return next((cc for cc, lo, hi in ICAO_CC if lo <= n <= hi), None)


def _json_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _save_cache(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc))
    path.chmod(0o600)


def merge_trail(prev: dict, ac: list[dict], now: float, keep: int = TRAIL_KEEP) -> dict:
    """hex 별 최근 위치 목록을 이번 회차 위치로 잇는다. keep 초보다 오래된 점과 사라진 기체는 버린다."""
    out = {}
    for h, pts in prev.items():
        pts = [p for p in pts if p[0] >= now - keep]
        if pts:
            out[h] = pts
    for a in ac:
        if a.get("hex"):
            pts = out.setdefault(a["hex"], [])
            if not pts or pts[-1][1:] != [a["lat"], a["lon"]]:
                pts.append([round(now), a["lat"], a["lon"]])
    return out


def photo_entry(doc: dict | None) -> dict | None:
    """planespotters 응답에서 첫 사진(큰 썸네일·링크·촬영자)만. 촬영자 표기는 API 약관."""
    ph = (doc or {}).get("photos") or []
    if not ph:
        return None
    x = ph[0]
    src = (x.get("thumbnail_large") or x.get("thumbnail") or {}).get("src")
    return {"src": src, "link": x.get("link"), "by": x.get("photographer")} if src else None


def fetch_photo(hexid: str) -> dict | None:
    req = urllib.request.Request(PHOTO_API.format(hex=hexid), headers={"User-Agent": PHOTO_UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"photos": []}
        raise


def photos_for(hexes: list[str], cache: dict, now: float, fetch=fetch_photo, max_new: int = PHOTO_MAX_NEW) -> dict:
    """캐시에 없거나 만료된 기체만 조회(회차당 max_new 건) — 조회 실패(404 외)는 캐시에 안 남겨 다음 회차에 다시."""
    new = 0
    for h in hexes:
        c = cache.get(h)
        if c and now - c["at"] < (PHOTO_TTL if c["p"] else PHOTO_NEG_TTL):
            continue
        if new >= max_new:
            break
        new += 1
        try:
            cache[h] = {"p": photo_entry(fetch(h)), "at": round(now)}
        except (OSError, ValueError):
            continue
    return cache


def _pack(a: dict, mil: bool, name: str | None) -> dict:
    alt = a.get("alt_baro")
    return {"hex": a.get("hex"), "lat": round(a["lat"], 3), "lon": round(a["lon"], 3), "trk": a.get("track", a.get("true_heading")),
            "alt": 0 if alt == "ground" else alt, "t": a.get("t"),
            "call": (a.get("flight") or "").replace("@", "").strip() or None, "mil": mil or None,
            "name": name, "cc": icao_cc(a.get("hex"))}


def air_snapshot() -> dict | None:
    """상황판 공역 지도용 항공기 목록(범위·군용 판정은 air_count.py 와 같은 상수)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import air_count as ac
    try:
        area, mil = ac.get(ac.AREA)["ac"], ac.get(ac.MIL)["ac"]
    except (OSError, ValueError, KeyError):
        return None
    milhex, seen, out = {a.get("hex") for a in mil}, set(), []
    for a in area + [m for m in mil if ac.inside(m, ac.BOX)]:
        if a.get("hex") in seen or a.get("lat") is None or a.get("lon") is None:
            continue
        seen.add(a.get("hex"))
        is_mil = a.get("hex") in milhex
        out.append(_pack(a, is_mil, ac.NAMED.get(a.get("t") or "") if is_mil else None))
    doc = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "src": "adsb.lol", "ac": out}
    now = time.time()
    try:   # 궤적·사진·대만은 부가 정보 — 실패해도 위치는 올라간다
        trail = merge_trail(_json_cache(TRAIL_CACHE), out, now)
        _save_cache(TRAIL_CACHE, trail)
        for a in out:
            pts = trail.get(a["hex"] or "", [])
            if len(pts) > 1:
                a["tr"] = [[p[1], p[2]] for p in pts[:-1]]
        photos = photos_for([a["hex"] for a in out if a["hex"]], _json_cache(PHOTO_CACHE), now)
        _save_cache(PHOTO_CACHE, photos)
        for a in out:
            if (c := photos.get(a["hex"] or "")) and c["p"]:
                a["photo"] = c["p"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(json.dumps({"air_extra": str(e)[:200]}))
    try:
        tw = [_pack(a, bool(a.get("dbFlags", 0) & 1), None) for a in ac.get(TW_AREA)["ac"] if a.get("lat") is not None and a.get("lon") is not None]
        doc["tw"] = {"src": "adsb.lol", "ac": tw}
    except (OSError, ValueError, KeyError):
        pass
    return doc


def ffmpeg() -> str:
    for p in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if Path(p).exists():
            return p
    raise SystemExit("ffmpeg 없음")


def url_cache() -> tuple[float, dict | None]:
    """(마지막 조회 시각, URL_TTL 안이면 주소 목록)."""
    try:
        d = json.loads(URL_CACHE.read_text())
        return d["at"], d["urls"] if time.time() - d["at"] < URL_TTL else None
    except (OSError, ValueError, KeyError):
        return 0.0, None


def save_url_cache(urls: dict) -> None:
    URL_CACHE.parent.mkdir(parents=True, exist_ok=True)
    URL_CACHE.write_text(json.dumps({"at": time.time(), "urls": urls}))
    URL_CACHE.chmod(0o600)


def area_urls(key: str, cams: list[dict]) -> dict[str, list[dict]]:
    """도로 종류별 1회 조회(카메라 전부를 덮는 상자) — 10/18 카메라마다 최대 3회 조회(5분에 최대 54회)로 월 한도를 넘겼다.
    한 회차 호출 = 종류 수(2) + 실패분 재조회 1바퀴(최대 2) = 최대 4회."""
    out: dict[str, list[dict]] = {}
    for road in sorted({c.get("road", "its") for c in cams}):
        cs = [c for c in cams if c.get("road", "its") == road]
        box = dict(x0=min(c["lon"] for c in cs) - .005, x1=max(c["lon"] for c in cs) + .005,
                   y0=min(c["lat"] for c in cs) - .005, y1=max(c["lat"] for c in cs) + .005)
        note_call()
        with urllib.request.urlopen(API.format(key=key, t=road, **box), timeout=30) as r:
            data = json.loads(r.read())["response"]["data"]
        out[road] = [data] if isinstance(data, dict) else data
    return out


def nearest(cam: dict, live: list[dict]) -> str | None:
    """이름이 같으면 그것, 아니면 300m 안의 가장 가까운 카메라(이름 표기가 가끔 바뀐다)."""
    for d in live:
        if d["cctvname"].strip() == cam["name"]:
            return d["cctvurl"]
    best, dist = None, 1e9
    for d in live:
        dy = (float(d["coordy"]) - cam["lat"]) * 111000
        dx = (float(d["coordx"]) - cam["lon"]) * 111000 * math.cos(math.radians(cam["lat"]))
        if (m := math.hypot(dx, dy)) < dist:
            best, dist = d["cctvurl"], m
    return best if dist < 300 else None


def grab(ff: str, url: str, out: Path) -> bool:
    try:
        subprocess.run([ff, "-nostdin", "-loglevel", "error", "-y", "-i", url, "-frames:v", "1",
                        "-vf", "scale=480:-2", "-q:v", "7", str(out)],
                       timeout=GRAB_TIMEOUT, check=True, capture_output=True)
        return out.exists() and out.stat().st_size > 2000
    except (subprocess.SubprocessError, OSError):
        return False


BUSY = ("daily_content_writer", "run_daily_naver", "auto_rewrite_runner")  # 맥부기의 회사 작업 — 도는 중이면 무거운 단계(ffmpeg 18대·라이브 ID·워크플로 깨우기)만 건너뛴다. 공역(GET 3번 + 업로드)은 계속 — 13:00 발행이 한 시간 넘게 돌면 공역이 그만큼 멎었다(10/20 실측 12:59→13:09)


def busy() -> str | None:
    for name in BUSY:
        if subprocess.run(["pgrep", "-f", name], capture_output=True).returncode == 0:
            return name
    return None


def main() -> int:
    dry = "--dry" in sys.argv
    light = busy() if not dry else None   # 회사 작업 중 = 가벼운 회차(공역만)
    if light:
        print(json.dumps({"light": light}))
    if not dry and (sa := stale_alert()):
        print(json.dumps({"stale": sa}, ensure_ascii=False))
    if not dry and not light and (k := kick_workflows()):
        print(json.dumps({"kick": k}))
    key = (CONF / "its_key").read_text().strip()
    cams = json.loads((ROOT / "data" / "cctv.json").read_text())["cameras"]
    ff, t0 = ffmpeg(), time.monotonic()
    quota = quota_blocked()
    if quota:
        print(json.dumps({"cctv": f"ITS 월 호출 한도 초과 — {quota} 까지 건너뜀"}, ensure_ascii=False))
    shots = [{"name": c["name"], "at": None, "img": None} for c in cams]
    with tempfile.TemporaryDirectory() as td:
        for n in range(0 if (quota or light) else 2):          # 1바퀴(캐시 주소 우선) + 실패분 1바퀴(새 주소 — 재생 서버 403 이 잦다)
            todo = [i for i, sh in enumerate(shots) if not sh["img"]]
            if not todo or time.monotonic() - t0 > BUDGET:
                break
            last, urls = url_cache()
            if n or not urls:
                if time.time() - last < MIN_REFETCH:
                    break   # 방금 받은 주소로도 실패 — 다음 회차에(Worker 가 직전 사진을 남긴다)
                if calls_this_month() >= ITS_BUDGET:
                    quota = block_quota()   # 우리 장부가 상한에 닿으면 서버 4001 전에 멈춘다
                    print(json.dumps({"cctv": f"ITS 월 호출 {calls_this_month()}회 — 상한 {ITS_BUDGET} 도달, {quota} 까지 중단"}, ensure_ascii=False))
                    break
                try:
                    urls = area_urls(key, cams)   # 전 카메라 상자 — 캐시가 다음 회차 전부를 덮게
                    save_url_cache(urls)
                except urllib.error.HTTPError as e:
                    if e.code == 401 and b'"resultCode":4001' in e.read().replace(b" ", b""):
                        quota = block_quota()   # 한 달 내내 401 을 두드리지 않는다
                        print(json.dumps({"cctv": f"ITS 월 호출 한도 초과 — {quota} 까지 중단"}, ensure_ascii=False))
                    break
                except (OSError, ValueError, KeyError):
                    break
            for i in todo:
                if time.monotonic() - t0 > BUDGET:
                    break
                c, p = cams[i], Path(td) / f"{i}.jpg"
                url = nearest(c, urls.get(c.get("road", "its"), []))
                if url and grab(ff, url, p):
                    shots[i]["img"] = base64.b64encode(p.read_bytes()).decode()
                    shots[i]["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ok = sum(1 for s in shots if s["img"])
    bundle = {"captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "shots": shots}
    if quota:
        bundle["paused"] = {"reason": "교통정보센터 월 호출 한도 초과", "until": quota}
    if not dry and not light and (lv := live_ids()):  # 못 찾은 채널은 빠진다 — 상황판이 data/live.json 의 fb 로 메운다
        bundle["live"], bundle["live_at"] = lv, bundle["captured_at"]
    if not dry and (air := air_snapshot()):
        bundle["air"] = air
    size = len(json.dumps(bundle))
    print(json.dumps({"ok": ok, "total": len(shots), "bytes": size, "sec": round(time.monotonic() - t0)}))
    if dry or (ok == 0 and "air" not in bundle):
        return 0 if ok else 1   # 올릴 게 없다 — 사진 전부 실패는 Worker 가 칸마다 직전 사진을 시각과 함께 남긴다
    req = urllib.request.Request(RELAY, data=json.dumps(bundle).encode(), method="POST", headers={
        "Authorization": "Bearer " + (CONF / "cctv_upload").read_text().strip(),
        "Content-Type": "application/json", "User-Agent": "jis-cctv-snap/1"})  # 기본 Python-urllib UA 는 Cloudflare 가 1010 으로 막는다
    with urllib.request.urlopen(req, timeout=30) as r:
        print("upload", r.status)
    return 0


if __name__ == "__main__":
    sys.exit(main())

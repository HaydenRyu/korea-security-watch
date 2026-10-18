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
TRIES = 3           # 재생 서버가 수시로 403 을 준다(10/18 실측) — 매번 새 주소를 받아 다시 시도
GRAB_TIMEOUT = 20   # 카메라 1대 상한(초)
BUDGET = 150        # 회차 전체 상한(초) — 5분 주기의 절반
LIVE_EVERY = 3600   # 라이브 ID 재조회 간격(초)
LIVE_MARK = Path.home() / "Library" / "Caches" / "jis-live-at"   # 마지막 조회 시각(파일 mtime)
# 10/18 실측: firms(:45 매시)는 하루 1회만, weekly(일 11:00Z)는 아예 안 돌았다 → 주간 요약 알림 누락
KICK = {"firms.yml": ("hourly", 70 * 60), "watchdog.yml": ("hourly", 7 * 3600), "weekly.yml": ("sun11z", 20 * 60)}
QUOTA_MARK = Path.home() / "Library" / "Caches" / "jis-its-quota"   # 월 호출 한도 초과(4001) — 다음 달 1일까지 CCTV 조회 중단
KICK_MARK = Path.home() / "Library" / "Caches" / "jis-kick"   # 워크플로별 마지막 깨운 시각(파일 mtime)
REPO = "HaydenRyu/korea-security-watch"


def live_ids() -> dict | None:
    """data/live.json 국내 채널마다 /streams 에서 「지금 라이브」 + 제목 패턴인 영상 ID.
    유튜브가 Cloudflare 출구 IP 에는 429 를 줘서(10/18 실측) Worker 가 못 하고 이 호스트가 한다. 한 시간에 한 번."""
    try:
        if time.time() - LIVE_MARK.stat().st_mtime < LIVE_EVERY:
            return None
    except OSError:
        pass
    out = {}
    for c in json.loads((ROOT / "data" / "live.json").read_text())["domestic"]:
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
        alt = a.get("alt_baro")
        out.append({"lat": round(a["lat"], 3), "lon": round(a["lon"], 3), "trk": a.get("track", a.get("true_heading")),
                    "alt": 0 if alt == "ground" else alt, "t": a.get("t"),
                    "call": (a.get("flight") or "").replace("@", "").strip() or None, "mil": (a.get("hex") in milhex) or None,
                    "name": ac.NAMED.get(a.get("t") or "") if a.get("hex") in milhex else None})
    return {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "src": "adsb.lol", "ac": out}


def ffmpeg() -> str:
    for p in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if Path(p).exists():
            return p
    raise SystemExit("ffmpeg 없음")


def live_urls(key: str, cam: dict) -> list[dict]:
    """카메라 주변 약 500m 만 조회한다 — 재생 주소는 받은 직후에 써야 잘 열린다."""
    box = dict(x0=cam["lon"] - .005, x1=cam["lon"] + .005, y0=cam["lat"] - .005, y1=cam["lat"] + .005)
    with urllib.request.urlopen(API.format(key=key, t=cam.get("road", "its"), **box), timeout=30) as r:
        data = json.loads(r.read())["response"]["data"]
    return [data] if isinstance(data, dict) else data


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


BUSY = ("daily_content_writer", "run_daily_naver", "auto_rewrite_runner")  # 맥부기의 회사 작업 — 도는 중이면 이 회차는 건너뛴다


def busy() -> str | None:
    for name in BUSY:
        if subprocess.run(["pgrep", "-f", name], capture_output=True).returncode == 0:
            return name
    return None


def main() -> int:
    dry = "--dry" in sys.argv
    if (b := busy()) and not dry:
        print(json.dumps({"skip": b}))
        return 0
    if not dry and (k := kick_workflows()):
        print(json.dumps({"kick": k}))
    key = (CONF / "its_key").read_text().strip()
    cams = json.loads((ROOT / "data" / "cctv.json").read_text())["cameras"]
    ff, t0 = ffmpeg(), time.monotonic()
    shots, quota = [], quota_blocked()
    if quota:
        print(json.dumps({"cctv": f"ITS 월 호출 한도 초과 — {quota} 까지 건너뜀"}, ensure_ascii=False))
    with tempfile.TemporaryDirectory() as td:
        for i, c in enumerate(cams):
            img, at, p = None, None, Path(td) / f"{i}.jpg"
            for _ in range(0 if quota else TRIES):
                if time.monotonic() - t0 > BUDGET:
                    break
                try:
                    url = nearest(c, live_urls(key, c))
                except urllib.error.HTTPError as e:
                    if e.code == 401 and b'"resultCode":4001' in e.read().replace(b" ", b""):
                        quota = block_quota()   # 10/18: 5분 × 카메라마다 조회로 한도 소진 — 한 달 내내 401 을 두드리지 않는다
                        print(json.dumps({"cctv": f"ITS 월 호출 한도 초과 — {quota} 까지 중단"}, ensure_ascii=False))
                        break
                    url = None
                except (OSError, ValueError, KeyError):
                    url = None
                if url and grab(ff, url, p):
                    img = base64.b64encode(p.read_bytes()).decode()
                    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    break
                time.sleep(2)
            shots.append({"name": c["name"], "at": at, "img": img})
    ok = sum(1 for s in shots if s["img"])
    bundle = {"captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "shots": shots}
    if quota:
        bundle["paused"] = {"reason": "교통정보센터 월 호출 한도 초과", "until": quota}
    if not dry and (lv := live_ids()):  # 못 찾은 채널은 빠진다 — 상황판이 data/live.json 의 fb 로 메운다
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

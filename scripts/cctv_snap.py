"""접경 CCTV 12대 화면을 한 장씩 찍어 중계 Worker 에 올린다 (한국 IP 호스트에서 5분마다).

  python3 scripts/cctv_snap.py            # ~/.config/ksw/its_key · cctv_upload 사용
  python3 scripts/cctv_snap.py --dry      # 올리지 않고 결과만 출력

교통정보센터는 해외 IP 를 막는다(GitHub 러너·Cloudflare 실측 Timeout) — 그래서 이 스크립트는 한국 호스트에서 돈다.
재생 주소에는 인증키가 서명으로 들어 있으므로 사진만 올리고 주소는 어디에도 남기지 않는다.
"""
from __future__ import annotations

import base64
import json
import math
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONF = Path.home() / ".config" / "ksw"
RELAY = "https://jis-cctv.jis-probe.workers.dev/upload"
API = ("https://openapi.its.go.kr:9443/cctvInfo?apiKey={key}&type={t}&cctvType=4"
       "&minX={x0}&maxX={x1}&minY={y0}&maxY={y1}&getType=json")
TRIES = 3           # 재생 서버가 수시로 403 을 준다(10/18 실측) — 매번 새 주소를 받아 다시 시도
GRAB_TIMEOUT = 20   # 카메라 1대 상한(초)
BUDGET = 150        # 회차 전체 상한(초) — 5분 주기의 절반


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
    key = (CONF / "its_key").read_text().strip()
    cams = json.loads((ROOT / "data" / "cctv.json").read_text())["cameras"]
    ff, t0 = ffmpeg(), time.monotonic()
    shots = []
    with tempfile.TemporaryDirectory() as td:
        for i, c in enumerate(cams):
            img, at, p = None, None, Path(td) / f"{i}.jpg"
            for _ in range(TRIES):
                if time.monotonic() - t0 > BUDGET:
                    break
                try:
                    url = nearest(c, live_urls(key, c))
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
    size = len(json.dumps(bundle))
    print(json.dumps({"ok": ok, "total": len(shots), "bytes": size, "sec": round(time.monotonic() - t0)}))
    if dry or ok == 0:
        return 0 if ok else 1   # 전부 실패면 올리지 않는다 — 직전 사진이 시각과 함께 남는 편이 낫다
    req = urllib.request.Request(RELAY, data=json.dumps(bundle).encode(), method="POST", headers={
        "Authorization": "Bearer " + (CONF / "cctv_upload").read_text().strip(),
        "Content-Type": "application/json", "User-Agent": "jis-cctv-snap/1"})  # 기본 Python-urllib UA 는 Cloudflare 가 1010 으로 막는다
    with urllib.request.urlopen(req, timeout=30) as r:
        print("upload", r.status)
    return 0


if __name__ == "__main__":
    sys.exit(main())

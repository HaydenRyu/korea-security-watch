"""접경 도로 CCTV 12대의 최근 화면 한 장씩 → <outdir>/cctv/<idx>.jpg + index.json

  ITS_KEY=<인증키> python3 scripts/cctv_snap.py _site

Pages 배포 워크플로(.github/workflows/pages.yml)가 30분마다 부른다. 이미지는 git 에 커밋하지 않고
배포 아티팩트에만 싣는다. 재생 주소(HLS)는 서명에 인증키가 들어 있어 2시간 만료 — 주소·키는
출력·저장하지 않는다(ffmpeg 오류 메시지도 주소를 담을 수 있어 버린다).

카메라 목록은 data/cctv.json(scripts/its_cctv.py 산출), 주소는 같은 API 를 이름으로 대조한다.
rc: 0 = 한 대 이상 성공 · 1 = 전부 실패 · 2 = ITS_KEY 없음
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# its_cctv.py 와 같은 요청(범위·형식). 바꾸면 두 곳을 같이 바꾼다
API = ("https://openapi.its.go.kr:9443/cctvInfo?apiKey={key}&type={t}&cctvType=4"
       "&minX=125.9&maxX=128.7&minY=37.85&maxY=38.9&getType=json")
FFMPEG_TIMEOUT = 30


def url_map(key: str) -> dict[str, str]:
    urls: dict[str, str] = {}
    for t in ("its", "ex"):  # its = 국도, ex = 고속도로
        try:
            with urllib.request.urlopen(API.format(key=key, t=t), timeout=40) as r:
                data = json.loads(r.read())["response"]["data"]
        except Exception as e:  # 예외 문자열에 주소가 섞일 수 있어 종류만 남긴다
            print(f"API {t} 실패: {type(e).__name__}")
            continue
        for d in data or []:
            name, url = (d.get("cctvname") or "").strip(), d.get("cctvurl") or ""
            if name and url:
                urls[name] = url
    return urls


def snap(url: str, out: Path) -> bool:
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-rw_timeout", "15000000", "-i", url,
           "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "6", str(out)]
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=FFMPEG_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return r.returncode == 0 and out.exists() and out.stat().st_size > 0


def main() -> int:
    if len(sys.argv) != 2:
        print("사용법: cctv_snap.py <outdir>")
        return 2
    key = os.environ.get("ITS_KEY")
    if not key:
        print("ITS_KEY 없음")
        return 2
    cams = json.loads((ROOT / "data" / "cctv.json").read_text())["cameras"]
    out = Path(sys.argv[1]) / "cctv"
    out.mkdir(parents=True, exist_ok=True)
    urls = url_map(key)
    shots = []
    for i, c in enumerate(cams):
        f = out / f"{i}.jpg"
        url = urls.get(c["name"])
        ok = bool(url) and snap(url, f)
        if not ok:
            f.unlink(missing_ok=True)  # 반쯤 쓴 파일을 남기지 않는다
        shots.append({"i": i, "name": c["name"], "ok": ok, "file": f"cctv/{i}.jpg" if ok else None})
        print(f"{i:2d} {'ok ' if ok else ('no-url' if not url else 'fail')} {c['name']}")
    doc = {"taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "shots": shots}
    (out / "index.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    n = sum(s["ok"] for s in shots)
    print(json.dumps({"cameras": len(cams), "matched": sum(1 for c in cams if c["name"] in urls), "ok": n},
                     ensure_ascii=False))
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main())

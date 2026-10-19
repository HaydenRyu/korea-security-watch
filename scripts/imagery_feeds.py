"""위성영상 분석 글 목록(38 North · CSIS Beyond Parallel) → data/imagery_reports.json

  python3 scripts/imagery_feeds.py [--out data/imagery_reports.json]

- 각 사이트의 「satellite-imagery」 태그 RSS 만 읽는다. **제목·링크·날짜만** 남긴다 — 본문·요약·이미지는 받지 않는다.
  38 North 주석 영상은 변경·무단 재게시 금지(38north.org/media), CSIS 재게시는 개별 허가(csis.org reprint-permissions).
- 이전 파일과 링크 기준으로 합쳐 최근 KEEP 건만 둔다. 한 소스가 실패하면 그 소스의 이전 항목을 그대로 둔다(rc 3).
- 하루 1회(weekly.yml calendar 잡). Beyond Parallel robots Crawl-delay 10 — 소스당 요청 1회라 해당 없음.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "imagery_reports.json"
FEEDS = {
    "38north": ("38 North", "https://www.38north.org/tag/satellite-imagery/feed/"),
    "beyondparallel": ("CSIS Beyond Parallel", "https://beyondparallel.csis.org/tag/satellite-imagery/feed/"),
}
KEEP = 60
UA = "korea-security-watch/1.0 (personal OSINT dashboard; titles and links only)"


def parse(xml_text: str, source: str) -> list[dict]:
    items = []
    for it in ET.fromstring(xml_text).iter("item"):
        title, link, pub = ((it.findtext(k) or "").strip() for k in ("title", "link", "pubDate"))
        if not (title and link.startswith("https://")):
            continue
        try:
            date = parsedate_to_datetime(pub).astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            date = None
        items.append({"source": source, "title": title, "link": link, "date": date})
    return items


def fetch(url: str) -> str:
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except OSError:
            if attempt == 2:
                raise
            time.sleep(10)
    raise RuntimeError("unreachable")


def merge(old: list[dict], new: list[dict], keep: int = KEEP) -> list[dict]:
    by = {x["link"]: x for x in old}
    by.update({x["link"]: x for x in new})
    return sorted(by.values(), key=lambda x: x.get("date") or "", reverse=True)[:keep]


def main(argv: list[str]) -> int:
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else OUT
    prev = json.loads(out.read_text()) if out.exists() else {}
    items, status, rc = list(prev.get("items", [])), {}, 0
    for sid, (name, url) in FEEDS.items():
        try:
            got = parse(fetch(url), sid)
        except Exception as e:  # noqa: BLE001 — 실패 소스는 이전 항목 유지
            print(f"{sid} 실패: {e}")
            status[sid] = {"ok": False, "error": str(e)[:200]}
            rc = 3
            continue
        items = merge(items, got)
        status[sid] = {"ok": True, "count": len(got)}
    doc = {"fetched_at": datetime.now(timezone.utc).isoformat(),
           "note": "제목·링크·날짜만. 본문·이미지 전재 금지(각 사이트 이용 조건)",
           "sources": {sid: {"name": n, "feed": u, **status.get(sid, {})} for sid, (n, u) in FEEDS.items()},
           "items": items}
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({k: v.get("count") for k, v in status.items()}))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))

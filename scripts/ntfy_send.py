"""지인 알림(ntfy). outbox.json 에 friends 블록이 있을 때만 보낸다.

  python3 scripts/ntfy_send.py state/outbox.json
env: NTFY_TOPIC (추측 불가한 무작위 문자열, 공개 사이트에 노출 금지), (선택) NTFY_SERVER
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request


def main(argv: list[str]) -> int:
    box = json.load(open(argv[1]))
    f = box.get("friends")
    if not f:
        print("no friends message")
        return 0
    topic = os.environ["NTFY_TOPIC"]
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    payload = {"topic": topic, "title": f["title"], "message": f["message"], "priority": f["priority"],
               "click": box.get("link"), "tags": ["rotating_light" if f["priority"] >= 4 else "information_source"]}
    req = urllib.request.Request(server, data=json.dumps(payload, ensure_ascii=False).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        if r.status != 200:
            raise SystemExit(f"ntfy HTTP {r.status}")
    print("ntfy sent")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

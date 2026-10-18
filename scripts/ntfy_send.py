"""ntfy 발송. 본인 주제(NTFY_TOPIC_ME)와 지인 주제(NTFY_TOPIC)로 나눠 보낸다.

  python3 scripts/ntfy_send.py outbox state/outbox.json      # me → 본인, friends → 지인
  python3 scripts/ntfy_send.py me "<제목>" "<본문>" [우선순위]  # 본인 주제로 직접(테스트·감시 정지)

주제 이름은 추측 불가한 무작위 문자열이고 시크릿에만 둔다. ntfy.sh 공개 주제는 이름만 알면 누구나
발행할 수 있어서, 공개 사이트·레포에 적으면 가짜 알림을 막을 수 없다.
env: NTFY_TOPIC_ME, NTFY_TOPIC, (선택) NTFY_SERVER
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

DASHBOARD_URL = "https://haydenryu.github.io/korea-security-watch/"


def send(topic_env: str, m: dict, link: str | None) -> None:
    topic = os.environ.get(topic_env)
    if not topic:
        raise SystemExit(f"{topic_env} 없음")  # 조용히 건너뛰지 않는다
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    payload = {"topic": topic, "title": m["title"], "message": m["message"], "priority": int(m["priority"]),
               "click": link or DASHBOARD_URL,
               "tags": ["rotating_light" if int(m["priority"]) >= 4 else "information_source"]}
    req = urllib.request.Request(server, data=json.dumps(payload, ensure_ascii=False).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        if r.status != 200:
            raise SystemExit(f"ntfy HTTP {r.status}")
    print(f"sent → {topic_env}")


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[1] == "outbox":
        box = json.load(open(argv[2]))
        failed = []
        for key, env in (("me", "NTFY_TOPIC_ME"), ("friends", "NTFY_TOPIC")):
            if box.get(key):
                try:
                    send(env, box[key], box.get("link"))
                except (SystemExit, OSError) as e:  # 한쪽 실패가 다른 쪽 발송을 막지 않게
                    print(f"{env} 실패: {e}")
                    failed.append(env)
        return 1 if failed else 0
    if len(argv) >= 4 and argv[1] == "me":
        send("NTFY_TOPIC_ME", {"title": argv[2], "message": argv[3],
                               "priority": argv[4] if len(argv) > 4 else 3}, None)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

"""상황판 방문자 웹 푸시 발송 — outbox 의 방문자(public) 메시지를 Worker /push/send 로 넘긴다.

  python3 scripts/push_send.py outbox state/outbox.json            # public 이 있으면 모든 구독자에게
  python3 scripts/push_send.py outbox state/outbox.json --dry-run  # 보낼 본문만 출력(토큰·네트워크 불필요)
  python3 scripts/push_send.py test <구독 id>                       # 구독 하나에만 시험 발송

방문자에게 무엇을 보낼지는 diff_state.py 가 정한다(주의 이상 진입·격상·새 징후 · 평시 복귀).
여기서 정책을 다시 만들지 않는다 — outbox 에 public 이 없으면 보내지 않는다.
Worker 는 한 번에 일부 구독만 처리하고 cursor 를 돌려준다(무료 플랜 하위요청 한도) → done 까지 반복.
env: PUSH_TOKEN, (선택) PUSH_URL
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

PUSH_URL = "https://jis-cctv.jis-probe.workers.dev/push/send"
MAX_PAGES = 200  # 20건 × 200 = 4,000 구독 — 넘으면 멈추고 알린다(무한 반복 방지)


def payload(box: dict) -> dict | None:
    fr = box.get("public")
    if not fr:
        return None
    return {"title": fr["title"], "message": fr["message"], "priority": int(fr.get("priority", 3)),
            "link": box.get("link")}


def post(body: dict, token: str) -> dict:
    url = os.environ.get("PUSH_URL", PUSH_URL)
    req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}",
                                          "User-Agent": "ksw-notify"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def send_all(body: dict, token: str, post_fn=post) -> dict:
    tot = {"sent": 0, "removed": 0, "failed": 0, "pages": 0, "errors": []}
    cursor = None
    while True:
        r = post_fn({**body, **({"cursor": cursor} if cursor else {})}, token)
        for k in ("sent", "removed", "failed"):
            tot[k] += int(r.get(k, 0))
        tot["errors"] += r.get("errors", [])
        tot["pages"] += 1
        if r.get("done", True) or not r.get("cursor"):
            return tot
        if tot["pages"] >= MAX_PAGES:
            raise SystemExit(f"페이지 {MAX_PAGES} 초과 — 구독 수 확인 필요")
        cursor = r["cursor"]


def verdict(tot: dict) -> int:
    # 일부 실패는 일시적일 수 있다(429·5xx). 전부 실패면 VAPID·토큰 같은 우리 쪽 고장이라 실패로 올린다
    return 1 if tot["failed"] and not tot["sent"] else 0


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[1] == "outbox":
        body = payload(json.load(open(argv[2])))
        if body is None:
            print("public 없음 — 방문자 푸시 안 함")
            return 0
        if "--dry-run" in argv:
            print(json.dumps(body, ensure_ascii=False, indent=2))
            return 0
        token = os.environ.get("PUSH_TOKEN")
        if not token:
            raise SystemExit("PUSH_TOKEN 없음")
        try:
            tot = send_all(body, token)
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"push 실패: {e}")
            return 1
        print(f"push sent={tot['sent']} removed={tot['removed']} failed={tot['failed']} pages={tot['pages']} "
              f"errors={tot['errors'][:5]}")
        return verdict(tot)
    if len(argv) >= 3 and argv[1] == "test":
        token = os.environ.get("PUSH_TOKEN")
        if not token:
            raise SystemExit("PUSH_TOKEN 없음")
        r = post({"title": "JIS 알림 시험", "message": "웹 푸시 수신 확인용 시험 알림입니다.", "priority": 3,
                  "only": argv[2]}, token)
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r.get("sent") else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

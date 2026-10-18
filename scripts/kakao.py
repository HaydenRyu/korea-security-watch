"""카카오톡 「나에게 보내기」.

  python3 scripts/kakao.py auth                 # 최초 1회(로컬): 브라우저 인가 → refresh_token 출력
  python3 scripts/kakao.py send <outbox.json>   # Actions: 토큰 갱신 + 발송
  python3 scripts/kakao.py send --text "..."    # 수동 테스트
  python3 scripts/kakao.py refresh              # Actions watchdog: 토큰 유지·회전만

env: KAKAO_REST_KEY, KAKAO_REFRESH_TOKEN, (선택) KAKAO_CLIENT_SECRET
회전된 refresh_token 은 ROTATED_TOKEN_FILE 경로에 쓴다(워크플로가 레포 시크릿으로 되돌려 넣는다).
"""
from __future__ import annotations

import http.server
import json
import os
import sys
import urllib.parse
import urllib.request
import webbrowser

AUTH = "https://kauth.kakao.com"
API = "https://kapi.kakao.com"
REDIRECT = "http://localhost:8766/callback"
DASHBOARD_URL = "https://haydenryu.github.io/korea-security-watch/"


def _post(url: str, data: dict, token: str | None = None) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded;charset=utf-8")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"kakao {url} HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")


def _client() -> dict:
    d = {"client_id": os.environ["KAKAO_REST_KEY"]}
    if os.environ.get("KAKAO_CLIENT_SECRET"):
        d["client_secret"] = os.environ["KAKAO_CLIENT_SECRET"]
    return d


def _mask(v: str) -> None:
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::add-mask::{v}")


def refresh() -> str:
    r = _post(f"{AUTH}/oauth/token", {**_client(), "grant_type": "refresh_token",
                                      "refresh_token": os.environ["KAKAO_REFRESH_TOKEN"]})
    _mask(r["access_token"])
    # 만료 1개월 전부터 새 refresh_token 이 함께 온다
    if r.get("refresh_token"):
        _mask(r["refresh_token"])
        out = os.environ.get("ROTATED_TOKEN_FILE")
        if out:
            with open(out, "w") as f:
                f.write(r["refresh_token"])
        print("refresh_token rotated")
    return r["access_token"]


def send(text: str, link: str = DASHBOARD_URL) -> None:
    token = refresh()
    tpl = {"object_type": "text", "text": text[:200],
           "link": {"web_url": link, "mobile_web_url": link}, "button_title": "상황판 보기"}
    r = _post(f"{API}/v2/api/talk/memo/default/send", {"template_object": json.dumps(tpl, ensure_ascii=False)}, token)
    if r.get("result_code") != 0:
        raise SystemExit(f"kakao send failed: {r}")
    print("sent")


def auth() -> None:
    q = urllib.parse.urlencode({"client_id": os.environ["KAKAO_REST_KEY"], "redirect_uri": REDIRECT,
                                "response_type": "code", "scope": "talk_message"})
    holder: dict = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            holder.update(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query))
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("인가 완료. 터미널로 돌아가세요.".encode())

        def log_message(self, *a):
            pass

    webbrowser.open(f"{AUTH}/oauth/authorize?{q}")
    http.server.HTTPServer(("localhost", 8766), H).handle_request()
    if "code" not in holder:
        raise SystemExit(f"인가 실패: {holder}")
    r = _post(f"{AUTH}/oauth/token", {**_client(), "grant_type": "authorization_code",
                                      "redirect_uri": REDIRECT, "code": holder["code"][0]})
    print("refresh_token 발급 완료. 아래 명령으로 레포 시크릿에 넣으세요(값은 화면에 남기지 마세요):")
    print("  gh secret set KAKAO_REFRESH_TOKEN -R HaydenRyu/korea-security-watch")
    print(r["refresh_token"])


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "auth":
        auth()
    elif cmd == "refresh":
        refresh()
        print("refresh ok")
    elif cmd == "send" and len(argv) >= 3:
        if argv[2] == "--text":
            send(" ".join(argv[3:]))
        else:
            box = json.load(open(argv[2]))
            send(box["text"], box.get("link") or DASHBOARD_URL)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

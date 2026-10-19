import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import diff_state as d  # noqa: E402
import push_send as p  # noqa: E402

SRC2 = [{"name": "합참", "url": "https://a.example"}, {"name": "Reuters", "url": "https://b.example"}]


def run(**over):
    items = {k: {"status": "none"} for k in d.ITEMS}
    items.update(over)
    return {"checked_at": "2026-10-18T05:00:00+00:00", "items": items, "overall_summary": "s",
            "coverage": {k: ["q"] for k in d.ITEMS},
            "opened": ["https://a.example", "https://b.example", "https://c.example"]}


def test_caution_pushes_visitors_not_friends():
    # 주의(1) 진입: 지인(ntfy)은 없지만 방문자 푸시는 간다 — 「평시 아닐 때」
    _, _, out = d.step(run(border_clash={"status": "single", "sources": SRC2[:1]}), None, [])
    assert out and out["grade"] == 1 and "friends" not in out
    body = p.payload(out)
    assert body and "지인 주제" not in body["message"] and body["link"] == d.DASHBOARD_URL


def test_return_to_calm_pushes_visitors():
    # 내림은 히스테리시스로 2회차 연속 평시일 때 확정된다
    l0, ev0, _ = d.step(run(border_clash={"status": "single", "sources": SRC2[:1]}), None, [])
    l1, ev1, held = d.step(run(), l0, [ev0])
    assert held is None or p.payload(held) is None
    _, _, out = d.step(run(), l1, [ev0, ev1])
    assert out and out["grade"] == 0 and "friends" not in out and out["me"]["priority"] == 2
    assert "해제" in p.payload(out)["title"]


def test_calm_no_push():
    _, _, out = d.step(run(), None, [])
    assert out is None or p.payload(out) is None


def test_alert_level_pushes_friends_message():
    _, _, out = d.step(run(troop_movement={"status": "corroborated", "sources": SRC2}), None, [])
    body = p.payload(out)
    assert out["grade"] >= 2 and body["title"] == out["friends"]["title"]
    assert body["message"] == out["friends"]["message"] and "지인 주제" not in body["message"]
    assert body["link"] == d.DASHBOARD_URL and body["priority"] >= 4


def test_send_all_follows_cursor():
    pages = [{"sent": 20, "removed": 1, "failed": 0, "cursor": "c1", "done": False},
             {"sent": 3, "removed": 0, "failed": 1, "cursor": None, "done": True, "errors": [500]}]
    seen = []

    def fake(body, token):
        seen.append(body.get("cursor"))
        return pages[len(seen) - 1]
    tot = p.send_all({"title": "t", "message": "m"}, "tok", fake)
    assert seen == [None, "c1"] and tot["sent"] == 23 and tot["removed"] == 1 and tot["failed"] == 1
    assert p.verdict(tot) == 0 and p.verdict({"sent": 0, "failed": 2}) == 1


def test_dry_run_cli():
    _, _, out = d.step(run(troop_movement={"status": "corroborated", "sources": SRC2}), None, [])
    f = ROOT / "tests" / "_outbox_dry.json"
    f.write_text(json.dumps(out, ensure_ascii=False))
    try:
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "push_send.py"), "outbox", str(f), "--dry-run"],
                           capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
        assert r.returncode == 0 and json.loads(r.stdout)["title"] == out["friends"]["title"]
        f.write_text(json.dumps({**out, "public": None}, ensure_ascii=False))
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "push_send.py"), "outbox", str(f)],
                           capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
        assert r.returncode == 0 and "public 없음" in r.stdout
    finally:
        f.unlink(missing_ok=True)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)

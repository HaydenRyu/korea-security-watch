"""지난 7일 이력 → 주간 요약 알림 한 통(JSON: title·message). LLM 없이 events.jsonl 만으로 만든다.

  python3 scripts/weekly_summary.py > /tmp/w.json
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from diff_state import GRADE_LABEL, ITEMS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TOP = 3


def summarize(events: list[dict], now: datetime) -> dict:
    week = [e for e in events if datetime.fromisoformat(e["checked_at"]) >= now - timedelta(days=7)]
    ok = [e for e in week if e.get("status") == "ok"]
    unv = sum(1 for e in week if e.get("status") != "ok" and not e.get("superseded"))  # 같은 회차 재시도는 세지 않는다
    if not ok:
        return {"title": "JIS 주간 요약", "message": f"지난 7일 판정 0회(판정 불가 {unv}회). 루틴 상태를 확인하세요."}
    grades = [e["grade"] for e in ok]
    cur, peak = grades[-1], max(grades)
    alerts = [e for e in ok if e.get("notified")]
    lines = [f"현재 {GRADE_LABEL[cur]}({cur}) · 7일 최고 {GRADE_LABEL[peak]}({peak})",
             f"판정 {len(ok)}회 · 판정 불가 {unv}회 · 알림 {len(alerts)}회"]
    for e in alerts[-TOP:][::-1]:
        t = datetime.fromisoformat(e["checked_at"]).astimezone(timezone(timedelta(hours=9))).strftime("%m.%d %H:%M")
        what = ", ".join(ITEMS.get(k, k) for k in e.get("new") or []) or (e.get("summary") or "")[:80]
        lines.append(f"· {t} {GRADE_LABEL[e['grade']]} — {what}")
    if not alerts:
        lines.append("· 이번 주 알림 없음")
    return {"title": f"JIS 주간 요약 · {GRADE_LABEL[cur]}", "message": "\n".join(lines)}


def main() -> int:
    p = ROOT / "state" / "events.jsonl"
    events = [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []
    print(json.dumps(summarize(events, datetime.now(timezone.utc)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

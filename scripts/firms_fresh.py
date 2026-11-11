"""state/firms.json 신선도 판정 — firms 잡 마지막 스텝.

  python3 scripts/firms_fresh.py [--file state/firms.json]

fetch 스텝이 continue-on-error 라 FIRMS 단발 통신 실패는 잡을 실패시키지 않는다(PR #7).
그 대신 장기 단절은 이 스텝만 잡는다 — 판정은 UTC 기준:
- now - fetched_at > MAX_AGE(3h) → rc 1 (정확히 3h 는 통과)
- fetched_at 없음·파일 없음·파싱 실패 → rc 1
- fetched_at 이 now 보다 FUTURE_TOL(10분) 넘게 미래 → rc 1
- 그 밖 rc 0
3h 는 실측 schedule 회차 간격(1~2시간) 기준이다(티켓 4057e099). cron 지연만으로 넘을 수 있다.
현재 시각은 환경변수 FIRMS_FRESH_NOW(ISO 8601)로 주입할 수 있다(픽스처용). 시간대 없는 값은 UTC 로 읽는다.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_AGE = timedelta(hours=3)
FUTURE_TOL = timedelta(minutes=10)


def _utc(s: str) -> datetime:
    t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def judge(doc: dict | None, now: datetime) -> tuple[int, str]:
    raw = (doc or {}).get("fetched_at")
    if not isinstance(raw, str) or not raw:
        return 1, "fetched_at 없음"
    try:
        t = _utc(raw)
    except ValueError:
        return 1, f"fetched_at 파싱 실패: {raw!r}"
    age = now - t
    if -age > FUTURE_TOL:
        return 1, f"fetched_at {raw} 이 현재보다 {-age} 미래(허용 {FUTURE_TOL})"
    if age > MAX_AGE:
        return 1, f"fetched_at {raw} · 경과 {age} > {MAX_AGE}"
    return 0, f"fetched_at {raw} · 경과 {age}"


def main(argv: list[str]) -> int:
    path = Path(argv[argv.index("--file") + 1]) if "--file" in argv else ROOT / "state" / "firms.json"
    env_now = os.environ.get("FIRMS_FRESH_NOW")
    now = _utc(env_now) if env_now else datetime.now(timezone.utc)
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        doc = None
        print(f"{path} 읽기 실패: {e}")
    rc, msg = judge(doc if isinstance(doc, dict) else None, now)
    print(("firms 신선: " if rc == 0 else "firms 오래됨: ") + msg)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))

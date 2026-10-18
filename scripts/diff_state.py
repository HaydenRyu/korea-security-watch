"""회차 판정 → 등급 산정 → 직전 상태와 비교 → 발송 여부 결정.

LLM 은 항목별 관측만 낸다(run.json). 등급·발송 여부는 이 파일의 코드가 정한다.

사용: python3 scripts/diff_state.py <run.json>
  - state/latest.json   직전 *검증된* 판정 (unverified 회차는 덮지 않는다)
  - state/events.jsonl  회차 이력 1줄씩 추가
  - state/outbox.json   이번 회차에 보낼 메시지(없으면 null) — Actions 가 이 파일 변경을 보고 발송
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state"

ITEMS = {
    "troop_movement": "병력 이동·동원",
    "nuclear": "핵·전략무기",
    "logistics": "군수 비축·수송",
    "border_clash": "DMZ·NLL 충돌",
    "foreign_forces": "미·중·러 이상 동향",
    "parade_massing": "기념일 전후 집결",
    "gov_warning": "정부 긴급 경고",
}
# 교차확인되면 단독으로 등급 3 을 만드는 항목
CRITICAL = {"nuclear", "troop_movement", "gov_warning"}
STATUSES = {"none", "single", "corroborated"}
GRADE_LABEL = {0: "평시", 1: "주의", 2: "경계", 3: "위험"}
FORWARD_MIN_GRADE = 2  # 이 등급 이상이면 「지인 전달 권고」
UNVERIFIED_ALERT_AT = 3  # 연속 판정 불가 N회째에 1회 알림
DASHBOARD_URL = "https://haydenryu.github.io/korea-security-watch/"


class RunError(ValueError):
    pass


def validate(run: dict) -> None:
    if run.get("status") == "unverified":
        return
    items = run.get("items")
    if not isinstance(items, dict) or set(items) != set(ITEMS):
        raise RunError(f"items 키는 정확히 {sorted(ITEMS)} 여야 한다")
    for key, it in items.items():
        if it.get("status") not in STATUSES:
            raise RunError(f"{key}.status 값 오류: {it.get('status')!r}")
        if it["status"] != "none" and not it.get("sources"):
            raise RunError(f"{key}: 징후가 있는데 출처가 없다")
        if it["status"] == "corroborated" and len({s.get("url") for s in it["sources"]}) < 2:
            raise RunError(f"{key}: corroborated 인데 출처가 2곳 미만이다")


def active_keys(items: dict) -> set[str]:
    """일상 활동(routine)이 아닌 징후 항목."""
    return {k for k, it in items.items() if it["status"] != "none" and not it.get("routine")}


def compute_grade(items: dict) -> int:
    active = active_keys(items)
    any_signal = any(it["status"] != "none" for it in items.values())
    if not active:
        # 일상 훈련·미사일 시험·위협 발언만 있으면 최대 1
        return 1 if any_signal else 0
    corroborated = {k for k in active if items[k]["status"] == "corroborated"}
    if corroborated & CRITICAL:
        return 3
    if len(active) >= 2 or corroborated:
        return 2
    return 1


def _load(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def _headline(items: dict, keys: set[str]) -> str:
    # 메시지에 실을 대표 항목: 교차확인 > 단일, ITEMS 순서
    order = sorted(keys, key=lambda k: (items[k]["status"] != "corroborated", list(ITEMS).index(k)))
    return order[0] if order else ""


def compose(run: dict, grade: int, new_keys: set[str]) -> str:
    """카카오 텍스트 템플릿 200자 제한 안에 맞춘다. 상세는 대시보드."""
    items = run["items"]
    key = _headline(items, new_keys or active_keys(items))
    it = items.get(key, {})
    t = datetime.fromisoformat(run["checked_at"]).astimezone().strftime("%m/%d %H:%M")
    lines = []
    if grade >= FORWARD_MIN_GRADE:
        lines.append("[지인 전달 권고]")
    lines.append(f"안보 감시 {GRADE_LABEL[grade]}({grade}) · {t}")
    if it:
        lines.append(f"{ITEMS[key]} · {it.get('region', '')}".rstrip(" ·"))
        lines.append(it.get("summary", ""))
        src = ", ".join(s.get("name", "") for s in it.get("sources", [])[:2] if s.get("name"))
        lines.append(f"신뢰도 {it.get('confidence', '-')} · {src}".rstrip(" ·"))
    text = "\n".join(l for l in lines if l)
    return text if len(text) <= 200 else text[:199] + "…"


def step(run: dict, latest: dict | None, events: list[dict]) -> tuple[dict | None, dict, dict | None]:
    """(새 latest 또는 None=유지, 이벤트, outbox 또는 None)"""
    validate(run)
    now = run.get("checked_at") or datetime.now(timezone.utc).isoformat()
    run["checked_at"] = now

    if run.get("status") == "unverified":
        streak = 1
        for e in reversed(events):
            if e.get("status") != "unverified":
                break
            streak += 1
        event = {"checked_at": now, "status": "unverified", "reason": run.get("reason", ""), "streak": streak}
        outbox = None
        if streak == UNVERIFIED_ALERT_AT:
            outbox = {"id": now, "text": f"안보 감시 중단: {streak}회 연속 판정 불가\n사유: {run.get('reason', '미상')}"[:200],
                      "link": DASHBOARD_URL}
        return None, event, outbox

    items = run["items"]
    grade = compute_grade(items)
    prev_grade = (latest or {}).get("grade", 0)
    prev_active = set((latest or {}).get("active", []))
    active = active_keys(items)
    new_keys = active - prev_active

    if latest is None:
        notify = grade >= 1  # 최초 분석: 징후가 있으면 알린다
    else:
        notify = grade > prev_grade or (grade >= 1 and grade == prev_grade and bool(new_keys))

    new_latest = {**run, "grade": grade, "grade_label": GRADE_LABEL[grade], "active": sorted(active)}
    event = {"checked_at": now, "status": "ok", "grade": grade, "prev_grade": prev_grade,
             "active": sorted(active), "new": sorted(new_keys), "notified": notify,
             "summary": run.get("overall_summary", "")}
    outbox = {"id": now, "text": compose(run, grade, new_keys), "link": DASHBOARD_URL} if notify else None
    return new_latest, event, outbox


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    run = json.loads(Path(argv[1]).read_text())
    latest = _load(STATE / "latest.json", None)
    events_path = STATE / "events.jsonl"
    events = [json.loads(l) for l in events_path.read_text().splitlines() if l.strip()] if events_path.exists() else []
    try:
        new_latest, event, outbox = step(run, latest, events)
    except RunError as e:
        # 스키마 위반도 판정 불가로 기록한다(평시로 두지 않는다)
        new_latest, event, outbox = step({"status": "unverified", "reason": f"schema: {e}",
                                          "checked_at": run.get("checked_at")}, latest, events)
    STATE.mkdir(exist_ok=True)
    if new_latest is not None:
        (STATE / "latest.json").write_text(json.dumps(new_latest, ensure_ascii=False, indent=2) + "\n")
    with events_path.open("a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    if outbox is not None:
        (STATE / "outbox.json").write_text(json.dumps(outbox, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"event": event, "notify": outbox is not None}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

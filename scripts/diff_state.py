"""회차 판정 → 등급 산정 → 직전 상태와 비교 → 발송 여부 결정.

LLM 은 항목별 관측만 낸다(run.json). 등급·발송 여부는 이 파일의 코드가 정한다.

사용: python3 scripts/diff_state.py <run.json>
  - state/latest.json   직전 *검증된* 판정 (unverified 회차는 덮지 않는다)
  - state/events.jsonl  회차 이력 1줄씩 추가
  - state/outbox.json   이번 회차에 보낼 메시지 — Actions 가 이 파일 변경을 보고 ntfy 로 발송
                        me = 본인 주제(주의 이상·해제·감시 중단), friends = 지인 주제(경계 이상·해제)
"""
from __future__ import annotations

import json
from urllib.parse import urlparse
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
# 이 중 하나가 교차확인되고 다른 항목 징후가 동시에 있으면 등급 3
CRITICAL = {"nuclear", "troop_movement", "gov_warning"}
STATUSES = {"none", "single", "corroborated"}
GRADE_LABEL = {0: "평시", 1: "주의", 2: "경계", 3: "위험"}
FORWARD_MIN_GRADE = 2  # 이 등급 이상이면 지인 주제에도 보낸다
MIN_OPENED = 3  # 회차마다 원문을 최소 3건 열어야 판정으로 인정
MIN_DOMAINS = 2  # 연 원문이 한 매체에 몰리면 교차확인이 아니다 — 서로 다른 사이트 2곳 이상
UNVERIFIED_ALERT_AT = 3  # 연속 판정 불가 N회째에 1회 알림
DASHBOARD_URL = "https://haydenryu.github.io/korea-security-watch/"


class RunError(ValueError):
    pass


_SLD = {"co", "go", "or", "ac", "ne", "re", "com", "gov", "org", "net"}


def site(url: str) -> str:
    """URL → 사이트 단위(yna.co.kr · nknews.org). 같은 매체의 하위 경로·서브도메인은 한 곳으로 센다."""
    host = urlparse(url).hostname or ""
    parts = host.removeprefix("www.").split(".")
    n = 3 if len(parts) >= 3 and parts[-2] in _SLD and len(parts[-1]) == 2 else 2
    return ".".join(parts[-n:])


def validate(run: dict) -> None:
    if run.get("status") == "unverified":
        return
    items = run.get("items")
    if not isinstance(items, dict) or set(items) != set(ITEMS):
        raise RunError(f"items 키는 정확히 {sorted(ITEMS)} 여야 한다")
    cov = run.get("coverage")
    if not isinstance(cov, dict) or any(not cov.get(k) for k in ITEMS):
        raise RunError("coverage 에 7개 항목마다 검색어가 1개 이상 있어야 한다")
    opened = {u for u in run.get("opened") or [] if isinstance(u, str) and u.startswith("http")}
    if len(opened) < MIN_OPENED:
        raise RunError(f"원문을 {MIN_OPENED}건 이상 열어야 한다(opened {len(opened)}건)")
    doms = {site(u) for u in opened}
    if len(doms) < MIN_DOMAINS:
        raise RunError(f"원문을 서로 다른 사이트 {MIN_DOMAINS}곳 이상에서 열어야 한다({', '.join(sorted(doms))})")
    for key, it in items.items():
        for s in it.get("sources") or []:
            if s.get("url") not in opened:
                raise RunError(f"{key}: 열어 보지 않은 출처 {s.get('url')!r}")
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
    if corroborated & CRITICAL and len(active) >= 2:
        return 3  # 핵실험 단독 등 한 항목만으로는 위험으로 올리지 않는다
    if len(active) >= 2 or corroborated:
        return 2
    return 1


def _load(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def _headline(items: dict, keys: set[str]) -> str:
    # 메시지에 실을 대표 항목: 교차확인 > 단일, ITEMS 순서
    order = sorted(keys, key=lambda k: (items[k]["status"] != "corroborated", list(ITEMS).index(k)))
    return order[0] if order else ""


MSG_MAX = 600  # 상세는 상황판. 알림은 한눈에 읽히는 길이로


def _t(run: dict) -> str:
    return datetime.fromisoformat(run["checked_at"]).astimezone().strftime("%m/%d %H:%M")


def compose(run: dict, new_keys: set[str]) -> str:
    """대표 항목 1개의 지역·요약·신뢰도·출처. 등급은 제목에 싣는다."""
    items = run["items"]
    key = _headline(items, new_keys or active_keys(items))
    it = items.get(key, {})
    lines = []
    if it:
        lines.append(f"{ITEMS[key]} · {it.get('region', '')}".rstrip(" ·"))
        lines.append(it.get("summary", ""))
        src = ", ".join(s.get("name", "") for s in it.get("sources", [])[:2] if s.get("name"))
        lines.append(f"신뢰도 {it.get('confidence', '-')} · {src}".rstrip(" ·"))
    return "\n".join(l for l in lines if l)


def _msg(title: str, body: str, priority: int) -> dict:
    body = (body.strip() + "\n상황판에서 출처를 확인하세요.").strip()
    return {"title": title, "message": body if len(body) <= MSG_MAX else body[:MSG_MAX - 1] + "…", "priority": priority}


def alert_messages(run: dict, grade: int, new_keys: set[str], released: bool, prev_grade: int,
                   friends: bool) -> tuple[dict, dict | None]:
    """(본인, 지인 또는 None). 우선순위: 위험 5 · 경계 4 · 주의·해제 3"""
    if released:
        m = _msg(f"한반도 징후 {GRADE_LABEL[prev_grade]} 해제 → {GRADE_LABEL[grade]}({grade})",
                 f"{_t(run)}\n{run.get('overall_summary', '')}", 3)
        return m, m
    m = _msg(f"한반도 징후 {GRADE_LABEL[grade]}({grade})", f"{_t(run)}\n{compose(run, new_keys)}",
             {3: 5, 2: 4}.get(grade, 3))
    if not friends:
        return m, None
    return {**m, "message": m["message"] + "\n(지인 주제에도 발송됨)"}, m


EVENT_SOURCES_MAX = 6


def event_sources(items: dict) -> list[dict]:
    """이력 한 줄에 남길 근거 — 신호가 있는 항목(정례 포함)의 출처, URL 중복 제거.
    latest.json 은 매 회차 덮이므로, 지난 알림의 기사 경로는 여기에만 남는다."""
    out, seen = [], set()
    for k, it in items.items():
        if it.get("status", "none") == "none":
            continue
        for src in it.get("sources") or []:
            u = src.get("url")
            if u and u not in seen:
                seen.add(u)
                out.append({"item": k, "name": src.get("name", ""), "url": u})
    return out[:EVENT_SOURCES_MAX]


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
            outbox = {"id": now, "link": DASHBOARD_URL,
                      "me": _msg("안보 감시 중단", f"{streak}회 연속 판정 불가\n사유: {run.get('reason', '미상')}", 4)}
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
    # 지인(ntfy): 경계 이상에서 알림 조건을 만족할 때 + 경계 이상에서 내려올 때(해제)
    friends = notify and grade >= FORWARD_MIN_GRADE
    released = latest is not None and prev_grade >= FORWARD_MIN_GRADE > grade
    notify = notify or released

    new_latest = {**run, "grade": grade, "grade_label": GRADE_LABEL[grade], "active": sorted(active)}
    event = {"checked_at": now, "status": "ok", "grade": grade, "prev_grade": prev_grade,
             "active": sorted(active), "new": sorted(new_keys), "notified": notify,
             "summary": run.get("overall_summary", ""), "sources": event_sources(items)}
    event["friends"] = friends or released
    outbox = None
    if notify:
        me, fr = alert_messages(run, grade, new_keys, released, prev_grade, friends)
        outbox = {"id": now, "grade": grade, "link": DASHBOARD_URL, "me": me}
        if fr:
            outbox["friends"] = fr
    return new_latest, event, outbox


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    run = json.loads(Path(argv[1]).read_text())
    run["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")  # 시각은 모델이 아니라 시계가 정한다
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

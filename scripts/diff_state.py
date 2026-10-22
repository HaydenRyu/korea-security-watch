"""회차 판정 → 등급 산정 → 직전 상태와 비교 → 발송 여부 결정.

LLM 은 항목별 관측만 낸다(run.json). 등급·발송 여부는 이 파일의 코드가 정한다.

사용: python3 scripts/diff_state.py <run.json>
      python3 scripts/diff_state.py --check <run.json>   형식 검사만(state/ 에 아무것도 쓰지 않는다). rc 0 통과 · 1 위반
  - state/latest.json   직전 *검증된* 판정 (unverified 회차는 덮지 않는다)
  - state/events.jsonl  회차 이력 1줄씩 추가. 같은 회차 안에서 판정 불가 뒤 RETRY_WINDOW_MIN 안에 판정이 성공하면
                        앞의 판정 불가 줄에 superseded(성공 회차 시각)를 적는다 — 기록은 남기되 화면·주간 요약은 세지 않는다
  - .commit_msg         이번 회차의 커밋 메시지(루틴이 git commit -F 로 그대로 쓴다 — 등급 문구를 모델이 짓지 않는다)
  - state/outbox.json   이번 회차에 보낼 메시지 — Actions 가 이 파일 변경을 보고 ntfy 로 발송
                        me = 본인 주제(주의 이상·해제·감시 중단), friends = 지인 주제(경계 이상·해제)
"""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state"

ITEMS = {
    "troop_movement": "병력 이동·동원",
    "nuclear": "핵·전략무기",
    "logistics": "군수 비축·수송",
    "border_clash": "DMZ·NLL 충돌",
    "electronic_cyber": "전자전·사이버",
    "foreign_forces": "외국군·북러·북중 협력",
    "parade_massing": "기념일 전후 집결",
    "nk_internal": "북 내부 전시태세",
    "gov_warning": "정부 긴급 경고",
    "hybrid_ops": "혼합위협 · 귀속 확정",
}
# 이 중 하나가 교차확인되고 다른 항목 징후가 동시에 있으면 등급 3.
# 전자전·사이버, 북 내부 전시태세는 일반 징후다 — 다른 항목과 겹치면 등급을 올리지만 단독으로 위험을 만들지 않는다.
CRITICAL = {"nuclear", "troop_movement", "gov_warning"}
STATUSES = {"none", "single", "corroborated"}
GRADE_LABEL = {0: "평시", 1: "주의", 2: "경계", 3: "위험"}
FORWARD_MIN_GRADE = 2  # 이 등급 이상이면 지인 주제에도 보낸다
MIN_OPENED = 3  # 회차마다 원문을 최소 3건 열어야 판정으로 인정
MIN_DOMAINS = 2  # 연 원문이 한 매체에 몰리면 교차확인이 아니다 — 서로 다른 사이트 2곳 이상
UNVERIFIED_ALERT_AT = 3  # 연속 판정 불가 N회째에 1회 알림
DASHBOARD_URL = "https://haydenryu.github.io/korea-security-watch/"
# 위기 바닥: 최근 이 시간 안에 위험(3)이 있었으면 해제 근거 없이 경계(2) 아래로 내리지 않는다.
# 근거: 2015-08 백테스트 — 24시간 만료로 최후통첩 시한 22시간 전에 「위험 해제」, 시한 당일 아침 「평시」가 나갔다.
CRISIS_FLOOR_H = 72
RETRY_WINDOW_MIN = 15  # 판정 불가 뒤 이 안에 성공한 판정은 같은 회차의 재시도 — 앞 줄을 superseded 로
CRISIS_FLOOR_GRADE = 2
DEADLINE_GRACE_H = 24  # 시한(until)이 있는 지속 상태는 시한 뒤 이 시간까지만 이어 간다(시한 + 24h)

# 항목 선택 필드 — 없으면 기존 동작이다(하위 호환). 값이 정확히 true 일 때만 켠다.
#   ongoing        지속 상태(준전시·전시상태, 대피령·진돗개·데프콘 격상, 최후통첩) — 해제 보도(ended) 또는 시한+24h 까지 유지
#   until          지속 상태의 시한(ISO 시각). 최후통첩 시한 등
#   ended          이번 회차에 해제·종료 보도가 있었다(status 는 보통 none) — 이어 가기·위기 바닥을 끊는다
#   unresolved     원인 미상 중대 사건(함정 침몰·대형 폭발, 조사 중) — 원인 발표(ended) 전까지 single 로 이어 간다
#   ultimatum      시한·표적·군사행동을 명시한 최후통첩 — routine 이 아니고 ongoing 으로 다룬다
#   fatalities / territory_hit   border_clash 에서 사망자 발생 / 남측 영토·함정 피격 — 교차확인이면 단독 위험(3)
#   overflight_alert            gov_warning 이 J-Alert 등 시험 발사 상공 통과 경보(해상 착탄) — routine
BORDER_SEVERE_FLAGS = ("fatalities", "territory_hit")


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
        raise RunError(f"coverage 에 {len(ITEMS)}개 항목마다 검색어가 1개 이상 있어야 한다")
    opened = _opened(run)
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
    normalize_conflict(items)
    normalize_attribution(items)
    normalize_attributed_by(items)


ATTRIBUTIONS = {"nk", "cn", "ru", "unknown"}

# 국내 해킹 단독 검색어(10/20): 「북한 GPS 교란 해킹」처럼 북한어와 묶으면 귀속 미확인 국내 사고(개인정보 유출 등)가
# 결과에 안 뜬다 — 10/18~20 41회차가 전부 묶어 써서 0건이었다. 회차를 막지는 않고(핵·병력 판정까지 멈춘다) 계수만 남긴다.
_HACK_RE = re.compile(r"해킹|사이버|개인정보|유출|랜섬|디도스|DDoS|hack|cyber|breach|ransomware", re.I)
_NK_RE = re.compile(r"북한|북측|North Korea|N\.? ?Korea|DPRK|Pyongyang|GPS|교란|jamming", re.I)


def cyber_domestic_queries(cov: dict | None) -> int:
    """electronic_cyber coverage 중 북한·GPS 어휘 없이 해킹·유출만 담은 검색어 수."""
    qs = (cov or {}).get("electronic_cyber") or []
    qs = qs if isinstance(qs, list) else [qs]
    return sum(1 for q in qs if isinstance(q, str) and _HACK_RE.search(q) and not _NK_RE.search(q))


def normalize_attribution(items: dict) -> dict:
    """귀속(attribution) 정규화 — 값은 ATTRIBUTIONS 만, 「unknown」이면 등급에 안 쓰도록 routine 강제(표시는 화면 칩).
    10/20 hayden: 북한·중국 소행으로 밝혀지지 않은 해킹은 항목에는 적되 등급은 올리지 않는다."""
    for it in items.values():
        a = it.get("attribution")
        if a is None:
            continue
        a = str(a).strip().lower()
        if a not in ATTRIBUTIONS:
            it.pop("attribution", None)
            continue
        it["attribution"] = a
        if a == "unknown" and it.get("status") != "none" and not it.get("routine"):
            it["routine"] = True
            it["routine_forced"] = "attribution unknown"
    return items


ATTRIBUTION_GATED = {"hybrid_ops"}  # 공식 귀속 발표가 없으면 징후가 아니라 배경이다(10/22 hayden: 사건 건수는 입력이 아니다)
HYBRID_CATEGORIES = {"간첩", "사이버", "가상자산", "마약", "무기", "방화", "테러"}
_A_SITES: set[str] | None = None


def a_tier_sites() -> set[str]:
    """data/sources.json A 등급(공식) 사이트 집합. 못 읽으면 빈 집합 — 도메인 검사만 건너뛴다(회차를 막지 않는다)."""
    global _A_SITES
    if _A_SITES is None:
        try:
            doc = json.loads((ROOT / "data" / "sources.json").read_text())
            _A_SITES = {site(i["url"]) for t in doc["tiers"] if t["id"] == "A" for g in t["groups"] for i in g["items"] if i.get("url")}
        except Exception:  # noqa: BLE001
            _A_SITES = set()
    return _A_SITES


def attributed_ok(it: dict) -> bool:
    """귀속 확정 = attributed_by{name, tier A} + A 등급 출처 1곳 이상(사이트가 sources.json A 등급에 있을 때)."""
    ab = it.get("attributed_by")
    if not isinstance(ab, dict) or str(ab.get("tier", "")).strip().upper() != "A" or not str(ab.get("name", "")).strip():
        return False
    a_sites = a_tier_sites()
    for s in it.get("sources") or []:
        if str(s.get("tier", "")).strip().upper() != "A":
            continue
        if not a_sites or site(s.get("url") or "") in a_sites:
            return True
    return False


def normalize_attributed_by(items: dict) -> dict:
    """귀속 발표 정규화(10/22). attributed_by 가 A 등급이 아니면 지운다. ATTRIBUTION_GATED 항목은 귀속 확정이 아니면
    status 를 none 으로 내리고(summary 는 판정 메모로 남는다) 사유를 attribution_dropped 에 적는다 — 화재·범죄 통계나
    귀속 없는 사건이 징후로 올라오는 길을 코드가 막는다. 프롬프트는 확률만 올린다."""
    for key, it in items.items():
        if not isinstance(it, dict):
            continue
        ok = attributed_ok(it)
        if ok:
            ab = it["attributed_by"]
            clean = {"name": str(ab["name"]).strip()[:60], "tier": "A"}
            if str(ab.get("what") or "").strip():
                clean["what"] = str(ab["what"]).strip()[:120]
            it["attributed_by"] = clean
        else:
            it.pop("attributed_by", None)
        cat = str(it.get("category") or "").strip()
        if cat and cat not in HYBRID_CATEGORIES:
            it.pop("category", None)
        if key in ATTRIBUTION_GATED and it.get("status") != "none" and not ok:
            it["status"] = "none"
            it["attribution_dropped"] = "A 등급 기관의 귀속 발표 없음"
            if it.get("summary") and not str(it["summary"]).startswith("귀속 미확정"):
                it["summary"] = "귀속 미확정 — " + str(it["summary"])
    return items


def normalize_conflict(items: dict) -> dict:
    """「상충」 표시(10/20): A~C 출처가 같은 사실을 서로 부정할 때 루틴이 conflict: true + conflict_what 을 낸다.
    등급 계산은 읽지 않는다(화면 표시만). 징후 없음·서로 다른 사이트 2곳 미만·무엇이 상충하는지 없음이면 지우고 사유를 남긴다."""
    for key, it in items.items():
        if not _flag(it, "conflict"):
            continue
        why = None
        if it.get("status") == "none":
            why = "징후 없음"
        elif len({site(s.get("url") or "") for s in it.get("sources") or []}) < 2:
            why = "서로 다른 사이트 2곳 미만"
        elif not str(it.get("conflict_what") or "").strip():
            why = "conflict_what 없음"
        if why:
            it["conflict"] = False
            it["conflict_dropped"] = why
    return items


def _flag(it: dict, key: str) -> bool:
    return isinstance(it, dict) and it.get(key) is True


def is_routine(it: dict) -> bool:
    """정례 여부. 최후통첩은 정례가 아니고, 시험 발사 상공 통과 경보(J-Alert 등)는 정례다."""
    if _flag(it, "ultimatum"):
        return False
    if _flag(it, "overflight_alert"):
        return True
    return bool(it.get("routine"))


def active_keys(items: dict) -> set[str]:
    """일상 활동(routine)이 아닌 징후 항목."""
    return {k for k, it in items.items() if it["status"] != "none" and not is_routine(it)}


def border_severe(items: dict) -> bool:
    """border_clash 에서 사망 또는 남측 영토·함정 피격이 교차확인됐는가 — 다른 항목 없이도 위험(3)."""
    it = items.get("border_clash") or {}
    return (it.get("status") == "corroborated" and not is_routine(it)
            and any(_flag(it, f) for f in BORDER_SEVERE_FLAGS))


def compute_grade(items: dict) -> int:
    active = active_keys(items)
    any_signal = any(it["status"] != "none" for it in items.values())
    if not active:
        # 일상 훈련·미사일 시험·위협 발언만 있으면 최대 1
        return 1 if any_signal else 0
    corroborated = {k for k in active if items[k]["status"] == "corroborated"}
    if border_severe(items):
        return 3  # 사망·영토 피격 교차확인은 단독으로 위험(연평도·천안함 백테스트)
    if corroborated & CRITICAL and len(active) >= 2:
        return 3  # 핵실험 단독 등 한 항목만으로는 위험으로 올리지 않는다
    if len(active) >= 2 or corroborated:
        return 2
    return 1


def _dt(v) -> datetime | None:
    """ISO 시각 → aware datetime. 시간대가 없으면 UTC 로 본다. 읽을 수 없으면 None."""
    if not isinstance(v, str):
        return None
    try:
        t = datetime.fromisoformat(v)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _persists(it: dict) -> bool:
    if it.get("ended_at"):  # 내림 보류 중에 해제 보도가 온 항목 — 표시는 남되 더 이어 가지 않는다
        return False
    return _flag(it, "ongoing") or _flag(it, "ultimatum") or _flag(it, "unresolved")


def carry_items(items: dict, latest: dict | None, now: str) -> tuple[dict, set[str]]:
    """지속 상태·원인 미상 사건을 이어 간다. 이번 회차가 그 항목을 none 으로 냈어도 해제 근거(ended)가 없으면
    직전 항목을 유지한다(24시간 만료 제외). 시한(until)이 있으면 시한 + 24h 까지.
    unresolved 만 있는 항목은 single 로 이어 간다(원인 발표 전까지 등급 1 바닥). 반환: (새 items, 이어 간 키)"""
    prev_items = (latest or {}).get("items") or {}
    t_now = _dt(now)
    out, carried = dict(items), set()
    for k, prev in prev_items.items():
        cur = items.get(k)
        if k not in ITEMS or not isinstance(prev, dict) or prev.get("status", "none") == "none" or not _persists(prev):
            continue
        if not isinstance(cur, dict) or cur.get("status") != "none" or _flag(cur, "ended"):
            continue
        until = _dt(prev.get("until"))
        if until and t_now and t_now >= until + timedelta(hours=DEADLINE_GRACE_H):
            continue
        it = {**prev, "carried": True, "carried_since": prev.get("carried_since") or now}
        if _flag(prev, "unresolved") and not (_flag(prev, "ongoing") or _flag(prev, "ultimatum")):
            it["status"] = "single"
        out[k] = it
        carried.add(k)
    return out, carried


def unresolved_keys(items: dict) -> list[str]:
    """해제 미확인 상태 — 지속 상태·원인 미상 사건으로 표시 중인 항목(상황판 표시용)."""
    return sorted(k for k, it in items.items() if it.get("status", "none") != "none" and _persists(it))


def deescalated(run: dict, latest: dict | None) -> bool:
    """위기 해제 근거: 회차 deescalation(합의 등 1문장) 또는 직전에 지속 상태(ongoing·최후통첩)였던 항목의 ended.
    원인 미상 사건의 원인 발표(unresolved 의 ended)는 위기 해제가 아니다."""
    d = run.get("deescalation")
    if isinstance(d, str) and d.strip():
        return True
    prev = (latest or {}).get("items") or {}
    return any(_flag(it, "ended") and (_flag(prev.get(k), "ongoing") or _flag(prev.get(k), "ultimatum"))
               for k, it in run["items"].items())


def crisis_floor(events: list[dict], now: str) -> str | None:
    """최근 CRISIS_FLOOR_H 안에 위험(3) 판정이 있었고 그 뒤 해제 근거가 없으면 그 마지막 위험 시각."""
    t_now = _dt(now)
    if t_now is None:
        return None
    for e in reversed(events):
        if e.get("status") != "ok":
            continue
        if e.get("deescalation"):
            return None
        t = _dt(e.get("checked_at"))
        if t is None:
            continue
        if t_now - t > timedelta(hours=CRISIS_FLOOR_H):
            return None
        if e.get("raw_grade", e.get("grade")) == 3:  # 내림 보류로 표시만 3 인 회차는 세지 않는다
            return e["checked_at"]
    return None


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


WORLD_MAX = 5  # 세계 안보 브리핑 — 참고용. 등급·알림·이력에는 쓰지 않는다
_WORLD_LEN = {"title": 120, "summary": 300, "link_korea": 200, "region": 40}


def _opened(run: dict) -> set[str]:
    return {u for u in run.get("opened") or [] if isinstance(u, str) and u.startswith("http")}


def clean_world(run: dict) -> tuple[list[dict], str] | None:
    """run.world 를 너그럽게 거른다: 모양이 틀린 항목·열지 않은 출처는 버리고 5건까지.
    남는 게 없으면 None. 어떤 입력에도 예외를 내지 않는다(판정을 막지 않는다)."""
    try:
        raw = run.get("world")
        if not isinstance(raw, list):
            return None
        opened = _opened(run)
        out = []
        for it in raw:
            if not isinstance(it, dict):
                continue
            src = it.get("source")
            if not isinstance(src, dict) or not isinstance(src.get("url"), str) or not src["url"].startswith("http"):
                continue
            # 기사 주소(url)가 직접 연 원문이거나, 실제로 연 피드·목록(via)에 실린 기사여야 한다.
            # 10/18: 피드 주소를 url 로 넣어 브리핑 링크가 RSS(XML)로 열렸다 → url = 기사, via = 연 피드
            via = src.get("via")
            if src["url"] not in opened and not (isinstance(via, str) and via in opened):
                continue
            row = {}
            for k, n in _WORLD_LEN.items():
                v = it.get(k)
                if isinstance(v, str) and v.strip():
                    v = v.strip()
                    row[k] = v if len(v) <= n else v[:n - 1] + "…"
            if "title" not in row or "summary" not in row:
                continue
            name = src.get("name")
            row["source"] = {"name": name.strip()[:60] if isinstance(name, str) and name.strip() else site(src["url"]),
                             "url": src["url"], **({"via": via} if isinstance(via, str) and via in opened else {})}
            out.append(row)
            if len(out) >= WORLD_MAX:
                break
        if not out:
            return None
        ctx = run.get("world_context")
        ctx = ctx.strip()[:200] if isinstance(ctx, str) else ""
        return out, ctx
    except Exception:  # noqa: BLE001 — 참고 블록이 회차를 깨뜨리면 안 된다
        return None


def with_world(new_latest: dict, run: dict, latest: dict | None, now: str) -> dict:
    """새 latest 에 세계 브리핑을 싣는다. 이번 회차에 쓸 만한 게 없으면 직전 것을 그대로 둔다."""
    for k in ("world", "world_context", "world_at"):
        new_latest.pop(k, None)
    w = clean_world(run)
    if w:
        new_latest["world"], ctx = w
        if ctx:
            new_latest["world_context"] = ctx
        new_latest["world_at"] = now
    elif isinstance(latest, dict) and isinstance(latest.get("world"), list) and latest["world"]:
        for k in ("world", "world_context", "world_at"):
            if k in latest:
                new_latest[k] = latest[k]
    return new_latest


# ── 임박 신호(signals, 선택 · 판정과 무관) ─────────────────────────────────────────────
# 기사에서 「앞으로 일어날 도발」을 가리키는 징후. 등급·알림에 쓰지 않는다. 우리 확률을 만들지 않는다.
# 검증은 scripts/risk_calendar.signal_check 가 만료된 신호의 이후 14일 발사 여부로 센다.
SIGNAL_KINDS = {
    "nav_warning": "항행경보",        # 일본 해상보안청·중국 해사국 항행경보/NOTAM 의 북한 관련 위험구역
    "nk_threat": "북 예고",           # 북한 공식 매체·담화의 구체적 행동 예고
    "site_activity": "시설 동향",     # 발사장·핵실험장·잠수함 기지 준비 정황 보도
    "launcher_move": "발사대 이동",   # TEL 이동·발사 준비 보도
    "scheduled_event": "공표 일정",   # 공식 발표된 북한 정치 일정·한미 훈련
}
SIGNAL_LEVELS = {"official", "report", "analysis"}
SIGNAL_TTL_DAYS = 14      # expires 기본값(첫 관측일 + 14일)
SIGNAL_MAX_TTL_DAYS = 30  # 모델이 더 길게 써도 이 안으로 자른다
SIGNAL_PER_RUN = 5
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}$")
_SIG_LEN = {"summary": 200, "title": 120, "publisher": 60}


def _date_ok(v) -> str | None:
    if not isinstance(v, str) or not _DATE_RE.match(v):
        return None
    try:
        datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        return None
    return v


def signal_id(kind: str, url: str, first_seen: str) -> str:
    return f"{kind}-{first_seen[:10].replace('-', '')}-{hashlib.sha1(url.encode()).hexdigest()[:8]}"


def clean_signal(raw, opened: set[str], now: str) -> dict | None:
    """신호 1건 검증 — 어기면 None(그 신호만 버린다). 어떤 입력에도 예외를 내지 않는다."""
    try:
        if not isinstance(raw, dict) or raw.get("kind") not in SIGNAL_KINDS or raw.get("level") not in SIGNAL_LEVELS:
            return None
        summ = raw.get("summary")
        src = raw.get("source")
        if not isinstance(summ, str) or not summ.strip() or not isinstance(src, dict):
            return None
        url = src.get("url")
        if not isinstance(url, str) or url not in opened:  # 실제로 연 원문만 근거
            return None
        win = raw.get("window")
        if win is not None:
            if not isinstance(win, dict):
                return None
            a, b = win.get("start"), win.get("end")
            a, b = (_date_ok(a) if a is not None else None), (_date_ok(b) if b is not None else None)
            if (win.get("start") is not None and a is None) or (win.get("end") is not None and b is None):
                return None  # 날짜 형식 오류
            if a and b and b < a:
                return None
            win = {"start": a, "end": b or a} if a else None
        t_now = _dt(now) or datetime.now(timezone.utc)
        first_seen = t_now.isoformat(timespec="seconds")
        cap = (t_now + timedelta(days=SIGNAL_MAX_TTL_DAYS)).date().isoformat()
        exp = raw.get("expires")
        if exp is None:
            exp = (t_now + timedelta(days=SIGNAL_TTL_DAYS)).date().isoformat()
        else:
            exp = _date_ok(exp)
            if exp is None or exp < t_now.date().isoformat():
                return None
            exp = min(exp, cap)
        name = src.get("publisher")
        out = {"id": signal_id(raw["kind"], url, first_seen), "kind": raw["kind"], "summary": summ.strip()[:_SIG_LEN["summary"]],
               "window": win, "level": raw["level"], "first_seen": first_seen, "expires": exp,
               "source": {"url": url, "title": str(src.get("title") or "").strip()[:_SIG_LEN["title"]],
                          "publisher": (name.strip()[:_SIG_LEN["publisher"]] if isinstance(name, str) and name.strip() else site(url))}}
        return out
    except Exception:  # noqa: BLE001 — 참고 블록이 회차를 깨뜨리면 안 된다
        return None


SIGNAL_ALSO_MAX = 5


def same_event(sig: dict, same_as, active: list[dict]) -> dict | None:
    """같은 사건 판정(코드): ① 루틴이 same_as 로 기존 신호 id 를 지목 ② 같은 종류 + 둘 다 시점이 있고 기간이 겹침.
    시점 없는 신호끼리는 문장만으로 같은지 코드가 알 수 없다 — 루틴의 same_as 에 맡긴다."""
    for x in active:
        if isinstance(same_as, str) and x.get("id") == same_as and x.get("kind") == sig["kind"]:
            return x
    w = sig.get("window")
    if not w:
        return None
    for x in active:
        v = x.get("window") or None
        if x.get("kind") == sig["kind"] and v and v.get("start") and w["start"] <= (v.get("end") or v["start"]) and v["start"] <= w["end"]:
            return x
    return None


def update_signals(run: dict, doc: dict | None, now: str) -> dict:
    """signals.json 갱신: ① 만료분을 expired 로 옮긴다(지우지 않음) ② run.signals 를 거른 통과분을 active 에 더한다.
    같은 kind·출처 URL 은 active·expired 어디에 있든 다시 쓰지 않는다. 모든 입력에 예외를 내지 않는다."""
    doc = doc if isinstance(doc, dict) else {}
    act = [x for x in doc.get("active") or [] if isinstance(x, dict)]
    exp = [x for x in doc.get("expired") or [] if isinstance(x, dict)]
    today = (_dt(now) or datetime.now(timezone.utc)).date().isoformat()
    keep = []
    for x in act:
        if isinstance(x.get("expires"), str) and x["expires"] < today:
            exp.append({**x, "expired_at": now})
        else:
            keep.append(x)
    seen = {(x.get("kind"), (x.get("source") or {}).get("url")) for x in keep + exp}
    raw = run.get("signals") if isinstance(run, dict) and run.get("status") != "unverified" else None
    added = 0
    if isinstance(raw, list):
        opened = _opened(run)
        for r in raw[:SIGNAL_PER_RUN * 2]:
            sig = clean_signal(r, opened, now)
            if not sig or (sig["kind"], sig["source"]["url"]) in seen:
                continue
            twin = same_event(sig, r.get("same_as") if isinstance(r, dict) else None, keep)
            if twin is not None:  # 같은 사건의 다른 보도 — 새 신호가 아니라 그 신호의 추가 출처(교차확인 수)
                also = twin.setdefault("also", [])
                if len(also) < SIGNAL_ALSO_MAX and sig["source"]["url"] not in {a.get("url") for a in also}:
                    also.append(sig["source"])
                seen.add((sig["kind"], sig["source"]["url"]))
                continue
            keep.append(sig)
            seen.add((sig["kind"], sig["source"]["url"]))
            added += 1
            if added >= SIGNAL_PER_RUN:
                break
    return {"version": 1, "updated_at": now, "active": keep, "expired": exp}


def _load_safe(path: Path):
    """참고 블록용 읽기 — 파일이 없거나 깨졌으면 None(예외를 내지 않는다)."""
    try:
        return json.loads(path.read_text()) if path.exists() else None
    except Exception:  # noqa: BLE001
        return None


def _clean_sources(raw, opened: set[str], limit: int = 3) -> list[dict]:
    """[{url, publisher|name}] → 실제로 연 원문만, URL 중복 제거. 단일 dict(source)도 받는다."""
    if isinstance(raw, dict):
        raw = [raw]
    out, seen = [], set()
    for s in raw if isinstance(raw, list) else []:
        if not isinstance(s, dict):
            continue
        u = s.get("url")
        if not isinstance(u, str) or u not in opened or u in seen:
            continue
        name = s.get("publisher") or s.get("name")
        out.append({"url": u, "publisher": name.strip()[:60] if isinstance(name, str) and name.strip() else site(u)})
        seen.add(u)
        if len(out) >= limit:
            break
    return out


# ── 긴장 국면(ladder, 선택 · 등급·알림과 무관) ─────────────────────────────────────────
# 정본 data/escalation_ladder.json(지표 16개·과거 근거). 루틴은 충족된 지표만 run.ladder 로 낸다.
# 국면 = corroborated 로 충족된 지표 중 가장 높은 stage. single 만이면 「확인 중」(국면을 올리지 않는다).
# 유지: 마지막 충족 뒤 CRISIS_FLOOR_H(기존 상수) — 지속 상태(ongoing)는 해제(ended) 또는 시한(until)+DEADLINE_GRACE_H 까지.
# 해제 근거(run.deescalation)가 있으면 전부 내린다. 새 상수는 표시용 길이 제한뿐이다.
LADDER_FILE = ROOT / "data" / "escalation_ladder.json"
LADDER_STATUSES = ("single", "corroborated")
LADDER_HISTORY_MAX = 60  # 국면 변화 이력 — 화면 표시용, 판정에 쓰지 않는다
LADDER_SOURCES_MAX = 5


def ladder_catalog(path: Path = LADDER_FILE) -> dict:
    """{ids: {id: stage}, names: {stage: 이름}, labels: {id: 지표 이름}, actions: {stage: 할 일}, total} — 읽을 수 없으면 빈 카탈로그."""
    doc = _load_safe(path)
    try:
        ids = {i["id"]: int(i["stage"]) for i in doc["indicators"] if isinstance(i.get("id"), str)}
        names = {int(s["n"]): str(s["name"]) for s in doc.get("stages") or []}
        labels = {i["id"]: str(i.get("label") or i["id"]) for i in doc["indicators"] if isinstance(i.get("id"), str)}
        actions = {int(s["n"]): str(s.get("action") or "") for s in doc.get("stages") or []}
        return {"ids": ids, "names": names, "labels": labels, "actions": actions, "total": len(ids)}
    except Exception:  # noqa: BLE001
        return {"ids": {}, "names": {}, "labels": {}, "total": 0}


def clean_ladder(run: dict, catalog: dict) -> dict:
    """run.ladder → {id: row}. 모르는 id·잘못된 status·연 원문 없는 지표는 그것만 버린다.
    corroborated 인데 서로 다른 사이트가 2곳 미만이면 single 로 낮춘다(items 와 같은 확인 기준).
    ended=true 행은 출처 없이도 받는다(해제 근거 summary). 어떤 입력에도 예외를 내지 않는다."""
    try:
        raw, ids, opened, out = run.get("ladder"), catalog.get("ids") or {}, _opened(run), {}
        if not isinstance(raw, list):
            return {}
        for r in raw[:len(ids) * 2 or 32]:
            if not isinstance(r, dict) or r.get("id") not in ids:
                continue
            summ = r.get("summary").strip()[:200] if isinstance(r.get("summary"), str) else ""
            srcs = _clean_sources(r.get("sources", r.get("source")), opened, LADDER_SOURCES_MAX)
            if _flag(r, "ended"):
                out[r["id"]] = {"ended": True, "summary": summ, "sources": srcs}
                continue
            st = r.get("status")
            if st not in LADDER_STATUSES or not srcs or not summ:
                continue
            if st == "corroborated" and len({site(s["url"]) for s in srcs}) < 2:
                st = "single"
            row = {"status": st, "summary": summ, "sources": srcs}
            if _flag(r, "ongoing"):
                row["ongoing"] = True
                if _dt(r.get("until")):
                    row["until"] = r["until"]
            prev = out.get(r["id"])  # 같은 id 가 두 번 오면 확인 수가 높은 쪽
            if prev and not prev.get("ended") and prev["status"] == "corroborated" and st == "single":
                continue
            out[r["id"]] = row
        return out
    except Exception:  # noqa: BLE001 — 참고 블록이 회차를 깨뜨리면 안 된다
        return {}


def _ladder_alive(ind: dict, t_now: datetime) -> bool:
    if ind.get("ongoing"):
        until = _dt(ind.get("until"))
        return not (until and t_now >= until + timedelta(hours=DEADLINE_GRACE_H))
    last = _dt(ind.get("last_seen"))
    return bool(last and t_now - last <= timedelta(hours=CRISIS_FLOOR_H))


def update_ladder(run: dict, doc: dict | None, now: str, catalog: dict | None = None) -> dict:
    """state/ladder.json = {version, updated_at, judged_at, stage, stage_name, total, met[], checking[],
    indicators{id: {stage,status,summary,sources,first_seen,last_seen,ongoing?,until?}}, history[]}.
    판정 불가 회차(run={})에도 시간 경과로 만료만 반영한다. 어떤 입력에도 예외를 내지 않는다."""
    doc = doc if isinstance(doc, dict) else {}
    try:
        cat = catalog if catalog is not None else ladder_catalog()
        ids, names = cat.get("ids") or {}, cat.get("names") or {}
        if not ids:  # 카탈로그를 못 읽으면 직전 상태를 그대로 둔다(지표를 지우지 않는다)
            return {**doc, "updated_at": now, "error": "catalog"}
        t_now = _dt(now) or datetime.now(timezone.utc)
        run = run if isinstance(run, dict) else {}
        prev_ind = doc.get("indicators") if isinstance(doc.get("indicators"), dict) else {}
        inds = {k: dict(v) for k, v in prev_ind.items() if k in ids and isinstance(v, dict)}
        why = ""
        d = run.get("deescalation")
        if isinstance(d, str) and d.strip():
            inds, why = {}, "해제 근거: " + d.strip()[:120]
        for k, row in clean_ladder(run, cat).items():
            if row.get("ended"):
                if inds.pop(k, None) is not None:
                    why = why or f"{k} 해제" + (f": {row['summary']}" if row.get("summary") else "")
                continue
            old = inds.get(k) or {}
            st = "corroborated" if "corroborated" in (row["status"], old.get("status")) else "single"
            srcs = list(old.get("sources") or [])
            for s in row["sources"]:
                if s["url"] not in {x.get("url") for x in srcs}:
                    srcs.append(s)
            new = {"stage": ids[k], "status": st, "summary": row["summary"], "sources": srcs[-LADDER_SOURCES_MAX:],
                   "first_seen": old.get("first_seen") or now, "last_seen": now}
            if row.get("ongoing"):
                new["ongoing"] = True
                if row.get("until"):
                    new["until"] = row["until"]
            inds[k] = new
        inds = {k: v for k, v in inds.items() if _ladder_alive(v, t_now)}
        met = sorted((k for k, v in inds.items() if v.get("status") == "corroborated"), key=lambda k: (-ids[k], k))
        checking = sorted((k for k, v in inds.items() if v.get("status") != "corroborated"), key=lambda k: (-ids[k], k))
        stage = max((ids[k] for k in met), default=0)
        hist = [h for h in doc.get("history") or [] if isinstance(h, dict)]
        prev_stage = doc.get("stage") if isinstance(doc.get("stage"), int) else 0
        if stage != prev_stage:
            hist.append({"at": now, "from": prev_stage, "to": stage, "why": why or ("만료(마지막 충족 뒤 72시간)" if stage < prev_stage else "")})
        out = {"version": 1, "updated_at": now, "judged_at": now if run.get("checked_at") else doc.get("judged_at"),
               "stage": stage, "stage_name": names.get(stage, ""), "stage_action": (cat.get("actions") or {}).get(stage, ""), "total": len(ids), "met": met, "checking": checking,
               "indicators": inds, "history": hist[-LADDER_HISTORY_MAX:]}
        return out
    except Exception as e:  # noqa: BLE001
        return {**doc, "updated_at": now, "error": str(e)[:120]}


# ── 시나리오(10/22, 확률 없음 · 등급·알림과 무관) ─────────────────────────────────────────
# 완화·현상 유지·격화 3줄. 시나리오마다 지표 충족 수만 센다. 지금 기울기 = 확인된 지표 중 충족 비율이 가장 높은 시나리오
# (동률·전부 0이면 현상 유지). 격화 = 긴장 국면 지표 그대로(met), 현상 유지 = 기존 값에서 코드 판정,
# 완화 = 루틴의 calm(대화 제안 등, 열어 본 출처 필수, 마지막 확인 뒤 72시간 만료) + 30일간 발사 없음.
CALM = {"dialogue_offer": "남북·북미 대화 제안·수용", "channel_restored": "통신선·연락채널 복원",
        "provocation_halt": "확성기·오물풍선 중단 발표", "talks_scheduled": "고위급 회담 일정 공표"}
SCEN_HISTORY_MAX = 60  # 하루 한 줄 — 화면의 「14일 전 대비」에만 쓴다
CAL_FILE = ROOT / "data" / "risk_calendar.json"


def clean_calm(run: dict) -> dict:
    """run.calm = [{id, summary, sources}] → {id: {summary, sources}}. 열어 보지 않은 출처는 버리고, 출처가 없으면 버린다."""
    out = {}
    raw = run.get("calm") if isinstance(run, dict) else None
    if not isinstance(raw, list):
        return out
    opened = _opened(run)
    for r in raw:
        if not isinstance(r, dict) or r.get("id") not in CALM:
            continue
        srcs = _clean_sources(r.get("sources"), opened)
        if srcs:
            out[r["id"]] = {"summary": str(r.get("summary") or "")[:200], "sources": srcs}
    return out


def _ind(i: str, label: str, on: bool | None, note: str = "") -> dict:
    return {"id": i, "label": label, "state": "pre" if on is None else ("on" if on else "off"), "note": note}


def update_scenarios(run: dict, doc: dict | None, now: str, latest: dict | None, ladder: dict | None,
                     signals: dict | None, cal: dict | None, catalog: dict | None = None) -> dict:
    """state/scenarios.json = {version, updated_at, tilt, scenarios[{id,name,tone,desc,indicators[],on,known}],
    calm{id: {...,first_seen,last_seen}}, calm_judged_at, history[{date,tilt,on{}}]}. 어떤 입력에도 예외를 내지 않는다."""
    doc = doc if isinstance(doc, dict) else {}
    try:
        t_now = _dt(now) or datetime.now(timezone.utc)
        run = run if isinstance(run, dict) else {}
        calm = {k: dict(v) for k, v in (doc.get("calm") or {}).items() if k in CALM and isinstance(v, dict)}
        for k, row in clean_calm(run).items():
            calm[k] = {**row, "first_seen": (calm.get(k) or {}).get("first_seen") or now, "last_seen": now}
        calm = {k: v for k, v in calm.items() if _ladder_alive(v, t_now)}
        judged = now if "calm" in run else doc.get("calm_judged_at")
        fresh = bool(_dt(judged) and t_now - _dt(judged) <= timedelta(hours=CRISIS_FLOOR_H))
        tp = (cal or {}).get("tempo") or {}
        recent = tp.get("recent") or []
        last30 = tp.get("last30")
        calm_ind = [_ind(k, v, (k in calm) if fresh else None, (calm.get(k) or {}).get("summary", "")) for k, v in CALM.items()]
        calm_ind.append(_ind("no_launch_30", "30일간 발사 없음", None if last30 is None else last30 == 0,
                             f"마지막 발사 {recent[0]['date'][5:].replace('-', '/')}" if recent and recent[0].get("date") else ""))
        ratio, grade = tp.get("ratio"), (latest or {}).get("grade")
        nsig = len((signals or {}).get("active") or []) if isinstance(signals, dict) else None
        stage = (ladder or {}).get("stage") if isinstance(ladder, dict) else None
        hold_ind = [
            _ind("tempo_up", "최근 30일 발사가 평소 이상", None if ratio is None else ratio >= 1, f"평소의 {ratio}배" if ratio is not None else ""),
            _ind("signal", "임박 신호 1건 이상", None if nsig is None else nsig >= 1, f"{nsig}건" if nsig is not None else ""),
            _ind("grade", "상황판 주의 이상", None if grade is None else grade >= 1, GRADE_LABEL.get(grade, "") if grade is not None else ""),
            _ind("ladder_low", "긴장 국면 0~1단계", None if stage is None else stage <= 1, (ladder or {}).get("stage_name", "")),
        ]
        cat = catalog if catalog is not None else ladder_catalog()
        met = set((ladder or {}).get("met") or []) if isinstance(ladder, dict) else set()
        names = {i: n for i, n in (cat.get("labels") or {}).items()}
        esc_ind = [_ind(i, names.get(i, i), None if stage is None else i in met) for i in (cat.get("ids") or {})]
        scen = []
        for sid, name, tone, desc, ind in (("calm", "완화", 0, "대화 재개·접경 긴장 완화", calm_ind),
                                           ("hold", "현상 유지", 1, "정례 수준의 반복 도발", hold_ind),
                                           ("esc", "격화", 2, "국지 충돌·대치로 번짐", esc_ind)):
            known = [x for x in ind if x["state"] != "pre"]
            scen.append({"id": sid, "name": name, "tone": tone, "desc": desc, "indicators": ind,
                         "on": sum(x["state"] == "on" for x in ind), "known": len(known)})
        rate = {s["id"]: (s["on"] / s["known"] if s["known"] else 0.0) for s in scen}
        top = max(rate.values())
        tilt = "hold" if top == 0 or rate["hold"] == top else max(rate, key=rate.get)
        day = (t_now + timedelta(hours=9)).date().isoformat()
        hist = [h for h in doc.get("history") or [] if isinstance(h, dict) and h.get("date") != day]
        hist.append({"date": day, "tilt": tilt, "on": {s["id"]: s["on"] for s in scen}})
        return {"version": 1, "updated_at": now, "tilt": tilt, "scenarios": scen, "calm": calm,
                "calm_judged_at": judged, "history": hist[-SCEN_HISTORY_MAX:]}
    except Exception as e:  # noqa: BLE001
        return {**doc, "updated_at": now, "error": str(e)[:120]}


# ── 배경 요인(factors, 선택 · 등급·알림과 무관) ─────────────────────────────────────────
# 4개 영역을 완화·보통·긴장으로 본 참고 판정(루틴이 KST 08·14·20시 회차, 또는 state/factors.json 이 10시간보다 오래됐을 때 낸다).
# 과거 20년 발사 기록으로 검증한 결과 네 영역 모두 「평소 수준」이라 등급·알림에 쓰지 않는다.
FACTOR_KEYS = ("nk_domestic", "inter_korean", "great_powers", "public_opinion")
FACTOR_LEVELS = ("완화", "보통", "긴장")
_FACTOR_LEN = {"summary": 200, "changed": 160}
FACTOR_HISTORY_MAX = 60  # 변화 이력 — 화면이 「언제부터」를 보여 주는 데만 쓴다


def clean_factors(run: dict) -> dict:
    """run.factors → {키: row}. 키·level 이 틀리면 그 영역만 버린다. 완화·긴장은 연 원문과 근거 문장이 있어야 한다
    (근거가 없으면 보통 — 루틴 규칙). 어떤 입력에도 예외를 내지 않는다."""
    try:
        raw, opened, out = run.get("factors"), _opened(run), {}
        if not isinstance(raw, dict):
            return {}
        for k in FACTOR_KEYS:
            it = raw.get(k)
            if not isinstance(it, dict) or it.get("level") not in FACTOR_LEVELS:
                continue
            row = {"level": it["level"], "sources": _clean_sources(it.get("sources"), opened)}
            for f, n in _FACTOR_LEN.items():
                v = it.get(f)
                if isinstance(v, str) and v.strip():
                    row[f] = v.strip()[:n]
            if it["level"] != "보통" and (not row["sources"] or "summary" not in row):
                continue
            out[k] = row
        return out
    except Exception:  # noqa: BLE001
        return {}


def update_factors(run: dict, doc: dict | None, now: str) -> dict:
    """state/factors.json = {version, at, domains{키: {level, summary, sources, at, since}}, history[]}.
    since = 지금 level 이 시작된 시각. 빠진 영역은 직전 값을 잇는다. at = 마지막으로 요인을 받은 회차."""
    doc = doc if isinstance(doc, dict) else {}
    try:
        dom = {k: v for k, v in (doc.get("domains") or {}).items() if k in FACTOR_KEYS and isinstance(v, dict)}
        hist = [h for h in doc.get("history") or [] if isinstance(h, dict)]
        new = clean_factors(run if isinstance(run, dict) else {})
        for k, row in new.items():
            prev = dom.get(k) or {}
            if prev.get("level") and prev["level"] != row["level"]:
                hist.append({"at": now, "key": k, "from": prev["level"], "to": row["level"], "why": row.get("changed", "")})
            since = prev.get("since") if prev.get("level") == row["level"] else now
            dom[k] = {**{f: v for f, v in row.items() if f != "changed"}, "at": now, "since": since or now}
        return {"version": 1, "at": now if new else doc.get("at"), "domains": dom, "history": hist[-FACTOR_HISTORY_MAX:]}
    except Exception as e:  # noqa: BLE001
        return {**doc, "error": str(e)[:120]}


# ── 연동 위기(linkage, 선택 · 등급·알림과 무관) ─────────────────────────────────────────
# 대만 해협 명명 훈련 여부(뉴스 판정) + 한반도 연결 사실(관측된 사실만). 정본·과거 검정은 data/linkage.json —
# 대만 해협 대규모 훈련·러-우/북러 사건 앞뒤 북한 발사는 모두 「평소 수준」이라 등급·알림에 쓰지 않는다.
# 확인 기준·유지는 긴장 국면(ladder)과 같다: 서로 다른 사이트 2곳 이상 = corroborated, 마지막 확인 뒤 CRISIS_FLOOR_H 유지.
# 루틴이 이번 회차에 linkage 를 안 내면 직전 값을 잇는다(시간 경과 만료만 반영).
TAIWAN_STATES = ("named", "large_unnamed", "none")
KOREA_LINKS_MAX = 5  # 화면 표시 길이 — 판정에 쓰지 않는다
_LINK_LEN = {"summary": 200, "name": 80}


def _alive(row: dict, t_now: datetime) -> bool:
    last = _dt(row.get("last_seen"))
    return bool(last and t_now - last <= timedelta(hours=CRISIS_FLOOR_H))


def _status(srcs: list[dict]) -> str:
    return "corroborated" if len({site(s["url"]) for s in srcs}) >= 2 else "single"


def _merge_sources(old: list, new: list) -> list[dict]:
    out = [s for s in old if isinstance(s, dict) and s.get("url")]
    for s in new:
        if s["url"] not in {x["url"] for x in out}:
            out.append(s)
    return out[-LADDER_SOURCES_MAX:]


def clean_linkage(run: dict) -> dict:
    """run.linkage → {taiwan?: row, korea: [row]}. 연 원문 없는 줄·모르는 state 는 그것만 버린다. 예외를 내지 않는다."""
    out: dict = {"korea": []}
    try:
        raw, opened = run.get("linkage"), _opened(run)
        if not isinstance(raw, dict):
            return out
        tw = raw.get("taiwan")
        if isinstance(tw, dict):
            srcs = _clean_sources(tw.get("sources", tw.get("source")), opened, LADDER_SOURCES_MAX)
            summ = tw.get("summary").strip()[:_LINK_LEN["summary"]] if isinstance(tw.get("summary"), str) else ""
            if _flag(tw, "ended") or tw.get("state") == "none":
                if srcs:  # 해제·없음도 연 원문이 있어야 직전 훈련을 내린다(근거 없는 「없음」은 직전 값을 잇는다)
                    out["taiwan"] = {"state": "none", "summary": summ, "sources": srcs}
            elif tw.get("state") in TAIWAN_STATES and srcs and summ:
                row = {"state": tw["state"], "summary": summ, "sources": srcs}
                if isinstance(tw.get("name"), str) and tw["name"].strip():
                    row["name"] = tw["name"].strip()[:_LINK_LEN["name"]]
                for f in ("start", "end"):
                    if _date_ok(tw.get(f)):
                        row[f] = tw[f]
                out["taiwan"] = row
        for k in raw.get("korea") if isinstance(raw.get("korea"), list) else []:
            if not isinstance(k, dict) or not isinstance(k.get("summary"), str) or not k["summary"].strip():
                continue
            srcs = _clean_sources(k.get("sources", k.get("source")), opened, LADDER_SOURCES_MAX)
            if not srcs:
                continue
            row = {"summary": k["summary"].strip()[:_LINK_LEN["summary"]], "sources": srcs}
            if _date_ok(k.get("date")):
                row["date"] = k["date"]
            if isinstance(k.get("same_as"), str):
                row["same_as"] = k["same_as"]
            out["korea"].append(row)
            if len(out["korea"]) >= KOREA_LINKS_MAX:
                break
        return out
    except Exception:  # noqa: BLE001 — 참고 블록이 회차를 깨뜨리면 안 된다
        return {"korea": []}


def update_linkage(run: dict, doc: dict | None, now: str) -> dict:
    """state/linkage.json = {version, updated_at, judged_at, taiwan{state,status,name?,start?,end?,summary,sources,first_seen,last_seen}|None,
    last_named{name,start?,end?,at}?, korea[{id,status,summary,date?,sources,first_seen,last_seen}]}.
    taiwan 은 corroborated 일 때만 화면이 「훈련 중」으로 쓴다(single = 확인 중). 어떤 입력에도 예외를 내지 않는다."""
    doc = doc if isinstance(doc, dict) else {}
    try:
        t_now = _dt(now) or datetime.now(timezone.utc)
        run = run if isinstance(run, dict) else {}
        new = clean_linkage(run)
        tw = doc.get("taiwan") if isinstance(doc.get("taiwan"), dict) else None
        last_named = doc.get("last_named") if isinstance(doc.get("last_named"), dict) else None
        row = new.get("taiwan")
        if row and row["state"] == "none":
            tw = None
        elif row:
            same = tw and tw.get("state") == row["state"] and (tw.get("name") or "") == (row.get("name") or "")
            srcs = _merge_sources(tw["sources"] if same else [], row["sources"])
            st = "corroborated" if (same and tw.get("status") == "corroborated") else _status(srcs)
            tw = {**{f: v for f, v in row.items() if f != "sources"}, "status": st, "sources": srcs,
                  "first_seen": (tw.get("first_seen") if same else None) or now, "last_seen": now}
        if tw and not _alive(tw, t_now):
            tw = None
        if tw and tw.get("state") == "named" and tw.get("status") == "corroborated" and tw.get("name"):
            last_named = {"name": tw["name"], **{f: tw[f] for f in ("start", "end") if tw.get(f)}, "at": now}
        korea = [dict(k) for k in doc.get("korea") or [] if isinstance(k, dict) and isinstance(k.get("sources"), list)]
        for r in new["korea"]:
            urls = {s["url"] for s in r["sources"]}
            hit = next((k for k in korea if k.get("id") == r.get("same_as") or urls & {s.get("url") for s in k["sources"]}), None)
            if hit:
                hit["sources"] = _merge_sources(hit["sources"], r["sources"])
                hit["status"] = _status(hit["sources"])
                hit["last_seen"] = now
                continue
            korea.append({"id": signal_id("korea", r["sources"][0]["url"], now), "status": _status(r["sources"]),
                          "summary": r["summary"], **({"date": r["date"]} if r.get("date") else {}),
                          "sources": r["sources"], "first_seen": now, "last_seen": now})
        korea = sorted((k for k in korea if _alive(k, t_now)), key=lambda k: k["last_seen"], reverse=True)[:KOREA_LINKS_MAX]
        got = "linkage" in run and isinstance(run.get("linkage"), dict)
        out = {"version": 1, "updated_at": now, "judged_at": now if got else doc.get("judged_at"),
               "taiwan": tw, "korea": korea}
        if last_named:
            out["last_named"] = last_named
        return out
    except Exception as e:  # noqa: BLE001
        return {**doc, "updated_at": now, "error": str(e)[:120]}


def last_verified(events: list[dict]) -> dict | None:
    """판정 불가 회차를 건너뛴 직전 판정 회차."""
    return next((e for e in reversed(events) if e.get("status") == "ok"), None)


def drop_confirmed(events: list[dict], cur_grade: int) -> bool:
    """내림은 두 회차 연속일 때만 — 직전 판정 회차의 산정 등급도 현재 등급보다 낮았는가.
    판정 불가 회차는 연속을 늘리지도 끊지도 않는다(last_verified 가 건너뛴다)."""
    e = last_verified(events)
    return e is not None and e.get("raw_grade", e.get("grade", cur_grade)) < cur_grade


def held_step(run: dict, latest: dict, events: list[dict], grade: int, active: set[str],
              now: str) -> tuple[dict, dict, None]:
    """내림 보류: 표시 등급·항목은 직전 판정 그대로, 이번 산정값은 이력과 latest.held 에 정직하게 남긴다.
    알림(해제 포함)은 내림이 적용되는 회차에 한 번만 나간다."""
    cur = latest.get("grade", 0)
    new_latest = with_world({k: v for k, v in latest.items() if k != "held"}, run, latest, now)
    ended = {k for k, it in run["items"].items() if _flag(it, "ended")}
    if ended & set(new_latest.get("items") or {}):
        new_latest["items"] = {k: ({**it, "ended_at": now} if k in ended and isinstance(it, dict) else it)
                               for k, it in new_latest["items"].items()}
        unres = unresolved_keys(new_latest["items"])
        new_latest.pop("unresolved", None)
        if unres:
            new_latest["unresolved"] = unres
    new_latest["held"] = {"raw_grade": grade, "raw_label": GRADE_LABEL[grade], "at": now,
                          "summary": run.get("overall_summary", "")}
    event = {"checked_at": now, "status": "ok", "grade": cur, "prev_grade": cur, "raw_grade": grade,
             "held": True, "active": sorted(latest.get("active", [])), "raw_active": sorted(active), "new": [],
             "notified": False, "summary": run.get("overall_summary", ""), "sources": event_sources(run["items"]),
             "friends": False}
    return new_latest, event, None


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

    deesc = deescalated(run, latest)
    items, carried = carry_items(run["items"], latest, now)
    run["items"] = items
    base_grade = compute_grade(items)
    grade = base_grade
    floor_at = None if deesc else crisis_floor(events, now)
    if floor_at and grade < CRISIS_FLOOR_GRADE:
        grade = CRISIS_FLOOR_GRADE
    else:
        floor_at = None
    prev_grade = (latest or {}).get("grade", 0)
    prev_active = set((latest or {}).get("active", []))
    active = active_keys(items)
    extra = {"base_grade": base_grade} if base_grade != grade else {}
    if floor_at:
        extra["floor"] = True
    if carried:
        extra["carried"] = sorted(carried)
    if deesc:
        extra["deescalation"] = True

    if latest is not None and grade < prev_grade and not drop_confirmed(events, prev_grade):
        nl, ev, ob = held_step(run, latest, events, grade, active, now)
        ev.update(extra)
        return nl, ev, ob

    new_keys = active - prev_active

    if latest is None:
        notify = grade >= 1  # 최초 분석: 징후가 있으면 알린다
    else:
        notify = grade > prev_grade or (grade >= 1 and grade == prev_grade and bool(new_keys))
    # 지인(ntfy): 경계 이상에서 알림 조건을 만족할 때 + 경계 이상에서 내려올 때(해제)
    friends = notify and grade >= FORWARD_MIN_GRADE
    released = latest is not None and prev_grade >= FORWARD_MIN_GRADE > grade
    # 주의 → 평시 는 본인에게만 조용히(우선순위 2) — 10/18 「주의 알림 뒤 아무 소식이 없다」(hayden 결정: 본인에게도 보낸다)
    calmed = latest is not None and not released and prev_grade >= 1 and grade == 0
    notify = notify or released or calmed

    new_latest = {**run, "grade": grade, "grade_label": GRADE_LABEL[grade], "active": sorted(active)}
    for k in ("unresolved", "floor", "signals", "ladder", "factors", "linkage", "calm"):  # 참고 블록은 state/signals·ladder·factors·linkage·scenarios.json 에 따로 쌓는다
        new_latest.pop(k, None)
    unres = unresolved_keys(items)
    if unres:
        new_latest["unresolved"] = unres
    if floor_at:
        new_latest["floor"] = {"since": floor_at, "base_grade": base_grade,
                               "until": (_dt(floor_at) + timedelta(hours=CRISIS_FLOOR_H)).isoformat()}
    new_latest = with_world(new_latest, run, latest, now)
    event = {"checked_at": now, "status": "ok", "grade": grade, "prev_grade": prev_grade, "raw_grade": grade,
             "active": sorted(active), "new": sorted(new_keys), "notified": notify,
             "summary": run.get("overall_summary", ""), "sources": event_sources(items), **extra}
    event["friends"] = friends or released
    outbox = None
    if notify:
        me, fr = alert_messages(run, grade, new_keys, released or calmed, prev_grade, friends)
        # 방문자(웹 푸시): 평시를 벗어날 때부터 — 주의 진입·격상·새 징후·평시 복귀 전부(10/19 hayden 「특히 평시 아닐 때」).
        # 본문은 지인 문구와 같은 틀(「지인 주제에도 발송됨」 꼬리 없음). 지인(ntfy) 기준은 그대로 경계 이상
        public = fr or me
        if calmed:
            me, fr = {**me, "priority": 2}, None
        outbox = {"id": now, "grade": grade, "link": DASHBOARD_URL, "me": me, "public": public}
        if fr:
            outbox["friends"] = fr
    return new_latest, event, outbox


def check(path: str) -> int:
    """기록 없는 사전 검사. 루틴이 고칠 수 있는 형식 오류를 본 실행 전에 걸러, 스스로 고친 시도가
    events.jsonl 에 판정 불가로 남지 않게 한다(10/19~21 「판정 불가 → 1분 뒤 정상」 3회).
    고치지 못한 채 본 실행에 넣으면 지금처럼 판정 불가로 기록되고 연속 계수에 들어간다."""
    try:
        run = json.loads(Path(path).read_text())
        if not isinstance(run, dict):
            raise RunError("run.json 최상위는 객체여야 한다")
        validate(run)
    except (RunError, ValueError, OSError) as e:
        print(json.dumps({"check": "fail", "error": f"schema: {e}"}, ensure_ascii=False))
        return 1
    print(json.dumps({"check": "ok"}, ensure_ascii=False))
    return 0


def supersede_retries(events: list[dict], event: dict) -> int:
    """성공 회차가 붙기 직전의 연속 판정 불가 줄 중 RETRY_WINDOW_MIN 안의 것에 superseded 를 적는다.
    루틴이 스키마 거절 뒤 출처를 더 열어 같은 회차에 다시 돌린 흔적이라, 「판정 불가」로 세면 노이즈다(10/18~10/21 4건 전부)."""
    if event.get("status") != "ok":
        return 0
    now = _dt(event["checked_at"])
    n = 0
    for e in reversed(events):
        if e.get("status") != "unverified" or e.get("superseded"):
            break
        if (now - _dt(e["checked_at"])).total_seconds() > RETRY_WINDOW_MIN * 60:
            break
        e["superseded"] = event["checked_at"]
        n += 1
    return n


def commit_message(event: dict) -> str:
    """git 이력의 등급 문구는 코드가 정한다(모델이 쓰면 '경계'·'관심' 같은 오표기가 섞인다 — 10/22 실측)."""
    t = event["checked_at"].replace("+00:00", "Z")
    if event.get("status") != "ok":
        return f"watch: unverified {t}"
    g = event["grade"]
    tag = f"{GRADE_LABEL[g]}({g})"
    if event.get("notified"):
        tag += " 알림"
    return f"watch: {tag} {t}"


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "--check":
        return check(argv[2])
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
    # 브리핑 진단: 루틴이 world 를 냈는지(raw) · 열람 검사 뒤 몇 건 남았는지(kept). 판정과 무관
    raw = run.get("world") if isinstance(run, dict) else None
    kept = clean_world(run) if isinstance(run, dict) else None
    event["world"] = {"raw": len(raw) if isinstance(raw, list) else 0, "kept": len(kept[0]) if kept else 0}
    if event.get("status") == "ok":  # 국내 해킹 단독 검색 여부 — 판정과 무관한 진단
        event["cyber_domestic"] = cyber_domestic_queries(run.get("coverage"))
    STATE.mkdir(exist_ok=True)
    try:  # 임박 신호는 판정과 무관 — 실패해도 회차를 막지 않는다
        sig = update_signals(run if event.get("status") == "ok" else {}, _load(STATE / "signals.json", None), event["checked_at"])
        (STATE / "signals.json").write_text(json.dumps(sig, ensure_ascii=False, indent=2) + "\n")
        event["signals"] = {"raw": len(run["signals"]) if isinstance(run.get("signals"), list) else 0, "active": len(sig["active"])}
    except Exception as e:  # noqa: BLE001
        event["signals"] = {"error": str(e)[:120]}
    ok = run if event.get("status") == "ok" and isinstance(run, dict) else {}
    try:  # 긴장 국면 — 등급·알림과 무관. 판정 불가 회차는 시간 경과(만료)만 반영
        lad = update_ladder(ok, _load_safe(STATE / "ladder.json"), event["checked_at"])
        (STATE / "ladder.json").write_text(json.dumps(lad, ensure_ascii=False, indent=2) + "\n")
        event["ladder"] = {"raw": len(ok["ladder"]) if isinstance(ok.get("ladder"), list) else 0,
                           "stage": lad.get("stage"), "met": len(lad.get("met") or []), "checking": len(lad.get("checking") or [])}
    except Exception as e:  # noqa: BLE001
        event["ladder"] = {"error": str(e)[:120]}
    try:  # 배경 요인 — 등급·알림과 무관. 요인 회차가 아니면 직전 값을 그대로 쓴다
        fac = update_factors(ok, _load_safe(STATE / "factors.json"), event["checked_at"])
        (STATE / "factors.json").write_text(json.dumps(fac, ensure_ascii=False, indent=2) + "\n")
        event["factors"] = {"raw": len(ok["factors"]) if isinstance(ok.get("factors"), dict) else 0,
                            "kept": len(clean_factors(ok))}
    except Exception as e:  # noqa: BLE001
        event["factors"] = {"error": str(e)[:120]}
    try:  # 연동 위기 — 등급·알림과 무관. 안 내면 직전 값을 잇고, 판정 불가 회차는 시간 경과(만료)만 반영
        lk = update_linkage(ok, _load_safe(STATE / "linkage.json"), event["checked_at"])
        (STATE / "linkage.json").write_text(json.dumps(lk, ensure_ascii=False, indent=2) + "\n")
        event["linkage"] = {"taiwan": (lk.get("taiwan") or {}).get("state", "none") if not lk.get("error") else None,
                            "korea": len(lk.get("korea") or [])}
    except Exception as e:  # noqa: BLE001
        event["linkage"] = {"error": str(e)[:120]}
    try:  # 시나리오 — 등급·알림과 무관. 판정 불가 회차는 시간 경과(만료)와 다른 칸 값만 반영
        sc = update_scenarios(ok, _load_safe(STATE / "scenarios.json"), event["checked_at"],
                              new_latest if new_latest is not None else latest, _load_safe(STATE / "ladder.json"),
                              _load_safe(STATE / "signals.json"), _load_safe(CAL_FILE))
        (STATE / "scenarios.json").write_text(json.dumps(sc, ensure_ascii=False, indent=2) + "\n")
        event["scenarios"] = {"tilt": sc.get("tilt"), "calm": len(sc.get("calm") or {})} if not sc.get("error") else {"error": sc["error"]}
    except Exception as e:  # noqa: BLE001
        event["scenarios"] = {"error": str(e)[:120]}
    if new_latest is not None:
        (STATE / "latest.json").write_text(json.dumps(new_latest, ensure_ascii=False, indent=2) + "\n")
    if supersede_retries(events, event):
        events_path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in [*events, event]))
    else:
        with events_path.open("a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    (STATE.parent / ".commit_msg").write_text(commit_message(event) + "\n")
    if outbox is not None:
        (STATE / "outbox.json").write_text(json.dumps(outbox, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"event": event, "notify": outbox is not None, "commit": commit_message(event)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

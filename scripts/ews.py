#!/usr/bin/env python3
"""조기경보 지수(EWS) — 한반도 직접(D)과 세계 연동(E)을 합친 참고 지수. 공식 등급·알림과 무관하다.

  python3 scripts/ews.py          매시간 루틴 2-2단계 — state/ews.json 만 쓴다(state/theaters.json 은 읽기만)

  state/theaters.json 은 배경 루틴(하루 3회)이 scripts/background.py 로 쌓는다 — 그 안에서 이 파일의 ingest 를 부른다(11/05 분리).
  diff_state.update_linkage 는 정해진 키만 다시 쓰므로 theaters 를 state/linkage.json 에 둘 수 없다.
  실패해도 rc 0 — 참고 지수가 회차를 막으면 안 된다. 실패는 stdout 의 ::warning:: 과 state/ews.json 의 error 로 남긴다.

  근거 없는 전역은 「평온」이 아니라 미관측이다. 미관측이 많거나 판정이 묵으면 0점 대신 보류한다.

  공식 단계와 어긋나지 않으면서 단계 안의 차이를 보여 준다.
  - α=1: E 는 표시만 하고 지수에 합치지 않는다. 대만 해협·러-우 사건 앞뒤 북한 발사는 과거 검정 6건 모두 「평소 수준」이다
    (data/linkage.json). α 는 Phase 3 백테스트를 통과한 뒤에만 낮춘다.
  - 단계 안 위치: 지수 구간은 공식 단계가 정하고, 값은 그 단계에서 D 가 가질 수 있는 최댓값 대비 위치다.
    D 그대로는 10개 항목 평균이라 공식 경계에서도 25 안팎이었다. 등급이 안 보는 정례 징후가 여기서 차이를 만든다.
    최댓값은 등급 규칙에서 나온 값이고 과거 사건에 맞춘 값이 아니다. 이 위치가 단계 상승을 앞서는지는 scripts/ews_backtest.py 로 본다.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import posixpath
from urllib.parse import urlsplit
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
STATE = ROOT / "state"
WEIGHTS = ROOT / "data" / "ews_weights.json"

THEATERS = ("taiwan", "ukraine", "scs_ecs", "mideast", "us_posture")
CHANNELS = ("c1_us_gap", "c2_nk_ru_cn", "c3_simul", "c4_sea_energy", "c5_diplomacy")
CORE_ITEMS = {"nuclear", "troop_movement", "gov_warning"}  # 항목 점수 ×2
GRADE_POINT = {"none": 0, "single": 1, "corroborated": 2}
BANDS = ((75, "고위험 징후"), (50, "경계 징후"), (25, "관찰"), (0, "안정"))
D_POINTS_MAX = 26
# 공식 단계별 (구간 하한, 상한, 그 단계에서 가능한 D 점수 최대). 점수 최대는 항목 상태 5^10 전수 열거 결과(compute_grade 기준).
GRADE_SCALE = {0: (0, 24, 26), 1: (25, 49, 13), 2: (50, 74, 20), 3: (75, 100, 26)}
SOURCES_MAX = 5
SUMMARY_LEN = 200
SOURCE_MAX_AGE_H = 72  # 전역 출처는 판정 시각 기준 72시간 안에 나온 원문만(prompts/background.md 4-5 theaters) — published_at 없음·파싱 불가도 버린다
# 미래 시각 허용폭. 판정 시각(now)은 background.py 가 회차 시작이 아니라 실행 직전 시계로 찍으므로, 회차 도중 열린 기사의 published_at 이
# now 를 넘을 일은 없다 — 남는 것은 매체 서버·우리 러너 시계의 차이뿐이라 NTP 오차 규모(초~분)로 작게 둔다. 시간대 표기 실수(±9시간 등)는 걸러진다.
FUTURE_SKEW_MIN = 10
ERROR_LEN = 200
# 평온 확인용 정본 피드(prompts/background.md 0-1 표와 같아야 한다 — 테스트가 대조). 기사가 드문 전역(남중국해·미 전략자산)은
# 「관련 기사 없음」 자체가 관측이다: state 0 에 한해 이 피드를 연 기록을 근거로 받는다(11/05 — 2곳이 매 회차 미관측으로 남았다).
# 조건 = 이 목록의 피드를 실제로 열었고(opened) · 피드 최신 항목이 72시간 안(피드가 살아 있다) · 정본이 2곳 이상이면 2곳 ·
# coverage["linkage:theaters"] 에 검색어가 있다. state 1 이상은 종전대로 기사 원문이 있어야 한다.
QUIET_FEEDS = {
    "taiwan": ("https://feeds.feedburner.com/rsscna/engnews", "https://feeds.feedburner.com/rsscna/politics",
               "https://feeds.feedburner.com/rsscna/mainland"),
    "scs_ecs": ("https://www.philstar.com/rss/nation", "https://www.rappler.com/feed/",
                "https://feeds.bbci.co.uk/news/world/asia/rss.xml"),
    "us_posture": ("https://www.stripes.com/theaters/asia_pacific/", "https://www.dvidshub.net/rss/news"),
    "ukraine": ("https://feeds.bbci.co.uk/news/world/europe/rss.xml",),
    "mideast": ("https://feeds.bbci.co.uk/news/world/middle_east/rss.xml",),
}
QUIET_MIN_FEEDS = 2


def _feed_key(u) -> str:
    """주소 비교용 정규화(표준 파서) — scheme·www·대소문자·기본 포트·fragment·query·점 경로·끝 슬래시 차이를 지운다."""
    try:
        sp = urlsplit(str(u).strip())
        host = (sp.hostname or "").lower()
        port = sp.port
    except ValueError:
        return str(u).strip().lower()
    host = host[4:] if host.startswith("www.") else host
    if port not in (None, 80, 443):
        host = f"{host}:{port}"
    path = posixpath.normpath(sp.path) if sp.path else ""
    path = "" if path in (".", "/") else path.lower()
    return (host + path).rstrip("/")


# 지시문 0-1 표에 있는 목록 주소 전부(평온 확인용 + 다른 블록용) — 어느 것이든 기사로는 세지 않는다
OTHER_FEEDS = ("https://feeds.bbci.co.uk/news/world/rss.xml", "https://www.segye.com/Articles/RSSList/segye_politic.xml")
ALL_FEEDS = {_feed_key(u) for us in (*QUIET_FEEDS.values(), OTHER_FEEDS) for u in us}
FEED_SEGMENTS = {"rss", "feed", "feeds", "atom", "rsslist"}  # 경로 조각이 정확히 이것이면 피드
FEED_EXT = (".xml", ".rss", ".atom")


def is_feed(u) -> bool:
    """목록(피드) 주소인가 — 표의 주소, 또는 모양이 피드(feeds.·feedburner 호스트, 경로 조각 rss/feed/atom, .xml·.rss·.atom).
    부분 문자열이 아니라 조각 단위로 본다(기사 경로의 atomic·feeding 은 피드가 아니다). 피드는 기사 근거가 아니다."""
    k = _feed_key(u)
    if k in ALL_FEEDS:
        return True
    host, _, path = k.partition("/")
    segs = [x for x in path.split("/") if x]
    return (host.startswith("feeds.") or "feedburner." in host or any(x in FEED_SEGMENTS for x in segs)
            or bool(segs) and segs[-1].endswith(FEED_EXT))


def _dt(s) -> datetime | None:
    """ISO 8601, 또는 RSS pubDate(RFC 822/2822, 예 'Thu, 05 Nov 2026 00:00:00 +0000') — 피드 날짜를 그대로 옮겨도 읽는다."""
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        pass
    try:
        return parsedate_to_datetime(str(s)) if s else None
    except (TypeError, ValueError, IndexError):
        return None


def _round(x: float) -> int:
    return math.floor(x + 0.5)


def band(ews: int) -> str:
    return next(name for low, name in BANDS if ews >= low)


def place(raw: float, grade) -> int:
    """반올림 전 값 → 공식 단계 구간 안의 지수. 단계를 모르면 값 그대로."""
    if grade not in GRADE_SCALE:
        return _round(raw)
    low, high, points = GRADE_SCALE[grade]
    return _round(low + (high - low) * min(raw / (points / D_POINTS_MAX * 100), 1))


def score_d(items: dict) -> float:
    """D = 항목 점수 합 / 최대 × 100. 등급점 × (routine 0.5) × (핵심 2). 항목이 빠지면 KeyError — 0점으로 넘기지 않는다."""
    import diff_state as d
    total = maximum = 0.0
    for key in d.ITEMS:
        core = 2 if key in CORE_ITEMS else 1
        it = items[key]
        total += GRADE_POINT[it["status"]] * (0.5 if d.is_routine(it) else 1) * core
        maximum += 2 * core
    return total / maximum * 100


def _observed(row, t_now: datetime, stale: timedelta) -> bool:
    if not isinstance(row, dict) or not any(isinstance(s, dict) and s.get("url") for s in row.get("sources") or []):
        return False
    at = _dt(row.get("judged_at"))  # 이번 회차에 안 내서 이어받은 전역이 묵으면 근거로 치지 않는다
    return at is None or t_now - at <= stale


def compute(latest: dict, doc: dict, weights: dict, now: str, last_good: dict | None = None) -> dict:
    t_now = _dt(now)
    stale = timedelta(hours=weights["stale_hours"])
    w, alpha = weights["weights"], weights["alpha"]
    d_raw = score_d(latest["items"])
    rows = doc.get("theaters") if isinstance(doc.get("theaters"), dict) else {}
    observed = {t: _observed(rows.get(t), t_now, stale) for t in THEATERS}

    judged_at, latest_at = doc.get("judged_at"), latest.get("checked_at")
    judged_dt, latest_dt = _dt(judged_at), _dt(latest_at)
    n_observed = sum(observed.values())
    world_short = (judged_dt is None or t_now - judged_dt > stale or n_observed < weights["min_observed_theaters"])
    latest_stale = latest_dt is None or t_now - latest_dt > stale
    withheld = latest_stale or (alpha < 1 and world_short)  # α=1 이면 세계 연동 공백이 지수를 막지 않는다

    denom = sum(w[t][c] for t in THEATERS for c in CHANNELS)

    def part(t: str, c: str) -> float:
        if not observed[t]:
            return 0.0
        row = rows[t]
        return row["state"] / 3 * w[t][c] if c in (row.get("channels") or []) else 0.0

    e_raw = sum(part(t, c) for t in THEATERS for c in CHANNELS) / denom * 100
    theaters = []
    for t in THEATERS:
        row = rows.get(t) if isinstance(rows.get(t), dict) else {}
        theaters.append({"key": t, "observed": observed[t], "state": row.get("state") if observed[t] else None,
                         "contribution": None if world_short else _round(sum(part(t, c) for c in CHANNELS) / denom * 100),
                         "channels": (row.get("channels") or []) if observed[t] else [],
                         "summary": row.get("summary", "") if observed[t] else "",
                         "sources": (row.get("sources") or []) if observed[t] else [],
                         "judged_at": row.get("judged_at") if observed[t] else None})  # 확인 시각 — 화면이 기여 대신 보여 준다(alpha==1)
    channels = [{"key": c, "observed": any(observed[t] and c in (rows[t].get("channels") or []) for t in THEATERS),
                 "value": None if world_short else _round(sum(part(t, c) for t in THEATERS) / denom * 100)}
                for c in CHANNELS]

    out = {"version": 1, "computed_at": now, "weights_version": weights["version"], "status": "withheld" if withheld else "ok",
           "ews": None, "band": None, "d": _round(d_raw), "e": None if world_short else _round(e_raw), "alpha": alpha,
           "grade": latest.get("grade"), "grade_label": latest.get("grade_label"),
           "coverage": {"observed_theaters": n_observed, "linkage_judged_at": judged_at, "latest_updated_at": latest_at},
           "theaters": theaters, "channels": channels, "last_good": last_good}
    if not withheld:
        ews = place(alpha * d_raw + (1 - alpha) * (0.0 if world_short else e_raw), latest.get("grade"))  # 반올림 전 값으로 합친다
        out.update(ews=ews, band=band(ews))
        out["last_good"] = {"ews": ews, "band": out["band"], "computed_at": now}
    return out


def error_doc(err: str, now: str, weights_version, last_good: dict | None) -> dict:
    return {"version": 1, "computed_at": now, "weights_version": weights_version, "status": "error", "ews": None, "band": None,
            "d": None, "e": None, "coverage": None, "theaters": [], "channels": [], "last_good": last_good,
            "error": err[:ERROR_LEN]}


def _fresh(src: dict, t_now: datetime | None) -> bool:
    """출처의 published_at 이 판정 시각 기준 [−FUTURE_SKEW_MIN, SOURCE_MAX_AGE_H] 안인가(UTC 정규화). 없거나 파싱이 안 되면 False."""
    pub = _dt(src.get("published_at")) if isinstance(src.get("published_at"), str) else None
    if pub is None or t_now is None:
        return False
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    if t_now.tzinfo is None:
        t_now = t_now.replace(tzinfo=timezone.utc)
    age = t_now.astimezone(timezone.utc) - pub.astimezone(timezone.utc)
    return -timedelta(minutes=FUTURE_SKEW_MIN) <= age <= timedelta(hours=SOURCE_MAX_AGE_H)


def _active(row, t_now: datetime | None) -> bool:
    """직전 값이 기사 근거의 긴장 이상(state ≥ 1)이고 판정 72시간 안인가."""
    if not isinstance(row, dict) or type(row.get("state")) is not int or row["state"] < 1:
        return False
    at = _dt(row.get("judged_at"))
    if at is None or t_now is None:
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return t_now - at <= timedelta(hours=SOURCE_MAX_AGE_H)


def ingest(run: dict, doc: dict | None, now: str) -> dict:
    return ingest_accepted(run, doc, now)[0]


def ingest_accepted(run: dict, doc: dict | None, now: str) -> tuple[dict, list[str]]:
    """(새 theaters.json, 이번 입력에서 받아들인 전역 키). ingest 와 같고, 관측 수를 시각 비교 없이 센다(background.py).
    run.linkage.theaters → state/theaters.json. 연 원문 없는 전역·모르는 값은 그것만 버리고, 안 낸 전역은 직전 값을 잇는다.
    72시간이 지났거나 published_at 이 없는 출처는 버린다 — 출처가 0이면 그 전역은 이번 회차 미관측(직전 값을 잇는다)."""
    import diff_state as d
    doc = doc if isinstance(doc, dict) else {}
    run = run if isinstance(run, dict) else {}
    t_now = _dt(now)
    rows = dict(doc.get("theaters")) if isinstance(doc.get("theaters"), dict) else {}
    raw = (run.get("linkage") or {}).get("theaters") if isinstance(run.get("linkage"), dict) else None
    opened, accepted = d._opened(run), []
    cov = run.get("coverage") if isinstance(run.get("coverage"), dict) else {}

    def searched(t: str) -> bool:  # 평온 확인은 그 전역을 따로 검색한 기록이 있어야 한다(검색 1건으로 여러 전역을 평온 처리하지 않는다)
        qs = cov.get(f"linkage:theaters:{t}")
        return isinstance(qs, list) and any(isinstance(q, str) and q.strip() for q in qs)

    for t in THEATERS if isinstance(raw, dict) else ():
        row = raw.get(t)
        if not isinstance(row, dict) or type(row.get("state")) is not int or not 0 <= row["state"] <= 3:
            continue
        good = [s for s in row.get("sources") or []
                if isinstance(s, dict) and s.get("url") in opened and _fresh(s, t_now)]
        arts = [s for s in good if not is_feed(s.get("url"))]
        own = {_feed_key(u) for u in QUIET_FEEDS[t]}
        feeds = list({_feed_key(s["url"]): s for s in good if _feed_key(s.get("url")) in own}.values())
        quiet = (not arts and row["state"] == 0 and searched(t)
                 and len(feeds) >= min(QUIET_MIN_FEEDS, len(QUIET_FEEDS[t])))
        if quiet and _active(rows.get(t), t_now):
            continue  # 보도 공백은 해소 근거가 아니다 — 아직 신선한 긴장·위기·교전을 피드 확인으로 평온으로 덮지 않는다(직전 값을 잇는다)
        if arts:
            srcs = [{f: s[f] for f in ("name", "tier", "url", "published_at") if s.get(f)} for s in arts][:SOURCES_MAX]
        elif quiet:
            srcs = [{**{f: s[f] for f in ("name", "url", "published_at") if s.get(f)}, "kind": "feed"} for s in feeds][:SOURCES_MAX]
        else:  # state 0 도 근거가 있어야 관측이다(기사 원문, 또는 위 평온 확인 조건)
            continue
        summary = row.get("summary").strip()[:SUMMARY_LEN] if isinstance(row.get("summary"), str) else ""
        if quiet and not summary:
            summary = f"정본 피드 {len(feeds)}곳에 72시간 안 관련 기사 없음"
        channels = [] if quiet else [c for c in CHANNELS if c in (row.get("channels") or [])]  # 피드 확인은 연결을 관측하지 않는다
        state, extra = row["state"], {}
        prev = rows.get(t)
        resolution = row.get("resolution").strip()[:SUMMARY_LEN] if isinstance(row.get("resolution"), str) else ""
        if _active(prev, t_now) and state < prev["state"]:
            if resolution:  # 하향은 해소 사실(전면 휴전 발효·철수·훈련 종료)을 적었을 때만
                extra = {"resolution": resolution}
            else:  # 국지 휴전·외교 행사 기사로 진행 중인 위기·교전을 낮추지 않는다(11/08 우크라이나 2→1 검수 결함)
                state, extra = prev["state"], {"downgrade_held": state}
        rows[t] = {"state": state, "channels": channels,
                   "sources": srcs, "summary": summary, "judged_at": now, **extra}
        accepted.append(t)
    got = bool(accepted)
    return {"schema_version": 2, "judged_at": now if got else doc.get("judged_at"), "theaters": rows}, accepted


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    last_good, weights_version = None, None
    try:
        last_good = _load(STATE / "ews.json").get("last_good")
    except Exception:  # noqa: BLE001 — 직전 값이 없거나 깨져도 이번 회차는 계산한다
        pass
    try:
        weights = _load(WEIGHTS)
        weights_version = weights["version"]
        theaters_path = STATE / "theaters.json"
        doc = _load(theaters_path) if theaters_path.exists() else {}
        if len(argv) > 1:  # 11/05 전 지시문(`ews.py run.json`)이 남아 있어도 theaters.json 을 쓰지 않는다 — 배경 루틴 소유
            print("::warning::ews run.json 인자는 무시한다 — state/theaters.json 은 scripts/background.py 가 쓴다")
        out = compute(_load(STATE / "latest.json"), doc, weights, now, last_good)
    except Exception as e:  # noqa: BLE001 — 회차를 막지 않는다
        err = f"{type(e).__name__}: {e}"
        out = error_doc(err, now, weights_version, last_good)
        print(f"::warning::ews {err[:ERROR_LEN]}")
    try:
        _write(STATE / "ews.json", out)
    except OSError as e:
        print(f"::warning::ews {str(e)[:ERROR_LEN]}")
    print(json.dumps({k: out.get(k) for k in ("status", "ews", "band", "d", "e")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

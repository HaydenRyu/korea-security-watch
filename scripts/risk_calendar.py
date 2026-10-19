"""다가오는 도발 위험 일정 — 과거 발사 기록으로 「이 시기에 실제로 잦았나」를 센다 → data/risk_calendar.json

  python3 scripts/risk_calendar.py          # 공개 DB 2곳을 받아 stats·tempo 를 다시 쓴다(키 불필요)
  python3 scripts/risk_calendar.py --dry    # 쓰지 않고 요약만

정본은 data/risk_calendar.json 한 파일이다.
  anchors  사람이 고치는 부분 — 훈련 연도별 날짜·기념일·선거·정상회담과 출처(원문을 확인한 값만)
  stats    이 스크립트가 쓰는 부분 — 기준일 ±WINDOW 일 안에 발사가 있던 해, 평소라면 기대되는 해 수
  tempo    이 스크립트가 쓰는 부분 — 최근 30일 발사한 날 수 vs 평소 30일, 연도별 종류별(미사일·핵·기타) 날 수
  season   이 스크립트가 쓰는 부분 — 달별 발사한 날 수 합·발사가 있던 해 수 vs 달 길이 비례 기대치
  relaunch 이 스크립트가 쓰는 부분 — 발사한 날 뒤 7·14·30일 안에 또 발사한 비율(과거 빈도), 마지막 발사일
  signal_check 이 스크립트가 쓰는 부분 — 기사로 뽑은 임박 신호(state/signals.json)를 종류별로 「본 뒤 14일 안 발사가 있었나」

발사 = CSIS Beyond Parallel 도발 DB 의 Missile·Nuclear Provocation 날짜 ∪ CNS 미사일 시험 DB(2024-11 까지, UTC → KST).
같은 날 여러 발은 1일. 풍선·포격 같은 Other Provocation 은 세지 않는다(종류가 섞여 해마다 기준이 흔들린다).
평소라면 = 그해 아무 날이나 같은 길이 기간을 골랐을 때 발사가 들어 있었을 확률을 연도별로 더한 값.
판단 = 연도별 그 확률로 「발사 있던 해 수」의 정확한 분포(Poisson-binomial)를 만들어, 실제 값이 우연으로 나올
가능성이 한쪽 10% 미만이면 more/less, 아니면 usual. 이 가능성(p_ge·p_le)은 판단용이고 화면에 확률로 싣지 않는다.
우리는 「전쟁 확률」을 만들지 않는다 — 이 파일은 「언제 더 자주 볼지」를 정하는 데만 쓴다.
"""
from __future__ import annotations

import html
import io
import json
import re
import sys
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "data" / "risk_calendar.json"
CSIS = "https://beyondparallel.csis.org/database-north-korean-provocations/"
# nti.org 는 자동 수집에 403 — 같은 파일의 Wayback 원본(id_ = 감싸기 없는 파일, 2026-09-02 수집).
# CNS 는 2024-11 에서 갱신이 멈췄다 → 받은 날짜는 doc["cns"] 에 남기고, 못 받으면 그것을 쓴다
CNS = ("https://web.archive.org/web/20260902203331id_/"
       "https://www.nti.org/wp-content/uploads/2021/10/north_korea_missile_test_database.xlsx")
UA = {"User-Agent": "Mozilla/5.0 (jis-risk-calendar; github.com/HaydenRyu/korea-security-watch)"}
KST = timezone(timedelta(hours=9))


def get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read()


def csis_events(raw: str) -> list[dict]:
    """CSIS 표(wpDataTable) 행 → {date, kind, event, src}. 표 구조가 바뀌면 0건이라 호출부가 멈춘다."""
    out = []
    for row in re.findall(r'<tr id="table_\d+_row_\d+"[^>]*>(.*?)</tr>', raw, re.S):
        td = [html.unescape(re.sub(r"<[^>]+>", "", x)).strip() for x in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(td) < 5 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", td[0]):
            continue
        src = re.search(r'href="([^"]+)"', row)
        out.append({"date": td[0], "kind": td[1], "event": td[2], "src": html.unescape(src.group(1)) if src else None})
    return out


def cns_dates(raw: bytes) -> dict[str, str | None]:
    """CNS xlsx 첫 시트 → {KST 날짜: 첫 출처 URL}. 날짜 칸(B)은 UTC 일련번호, 발사 시각(D)이 있으면 KST 로 넘긴다."""
    z = zipfile.ZipFile(io.BytesIO(raw))
    ss = [''.join(re.findall(r"<t[^>]*>(.*?)</t>", s, re.S))
          for s in re.findall(r"<si>(.*?)</si>", z.read("xl/sharedStrings.xml").decode(), re.S)]
    out: dict[str, str | None] = {}
    for row in re.findall(r"<row [^>]*>(.*?)</row>", z.read("xl/worksheets/sheet1.xml").decode(), re.S)[1:]:
        cell = {}
        for ref, attrs, body in re.findall(r'<c r="([A-Z]+)\d+"([^>]*?)(?:/>|>(.*?)</c>)', row, re.S):
            v = re.search(r"<v>(.*?)</v>", body or "")
            if v:
                cell[ref] = ss[int(v.group(1))] if 't="s"' in attrs else v.group(1)
        try:
            day = float(cell["B"]) + (float(cell["D"]) if re.fullmatch(r"[\d.]+", cell.get("D", "")) else 0)
        except (KeyError, ValueError):
            continue
        utc = datetime(1899, 12, 30, tzinfo=timezone.utc) + timedelta(days=day)
        d = utc.astimezone(KST).date().isoformat() if cell.get("D") else utc.date().isoformat()
        src = re.search(r"https?://\S+", html.unescape(cell.get("S", "")))
        out.setdefault(d, src.group(0).rstrip(";,") if src else None)
    return out


def launch_days(csis: list[dict], cns: dict) -> dict[date, str | None]:
    days: dict[date, str | None] = {}
    for e in csis:
        if e["kind"] != "Other Provocation":
            days.setdefault(date.fromisoformat(e["date"]), e["src"])
    for d, s in cns.items():
        days.setdefault(date.fromisoformat(d), s)
    return days


def occurrences(a: dict, years: range) -> dict[int, tuple[date, date] | None]:
    """기준 기간(연도별). 기념일은 md, 그 밖은 dates{연도: [시작, 끝] | null(미실시·미확인)}."""
    if a.get("md"):
        return {y: (date.fromisoformat(f"{y}-{a['md']}"),) * 2 for y in years}
    out = {}
    for y, v in (a.get("dates") or {}).items():
        if int(y) in years:
            out[int(y)] = (date.fromisoformat(v[0]), date.fromisoformat(v[1] or v[0])) if v else None
    return out


def chance(year: int, length: int, days: set[date]) -> float:
    """그해 아무 날을 시작으로 length 일 기간을 골랐을 때 발사가 들어 있을 확률."""
    d0, n, hit = date(year, 1, 1), 0, 0
    for k in range(365 - length + 1):
        a, b = d0 + timedelta(k), d0 + timedelta(k + length - 1)
        n += 1
        hit += any(a <= d <= b for d in days)
    return hit / n


VERDICT_P = 0.1  # 한쪽 꼬리 10% — 「우연으로 보기 어려운 정도」


def poisson_binomial(ps: list[float]) -> list[float]:
    """독립 시행 확률 ps → 성공 횟수 k 의 정확한 확률 [P(X=0), …, P(X=n)]."""
    dist = [1.0]
    for p in ps:
        nxt = [0.0] * (len(dist) + 1)
        for k, q in enumerate(dist):
            nxt[k] += q * (1 - p)
            nxt[k + 1] += q * p
        dist = nxt
    return dist


def verdict(ps: list[float], hit: int, cut: float = VERDICT_P) -> tuple[str, float, float]:
    """(more|less|usual, P(X≥hit), P(X≤hit)) — 한쪽 꼬리가 cut 미만이면 평소와 다르다고 본다."""
    dist = poisson_binomial(ps)
    ge, le = sum(dist[hit:]), sum(dist[:hit + 1])
    return ("more" if ge < cut else "less" if le < cut else "usual"), ge, le


def anchor_stats(a: dict, days: dict[date, str | None], years: range, w: int) -> dict | None:
    occ = occurrences(a, years)
    if not occ:
        return None
    by_year = {y: {d for d in days if d.year == y} for y in years}
    rows, ps = [], []
    for y in sorted(occ):
        if occ[y] is None:
            rows.append({"year": y, "held": False})
            continue
        lo, hi = occ[y][0] - timedelta(w), occ[y][1] + timedelta(w)
        hits = sorted(d for d in days if lo <= d <= hi)
        ps.append(chance(y, (hi - lo).days + 1, by_year[y]))
        rows.append({"year": y, "n": len(hits), **({"first": hits[0].isoformat(), "src": days[hits[0]]} if hits else {})})
    counted = [r for r in rows if r.get("held", True)]
    hit = sum(1 for r in counted if r["n"])
    v, ge, le = verdict(ps, hit)
    return {"years": len(counted), "hit": hit, "expected": round(sum(ps), 1), "verdict": v,
            "p_ge": round(ge, 3), "p_le": round(le, 3), "rows": rows}


def month_len(y: int, m: int) -> int:
    return ((date(y + m // 12, m % 12 + 1, 1)) - date(y, m, 1)).days


def season(days: set[date], years: range) -> list[dict]:
    """달별 {발사한 날 수 합, 발사가 있던 해 수, 평소(그해 발사한 날 수를 달 길이로 나눈 기대치의 합)}."""
    out = []
    for m in range(1, 13):
        n = sum(1 for d in days if d.year in years and d.month == m)
        yrs = sum(1 for y in years if any(d.year == y and d.month == m for d in days))
        exp = sum(sum(1 for d in days if d.year == y) * month_len(y, m) / (date(y + 1, 1, 1) - date(y, 1, 1)).days
                  for y in years)
        out.append({"m": m, "days": n, "years": yrs, "expected": round(exp, 1)})
    return out


def relaunch(days: set[date], years: range, today: date, ks: tuple[int, ...] = (7, 14, 30)) -> dict:
    """기간 안 발사한 날마다 「k 일 안에 또 발사했나」 — 과거 빈도다(예측 확률 아님).
    다음 발사는 기간 밖(이후)도 본다. 아직 k 일이 다 지나지 않은 날은 분모에서 뺀다."""
    srt = sorted(d for d in days if d <= today)
    base = [d for d in srt if d.year in years]
    nxt = {a: b for a, b in zip(srt, srt[1:])}
    rate = {}
    for k in ks:
        pool = [d for d in base if d in nxt or (today - d).days > k]
        hit = sum(1 for d in pool if d in nxt and (nxt[d] - d).days <= k)
        rate[str(k)] = {"n": len(pool), "hit": hit, "pct": round(100 * hit / len(pool)) if pool else None}
    last = srt[-1] if srt else None
    return {"rate": rate, "last": last.isoformat() if last else None, "since": (today - last).days if last else None,
            "basis": f"발사한 날 {len(base)}일({years.start}~{years.stop - 1}) 각각의 다음 발사까지 간격"}


KIND_RANK = {"nuclear": 0, "missile": 1, "other": 2}


# CSIS 사건명(영어) → 화면에 쓰는 우리말. 앞에서부터 처음 맞는 것 — 구체적인 것을 일반적인 것보다 먼저 둔다.
# 못 맞추면 None(화면은 원문 event 를 그대로 보인다)
EVENT_KO = (
    (r"Submarine[- ]Launched Ballistic", "잠수함 발사 탄도미사일"),
    (r"Submarine[- ]Launched Cruise", "잠수함 발사 순항미사일"),
    (r"Intercontinental|ICBM", "대륙간 탄도미사일 발사"),
    (r"Intermediate[- ]range", "중거리 탄도미사일 발사"),
    (r"Short[- ]range Ballistic", "단거리 탄도미사일 발사"),
    (r"Hypersonic", "극초음속 미사일 발사"),
    (r"Multiple Launch Rocket|MLRS", "다연장로켓 시험"),
    (r"Cruise Missile", "순항미사일 발사"),
    (r"Satellite", "위성 발사"),
    (r"Nuclear Test", "핵실험"),
    (r"Ballistic Missile", "탄도미사일 발사"),
    (r"Missile", "미사일 발사"),
)


def event_ko(event: str | None) -> str | None:
    for pat, ko in EVENT_KO:
        if event and re.search(pat, event, re.I):
            return ko
    return None


def tempo(csis: list[dict], today: date, years: range, base_years: range | None = None) -> dict:
    """최근 30일 발사한 날 수 — CSIS 만 쓴다(CNS 는 2024-11 에서 끝나 최근과 비교 기준이 달라진다).
    per_year = 연도별 종류별 날 수. 한 날에 종류가 겹치면 핵 > 미사일 > 기타 하나로(누적 막대 합 = 도발한 날 수).
    launch = 미사일+핵(위험 판단에 쓰는 값) · other 는 표시만 한다.
    평소(avg30)는 base_years(config.tempo_baseline, 최근 10년) — 지금 빈도는 지금 체제와 비교한다(2006~13 저빈도가 기준을 낮춰 배율을 부풀린다)."""
    base_years = base_years or years
    kind = {}
    for e in csis:
        k = {"Nuclear Provocation": "nuclear", "Missile Provocation": "missile"}.get(e["kind"], "other")
        x = date.fromisoformat(e["date"])
        if KIND_RANK[k] < KIND_RANK.get(kind.get(x), 9):
            kind[x] = k
    d = {}
    for e in csis:
        if e["kind"] != "Other Provocation":
            d.setdefault(date.fromisoformat(e["date"]), e)
    last = sorted((x for x in d if today - timedelta(30) <= x < today), reverse=True)
    base = sum(1 for x in d if x.year in base_years) / (len(base_years) * 365) * 30
    per_year = {}
    for y in range(years.start, today.year + 1):
        c = {k: sum(1 for x, v in kind.items() if x.year == y and v == k) for k in KIND_RANK}
        per_year[str(y)] = {**c, "launch": c["missile"] + c["nuclear"]}
    return {"last30": len(last), "avg30": round(base, 1), "ratio": round(len(last) / base, 1) if base else None,
            "recent": [{"date": x.isoformat(), "event": d[x]["event"], "event_ko": event_ko(d[x]["event"]), "src": d[x]["src"]}
                       for x in last],
            "per_year": per_year, "basis": f"CSIS 미사일·핵 도발 날짜, 평소 = {base_years.start}~{base_years.stop - 1} 평균"}


SIGNAL_WINDOW_D = 14  # 신호를 처음 본 날부터 이 안에 발사가 있었으면 적중
SIGNAL_MIN_N = 5      # 종류별 n 이 이보다 적으면 화면은 「집계 중」


def signal_check(sig_doc: dict | None, launch: set[date], today: date,
                 data_last: date | None = None, base_years: range | None = None) -> dict:
    """만료된 신호마다 first_seen 부터 14일 안에 미사일·핵 발사가 있었는지 → 종류별 {n, hit}.
    14일이 다 지나지 않은 신호는 세지 않는다(미확정). 적중률이 아니라 건수다 — n 이 작으면 화면이 확률로 읽지 않게 「집계 중」.
    data_last(CSIS 최신 기록일) 뒤로 14일이 걸치는 신호도 세지 않는다 — DB 가 늦게 올라오면 실제 발사를 miss 로 셀 수 있다.
    baseline = base_years 에서 아무 날이나 시작한 14일 구간에 발사가 들어 있던 비율(평소 기준선, 같은 식으로 비교)."""
    sig_doc = sig_doc if isinstance(sig_doc, dict) else {}
    done = [x for x in sig_doc.get("expired") or [] if isinstance(x, dict)]
    done += [x for x in sig_doc.get("active") or [] if isinstance(x, dict) and str(x.get("expires", "9")) < today.isoformat()]
    kinds: dict[str, dict] = {}
    for x in done:
        try:
            a = date.fromisoformat(str(x["first_seen"])[:10])
        except (KeyError, ValueError):
            continue
        if (today - a).days < SIGNAL_WINDOW_D or not isinstance(x.get("kind"), str):
            continue
        if data_last and a + timedelta(SIGNAL_WINDOW_D) > data_last:
            continue
        k = kinds.setdefault(x["kind"], {"n": 0, "hit": 0})
        k["n"] += 1
        k["hit"] += any(a <= d <= a + timedelta(SIGNAL_WINDOW_D) for d in launch)
    baseline = None
    if base_years:
        ys = [y for y in base_years]
        baseline = round(sum(chance(y, SIGNAL_WINDOW_D + 1, {d for d in launch if d.year == y}) for y in ys) / len(ys), 2)
    return {"window_days": SIGNAL_WINDOW_D, "min_n": SIGNAL_MIN_N, "kinds": kinds, "baseline": baseline,
            "basis": f"신호를 처음 본 날부터 {SIGNAL_WINDOW_D}일 안 CSIS 미사일·핵 발사 여부 · 기간이 다 지난 신호만"}


def main() -> int:
    doc = json.loads(DOC.read_text())
    cfg = doc["config"]
    today = datetime.now(KST).date()
    years = range(cfg["lookback"][0], cfg["lookback"][1] + 1)
    csis = csis_events(get(CSIS).decode("utf-8", "replace"))
    if len(csis) < 300:  # 2026-10 기준 508행 — 표 구조가 바뀌었으면 덮지 않는다
        print(f"CSIS 표 {len(csis)}행 — 구조 변경 의심, 기존 파일 유지")
        return 1
    try:
        cns = cns_dates(get(CNS))
        doc["cns"] = cns
    except (OSError, zipfile.BadZipFile, KeyError) as e:
        cns = doc.get("cns") or {}
        print(f"CNS 받기 실패 — 저장본 {len(cns)}일 사용: {e}")
    days = launch_days(csis, cns)
    doc["stats"] = {a["id"]: s for a in doc["anchors"] if (s := anchor_stats(a, days, years, cfg["window_days"]))}
    tb = cfg.get("tempo_baseline")
    doc["tempo"] = tempo(csis, today, years, range(tb[0], tb[1] + 1) if tb else None)
    doc["season"] = {"months": season(set(days), years), "years": len(years),
                     "basis": f"발사한 날(CSIS ∪ CNS) {years.start}~{years.stop - 1} · 평소 = 그해 발사한 날 수 × 달 길이/1년"}
    doc["relaunch"] = relaunch(set(days), years, today)
    sp = ROOT / "state" / "signals.json"
    doc["signal_check"] = signal_check(json.loads(sp.read_text()) if sp.exists() else None, set(days), today,
                                       date.fromisoformat(max(e["date"] for e in csis)), range(tb[0], tb[1] + 1) if tb else years)
    doc["data"] = {"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "csis_rows": len(csis), "csis_last": max(e["date"] for e in csis),
                   "cns_days": len(cns), "cns_last": max(cns), "launch_days": len(days)}
    summary = {k: f'{v["hit"]}/{v["years"]} vs {v["expected"]} {v["verdict"]} (≥{v["p_ge"]} ≤{v["p_le"]})'
               for k, v in doc["stats"].items()}
    top = sorted(doc["season"]["months"], key=lambda r: -r["days"])[:3]
    print(json.dumps({"tempo": {k: doc["tempo"][k] for k in ("last30", "avg30", "ratio")}, "stats": summary,
                      "season_top3": top, "relaunch": doc["relaunch"], "data": doc["data"]}, ensure_ascii=False, indent=1))
    if "--dry" not in sys.argv:
        DOC.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

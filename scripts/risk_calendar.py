"""다가오는 도발 위험 일정 — 과거 발사 기록으로 「이 시기에 실제로 잦았나」를 센다 → data/risk_calendar.json

  python3 scripts/risk_calendar.py          # 공개 DB 2곳을 받아 stats·tempo 를 다시 쓴다(키 불필요)
  python3 scripts/risk_calendar.py --dry    # 쓰지 않고 요약만

정본은 data/risk_calendar.json 한 파일이다.
  anchors  사람이 고치는 부분 — 훈련 연도별 날짜·기념일·선거·정상회담과 출처(원문을 확인한 값만)
  stats    이 스크립트가 쓰는 부분 — 기준일 ±WINDOW 일 안에 발사가 있던 해, 평소라면 기대되는 해 수
  tempo    이 스크립트가 쓰는 부분 — 최근 30일 발사한 날 수 vs 평소 30일

발사 = CSIS Beyond Parallel 도발 DB 의 Missile·Nuclear Provocation 날짜 ∪ CNS 미사일 시험 DB(2024-11 까지, UTC → KST).
같은 날 여러 발은 1일. 풍선·포격 같은 Other Provocation 은 세지 않는다(종류가 섞여 해마다 기준이 흔들린다).
평소라면 = 그해 아무 날이나 같은 길이 기간을 골랐을 때 발사가 들어 있었을 확률을 연도별로 더한 값.
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


def anchor_stats(a: dict, days: dict[date, str | None], years: range, w: int) -> dict | None:
    occ = occurrences(a, years)
    if not occ:
        return None
    by_year = {y: {d for d in days if d.year == y} for y in years}
    rows, exp = [], 0.0
    for y in sorted(occ):
        if occ[y] is None:
            rows.append({"year": y, "held": False})
            continue
        lo, hi = occ[y][0] - timedelta(w), occ[y][1] + timedelta(w)
        hits = sorted(d for d in days if lo <= d <= hi)
        exp += chance(y, (hi - lo).days + 1, by_year[y])
        rows.append({"year": y, "n": len(hits), **({"first": hits[0].isoformat(), "src": days[hits[0]]} if hits else {})})
    counted = [r for r in rows if r.get("held", True)]
    hit = sum(1 for r in counted if r["n"])
    diff = hit - exp
    return {"years": len(counted), "hit": hit, "expected": round(exp, 1),
            "verdict": "more" if diff >= 2 else "less" if diff <= -2 else "usual", "rows": rows}


def tempo(csis: list[dict], today: date, years: range) -> dict:
    """최근 30일 발사한 날 수 — CSIS 만 쓴다(CNS 는 2024-11 에서 끝나 최근과 비교 기준이 달라진다)."""
    d = {}
    for e in csis:
        if e["kind"] != "Other Provocation":
            d.setdefault(date.fromisoformat(e["date"]), e)
    last = sorted((x for x in d if today - timedelta(30) <= x < today), reverse=True)
    base = sum(1 for x in d if x.year in years) / (len(years) * 365) * 30
    per_year = {str(y): sum(1 for x in d if x.year == y) for y in range(years.start, today.year + 1)}
    return {"last30": len(last), "avg30": round(base, 1), "ratio": round(len(last) / base, 1) if base else None,
            "recent": [{"date": x.isoformat(), "event": d[x]["event"], "src": d[x]["src"]} for x in last],
            "per_year": per_year, "basis": f"CSIS 미사일·핵 도발 날짜, 평소 = {years.start}~{years.stop - 1} 평균"}


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
    doc["tempo"] = tempo(csis, today, years)
    doc["data"] = {"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "csis_rows": len(csis), "csis_last": max(e["date"] for e in csis),
                   "cns_days": len(cns), "cns_last": max(cns), "launch_days": len(days)}
    summary = {k: f'{v["hit"]}/{v["years"]} vs {v["expected"]} {v["verdict"]}' for k, v in doc["stats"].items()}
    print(json.dumps({"tempo": {k: doc["tempo"][k] for k in ("last30", "avg30", "ratio")}, "stats": summary,
                      "data": doc["data"]}, ensure_ascii=False, indent=1))
    if "--dry" not in sys.argv:
        DOC.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

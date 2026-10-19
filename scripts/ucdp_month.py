"""세계 분쟁 현황 — UCDP Candidate GED 월간 CSV(키 불필요, CC BY 4.0)로 지난달 국가별 사망(best 합)을 센다.

사용: python3 scripts/ucdp_month.py [--force]
  - https://ucdp.uu.se/downloads/ 에서 candidateged/GEDEvent_v<YY>_0_<M>.csv 중 가장 최신 판과 그 전 판을 고른다
  - 판마다 그 달(date_start 기준) 사건만 센다. 우크라이나·러시아는 한 행(러-우 전쟁)으로 합친다
  - data/conflicts.json 을 쓴다. 이미 같은 판이면 받지 않는다(--force 로 다시 계산)
  - 실패하면 이전 파일을 그대로 두고 rc 1 — 화면은 「n월 기준」으로 묵은 정도를 보인다
상태 라벨(악화·개선)은 만들지 않는다. 숫자로 문턱을 정하면 임의값이 되므로 두 달 값을 나란히 보여 주기만 한다.
GitHub Actions weekly.yml calendar 잡이 매달 21~28일에 부른다(Candidate 판은 월말 뒤 약 3주에 나온다).
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "conflicts.json"
PAGE = "https://ucdp.uu.se/downloads/"
_LINK = re.compile(r"https://ucdp\.uu\.se/downloads/candidateged/GEDEvent_v(\d{2})_0_(\d{1,2})\.csv")
ROWS_MAX = 15
MERGE = {"Ukraine": "russia_ukraine", "Russia (Soviet Union)": "russia_ukraine"}
NAME_KO = {
    "russia_ukraine": "러-우 전쟁", "Yemen (North Yemen)": "예멘", "Nigeria": "나이지리아", "Pakistan": "파키스탄",
    "Ethiopia": "에티오피아", "Sudan": "수단", "South Sudan": "남수단", "Colombia": "콜롬비아", "Mexico": "멕시코",
    "DR Congo (Zaire)": "콩고민주공화국", "Burkina Faso": "부르키나파소", "Somalia": "소말리아", "Myanmar (Burma)": "미얀마",
    "Israel": "이스라엘·가자", "Mali": "말리", "Afghanistan": "아프가니스탄", "Syria": "시리아", "Iraq": "이라크",
    "Iran": "이란", "Lebanon": "레바논", "Niger": "니제르", "Cameroon": "카메룬", "Mozambique": "모잠비크",
    "India": "인도", "Philippines": "필리핀", "Brazil": "브라질", "Haiti": "아이티", "Ecuador": "에콰도르",
    "Central African Republic": "중앙아프리카공화국", "Chad": "차드", "Libya": "리비아", "Kenya": "케냐",
    "Bangladesh": "방글라데시", "Thailand": "태국", "Cambodia (Kampuchea)": "캄보디아", "Benin": "베냉",
    "Togo": "토고", "Uganda": "우간다", "Burundi": "부룬디", "Egypt": "이집트", "Algeria": "알제리", "Turkey": "튀르키예",
    "Saudi Arabia": "사우디아라비아", "Venezuela": "베네수엘라", "Peru": "페루", "Indonesia": "인도네시아",
}
REGION_KO = {"Africa": "아프리카", "Americas": "아메리카", "Asia": "아시아", "Europe": "유럽", "Middle East": "중동"}


def list_versions(html: str) -> list[tuple[int, int, str]]:
    """다운로드 페이지 → [(연도 2자리, 월, csv 주소)] 오래된 판부터."""
    found = {(int(y), int(m)): u for u, y, m in ((x.group(0), x.group(1), x.group(2)) for x in _LINK.finditer(html))}
    return [(y, m, found[(y, m)]) for y, m in sorted(found) if 1 <= m <= 12]


def month_of(yy: int, m: int) -> str:
    return f"20{yy:02d}-{m:02d}"


def month_totals(text: str, month: str) -> dict[str, dict]:
    """CSV 본문 → {키: {best, events, name_en, region}} — date_start 가 그 달인 사건만."""
    out: dict[str, dict] = {}
    for r in csv.DictReader(io.StringIO(text)):
        if not (r.get("date_start") or "").startswith(month):
            continue
        c = (r.get("country") or "").strip()
        if not c:
            continue
        k = MERGE.get(c, c)
        try:
            best = int(float(r.get("best") or 0))
        except ValueError:
            best = 0
        row = out.setdefault(k, {"best": 0, "events": 0, "name_en": "Ukraine + Russia" if k == "russia_ukraine" else c,
                                 "region": "Europe" if k == "russia_ukraine" else (r.get("region") or "").strip()})
        row["best"] += best
        row["events"] += 1
    return out


def build(cur: dict, prev: dict | None, month: str, prev_month: str | None, version: str, url: str, now: str) -> dict:
    keys = sorted(cur, key=lambda k: -cur[k]["best"])[:ROWS_MAX]
    rows = []
    for k in keys:
        c, p = cur[k], (prev or {}).get(k)
        rows.append({"key": k, "name": NAME_KO.get(k, c["name_en"]), "name_en": c["name_en"],
                     "region": REGION_KO.get(c["region"], c["region"]), "best": c["best"], "events": c["events"],
                     "prev_best": p["best"] if p else (0 if prev is not None else None)})
    return {"version": version, "month": month, "prev_month": prev_month, "fetched_at": now, "source_url": url,
            "page": PAGE, "license": "CC BY 4.0 — UCDP Candidate Events Dataset (Uppsala Conflict Data Program)",
            "basis": "국가별 사건의 best 사망 추정 합(잠정치) · 사건 시작일 기준 월 · 우크라이나·러시아 영토 사건은 한 행",
            "rows": rows}


def _get(url: str, timeout: int = 90) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "korea-security-watch (github.com/HaydenRyu/korea-security-watch)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def main(argv: list[str]) -> int:
    force = "--force" in argv
    try:
        vers = list_versions(_get(PAGE, 60))
        if not vers:
            raise RuntimeError("다운로드 페이지에서 Candidate 월간 CSV 를 찾지 못했다")
        yy, m, url = vers[-1]
        version = f"v{yy:02d}_0_{m}"
        old = json.loads(OUT.read_text()) if OUT.exists() else {}
        if old.get("version") == version and not force:
            print(json.dumps({"unchanged": version}))
            return 0
        month = month_of(yy, m)
        cur = month_totals(_get(url), month)
        prev, prev_month = None, None
        if len(vers) >= 2:
            py, pm, purl = vers[-2]
            prev_month = month_of(py, pm)
            prev = month_totals(_get(purl), prev_month)
        if not cur:
            raise RuntimeError(f"{version}: {month} 사건이 0건")
        doc = build(cur, prev, month, prev_month, version, url, datetime.now(timezone.utc).isoformat(timespec="seconds"))
        OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
        print(json.dumps({"version": version, "month": month, "rows": len(doc["rows"])}, ensure_ascii=False))
        return 0
    except Exception as e:  # noqa: BLE001 — 실패하면 이전 값을 둔다
        print(json.dumps({"error": str(e)[:200]}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

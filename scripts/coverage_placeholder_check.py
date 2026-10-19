#!/usr/bin/env python3
"""감시 루틴 coverage 의 자리표시 검색어 계수 — 읽기 전용(파일·git 을 바꾸지 않는다).

배경: 2026-10-19 회차들이 coverage 에 「북한 border_clash 동향」처럼 항목 키를 끼운 문구를 적었고,
그 회차들은 해안 지뢰 보도를 놓쳤다. monitor.md 1단계에 「실제 검색어만」 규칙을 넣은 뒤
게이트(diff_state.validate 차단)를 만들지 말지 정하려고 하루 관찰한다(2026-10-20 17:00 KST 판정).

  python3 scripts/coverage_placeholder_check.py --until 2026-10-20T17:00+09:00 -n 24

watch 커밋(`git log --grep=^watch -- state/latest.json`) 마다 그 시점 state/latest.json 의 coverage 를 읽어
자리표시 검색어(항목 키 문자열이 들어간 검색어)를 센다. rc 0 = 계수 완료(자리표시 유무와 무관), 2 = 판정 불가(커밋 0건).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from diff_state import ITEMS  # noqa: E402  — 항목 키 정본

# 밑줄 있는 키(border_clash 등)는 어디 들어가도 자리표시다. 낱말 키(nuclear·logistics)는 영어 검색어에 정상으로 쓰이므로
# (「North Korea nuclear」는 실제 검색어일 수 있다) 한글 검색어 안에 영어 키가 끼었을 때만(「북한 nuclear 동향」) 자리표시로 본다.
_U = [k for k in ITEMS if "_" in k]
_W = [k for k in ITEMS if "_" not in k]
KEY_RE = re.compile(r"(?<![A-Za-z_])(" + "|".join(map(re.escape, sorted(_U, key=len, reverse=True))) + r")(?![A-Za-z_])")
TPL_RE = re.compile(r"^(?=.*[가-힣]).*(?<![A-Za-z_])(" + "|".join(map(re.escape, _W)) + r")(?![A-Za-z_])", re.I)


def placeholder_queries(cov: dict) -> dict[str, list[str]]:
    """항목별 자리표시 검색어 — 항목 키(border_clash 등)를 그대로 끼운 검색어. 사람이 검색창에 넣는 말이 아니다."""
    out: dict[str, list[str]] = {}
    for item, qs in (cov or {}).items():
        bad = [q for q in (qs if isinstance(qs, list) else [qs]) if isinstance(q, str) and (KEY_RE.search(q) or TPL_RE.search(q))]
        if bad:
            out[item] = bad
    return out


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True, capture_output=True, text=True).stdout


def runs(until: str, n: int) -> list[tuple[str, str]]:
    log = _git("log", f"--until={until}", f"-n{n}", "--format=%h %cI", "--grep=^watch", "--", "state/latest.json")
    return [tuple(line.split(" ", 1)) for line in log.splitlines() if line.strip()]


def check(h: str) -> dict:
    try:
        doc = json.loads(_git("show", f"{h}:state/latest.json"))
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        return {"commit": h, "error": f"{type(e).__name__}"}
    cov = doc.get("coverage") or {}
    bad = placeholder_queries(cov)
    total = sum(len(v) if isinstance(v, list) else 1 for v in cov.values())
    return {"commit": h, "checked_at": doc.get("checked_at"), "queries": total,
            "placeholder": sum(len(v) for v in bad.values()), "items": sorted(bad)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--until", default="now", help="이 시각까지의 watch 커밋(git --until 형식, 예 2026-10-20T17:00+09:00)")
    ap.add_argument("-n", type=int, default=24, help="최근 회차 수(기본 24)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rows = [dict(check(h), committed=t) for h, t in runs(a.until, a.n)]
    if not rows:
        print("watch 커밋 0건 — 판정 불가", file=sys.stderr)
        return 2
    ok = [r for r in rows if "error" not in r]
    hit = [r for r in ok if r["placeholder"]]
    summary = {"until": a.until, "runs": len(rows), "unreadable": len(rows) - len(ok),
               "runs_with_placeholder": len(hit), "placeholder_queries": sum(r["placeholder"] for r in ok),
               "queries": sum(r["queries"] for r in ok)}
    if a.json:
        print(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1))
        return 0
    for r in rows:
        if "error" in r:
            print(f"{r['commit']} {r['committed']}  판정 불가({r['error']})")
        else:
            mark = "자리표시" if r["placeholder"] else "정상    "
            print(f"{r['commit']} {r['checked_at']}  {mark} {r['placeholder']:>2}/{r['queries']:<2} {','.join(r['items'])}")
    print(f"\n최근 {summary['runs']}회(--until {a.until}) 중 자리표시 검색어가 있는 회차 {summary['runs_with_placeholder']}회"
          f" · 자리표시 {summary['placeholder_queries']}/{summary['queries']} 검색어 · 판정 불가 {summary['unreadable']}회")
    return 0


if __name__ == "__main__":
    sys.exit(main())

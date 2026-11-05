#!/usr/bin/env python3
"""배경 블록 회차(하루 3회 루틴 — prompts/background.md) → state/ 의 배경 소유 파일만 쓴다.

  python3 scripts/background.py bg_run.json           # 쓰기
  python3 scripts/background.py --check bg_run.json   # 쓰기 없이 블록별 예상 상태만 출력(회차 하한 재시도 판단용)

  쓰는 파일(이 스크립트만 쓴다 — 매시간 diff_state.py·ews.py 는 읽기만 한다):
    state/world.json      세계 안보 브리핑(diff_state.update_world)
    state/factors.json    배경 요인(diff_state.update_factors)
    state/linkage.json    연동 위기 — 대만 해협·한반도 연결(diff_state.update_linkage)
    state/theaters.json   조기경보 지수의 세계 5개 전역(ews.ingest)
    state/incidents.json  사건 기록(diff_state.update_incidents)
    state/background.json 블록 수행 기록 {at, opened, blocks{키: {status, last_ok, raw, kept, note?}}, error?}
    .bg_commit_msg        이번 회차 커밋 메시지(루틴이 git commit -F 로 그대로 쓴다)

  판정 규칙은 기존 함수를 그대로 쓴다(사본 없음): 각 블록 sources 는 opened 안 URL 이어야 하고, 걸러진 줄만 버린다.
  배경 회차는 items 가 없다. 완료 조건은 opened 1건 이상뿐이다 — 매시간 판정의 MIN_OPENED(3)·사이트 2곳 규칙은 여기 쓰지 않는다.
  opened 가 없거나 파일이 깨졌으면 블록 입력을 하나도 쓰지 않고(시간 경과 만료만 반영) 전 블록을 failed 로 적는다.
  diff_state 의 status=unverified 경로는 쓰지 않는다(그 경로는 블록 입력을 {} 로 바꾼다).

  블록 상태: 루틴이 bg_run.blocks 에 checked(찾아서 적음) · no_findings(찾았지만 해당 없음) · failed 를 낸다.
  키가 없으면 skipped. 코드가 자기 신고를 결과와 대조해 고친다(11/05 첫 실회차: theaters 5곳 중 2곳만 내고 checked,
  linkage 는 원문 0건인데 no_findings 였다):
    - no_findings 는 그 블록의 coverage 검색어 키(COVERAGE_KEYS)가 1개 이상 있을 때만 인정 — 없으면 failed(「검색 기록 없음」).
    - checked 인데 검사 뒤 남은 줄이 0이면 failed(「검사 뒤 남은 줄 0」) — 적었다고 했지만 반영된 것이 없다.
    - theaters 는 「해당 없음」이 없다(0 도 원문으로 적는다). 관측 전역이 0이면 failed, data/ews_weights.json
      min_observed_theaters(3) 미만이면 partial(코드만 매기는 상태, note = 빠진 전역 키). 관측된 전역은 그대로 쌓는다.
  last_ok = checked·no_findings 의 마지막 시각(partial·failed·skipped 는 올리지 않는다 — 다음 회차가 먼저 채운다).

  블록 하나의 형식 오류·예외는 그 블록만 failed(note = 오류 요약)로 적고 그 파일은 직전 내용을 둔다 — 나머지 블록·background.json·
  커밋 메시지는 그대로 만든다(11/05 리뷰: theaters.sources 가 숫자면 ews.ingest 예외로 회차 전체가 멈췄다).

  세계 브리핑 전환(1회성): latest.json 의 world 가 world.json 보다 새것이면(머지 전 옛 매시간 회차가 쓴 것) 이번 회차에 새 브리핑이
  없을 때 그것을 world.json 으로 옮긴다. latest.json 은 읽기만 한다 — 지우는 것은 매시간 diff_state 가 world.json 이 따라잡은 뒤에 한다.

  --check: 같은 판정을 하되 아무 파일도 쓰지 않는다. rc 0 = 전 블록 checked·no_findings · 3 = partial·failed·skipped 있음
  (출력 retry 에 블록, missing_theaters 에 빠진 전역) · 1 = 입력 무효.

  rc 0 = 정상(블록별 failed 가 있어도 0 — 상태는 출력의 blocks) · 1 = 입력 무효(opened 0건·깨진 JSON — 그래도 파일은 쓴다, 실패도 커밋해 기록으로 남긴다).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import diff_state as d  # noqa: E402
import ews  # noqa: E402

STATE = ROOT / "state"
COMMIT_MSG = ROOT / ".bg_commit_msg"
BLOCKS = d.BACKGROUND_BLOCKS
FILES = {"world": "world.json", "factors": "factors.json", "linkage": "linkage.json",
         "theaters": "theaters.json", "incidents": "incidents.json"}
OWNED = (*FILES.values(), "background.json")  # 배경 루틴이 git add 하는 파일 — prompts/background.md 와 같아야 한다
NOTE_LEN = 160
WEIGHTS = ROOT / "data" / "ews_weights.json"
PARTIAL = "partial"  # 코드만 매긴다(루틴 입력 어휘는 d.BLOCK_STATUSES) — 일부만 관측
STATUSES = (*d.BLOCK_STATUSES, PARTIAL, "skipped")  # background.json·커밋 메시지에 나오는 전체 어휘
# no_findings 를 인정하는 coverage 키 — prompts/background.md 가 요구하는 키 이름 그대로. 끝이 ":" 면 접두사.
COVERAGE_KEYS = {"world": ("world",), "factors": ("factor:",), "linkage": ("linkage:taiwan", "linkage:korea"),
                 "theaters": ("linkage:theaters", "linkage:theaters:"), "incidents": ("incidents",)}
MIN_OBSERVED_DEFAULT = 3  # ews_weights.json 을 못 읽을 때만(지수 쪽 기본과 같은 값)


def min_observed() -> int:
    try:
        v = json.loads(WEIGHTS.read_text(encoding="utf-8")).get("min_observed_theaters")
        return v if type(v) is int and v > 0 else MIN_OBSERVED_DEFAULT
    except Exception:  # noqa: BLE001
        return MIN_OBSERVED_DEFAULT


def searched(run, block: str) -> bool:
    """coverage 에 그 블록의 검색어 키가 있고 검색어가 1개 이상인가."""
    cov = run.get("coverage") if isinstance(run, dict) else None
    if not isinstance(cov, dict):
        return False
    for key, qs in cov.items():
        if not (isinstance(key, str) and isinstance(qs, list) and any(isinstance(q, str) and q.strip() for q in qs)):
            continue
        if any(key.startswith(p) if p.endswith(":") else key == p for p in COVERAGE_KEYS[block]):
            return True
    return False


def observed_theaters(run, now: str, doc=None) -> list[str]:
    """이번 회차 입력에서 받아들인 전역 키 — 직전 판(doc)과 함께 돌린다
    (직전 값을 이은 전역·평온 확인이 신선한 긴장을 못 덮어 버려진 전역은 이번 관측이 아니다)."""
    try:
        return ews.ingest_accepted(run, doc, now)[1]
    except Exception:  # noqa: BLE001
        return []


def invalid(run) -> str | None:
    """배경 회차 완료 조건 — opened 1건 이상(http URL). 어기면 사유, 통과면 None."""
    if not isinstance(run, dict):
        return "bg_run.json 최상위가 객체가 아니다"
    if not d._opened(run):
        return "opened 0건 — 원문을 하나도 열지 않았다"
    return None


def counts(run: dict, key: str, now: str, doc=None) -> tuple[int, int]:
    """(루틴이 낸 줄 수, 기존 검사 함수를 지난 줄 수)."""
    try:
        if key == "world":
            raw = run.get("world")
            w = d.clean_world(run)
            return (len(raw) if isinstance(raw, list) else 0), (len(w[0]) if w else 0)
        if key == "factors":
            raw = run.get("factors")
            return (len(raw) if isinstance(raw, dict) else 0), len(d.clean_factors(run))
        lk = run.get("linkage") if isinstance(run.get("linkage"), dict) else {}
        if key == "linkage":
            raw = int(isinstance(lk.get("taiwan"), dict)) + (len(lk["korea"]) if isinstance(lk.get("korea"), list) else 0)
            c = d.clean_linkage(run)
            return raw, int("taiwan" in c) + len(c["korea"])
        if key == "theaters":
            raw = lk.get("theaters")
            return (len(raw) if isinstance(raw, dict) else 0), len(observed_theaters(run, now, doc))
        if key == "incidents":
            raw = run.get("incidents")
            return (len(raw) if isinstance(raw, list) else 0), len(d.clean_incidents(run))
    except Exception:  # noqa: BLE001 — 진단 계수가 회차를 깨뜨리면 안 된다
        pass
    return 0, 0


ERR_LEN = 120


def _seed_world(world_doc, latest):
    """전환기 1회성 — latest.world 가 world.json 보다 새것이면 그 브리핑을 직전 값으로 쓴다(latest 는 읽기만)."""
    if not d.world_newer(latest, world_doc):
        return world_doc
    return {k: latest[k] for k in d.WORLD_KEYS if k in latest}


def _guarded(fn, prev):
    """(결과, 오류 요약|None). 예외가 나거나 기존 함수가 error 를 돌려주면 직전 내용을 그대로 둔다."""
    try:
        res = fn()
    except Exception as e:  # noqa: BLE001 — 블록 하나가 회차 전체를 멈추면 안 된다
        return prev, f"{type(e).__name__}: {e}"[:ERR_LEN]
    if isinstance(res, dict) and res.get("error"):
        return prev, f"error: {res['error']}"[:ERR_LEN]
    return res, None


def step(run, docs: dict, now: str) -> tuple[dict, dict]:
    """(새 파일 내용 {블록: doc|None}, background.json). docs = 직전 파일 내용({블록|"background"|"latest": doc|None}).
    블록 결과가 None(직전 파일이 없고 그 블록이 실패)이면 그 파일은 쓰지 않는다."""
    reason = invalid(run)
    use = run if reason is None else {}  # 무효 입력은 블록 입력 없이 시간 경과(만료)만
    world_prev = _seed_world(docs.get("world"), docs.get("latest"))
    calls = {"world": (lambda: d.update_world(use, world_prev, now), world_prev),
             "factors": (lambda: d.update_factors(use, docs.get("factors"), now), docs.get("factors")),
             "linkage": (lambda: d.update_linkage(use, docs.get("linkage"), now), docs.get("linkage")),
             "theaters": (lambda: ews.ingest(use, docs.get("theaters"), now), docs.get("theaters")),
             "incidents": (lambda: d.update_incidents(use, docs.get("incidents"), now), docs.get("incidents"))}
    out, errs = {}, {}
    for k, (fn, prev_doc) in calls.items():
        out[k], err = _guarded(fn, prev_doc)
        if err:
            errs[k], out[k] = err, None  # None = 파일을 쓰지 않는다(직전 내용 바이트 그대로)
    prev_bg = docs.get("background") if isinstance(docs.get("background"), dict) else {}
    prev = prev_bg.get("blocks") if isinstance(prev_bg.get("blocks"), dict) else {}
    notes = run.get("notes") if isinstance(run, dict) and isinstance(run.get("notes"), dict) else {}
    blocks = {}
    for k in BLOCKS:
        raw, kept = counts(use, k, now, docs.get(k))
        st, note = ("failed", reason) if reason else (d.block_status(run, k), "")
        if k in errs:
            st, note = "failed", "오류 — " + errs[k]
        elif st == "no_findings" and not searched(use, k):
            st, note = "failed", "검색 기록 없음 — coverage 에 " + "·".join(COVERAGE_KEYS[k]) + " 키가 없다"
        elif k == "theaters" and st in d.BLOCK_DONE:
            seen = observed_theaters(use, now, docs.get("theaters"))
            missing = [t for t in ews.THEATERS if t not in seen]
            if not seen:
                st, note = "failed", "관측 전역 0 — 전역마다 72시간 안 원문으로 state(0 포함)를 적는다"
            elif len(seen) < min_observed():
                st, note = PARTIAL, f"관측 {len(seen)}/{len(ews.THEATERS)} — 빠진 전역: " + ", ".join(missing)
        elif st == "checked" and kept == 0:
            st, note = "failed", "검사 뒤 남은 줄 0 — opened 밖 출처·필수 필드 누락"
        if not note and isinstance(notes.get(k), str) and notes[k].strip():
            note = notes[k].strip()
        last = (prev.get(k) or {}).get("last_ok") if isinstance(prev.get(k), dict) else None
        row = {"status": st, "last_ok": now if st in d.BLOCK_DONE else last, "raw": raw, "kept": kept}
        if note:
            row["note"] = note[:NOTE_LEN]
        blocks[k] = row
    bg = {"version": 1, "at": now, "opened": len(d._opened(run)) if isinstance(run, dict) else 0, "blocks": blocks}
    if reason:
        bg["error"] = reason
    return out, bg


def commit_message(bg: dict) -> str:
    """bg: world=checked factors=no_findings … 2026-11-05T23:37:12Z — 블록 상태는 코드가 적는다."""
    t = bg["at"].replace("+00:00", "Z")
    return "bg: " + " ".join(f"{k}={v['status']}" for k, v in bg["blocks"].items()) + f" {t}"


def _write(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")


def main(argv: list[str]) -> int:
    check = len(argv) == 3 and argv[1] == "--check"
    if check:
        argv = [argv[0], argv[2]]
    if len(argv) != 2 or argv[1].startswith("--"):
        print(__doc__)
        return 2
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")  # 시각은 모델이 아니라 시계가 정한다
    try:
        run = json.loads(Path(argv[1]).read_text())
    except (OSError, ValueError) as e:
        run = None
        print(f"::warning::background bg_run.json 을 읽지 못했다: {str(e)[:120]}")
    docs = {k: d._load_safe(STATE / f) for k, f in FILES.items()}
    docs["background"] = d._load_safe(STATE / "background.json")
    docs["latest"] = d._load_safe(STATE / "latest.json")  # 읽기만 — 매시간 소유
    out, bg = step(run, docs, now)
    if check:  # 쓰기 없음 — 상태 파일·background.json·.bg_commit_msg 모두 그대로
        retry = [k for k, v in bg["blocks"].items() if v["status"] not in d.BLOCK_DONE]
        seen = observed_theaters(run if bg.get("error") is None else {}, now, docs.get("theaters"))
        print(json.dumps({"check": "invalid" if bg.get("error") else ("ok" if not retry else "retry"),
                          "blocks": {k: {"status": v["status"], **({"note": v["note"]} if v.get("note") else {})}
                                     for k, v in bg["blocks"].items()},
                          "retry": retry, "missing_theaters": [t for t in ews.THEATERS if t not in seen],
                          **({"error": bg["error"]} if bg.get("error") else {})}, ensure_ascii=False))
        return 1 if bg.get("error") else (3 if retry else 0)
    STATE.mkdir(exist_ok=True)
    for k, f in FILES.items():
        if out[k] is not None:
            _write(STATE / f, out[k])
    _write(STATE / "background.json", bg)
    msg = commit_message(bg)
    COMMIT_MSG.write_text(msg + "\n")
    print(json.dumps({"background": "invalid" if bg.get("error") else "ok", "blocks": {k: v["status"] for k, v in bg["blocks"].items()},
                      **({"error": bg["error"]} if bg.get("error") else {}), "commit": msg}, ensure_ascii=False))
    return 1 if bg.get("error") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

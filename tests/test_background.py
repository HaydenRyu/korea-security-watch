"""배경 루틴 분리(11/05) — 파일 소유 · 블록 수행 기록 · judged_at · 세계 브리핑 폴백.

두 루틴(매시간 monitor.md · 하루 3회 background.md)이 서로 다른 파일만 써야 git pull --rebase 가 충돌 없이 합쳐진다.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import background as bg  # noqa: E402
import diff_state as d  # noqa: E402
import ews  # noqa: E402

OPENED = ["https://www.yna.co.kr/a", "https://www.reuters.com/b", "https://apnews.com/c"]
NOW = "2026-11-05T00:00:00+00:00"
HOURLY_OWNED = ("latest.json", "events.jsonl", "signals.json", "ladder.json", "scenarios.json", "ews.json", "outbox.json")
W = {"title": "제목", "summary": "요약", "link_korea": "연결", "source": {"name": "Reuters", "url": OPENED[1]}}


def src(*urls):
    return [{"url": u, "publisher": "p"} for u in urls]


def full_run(**extra):
    """매시간 run 에 배경 블록까지 전부 실은 것 — 옛 지시문·실수로 내도 매시간 코드는 배경 파일을 쓰면 안 된다."""
    return {"items": {k: {"status": "none"} for k in d.ITEMS}, "overall_summary": "s",
            "coverage": {k: ["q"] for k in d.ITEMS}, "opened": OPENED,
            "world": [W], "world_context": "맥락",
            "factors": {"nk_domestic": {"level": "긴장", "summary": "s", "sources": src(OPENED[0])}},
            "linkage": {"korea": [{"summary": "s", "sources": src(OPENED[0], OPENED[1])}],
                        "theaters": {"taiwan": {"state": 1, "channels": [], "summary": "s",
                                                "sources": [{"url": OPENED[0], "published_at": NOW}]}}},
            "incidents": [{"tier": "indirect", "category": "탄약", "when": {"occurred": "2026-11-04"},
                           "where": {"place": "강화"}, "what": "실탄 발견", "sources": src(OPENED[0])}],
            **extra}


class Box:
    """tmp state/ 하나에 세 모듈(diff_state·ews·background)을 함께 가리킨다."""

    def __init__(self, tmp: Path):
        self.state = tmp / "state"
        self.state.mkdir()
        self.tmp = tmp
        self.old = (d.STATE, ews.STATE, bg.STATE, bg.COMMIT_MSG)

    def __enter__(self):
        d.STATE = ews.STATE = bg.STATE = self.state
        bg.COMMIT_MSG = self.tmp / ".bg_commit_msg"
        return self

    def __exit__(self, *a):
        d.STATE, ews.STATE, bg.STATE, bg.COMMIT_MSG = self.old

    def snap(self, names):
        return {n: (self.state / n).read_bytes() if (self.state / n).exists() else None for n in names}

    def write(self, name, obj):
        p = self.tmp / name
        p.write_text(json.dumps(obj, ensure_ascii=False))
        return str(p)


def _seed_background(box):
    for n in bg.OWNED:
        (box.state / n).write_text(json.dumps({"seed": n}) + "\n")


def _seed_hourly(box):
    for n in HOURLY_OWNED:
        (box.state / n).write_text("" if n.endswith(".jsonl") else json.dumps({"seed": n}) + "\n")


# ── 1. 파일 소유 ──
def test_hourly_main_and_ews_leave_background_files_byte_identical():
    with tempfile.TemporaryDirectory() as td, Box(Path(td)) as box:
        _seed_background(box)
        before = box.snap(bg.OWNED)
        rj = box.write("run.json", full_run())
        assert d.main(["x", rj]) == 0
        assert ews.main(["ews.py", rj]) == 0  # 옛 지시문의 `ews.py run.json` 도 theaters.json 을 쓰지 않는다
        assert box.snap(bg.OWNED) == before
        # 판정 불가 회차도 마찬가지(예전엔 만료 반영으로 매 회차 다시 썼다)
        assert d.main(["x", box.write("run.json", {"status": "unverified", "reason": "x"})]) == 0
        assert box.snap(bg.OWNED) == before
        written = {p.name for p in box.state.iterdir()}
        assert written - set(bg.OWNED) <= set(HOURLY_OWNED)  # 매시간이 쓴 파일은 매시간 목록 안
        latest = json.loads((box.state / "latest.json").read_text())
        assert not any(k in latest for k in (*d.WORLD_KEYS, "factors", "linkage", "incidents", "blocks"))


def test_background_main_leaves_hourly_files_byte_identical():
    with tempfile.TemporaryDirectory() as td, Box(Path(td)) as box:
        _seed_hourly(box)
        before = box.snap(HOURLY_OWNED)
        r = full_run(blocks={k: "checked" for k in d.BACKGROUND_BLOCKS})
        assert bg.main(["x", box.write("bg_run.json", r)]) == 0
        assert box.snap(HOURLY_OWNED) == before
        assert {p.name for p in box.state.iterdir()} == set(HOURLY_OWNED) | set(bg.OWNED)
        w = json.loads((box.state / "world.json").read_text())
        th = json.loads((box.state / "theaters.json").read_text())
        fac = json.loads((box.state / "factors.json").read_text())
        inc = json.loads((box.state / "incidents.json").read_text())
        assert w["world"][0]["title"] == "제목" and w["world_context"] == "맥락"
        assert set(th["theaters"]) == {"taiwan"} and fac["domains"]["nk_domestic"]["level"] == "긴장" and len(inc["items"]) == 1
        msg = (box.tmp / ".bg_commit_msg").read_text()
        assert msg.startswith("bg: world=checked factors=checked linkage=checked theaters=checked incidents=checked ")


def test_prompts_commit_only_owned_files():
    """지시문의 git add 줄이 코드의 소유 목록과 같고, 디렉터리째 넣지 않는다."""
    bgp = (ROOT / "prompts" / "background.md").read_text()
    mon = (ROOT / "prompts" / "monitor.md").read_text()
    bg_add = re.search(r"`git add (state/world\.json[^`&]*)&&", bgp).group(1).split()
    assert sorted(Path(x).name for x in bg_add) == sorted(bg.OWNED)
    mon_add = re.search(r"`git add (state/latest\.json[^`&]*)&&", mon).group(1)
    assert not any(f"state/{n}" in mon_add for n in bg.OWNED)
    assert all(f"state/{n}" in mon_add for n in HOURLY_OWNED)
    assert not re.search(r"git add state/ ", bgp + mon)  # 디렉터리째 add 하는 명령이 없다(금지 문구 「`git add state/` 처럼」은 허용)
    assert "요인 회차" not in mon and "요인 회차" not in bgp
    assert "python3 scripts/ews.py run.json" not in mon


# ── 2. 블록 수행 기록 ──
def test_hourly_blocks_missing_or_invalid_recorded_as_skipped():
    with tempfile.TemporaryDirectory() as td, Box(Path(td)) as box:
        r = full_run(blocks={"signals": "checked", "ladder": "done!"})
        assert d.main(["x", box.write("run.json", r)]) == 0
        ev = json.loads((box.state / "events.jsonl").read_text().splitlines()[-1])
        assert ev["blocks"] == {"signals": "checked", "ladder": "skipped", "calm": "skipped"}
        assert d.main(["x", box.write("run.json", {"status": "unverified", "reason": "x", "blocks": {"calm": "checked"}})]) == 0
        ev = json.loads((box.state / "events.jsonl").read_text().splitlines()[-1])
        assert ev["blocks"] == {k: "skipped" for k in d.HOURLY_BLOCKS}  # 판정 불가 회차는 블록 입력을 쓰지 않는다


def test_background_blocks_skipped_failed_and_last_ok():
    t1, t2 = NOW, "2026-11-05T06:00:00+00:00"
    r = full_run(blocks={"world": "checked", "factors": "no_findings", "theaters": "failed"},
                 notes={"theaters": "검색 실패"})
    r["incidents"][0]["sources"] = src("https://never-opened.example/x")  # 검사 뒤 0줄
    r["blocks"]["incidents"] = "checked"
    _, rec = bg.step(r, {}, t1)
    b = rec["blocks"]
    assert b["world"]["status"] == "checked" and b["world"]["last_ok"] == t1
    assert b["factors"]["status"] == "no_findings" and b["factors"]["last_ok"] == t1
    assert b["linkage"] == {"status": "skipped", "last_ok": None, "raw": 1, "kept": 1}  # 키 누락 = 안 함(입력은 반영)
    assert b["theaters"]["status"] == "failed" and b["theaters"]["note"] == "검색 실패"
    assert b["incidents"]["status"] == "failed" and b["incidents"]["kept"] == 0 and "남은 줄 0" in b["incidents"]["note"]
    _, rec2 = bg.step({"opened": OPENED, "blocks": {"world": "failed"}}, {"background": rec}, t2)
    assert rec2["blocks"]["world"]["last_ok"] == t1 and rec2["blocks"]["factors"]["status"] == "skipped"


def test_background_invalid_input_applies_nothing_but_expires():
    prev = {"linkage": d.update_linkage({"opened": OPENED, "linkage": {"korea": [{"summary": "s", "sources": src(OPENED[0])}]}},
                                        None, "2026-11-01T00:00:00+00:00")}
    out, rec = bg.step({"opened": [], "blocks": {k: "checked" for k in d.BACKGROUND_BLOCKS}, **full_run(opened=[])}, prev, NOW)
    assert rec["error"] and all(v["status"] == "failed" for v in rec["blocks"].values())
    assert out["linkage"]["korea"] == [] and out["world"]["world"] == [] and out["theaters"]["theaters"] == {}  # 96시간 뒤 만료만
    out, rec = bg.step("garbage", {}, NOW)
    assert rec["error"] and rec["opened"] == 0


# ── 3. 긴장 국면 judged_at — 생략 회차엔 유지 ──
def test_ladder_judged_at_kept_when_block_not_done():
    cat = {"ids": {"ultimatum": 3}, "names": {}, "total": 1}
    t0, t1 = "2026-11-04T00:00:00+00:00", "2026-11-04T01:00:00+00:00"
    base = d.update_ladder({"checked_at": t0, "blocks": {"ladder": "no_findings"}}, None, t0, cat)
    assert base["judged_at"] == t0
    for blocks in (None, {"ladder": "failed"}, {"signals": "checked"}):
        r = {"checked_at": t1, **({"blocks": blocks} if blocks else {})}
        assert d.update_ladder(r, base, t1, cat)["judged_at"] == t0
    assert d.update_ladder({"checked_at": t1, "blocks": {"ladder": "checked"}}, base, t1, cat)["judged_at"] == t1


# ── 4. 세계 브리핑 화면 폴백(index.html pickWorld) ──
@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_index_world_prefers_world_json_and_falls_back_to_latest():
    html = (ROOT / "index.html").read_text()
    fn = re.search(r"^function pickWorld\(.*$", html, re.M).group(0)
    assert 'get("world.json")' in html
    cases = [
        ({"world": [1], "world_at": "2026-11-05T00:00:00Z"}, {"world": [2], "world_at": "2026-11-04T00:00:00Z"}, [1]),
        (None, {"world": [2], "world_at": "2026-11-04T00:00:00Z"}, [2]),                       # world.json 없음(전환기·sample)
        ({"world": [], "world_at": None}, {"world": [2], "world_at": "2026-11-04T00:00:00Z"}, [2]),  # 아직 빈 world.json
        ({"world": [1], "world_at": "2026-11-04T00:00:00Z"}, {"world": [2], "world_at": "2026-11-05T00:00:00Z"}, [2]),  # 옛 회차가 더 늦게 씀
        ({"world": [1], "world_at": "2026-11-05T00:00:00Z"}, {"grade": 0}, [1]),               # 분리 뒤 latest 엔 world 없음
        (None, None, None),
    ]
    js = fn + "\nconst C=" + json.dumps(cases) + ";console.log(JSON.stringify(C.map(([a,b])=>{const r=pickWorld(a,b);return r?r.world:null})));"
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=20, check=True).stdout
    assert json.loads(out) == [c[2] for c in cases]

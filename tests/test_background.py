"""배경 루틴 분리(11/05) — 파일 소유 · 블록 수행 기록 · judged_at · 세계 브리핑 폴백.

두 루틴(매시간 monitor.md · 하루 3회 background.md)이 서로 다른 파일만 써야 git pull --rebase 가 충돌 없이 합쳐진다.
"""
import json
from datetime import datetime, timezone
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
LIVE = datetime.now(timezone.utc).isoformat(timespec="seconds")  # main() 은 실제 시계를 쓴다 — 그 경로 fixture 의 published_at
HOURLY_OWNED = ("latest.json", "events.jsonl", "signals.json", "ladder.json", "scenarios.json", "ews.json", "outbox.json")
W = {"title": "제목", "summary": "요약", "link_korea": "연결", "source": {"name": "Reuters", "url": OPENED[1]}}


def src(*urls):
    return [{"url": u, "publisher": "p"} for u in urls]


def full_run(pub=NOW, **extra):
    """매시간 run 에 배경 블록까지 전부 실은 것 — 옛 지시문·실수로 내도 매시간 코드는 배경 파일을 쓰면 안 된다."""
    return {"items": {k: {"status": "none"} for k in d.ITEMS}, "overall_summary": "s",
            "coverage": {k: ["q"] for k in d.ITEMS}, "opened": OPENED,
            "world": [W], "world_context": "맥락",
            "factors": {"nk_domestic": {"level": "긴장", "summary": "s", "sources": src(OPENED[0])}},
            "linkage": {"korea": [{"summary": "s", "sources": src(OPENED[0], OPENED[1])}],
                        "theaters": {"taiwan": {"state": 1, "channels": [], "summary": "s",
                                                "sources": [{"url": OPENED[0], "published_at": pub}]}}},
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
        r = full_run(pub=LIVE, blocks={k: "checked" for k in d.BACKGROUND_BLOCKS})
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
        assert msg.startswith("bg: world=checked factors=checked linkage=checked theaters=partial incidents=checked ")  # 전역 1/5 < 3


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
    r["coverage"]["factor:nk_domestic"] = ["북한 당 전원회의"]  # no_findings 는 검색 기록이 있어야 인정
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


# ── 리뷰 P2(11/05) ──
def test_block_exception_isolated_others_saved_and_rc0():
    """theaters.sources 가 숫자 → ews.ingest 예외. 그 블록만 failed, 나머지·background.json·커밋 메시지는 만든다."""
    with tempfile.TemporaryDirectory() as td, Box(Path(td)) as box:
        (box.state / "theaters.json").write_text('{"seed": 1}\n')
        r = full_run(pub=LIVE, blocks={k: "checked" for k in d.BACKGROUND_BLOCKS})
        r["linkage"]["theaters"]["taiwan"]["sources"] = 5
        assert bg.main(["x", box.write("bg_run.json", r)]) == 0
        rec = json.loads((box.state / "background.json").read_text())["blocks"]
        assert rec["theaters"]["status"] == "failed" and rec["theaters"]["note"].startswith("오류 — TypeError")
        assert rec["theaters"]["last_ok"] is None
        assert all(rec[k]["status"] == "checked" for k in ("world", "factors", "linkage", "incidents"))
        assert (box.state / "theaters.json").read_text() == '{"seed": 1}\n'  # 직전 내용 유지
        assert json.loads((box.state / "world.json").read_text())["world"]
        assert "theaters=failed" in (box.tmp / ".bg_commit_msg").read_text()
    with tempfile.TemporaryDirectory() as td, Box(Path(td)) as box:  # 직전 파일이 없으면 그 파일은 만들지 않는다
        assert bg.main(["x", box.write("bg_run.json", r)]) == 0
        assert not (box.state / "theaters.json").exists() and (box.state / "background.json").exists()


def test_transition_newer_latest_world_is_kept_until_background_catches_up():
    """머지 전 옛 매시간 회차가 시드보다 새 latest.world 를 쓴 경우 — 매시간은 지우지 않고, 배경이 옮긴 뒤에 뺀다."""
    seed = {"version": 1, "world_at": "2026-11-04T13:13:53+00:00", "world": [W]}
    newer = {"items": {k: {"status": "none"} for k in d.ITEMS}, "grade": 0, "active": [], "checked_at": "x",
             "world": [{**W, "title": "새 브리핑"}], "world_context": "새 맥락", "world_at": "2026-11-05T01:00:00+00:00"}
    r = full_run(checked_at="2026-11-05T02:00:00+00:00")
    nl, _, _ = d.step(json.loads(json.dumps(r)), newer, [], seed)
    assert nl["world"][0]["title"] == "새 브리핑" and nl["world_at"] == newer["world_at"]  # 유실 없음(이번 run 의 world 는 안 실림)
    held_from = {**newer, "grade": 1, "active": ["border_clash"]}
    held, ev, _ = d.step(json.loads(json.dumps(r)), held_from, [{"status": "ok", "raw_grade": 1, "checked_at": "x"}], seed)
    assert ev.get("held") and held["world_at"] == newer["world_at"]
    nl2, _, _ = d.step(json.loads(json.dumps(r)), newer, [], None)  # world.json 없음 — 지우지 않는다
    assert nl2["world_at"] == newer["world_at"]
    # 배경 회차: 새 브리핑이 없으면 latest 의 새것을 world.json 으로 옮긴다(latest 는 읽기만)
    out, _ = bg.step({"opened": OPENED, "blocks": {"world": "failed"}}, {"world": seed, "latest": newer}, NOW)
    assert out["world"]["world"][0]["title"] == "새 브리핑" and out["world"]["world_at"] == newer["world_at"]
    assert out["world"]["world_context"] == "새 맥락"
    nl3, _, _ = d.step(json.loads(json.dumps(r)), nl, [], out["world"])  # 따라잡은 뒤 매시간이 뺀다
    assert not any(k in nl3 for k in d.WORLD_KEYS)
    out2, _ = bg.step({"opened": OPENED}, {"world": out["world"], "latest": {**newer, "world_at": "2026-11-01T00:00:00+00:00"}}, NOW)
    assert out2["world"]["world_at"] == newer["world_at"]  # 더 오래된 latest 는 덮지 않는다


def test_index_refresh_checks_background_before_hourly_early_return():
    html = (ROOT / "index.html").read_text()
    body = html[html.index("const refresh=async()=>{"):]
    body = body[:body.index("setInterval(refresh")]
    early = body.index("checked_at===latest?.checked_at)return;")
    assert body.index('get("background.json")') < early
    bg_branch = body[body.index("if(bgNew){"):early]
    assert 'get("world.json")' in bg_branch and "refs()" in bg_branch and "ksw:bg" in bg_branch
    assert body.count('get("background.json")') == 1  # 폴링당 추가 요청은 1건
    assert 'addEventListener("ksw:bg"' in (ROOT / "board2.js").read_text()


def test_theater_sources_in_future_rejected_beyond_skew():
    def one(pub):
        r = {"opened": [OPENED[0]], "linkage": {"theaters": {"taiwan": {"state": 1, "channels": [], "summary": "s",
                                                               "sources": [{"url": OPENED[0], "published_at": pub}]}}}}
        return set(ews.ingest(r, None, NOW)["theaters"])
    assert one("2026-11-05T00:09:00+00:00") == {"taiwan"}   # 9분 앞 — 시계 오차 허용
    assert one("2026-11-05T00:11:00+00:00") == set()        # 11분 앞
    assert one("2026-11-05T09:00:00+09:00") == {"taiwan"}   # KST 표기 = UTC 00:00 — 정규화
    assert one("2026-11-05T09:00:00") == set()              # 시간대 없음 → UTC 09:00 = 9시간 미래
    assert ews.FUTURE_SKEW_MIN == 10


def test_theater_sources_accept_rss_pubdate():
    """정본 출처는 RSS 다 — pubDate(RFC 822) 를 그대로 옮겨도 72시간 검사를 지나야 한다(11/05 리뷰)."""
    r = {"opened": [OPENED[0]], "linkage": {"theaters": {"taiwan": {"state": 1, "channels": [], "summary": "s",
                                                           "sources": [{"url": OPENED[0], "published_at": "Wed, 04 Nov 2026 22:00:00 +0000"}]}}}}
    assert set(ews.ingest(r, None, NOW)["theaters"]) == {"taiwan"}
    assert ews._dt("Thu, 05 Nov 2026 09:00:00 +0900") == ews._dt("2026-11-05T00:00:00+00:00")
    assert ews._dt("not a date") is None and ews._dt("") is None and ews._dt(None) is None


def test_theater_quiet_check_from_canonical_feeds():
    """기사가 드문 전역은 정본 피드 확인이 state 0 관측이다 — 조건 하나라도 빠지면 미관측(11/05)."""
    F = ews.QUIET_FEEDS["scs_ecs"]
    def run(state=0, feeds=F[:2], cov=True, opened=None, pub=NOW, extra=()):
        srcs = [{"name": "f", "url": u, "published_at": pub} for u in feeds] + list(extra)
        r = {"opened": list(opened if opened is not None else feeds) + [x["url"] for x in extra],
             "linkage": {"theaters": {"scs_ecs": {"state": state, "channels": [], "summary": "", "sources": srcs}}}}
        if cov:
            r["coverage"] = {"linkage:theaters:scs_ecs": ["South China Sea"]}
        return ews.ingest(r, None, NOW)["theaters"].get("scs_ecs")
    ok = run()
    assert ok["state"] == 0 and all(s["kind"] == "feed" for s in ok["sources"]) and "72시간" in ok["summary"]
    assert run(state=1) is None                                    # 피드만으로 1 이상은 안 된다
    assert run(feeds=F[:1]) is None                                # 정본 2곳 이상인 전역은 2곳
    assert run(cov=False) is None                                  # 검색 기록 없음
    assert run(opened=F[:1]) is None                               # 실제로 연 피드만
    assert run(pub="2026-10-30T00:00:00+00:00") is None            # 죽은 피드(최신 항목 72시간 밖)
    off = run(feeds=("https://example.com/rss", "https://example.org/rss"))  # 정본 밖 주소 = 종전 규칙대로 「연 원문」 — 평온 확인 표식은 안 붙는다
    assert off is not None and not any(s.get("kind") for s in off["sources"]) and off["summary"] == ""
    art = {"name": "a", "url": OPENED[0], "published_at": NOW}
    mixed = run(state=1, extra=(art,))                              # 기사가 있으면 기사로 판정, 피드는 근거에서 뺀다
    assert mixed["state"] == 1 and [s["url"] for s in mixed["sources"]] == [OPENED[0]]
    one = {"opened": [ews.QUIET_FEEDS["ukraine"][0]], "coverage": {"linkage:theaters:ukraine": ["q"]},
           "linkage": {"theaters": {"ukraine": {"state": 0, "sources": [{"url": ews.QUIET_FEEDS["ukraine"][0], "published_at": NOW}]}}}}
    assert ews.ingest(one, None, NOW)["theaters"]["ukraine"]["state"] == 0   # 정본 1곳뿐인 전역은 1곳
    # 리뷰(11/05) 보강 ─ ①다른 전역의 정본 피드·주소 변형은 기사로 통과하지 않는다
    bbc_asia = ews.QUIET_FEEDS["scs_ecs"][2]
    tw = {"opened": [bbc_asia, bbc_asia.replace("https://", "http://") + "/"],
          "linkage": {"theaters": {"taiwan": {"state": 2, "sources": [{"url": bbc_asia, "published_at": NOW},
                                                                       {"url": bbc_asia.replace("https://", "http://") + "/", "published_at": NOW}]}}}}
    assert "taiwan" not in ews.ingest(tw, None, NOW)["theaters"]
    # ②검색은 전역마다 — 대만 검색 1건으로 다른 전역을 평온 처리하지 않는다
    multi = {"opened": [ews.QUIET_FEEDS["ukraine"][0], ews.QUIET_FEEDS["mideast"][0]],
             "coverage": {"linkage:theaters:taiwan": ["PLA drills around Taiwan"], "linkage:theaters": ["x"]},
             "linkage": {"theaters": {t: {"state": 0, "sources": [{"url": ews.QUIET_FEEDS[t][0], "published_at": NOW}]}
                                      for t in ("ukraine", "mideast")}}}
    assert ews.ingest(multi, None, NOW)["theaters"] == {}
    # ③신선한 긴장 이상은 피드 확인으로 평온이 되지 않는다(직전 값 유지) · 72시간 지난 직전 값은 덮는다
    prev = {"theaters": {"scs_ecs": {"state": 3, "channels": ["c1_us_gap"], "sources": [{"url": "u"}], "summary": "p",
                                     "judged_at": "2026-11-04T00:00:00+00:00"}}}
    r = {"opened": list(F[:2]), "coverage": {"linkage:theaters:scs_ecs": ["q"]},
         "linkage": {"theaters": {"scs_ecs": {"state": 0, "sources": [{"url": u, "published_at": NOW} for u in F[:2]]}}}}
    assert ews.ingest(r, prev, NOW)["theaters"]["scs_ecs"]["state"] == 3
    assert bg.observed_theaters(r, NOW, prev) == [] and bg.observed_theaters(r, NOW) == ["scs_ecs"]
    old = json.loads(json.dumps(prev)); old["theaters"]["scs_ecs"]["judged_at"] = "2026-11-01T00:00:00+00:00"
    assert ews.ingest(r, old, NOW)["theaters"]["scs_ecs"]["state"] == 0
    # ④피드 확인은 연결 채널을 관측하지 않는다
    r["linkage"]["theaters"]["scs_ecs"]["channels"] = ["c1_us_gap"]
    assert ews.ingest(r, None, NOW)["theaters"]["scs_ecs"]["channels"] == []
    # 2차 리뷰 ─ fragment·query 붙인 피드도 피드다
    frag = ews.QUIET_FEEDS["ukraine"][0] + "#top"
    fr = {"opened": [frag], "linkage": {"theaters": {"taiwan": {"state": 3, "sources": [{"url": frag, "published_at": NOW}]}}}}
    assert ews.ingest(fr, None, NOW)["theaters"] == {}
    # 같은 초의 직전 판은 이번 관측이 아니다
    same = {"theaters": {t: {"state": 1, "sources": [{"url": "u"}], "judged_at": NOW} for t in ("taiwan", "ukraine", "mideast")}}
    assert bg.observed_theaters({"opened": OPENED, "linkage": {"theaters": {}}}, NOW, same) == []
    # 거절된 평온 입력은 kept 에 들지 않는다
    rr = {**r, "blocks": {"theaters": "checked"}}
    _, rec = bg.step(rr, {"theaters": prev}, NOW)
    assert rec["blocks"]["theaters"]["kept"] == 0 and rec["blocks"]["theaters"]["status"] == "failed"
    js = (ROOT / "ews.js").read_text()
    assert "피드 확인" in js and 'kind==="feed"' in js  # ⑤화면이 피드 확인과 기사 근거를 구분한다
    fc = js.split("function initForecast", 1)[1].split("\n}", 1)[0]
    assert "quietOnly(t)" in fc  # 예측 표도 같은 표시
    md = (ROOT / "prompts" / "background.md").read_text()
    for t, urls in ews.QUIET_FEEDS.items():                         # 지시문 정본 표와 코드 목록이 같다
        row = next(l for l in md.splitlines() if l.startswith(f"| `{t}` |"))
        assert all(u in row for u in urls), t


# ── 회차 하한(11/05 첫 실회차: theaters 2/5 인데 checked · linkage 원문 0건인데 no_findings) ──
def _theater(pub=NOW):
    return {"state": 0, "channels": [], "summary": "s", "sources": [{"url": OPENED[0], "published_at": pub}]}


def test_theaters_partial_below_min_observed_and_checked_at_min():
    assert bg.min_observed() == json.loads((ROOT / "data" / "ews_weights.json").read_text())["min_observed_theaters"] == 3
    two = {"opened": OPENED, "coverage": {"linkage:theaters": ["q"]}, "blocks": {"theaters": "checked"},
           "linkage": {"theaters": {"ukraine": _theater(), "mideast": _theater()}}}
    out, rec = bg.step(two, {}, NOW)
    t = rec["blocks"]["theaters"]
    assert t["status"] == "partial" and t["last_ok"] is None and t["kept"] == 2
    assert t["note"] == "관측 2/5 — 빠진 전역: taiwan, scs_ecs, us_posture"
    assert set(out["theaters"]["theaters"]) == {"ukraine", "mideast"}  # 관측분은 그대로 쌓는다
    assert "theaters=partial" in bg.commit_message(rec) and "partial" in bg.STATUSES
    three = json.loads(json.dumps(two))
    three["linkage"]["theaters"]["taiwan"] = _theater()
    _, rec3 = bg.step(three, {"background": rec}, NOW)
    assert rec3["blocks"]["theaters"]["status"] == "checked" and rec3["blocks"]["theaters"]["last_ok"] == NOW
    stale = json.loads(json.dumps(three))  # 72시간 밖 출처는 관측으로 세지 않는다 — 자기 신고 checked 여도 partial
    stale["linkage"]["theaters"]["taiwan"] = _theater("2026-10-30T00:00:00+00:00")
    assert bg.step(stale, {}, NOW)[1]["blocks"]["theaters"]["status"] == "partial"
    none = {"opened": OPENED, "coverage": {"linkage:theaters": ["q"]}, "blocks": {"theaters": "no_findings"}}
    assert bg.step(none, {}, NOW)[1]["blocks"]["theaters"]["status"] == "failed"  # theaters 엔 해당 없음이 없다


def test_no_findings_requires_search_record_per_block():
    base = {"opened": OPENED, "blocks": {k: "no_findings" for k in d.BACKGROUND_BLOCKS if k != "theaters"}}
    _, rec = bg.step(base, {}, NOW)
    for k in ("world", "factors", "linkage", "incidents"):
        assert rec["blocks"][k]["status"] == "failed" and rec["blocks"][k]["note"].startswith("검색 기록 없음"), k
        assert rec["blocks"][k]["last_ok"] is None
    cov = {"world": ["세계 안보"], "factor:inter_korean": ["대북 전단"], "linkage:korea": ["북러 군사협력"], "incidents": ["실탄 발견 해안"]}
    _, rec2 = bg.step({**base, "coverage": cov}, {}, NOW)
    assert all(rec2["blocks"][k]["status"] == "no_findings" for k in ("world", "factors", "linkage", "incidents"))
    bad = {"world": [], "factors": ["x"], "linkage:theaters": ["q"], "incidents": [" "]}  # 빈 목록·접두사 없는 키·다른 블록 키
    _, rec3 = bg.step({**base, "coverage": bad}, {}, NOW)
    assert all(rec3["blocks"][k]["status"] == "failed" for k in ("world", "factors", "linkage", "incidents"))
    assert bg.step({**base, "coverage": {"linkage:taiwan": ["PLA drills around Taiwan"]}}, {}, NOW)[1]["blocks"]["linkage"]["status"] == "no_findings"


def test_check_mode_writes_nothing_and_reports_retry():
    with tempfile.TemporaryDirectory() as td, Box(Path(td)) as box:
        _seed_hourly(box)
        (box.state / "theaters.json").write_text('{"seed": 1}\n')
        before = {p.name: p.read_bytes() for p in box.state.iterdir()}
        r = {"opened": OPENED, "coverage": {"linkage:theaters": ["q"]}, "blocks": {"theaters": "checked", "linkage": "no_findings"},
             "linkage": {"theaters": {"ukraine": _theater(LIVE), "mideast": _theater(LIVE)}}}
        p = box.write("bg_run.json", r)
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bg.main(["x", "--check", p])
        assert rc == 3
        res = json.loads(buf.getvalue().strip().splitlines()[-1])
        assert res["check"] == "retry" and res["blocks"]["theaters"]["status"] == "partial"
        assert res["blocks"]["linkage"]["status"] == "failed" and set(res["retry"]) == {"world", "factors", "linkage", "theaters", "incidents"}
        assert res["missing_theaters"] == ["taiwan", "scs_ecs", "us_posture"]
        assert {p.name: p.read_bytes() for p in box.state.iterdir()} == before  # 쓰기 없음
        assert not (box.tmp / ".bg_commit_msg").exists()
        assert bg.main(["x", "--check", box.write("bad.json", {"opened": []})]) == 1
        assert {p.name: p.read_bytes() for p in box.state.iterdir()} == before
    with tempfile.TemporaryDirectory() as td, Box(Path(td)) as box:  # state/ 가 없어도 만들지 않는다
        shutil.rmtree(box.state, ignore_errors=True)
        with contextlib.redirect_stdout(io.StringIO()):
            bg.main(["x", "--check", box.write("bg_run.json", r)])
        assert not box.state.exists()


def test_background_prompt_requires_check_retry_and_fixed_sources():
    md = (ROOT / "prompts" / "background.md").read_text()
    assert "python3 -B scripts/background.py --check bg_run.json" in md  # -B: --check 는 .pyc 도 쓰지 않는다
    for t in ews.THEATERS:
        assert f"`{t}`" in md
    for k in ("\"world\"", "factor:", "linkage:taiwan", "linkage:korea", "linkage:theaters", "\"incidents\""):
        assert k in md, k
    assert "kmib.co.kr" not in md.split("## 0-1.")[1].split("## 1.")[0]  # 국민일보는 curl 우회 금지 매체 — 정본에 올리지 않는다
    table = "\n".join(l for l in md.split("## 0-1.")[1].split("## 1.")[0].splitlines() if l.startswith("|"))
    assert "aljazeera" not in table.lower()  # 약관이 허가 없는 자동 접근을 제한 — 정본 표에 올리지 않는다

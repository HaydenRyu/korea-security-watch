"""ews.js 화면 게이트 — public_globe·public_index 분리와 alpha==1 표시 제거(node 로 ews.js 를 최소 DOM 스텁 위에서 실제 실행).

지구본(MapLibre)은 「세계 보기」를 누를 때만 읽으므로 여기서는 열지 않는다. 연결선은 arcs()(지구본이 그대로 쓰는 함수)로 본다.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")

HARNESS = r"""
const CASE=JSON.parse(process.argv[1]);
const els={};
const el=id=>els[id]||(els[id]={id,innerHTML:"",textContent:"",hidden:["ews-tabs","ews-v","ews-fc"].includes(id),tabIndex:0,
  dataset:{},attrs:{},classList:{add(){},toggle(){}},addEventListener(){},setAttribute(k,v){this.attrs[k]=v},getAttribute(k){return this.attrs[k]},focus(){},
  querySelectorAll(){return id==="ews-tabs"?[el("tab-world"),el("tab-map")]:[]},querySelector(s){return s===".k"?el("fc-k"):null}});
el("tab-world").dataset.v="world";el("tab-map").dataset.v="map";el("fc-k").textContent="조기경보 지표";
for(const id of CASE.ids)el(id);
globalThis.document={getElementById:id=>CASE.ids.includes(id)?el(id):null,createElement:()=>({}),head:{append(){}}};
globalThis.location={search:CASE.search};
globalThis.localStorage={getItem:()=>null,setItem(){}};
globalThis.getComputedStyle=()=>({getPropertyValue:()=>"#fff"});
globalThis.fetch=async u=>{const b=CASE.files[u];return b===undefined?{ok:false}:{ok:true,json:async()=>b}};
if(CASE.hook)globalThis.__EWS_TEST__={};
require(CASE.js);
setTimeout(()=>{
  const out={tabs_hidden:el("ews-tabs").hidden,fc_hidden:el("ews-fc").hidden,side:el("ews-side").innerHTML,fc:el("ews-fc-b").innerHTML,
    fc_k:el("fc-k").textContent,fc_v:el("ews-fc-v").textContent};
  if(CASE.hook){const T=globalThis.__EWS_TEST__;out.gates=CASE.hook.gates.map(([w,s,q])=>T.gates(w,s,new URLSearchParams(q)));
    out.arcs=CASE.hook.arcs.map(([linked,doc])=>{T.setView({globe:true,index:true,linked});return T.arcs(doc).features.length})}
  console.log(JSON.stringify(out));
},50);
"""

IDS = ["ews-tabs", "ews-v", "map-p", "ews-side", "ews-globe", "ews-fc", "ews-fc-v", "ews-fc-b"]


def doc(alpha=1.0):
    return {"version": 1, "computed_at": "2026-11-05T03:00:00+00:00", "weights_version": 1, "status": "ok", "ews": 55, "band": "경계 징후",
            "d": 15, "e": 22, "alpha": alpha, "grade": 2, "grade_label": "경계",
            "coverage": {"observed_theaters": 2, "linkage_judged_at": "2026-11-05T02:00:00+00:00", "latest_updated_at": "2026-11-05T03:00:00+00:00"},
            "theaters": [
                {"key": "taiwan", "observed": True, "state": 2, "contribution": 11, "channels": ["c1_us_gap"], "summary": "대만 주변 합동훈련",
                 "sources": [{"name": "매체A", "url": "https://a.example/1", "published_at": "2026-11-05T01:00:00+00:00"}],
                 "judged_at": "2026-11-05T02:00:00+00:00"},
                {"key": "scs_ecs", "observed": True, "state": 0, "contribution": 0, "channels": [], "summary": "정본 피드 3곳에 72시간 안 관련 기사 없음",
                 "sources": [{"name": "피드B", "url": "https://b.example/rss", "published_at": "2026-11-05T00:00:00+00:00", "kind": "feed"}],
                 "judged_at": "2026-11-04T20:00:00+00:00"},
                {"key": "us_posture", "observed": False, "state": None, "contribution": 0, "channels": [], "summary": "", "sources": [], "judged_at": None}],
            "channels": [{"key": "c1_us_gap", "observed": True, "value": 7}], "last_good": None}


def run(weights, ews_doc=None, search="", hook=None, files=None):
    f = {"data/ews_weights.json": weights, "state/ews.json": ews_doc if ews_doc is not None else doc()}
    f.update(files or {})
    case = {"ids": IDS, "search": search, "files": f, "js": str(ROOT / "ews.js"), "hook": hook}
    r = subprocess.run(["node", "-e", HARNESS, json.dumps(case, ensure_ascii=False)], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


W = {"version": 1, "alpha": 1.0}


def test_gate_closed_draws_nothing():
    o = run({**W, "public_globe": False, "public_index": False})
    assert o["tabs_hidden"] and o["fc_hidden"] and o["side"] == "" and o["fc"] == ""


def test_legacy_public_key_opens_both():
    o = run({**W, "public": True})
    assert not o["tabs_hidden"] and "조기경보 지수(EWS)" in o["side"] and "조기경보 지수(EWS) 55" in o["fc"]
    assert run({**W, "public": False})["tabs_hidden"]


def test_new_keys_override_legacy_and_gates_table():
    q = {"gates": [
        [{"public": True}, False, ""], [{"public": False}, False, ""],
        [{"public": True, "public_globe": True, "public_index": False}, False, ""],
        [{"public_globe": True}, False, ""],                       # public_index 없음·옛 키 없음 → 닫힘
        [{"public": True, "public_index": False}, False, ""],     # 새 키 하나만 있으면 나머지는 옛 키
        [None, False, ""],
        [{"public_globe": False, "public_index": False}, True, "sample=1"],
        [{"public_globe": False, "public_index": False}, True, "sample=1&gate=globe"],
        [{"public_globe_view": True}, False, ""],                  # 지구본 화면만
        [{"public_globe_view": True, "public_globe": True}, False, ""],
        [{"public_globe": False, "public_index": False}, True, "sample=1&gate=view"]],
        "arcs": []}
    o = run({**W, "public_globe": False, "public_index": False}, hook=q)
    G = lambda v, g, i: {"view": v, "globe": g, "index": i}
    assert o["gates"] == [G(True, True, True), G(False, False, False), G(True, True, False),
                          G(True, True, False), G(True, True, False), G(False, False, False),
                          G(True, True, True), G(True, True, False),
                          G(True, False, False), G(True, True, False), G(True, False, False)]


def test_globe_view_only_shows_positions_without_judgments():
    """11/08 — 판정 공개(public_globe) 전 3D 지구본 화면만 연다. 단계·요약·출처·미관측 문구가 화면에 없어야 한다."""
    for o in (run({**W, "public_globe_view": True, "public_globe": False, "public_index": False}),
              run({**W, "public_globe": False, "public_index": False}, search="?sample=1&gate=view")):
        side = o["side"]
        assert not o["tabs_hidden"] and o["fc_hidden"] and o["fc"] == ""
        assert "판정 준비 중" in side and "위치만 보여 드립니다" in side
        assert all(n in side for n in ("대만해협", "러시아-우크라이나", "남·동중국해", "중동·호르무즈", "미 전략자산"))
        for bad in ("평온", "긴장", "위기", "교전", "대만 주변 합동훈련", "https://", "확인된 근거 없음", "조기경보 지수", "--g"):
            assert bad not in side, bad


def test_globe_public_index_hidden_shows_world_without_index():
    o = run({**W, "public_globe": True, "public_index": False})
    assert not o["tabs_hidden"] and not o["fc_hidden"]
    for s in (o["side"], o["fc"]):  # 지수 게이지·숫자·지수 문장 없음
        assert "조기경보 지수" not in s and "ews-meter" not in s and "ews-num" not in s and "55" not in s and "경계 징후" not in s
        assert "백테스트" not in s and "공식 경보 단계와 별개인 참고 지수" not in s
    assert "세계 전역" in o["side"] and "대만해협" in o["side"] and "남·동중국해" in o["side"]
    assert o["fc_k"] == "세계 전역 상황" and "백테스트" not in o["fc_v"] and o["fc_v"].startswith("확인 ")
    assert "대만 주변 합동훈련" in o["fc"] and "https://a.example/1" in o["fc"]


def test_globe_public_discloses_ai_and_stage_criteria_without_tier():
    """11/11 — 판정 공개 화면은 AI 판정임과 단계 기준을 밝히고, 설명 없는 출처 등급 글자는 싣지 않는다."""
    d = doc()
    d["theaters"][0]["sources"][0]["tier"] = "B"
    o = run({**W, "public_globe": True, "public_index": False}, ews_doc=d)
    for s in (o["side"], o["fc"]):
        assert "AI가 공개 기사를 읽고 쓴 판정" in s and "무력 충돌 계속 중" in s
    assert "매체A · B" not in o["fc"] and "매체A · " in o["fc"]  # 출처는 선택 전에도 나오는 예측 절로 본다


def test_alpha1_hides_contribution_channels_and_arcs():
    o = run({**W, "public_globe": True, "public_index": False},
            hook={"gates": [], "arcs": [[False, doc(1.0)], [True, doc(0.8)]]})
    side, fc = o["side"], o["fc"]
    assert "긴장 · " not in side and "위기 · 11" not in side and "평온(피드 확인) · 0" not in side  # 기여 점수
    assert "평온(피드 확인)" in side and "평온(피드 확인)" in fc  # 피드 확인 표시는 남는다
    assert "연결 채널" not in side and "미군 전력 공백" not in side and "<th>채널</th>" not in fc and "<th>기여</th>" not in fc
    assert "<th>확인</th>" in fc and "11.05 11:00 KST" in fc and "11.05 05:00 KST" in fc  # 전역별 확인 시각(judged_at)
    assert "확인된 근거 없음" in fc  # 미관측은 미관측으로
    assert o["arcs"] == [0, 1]  # alpha==1 연결선 0 · alpha<1 이면 기여>0 전역만 선


def test_alpha_below_one_keeps_previous_display():
    o = run({**W, "alpha": 0.8, "public_globe": True, "public_index": True}, ews_doc=doc(0.8))
    assert "위기 · 11" in o["side"] and "연결 채널" in o["side"] and "연동 전역" in o["side"]
    assert "<th>기여</th>" in o["fc"] and "<th>채널</th>" in o["fc"] and "조기경보 지수(EWS) 55" in o["fc"]


def test_alpha_unknown_is_treated_as_not_linked():
    d = doc(1.0)
    d.pop("alpha")
    o = run({"version": 1, "public_globe": True, "public_index": False}, ews_doc=d)
    assert "위기 · 11" not in o["side"] and "연결 채널" not in o["side"]


def test_sample_preview_globe_only():
    sample = json.loads((ROOT / "state" / "sample" / "ews.json").read_text(encoding="utf-8"))
    o = run({**W, "public_globe": False, "public_index": False}, search="?sample=1&gate=globe", files={"state/sample/ews.json": sample})
    assert not o["tabs_hidden"] and "조기경보 지수" not in o["side"] and "세계 전역" in o["side"]
    o = run({**W, "public_globe": False, "public_index": False}, search="?sample=1", files={"state/sample/ews.json": sample})
    assert "조기경보 지수(EWS)" in o["side"]


def test_error_doc_globe_only_says_so_without_index_words():
    err = {"version": 1, "status": "error", "error": "x", "theaters": [], "channels": [], "computed_at": None, "weights_version": 1}
    o = run({**W, "public_globe": True, "public_index": False}, ews_doc=err)
    assert "세계 전역 자료를 불러오지 못했습니다" in o["side"] and "조기경보 지수" not in o["side"] + o["fc"]


def test_compute_carries_theater_judged_at():
    """ews.py 가 theaters[] 에 판정 시각을 싣는다 — 관측 전역은 theaters.json 의 judged_at, 미관측은 None."""
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import diff_state as d
    import ews
    now, at = "2026-11-03T00:00:00+00:00", "2026-11-02T20:00:00+00:00"
    row = {"state": 1, "channels": ["c1_us_gap"], "summary": "s", "judged_at": at,
           "sources": [{"name": "n", "url": "https://e.example/0", "published_at": at}]}
    rows = {t: dict(row) for t in ews.THEATERS[:3]}
    w = {"version": 1, "alpha": 1.0, "stale_hours": 48, "min_observed_theaters": 3,
         "weights": {t: {c: 1.0 for c in ews.CHANNELS} for t in ews.THEATERS}}
    latest = {"checked_at": now, "items": {k: {"status": "none"} for k in d.ITEMS}}
    o = ews.compute(latest, {"schema_version": 2, "judged_at": at, "theaters": rows}, w, now)
    got = {t["key"]: t["judged_at"] for t in o["theaters"]}
    assert got == {"taiwan": at, "ukraine": at, "scs_ecs": at, "mideast": None, "us_posture": None}

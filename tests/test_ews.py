"""조기경보 지수(EWS) — 계산 규칙·보류·실패·공식 등급 불변. 가중치는 고정 픽스처(전 칸 1.0, α 0.7)를 쓴다."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import diff_state as d  # noqa: E402
import ews  # noqa: E402

NOW = "2026-11-03T00:00:00+00:00"
H49 = "2026-10-31T23:00:00+00:00"  # 49시간 전
WEIGHTS = {"version": 9, "alpha": 0.7, "stale_hours": 48, "min_observed_theaters": 3,
           "weights": {t: {c: 1.0 for c in ews.CHANNELS} for t in ews.THEATERS}}


def latest(**status):
    return {"checked_at": NOW, "items": {k: {"status": status.get(k, "none")} for k in d.ITEMS}}


def row(state=0, channels=(), urls=1, published_at=NOW):
    return {"state": state, "channels": list(channels), "summary": "s",
            "sources": [{"name": "n", "tier": "B", "url": f"https://e.example/{i}", "published_at": published_at}
                        for i in range(urls)]}


def doc(judged_at=NOW, **over):
    rows = {t: row() for t in ews.THEATERS}
    rows.update(over)
    return {"schema_version": 2, "judged_at": judged_at, "theaters": rows}


def by_key(out, field):
    return {x["key"]: x for x in out[field]}


def run_main(tmp, weights=WEIGHTS, latest_doc=None, theaters=None, argv=("ews.py",)):
    state = tmp / "state"
    state.mkdir(exist_ok=True)
    wf = tmp / "w.json"
    wf.write_text(json.dumps(weights))
    if latest_doc is not None:
        (state / "latest.json").write_text(latest_doc if isinstance(latest_doc, str) else json.dumps(latest_doc))
    if theaters is not None:
        (state / "theaters.json").write_text(json.dumps(theaters))
    old = ews.STATE, ews.WEIGHTS
    ews.STATE, ews.WEIGHTS = state, wf
    try:
        rc = ews.main(list(argv))
    finally:
        ews.STATE, ews.WEIGHTS = old
    return rc, json.loads((state / "ews.json").read_text())


def test_a_all_quiet_is_zero_and_stable():
    o = ews.compute(latest(), doc(), WEIGHTS, NOW)
    assert (o["d"], o["e"], o["ews"], o["band"], o["status"]) == (0, 0, 0, "안정", "ok")


def test_b_core_corroborated_scores_before_rounding():
    o = ews.compute(latest(nuclear="corroborated"), doc(), WEIGHTS, NOW)
    assert o["d"] == 15 and o["ews"] == 11  # 4/26×100=15.38 · 0.7×15.38=10.77


def test_c_theater_contribution_splits_over_channels():
    o = ews.compute(latest(), doc(taiwan=row(3, ews.CHANNELS)), WEIGHTS, NOW)
    assert o["e"] == 20
    assert all(c["value"] == 4 and c["observed"] for c in o["channels"])
    assert by_key(o, "theaters")["taiwan"]["contribution"] == 20


def test_d_no_source_means_unobserved_not_calm():
    o = ews.compute(latest(), doc(taiwan=row(3, ews.CHANNELS, urls=0)), WEIGHTS, NOW)
    t = by_key(o, "theaters")["taiwan"]
    assert t["observed"] is False and t["contribution"] == 0 and t["state"] is None
    assert o["status"] == "ok" and o["e"] == 0 and o["coverage"]["observed_theaters"] == 4
    assert all(c["observed"] is False for c in o["channels"])


def test_e_withheld_conditions_keep_d():
    few = {t: row() for t in ews.THEATERS[:2]}
    cases = [doc(judged_at=None), {"judged_at": NOW, "theaters": few}, doc(judged_at=H49)]
    for case in cases:
        o = ews.compute(latest(nuclear="corroborated"), case, WEIGHTS, NOW)
        assert o["status"] == "withheld" and o["e"] is None and o["ews"] is None and o["band"] is None
        assert o["d"] == 15
    stale_latest = {**latest(), "checked_at": H49}
    assert ews.compute(stale_latest, doc(), WEIGHTS, NOW)["status"] == "withheld"


def test_e_withheld_keeps_last_good():
    good = {"ews": 30, "band": "관찰", "computed_at": H49}
    assert ews.compute(latest(), doc(judged_at=None), WEIGHTS, NOW, good)["last_good"] == good


def test_inherited_stale_theater_is_unobserved():
    o = ews.compute(latest(), doc(taiwan={**row(3, ews.CHANNELS), "judged_at": H49}), WEIGHTS, NOW)
    assert by_key(o, "theaters")["taiwan"]["observed"] is False and o["e"] == 0


def test_f_broken_input_is_error_rc0_warning_and_last_good():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "state").mkdir()
        good = {"ews": 30, "band": "관찰", "computed_at": H49}
        (tmp / "state" / "ews.json").write_text(json.dumps({"last_good": good}))
        rc, o = run_main(tmp, latest_doc="{broken")
    assert rc == 0 and o["status"] == "error" and o["ews"] is None and o["last_good"] == good
    assert o["error"] and len(o["error"]) <= 200


def test_f_warning_goes_to_stdout(capsys):
    with tempfile.TemporaryDirectory() as td:
        run_main(Path(td), latest_doc="{broken")
    assert capsys.readouterr().out.startswith("::warning::ews ")


def test_g_band_boundaries():
    for raw, want_ews, want_band in [(24.49, 24, "안정"), (24.5, 25, "관찰"), (49, 49, "관찰"), (50, 50, "경계 징후"),
                                     (74, 74, "경계 징후"), (75, 75, "고위험 징후")]:
        assert ews._round(raw) == want_ews and ews.band(ews._round(raw)) == want_band


# ── 공식 단계·과거 검정과 어긋나지 않는다 ──
ALPHA1 = {**WEIGHTS, "alpha": 1.0}


def test_index_never_below_official_grade_band():
    items = {k: {"status": "none"} for k in d.ITEMS}
    for grade, want in [(0, "안정"), (1, "관찰"), (2, "경계 징후"), (3, "고위험 징후")]:
        for w in (WEIGHTS, ALPHA1):
            o = ews.compute({"checked_at": NOW, "items": items, "grade": grade, "grade_label": "x"}, doc(), w, NOW)
            assert o["band"] == want and o["d"] == 0


def test_index_stays_inside_official_grade_band():
    o = ews.compute({**latest(**{k: "corroborated" for k in d.ITEMS}), "grade": 1}, doc(), ALPHA1, NOW)
    assert o["ews"] == 49 and o["band"] == "관찰"


def test_index_moves_with_d_inside_a_grade():
    low = {"troop_movement": {"status": "single"}}
    high = {**low, "border_clash": {"status": "corroborated", "routine": True}, "foreign_forces": {"status": "corroborated", "routine": True}}
    vals = []
    for over in (low, high):
        items = {**{k: {"status": "none"} for k in d.ITEMS}, **over}
        assert d.compute_grade(items) == 1
        vals.append(ews.compute({"checked_at": NOW, "items": items, "grade": 1}, doc(), ALPHA1, NOW)["ews"])
    assert vals == [29, 32]  # D 7.69 → 25+24×7.69/50 · D 15.38 → 25+24×15.38/50 (반올림)


def _top_of_grade(grade):
    routine = {"status": "corroborated", "routine": True}
    items = {k: routine for k in d.ITEMS}
    if grade == 1:
        items["troop_movement"] = {"status": "single"}
    if grade == 2:
        items = {k: {"status": "corroborated"} for k in d.ITEMS if k not in d.CRITICAL}
        items.update({k: {"status": "single"} for k in d.CRITICAL})
    if grade == 3:
        items = {k: {"status": "corroborated"} for k in d.ITEMS}
    return items


def test_grade_scale_points_are_reachable_at_that_grade():
    for grade in (1, 2, 3):
        items = _top_of_grade(grade)
        assert d.compute_grade(items) == grade
        assert round(ews.score_d(items) / 100 * ews.D_POINTS_MAX) == ews.GRADE_SCALE[grade][2]
        assert ews.place(ews.score_d(items), grade) == ews.GRADE_SCALE[grade][1]


def test_alpha1_keeps_world_out_of_index_but_shows_e():
    o = ews.compute(latest(), doc(taiwan=row(3, ews.CHANNELS)), ALPHA1, NOW)
    assert o["e"] == 20 and o["ews"] == 0 and o["status"] == "ok"


def test_alpha1_world_gap_does_not_withhold_index():
    o = ews.compute({**latest(), "grade": 1}, doc(judged_at=None), ALPHA1, NOW)
    assert o["status"] == "ok" and o["ews"] == 25 and o["e"] is None  # 항목 전부 none 이어도 구간 하한
    assert all(t["contribution"] is None for t in o["theaters"]) and all(c["value"] is None for c in o["channels"])


def test_alpha1_stale_official_judgment_still_withholds():
    o = ews.compute({**latest(), "checked_at": H49, "grade": 2}, doc(), ALPHA1, NOW)
    assert o["status"] == "withheld" and o["ews"] is None and o["band"] is None


def test_real_weights_shape():
    w = json.loads((ROOT / "data" / "ews_weights.json").read_text())
    assert w["public_globe"] is True and w["public_index"] is False and "public" not in w and w["version"] == 1 and "백테스트 전" in w["basis"]
    assert w["alpha"] == 1.0  # 낮추려면 Phase 3 백테스트 통과가 먼저다
    assert set(w["weights"]) == set(ews.THEATERS)
    assert all(set(v) == set(ews.CHANNELS) for v in w["weights"].values())


# ── 쌓기: run.linkage.theaters → state/theaters.json ──
def _run(**t):
    return {"opened": ["https://e.example/0"], "linkage": {"schema_version": 2, "theaters": t}}


def test_ingest_requires_opened_source():
    ok = row(1, ["c3_simul"])
    bad = {**row(2), "sources": [{"url": "https://unopened.example/x"}]}
    out = ews.ingest(_run(taiwan=ok, ukraine=bad), None, NOW)
    assert set(out["theaters"]) == {"taiwan"} and out["judged_at"] == NOW and out["schema_version"] == 2
    assert out["theaters"]["taiwan"]["judged_at"] == NOW


def test_ingest_drops_unknown_values_and_keeps_previous():
    prev = {"judged_at": H49, "theaters": {"mideast": {**row(2), "judged_at": H49}}}
    out = ews.ingest(_run(taiwan={**row(9)}, ukraine={**row(True)}, nope=row(1)), prev, NOW)
    assert set(out["theaters"]) == {"mideast"} and out["judged_at"] == H49
    out = ews.ingest(_run(taiwan=row(1, ["c1_us_gap", "made_up"])), prev, NOW)
    assert out["theaters"]["taiwan"]["channels"] == ["c1_us_gap"] and "mideast" in out["theaters"]


def test_main_ignores_run_json_and_never_writes_theaters(capsys):
    """11/05 — theaters.json 은 배경 루틴 소유. 옛 지시문이 `ews.py run.json` 을 불러도 읽기만 한다."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rj = tmp / "run.json"
        rj.write_text(json.dumps(_run(**{t: row() for t in ews.THEATERS})))
        prev = {"schema_version": 2, "judged_at": None, "theaters": {}}
        rc, o = run_main(tmp, latest_doc={**latest(), "checked_at": ews.datetime.now(ews.timezone.utc).isoformat()},
                         theaters=prev, argv=("ews.py", str(rj)))
        saved = json.loads((tmp / "state" / "theaters.json").read_text())
    assert rc == 0 and saved == prev and "::warning::ews run.json" in capsys.readouterr().out


def test_ingest_drops_sources_older_than_72h_or_without_published_at():
    t71, t73 = "2026-10-31T01:00:00+00:00", "2026-10-30T23:00:00+00:00"  # NOW 기준 71시간 · 73시간 전
    ok = row(1, published_at=t71)
    old = row(2, published_at=t73)
    none_ = row(1)
    none_["sources"][0].pop("published_at")
    bad = row(1, published_at="어제")
    zless = row(1, published_at="2026-11-02T09:00:00")  # 시간대 없음 → UTC 로 본다
    out = ews.ingest(_run(taiwan=ok, ukraine=old, scs_ecs=none_, mideast=bad, us_posture=zless), None, NOW)
    assert set(out["theaters"]) == {"taiwan", "us_posture"}
    mixed = {**row(1), "sources": [*row(1, published_at=t73)["sources"], {**row(1)["sources"][0], "url": "https://e.example/0"}]}
    out = ews.ingest(_run(taiwan=mixed), None, NOW)
    assert [s["published_at"] for s in out["theaters"]["taiwan"]["sources"]] == [NOW]  # 묵은 출처만 빠지고 전역은 남는다
    prev = {"judged_at": H49, "theaters": {"ukraine": {**row(2), "judged_at": H49}}}
    out = ews.ingest(_run(ukraine=old), prev, NOW)  # 출처가 전부 묵으면 이번 회차 미관측 — 직전 값을 잇는다
    assert out["judged_at"] == H49 and out["theaters"]["ukraine"]["judged_at"] == H49


# ── AC11 구버전 호환 · AC3 공식 등급 불변 ──
def test_ac11_old_linkage_shape_withholds_without_theaters_file():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "state").mkdir()
        (tmp / "state" / "linkage.json").write_text(json.dumps(
            {"version": 1, "updated_at": NOW, "judged_at": None, "taiwan": None, "korea": []}))
        rc, o = run_main(tmp, latest_doc={**latest(), "checked_at": ews.datetime.now(ews.timezone.utc).isoformat()})
    assert rc == 0 and o["status"] == "withheld" and o["ews"] is None and o["d"] == 0  # 픽스처 α 0.7 — E 를 합칠 때는 보류


def test_ac11_theaters_key_does_not_disturb_linkage_consumers():
    plain = d.update_linkage({"opened": [], "linkage": {}}, {"taiwan": None, "korea": []}, NOW)
    both = d.update_linkage(_run(taiwan=row()), {"taiwan": None, "korea": []}, NOW)
    assert both == plain and set(both) == {"version", "updated_at", "judged_at", "taiwan", "korea"}


def test_ac3_grade_and_outbox_same_with_theaters():
    items = {k: {"status": "none"} for k in d.ITEMS}
    items["nuclear"] = {"status": "corroborated", "region": "r", "summary": "s",
                        "sources": [{"name": "a", "tier": "A", "url": "https://a.example/1"},
                                    {"name": "b", "tier": "B", "url": "https://b.example/2"}]}
    base = {"checked_at": NOW, "items": items, "opened": ["https://a.example/1", "https://b.example/2", "https://c.example/3"],
            "coverage": {k: ["q"] for k in d.ITEMS}, "overall_summary": "s"}
    with_th = {**base, "linkage": {"schema_version": 2, "theaters": {"taiwan": row(3, ews.CHANNELS)}}}
    a, ev_a, out_a = d.step(json.loads(json.dumps(base)), None, [])
    b, ev_b, out_b = d.step(json.loads(json.dumps(with_th)), None, [])
    assert ev_a["grade"] == ev_b["grade"] and out_a == out_b
    assert (a or {}).get("grade") == (b or {}).get("grade") and "theaters" not in (b or {}) and "linkage" not in (b or {})


# ── AC7 확정 카피 — 화면 문구는 글자 단위로 같다({...} 자리는 코드가 넣는다) ──
COPY = ["세계 보기", "한반도 상세", "조기경보 지수(EWS)", "0–24 안정 · 25–49 관찰 · 50–74 경계 징후 · 75–100 고위험 징후",
        "공식 경보 단계와 별개인 참고 지수입니다. 공식 경보는 재난안전포털·긴급재난문자를 따르세요.",
        "조기경보 지수를 계산하지 못했습니다 — ", "확인된 근거 없음", "대만해협", "러시아-우크라이나", "남·동중국해", "중동·호르무즈", "미 전략자산",
        "미군 전력 공백", "북-러·북-중 군사협력", "동시 압박", "해상교통로·에너지", "제재·외교", "조기경보 지표", "한반도로 돌아가기",
        "판정 보류 — 세계 연동 데이터가 부족하거나 오래되었습니다 (관측 전역 ", "마지막 정상값 ",
        "이 브라우저에서는 지구본을 표시할 수 없어 목록으로 보여 드립니다.", " · 백테스트 전", "한반도 직접 ", " · 세계 연동 "]


def test_ac7_fixed_copy_present():
    text = "".join((ROOT / p).read_text(encoding="utf-8") for p in ("ews.js", "index.html", "forecast.html"))
    assert [c for c in COPY if c not in text] == []


def test_sample_has_three_states():
    s = json.loads((ROOT / "state" / "sample" / "ews.json").read_text(encoding="utf-8"))
    assert {k: s[k]["status"] for k in ("ok", "stale", "error")} == {"ok": "ok", "stale": "withheld", "error": "error"}
    assert s["withheld"]["e"] is None and s["withheld"]["ews"] is not None  # 세계 연동 공백은 지수를 막지 않는다


def test_backtest_counts_only_upgrades_inside_window():
    import ews_backtest as b
    r = [{"at": f"2026-10-0{i}T00:00:00+00:00", "grade": g, "d": x} for i, (g, x) in enumerate([(1, 4.0), (1, 12.0), (2, 19.0), (1, 4.0)], 1)]
    assert b.entries(r) == ["2026-10-03T00:00:00+00:00"]
    assert b.lead_table(r, 1, 2) == {4.0: [2, 0], 12.0: [1, 1]}


def test_ingest_holds_downgrade_without_resolution():
    """11/08 검수 — 우크라이나 2→1 이 국지 휴전·외교 기사 1건으로 내려갔다. 하향은 해소 근거(resolution)가 있어야 한다."""
    prev = {"judged_at": H49, "theaters": {"ukraine": {**row(2), "judged_at": "2026-11-02T12:00:00+00:00"}}}
    out = ews.ingest(_run(ukraine=row(1)), prev, NOW)
    u = out["theaters"]["ukraine"]
    assert u["state"] == 2 and u["downgrade_held"] == 1 and u["judged_at"] == NOW and "resolution" not in u
    out = ews.ingest(_run(ukraine={**row(0), "resolution": "전선 전체 휴전 발효"}), prev, NOW)
    u = out["theaters"]["ukraine"]
    assert u["state"] == 0 and u["resolution"] == "전선 전체 휴전 발효" and "downgrade_held" not in u
    out = ews.ingest(_run(ukraine=row(3)), prev, NOW)  # 상향은 그대로
    assert out["theaters"]["ukraine"]["state"] == 3
    stale = {"judged_at": H49, "theaters": {"ukraine": {**row(2), "judged_at": "2026-10-30T00:00:00+00:00"}}}
    assert ews.ingest(_run(ukraine=row(1)), stale, NOW)["theaters"]["ukraine"]["state"] == 1  # 72시간 지난 직전 값은 묶지 않는다

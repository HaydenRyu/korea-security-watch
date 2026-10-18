import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import diff_state as d  # noqa: E402

SRC2 = [{"name": "합참", "url": "https://a.example"}, {"name": "Reuters", "url": "https://b.example"}]
SRC1 = SRC2[:1]


def run(**over):
    items = {k: {"status": "none"} for k in d.ITEMS}
    for k, v in over.items():
        items[k] = v
    return {"checked_at": "2026-10-18T05:00:00+00:00", "items": items, "overall_summary": "s",
            "coverage": {k: ["q"] for k in d.ITEMS},
            "opened": ["https://a.example", "https://b.example", "https://c.example"]}


def test_quiet_first_run_no_notify():
    latest, ev, out = d.step(run(), None, [])
    assert latest["grade"] == 0 and out is None


def test_same_state_no_notify():
    r = run(border_clash={"status": "single", "sources": SRC1})
    latest, _, _ = d.step(r, None, [])
    _, ev, out = d.step(run(border_clash={"status": "single", "sources": SRC1}), latest, [])
    assert out is None and ev["grade"] == 1


def test_grade_rise_notifies_me_and_friends():
    latest, _, _ = d.step(run(border_clash={"status": "single", "sources": SRC1}), None, [])
    r = run(border_clash={"status": "single", "sources": SRC1},
            logistics={"status": "single", "sources": SRC1})
    _, ev, out = d.step(r, latest, [])
    assert ev["grade"] == 2 and out["me"]["title"] == "한반도 징후 경계(2)" and "friends" in out
    assert len(out["me"]["message"]) <= d.MSG_MAX


def test_routine_only_caps_at_1():
    r = run(nuclear={"status": "corroborated", "routine": True, "sources": SRC2},
            border_clash={"status": "corroborated", "routine": True, "sources": SRC2})
    latest, _, _ = d.step(r, None, [])
    assert latest["grade"] == 1


def test_critical_alone_is_2():
    latest, _, out = d.step(run(nuclear={"status": "corroborated", "sources": SRC2}), None, [])
    assert latest["grade"] == 2 and out is not None


def test_critical_with_other_signal_is_3():
    r = run(nuclear={"status": "corroborated", "sources": SRC2}, logistics={"status": "single", "sources": SRC1})
    latest, _, out = d.step(r, None, [])
    assert latest["grade"] == 3 and out["friends"]["priority"] == 5


def test_routine_other_signal_does_not_make_3():
    r = run(nuclear={"status": "corroborated", "sources": SRC2},
            border_clash={"status": "single", "routine": True, "sources": SRC1})
    latest, _, _ = d.step(r, None, [])
    assert latest["grade"] == 2


def _drop_twice(latest, events, r_factory=run):
    """내림은 두 회차 연속이어야 적용된다 — 첫 회차는 보류, 두 번째에 적용."""
    l1, ev1, out1 = d.step(r_factory(), latest, events)
    assert ev1["held"] and out1 is None and l1["grade"] == latest["grade"]
    l2, ev2, out2 = d.step(r_factory(), l1, events + [ev1])
    return l2, ev2, out2


def _deesc():
    r = run()
    r["deescalation"] = "남북 고위급 합의 — 준전시상태 해제"
    return r


def test_drop_from_3_sends_release_only():
    # 위기 바닥(72h) 안에서는 해제 근거가 있어야 2 아래로 내려간다
    latest, ev0, _ = d.step(run(nuclear={"status": "corroborated", "sources": SRC2},
                                logistics={"status": "single", "sources": SRC1}), None, [])
    _, ev, out = _drop_twice(latest, [ev0], _deesc)
    assert ev["grade"] == 0 and out["me"]["title"].startswith("한반도 징후 위험 해제")


def test_unverified_keeps_latest_and_alerts_on_third():
    events = []
    outs = []
    for _ in range(4):
        latest, ev, out = d.step({"status": "unverified", "reason": "search failed"}, {"grade": 2}, events)
        assert latest is None
        events.append(ev)
        outs.append(out)
    assert [o is not None for o in outs] == [False, False, True, False]
    assert outs[2]["me"]["title"] == "안보 감시 중단" and "friends" not in outs[2]


def test_corroborated_requires_two_sources():
    try:
        d.validate(run(nuclear={"status": "corroborated", "sources": SRC1}))
    except d.RunError:
        return
    raise AssertionError("expected RunError")


def test_friends_only_at_warning_or_above():
    _, _, out1 = d.step(run(border_clash={"status": "single", "sources": SRC1}), None, [])
    assert out1 is not None and "friends" not in out1 and out1["me"]["priority"] == 3
    latest, _, _ = d.step(run(border_clash={"status": "single", "sources": SRC1}), None, [])
    _, _, out2 = d.step(run(border_clash={"status": "corroborated", "sources": SRC2}), latest, [])
    assert out2["friends"]["priority"] == 4 and "지인 주제" not in out2["friends"]["message"]


def test_release_notifies_friends():
    latest, ev0, _ = d.step(run(border_clash={"status": "corroborated", "sources": SRC2}), None, [])
    _, ev, out = _drop_twice(latest, [ev0])
    assert out is not None and "해제" in out["friends"]["title"] and ev["friends"]


def test_drop_within_low_grades_silent():
    latest, ev0, _ = d.step(run(border_clash={"status": "single", "sources": SRC1}), None, [])
    l2, ev, out = _drop_twice(latest, [ev0])
    assert out is None and l2["grade"] == 0 and "held" not in l2


# ── 내림 히스테리시스: 같은 사건이 회차마다 1↔0 으로 뒤집히던 문제(10/18 AI 어뢰 시험) ──
R1 = dict(nuclear={"status": "single", "routine": True, "sources": SRC1})


def _r1():
    return run(**R1)


def test_single_drop_is_held_keeps_items_and_logs_raw():
    latest, ev0, _ = d.step(_r1(), None, [])
    r = run()
    r["overall_summary"] = "이번엔 평시"
    l1, ev1, out1 = d.step(r, latest, [ev0])
    assert out1 is None and ev1["held"] is True and ev1["grade"] == 1 and ev1["raw_grade"] == 0
    assert ev1["notified"] is False
    assert l1["grade"] == 1 and l1["items"] == latest["items"] and l1["active"] == latest["active"]
    assert l1["held"]["raw_grade"] == 0 and l1["held"]["summary"] == "이번엔 평시"
    assert l1["checked_at"] == latest["checked_at"]  # 표시 항목은 직전 판정 시각의 것


def test_flip_flop_1_0_1_0_stays_1_and_silent():
    latest, ev, _ = d.step(_r1(), None, [])
    events = [ev]
    outs = []
    for r in [run(), _r1(), run(), _r1()]:
        nl, ev, out = d.step(r, latest, events)
        latest = nl or latest
        events.append(ev)
        outs.append(out)
    assert [e["grade"] for e in events] == [1, 1, 1, 1, 1]
    assert [e.get("held", False) for e in events] == [False, True, False, True, False]
    assert outs == [None] * 4 and "held" not in latest


def test_two_consecutive_drops_apply():
    latest, ev0, _ = d.step(_r1(), None, [])
    l1, ev1, _ = d.step(run(), latest, [ev0])
    l2, ev2, out2 = d.step(run(), l1, [ev0, ev1])
    assert ev2["grade"] == 0 and not ev2.get("held") and l2["grade"] == 0 and "held" not in l2 and out2 is None


def test_increase_applies_immediately_after_held():
    latest, ev0, _ = d.step(run(border_clash={"status": "single", "sources": SRC1}), None, [])
    l1, ev1, _ = d.step(run(), latest, [ev0])
    assert ev1["held"]
    two = run(border_clash={"status": "single", "sources": SRC1}, logistics={"status": "single", "sources": SRC1})
    l2, ev2, out2 = d.step(two, l1, [ev0, ev1])
    assert l2["grade"] == 2 and out2["me"]["title"] == "한반도 징후 경계(2)" and "held" not in l2


def test_unverified_neither_counts_nor_resets_drop_streak():
    latest, ev0, _ = d.step(run(border_clash={"status": "corroborated", "sources": SRC2}), None, [])
    l1, ev1, out1 = d.step(run(), latest, [ev0])
    assert ev1["held"] and out1 is None
    _, evu, _ = d.step({"status": "unverified", "reason": "x"}, l1, [ev0, ev1])
    # 판정 불가는 연속을 끊지 않는다 → 다음 낮은 회차에 적용 + 해제 1회
    l2, ev2, out2 = d.step(run(), l1, [ev0, ev1, evu])
    assert l2["grade"] == 0 and out2 is not None and "해제" in out2["me"]["title"]
    # 판정 불가만으로는 연속이 생기지 않는다: 높은 등급 → 판정 불가 → 낮은 회차 = 보류
    _, evu2, _ = d.step({"status": "unverified", "reason": "x"}, latest, [ev0])
    _, ev3, out3 = d.step(run(), latest, [ev0, evu2])
    assert ev3["held"] and out3 is None


def test_release_fires_once_when_drop_applied():
    latest, ev0, _ = d.step(run(border_clash={"status": "corroborated", "sources": SRC2}), None, [])
    events, outs = [ev0], []
    for _ in range(4):
        nl, ev, out = d.step(run(), latest, events)
        latest = nl or latest
        events.append(ev)
        outs.append(out)
    assert [o is not None for o in outs] == [False, True, False, False]
    assert "해제" in outs[1]["me"]["title"] and outs[1]["friends"]


def test_new_items_are_normal_signals():
    assert {"electronic_cyber", "nk_internal"} <= set(d.ITEMS) and len(d.ITEMS) == 9
    assert not {"electronic_cyber", "nk_internal"} & d.CRITICAL
    r = run(electronic_cyber={"status": "corroborated", "sources": SRC2},
            nk_internal={"status": "single", "sources": SRC1})
    latest, _, _ = d.step(r, None, [])
    assert latest["grade"] == 2  # 교차확인돼도 핵심 항목이 아니면 위험(3)이 아니다
    r = run(nuclear={"status": "corroborated", "sources": SRC2}, nk_internal={"status": "single", "sources": SRC1})
    latest, _, _ = d.step(r, None, [])
    assert latest["grade"] == 3


def test_old_seven_key_run_rejected():
    r = run()
    del r["items"]["electronic_cyber"]
    assert _raises(r)


def _raises(r):
    try:
        d.validate(r)
    except d.RunError:
        return True
    return False


def test_coverage_required_for_every_item():
    r = run()
    del r["coverage"]["nuclear"]
    assert _raises(r)


def test_min_opened_pages():
    r = run()
    r["opened"] = r["opened"][:2]
    assert _raises(r)


def test_source_must_be_opened():
    r = run(border_clash={"status": "single", "sources": [{"name": "x", "url": "https://never.example"}]})
    assert _raises(r)


def test_event_keeps_sources_for_history():
    r = run(nuclear={"status": "corroborated", "routine": True, "sources": SRC2})
    _, ev, _ = d.step(r, None, [])
    assert [s["url"] for s in ev["sources"]] == ["https://a.example", "https://b.example"]
    _, ev0, _ = d.step(run(), None, [])
    assert ev0["sources"] == []


def test_opened_must_span_two_sites():
    r = run()
    r["opened"] = ["https://www.nknews.org/", "https://www.nknews.org/pro/", "https://nknews.org/category/news/"]
    assert _raises(r)
    r["opened"] = ["https://www.nknews.org/", "https://en.yna.co.kr/nk", "https://www.yna.co.kr/x"]
    assert not _raises(r)
    assert d.site("https://en.yna.co.kr/a") == "yna.co.kr" and d.site("https://www.38north.org/") == "38north.org"


W_OK = {"title": "제목", "summary": "요약", "link_korea": "연결", "region": "중동",
        "source": {"name": "Reuters", "url": "https://b.example"}}


def test_world_passes_to_latest_not_events():
    r = run()
    r["world"] = [W_OK]
    r["world_context"] = "맥락"
    latest, ev, _ = d.step(r, None, [])
    assert latest["world"][0]["title"] == "제목" and latest["world_context"] == "맥락"
    assert latest["world_at"] == r["checked_at"]
    assert not any(k.startswith("world") for k in ev)


def test_world_drops_bad_items_and_caps():
    r = run()
    r["world"] = ([W_OK] * 7 + ["x", {"title": "t"}, {**W_OK, "source": {"url": "https://never.example"}}])
    latest, _, _ = d.step(r, None, [])
    assert len(latest["world"]) == d.WORLD_MAX
    assert all(w["source"]["url"] == "https://b.example" for w in latest["world"])


def test_bad_world_never_blocks_run():
    for bad in ["str", 3, {"a": 1}, [None, 5, {"source": "x"}], [{"title": 1, "summary": [], "source": {"url": 2}}]]:
        r = run(border_clash={"status": "single", "sources": SRC1})
        r["world"] = bad
        r["world_context"] = {"no": 1}
        latest, ev, _ = d.step(r, None, [])
        assert ev["status"] == "ok" and latest["grade"] == 1 and "world" not in latest


def test_world_kept_when_missing_or_unverified():
    r = run()
    r["world"] = [W_OK]
    first, _, _ = d.step(r, None, [])
    nxt, _, _ = d.step(run(), first, [])  # 이번 회차에 world 없음 → 직전 것 유지
    assert nxt["world"] == first["world"] and nxt["world_at"] == first["world_at"]
    kept, _, _ = d.step({"status": "unverified", "reason": "x"}, first, [])
    assert kept is None  # unverified 는 latest 를 덮지 않는다


def test_world_does_not_affect_grade():
    a, _, _ = d.step(run(), None, [])
    r = run()
    r["world"] = [W_OK]
    b, _, out = d.step(r, None, [])
    assert a["grade"] == b["grade"] == 0 and out is None


# ── 2026-10-18 역검증(과거 위기 4건) 반영 규칙 ──
def _at(r, iso):
    r["checked_at"] = iso
    return r


def _seq(runs, latest=None, events=None):
    """runs 를 차례로 넣고 (latest, events, outs) 를 돌려준다."""
    events = list(events or [])
    outs = []
    for r in runs:
        nl, ev, out = d.step(r, latest, events)
        latest = nl or latest
        events.append(ev)
        outs.append(out)
    return latest, events, outs


def _h(n):
    from datetime import datetime, timedelta, timezone
    return (datetime(2026, 10, 18, 5, tzinfo=timezone.utc) + timedelta(hours=n)).isoformat()


# 제안 1 — 지속 상태는 24h 만료로 빠지지 않고 해제 보도까지 유지
QUASI_WAR = {"status": "single", "ongoing": True, "sources": SRC1, "summary": "준전시상태 선포"}


def test_ongoing_state_carried_when_model_drops_it():
    latest, events, outs = _seq([_at(run(nk_internal=dict(QUASI_WAR)), _h(0))] +
                                [_at(run(), _h(n)) for n in range(25, 30)])
    assert [e["grade"] for e in events] == [1] * 6
    assert latest["items"]["nk_internal"]["carried"] is True
    assert latest["items"]["nk_internal"]["carried_since"] == _h(25)  # 처음 이어 간 시각을 유지
    assert latest["unresolved"] == ["nk_internal"] and events[-1]["carried"] == ["nk_internal"]
    assert not any(e.get("held") for e in events) and outs[1:] == [None] * 5


def test_ongoing_state_ends_on_release_report():
    ended = run(nk_internal={"status": "none", "ended": True, "summary": "준전시상태 해제 보도"})
    latest, events, _ = _seq([_at(run(nk_internal=dict(QUASI_WAR)), _h(0)), _at(run(), _h(25)),
                              _at(ended, _h(26)), _at(run(), _h(27))])
    assert [e["grade"] for e in events] == [1, 1, 1, 0]  # 해제 뒤 내림은 기존 2회 연속 규칙
    assert latest["items"]["nk_internal"]["status"] == "none" and "unresolved" not in latest


def test_ongoing_with_deadline_expires_deadline_plus_24h():
    ult = {"status": "corroborated", "ultimatum": True, "until": _h(48), "sources": SRC2}
    latest, events, _ = _seq([_at(run(border_clash=dict(ult)), _h(0))] +
                             [_at(run(), _h(n)) for n in (30, 71, 72, 73)])
    assert [e["grade"] for e in events] == [2, 2, 2, 2, 0]  # 시한(48h) + 24h = 72h 부터 이어 가지 않음
    assert events[3].get("held") and not events[4].get("held")


def test_old_runs_without_new_fields_unchanged():
    # 새 필드가 없으면 기존 동작: 이어 가지 않고 24h 뒤 판정 그대로 내린다
    latest, events, _ = _seq([_at(run(nk_internal={"status": "single", "sources": SRC1}), _h(0)),
                              _at(run(), _h(25)), _at(run(), _h(26))])
    assert [e["grade"] for e in events] == [1, 1, 0] and "unresolved" not in latest


# 제안 2 — 최후통첩은 routine 이 아니다
def test_ultimatum_never_routine():
    it = {"status": "single", "routine": True, "ultimatum": True, "sources": SRC1}
    latest, _, _ = d.step(run(border_clash=it), None, [])
    assert latest["active"] == ["border_clash"] and latest["grade"] == 1
    latest, _, _ = d.step(run(border_clash=it, nuclear={"status": "corroborated", "sources": SRC2}), None, [])
    assert latest["grade"] == 3  # 정례였다면 핵 단독 2


# 제안 3 — border_clash 사망·영토 피격 교차확인은 단독 3
def test_border_clash_fatal_or_territory_alone_is_3():
    for f in ("fatalities", "territory_hit"):
        latest, _, out = d.step(run(border_clash={"status": "corroborated", f: True, "sources": SRC2}), None, [])
        assert latest["grade"] == 3 and out["friends"]["priority"] == 5
    # 단일 출처·정례·플래그 없음은 기존대로
    assert d.compute_grade(run(border_clash={"status": "single", "fatalities": True, "sources": SRC1})["items"]) == 1
    assert d.compute_grade(run(border_clash={"status": "corroborated", "sources": SRC2})["items"]) == 2
    assert d.compute_grade(run(border_clash={"status": "corroborated", "routine": True, "territory_hit": True,
                                             "sources": SRC2})["items"]) == 1
    assert d.compute_grade(run(nuclear={"status": "corroborated", "fatalities": True,
                                        "sources": SRC2})["items"]) == 2  # 다른 항목에는 적용 안 함


# 제안 4 — 위기 바닥 72h
THREE = dict(border_clash={"status": "corroborated", "territory_hit": True, "sources": SRC2})


def test_crisis_floor_holds_2_for_72h_then_releases():
    runs = [_at(run(**THREE), _h(0))] + [_at(run(), _h(n)) for n in (1, 2, 3, 71, 72, 73, 74)]
    latest, events, outs = _seq(runs)
    assert [e["grade"] for e in events] == [3, 3, 2, 2, 2, 2, 2, 0]
    assert events[2]["floor"] and events[2]["base_grade"] == 0
    assert not events[6].get("floor") and events[6]["held"]  # 72h 넘은 첫 회차 = 내림 보류
    rel = [o for o in outs if o and "friends" in o and "해제" in o["friends"]["title"]]
    assert len(rel) == 1 and rel[0]["me"]["title"].startswith("한반도 징후 경계 해제")  # 위기 중 「위험 해제」는 없다
    assert latest["grade"] == 0 and "floor" not in latest


def test_crisis_floor_marks_latest_and_counts_from_last_3():
    latest, events, _ = _seq([_at(run(**THREE), _h(0)), _at(run(**THREE), _h(40))] +
                             [_at(run(), _h(n)) for n in (41, 42, 100)])
    assert [e["grade"] for e in events] == [3, 3, 3, 2, 2]  # 마지막 3(40h) 기준 72h → 100h 도 바닥
    assert latest["floor"]["since"] == _h(40) and latest["floor"]["base_grade"] == 0


def test_deescalation_lifts_floor_until_next_3():
    runs = [_at(run(**THREE), _h(0)), _at(_deesc(), _h(1)), _at(run(), _h(2)), _at(run(), _h(3))]
    _, events, _ = _seq(runs)
    assert [e["grade"] for e in events] == [3, 3, 0, 0] and events[1]["deescalation"]
    # 해제 근거 뒤 다시 3 이 되면 바닥이 다시 선다
    _, events, _ = _seq([_at(run(**THREE), _h(5)), _at(run(), _h(6)), _at(run(), _h(7))], None, events)
    assert [e["grade"] for e in events[-3:]] == [3, 3, 2]


# 제안 5 — J-Alert 상공 통과 경보는 routine
def test_overflight_alert_is_routine():
    jalert = {"status": "corroborated", "overflight_alert": True, "sources": SRC2}
    launch = {"status": "corroborated", "routine": True, "sources": SRC2}
    latest, _, _ = d.step(run(gov_warning=jalert, nuclear=launch), None, [])
    assert latest["grade"] == 1 and latest["active"] == []
    latest, _, _ = d.step(run(gov_warning=jalert, nuclear={"status": "corroborated", "sources": SRC2}), None, [])
    assert latest["grade"] == 2  # 정례 경보는 핵 항목과 겹쳐도 위험을 만들지 않는다


# 제안 6 — unresolved: 원인 미상 중대 사건은 원인 발표 전까지 single 로 이어 간다(등급 1 바닥)
def test_unresolved_carried_as_single_until_resolved():
    sink = {"status": "corroborated", "unresolved": True, "fatalities": True, "sources": SRC2}
    runs = [_at(run(border_clash=sink), _h(0))] + [_at(run(), _h(n)) for n in (25, 26, 75, 76, 200)]
    latest, events, _ = _seq(runs)
    assert [e["grade"] for e in events] == [3, 3, 2, 2, 1, 1]  # 72h 바닥 뒤에도 1, 0 이 아니다
    assert latest["items"]["border_clash"]["status"] == "single" and latest["unresolved"] == ["border_clash"]
    found = run(border_clash={"status": "corroborated", "ended": True, "sources": SRC2, "summary": "조사 결론 발표"})
    latest, events, _ = _seq([_at(found, _h(201)), _at(run(), _h(230)), _at(run(), _h(231))], latest, events)
    assert [e["grade"] for e in events[-3:]] == [2, 2, 0] and "unresolved" not in latest


def test_state_release_lifts_floor_but_cause_report_does_not():
    quasi = {"status": "corroborated", "ongoing": True, "sources": SRC2}
    first = run(nk_internal=quasi, **THREE)
    rel = run(nk_internal={"status": "none", "ended": True, "summary": "준전시 해제"})
    _, events, _ = _seq([_at(first, _h(0)), _at(rel, _h(1)), _at(run(), _h(2))])
    assert events[1]["deescalation"] and [e["grade"] for e in events] == [3, 3, 0]
    sink = {"status": "corroborated", "unresolved": True, "sources": SRC2}
    found = run(border_clash={"status": "none", "ended": True, "summary": "원인 발표"})
    _, events, _ = _seq([_at(run(border_clash=sink, nuclear={"status": "corroborated", "sources": SRC2}), _h(0)),
                         _at(found, _h(1)), _at(run(), _h(2))])
    assert not events[1].get("deescalation") and [e["grade"] for e in events] == [3, 3, 2]


def test_world_article_via_opened_feed():
    feed = "https://feeds.bbci.co.uk/news/world/rss.xml"
    art = "https://www.bbc.com/news/articles/abc123"
    run = {"opened": [feed], "world": [
        {"title": "t", "summary": "s", "source": {"name": "BBC", "url": art, "via": feed}},
        {"title": "t2", "summary": "s2", "source": {"name": "X", "url": "https://x.example/a", "via": "https://not-opened.example/rss"}}]}
    out, _ = d.clean_world(run)
    assert [r["source"]["url"] for r in out] == [art]
    assert out[0]["source"]["via"] == feed

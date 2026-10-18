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
    return {"checked_at": "2026-10-18T05:00:00+00:00", "items": items, "overall_summary": "s"}


def test_quiet_first_run_no_notify():
    latest, ev, out = d.step(run(), None, [])
    assert latest["grade"] == 0 and out is None


def test_same_state_no_notify():
    r = run(border_clash={"status": "single", "sources": SRC1})
    latest, _, _ = d.step(r, None, [])
    _, ev, out = d.step(run(border_clash={"status": "single", "sources": SRC1}), latest, [])
    assert out is None and ev["grade"] == 1


def test_grade_rise_notifies_with_forward_flag():
    latest, _, _ = d.step(run(border_clash={"status": "single", "sources": SRC1}), None, [])
    r = run(border_clash={"status": "single", "sources": SRC1},
            logistics={"status": "single", "sources": SRC1})
    _, ev, out = d.step(r, latest, [])
    assert ev["grade"] == 2 and out["text"].startswith("[지인 전달 권고]")
    assert len(out["text"]) <= 200


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


def test_drop_from_3_sends_release_only():
    latest, _, _ = d.step(run(nuclear={"status": "corroborated", "sources": SRC2},
                              logistics={"status": "single", "sources": SRC1}), None, [])
    _, ev, out = d.step(run(), latest, [])
    assert ev["grade"] == 0 and out["text"].startswith("안보 감시 위험 해제")


def test_unverified_keeps_latest_and_alerts_on_third():
    events = []
    outs = []
    for _ in range(4):
        latest, ev, out = d.step({"status": "unverified", "reason": "search failed"}, {"grade": 2}, events)
        assert latest is None
        events.append(ev)
        outs.append(out)
    assert [o is not None for o in outs] == [False, False, True, False]


def test_corroborated_requires_two_sources():
    try:
        d.validate(run(nuclear={"status": "corroborated", "sources": SRC1}))
    except d.RunError:
        return
    raise AssertionError("expected RunError")


def test_friends_only_at_warning_or_above():
    _, _, out1 = d.step(run(border_clash={"status": "single", "sources": SRC1}), None, [])
    assert out1 is not None and "friends" not in out1
    latest, _, _ = d.step(run(border_clash={"status": "single", "sources": SRC1}), None, [])
    _, _, out2 = d.step(run(border_clash={"status": "corroborated", "sources": SRC2}), latest, [])
    assert out2["friends"]["priority"] == 4 and "지인 전달" not in out2["friends"]["message"]


def test_release_notifies_friends():
    latest, _, _ = d.step(run(border_clash={"status": "corroborated", "sources": SRC2}), None, [])
    _, ev, out = d.step(run(), latest, [])
    assert out is not None and "해제" in out["friends"]["title"] and ev["friends"]


def test_drop_within_low_grades_silent():
    latest, _, _ = d.step(run(border_clash={"status": "single", "sources": SRC1}), None, [])
    _, _, out = d.step(run(), latest, [])
    assert out is None

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import drills_kr as k  # noqa: E402

# 10/21 실측 RSS 항목(철원) 축약
CW = """<rss><channel>
<item><title><![CDATA[＇26년 10월 4주차 포탄사격 훈련일정 안내]]></title>
<link><![CDATA[https://www.cwg.go.kr/www/selectBbsNttView.do?key=206&bbsNo=24&nttNo=301835]]></link>
<pubDate><![CDATA[Fri, 16 Oct 2026 22:07:18 +0900]]></pubDate>
<description><![CDATA[2026년 10월 4주차 포탄사격 훈련일정을 아래와 같이 안내드립니다.
&lt;br/&gt;○ 2026. 10. 19. （월） 08:00~18:00
&lt;br/&gt;- 다락대 피탄지
&lt;br/&gt;○ 2026. 10. 21. （수） 08:00~18:00
&lt;br/&gt;- 문혜리 D진지 → 용화동 피탄지
&lt;br/&gt;○ 2026. 10. 23. （금）  08:00~18:00
&lt;br/&gt;- 다락대 피탄지
&lt;br/&gt;※문의 : 제5포병여단 031） 539-7151]]></description></item>
<item><title><![CDATA[2026년 고성군 일자리박람회 개최]]></title><link><![CDATA[https://x]]></link>
<pubDate><![CDATA[Fri, 16 Oct 2026 22:07:18 +0900]]></pubDate><description><![CDATA[무관]]></description></item>
</channel></rss>"""

GS_LIST = """<a href="#view" onclick="javascript: fn_search_detail('B000000080824Vr4hZ9f'); return false;">'26년 10월 제3포병여단 10월 포탄사격 훈련 일정 안내</a>
<a href="#view" onclick="javascript: fn_search_detail('B000000080906Bd3hN0z'); return false;">「고성 금강산 옛길」 자연유산(명승) 지정 예고 알림</a>"""

GS_VIEW = """<span class="date"><i>등록일</i>
 2026-10-12</span><div class="ui bbs--view--content"><p>육군 제3포병여단 포탄 사격 훈련 일정 안내드립니다.</p>
<p>○ 훈련기간 : 26. 10. 15.(목) 07:00 ~ 10:00 / 155mm포탄 150여발</p><p>○ 훈련장소 : 선유실리 사격장 / 오소령 표적지</p>
</div><div class="kogl--mask">공공누리</div>"""

NOW = datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc)


def test_cheorwon_rss_keeps_firing_posts_with_dates():
    ps = k.parse_cheorwon(CW)
    assert len(ps) == 1
    p = ps[0]
    assert p["title"].startswith("'26년 10월 4주차")
    assert p["posted"] == "2026-10-16"
    assert (p["first"], p["last"]) == ("2026-10-19", "2026-10-23")
    assert p["unit"] == "제5포병여단"
    assert "539" not in "".join(p["days"])  # 전화번호는 날짜가 아니다


def test_goseong_list_filters_and_view_parses():
    rows = k.parse_goseong_list(GS_LIST)
    assert rows == [("B000000080824Vr4hZ9f", "'26년 10월 제3포병여단 10월 포탄사격 훈련 일정 안내")]
    posted, body = k.parse_goseong_view(GS_VIEW)
    assert posted == "2026-10-12"
    assert "155mm포탄 150여발" in body and "공공누리" not in body
    assert k.drill_dates(body) == ["2026-10-15"]


def test_goseong_uses_cache_for_known_posts():
    calls = []

    def get(url, data=None):
        calls.append(url)
        return GS_LIST if data else GS_VIEW
    url = k.GS_BASE + "view.do?nttId=B000000080824Vr4hZ9f"
    out = k.fetch_goseong({url: {"url": url, "cached": True}}, get=get)
    assert out == [{"url": url, "cached": True}] and len(calls) == 1


def test_failed_source_keeps_previous_posts_and_rc3():
    prev_post = {"source": "goseong", "url": "u", "title": "t", "posted": "2026-10-12", "first": "2026-10-15",
                 "last": "2026-10-15", "days": ["2026-10-15"], "body": "", "unit": ""}
    prev = {"posts": [prev_post], "sources": {}}

    def boom(cache):
        raise RuntimeError("timeout")
    doc, rc = k.run(prev, NOW, {"cheorwon": lambda c: k.parse_cheorwon(CW), "goseong": boom})
    assert rc == 3
    assert doc["sources"]["goseong"]["status"] == "error" and doc["sources"]["cheorwon"]["status"] == "ok"
    assert {p["source"] for p in doc["posts"]} == {"cheorwon", "goseong"}
    cw = [p for p in doc["posts"] if p["source"] == "cheorwon"][0]
    assert cw["active_now"] is True and doc["posts"][0] is cw  # 진행 중이 먼저
    gs = [p for p in doc["posts"] if p["source"] == "goseong"][0]
    assert gs["active_now"] is False


def test_gap_day_between_first_and_last_is_not_active():
    p = k.parse_cheorwon(CW)[0]
    doc, _ = k.run({}, datetime(2026, 10, 22, 3, 0, tzinfo=timezone.utc),
                   {"cheorwon": lambda c: [dict(p)], "goseong": lambda c: []})
    assert doc["posts"][0]["active_now"] is False  # 10/22 은 공지된 사격일이 아니다


def test_old_posts_dropped_and_interval_respected():
    old = {"source": "cheorwon", "url": "o", "posted": "2026-08-01", "first": "2026-08-03", "last": "2026-08-07"}
    assert not k.keep(old, NOW.date())
    ok = {"status": "ok", "last_ok_at": "2026-10-21T01:00:00+00:00"}
    assert not k.due({"sources": {"cheorwon": ok, "goseong": ok}}, NOW)
    assert k.due({"sources": {"cheorwon": ok, "goseong": dict(ok, status="error")}}, NOW)
    assert k.due({}, NOW)


def test_derive_places_and_arms():
    r = k.derive("'26년 10월 제53보병여단 박격포 사격 훈련 일정 안내",
                 "○ 훈련기간 : 26. 10. 08.(목) 06:30 ~ 11:00 / 60mm고폭탄 50발\n○ 훈련장소 : 냉천리 사격장 / 오소령 표적지")
    assert r["places"] == ["냉천리 사격장 / 오소령 표적지"] and "박격포" in r["arms"] and "60mm고폭탄50발" in r["arms"]
    r = k.derive("개인화기 사격훈련", "- 2026. 9. 8.(화) 08:30 ~ 16:00\n- 문혜리 사격장")
    assert r["places"] == ["문혜리 사격장"]

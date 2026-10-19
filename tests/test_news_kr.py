import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import news_kr as m  # noqa: E402

KW = json.loads((ROOT / "data" / "news_keywords.json").read_text())
NOW = datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc)

RSS = """<?xml version='1.0' encoding='UTF-8'?><rss version='2.0'><channel>
<item><title><![CDATA[인천 강화군 해안서 '北 유실 추정' 지뢰 또 발견…이달 4번째]]></title>
 <link>https://www.yna.co.kr/view/AKR20261019000100065?input=1195m</link>
 <pubDate>Mon, 19 Oct 2026 16:05:00 +0900</pubDate>
 <description><![CDATA[(인천=연합뉴스) 해병대 2사단은 볼음도 해안에서 반보병지뢰 1발을 발견해 폭파 처리했다. 긴 본문 문장]]></description></item>
<item><title><![CDATA[주말 서울 청계광장 '야장 거리' 변신]]></title><link>https://www.yna.co.kr/view/AKR2</link>
 <pubDate>Mon, 19 Oct 2026 15:00:00 +0900</pubDate><description>전통문화 공연과 먹거리</description></item>
</channel></rss>"""

FUTURE = """<rss><channel>
<item><title>&amp;#91;단독&amp;#93; 미래 시각 기사 DMZ</title><link>https://www.kmib.co.kr/a?arcid=1&amp;utm_source=rss</link>
 <pubDate><![CDATA[ Tue, 20 Oct 2026 09:00:00 +0900 ]]></pubDate></item>
<item><title>시각 없는 기사</title><link>https://www.hani.co.kr/arti/1.html</link></item>
</channel></rss>"""


# ── 키워드 필터 ──────────────────────────────────────────────────────
def test_strong_term_hits_alone_and_maps_item():
    r = m.match("강화도 해안서 지뢰 발견", "", KW)
    assert r and "mine" in r["groups"] and "border_clash" in r["items"] and "지뢰" in r["terms"]


def test_weak_term_needs_context_and_title():
    assert m.match("서해 표류물 수거 작업", "", KW) is None               # 맥락어 없음
    assert "mine" in m.match("北 표류물 수거 작업", "", KW)["groups"]  # 맥락어(北) 있음
    assert m.match("주말 행사 안내", "북한 관련 대피 훈련 표류물", KW) is None  # weak 은 요약에서 보지 않는다


def test_exclude_and_substring_traps():
    assert m.match("부동산 지뢰밭 된 강남", "북한", KW) is None
    assert m.match("주말 청계광장 행사", "전통문화 공연", KW) is None       # 「전통문」 오탐 방지
    assert m.match("사격 국가대표 금메달", "군", KW) is None


# ── 파싱·저장 범위 ───────────────────────────────────────────────────
def test_parse_rss_canon_link_and_kst():
    got = m.parse_feed(RSS, NOW)
    assert got[0]["link"] == "https://www.yna.co.kr/view/AKR20261019000100065?input=1195m"
    assert got[0]["published"] == "2026-10-19T07:05:00+00:00"
    assert m.canon_link("https://news.sbs.co.kr/news/endPage.do?news_id=N1&plink=RSSLINK&cooper=RSSREADER") == \
        "https://news.sbs.co.kr/news/endPage.do?news_id=N1"
    assert m.canon_link("http://kookbang.dema.mil.kr/newsWeb/20261019/3/view.do") == "https://kookbang.dema.mil.kr/newsWeb/20261019/3/view.do"


def test_future_or_missing_time_and_double_escape():
    got = m.parse_feed(FUTURE, NOW)
    assert got[0]["title"] == "[단독] 미래 시각 기사 DMZ"           # 이중 이스케이프 해제
    assert got[0]["published"] is None                                 # 미래 시각은 믿지 않는다
    assert got[0]["link"] == "https://www.kmib.co.kr/a?arcid=1"        # 추적 파라미터 제거
    assert got[1]["published"] is None and got[1]["title"] == "시각 없는 기사"


def test_output_has_no_body_or_summary():
    doc = m.collect([{"outlet": "연합뉴스", "tier": "B", "section": "사회", "url": "u1"}],
                    KW, {}, NOW, fetcher=lambda u: RSS)
    s = json.dumps(doc, ensure_ascii=False)
    assert "긴 본문 문장" not in s and "볼음도" not in s  # 요약·본문 전재 없음
    assert doc["counts"]["hits"] == 1 and doc["clusters"][0]["items"] == ["border_clash"]
    assert doc["clusters"][0]["articles"][0]["title"].startswith("인천 강화군")  # 루틴 세션판에는 제목·링크가 있다


def test_public_version_has_no_titles_or_article_links():
    doc = m.collect([{"outlet": "연합뉴스", "tier": "B", "section": "사회", "url": "https://www.yna.co.kr/rss/society.xml"}],
                    KW, {}, NOW, fetcher=lambda u: RSS)
    pub = m.public(doc)
    s = json.dumps(pub, ensure_ascii=False)
    assert "강화" not in s and "AKR2026" not in s and "clusters" not in {k for k in pub if k != "counts"}
    assert pub["groups"]["mine"] == {"clusters": 1, "max_independent": 1, "latest": "2026-10-19T07:05:00+00:00"}
    assert pub["feeds"][0]["url"].endswith("society.xml") and pub["feeds"][0]["hits"] == 1  # 피드 주소·건수는 남긴다


# ── 중복 묶음 ────────────────────────────────────────────────────────
def _art(outlet, title, link, pub, wire=None):
    a = {"outlet": outlet, "title": title, "link": link, "published": pub, "first_seen": pub,
         "groups": ["mine", "border"], "items": ["border_clash"], "terms": ["지뢰"]}
    if wire:
        a["wire_copy"] = wire
    return a


def test_cluster_merges_same_event_across_outlets():
    arts = [_art("연합뉴스", "인천 강화군 해안서 '北 유실 추정' 지뢰 또 발견…이달 4번째", "https://a/1", "2026-10-19T07:05:00+00:00"),
            _art("동아일보", "강화도 해안서 '北 유실 추정 지뢰' 또 발견…올해만 19번째", "https://b/1", "2026-10-19T06:43:00+00:00"),
            _art("중앙일보", "강화 해안에 또 지뢰…'北 유실 추정' 이달에만 4번째 발견", "https://c/1", "2026-10-19T07:35:00+00:00"),
            _art("국민일보", "[반도 앨리스] 육군, 가상공간 DMZ서 로봇개 수색·정찰 훈련", "https://d/1", "2026-10-19T05:00:00+00:00")]
    cs = m.cluster(arts)
    big = max(cs, key=lambda c: c["n_outlets"])
    assert big["n_outlets"] == 3 and big["outlets"] == ["동아일보", "연합뉴스", "중앙일보"]
    assert big["first"] == "2026-10-19T06:43:00+00:00" and len(cs) == 2


def test_cluster_wire_copy_counts_once_and_time_gap_splits():
    t = "강화 해안서 北 유실 추정 지뢰 또 발견"
    cs = m.cluster([_art("연합뉴스", t, "https://a/1", "2026-10-19T07:00:00+00:00"),
                    _art("세계일보", t, "https://e/1", "2026-10-19T07:10:00+00:00", wire="연합뉴스")])
    assert cs[0]["n_outlets"] == 2 and cs[0]["independent"] == ["연합뉴스"]
    cs = m.cluster([_art("연합뉴스", t, "https://a/1", "2026-10-10T07:00:00+00:00"),
                    _art("동아일보", t, "https://b/1", "2026-10-19T07:00:00+00:00")])
    assert len(cs) == 2  # 9일 떨어진 같은 제목은 다른 사건


def test_wire_of_detects_other_outlet_only():
    assert m.wire_of("세계일보", "제목", "(인천=연합뉴스) 홍길동 기자 =") == "연합뉴스"
    assert m.wire_of("연합뉴스", "제목", "(인천=연합뉴스) 홍길동 기자 =") is None


# ── 피드 실패 → status ──────────────────────────────────────────────
FEEDS = [{"outlet": "연합뉴스", "tier": "B", "section": "사회", "url": "ok"},
         {"outlet": "SBS", "tier": "B", "section": "정치", "url": "bad"}]


def _fetcher(u):
    if u == "bad":
        raise OSError("HTTP Error 503")
    return RSS


def test_one_feed_failure_is_partial_and_others_continue():
    doc = m.collect(FEEDS, KW, {}, NOW, fetcher=_fetcher)
    assert doc["status"] == "partial" and "SBS/정치" in doc["status_detail"]
    bad = [f for f in doc["feeds"] if not f["ok"]][0]
    assert bad["outlet"] == "SBS" and "503" in bad["error"]
    assert doc["counts"]["feeds_failed"] == 1 and doc["counts"]["hits"] == 1  # 성공 피드는 반영
    assert doc["outlets"]["SBS"]["failed"] == 1 and doc["last_ok_at"] is None
    assert m.public(doc)["status"] == "partial" and m.public(doc)["feeds"][1]["ok"] is False  # 공개판에도 남는다


def test_all_fail_or_empty_feed_is_error_and_keeps_previous_hits():
    first = m.collect(FEEDS[:1], KW, {}, NOW, fetcher=_fetcher)
    later = NOW + timedelta(hours=1)
    doc = m.collect(FEEDS, KW, first, later, fetcher=lambda u: "<rss><channel></channel></rss>")
    assert doc["status"] == "error" and all("0건" in f["error"] for f in doc["feeds"])
    assert doc["counts"]["hits"] == 1  # 직전 적중분은 창(72h) 안에서 이어 간다
    gone = m.collect(FEEDS[:1], KW, first, NOW + timedelta(hours=m.WINDOW_H + 2), fetcher=lambda u: "<rss></rss>")
    assert gone["counts"]["hits"] == 0  # 창이 지나면 버린다


def test_main_rc3_on_failure_and_writes_status(tmp=None):
    import tempfile
    d = Path(tempfile.mkdtemp())
    src = d / "s.json"
    src.write_text(json.dumps({"tiers": [{"id": "B", "groups": [{"name": "", "items": [
        {"name": "X", "url": "https://x", "feeds": [{"section": "s", "url": "http://127.0.0.1:9/none.xml"}]}]}]}]}))
    out = d / "o.json"
    old = m.TIMEOUT_S
    m.TIMEOUT_S = 2
    try:
        rc = m.main(["--public", "--out", str(out), "--sources", str(src)])
    finally:
        m.TIMEOUT_S = old
    doc = json.loads(out.read_text())
    assert rc == 3 and doc["status"] == "error" and doc["feeds"][0]["ok"] is False and "clusters" not in doc


def test_sources_feeds_are_tier_b_and_https():
    feeds = m.load_feeds(json.loads((ROOT / "data" / "sources.json").read_text()))
    src = json.loads((ROOT / "data" / "sources.json").read_text())
    outlets = {f["outlet"] for f in feeds}
    assert len(outlets) >= 6 and all(f["tier"] == "B" and f["url"].startswith("https://") for f in feeds)
    excluded = {x["name"] for x in src["news_feed_audit"]["excluded"]}
    assert not outlets & excluded and all(x["reason"] for x in src["news_feed_audit"]["excluded"])  # 제외 매체는 수집하지 않는다


def test_time_budget_marks_remaining_feeds_failed():
    old = m.BUDGET_S
    m.BUDGET_S = -1  # 첫 피드 전부터 예산 초과
    try:
        doc = m.collect(FEEDS, KW, {}, NOW, fetcher=_fetcher)
    finally:
        m.BUDGET_S = old
    assert doc["status"] == "error" and all("예산" in f["error"] for f in doc["feeds"])


# ── 이용 제한 표시 → 여는 순서 ───────────────────────────────────────
def test_sources_restrict_codes_are_defined_and_known():
    src = json.loads((ROOT / "data" / "sources.json").read_text())
    codes = src["news_feed_audit"]["restrict_codes"]
    feeds = m.load_feeds(src)
    by = {f["outlet"]: f["restrict"] for f in feeds}
    assert set(codes) == set(m.RESTRICT_RANK)  # 정의된 코드와 수집기 등급표가 같다
    assert all(set(r) <= set(codes) for r in by.values())
    assert by["연합뉴스"] == ["ai_use"] and by["동아일보"] == ["ai_use"] and by["SBS"] == ["ai_use"]
    assert by["국민일보"] == ["claude_user_blocked"] and by["세계일보"] == [] and by["국방일보"] == []
    assert "조선일보" not in by  # 약관 제21조 — 자동화 도구 활용 불허
    v = {x["name"]: x for x in src["news_feed_audit"]["verified"]}
    assert {"조선일보", "국민일보", "국방일보"} <= set(v) and all(x["clause"] and x["verdict"] for x in v.values())


def _clu(arts, restrict):
    by = {k: v for k, v in restrict.items()}
    for a in arts:
        a["restrict"] = m.restrict_of(a, by)
    return m.cluster(arts)


R = {"연합뉴스": ["ai_use"], "동아일보": ["ai_use"], "국민일보": ["claude_user_blocked"], "세계일보": [], "국방일보": []}


def test_cluster_orders_unrestricted_original_first():
    t = "강화 해안서 北 유실 추정 지뢰 또 발견"
    cs = _clu([_art("연합뉴스", t, "https://a/1", "2026-10-19T06:00:00+00:00"),
               _art("국민일보", t, "https://d/1", "2026-10-19T06:10:00+00:00"),
               _art("동아일보", t, "https://b/1", "2026-10-19T06:20:00+00:00"),
               _art("국방일보", t, "https://k/1", "2026-10-19T07:30:00+00:00")], R)
    c = cs[0]
    assert [a["outlet"] for a in c["articles"]] == ["국방일보", "연합뉴스", "동아일보", "국민일보"]
    assert c["unrestricted"] is True and c["openable"] is True
    assert "restrict" not in c["articles"][0] and c["articles"][1]["restrict"] == ["ai_use"]
    assert c["first"] == "2026-10-19T06:00:00+00:00"  # 묶음 시각은 정렬과 무관


def test_wire_copy_of_restricted_wire_inherits_restriction():
    t = "강화 해안서 北 유실 추정 지뢰 또 발견"
    cs = _clu([_art("연합뉴스", t, "https://a/1", "2026-10-19T06:00:00+00:00"),
               _art("세계일보", t, "https://e/1", "2026-10-19T06:10:00+00:00", wire="연합뉴스")], R)
    c = cs[0]
    assert c["unrestricted"] is False and c["openable"] is True  # 세계일보 연합 전재는 제한 없는 원문이 아니다
    assert all(a["restrict"] == ["ai_use"] for a in c["articles"])


def test_blocked_only_cluster_is_not_openable_and_unknown_code_is_not_rank0():
    t = "강화 해안서 北 유실 추정 지뢰 또 발견"
    c = _clu([_art("국민일보", t, "https://d/1", "2026-10-19T06:00:00+00:00")], R)[0]
    assert c["unrestricted"] is False and c["openable"] is False
    assert m.open_rank({"restrict": ["new_code"]}) == 1 and m.open_rank({}) == 0


def test_collect_marks_restrict_from_feed_config():
    feeds = [{"outlet": "연합뉴스", "tier": "B", "section": "사회", "url": "ok", "restrict": ["ai_use"]}]
    doc = m.collect(feeds, KW, {}, NOW, fetcher=_fetcher)
    a = doc["clusters"][0]["articles"][0]
    assert a["restrict"] == ["ai_use"] and doc["clusters"][0]["unrestricted"] is False
    assert "restrict" not in json.dumps(m.public(doc))  # 공개판은 기사 단위 정보가 없다

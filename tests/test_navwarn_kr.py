import contextlib
import io
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import navwarn_kr as k  # noqa: E402
import navwarn_parse as nw  # noqa: E402

KEY = "Ab+c/d=TEST%KEY=="  # +·/·=·% 가 섞인 Decoding 키 모양 — 인코딩형까지 지워지는지 본다
NOW = datetime(2026, 10, 19, 3, 0, tzinfo=timezone.utc)  # = 10.19 12:00 KST

# data.go.kr 명세 예시값(15057792 현황·상세, 15058193 구역)으로 만든 응답
LIST_XML = """<?xml version="1.0" encoding="UTF-8"?><response><header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header>
<body><items><item><doc_num>26-501</doc_num><doc_type>상설</doc_type><gov_cd>합동참모본부</gov_cd><noti_cat>해상사격훈련</noti_cat>
<app_cat>접근금지</app_cat><title>10월 3주 상설 해상사격훈련 실시 알림</title><basic>합동참모본부 화력과-1729</basic>
<content>해상사격훈련이 아래와 같이 실시될 예정</content><area>A</area></item></items>
<numOfRows>100</numOfRows><pageNo>1</pageNo><totalCount>1</totalCount></body></response>"""
DETAIL_XML = """<response><header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header><body><items>
<item><doc_num>26-501</doc_num><area>A</area><position_nm>서해 격렬비열도 서방(R-99)</position_nm>
<position>36-30-00.0N,124-00-00.0E, 36-30-00.0N,125-00-00.0E, 35-30-00.0N,125-00-00.0E, 35-30-00.0N,124-00-00.0E</position>
<position_desc>해당 지점을 순차 연결한 선내 해역</position_desc><alarm_date>2026-10-19,2026-10-20,</alarm_date>
<alarm_time>08:00 ~ 22:00,08:00 ~ 22:00,</alarm_time><sea_pos>462</sea_pos></item></items><totalCount>1</totalCount></body></response>"""
ZONE_XML = """<response><header><resultCode>00</resultCode></header><body><items><item><posCd>R-72</posCd>
<centerZoneX>128-17-30.00 E</centerZoneX><centerZoneY>34-08-60.00 N</centerZoneY><crdnt>대한해협-욕지도남방근해</crdnt>
<hight>무한대</hight><reviym>201804</reviym><tnzone>해군 훈련구역</tnzone>
<zoneCrdnt>34-18-00.0N,128-00-00.0E, 34-18-00.0N,128-35-00.0E, 34-00-00.0N,128-35-00.0E, 34-00-00.0N,128-00-00.0E</zoneCrdnt>
<zoneDesc>해당 지점을 순차 연결한 선내 해역</zoneDesc></item></items><totalCount>1</totalCount></body></response>"""
NOT_REG = '<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</errMsg>' \
          '<returnAuthMsg>등록되지 않은 서비스키</returnAuthMsg><returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>'
NODATA = "<response><header><resultCode>03</resultCode><resultMsg>NODATA_ERROR</resultMsg></header><body><items/><totalCount>0</totalCount></body></response>"


def fake(routes, calls=None):
    def get(url, params):
        if calls is not None:
            calls.append((url.rsplit("/", 1)[1], dict(params)))
        for frag, body in routes:
            if frag in url:
                return body(params) if callable(body) else body
        raise AssertionError(url)
    return get


def test_ok_parses_warnings_areas_windows_and_zones():
    list_by_day = lambda p: LIST_XML if p["date"] == "20261019" else NODATA  # noqa: E731
    calls = []
    doc, rc = k.run(KEY, {}, NOW, fake([("WarningInfo", list_by_day), ("DetailInfo", DETAIL_XML), ("Seatn", ZONE_XML)], calls))
    assert rc == 0 and doc["status"] == "ok"
    assert sum(c[0] == "getNavigationalWarningInfo" for c in calls) == k.LOOKBACK_DAYS
    w = doc["warnings"]
    assert len(w) == 1 and w[0]["doc_num"] == "26-501" and w[0]["exercise"] and w[0]["active_now"]
    a = w[0]["areas"][0]
    assert a["polygon"][0] == [36.5, 124.0] and len(a["polygon"]) == 4
    assert a["windows"][0] == {"from": "2026-10-18T23:00:00+00:00", "to": "2026-10-19T13:00:00+00:00"}  # 08~22 KST
    assert doc["counts"]["by_kind"] == {"해상사격훈련": 1} and doc["counts"]["valid"] == 1
    z = doc["zones"]["items"][0]
    assert z["code"] == "R-72" and len(z["polygon"]) == 4 and z["center"][0] > 34.1
    # 다음 회차: 상세·구역은 캐시 — 현황만 다시 부른다
    calls2 = []
    doc2, _ = k.run(KEY, doc, NOW, fake([("WarningInfo", list_by_day), ("DetailInfo", DETAIL_XML), ("Seatn", ZONE_XML)], calls2))
    assert {c[0] for c in calls2} == {"getNavigationalWarningInfo"} and doc2["counts"]["new_details"] == 0


def test_expired_warning_drops_and_zero_is_ok_not_inactive():
    later = datetime(2026, 10, 21, 0, 0, tzinfo=timezone.utc)
    doc, rc = k.run(KEY, {}, NOW, fake([("WarningInfo", LIST_XML), ("DetailInfo", DETAIL_XML), ("Seatn", ZONE_XML)]))
    doc2, rc2 = k.run(KEY, doc, later, fake([("WarningInfo", NODATA), ("Seatn", ZONE_XML)]))
    assert rc2 == 0 and doc2["status"] == "ok" and doc2["warnings"] == []   # 기간 끝 → 빠짐, 0건은 ok 에서만


def test_key_inactive_is_separate_from_zero_and_keeps_previous():
    prev, _ = k.run(KEY, {}, NOW, fake([("WarningInfo", LIST_XML), ("DetailInfo", DETAIL_XML), ("Seatn", ZONE_XML)]))
    doc, rc = k.run(KEY, prev, NOW, fake([("", NOT_REG)]))
    assert rc == 3 and doc["status"] == "key_inactive" and "NOT_REGISTERED" in doc["status_detail"]
    assert doc["warnings"] == prev["warnings"] and doc["last_ok_at"] == prev["last_ok_at"]   # 0건과 섞지 않는다
    doc, rc = k.run(None, {}, NOW)
    assert rc == 3 and doc["status"] == "key_missing"
    try:
        k.parse_response('{"OpenAPI_ServiceResponse":{"cmmMsgHeader":{"errMsg":"LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR","returnReasonCode":"22"}}}')
        raise AssertionError
    except k.ApiError as e:
        assert e.status == "quota"


def test_key_never_appears_in_output_logs_or_errors():
    from urllib.parse import quote, quote_plus
    forms = [KEY, quote(KEY, safe=""), quote_plus(KEY)]

    def boom(url, params):  # 실제 urllib 오류처럼 URL 이 메시지에 섞여 오는 경우
        raise k.ApiError("error", f"URLError: <urlopen error https://apis.data.go.kr/x?serviceKey={quote(KEY, safe='')}&date=1> {KEY}")
    doc, rc = k.run(KEY, {}, NOW, boom)
    assert rc == 3 and doc["status"] == "error"
    blob = json.dumps(doc, ensure_ascii=False)
    assert not any(f in blob for f in forms) and "serviceKey=***" in blob
    # main 경로: 실제 _http 가 접속 실패(.invalid) — 산출 파일·표준출력 모두 키 없음
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "navwarn_kr.json"
        old = (k.BASE, k.EP_LIST, k.TIMEOUT, os.environ.get("DATA_GO_KR_KEY"))
        k.EP_LIST, k.TIMEOUT = "https://navwarn-test.invalid/1192136/NavigationalWarning/getNavigationalWarningInfo", 3
        os.environ["DATA_GO_KR_KEY"] = KEY
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                rc = k.main([str(out)])
        finally:
            k.BASE, k.EP_LIST, k.TIMEOUT = old[:3]
            os.environ.pop("DATA_GO_KR_KEY") if old[3] is None else os.environ.__setitem__("DATA_GO_KR_KEY", old[3])
        text = out.read_text() + buf.getvalue()
        assert rc == 3 and json.loads(out.read_text())["status"] == "error"
        assert not any(f in text for f in forms), text
    assert k.redact(f"a?serviceKey={KEY}&b=1", KEY) == "a?serviceKey=***&b=1"


def test_xml_with_bare_ampersand_and_control_chars_still_parses():
    body = LIST_XML.replace("해상사격훈련이 아래와", "R&D 해역 \x01사격훈련이 아래와")
    items, total = k.parse_response(body)
    assert total == 1 and items[0]["content"].startswith("R&D 해역")
    try:
        k.parse_response("<response><header><resultCode>00</resultCode></header><body><items><item>")
        raise AssertionError
    except k.ApiError as e:
        assert e.status == "error" and "XML" in e.detail


def test_foreign_relay_is_not_kr_exercise():
    # 10/19 실측 형태: KHOA 가 일본 경보를 「해상사격」으로 중계(사격부대 공란) — 억제 입력이 되면 안 된다
    assert not k.is_exercise({"noti_cat": "해상사격", "doc_type": "상설", "unit": "", "title": "일본 해상보안청 항행경보 알림"})
    assert k.is_exercise({"noti_cat": "해상사격", "doc_type": "상설", "unit": "합동참모본부", "title": "10월 4주 해상사격훈련(합동참모본부) 실시 알림"})
    assert k.is_exercise({"noti_cat": "해상훈련", "doc_type": "해상훈련", "unit": "해군작전사령부", "title": "소해임무장비 해상시험 실시 알림"})
    assert not k.is_exercise({"noti_cat": "선박사고", "unit": "해양경찰청", "title": "좌초 선박 알림"})


def test_truncated_page_retries_with_smaller_rows():
    seen = []

    def get(url, params):
        seen.append(params["numOfRows"])
        return ZONE_XML[:-40] if params["numOfRows"] > 25 else ZONE_XML   # 큰 쪽은 서버가 중간에 자른다
    assert len(k.fetch_all(get, k.EP_ZONE, {})) == 1 and seen == [100, 25]


def test_circle_area_becomes_polygon():
    a = k.area_of({"area": "3", "position": "36-00-00.0N,125-00-00.0E", "position_desc": "좌표를 중심으로 반경 5NM",
                   "alarm_date": "2026-10-19", "alarm_time": "08:00 ~ 17:00"})
    assert len(a["polygon"]) == 16 and a["radius_km"] == 9.26
    assert nw.shapes_overlap([(36.05, 125.02)], [tuple(p) for p in a["polygon"]])        # 반경 안(약 6km)
    assert not nw.shapes_overlap([(36.2, 125.0)], [tuple(p) for p in a["polygon"]])      # 반경 밖(약 22km)


def test_xml_with_bare_lt_parses():
    items, _ = k.parse_response(ZONE_XML.replace("무한대", "<3000ft"))
    assert items[0]["hight"] == "<3000ft"


def test_windows_cross_midnight_and_reuse_last_time():
    w = k.windows("2026-10-19,2026-10-20", "20:00 ~ 02:00")
    assert w[0] == {"from": "2026-10-19T11:00:00+00:00", "to": "2026-10-19T17:00:00+00:00"} and len(w) == 2


def _dprk_rec(day):
    # 2023-05 북한 위성 발사 통보 형상(황해 A·B + 필리핀 동방 C)
    t = ("ROCKET LAUNCHING 30 MAY TO 11 JUN\nA. 35-58N 123-52E 35-58N 124-16E 35-24N 124-19E 35-24N 123-55E\n"
         "B. 33-53N 123-34E 33-53N 124-08E 33-19N 124-12E 33-19N 123-38E\n"
         "C. 14-03N 128-52E 14-03N 129-35E 13-12N 129-38E 13-12N 128-55E")
    rec = {"id": "T", "window_from": f"2026-10-{day:02d}T00:00:00+00:00", "window_to": f"2026-10-{day:02d}T06:00:00+00:00"}
    return nw.classify(rec, t)


def _kr_doc(polys, day=19):
    areas = [{"area": chr(65 + i), "name": f"구역{i}", "polygon": [list(p) for p in poly],
              "windows": [{"from": f"2026-10-{day - 1:02d}T23:00:00+00:00", "to": f"2026-10-{day:02d}T13:00:00+00:00"}]}
             for i, poly in enumerate(polys)]
    return {"status": "ok", "warnings": [{"doc_num": "26-501", "exercise": True, "areas": areas}]}


def test_kr_exercise_suppresses_dprk_candidate_only_same_time_and_place():
    A = [(36.2, 123.7), (36.2, 124.4), (35.2, 124.4), (35.2, 123.7)]
    B = [(34.1, 123.4), (34.1, 124.3), (33.1, 124.3), (33.1, 123.4)]
    r = nw.apply_kr([_dprk_rec(19)], _kr_doc([A, B]))[0]
    assert r["dprk_pattern"] and not r["dprk_candidate"] and len(r["kr_exercise_overlap"]) == 2
    r = nw.apply_kr([_dprk_rec(25)], _kr_doc([A, B]))[0]                    # 다른 날 → 억제 안 함
    assert r["dprk_candidate"] and r["kr_exercise_overlap"] == []
    r = nw.apply_kr([_dprk_rec(19)], _kr_doc([A]))[0]                        # 황해 한 구역만 설명 → 후보 유지
    assert r["dprk_candidate"]
    far = [(37.5, 129.5), (37.5, 130.0), (37.0, 130.0), (37.0, 129.5)]      # 동해 → 겹치지 않음
    assert nw.apply_kr([_dprk_rec(19)], _kr_doc([far]))[0]["dprk_candidate"]


def test_no_kr_input_or_unknown_time_keeps_candidates():
    for kr in (None, {}, {"status": "key_inactive", "warnings": []}):
        r = nw.apply_kr([_dprk_rec(19)], kr)[0]
        assert r["dprk_candidate"] and r["kr_check"] == "no_input"
    rec = _dprk_rec(19)
    rec["window_from"] = rec["window_to"] = None
    r = nw.apply_kr([rec], _kr_doc([[(36.2, 123.7), (36.2, 124.4), (35.2, 124.4)]]))[0]
    assert r["dprk_candidate"] and r["kr_check"] == "time_unknown"


def test_nga_regression_unchanged_with_empty_kr():
    fx = Path(__file__).resolve().parent / "fixtures" / "nga_hydropac_2023_2024.json.gz"
    recs = nw.apply_kr(nw.from_nga(str(fx)), None)
    assert sorted(r["id"] for r in recs if r["dprk_candidate"]) == sorted(r["id"] for r in recs if r["dprk_pattern"])
    assert sum(r["dprk_candidate"] for r in recs) == 3


def test_firms_workflow_runs_navwarn_kr_without_blocking_hotspots():
    y = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "firms.yml").read_text()
    i, j = y.index("scripts/firms.py"), y.index("scripts/navwarn_kr.py")
    assert i < j and "continue-on-error: true" in y[y.rfind("- name", 0, j):j]
    assert "DATA_GO_KR_KEY: \"${{ secrets.DATA_GO_KR_KEY }}\"" in y and "state/navwarn_kr.json" in y


def test_foreign_relay_flag_is_separate_from_blank_unit():
    """중계 = 외국 표기 있음(발령 부대 공란·외국 기관) / 공란 + 외국 표기 없음 = 미기재(중계 아님) / 한국 기관 = 중계 아님."""
    assert k.is_foreign_relay({"noti_cat": "해상사격", "doc_type": "상설", "unit": "", "title": "일본 해상보안청 항행경보 알림"})
    assert k.is_foreign_relay({"noti_cat": "해상사격", "unit": "", "title": "항행경보 알림", "basis": "일본 해상보안청 항행경보(No.26-1844)"})
    assert not k.is_foreign_relay({"noti_cat": "해상사격", "unit": "", "title": "해상사격훈련 실시 알림"})
    assert not k.is_foreign_relay({"noti_cat": "해상사격", "unit": "합동참모본부", "title": "한미 연합 해상사격훈련 알림"})


def test_gateway_timeout_retries_with_backoff_then_succeeds():
    """SERVICETIMEOUT(05) 가 두 번 이어져도 백오프(5·25·60초 창) 안에 살아나면 성공, 끝까지 05 면 4회 뒤 실패."""
    err05 = '<response><header><resultCode>05</resultCode><resultMsg>SERVICETIMEOUT_ERROR</resultMsg></header></response>'
    orig_http1, orig_sleep = k._http1, k.time.sleep

    def run(bodies):
        waits, n = [], [0]

        def http1(url, params, key):
            n[0] += 1
            return bodies[min(n[0], len(bodies)) - 1]
        k._http1, k.time.sleep = http1, lambda s: waits.append(s)
        try:
            return k._http(k.EP_ZONE, {"pageNo": 1}, "k"), waits, n[0]
        except k.ApiError as e:
            return e, waits, n[0]
    try:
        body, waits, n = run([err05, err05, ZONE_XML])
        assert body == ZONE_XML and waits == [5, 25] and n == 3
        err, waits, n = run([err05])
        assert isinstance(err, k.ApiError) and "code 05" in err.detail and waits == [5, 25, 60] and n == 4
    finally:
        k._http1, k.time.sleep = orig_http1, orig_sleep

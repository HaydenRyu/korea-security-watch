import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import firms as f  # noqa: E402


def test_border_uses_mdl_distance():
    # 10/19: 근사 상자(38.9N 까지)가 평양 남동 30km 를 「접경」으로 찍었다
    assert not f.in_border(38.83, 126.0) and f.mdl_km(38.83, 126.0) > 80
    assert f.mdl_km(37.956, 126.677) < 2            # 판문점
    assert f.in_border(37.97, 126.55)               # 개성
    assert not f.in_border(37.57, 126.98)           # 서울


# ── 유형 추정(10/19) ──────────────────────────────────────────────────────────
from datetime import datetime, timezone  # noqa: E402


def test_north_uses_boundary_polygon_not_box():
    # 10/19 시제품: 위경도 상자가 랴오닝·지린 열점(10/17 실데이터)을 북측으로 찍었다
    for lat, lon in [(41.455, 124.18), (40.894, 124.173), (42.32, 125.25), (42.87, 126.154), (40.16, 124.36)]:
        assert not f.in_nk(lat, lon), (lat, lon)
    for lat, lon in [(39.03, 125.75), (38.878, 126.037), (39.446, 126.024), (42.25, 130.3), (40.10, 124.40)]:
        assert f.in_nk(lat, lon), (lat, lon)                # 평양·상원·순천·나선·신의주
    assert not f.in_nk(37.57, 126.98) and not f.in_nk(37.67, 125.70)   # 서울·연평도


def _pt(lat, lon, day):
    return {"lat": lat, "lon": lon, "t": f"{day}T03:00:00+00:00"}


def test_cells_accumulate_dedupe_and_prune():
    cells = {}
    f.update_cells(cells, [_pt(36.985, 126.69, "2026-10-17"), _pt(36.985, 126.69, "2026-10-17")], "2026-10-17")
    f.update_cells(cells, [_pt(36.986, 126.691, "2026-10-18")], "2026-10-18")   # 같은 격자 또는 이웃
    k = f.cell_key(36.985, 126.69)
    assert f.days_seen(cells, k) == 2
    f.update_cells(cells, [], "2026-11-30")                                    # 보관 기간 밖 → 비워진 격자 삭제
    assert cells == {}


def test_classify_labels_are_estimates_with_reasons():
    kind, conf, why = f.classify(2, ["현대제철 당진공장"], north=False)
    assert (kind, conf) == ("fixed", "중") and any("현대제철" in w for w in why)
    assert f.classify(1, ["POSCO 광양제철소"], False)[:2] == ("fixed", "낮음")
    assert f.classify(1, [], False)[0] == "burn"
    assert f.classify(3, [], False)[0] == "unknown"                            # 시설 없이 반복
    assert f.classify(1, [], True)[0] == "unknown"                             # 북측 OSM 희박
    assert f.classify(1, None, False)[0] == "unknown"                          # 미조회
    for k in f.KIND_LABEL.values():
        assert "추정" in k or k == "미상"


def test_facility_labels_translate_bare_osm_tags():
    # 이름 없는 시설은 태그값만 캐시된다 → 근거·표시 문구는 우리말. 질의하는 태그값 전부가 매핑돼야 한다
    for v in ("industrial", "plant", "works", "kiln", "flare", "chimney", "시설"):
        assert v in f.OSM_TAG_KO and "이름 없음" in f.OSM_TAG_KO[v]
    assert f.facility_labels(["industrial", "plant"]) == ["공업 지역(이름 없음)", "발전소(이름 없음)"]
    assert f.facility_labels(["상원세멘트련합기업소", "chimney"]) == ["상원세멘트련합기업소", "굴뚝(이름 없음)"]
    assert f.facility_labels(["works", "works"]) == ["공장(이름 없음)"]
    cells = {f.cell_key(36.95, 126.62): {"dates": ["2026-10-19"], "osm": ["industrial"], "osm_checked": "2026-10-19"}}
    pts = [{"lat": 36.95, "lon": 126.62}]
    f.annotate(pts, cells)
    assert pts[0]["osm"] == ["공업 지역(이름 없음)"] and "industrial" not in " ".join(pts[0]["kind_why"])
    assert any("공업 지역(이름 없음)" in w for w in pts[0]["kind_why"])
    assert cells[f.cell_key(36.95, 126.62)]["osm"] == ["industrial"]   # 캐시는 원본 그대로


def test_split_batch_assigns_results_per_cell():
    # 10/19 실측 응답 형태(make 요약 원소). 이름 우선 ko → name → en, 이름 없으면 태그값, n=0 은 「시설 없음」
    els = [
        {"type": "c", "id": 1, "tags": {"cell": "a", "n": "2", "ko": "현대제철 당진공장", "nm": "현대제철 당진공장;삼우 당진2공장",
                                         "en": "Hyundai Steel Dangjin Plant", "pw": "", "mm": "works", "lu": "industrial"}},
        {"type": "c", "id": 2, "tags": {"cell": "b", "n": "0", "ko": "", "nm": "", "en": "", "pw": "", "mm": "", "lu": ""}},
        {"type": "c", "id": 3, "tags": {"cell": "c", "n": "1", "ko": "", "nm": "", "en": "", "pw": "plant", "mm": "", "lu": "industrial"}},
        {"type": "c", "id": 4, "tags": {"cell": "zz", "n": "1", "nm": "남의 칸"}},
    ]
    got = f.split_batch(els, ["a", "b", "c", "d"])
    assert got["a"] == ["현대제철 당진공장", "삼우 당진2공장", "Hyundai Steel Dangjin Plant"]
    assert got["b"] == [] and got["c"] == ["plant"]
    assert "d" not in got and "zz" not in got           # 응답에 없는 칸 = 미조회로 남김, 요청 밖 칸은 버림
    q = f._batch_query([("a", 36.985, 126.69), ("b", 34.915, 127.755)])
    assert q.count("make c cell=") == 2 and 'cell="b"' in q and "around:750,34.915,127.755" in q


def test_enrich_osm_batches_caches_and_stops_on_failure():
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    cells = {}
    pts = [_pt(36.985 + i * 0.01, 126.69, "2026-10-18") for i in range(5)] + [_pt(34.915, 127.755, "2026-10-19")]
    f.update_cells(cells, pts, "2026-10-19")
    calls = []

    def boom(items):
        calls.append(len(items))
        raise OSError("502")
    assert f.enrich_osm(cells, now, lookup=boom, batch=2) == 0 and calls == [2]   # 작은 묶음 실패에서 멈춤
    assert all("osm_checked" not in c for c in cells.values())                 # 실패는 캐시하지 않는다

    def fake(items):
        calls.append(len(items))
        return {k: (["X"] if lat < 35 else []) for k, lat, lon in items[:-1]}  # 마지막 칸은 응답 누락
    calls.clear()
    pri = {f.cell_key(36.985, 126.69)}
    assert f.enrich_osm(cells, now, lookup=fake, priority=pri, batch=4) == 4 and calls == [4, 2]
    assert sum("osm_checked" in c for c in cells.values()) == 4                 # 누락 2칸은 미조회 유지
    assert cells[f.cell_key(36.985, 126.69)]["osm"] == []                      # 우선 칸이 첫 묶음에 들어갔다
    assert cells[f.cell_key(34.915, 127.755)]["osm"] == ["X"]
    calls.clear()
    f.enrich_osm(cells, now, lookup=fake, batch=4)
    assert calls == [2]                                                         # TTL 안 칸은 재조회 없음


def test_enrich_osm_splits_failed_batch():
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    cells = {}
    f.update_cells(cells, [_pt(36.0 + i * 0.01, 126.69, "2026-10-19") for i in range(24)], "2026-10-19")
    calls = []

    def flaky(items):  # 혼잡: 12칸 넘는 묶음은 504
        calls.append(len(items))
        if len(items) > 12:
            raise OSError("504")
        return {k: [] for k, _, _ in items}
    assert f.enrich_osm(cells, now, lookup=flaky, batch=24) == 24
    assert calls == [24, 12, 12]


def test_typed_keeps_hotspots_when_overpass_fails(tmp=None):
    import tempfile
    from pathlib import Path
    orig = f.osm_batch
    f.osm_batch = lambda items: (_ for _ in ()).throw(OSError("504"))
    try:
        with tempfile.TemporaryDirectory() as d:
            pts = [_pt(36.985, 126.69, "2026-10-19") | {"border": False}]
            cells = f.typed(pts, Path(d) / "cells.json", datetime(2026, 10, 19, 6, tzinfo=timezone.utc), lc=False)
            assert cells is not None and len(cells) == 1                       # 이력은 쌓인다
            assert pts[0]["kind"] == "unknown" and "OSM 시설 미조회" in pts[0]["kind_why"]
    finally:
        f.osm_batch = orig


# ── 토지피복·군사 시설 표지(10/28) ─────────────────────────────────────────────
def test_classify_splits_burn_by_land_cover():
    assert f.classify(1, [], False, (40, 70))[0] == "farm"
    assert f.classify(1, [], False, (10, 55))[0] == "wildfire"
    assert f.classify(2, [], False, (30, 60))[0] == "wildfire"
    assert f.classify(1, [], False, (50, 80))[0] == "urban"
    assert f.classify(5, [], False, (50, 80))[0] == "urban"                  # 지도에 없는 공장 — 반복이어도 시가지 열원
    assert f.classify(3, [], False, (40, 70))[0] == "unknown"                # 반복 농경지 = 그대로 미상
    assert f.classify(1, [], False, (80, 90))[0] == "burn"                   # 수역 등 = 종전 산불·소각
    kind, conf, why = f.classify(1, [], False, (40, 70))
    assert conf == "낮음" and any("농경지 70%" in w and "WorldCover" in w for w in why)
    kind, conf, why = f.classify(2, ["현대제철 당진공장"], False, (50, 90))  # 시설 근거가 우선
    assert (kind, conf) == ("fixed", "중")
    for k in ("urban", "farm", "wildfire"):
        assert "추정" in f.KIND_LABEL[k]


def test_land_cover_failure_is_not_a_class():
    kind, conf, why = f.classify(1, [], False, None)
    assert kind == "burn" and "토지피복 미조회" in why                       # 종전 동작 + 사유
    kind, _, why = f.classify(1, [], True, None)
    assert kind == "unknown" and "토지피복 미조회" in why


def test_north_uses_land_cover_with_low_confidence():
    k, c, why = f.classify(1, [], True, (40, 65))
    assert (k, c) == ("farm", "낮음") and any("농경지" in w for w in why)
    assert f.classify(2, [], True, (10, 80))[:2] == ("wildfire", "낮음")
    assert f.classify(3, [], True, (50, 70))[:2] == ("urban", "낮음")
    assert f.classify(1, [], True, (50, 70))[0] == "unknown"                 # 북측 시가지 1회는 미상
    assert f.classify(4, [], True, (40, 70))[0] == "unknown"
    # annotate: 북측 시가지 반복은 라벨을 따로(시설 열원 추정)
    lat, lon = 39.03, 125.75                                                 # 평양
    key = f.cell_key(lat, lon)
    cells = {key: {"lat": lat, "lon": lon, "dates": ["2026-10-20", "2026-10-22", "2026-10-25"], "osm": [], "mil": [],
                   "osm_checked": "2026-10-25", "lc": 50, "lc_pct": 72, "lc_checked": "2026-10-28"}}
    pts = [{"lat": lat, "lon": lon}]
    f.annotate(pts, cells)
    assert pts[0]["kind"] == "urban" and pts[0]["kind_label"] == f.NK_URBAN_LABEL and pts[0]["lc"] == 50
    assert any("시가지 72%" in w for w in pts[0]["kind_why"]) and "mil" not in pts[0]


def test_enrich_lc_caches_once_bounds_and_stops_on_failure():
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)
    cells = {}
    f.update_cells(cells, [_pt(36.0 + i * 0.01, 126.69, "2026-10-27") for i in range(5)], "2026-10-28")
    calls = []

    def boom(lat, lon):
        calls.append(lat)
        raise OSError("503")
    assert f.enrich_lc(cells, now, lookup=boom) == 0 and len(calls) == 3      # 3연속 실패에서 멈춘다
    assert all("lc_checked" not in c for c in cells.values())                 # 실패는 캐시하지 않는다

    def ok(lat, lon):
        calls.append(lat)
        return 40, 60
    calls.clear()
    assert f.enrich_lc(cells, now, lookup=ok, limit=2) == 2 and len(calls) == 2   # 회차 상한
    assert f.enrich_lc(cells, now, lookup=ok) == 3 and len(calls) == 5
    calls.clear()
    assert f.enrich_lc(cells, now, lookup=ok) == 0 and calls == []            # 한 번 읽은 칸은 다시 묻지 않는다
    assert all(c["lc"] == 40 and c["lc_pct"] == 60 for c in cells.values())


def test_wc_class_window_mode_and_tile_math():
    assert f.wc_tile_name(36.98, 126.71)[0] == "N36E126" and f.wc_tile_name(39.66, 124.71)[0] == "N39E123"
    assert f.wc_tile_name(41.28, 129.08)[0] == "N39E129"
    tile, la0, lo0 = f.wc_tile_name(38.5, 127.5)
    W = tw = 1024 * 2
    hd = {"url": "x", "W": W, "H": W, "tw": 1024, "th": 1024, "off": (0,) * 4, "cnt": (0,) * 4}
    x, y = int((127.5 - lo0) / 3 * W), int((la0 + 3 - 38.5) / 3 * W)
    idx = (y // 1024) * 2 + x // 1024
    buf = bytearray([40]) * (1024 * 1024)
    cx, cy = x % 1024, y % 1024
    for r in range(cy - 20, cy - 5):                                         # 창 일부만 시가지
        buf[r * 1024 + cx - 20: r * 1024 + cx + 21] = bytes([50]) * 41
    f._WC_HDR[tile], f._WC_TILE[(tile, idx)] = hd, bytes(buf)
    try:
        code, pct = f.wc_class(38.5, 127.5)
        assert code == 40 and 55 <= pct <= 70
        f._WC_TILE[(tile, idx)] = bytes(1024 * 1024)                          # 전부 자료 없음(0) → 실패
        try:
            f.wc_class(38.5, 127.5)
            assert False, "자료 없음은 분류가 아니다"
        except RuntimeError:
            pass
    finally:
        f._WC_HDR.pop(tile, None), f._WC_TILE.pop((tile, idx), None)


def test_military_in_same_overpass_request_and_split():
    q = f._batch_query([("a", 36.985, 126.69)])
    assert q.count("[landuse=military]") == 1 and "[military]" in q and q.count("make m cell=") == 1
    els = [
        {"type": "c", "id": 1, "tags": {"cell": "a", "n": "0"}},
        {"type": "m", "id": 2, "tags": {"cell": "a", "n": "2", "ko": "", "nm": "제0000부대", "en": ""}},
        {"type": "c", "id": 3, "tags": {"cell": "b", "n": "0"}},
        {"type": "m", "id": 4, "tags": {"cell": "b", "n": "1", "ko": "", "nm": "", "en": ""}},
        {"type": "m", "id": 5, "tags": {"cell": "c", "n": "0"}},
    ]
    assert f.split_batch(els, ["a", "b", "c"]) == {"a": [], "b": []}           # 군사 요약은 시설로 세지 않는다
    assert f.split_mil(els, ["a", "b", "c"]) == {"a": ["제0000부대"], "b": [f.MIL_TAG_KO], "c": []}


def test_enrich_osm_requeries_cells_without_mil_once():
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)
    k = f.cell_key(36.985, 126.69)
    cells = {k: {"lat": 36.985, "lon": 126.69, "dates": ["2026-10-27"], "osm": [], "osm_checked": "2026-10-20"}}
    got = []

    def look(items):
        got.append(len(items))
        return {kk: [] for kk, _, _ in items}, {kk: ["제0000부대"] for kk, _, _ in items}
    assert f.enrich_osm(cells, now, lookup=look) == 1 and cells[k]["mil"] == ["제0000부대"]
    assert f.enrich_osm(cells, now, lookup=look) == 0 and got == [1]           # mil 채워지면 TTL 안 재조회 없음


def test_military_flag_never_changes_kind():
    base = {"dates": ["2026-10-27"], "osm": [], "osm_checked": "2026-10-27", "lc": 10, "lc_pct": 80, "lc_checked": "2026-10-28"}
    # 풍계리(places.json 41.28, 129.08) 약 2km — 북측 주요 시설 거리 표지
    lat, lon = 41.29, 129.06
    a = [{"lat": lat, "lon": lon}]
    f.annotate(a, {f.cell_key(lat, lon): dict(base, lat=lat, lon=lon, mil=[])})
    assert a[0]["kind"] == "wildfire" and "풍계리(핵실험장)" in a[0]["mil"]
    assert any(w.startswith("군사 시설 인근: 풍계리") and w.endswith("보도 대조 필요") for w in a[0]["kind_why"])
    # 남측 OSM 군사 시설 — 같은 칸 근거에 mil 만 다르면 유형·신뢰도 동일
    lat, lon = 37.9, 127.2
    b = [{"lat": lat, "lon": lon}]
    c = [{"lat": lat, "lon": lon}]
    f.annotate(b, {f.cell_key(lat, lon): dict(base, lat=lat, lon=lon, mil=["제0000부대"])})
    f.annotate(c, {f.cell_key(lat, lon): dict(base, lat=lat, lon=lon, mil=[])})
    assert (b[0]["kind"], b[0]["kind_conf"]) == (c[0]["kind"], c[0]["kind_conf"]) == ("wildfire", "낮음")
    assert b[0]["mil"] == "제0000부대(OSM 750m 이내)" and "mil" not in c[0]
    far = [{"lat": 41.40, "lon": 129.08}]                                     # 풍계리 약 13km — 표지 없음
    f.annotate(far, {})
    assert "mil" not in far[0]
    assert {n.split("(")[0] for n, _, _ in f.nk_sites()} == set(f.NK_SITES)  # places.json 에 4곳 모두 있다

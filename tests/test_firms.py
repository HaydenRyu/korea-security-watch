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


def test_enrich_osm_caches_and_stops_on_failure():
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    cells = {}
    f.update_cells(cells, [_pt(36.985, 126.69, "2026-10-18"), _pt(34.915, 127.755, "2026-10-19")], "2026-10-19")
    calls = []

    def boom(lat, lon):
        calls.append((lat, lon))
        raise OSError("502")
    assert f.enrich_osm(cells, now, lookup=boom) == 0 and len(calls) == 1      # 첫 실패에서 멈춤
    assert all("osm_checked" not in c for c in cells.values())                 # 실패는 캐시하지 않는다
    f.time.sleep, orig = (lambda s: None), f.time.sleep
    try:
        assert f.enrich_osm(cells, now, lookup=lambda a, b: ["X"]) == 2
        assert f.enrich_osm(cells, now, lookup=boom) == 0                       # TTL 안 → 재조회 없음
    finally:
        f.time.sleep = orig
    pts = [_pt(36.985, 126.69, "2026-10-18")]
    f.annotate(pts, cells)
    assert pts[0]["kind"] == "fixed" and pts[0]["kind_label"] == "상시 열원 추정" and pts[0]["north"] is False

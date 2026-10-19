import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import navwarn_parse as m  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "nga_hydropac_2023_2024.json.gz"
_RECS = None


def recs():
    global _RECS
    if _RECS is None:
        _RECS = m.from_nga(str(FIX))
    return _RECS


def test_fixture_is_full_archive():
    assert len(recs()) == 5643          # 2023 4,070 + 2024 1,573 — 범위를 좁히면 「오탐 0」이 의미를 잃는다


def test_dprk_pattern_finds_exactly_2023_satellite_notices():
    hits = sorted((r["relay_of"].split()[2], r["window_from"], r["window_to"]) for r in recs() if r["dprk_pattern"])
    assert hits == [
        ("187/23", "2023-05-30T15:00:00+00:00", "2023-06-10T15:00:00+00:00"),
        ("288/23", "2023-08-23T15:00:00+00:00", "2023-08-30T15:00:00+00:00"),
        ("408/23", "2023-11-21T15:00:00+00:00", "2023-11-30T15:00:00+00:00"),
    ]


def test_korea_launch_false_positives_are_russian_missile_ops():
    other = [r for r in recs() if r["korea_related"] and r["launch_related"] and not r["dprk_pattern"]]
    assert len(other) == 13
    assert all("MISSILE OPERATIONS" in r["text_excerpt"] and "RUSSIA" in r["text_excerpt"] for r in other)


def test_coordinate_formats():
    assert m.coords("36-02.44N 123-59.18E") == [(36.0407, 123.9863)]
    assert m.coords("北緯33度27分23秒　東経130度02分10秒") == [(33.4564, 130.0361)]
    assert m.coords("38−19．86S  144−19．61E") == [(-38.331, 144.3268)]
    assert m.coords("34-18-00.0N,128-00-00.0E") == [(34.3, 128.0)]


def test_window_dtg():
    from datetime import datetime, timezone
    iss = datetime(2023, 11, 20, tzinfo=timezone.utc)
    assert m.parse_window("211500Z TO 301500Z NOV IN AREAS", iss) == ("2023-11-21T15:00:00+00:00", "2023-11-30T15:00:00+00:00")
    assert m.parse_window("301500Z MAY TO 101500Z JUN", iss)[0].startswith("2023-05-30")

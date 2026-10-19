"""공역 스냅샷(10/20): 궤적 잇기·사진 캐시·기종 포장 — 네트워크 없이."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cctv_snap as cs  # noqa: E402


def test_merge_trail_appends_and_expires():
    now = 10_000
    prev = {"A": [[now - 40 * 60, 37.0, 127.0], [now - 300, 37.1, 127.1]], "GONE": [[now - 3000, 36, 126]]}
    ac = [{"hex": "A", "lat": 37.2, "lon": 127.2}, {"hex": "B", "lat": 35.0, "lon": 128.0}, {"hex": None, "lat": 1, "lon": 1}]
    t = cs.merge_trail(prev, ac, now)
    assert [p[1:] for p in t["A"]] == [[37.1, 127.1], [37.2, 127.2]]   # 35분 넘은 점은 버린다
    assert t["B"] == [[now, 35.0, 128.0]] and "GONE" not in t
    # 같은 자리면 점을 늘리지 않는다(지상 대기)
    assert len(cs.merge_trail(t, [{"hex": "B", "lat": 35.0, "lon": 128.0}], now + 300)["B"]) == 1


def test_photo_entry_prefers_large_thumbnail_with_credit():
    doc = {"photos": [{"thumbnail": {"src": "s"}, "thumbnail_large": {"src": "L"}, "link": "u", "photographer": "P"}]}
    assert cs.photo_entry(doc) == {"src": "L", "link": "u", "by": "P"}
    assert cs.photo_entry({"photos": []}) is None and cs.photo_entry(None) is None


def test_photos_for_respects_cache_and_budget():
    now = 1_000_000
    calls = []

    def fetch(h):
        calls.append(h)
        return {"photos": []} if h == "N" else {"photos": [{"thumbnail_large": {"src": h}, "link": "l", "by": "b"}]}
    cache = {"OLD": {"p": {"src": "x"}, "at": now - 31 * 86400}, "NEG": {"p": None, "at": now - 100}, "OK": {"p": {"src": "y"}, "at": now - 100}}
    out = cs.photos_for(["OK", "NEG", "OLD", "A", "B", "N"], cache, now, fetch=fetch, max_new=3)
    assert calls == ["OLD", "A", "B"]           # 유효 캐시는 건너뛰고 회차 한도에서 멈춘다
    assert out["OLD"]["at"] == now and out["A"]["p"]["src"] == "A" and "N" not in out


def test_photos_for_skips_failed_lookup():
    def boom(h):
        raise OSError("down")
    assert cs.photos_for(["A"], {}, 5, fetch=boom) == {}   # 실패는 캐시에 안 남겨 다음 회차에 다시


def test_pack_keeps_hex_and_ground():
    a = {"hex": "71BE10", "lat": 37.1234, "lon": 127.5678, "track": 90, "alt_baro": "ground", "t": "B748", "flight": "KAL591 @"}
    e = cs._pack(a, False, None)
    assert e["hex"] == "71BE10" and e["alt"] == 0 and e["call"] == "KAL591" and e["cc"] == "KR" and e["mil"] is None

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

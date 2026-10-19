import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import coverage_placeholder_check as m  # noqa: E402


def test_item_key_in_query_is_placeholder():
    cov = {"border_clash": ["북한 border_clash 동향", "North Korea border_clash"],
           "nuclear": ["풍계리 핵실험장 위성", "North Korea nuclear test site"],
           "nk_internal": ["북한 nk_internal 동향", "평양 전시태세"]}
    bad = m.placeholder_queries(cov)
    assert bad == {"border_clash": ["북한 border_clash 동향", "North Korea border_clash"],
                   "nk_internal": ["북한 nk_internal 동향"]}


def test_real_queries_and_odd_shapes_pass():
    assert m.placeholder_queries({"border_clash": ["지뢰 발견 해안", "DMZ 귀순"], "logistics": "철도 수송 북한"}) == {}
    assert m.placeholder_queries({}) == {} and m.placeholder_queries(None) == {}
    assert m.placeholder_queries({"nuclear": ["nuclear_test_site"]}) == {}  # 키 일부만 같은 다른 낱말은 아니다


def test_word_keys_only_as_template():
    bad = m.placeholder_queries({"nuclear": ["북한 nuclear 동향", "North Korea nuclear", "North Korea nuclear test"],
                                 "logistics": ["North Korea logistics rail"]})
    assert bad == {"nuclear": ["북한 nuclear 동향"]}  # 영어 「North Korea nuclear」는 실제 검색어일 수 있다

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import imagery_feeds as m  # noqa: E402

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Several Naval Construction Projects Underway</title>
 <link>https://www.38north.org/2026/10/several-naval-construction-projects-underway/</link>
 <pubDate>Fri, 02 Oct 2026 13:31:30 +0000</pubDate>
 <description><![CDATA[<img src="https://www.38north.org/x.jpg"> long body]]></description>
 <enclosure url="https://www.38north.org/x.jpg" type="image/jpeg"/></item>
<item><title>no scheme</title><link>http://example.org/a</link><pubDate>bad</pubDate></item>
</channel></rss>"""


def test_parse_keeps_title_link_date_only():
    got = m.parse(RSS, "38north")
    assert len(got) == 1                                     # http 링크 제외
    assert set(got[0]) == {"source", "title", "link", "date"}   # 본문·이미지 필드 없음
    assert got[0]["date"] == "2026-10-02T13:31:30+00:00"


def test_merge_dedupes_by_link_and_caps():
    a = [{"source": "s", "title": "old", "link": "https://x/1", "date": "2026-01-01"}]
    b = [{"source": "s", "title": "new", "link": "https://x/1", "date": "2026-01-01"},
         {"source": "s", "title": "n2", "link": "https://x/2", "date": "2026-02-01"}]
    out = m.merge(a, b, keep=5)
    assert [x["link"] for x in out] == ["https://x/2", "https://x/1"] and out[1]["title"] == "new"
    assert len(m.merge(a, b, keep=1)) == 1

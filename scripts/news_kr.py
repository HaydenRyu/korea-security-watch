"""국내 주요 언론 RSS → 안보 키워드 후보 기사 묶음

  python3 scripts/news_kr.py --public [--out state/news_kr.json]     # 워크플로(firms.yml): 건수·상태만 — 레포에 올라간다
  python3 scripts/news_kr.py --out news_kr.local.json                # 감시 루틴: 후보 기사 목록 — 레포에 올리지 않는다(.gitignore)
  공통 옵션: [--sources data/sources.json] [--keywords data/news_keywords.json]

- 매체·피드 정본 = data/sources.json 의 항목 중 `feeds` 가 있는 것(등급 B). 키워드 정본 = data/news_keywords.json.
  제외 매체와 사유는 sources.json `news_feed_audit` 에 있다(약관이 자동 수집을 금지하거나 robots.txt 가 막는 곳).
- 채택 매체의 RSS 는 「개인·비상업 구독」만 허용한다(연합·SBS·동아 RSS 안내, 온라인신문협회 이용규칙: 공중 배포·재RSS 금지).
  그래서 **제목·링크는 공개 레포에 쓰지 않는다** — `--public` 은 매체·피드별 건수와 상태만 남긴다.
  전체 목록(제목·링크·시각·적중어)은 루틴 세션 안에서만 만들고 버린다.
- 어느 모드든 피드 요약(description)은 키워드 대조와 통신사 전재 표시에만 메모리에서 쓰고 저장하지 않는다 — 본문·요약 전재 없음.
- 같은 링크는 한 기사, 제목이 거의 같은 기사(글자 2-gram 겹침)는 매체를 넘어 한 묶음(cluster)으로 묶는다.
- 이전 파일과 링크 기준으로 합쳐 최근 WINDOW_H 시간만 남긴다. 한 피드가 실패해도 나머지는 계속하고, 실패는 feeds[].error 와
  최상위 status(ok|partial|error)에 남긴다. 실패가 하나라도 있으면 rc 3(워크플로는 continue-on-error).
- 판정은 하지 않는다. 감시 루틴(prompts/monitor.md 5절 0-2)이 이 목록에서 후보를 골라 원문을 열어 판정한다.
"""
from __future__ import annotations

import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_PUBLIC = ROOT / "state" / "news_kr.json"   # 레포에 올라간다 — 건수·상태만
OUT_LOCAL = ROOT / "news_kr.local.json"        # 루틴 세션 전용 — .gitignore
SOURCES = ROOT / "data" / "sources.json"
KEYWORDS = ROOT / "data" / "news_keywords.json"
UA = "korea-security-watch/1.0 (personal OSINT dashboard; titles and links only; +https://haydenryu.github.io/korea-security-watch/)"
WINDOW_H = 72          # 이 시간보다 오래된 기사는 버린다(발행 시각, 없으면 처음 본 시각 기준)
CLUSTER_GAP_H = 36     # 같은 사건으로 묶는 발행 시각 차 상한
SIM_MIN = 0.5          # 제목 2-gram 겹침 계수(|A∩B|/min) 문턱
FUTURE_SKEW = timedelta(minutes=10)
TIMEOUT_S = 10
BUDGET_S = 90          # 피드 전체 시간 예산 — 넘으면 남은 피드는 실패로 적고 끝낸다(워크플로 스텝 상한 3분 안)
WIRES = ("연합뉴스", "뉴시스", "뉴스1")
WIRE_RE = re.compile(r"[\(\[]\s*[^\)\]\s=]{1,12}\s*=\s*(연합뉴스|뉴시스|뉴스1)\s*[\)\]]")
DROP_PARAMS = re.compile(r"^(utm_.*|plink|cooper|from|ref|rss|sns|section_code)$", re.I)


# ── 입력 ─────────────────────────────────────────────────────────────
def load_feeds(sources: dict) -> list[dict]:
    """sources.json 에서 feeds 가 있는 항목 → [{outlet, tier, section, url}]."""
    out = []
    for tier in sources.get("tiers", []):
        for g in tier.get("groups", []):
            for it in g.get("items", []):
                for f in it.get("feeds") or []:
                    out.append({"outlet": it["name"], "tier": tier.get("id"), "section": f.get("section", ""), "url": f["url"]})
    return out


def fetch(url: str) -> str:
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"})
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                if r.status != 200:
                    raise OSError(f"HTTP {r.status}")
                return r.read().decode("utf-8", "replace")
        except OSError:
            if attempt == 1:
                raise
            time.sleep(3)
    raise RuntimeError("unreachable")


# ── 파싱(정규식 — 매체 피드가 XML 규칙을 자주 어겨서 관대하게 읽는다) ─────────
def _txt(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", s, flags=re.S)
    for _ in range(2):  # 이스케이프된 태그(&lt;p&gt;)·이중 이스케이프(&amp;#91;) 피드가 있다
        s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _field(block: str, *tags: str) -> str:
    for t in tags:
        m = re.search(rf"<{t}(?:\s[^>]*)?>(.*?)</{t}>", block, re.S)
        if m and _txt(m.group(1)):
            return m.group(1)
    return ""


def parse_time(s: str, now: datetime) -> str | None:
    s = _txt(s)
    if not s:
        return None
    t = None
    try:
        t = parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        try:
            t = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if t is None:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone(timedelta(hours=9)))  # 시간대 없는 국내 피드는 KST
    if t - now > FUTURE_SKEW:
        return None  # 미래 시각(엠바고·시간대 오표기)은 믿지 않는다 — 처음 본 시각으로 대신한다
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def canon_link(u: str) -> str:
    u = _txt(u)
    p = urllib.parse.urlsplit(u)
    scheme = "https" if p.scheme == "http" else p.scheme  # 국방일보 피드는 http 링크를 준다(https 로 열린다)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True) if not DROP_PARAMS.match(k)]
    return urllib.parse.urlunsplit((scheme, p.netloc, p.path, urllib.parse.urlencode(q), ""))


def parse_feed(text: str, now: datetime) -> list[dict]:
    """RSS·Atom → [{title, link, published, desc}] (desc 는 메모리 전용 — 저장하지 않는다)."""
    out = []
    for b in re.findall(r"<item[\s>](.*?)</item>|<entry[\s>](.*?)</entry>", text, re.S):
        b = b[0] or b[1]
        link = _field(b, "link")
        if not link:
            m = re.search(r"<link[^>]*href=[\"']([^\"']+)", b)
            link = m.group(1) if m else ""
        title, link = _txt(_field(b, "title")), canon_link(link)
        if not (title and link.startswith("https://")):
            continue
        desc = _txt(_field(b, "description", "summary", "content:encoded"))
        out.append({"title": title, "link": link,
                    "published": parse_time(_field(b, "pubDate", "dc:date", "published", "updated"), now),
                    "desc": desc[:2000]})
    return out


# ── 키워드 ───────────────────────────────────────────────────────────
def match(title: str, desc: str, kw: dict) -> dict | None:
    """→ {groups, items, terms} 또는 None. exclude 는 제목만, strong·context 는 제목+요약, weak 은 제목에서만 본다."""
    if any(x in title for x in kw.get("exclude", [])):
        return None
    text = f"{title} {desc}"
    ctx = any(c in text for c in kw.get("context", []))
    groups, items, terms = [], [], []
    for g in kw.get("groups", []):
        hit = [t for t in g.get("strong", []) if t in text]
        if ctx:
            hit += [t for t in g.get("weak", []) if t in title]
        if hit:
            groups.append(g["id"])
            if g.get("item") and g["item"] not in items:
                items.append(g["item"])
            terms += [t for t in hit if t not in terms]
    return {"groups": groups, "items": items, "terms": terms} if groups else None


def wire_of(outlet: str, title: str, desc: str) -> str | None:
    """다른 매체가 통신사 기사를 받아 실은 것이면 그 통신사 이름(교차확인 때 1곳으로 센다)."""
    m = WIRE_RE.search(f"{title} {desc[:300]}")
    return m.group(1) if m and m.group(1) != outlet else None


# ── 묶음 ─────────────────────────────────────────────────────────────
def _grams(title: str) -> set[str]:
    t = re.sub(r"\[[^\]]{0,12}\]|\([^)]{0,12}\)|속보|단독|종합|포토|영상", " ", title)
    t = re.sub(r"[^\w가-힣]", "", t.replace("北", "북한"))
    return {t[i:i + 2] for i in range(len(t) - 1)}


def similar(a: str, b: str) -> float:
    ga, gb = _grams(a), _grams(b)
    return len(ga & gb) / min(len(ga), len(gb)) if ga and gb else 0.0


def _when(a: dict) -> str:
    return a.get("published") or a.get("first_seen") or ""


def cluster(articles: list[dict]) -> list[dict]:
    arts = sorted(articles, key=lambda a: (_when(a), a["link"]))
    parent = list(range(len(arts)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(arts)):
        for j in range(i + 1, len(arts)):
            a, b = arts[i], arts[j]
            if not set(a["groups"]) & set(b["groups"]):
                continue
            ta, tb = _when(a), _when(b)
            if ta and tb and abs(datetime.fromisoformat(ta) - datetime.fromisoformat(tb)) > timedelta(hours=CLUSTER_GAP_H):
                continue
            if similar(a["title"], b["title"]) >= SIM_MIN:
                parent[root(j)] = root(i)
    by: dict[int, list[dict]] = {}
    for i, a in enumerate(arts):
        by.setdefault(root(i), []).append(a)
    out = []
    for members in by.values():
        first = members[0]
        outlets = sorted({m["outlet"] for m in members})
        independent = sorted({m.get("wire_copy") or m["outlet"] for m in members})
        groups = sorted({g for m in members for g in m["groups"]})
        items = sorted({x for m in members for x in m["items"]})
        out.append({"id": first["link"], "title": first["title"], "groups": groups, "items": items,
                    "first": _when(first), "last": max(_when(m) for m in members),
                    "n_outlets": len(outlets), "outlets": outlets, "independent": independent,
                    "articles": [{k: m[k] for k in ("outlet", "title", "link", "published", "first_seen", "groups", "items",
                                                    "terms", "wire_copy") if m.get(k)} for m in members]})
    out.sort(key=lambda c: (c["last"], c["n_outlets"]), reverse=True)
    return out


# ── 실행 ─────────────────────────────────────────────────────────────
def collect(feeds: list[dict], kw: dict, prev: dict, now: datetime, fetcher=fetch) -> dict:
    now_s = now.isoformat(timespec="seconds")
    # 이전 적중분을 이어 간다(피드에서 밀려나도 WINDOW_H 동안 남는다). 공개 파일에는 기사가 없으니 이어 갈 것도 없다
    articles = {a["link"]: dict(a) for c in prev.get("clusters", []) for a in c.get("articles", []) if a.get("title")}
    stat, fetched, start = [], 0, time.time()
    for f in feeds:
        t0 = time.time()
        row = {"outlet": f["outlet"], "section": f["section"], "url": f["url"]}
        if t0 - start > BUDGET_S:
            row.update(ok=False, error=f"시간 예산 {BUDGET_S}s 초과로 건너뜀")
            stat.append(row)
            continue
        try:
            got = parse_feed(fetcher(f["url"]), now)
            if not got:
                raise ValueError("항목 0건(형식 변경·빈 응답)")
        except Exception as e:  # noqa: BLE001 — 실패 피드는 기록만, 나머지는 계속
            row.update(ok=False, error=f"{type(e).__name__}: {e}"[:200])
            stat.append(row)
            continue
        hits = 0
        for g in got:
            fetched += 1
            m = match(g["title"], g["desc"], kw)
            if not m:
                continue
            hits += 1
            a = articles.get(g["link"])
            if a is None:
                a = articles[g["link"]] = {"outlet": f["outlet"], "title": g["title"], "link": g["link"],
                                           "published": g["published"], "first_seen": now_s}
            a.update(groups=m["groups"], items=m["items"], terms=m["terms"])
            a["published"] = a.get("published") or g["published"]
            w = wire_of(f["outlet"], g["title"], g["desc"])
            if w:
                a["wire_copy"] = w
        row.update(ok=True, count=len(got), hits=hits, ms=int((time.time() - t0) * 1000))
        stat.append(row)
    cutoff = (now - timedelta(hours=WINDOW_H)).isoformat(timespec="seconds")
    kept = [a for a in articles.values() if a.get("groups") and _when(a) >= cutoff]
    clusters = cluster(kept)
    failed = [s for s in stat if not s["ok"]]
    status = "ok" if not failed else ("error" if len(failed) == len(stat) else "partial")
    outlets: dict[str, dict] = {}
    for s in stat:
        o = outlets.setdefault(s["outlet"], {"feeds": 0, "failed": 0, "count": 0, "hits": 0})
        o["feeds"] += 1
        o["failed"] += 0 if s["ok"] else 1
        o["count"] += s.get("count", 0)
        o["hits"] += s.get("hits", 0)
    by_group: dict[str, int] = {}
    for c in clusters:
        for g in c["groups"]:
            by_group[g] = by_group.get(g, 0) + 1
    return {
        "fetched_at": now_s,
        "status": status,
        "status_detail": "; ".join(f"{s['outlet']}/{s['section']}: {s['error']}" for s in failed)[:1000],
        "last_ok_at": now_s if status == "ok" else prev.get("last_ok_at"),
        "window_h": WINDOW_H,
        "note": "루틴 세션 전용(레포에 올리지 않는다). 제목·링크·시각·적중어만, 본문·요약 전재 없음. 판정 아님 — 루틴이 원문을 열어 판정한다. "
                "independent = 통신사 전재를 통신사 1곳으로 접은 매체 목록(교차확인 계수용).",
        "counts": {"feeds": len(stat), "feeds_failed": len(failed), "fetched": fetched,
                   "hits": len(kept), "clusters": len(clusters), "by_group": by_group},
        "outlets": outlets,
        "feeds": stat,
        "clusters": clusters,
    }


def public(doc: dict) -> dict:
    """레포에 올리는 판 — 제목·링크 없이 건수·상태만(RSS 는 개인 구독 한정, 공중 배포 금지)."""
    groups: dict[str, dict] = {}
    for c in doc.get("clusters", []):
        for g in c["groups"]:
            x = groups.setdefault(g, {"clusters": 0, "max_independent": 0, "latest": ""})
            x["clusters"] += 1
            x["max_independent"] = max(x["max_independent"], len(c["independent"]))
            x["latest"] = max(x["latest"], c["last"])
    keep = ("fetched_at", "status", "status_detail", "last_ok_at", "window_h", "counts", "outlets")
    return {**{k: doc[k] for k in keep if k in doc},
            "note": "공개판: 매체·피드별 건수와 상태만. 기사 제목·링크는 싣지 않는다(채택 매체 RSS 는 개인·비상업 구독만 허용). "
                    "후보 기사 목록은 감시 루틴이 세션 안에서 `python3 scripts/news_kr.py` 로 만든다.",
            "groups": groups,
            "feeds": [{k: f[k] for k in ("outlet", "section", "url", "ok", "count", "hits", "error") if k in f}
                      for f in doc.get("feeds", [])]}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    arg = lambda k, d: Path(argv[argv.index(k) + 1]) if k in argv else d  # noqa: E731
    is_public = "--public" in argv
    out = arg("--out", OUT_PUBLIC if is_public else OUT_LOCAL)
    src, kwp = arg("--sources", SOURCES), arg("--keywords", KEYWORDS)
    try:
        prev = json.loads(out.read_text()) if out.exists() else {}
    except ValueError:
        prev = {}
    now = datetime.now(timezone.utc)
    try:
        feeds, kw = load_feeds(json.loads(src.read_text())), json.loads(kwp.read_text())
        if not feeds:
            raise ValueError("sources.json 에 feeds 가 있는 매체가 없다")
        doc = collect(feeds, kw, prev, now)
    except Exception as e:  # noqa: BLE001 — 정본 파일 손상: 직전 목록은 두고 status 만 바꾼다
        doc = {**prev, "fetched_at": now.isoformat(timespec="seconds"), "status": "error",
               "status_detail": f"{type(e).__name__}: {e}"[:300]}
    if is_public:
        doc = public(doc)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    c = doc.get("counts", {})
    print(f"news_kr: status={doc['status']} · 피드 {c.get('feeds', 0)}(실패 {c.get('feeds_failed', 0)}) · 받은 {c.get('fetched', 0)}"
          f" · 적중 {c.get('hits', 0)} · 묶음 {c.get('clusters', 0)} {c.get('by_group', {})} → {out.name}")
    if doc["status"] != "ok":
        print(f"::warning title=news_kr::{doc['status']} — {doc.get('status_detail', '')}")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())

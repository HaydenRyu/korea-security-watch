// JIS 오프라인 캐시 — 통신이 끊기거나 몰려도 감시판·대피소·행동요령을 다시 열 수 있게 한다.
//
// 범위: 이 파일이 레포 루트(= Pages 의 /korea-security-watch/)에 있으므로 기본 scope 도 그 아래뿐이다.
//       경로는 전부 이 파일 위치 기준 상대경로로 적는다("/index.html" 처럼 / 로 시작하면 Pages 에서 도메인 루트로 간다).
//
// ── 새 배포 시 캐시 갱신 ──────────────────────────────────────────────
// 1) 아래 VERSION 의 날짜·순번을 올린다(예: jis-2026-10-18a → jis-2026-10-19a).
//    sw.js 가 1바이트라도 바뀌어야 브라우저가 새 워커를 설치하고, activate 때 이전 VERSION 캐시를 지운다.
// 2) PRECACHE 에 새 정적 파일을 더했으면 목록에도 적는다(없는 파일이 있으면 설치가 실패해 이전 캐시가 계속 쓰인다).
// 3) HTML·data 는 네트워크 우선이라 VERSION 을 안 올려도 온라인이면 새 내용이 보인다.
//    VERSION 을 올려야 하는 경우 = 이 파일의 전략을 바꿨거나, CDN 자산 버전(Leaflet 등)을 바꿨을 때.
//
// ── 전략 ─────────────────────────────────────────────────────────────
// · 페이지(index·shelter)·data/*·state/* : 네트워크 우선(시간 제한 NET_TIMEOUT_MS) → 실패·지연 시 캐시.
//   통신이 '몰려서 느린' 상황에서 무한정 기다리지 않도록 시간 제한을 둔다. 늦게 온 응답도 캐시는 갱신한다.
//   ?t=... 캐시버스터가 붙은 요청은 쿼리를 뗀 주소로 저장·조회한다(안 그러면 매번 새 키라 오프라인에 못 찾는다).
// · CDN 자산(Leaflet·Pretendard·Martian Mono) : 캐시 우선. 주소에 버전이 박혀 있어 내용이 안 바뀐다.
// · 지도 타일 : 캐시하지 않는다.
//   - NASA GIBS 위성 타일은 주소에 날짜가 들어가 매일 바뀐다 → 캐시해도 다음 날엔 안 쓰이고 용량만 쌓인다.
//   - OSM 타일은 사용 정책상 대량 선다운로드(오프라인용 미리 받기)를 금지한다.
//     자곡동 반경 z13~16 만 받아도 수백 장(수 MB)이고, 그마저 정책 위반이다.
//   - 대신 shelter.html 이 외부 타일 없는 SVG 지도(대피소 점·기준점·축척)를 그린다. 오프라인 지도 역할은 그쪽이 한다.
// · 그 밖(goatcounter·ntfy·CCTV relay·항공기 등 외부 API, POST) : 손대지 않는다.
//
// ── 웹 푸시 ──────────────────────────────────────────────────────────
// 상황판 「알림 받기」로 구독한 방문자에게 Worker(jis-cctv /push/send)가 보낸 등급 알림을 띄운다.
// 무엇을 언제 보내는지는 scripts/diff_state.py 의 outbox.public — 주의 이상 진입·새 징후·평시 복귀.

const VERSION = "jis-2026-10-19a";
const NET_TIMEOUT_MS = 4000;

const PRECACHE = [
  "index.html", "shelter.html", "manifest.webmanifest",
  "data/prep.json", "data/shelters.json", "data/places.json", "data/dmz.json",
  "data/cctv.json", "data/sources.json", "data/context_2026.json",
  "state/latest.json", "state/events.jsonl", "state/firms.json", "state/air.json",
  "assets/favicon.svg", "assets/favicon-32.png", "assets/apple-touch-icon.png", "assets/jis-mark.svg",
];
// CDN 은 실패해도 설치를 막지 않는다(로컬 파일만 있으면 대피 안내는 읽힌다)
const CDN = [
  "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css",
  "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js",
  "https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css",
];
// 캐시 우선으로 런타임 저장할 외부 호스트(글꼴 조각 파일 포함)
const CDN_HOSTS = ["cdnjs.cloudflare.com", "cdn.jsdelivr.net", "fonts.googleapis.com", "fonts.gstatic.com"];

const SCOPE = new URL("./", self.location).href;
const key = url => { const u = new URL(url); u.search = ""; u.hash = ""; return u.href; };

self.addEventListener("install", e => {
  e.waitUntil((async () => {
    const c = await caches.open(VERSION);
    await c.addAll(PRECACHE.map(p => new Request(new URL(p, SCOPE), { cache: "reload" })));
    await Promise.allSettled(CDN.map(async u => {
      const r = await fetch(u, { mode: "cors" });
      if (r.ok) await c.put(u, r);
    }));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", e => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k.startsWith("jis-") && k !== VERSION) await caches.delete(k);
    await self.clients.claim();
  })());
});

async function networkFirst(req) {
  const c = await caches.open(VERSION);
  let k = key(req.url);
  if (k === SCOPE) k = new URL("index.html", SCOPE).href;  // 디렉터리 주소(/korea-security-watch/)는 index.html 로 저장
  const net = fetch(req).then(r => { if (r.ok) c.put(k, r.clone()); return r; });
  net.catch(() => {});  // 시간 제한에 진 뒤의 실패는 조용히 버린다
  const timeout = new Promise(res => setTimeout(() => res(null), NET_TIMEOUT_MS));
  try {
    const r = await Promise.race([net, timeout]);
    if (r) return r;
  } catch (_) { /* 오프라인 — 아래 캐시로 */ }
  const hit = await c.match(k);
  if (hit) return hit;
  try { return await net; } catch (_) {}  // 캐시도 없으면 늦더라도 네트워크를 기다린다
  if (req.mode === "navigate") { const s = await c.match(new URL("shelter.html", SCOPE).href); if (s) return s; }
  return Response.error();
}

async function cacheFirst(req) {
  const c = await caches.open(VERSION);
  const hit = await c.match(req.url);
  if (hit) return hit;
  const r = await fetch(req);
  if (r.ok || r.type === "opaque") c.put(req.url, r.clone());
  return r;
}

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const u = new URL(req.url);
  if (u.href.startsWith(SCOPE)) {
    if (u.pathname.endsWith("/sw.js")) return;
    e.respondWith(networkFirst(req));
  } else if (CDN_HOSTS.includes(u.hostname)) {
    e.respondWith(cacheFirst(req));
  }
});

// ── 웹 푸시 수신 ──
const PUSH_API = "https://jis-cctv.jis-probe.workers.dev/push/";
const ICON = new URL("assets/apple-touch-icon.png", SCOPE).href;  // 안드로이드 알림은 SVG 아이콘을 못 그린다 — PNG

self.addEventListener("push", e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = { body: e.data ? e.data.text() : "" }; }
  const url = typeof d.url === "string" && d.url.startsWith("https://haydenryu.github.io/") ? d.url : SCOPE;
  e.waitUntil(self.registration.showNotification(d.title || "JIS 알림", {
    body: d.body || "상황판에서 확인하세요.", icon: ICON, badge: new URL("assets/favicon-32.png", SCOPE).href,
    tag: d.tag || "jis-grade", renotify: true, requireInteraction: (d.priority || 3) >= 5, data: { url },
  }));
});

self.addEventListener("notificationclick", e => {
  e.notification.close();
  const url = e.notification.data?.url || SCOPE;
  e.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const w = wins.find(c => c.url.startsWith(SCOPE) && !/shelter|forecast|live/.test(c.url)) || wins.find(c => c.url.startsWith(SCOPE));
    if (w) { await w.focus(); if (w.navigate && w.url !== url) { try { await w.navigate(url); } catch (_) {} } return; }
    await self.clients.openWindow(url);
  })());
});

// 브라우저가 구독을 갈아 끼우면(만료·키 교체) 새 구독을 다시 등록한다 — 안 그러면 조용히 알림이 끊긴다
self.addEventListener("pushsubscriptionchange", e => {
  e.waitUntil((async () => {
    const key = e.oldSubscription?.options?.applicationServerKey;
    const sub = e.newSubscription || (key && await self.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key }));
    if (e.oldSubscription) await fetch(PUSH_API + "unsubscribe", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint: e.oldSubscription.endpoint }) }).catch(() => {});
    if (sub) await fetch(PUSH_API + "subscribe", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(sub.toJSON()) }).catch(() => {});
  })());
});

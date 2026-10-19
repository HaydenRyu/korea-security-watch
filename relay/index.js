// POST /upload (Bearer UPLOAD_TOKEN) → KV "bundle" 한 키(무료 쓰기 하루 1,000회 — 5분 주기 288회)
// GET /snap.json → 카메라별 이름·촬영 시각 / GET /img/<n>.jpg → 사진 / GET /live.json → 라이브 방송 국내 채널의 현재 영상 ID
// GET /air.json → 한반도 상공 항공기(맥이 5분마다 사진과 같이 올린다). adsb.lol 지도 iframe 은 봇 차단 쿠키(SameSite=Lax)가
//   다른 사이트 안에서 안 실려 깨지고, API 는 CORS 가 없고 Cloudflare 출구엔 503(adsb.fi 403) — 10/18 실측. 그래서 한국 맥이 받는다
// /push/* → push.js (상황판 방문자 웹 푸시 — 구독 저장·발송)
import { handlePush } from "./push.js";
const CORS = { "Access-Control-Allow-Origin": "*" };
const MAX_BYTES = 3_000_000;
export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (url.pathname.startsWith("/push/")) return handlePush(req, env, url.pathname);
    if (req.method === "POST" && url.pathname === "/upload") {
      if (req.headers.get("Authorization") !== `Bearer ${env.UPLOAD_TOKEN}`) return new Response("forbidden", { status: 403 });
      const body = await req.text();
      if (body.length > MAX_BYTES) return new Response("too large", { status: 413 });
      const b = JSON.parse(body);
      if (!Array.isArray(b.shots)) return new Response("bad", { status: 400 });
      // 실패한 칸은 직전 사진을 시각과 함께 남긴다 — 빈 칸보다 「몇 분 전 화면」이 낫다
      const prev = await env.SNAP.get("bundle", "json");
      b.shots = b.shots.map((s, i) => s.img ? s : (prev?.shots?.[i]?.name === s.name ? prev.shots[i] : s));
      // 라이브 ID 는 맥이 1시간에 한 번만 보낸다(유튜브가 Cloudflare 출구 IP 에 429 — 10/18 실측) → 안 보낸 회차는 직전 값 유지
      if (!b.live && prev?.live) { b.live = prev.live; b.live_at = prev.live_at; }
      if (!b.air && prev?.air) b.air = prev.air;   // 항공 조회 실패 회차는 직전 값(시각 포함) 유지
      await env.SNAP.put("bundle", JSON.stringify(b));
      return new Response("ok");
    }
    if (req.method !== "GET") return new Response("method", { status: 405 });
    const b = await env.SNAP.get("bundle", { type: "json", cacheTtl: 60 });
    if (!b) return new Response("no data", { status: 404, headers: CORS });
    if (url.pathname === "/live.json")
      return Response.json({ at: b.live_at || null, ids: b.live || {} }, { headers: { ...CORS, "Cache-Control": "public, max-age=300" } });
    if (url.pathname === "/air.json")
      return b.air ? Response.json(b.air, { headers: { ...CORS, "Cache-Control": "public, max-age=60" } })
                   : new Response("no air", { status: 404, headers: CORS });
    if (url.pathname === "/snap.json") {
      const shots = b.shots.map((s, i) => ({ i, name: s.name, at: s.at, ok: !!s.img }));
      return Response.json({ captured_at: b.captured_at, shots, paused: b.paused || null },
        { headers: { ...CORS, "Cache-Control": "public, max-age=60" } });
    }
    const m = url.pathname.match(/^\/img\/(\d+)\.jpg$/);
    const s = m && b.shots[+m[1]];
    if (!s?.img) return new Response("not found", { status: 404, headers: CORS });
    const bin = Uint8Array.from(atob(s.img), c => c.charCodeAt(0));
    return new Response(bin, { headers: { ...CORS, "Content-Type": "image/jpeg", "Cache-Control": "public, max-age=60" } });
  },
};


// POST /upload (Bearer UPLOAD_TOKEN) → KV "bundle" 한 키(무료 쓰기 하루 1,000회 — 5분 주기 288회)
// GET /snap.json → 카메라별 이름·촬영 시각 / GET /img/<n>.jpg → 사진
const CORS = { "Access-Control-Allow-Origin": "*" };
const MAX_BYTES = 3_000_000;

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (req.method === "POST" && url.pathname === "/upload") {
      if (req.headers.get("Authorization") !== `Bearer ${env.UPLOAD_TOKEN}`) return new Response("forbidden", { status: 403 });
      const body = await req.text();
      if (body.length > MAX_BYTES) return new Response("too large", { status: 413 });
      const b = JSON.parse(body);
      if (!Array.isArray(b.shots)) return new Response("bad", { status: 400 });
      // 실패한 칸은 직전 사진을 시각과 함께 남긴다 — 빈 칸보다 「몇 분 전 화면」이 낫다
      const prev = await env.SNAP.get("bundle", "json");
      b.shots = b.shots.map((s, i) => s.img ? s : (prev?.shots?.[i]?.name === s.name ? prev.shots[i] : s));
      await env.SNAP.put("bundle", JSON.stringify(b));
      return new Response("ok");
    }
    if (req.method !== "GET") return new Response("method", { status: 405 });
    const b = await env.SNAP.get("bundle", { type: "json", cacheTtl: 60 });
    if (!b) return new Response("no data", { status: 404, headers: CORS });
    if (url.pathname === "/snap.json") {
      const shots = b.shots.map((s, i) => ({ i, name: s.name, at: s.at, ok: !!s.img }));
      return Response.json({ captured_at: b.captured_at, shots },
        { headers: { ...CORS, "Cache-Control": "public, max-age=60" } });
    }
    const m = url.pathname.match(/^\/img\/(\d+)\.jpg$/);
    const s = m && b.shots[+m[1]];
    if (!s?.img) return new Response("not found", { status: 404, headers: CORS });
    const bin = Uint8Array.from(atob(s.img), c => c.charCodeAt(0));
    return new Response(bin, { headers: { ...CORS, "Content-Type": "image/jpeg", "Cache-Control": "public, max-age=60" } });
  },
};

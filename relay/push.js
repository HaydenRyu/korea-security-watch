// 상황판 방문자 웹 푸시 — 구독 저장(KV PUSH)과 일괄 발송.
// POST /push/subscribe   { endpoint, keys:{p256dh,auth} }  → { id }      (CORS: 상황판만, IP 당 분당 10회)
// POST /push/unsubscribe { endpoint }                        → { removed }
// POST /push/send  Bearer PUSH_TOKEN  { title, message, priority, link, cursor?, only? }
//                  → { sent, removed, failed, cursor, done }  — 한 번에 PAGE 건(무료 플랜 요청당 하위요청 50 한도).
//                    done 이 false 면 받은 cursor 로 다시 부른다(scripts/push_send.py 가 반복). only = 구독 id 하나만(시험용).
// 보내는 쪽은 notify.yml 하나뿐이고, 무엇을 보낼지는 diff_state.py 의 지인(friends) 정책이 정한다.
// 비밀: VAPID_PRIVATE(d, base64url) · PUSH_TOKEN — wrangler secret. 공개키 VAPID_PUBLIC 은 wrangler.toml vars.
import { sendPush, validSubscription } from "./webpush.js";

const ORIGINS = ["https://haydenryu.github.io"];
const MAX_BODY = 4096;
const PAGE = 20;
const RL_PER_MIN = 10;
const SUBJECT = "https://haydenryu.github.io/korea-security-watch/";

function cors(req) {
  const o = req.headers.get("Origin");
  return ORIGINS.includes(o) ? { "Access-Control-Allow-Origin": o, Vary: "Origin" } : { Vary: "Origin" };
}
const json = (obj, status, h) => Response.json(obj, { status, headers: h });

async function idOf(endpoint) {
  const h = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(endpoint)));
  return [...h.slice(0, 16)].map(b => b.toString(16).padStart(2, "0")).join("");
}

// 간이 레이트리밋 — isolate 메모리(분 단위 창). 여러 isolate 에 흩어지면 느슨해지지만 KV 쓰기 낭비 없이 폭주를 꺾는다
const hits = new Map();
function limited(ip) {
  const w = Math.floor(Date.now() / 60000), k = `${ip}:${w}`;
  const n = (hits.get(k) || 0) + 1;
  hits.set(k, n);
  if (hits.size > 5000) for (const key of hits.keys()) if (!key.endsWith(`:${w}`)) hits.delete(key);
  return n > RL_PER_MIN;
}

async function readJson(req) {
  const t = await req.text();
  if (t.length > MAX_BODY) throw new Error("too large");
  return JSON.parse(t);
}

export async function handlePush(req, env, path) {
  const h = cors(req);
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: { ...h, "Access-Control-Allow-Methods": "POST",
      "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Max-Age": "86400" } });
  }
  if (req.method !== "POST") return json({ error: "method" }, 405, h);

  if (path === "/push/send") {
    if (!env.PUSH_TOKEN || req.headers.get("Authorization") !== `Bearer ${env.PUSH_TOKEN}`) return json({ error: "forbidden" }, 403, h);
    let b;
    try { b = await readJson(req); } catch { return json({ error: "bad json" }, 400, h); }
    if (typeof b.title !== "string" || typeof b.message !== "string") return json({ error: "title/message" }, 400, h);
    const pr = Number(b.priority) || 3;
    const payload = { title: b.title.slice(0, 120), body: b.message.slice(0, 1000),
      url: typeof b.link === "string" && b.link.startsWith("https://haydenryu.github.io/") ? b.link : SUBJECT,
      tag: "jis-grade", priority: pr };
    const vapid = { publicKey: env.VAPID_PUBLIC, privateKey: env.VAPID_PRIVATE, subject: SUBJECT };
    if (!vapid.publicKey || !vapid.privateKey) return json({ error: "vapid not configured" }, 500, h);
    let subs, cursor = null, done = true;
    if (b.only) {
      const v = await env.PUSH.get(`sub:${String(b.only).slice(0, 64)}`, "json");
      subs = v ? [{ name: `sub:${b.only}`, sub: v.sub }] : [];
    } else {
      const l = await env.PUSH.list({ prefix: "sub:", limit: PAGE, cursor: b.cursor || undefined });
      subs = l.keys.map(k => ({ name: k.name, sub: k.metadata?.sub }));
      done = l.list_complete; cursor = l.list_complete ? null : l.cursor;
    }
    let sent = 0, removed = 0, failed = 0;
    const errors = [];
    await Promise.all(subs.map(async ({ name, sub }) => {
      if (!sub) { failed++; return; }
      try {
        const { status: st, reason } = await sendPush(sub, payload, vapid, { ttl: 86400, urgency: pr >= 4 ? "high" : "normal", topic: "jisgrade" });
        if (st >= 200 && st < 300) sent++;
        else if (st === 404 || st === 410) { await env.PUSH.delete(name); removed++; }  // 만료·해지된 구독
        else { failed++; errors.push(reason ? `${st} ${reason}` : st); }  // 사유를 남겨야 400 의 정체를 알 수 있다(10/19 애플 400)
      } catch (e) { failed++; errors.push(String(e).slice(0, 80)); }
    }));
    return json({ sent, removed, failed, cursor, done, errors: errors.slice(0, 5) }, 200, h);
  }

  if (!h["Access-Control-Allow-Origin"]) return json({ error: "origin" }, 403, h);
  if (limited(req.headers.get("CF-Connecting-IP") || "?")) return json({ error: "rate" }, 429, h);
  let b;
  try { b = await readJson(req); } catch { return json({ error: "bad json" }, 400, h); }

  if (path === "/push/subscribe") {
    const sub = { endpoint: b?.endpoint, keys: { p256dh: b?.keys?.p256dh, auth: b?.keys?.auth } };
    const bad = validSubscription(sub);
    if (bad) return json({ error: bad }, 400, h);
    const id = await idOf(sub.endpoint);
    // 구독은 list 메타데이터(1KB 한도)에 싣는다 — 발송 때 구독마다 get 하지 않게(하위요청 절약)
    await env.PUSH.put(`sub:${id}`, JSON.stringify({ sub, created: new Date().toISOString() }), { metadata: { sub } });
    return json({ id }, 200, h);
  }
  if (path === "/push/unsubscribe") {
    if (typeof b?.endpoint !== "string" || b.endpoint.length > 700) return json({ error: "endpoint" }, 400, h);
    const id = await idOf(b.endpoint);
    const had = (await env.PUSH.get(`sub:${id}`)) !== null;
    if (had) await env.PUSH.delete(`sub:${id}`);
    return json({ removed: had }, 200, h);
  }
  return json({ error: "not found" }, 404, h);
}

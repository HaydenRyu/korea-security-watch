// Web Push 발송 — RFC 8291(메시지 암호화, aes128gcm) + RFC 8292(VAPID). 외부 라이브러리 없이 WebCrypto 만 쓴다.
// Workers·Node(>=20) 양쪽에서 같은 코드가 돈다(테스트: relay/test/webpush.test.mjs — RFC 8291 §5 벡터).

const te = new TextEncoder();

export function b64u(buf) {
  const b = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  let s = "";
  for (let i = 0; i < b.length; i++) s += String.fromCharCode(b[i]);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function unb64u(s) {
  if (typeof s !== "string" || !/^[A-Za-z0-9_\-]*={0,2}$/.test(s)) throw new Error("base64url 아님");
  const t = s.replace(/=+$/, "").replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(t + "=".repeat((4 - (t.length % 4)) % 4));
  return Uint8Array.from(bin, c => c.charCodeAt(0));
}

function concat(...parts) {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}

async function hmac(key, data) {
  const k = await crypto.subtle.importKey("raw", key, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return new Uint8Array(await crypto.subtle.sign("HMAC", k, data));
}

// HKDF 한 블록(출력 ≤ 32바이트) — RFC 5869 Extract + Expand(T(1))
async function hkdf(salt, ikm, info, len) {
  const prk = await hmac(salt, ikm);
  return (await hmac(prk, concat(info, new Uint8Array([1])))).slice(0, len);
}

// P-256 개인키(d, base64url) + 공개키(65바이트 비압축 0x04||x||y) → JWK
function jwk(d, pub, ops) {
  return { kty: "EC", crv: "P-256", d, x: b64u(pub.slice(1, 33)), y: b64u(pub.slice(33, 65)), ext: true, key_ops: ops };
}

/**
 * RFC 8291 암호화. 반환 = 요청 본문(헤더 salt·rs·keyid + 암호문 한 레코드).
 * opts.asPrivate/asPublic/salt 는 테스트 벡터 재현용 — 운영에선 비워 매번 새 임시 키·salt 를 만든다.
 */
export async function encrypt(plaintext, uaPublicB64, authB64, opts = {}) {
  const uaPublic = unb64u(uaPublicB64);
  const auth = unb64u(authB64);
  if (uaPublic.length !== 65 || uaPublic[0] !== 4) throw new Error("p256dh 형식");
  if (auth.length !== 16) throw new Error("auth 형식");
  let asPriv, asPublic;
  if (opts.asPrivate) {
    asPublic = unb64u(opts.asPublic);
    asPriv = await crypto.subtle.importKey("jwk", jwk(opts.asPrivate, asPublic, ["deriveBits"]),
      { name: "ECDH", namedCurve: "P-256" }, false, ["deriveBits"]);
  } else {
    const kp = await crypto.subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, true, ["deriveBits"]);
    asPriv = kp.privateKey;
    asPublic = new Uint8Array(await crypto.subtle.exportKey("raw", kp.publicKey));
  }
  const uaKey = await crypto.subtle.importKey("raw", uaPublic, { name: "ECDH", namedCurve: "P-256" }, false, []);
  const ecdh = new Uint8Array(await crypto.subtle.deriveBits({ name: "ECDH", public: uaKey }, asPriv, 256));
  const salt = opts.salt ? unb64u(opts.salt) : crypto.getRandomValues(new Uint8Array(16));

  const ikm = await hkdf(auth, ecdh, concat(te.encode("WebPush: info\0"), uaPublic, asPublic), 32);
  const cek = await hkdf(salt, ikm, te.encode("Content-Encoding: aes128gcm\0"), 16);
  const nonce = await hkdf(salt, ikm, te.encode("Content-Encoding: nonce\0"), 12);

  const pt = typeof plaintext === "string" ? te.encode(plaintext) : plaintext;
  const rs = 4096;
  if (pt.length + 1 + 16 > rs) throw new Error("본문이 한 레코드(4096)를 넘는다");
  const key = await crypto.subtle.importKey("raw", cek, "AES-GCM", false, ["encrypt"]);
  const ct = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce }, key,
    concat(pt, new Uint8Array([2]))));  // 0x02 = 마지막 레코드 구분자
  const header = new Uint8Array(21);
  header.set(salt, 0);
  new DataView(header.buffer).setUint32(16, rs);
  header[20] = asPublic.length;
  return concat(header, asPublic, ct);
}

/** RFC 8292 VAPID Authorization 헤더 값. privB64 = d(32바이트), pubB64 = 65바이트 비압축 공개키 */
export async function vapidAuth(endpoint, pubB64, privB64, subject, nowSec = Math.floor(Date.now() / 1000)) {
  const pub = unb64u(pubB64);
  const key = await crypto.subtle.importKey("jwk", jwk(privB64, pub, ["sign"]),
    { name: "ECDSA", namedCurve: "P-256" }, false, ["sign"]);
  const head = b64u(te.encode(JSON.stringify({ typ: "JWT", alg: "ES256" })));
  const body = b64u(te.encode(JSON.stringify({ aud: new URL(endpoint).origin, exp: nowSec + 12 * 3600, sub: subject })));
  const sig = await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, key, te.encode(`${head}.${body}`));
  return `vapid t=${head}.${body}.${b64u(sig)}, k=${pubB64}`;
}

/** 구독 하나에 발송. 반환 = { status, reason } — reason 은 실패 응답 본문 앞 120자(애플은 400 에 reason 을 담아 준다) */
export async function sendPush(sub, payload, vapid, { ttl = 86400, urgency = "normal", topic } = {}) {
  const body = await encrypt(JSON.stringify(payload), sub.keys.p256dh, sub.keys.auth);
  const headers = {
    "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", TTL: String(ttl), Urgency: urgency,
    Authorization: await vapidAuth(sub.endpoint, vapid.publicKey, vapid.privateKey, vapid.subject),
  };
  if (topic) headers.Topic = topic;  // 같은 topic 의 미배달 알림은 새 것으로 교체된다(오래된 등급이 쌓이지 않게)
  const r = await fetch(sub.endpoint, { method: "POST", headers, body });
  let reason = "";
  if (!r.ok) { try { reason = (await r.text()).replace(/\s+/g, " ").slice(0, 120); } catch { /* 본문 없음 */ } }
  return { status: r.status, reason };
}

// 알려진 브라우저 푸시 서비스만 받는다(임의 주소로 Worker 가 POST 하는 통로가 되지 않게)
export const PUSH_HOSTS = [
  "fcm.googleapis.com",            // Chrome·Android·Samsung·Opera·Brave
  "push.services.mozilla.com",     // Firefox (updates.push.services.mozilla.com)
  "notify.windows.com",            // Edge (wns2-*.notify.windows.com)
  "push.apple.com",                // Safari·iOS 홈 화면 (web.push.apple.com)
];

export function validSubscription(s) {
  if (!s || typeof s !== "object") return "형식";
  let u;
  try { u = new URL(s.endpoint); } catch { return "endpoint"; }
  if (u.protocol !== "https:" || s.endpoint.length > 700) return "endpoint";
  if (!PUSH_HOSTS.some(h => u.hostname === h || u.hostname.endsWith("." + h))) return "푸시 서비스 아님";
  try {
    const p = unb64u(s.keys?.p256dh), a = unb64u(s.keys?.auth);
    if (p.length !== 65 || p[0] !== 4 || a.length !== 16) return "keys";
  } catch { return "keys"; }
  return null;
}

// node --test relay/test/  — RFC 8291 §5 테스트 벡터 + 수신 측 복호 왕복 + VAPID 서명 검증
import { test } from "node:test";
import assert from "node:assert/strict";
import { encrypt, vapidAuth, b64u, unb64u, validSubscription } from "../webpush.js";

// RFC 8291 §5 "Push Message Encryption Example"
const V = {
  plaintext: "When I grow up, I want to be a watermelon",
  asPublic: "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8",
  asPrivate: "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw",
  uaPublic: "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
  uaPrivate: "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94",
  auth: "BTBZMqHH6r4Tts7J_aSIgg",
  salt: "DGv6ra1nlYgDCS1FRnbzlw",
  body: "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN",
};

test("RFC 8291 §5 벡터와 바이트 단위로 같다", async () => {
  const out = await encrypt(V.plaintext, V.uaPublic, V.auth, { asPrivate: V.asPrivate, asPublic: V.asPublic, salt: V.salt });
  assert.equal(b64u(out), V.body);
});

// 수신자(브라우저) 쪽 복호 — 발송 코드와 따로 적은 구현으로 왕복 확인
async function hmac(k, d) {
  const key = await crypto.subtle.importKey("raw", k, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return new Uint8Array(await crypto.subtle.sign("HMAC", key, d));
}
const cat = (...a) => { const o = new Uint8Array(a.reduce((n, x) => n + x.length, 0)); let i = 0; for (const x of a) { o.set(x, i); i += x.length; } return o; };
async function decrypt(body, uaPrivD, uaPub, auth) {
  const salt = body.slice(0, 16), idlen = body[20], asPub = body.slice(21, 21 + idlen), ct = body.slice(21 + idlen);
  const jwk = { kty: "EC", crv: "P-256", d: uaPrivD, x: b64u(uaPub.slice(1, 33)), y: b64u(uaPub.slice(33)) };
  const priv = await crypto.subtle.importKey("jwk", jwk, { name: "ECDH", namedCurve: "P-256" }, false, ["deriveBits"]);
  const pubKey = await crypto.subtle.importKey("raw", asPub, { name: "ECDH", namedCurve: "P-256" }, false, []);
  const ecdh = new Uint8Array(await crypto.subtle.deriveBits({ name: "ECDH", public: pubKey }, priv, 256));
  const te = new TextEncoder();
  const ikm = await hmac(await hmac(auth, ecdh), cat(te.encode("WebPush: info\0"), uaPub, asPub, new Uint8Array([1])));
  const prk = await hmac(salt, ikm);
  const cek = (await hmac(prk, cat(te.encode("Content-Encoding: aes128gcm\0"), new Uint8Array([1])))).slice(0, 16);
  const nonce = (await hmac(prk, cat(te.encode("Content-Encoding: nonce\0"), new Uint8Array([1])))).slice(0, 12);
  const k = await crypto.subtle.importKey("raw", cek, "AES-GCM", false, ["decrypt"]);
  const pt = new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv: nonce }, k, ct));
  assert.equal(pt[pt.length - 1], 2);
  return new TextDecoder().decode(pt.slice(0, -1));
}

test("임시 키·무작위 salt 로 암호화 → 수신 측 복호 왕복(한글 포함)", async () => {
  const msg = JSON.stringify({ title: "한반도 징후 경계(2)", body: "10/18 06:09\n상황판에서 출처를 확인하세요." });
  const out = await encrypt(msg, V.uaPublic, V.auth);
  assert.equal(await decrypt(out, V.uaPrivate, unb64u(V.uaPublic), unb64u(V.auth)), msg);
  const out2 = await encrypt(msg, V.uaPublic, V.auth);
  assert.notEqual(b64u(out), b64u(out2));  // salt·임시 키가 매번 다르다
});

test("VAPID JWT — ES256 서명이 공개키로 검증되고 aud 는 endpoint origin", async () => {
  const kp = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
  const pub = b64u(await crypto.subtle.exportKey("raw", kp.publicKey));
  const d = (await crypto.subtle.exportKey("jwk", kp.privateKey)).d;
  const h = await vapidAuth("https://fcm.googleapis.com/fcm/send/abc", pub, d, "https://example.org/", 1000);
  const m = h.match(/^vapid t=([^.]+)\.([^.]+)\.([^,]+), k=(.+)$/);
  assert.ok(m);
  assert.equal(m[4], pub);
  const claims = JSON.parse(new TextDecoder().decode(unb64u(m[2])));
  assert.deepEqual(claims, { aud: "https://fcm.googleapis.com", exp: 1000 + 43200, sub: "https://example.org/" });
  const ok = await crypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, kp.publicKey, unb64u(m[3]),
    new TextEncoder().encode(`${m[1]}.${m[2]}`));
  assert.ok(ok);
});

test("구독 검증 — 알려진 푸시 서비스·https·키 길이", () => {
  const keys = { p256dh: V.uaPublic, auth: V.auth };
  assert.equal(validSubscription({ endpoint: "https://fcm.googleapis.com/fcm/send/x", keys }), null);
  assert.equal(validSubscription({ endpoint: "https://updates.push.services.mozilla.com/wpush/v2/x", keys }), null);
  assert.equal(validSubscription({ endpoint: "https://web.push.apple.com/x", keys }), null);
  assert.equal(validSubscription({ endpoint: "https://wns2-par02p.notify.windows.com/w/?token=x", keys }), null);
  assert.ok(validSubscription({ endpoint: "http://fcm.googleapis.com/x", keys }));
  assert.ok(validSubscription({ endpoint: "https://evil.example/fcm.googleapis.com", keys }));
  assert.ok(validSubscription({ endpoint: "https://fcm.googleapis.com.evil.example/x", keys }));
  assert.ok(validSubscription({ endpoint: "https://fcm.googleapis.com/x", keys: { p256dh: V.uaPublic, auth: "AAAA" } }));
});

// 서해 선박(aisstream.io) — cron 5분마다 RECV_MS 동안 받아 KV "sea" 한 키에 쌓는다(쓰기 하루 288회, bundle 288회와 합쳐 1,000 안).
// 지상국 AIS 라 북한 연안은 거의 안 잡힌다(10/27 실측: 서해 NLL·인천 2분 29척, 남포·나진 0). 「신호 끈 배」는 못 본다.
// 기록(log)은 두 가지만 남긴다 — 화면이 그 순간을 다시 그릴 수 있게 두 배의 항적을 같이 저장한다(스크린샷 대신):
//   nk_meet  = 북한(MID 445) 선박과 중국·러시아 선박이 MEET_KM 안 — 정지 여부·지속 시간과 무관하게 처음 본 순간 기록
//   stop_pair = NLL 상자 안 국적이 다른 두 배가 STOP_KM 안에서 둘 다 STOP_KN 미만으로 STOP_H 이상 — 「근접·정지」(환적 판정 아님)
// 국적 = MMSI 앞 3자리(MID) = 선적국. 편의치적·MMSI 위조가 흔해 운항사 국적이 아니다.
const WS = "https://stream.aisstream.io/v0/stream";
const BOX = [[36.5, 124.0], [38.8, 127.0]];          // 수신 범위 = 화면 지도 범위
const NLL = [[37.45, 124.5], [38.05, 126.15]];       // 근접·정지 판정 상자(인천 묘박지 제외)
const RECV_MS = 25000;                               // 1시간 미만 주기 cron 의 벽시계 한도(30초) 안
const MAX_MSG = 1500;                                // CPU 보호
const SHOW_MIN = 20, TRAIL_MIN = 60, DROP_MIN = 90;
const MEET_KM = 1.0, STOP_KM = 0.5, STOP_KN = 2, STOP_H = 2, PAIR_GAP_MIN = 15;
const LOG_MAX = 200, HIST_DAYS = 8;
const FLAGS = [["북한", /^445/], ["중국", /^41[234]/], ["러시아", /^273/], ["한국", /^44[01]/], ["일본", /^43[12]/]];
export const flagOf = m => (FLAGS.find(([, re]) => re.test(String(m))) || ["기타"])[0];

const km = (a, b) => {
  const r = Math.PI / 180, dLa = (b.lat - a.lat) * r, dLo = (b.lon - a.lon) * r;
  const h = Math.sin(dLa / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(dLo / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(h));
};
const inBox = (s, [[la0, lo0], [la1, lo1]]) => s.lat >= la0 && s.lat <= la1 && s.lon >= lo0 && s.lon <= lo1;
const r4 = v => Math.round(v * 1e4) / 1e4;

// 받은 메시지 1건 → ships 갱신. 위치 없는 메시지(구독 확인·정적 정보)는 이름만 반영한다
export function ingest(ships, m, now) {
  const md = m?.MetaData; if (!md?.MMSI) return;
  const id = String(md.MMSI);
  const s = ships[id] || (ships[id] = { tr: [] });
  const nm = (md.ShipName || "").trim(); if (nm) s.name = nm;
  const body = m.Message && Object.values(m.Message)[0];
  if (typeof md.latitude !== "number" || typeof md.longitude !== "number" || !body || !("Sog" in body)) return;
  if (Math.abs(md.latitude) > 90 || Math.abs(md.longitude) > 180) return;
  s.lat = r4(md.latitude); s.lon = r4(md.longitude); s.t = now;
  s.sog = typeof body.Sog === "number" && body.Sog < 102.3 ? Math.round(body.Sog * 10) / 10 : null;
  const last = s.tr[s.tr.length - 1];
  if (!last || now - last[2] >= 300e3 || km({ lat: last[0], lon: last[1] }, s) > 0.1) s.tr.push([s.lat, s.lon, now]);
}

// 항적 정리 · 오래된 배 제거 · 시간대 표본 · 근접 판정. state 를 제자리에서 바꾸고 이번에 새로 남긴 기록 수를 돌려준다
export function settle(st, now) {
  st.ships ||= {}; st.pairs ||= {}; st.log ||= []; st.hist ||= {};
  for (const [id, s] of Object.entries(st.ships)) {
    if (!s.t || now - s.t > DROP_MIN * 60e3) { delete st.ships[id]; continue; }
    s.tr = (s.tr || []).filter(p => now - p[2] <= TRAIL_MIN * 60e3);
  }
  const live = Object.entries(st.ships).filter(([, s]) => now - s.t <= SHOW_MIN * 60e3).map(([id, s]) => ({ id, ...s, f: flagOf(id) }));
  const hk = new Date(now).toISOString().slice(0, 13);
  st.hist[hk] = Math.max(st.hist[hk] || 0, live.length);
  for (const k of Object.keys(st.hist)) if (now - Date.parse(k + ":00:00Z") > HIST_DAYS * 864e5) delete st.hist[k];
  let added = 0;
  const seen = new Set();
  for (let i = 0; i < live.length; i++) for (let j = i + 1; j < live.length; j++) {
    const a = live[i], b = live[j];
    if (a.f === b.f) continue;
    const d = km(a, b);
    const nk = (a.f === "북한" && (b.f === "중국" || b.f === "러시아")) || (b.f === "북한" && (a.f === "중국" || a.f === "러시아"));
    const stop = d <= STOP_KM && inBox(a, NLL) && inBox(b, NLL) && (a.sog ?? 99) < STOP_KN && (b.sog ?? 99) < STOP_KN;
    if (!(nk && d <= MEET_KM) && !stop) continue;
    const key = [a.id, b.id].sort().join("-");
    seen.add(key);
    const p = st.pairs[key] || (st.pairs[key] = { start: now, kind: nk ? "nk_meet" : "stop_pair" });
    p.last = now; p.km = Math.round(d * 100) / 100;
    const h = (now - p.start) / 36e5;
    if (!p.logged && (p.kind === "nk_meet" || h >= STOP_H)) {
      p.logged = true; added++;
      const side = s => ({ mmsi: s.id, flag: s.f, name: s.name || null, lat: s.lat, lon: s.lon, sog: s.sog, tr: s.tr.map(q => [q[0], q[1]]) });
      st.log.unshift({ at: new Date(now).toISOString(), kind: p.kind, km: p.km, since: new Date(p.start).toISOString(), a: side(a), b: side(b) });
    }
    if (p.logged) { const e = st.log.find(x => [x.a.mmsi, x.b.mmsi].sort().join("-") === key && x.since === new Date(p.start).toISOString()); if (e) e.h = Math.round(h * 10) / 10; }
  }
  for (const [k, p] of Object.entries(st.pairs)) if (!seen.has(k) && now - p.last > PAIR_GAP_MIN * 60e3) delete st.pairs[k];
  st.log = st.log.slice(0, LOG_MAX);
  st.at = new Date(now).toISOString();
  return added;
}

// 평소 이 시각 = 지난 7일 같은 시(UTC)의 표본 중앙값. 3일 미만이면 null(화면: 「평소 값 쌓는 중」)
export function baseline(hist, now) {
  const hh = new Date(now).toISOString().slice(11, 13), today = new Date(now).toISOString().slice(0, 10);
  const v = Object.entries(hist || {}).filter(([k]) => k.slice(11, 13) === hh && k.slice(0, 10) !== today).map(([, n]) => n).sort((x, y) => x - y);
  return v.length >= 3 ? v[Math.floor(v.length / 2)] : null;
}

export function view(st, now) {
  const ships = Object.entries(st.ships || {}).filter(([, s]) => now - s.t <= SHOW_MIN * 60e3)
    .map(([mmsi, s]) => ({ mmsi, lat: s.lat, lon: s.lon, sog: s.sog, name: s.name || null, tr: s.tr.map(p => [p[0], p[1]]) }));
  const nm = id => { const s = st.ships?.[id]; return `${flagOf(id)} ${s?.name || id}`; };
  const meets = Object.entries(st.pairs || {}).filter(([, p]) => now - p.last <= PAIR_GAP_MIN * 60e3 && (p.kind === "nk_meet" || (now - p.start) / 36e5 >= STOP_H))
    .map(([k, p]) => { const [a, b] = k.split("-"); return { a: nm(a), b: nm(b), kind: p.kind, km: p.km, h: Math.round((now - p.start) / 36e5 * 10) / 10 }; });
  return { at: st.at, src: "aisstream.io", ys: { ships, base: baseline(st.hist, now) }, meets, log: (st.log || []).slice(0, 30), log_n: (st.log || []).length };
}

async function receive(env, ships) {
  const r = await fetch(WS, { headers: { Upgrade: "websocket" } });
  const ws = r.webSocket; if (!ws) throw new Error("ws upgrade " + r.status);
  ws.accept();
  let n = 0, bad = 0, dt = null;
  const done = new Promise(res => {
    const t = setTimeout(() => res("time"), RECV_MS);
    ws.addEventListener("message", async e => {
      n++; dt ||= typeof e.data === "string" ? "string" : e.data?.constructor?.name || typeof e.data;
      try {
        const txt = typeof e.data === "string" ? e.data : e.data instanceof Blob ? await e.data.text() : new TextDecoder().decode(e.data);
        ingest(ships, JSON.parse(txt), Date.now());
      } catch { bad++; }
      if (n >= MAX_MSG) { clearTimeout(t); res("max"); }
    });
    ws.addEventListener("close", ev => { clearTimeout(t); res("close " + ev.code); });
  });
  ws.send(JSON.stringify({ APIKey: env.AISSTREAM_KEY, BoundingBoxes: [BOX],
    FilterMessageTypes: ["PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport", "ShipStaticData"] }));
  const why = await done; try { ws.close(1000); } catch {}
  return { n, bad, dt, why };
}

export async function collectSea(env) {
  if (!env.AISSTREAM_KEY) return { skip: "no key" };
  const st = (await env.SNAP.get("sea", "json")) || {};
  st.ships ||= {};
  let rx;
  try { rx = await receive(env, st.ships); } catch (e) { rx = { n: 0, why: "err " + String(e).slice(0, 80) }; }
  rx.pos = Object.values(st.ships).filter(s => s.t && Date.now() - s.t < RECV_MS + 5000).length;
  const added = settle(st, Date.now());
  st.rx = { at: new Date().toISOString(), ...rx };
  await env.SNAP.put("sea", JSON.stringify(st));
  console.log(JSON.stringify({ sea: rx, ships: Object.keys(st.ships).length, added }));
  return { ...rx, added };
}

export async function seaJson(env, cors) {
  const st = await env.SNAP.get("sea", { type: "json", cacheTtl: 60 });
  if (!st?.at) return new Response("no sea", { status: 404, headers: cors });
  return Response.json({ ...view(st, Date.now()), rx: st.rx || null }, { headers: { ...cors, "Cache-Control": "public, max-age=60" } });
}

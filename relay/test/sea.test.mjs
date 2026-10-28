// node --test relay/test/ — 서해 선박: 수신 반영 · 북·중 근접 즉시 기록 · 근접·정지 2시간 · 평소 값
import { test } from "node:test";
import assert from "node:assert/strict";
import { ingest, settle, baseline, view, flagOf } from "../sea.js";

const T = Date.parse("2026-10-28T03:00:00Z");
const pos = (mmsi, lat, lon, sog = 0.5, name = "") => ({ MetaData: { MMSI: mmsi, ShipName: name, latitude: lat, longitude: lon },
  Message: { PositionReport: { Sog: sog } } });

test("국적 = MID", () => {
  assert.equal(flagOf(445123456), "북한"); assert.equal(flagOf(413000001), "중국");
  assert.equal(flagOf(273000001), "러시아"); assert.equal(flagOf(440000001), "한국"); assert.equal(flagOf(538000001), "기타");
});

test("위치 없는 메시지는 이름만, 위치는 반영", () => {
  const ships = {};
  ingest(ships, { MessageType: "SubscriptionConfirmation" }, T);
  ingest(ships, pos(413000001, 37.6, 124.9, 3.2, "LU YU 1"), T);
  assert.equal(ships["413000001"].lat, 37.6); assert.equal(ships["413000001"].sog, 3.2);
  assert.equal(ships["413000001"].name, "LU YU 1");
});

test("북한-중국 1km 안이면 처음 본 회차에 기록(정지 무관)", () => {
  const st = { ships: {} };
  ingest(st.ships, pos(445000001, 37.70, 125.00, 8), T);
  ingest(st.ships, pos(413000001, 37.705, 125.00, 8), T);
  assert.equal(settle(st, T), 1);
  assert.equal(st.log[0].kind, "nk_meet"); assert.equal(st.log[0].a.tr.length > 0, true);
  assert.equal(settle(st, T + 300e3), 0, "같은 쌍은 한 번만");
  assert.equal(view(st, T + 300e3).meets.length, 1);
});

test("근접·정지는 NLL 상자 안 2시간 뒤에만 기록, 같은 국적 쌍 제외", () => {
  const st = { ships: {} };
  let added = 0;
  for (let m = 0; m <= 125; m += 5) {
    const t = T + m * 60e3;
    ingest(st.ships, pos(440000001, 37.70, 125.50, 0.3), t);
    ingest(st.ships, pos(413000002, 37.701, 125.50, 0.2), t);
    ingest(st.ships, pos(413000003, 37.702, 125.50, 0.2), t);
    added += settle(st, t);
  }
  assert.equal(added, 2);  // 한국-중국 2쌍, 중국-중국 쌍은 제외
  assert.ok(st.log.every(e => e.kind === "stop_pair" && e.h >= 2));
});

test("인천 묘박지(NLL 상자 밖)는 근접·정지 아님", () => {
  const st = { ships: {} };
  for (let m = 0; m <= 130; m += 5) {
    ingest(st.ships, pos(440000001, 37.30, 126.40, 0), T + m * 60e3);
    ingest(st.ships, pos(413000002, 37.301, 126.40, 0), T + m * 60e3);
    settle(st, T + m * 60e3);
  }
  assert.equal(st.log.length, 0);
});

test("평소 값 = 지난 날 같은 시 중앙값, 3일 미만 null", () => {
  const h = { "2026-10-27T03": 20, "2026-10-26T03": 30 };
  assert.equal(baseline(h, T), null);
  h["2026-10-25T03"] = 25; h["2026-10-28T03"] = 99;
  assert.equal(baseline(h, T), 25);
});

test("90분 넘게 안 보인 배는 지운다", () => {
  const st = { ships: {} };
  ingest(st.ships, pos(413000001, 37.6, 124.9), T);
  settle(st, T + 91 * 60e3);
  assert.equal(Object.keys(st.ships).length, 0);
});

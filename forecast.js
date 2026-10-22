// 예측 공용 계산 — 상황판 「흐름·예측」 칸과 forecast.html 이 같은 함수를 쓴다(정본 data/risk_calendar.json)
// 확률은 자주 있는 도발(미사일·핵 발사)에만 — data/forecast_prob.json(지난 1년 빈도 + 과거 이 수준일 때 실제 비율). 전쟁 확률은 만들지 않는다(시나리오 지표로만)
const FC_DAY = 864e5;
const fcKST = d => new Date(d.toLocaleString("en-US", { timeZone: "Asia/Seoul" }));
const fcISO = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const fcMD = iso => iso.slice(5).replace("-", ".");

// 앞으로 horizon 일 안에 시작하거나 지금 진행 중인 일정 → [{id,name,start,end,src,stat}]
function fcUpcoming(doc, now = new Date(), horizon = doc.config?.horizon_days || 30) {
  const t = fcKST(now), today = fcISO(t), until = fcISO(new Date(t.getTime() + horizon * FC_DAY)), y = t.getFullYear();
  const out = [];
  const push = (a, start, end, label, src, note) => {
    if ((end || start) < today || start > until) return;
    out.push({ id: a.id, name: label || a.name, start, end, src, note: note || null, stat: doc.stats?.[a.id] || null });
  };
  for (const a of doc.anchors) {
    if (a.md) { for (const yy of [y, y + 1]) push(a, `${yy}-${a.md}`, null, null, null); continue; }
    if (a.next) push(a, a.next.start, a.next.end, a.next.label, a.next.source, a.next.note);
    const cur = a.dates?.[String(y)];
    if (cur && a.kind === "exercise") push(a, cur[0], cur[1], null, a.sources?.[String(y)]);
  }
  return out.sort((p, q) => p.start.localeCompare(q.start));
}

// 「최근 20년 중 8년 이 기간에 발사 · 평소라면 5.8년」(years 는 집계 기간에서 자동) — 집계 없는 일정은 그렇다고 적는다
function fcStatText(s) {
  if (!s) return "과거 집계 없음";
  return `최근 ${s.years}년 중 ${s.hit}년 이 기간에 발사 · 평소라면 ${s.expected}년`;
}
const FC_VERDICT = { more: "평소보다 잦음", usual: "평소 수준", less: "오히려 드묾" };

// 임박 신호(기사로 본 앞으로의 단서) — 정본 state/signals.json(sample 모드는 state/sample/). 우리 확률이 아니라 기사가 가리키는 사실만 보여 준다
const FC_SIG_KIND = { nav_warning: "항행경보", nk_threat: "북 예고", site_activity: "시설 동향", launcher_move: "발사대 이동", scheduled_event: "공표 일정" };
const FC_SIG_LEVEL = { official: "공식 발표", report: "언론 보도", analysis: "분석" };
const FC_SIG_FULL = { nav_warning: "항행경보(해상보안청·해사국)", nk_threat: "북한 공식 예고", site_activity: "발사장·시설 동향", launcher_move: "발사대 이동", scheduled_event: "공표 일정" };

// 유효(만료 전) 신호, 시점이 가까운 순 — 시점 미특정은 뒤로. expire=false 면 만료를 보지 않는다(sample 예시용)
function fcSignals(sig, now = new Date(), expire = true) {
  const today = fcISO(fcKST(now));
  return (sig?.active || []).filter(s => !expire || !s.expires || s.expires >= today)
    .sort((p, q) => (p.window?.start || "9").localeCompare(q.window?.start || "9") || p.first_seen.localeCompare(q.first_seen));
}
const fcSigWhen = s => !s.window?.start ? "시점 미특정" : s.window.end && s.window.end !== s.window.start ? `${fcMD(s.window.start)}–${fcMD(s.window.end)}` : fcMD(s.window.start);
// 종류별 과거 적중 한 줄 조각 — n 이 min_n 미만이면 건수를 비율로 읽지 않게 「집계 중」
function fcSigCheck(chk) {
  const ks = Object.entries(chk?.kinds || {}).filter(([k]) => FC_SIG_KIND[k]);
  const base = chk?.baseline != null ? ` · 평소 아무 ${chk.window_days || 14}일 중 ${Math.round(chk.baseline * 100)}%` : "";
  return ks.map(([k, v]) => v.n < (chk.min_n || 5) ? `${FC_SIG_FULL[k]} 집계 중(${v.n}건)` : `${FC_SIG_FULL[k]} ${v.n}건 중 ${v.hit}건 ${chk.window_days || 14}일 안 발사${base}`);
}

// 지금 할 일(10/22) — 상황판·예측 화면이 같은 문장을 낸다. 등급별 문장이 기본이고, 긴장 국면 단계가 등급보다 높을 때만
// 국면 할 일(data/escalation_ladder.json stages[].action → state/ladder.json stage_action)이 앞선다 — 더 심각한 쪽을 보인다
const FC_GRADE = ["평시", "주의", "경계", "위험"];
const FC_TODO = ["가까운 지하 대피소 위치를 한 번 알아 둡니다.",
  "뉴스로 상황만 확인합니다. 따로 할 일은 없습니다.",
  "비상용품(물·식량·상비약·라디오·보조배터리)을 점검하고 가족 연락 방법을 정해 둡니다.",
  "재난문자·TV·라디오의 정부 안내를 확인하고, 경보가 울리면 가까운 지하 대피소로 갑니다."];
function fcTodo(g, lad) {
  if (g == null) return null;
  const s = typeof lad?.stage === "number" ? lad.stage : 0;
  if (s >= 1 && s > g && lad.stage_action) return { text: lad.stage_action, why: `긴장 국면 「${lad.stage_name || s + "단계"}」` };
  return { text: FC_TODO[g], why: `현재 등급 ${FC_GRADE[g]}` };
}
// 발사 확률 한 조각 — 「46% · 과거 이 수준일 때 실제 27%」. 자료가 없으면 null
function fcProb(pb, days) {
  const h = (pb?.h || []).find(x => x.days === days);
  if (!h) return null;
  const tr = h.track || {};
  return { pct: Math.round(h.p * 100), actual: tr.n && tr.actual != null ? Math.round(tr.actual * 100) : null, hits: h.hits, n: h.n };
}
// 시나리오 기울기 — state/scenarios.json. {name, tone, on, known} 또는 null
function fcTilt(sc) {
  const rows = Array.isArray(sc?.scenarios) ? sc.scenarios : null;
  if (!rows) return null;
  const t = rows.find(c => c.id === sc.tilt) || rows.find(c => c.id === "hold");
  return t ? { ...t, rows } : null;
}

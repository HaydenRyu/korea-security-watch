// 예측 공용 계산 — 상황판 「흐름·예측」 칸과 forecast.html 이 같은 함수를 쓴다(정본 data/risk_calendar.json)
// 우리는 「전쟁 확률」을 만들지 않는다: 과거 같은 시기에 발사가 있던 해 수와 평소 기대치만 보여 준다(기본 앞으로 config.horizon_days=90일)
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

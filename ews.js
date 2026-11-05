// 조기경보 지수(EWS) 화면 — index.html 「세계 보기」 탭과 forecast.html 「조기경보 지표」 절. 공식 등급·알림과 무관한 참고 지수다.
// 공개 게이트 2개(data/ews_weights.json): public_globe = 「세계 보기」 탭·지구본·전역 목록·카드·예측 절 전역 표 · public_index = 지수 숫자·게이지·지수 문장.
// 옛 키 public 만 있으면 둘 다 그 값으로 읽는다. 둘 다 닫히면 ?sample=1 에서만 그린다(?sample=1&gate=globe = 지구본만 공개한 화면 미리보기).
// 게이트가 둘 다 닫히면 state/ews.json·MapLibre·스타일을 건드리지 않는다.
// alpha==1(세계 연동이 지수에 안 들어감)이면 기여 점수·연결 채널 값·한반도 연결선을 그리지 않는다 — 「이 지역이 한국 위험을 올렸다」로 읽히기 때문이다.
// MapLibre 는 「세계 보기」를 처음 누를 때 읽는다. 미관측·보류·실패를 0점이나 「안정」으로 그리지 않는다.
(function(){
"use strict";
const $=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const Q=new URLSearchParams(location.search),SAMPLE=Q.has("sample");
const ML="https://cdnjs.cloudflare.com/ajax/libs/maplibre-gl/5.6.1/maplibre-gl.min";
const GIBS="https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_ShadedRelief_Bathymetry/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg";
const HOME={center:[127,38]};
const GLOBE_PX_Z0=163;  // 지구본 지름(px) ≒ 163 × 2^zoom — 칸에 지구 전체가 들어오는 줌을 칸 크기로 정한다
const VIEW_KEY="jis-map-view";
// lb = 라벨 방향(지구본 가장자리에서 잘리거나 이웃과 겹치지 않는 쪽)
const THEATER={taiwan:{n:"대만해협",ll:[121,23.7],lb:"l"},ukraine:{n:"러시아-우크라이나",ll:[31.2,48.4],lb:"t"},scs_ecs:{n:"남·동중국해",ll:[113,11],lb:"b"},
  mideast:{n:"중동·호르무즈",ll:[56.3,26.5],lb:"r"},us_posture:{n:"미 전략자산",ll:[144.8,13.4],lb:"b"}};
const CHANNEL={c1_us_gap:"미군 전력 공백",c2_nk_ru_cn:"북-러·북-중 군사협력",c3_simul:"동시 압박",c4_sea_energy:"해상교통로·에너지",c5_diplomacy:"제재·외교"};
const STATE_NAME=["평온","긴장","위기","교전"];
const BAND_C={"안정":"--g0","관찰":"--g1","경계 징후":"--g2","고위험 징후":"--g3"};
const C5="0–24 안정 · 25–49 관찰 · 50–74 경계 징후 · 75–100 고위험 징후";
const C7="공식 경보 단계와 별개인 참고 지수입니다. 공식 경보는 재난안전포털·긴급재난문자를 따르세요.";
const C9="확인된 근거 없음";
const C16="이 브라우저에서는 지구본을 표시할 수 없어 목록으로 보여 드립니다.";
const NOTE_W="각 지역의 상태·요약·출처입니다. 한반도 위험 계산에는 넣지 않습니다.";
// 게이트 판정 — 새 키(boolean)가 있으면 그 값, 없으면 옛 public. 미리보기는 둘 다 연다(gate=globe 면 지수만 닫는다).
function gates(w,sample,q){if(sample)return{globe:true,index:(q&&q.get("gate"))!=="globe"};const k=n=>typeof w?.[n]==="boolean"?w[n]:w?.public===true;return{globe:k("public_globe")===true,index:k("public_index")===true}}
// 세계 연동이 지수에 들어가는가(alpha<1). 모르면 안 들어간다고 본다 — 기여 표시는 연동이 확인될 때만.
function linkedOf(doc,w){const a=typeof doc?.alpha==="number"?doc.alpha:w?.alpha;return typeof a==="number"&&a<1}
let VIEW={globe:true,index:true,linked:true};

const jf=u=>fetch(u,{cache:"no-store"}).then(r=>r.ok?r.json():null).catch(()=>null);
const kst=iso=>{const t=new Date(iso);if(isNaN(t))return"—";const p=Object.fromEntries(new Intl.DateTimeFormat("en-GB",{timeZone:"Asia/Seoul",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false}).formatToParts(t).map(x=>[x.type,x.value]));
  return`${p.month}.${p.day} ${p.hour}:${p.minute} KST`};
const still=()=>matchMedia("(prefers-reduced-motion:reduce)").matches||innerWidth<760;
const token=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const stateVar=t=>t.observed&&t.state!=null?`--g${t.state}`:"--unk";

const CSS=`
.ews-v{position:absolute;inset:0;z-index:1100;display:grid;grid-template-columns:minmax(0,1fr) clamp(240px,42%,320px);background:var(--ink)}
.ews-globe{position:relative;min-width:0;min-height:0;background:var(--ink)}
.ews-globe .maplibregl-map{position:absolute;inset:0}
.ews-globe .maplibregl-ctrl-attrib{background:rgba(5,8,14,.75)!important;color:var(--dim);font:400 9px var(--data)}.ews-globe .maplibregl-ctrl-attrib a{color:var(--fog)}
.ews-msg{position:absolute;inset:0;margin:0;display:grid;place-items:center;padding:var(--s16);text-align:center;font-size:13px;color:var(--fog)}
.ews-home{position:absolute;right:var(--s8);top:var(--s8);z-index:2;font:500 11px/16px var(--data);color:var(--ice);background:var(--head);border:1px solid var(--line-2);padding:var(--s4) var(--s8);cursor:pointer}
.ews-v.nogl{grid-template-columns:minmax(0,1fr) minmax(0,2fr)}
.ews-v.nogl .ews-home{display:none}
.ews-side{min-height:0;overflow-y:auto;border-left:1px solid var(--line);padding:var(--s12);display:flex;flex-direction:column;gap:var(--s12);background:var(--panel)}
.ews-side h3{margin:0;font:500 11px/1.4 var(--data);color:var(--fog);letter-spacing:.02em}
.ews-num{display:flex;align-items:baseline;gap:var(--s8);margin-top:var(--s4)}
.ews-num b{font:600 32px/1 var(--data)}.ews-num span{font-size:13px;font-weight:600}
.ews-meter{position:relative;display:grid;grid-template-columns:25fr 25fr 25fr 26fr;gap:2px;height:6px;margin-top:var(--s8)}
.ews-meter i{opacity:.35}.ews-meter u{position:absolute;top:-3px;width:2px;height:12px;background:var(--ice)}
.ews-p{margin:var(--s4) 0 0;font-size:12px;line-height:1.5;color:var(--ice)}
.ews-s{margin:var(--s4) 0 0;font-size:11px;line-height:1.5;color:var(--fog)}
.ews-err{color:var(--g3)}
.ews-list{list-style:none;margin:var(--s4) 0 0;padding:0}
.ews-list li{border-top:1px solid var(--line)}
.ews-row{all:unset;box-sizing:border-box;display:flex;align-items:center;gap:var(--s8);width:100%;min-height:32px;padding:var(--s4) 0;font-size:12px;cursor:pointer}
.ews-row:focus-visible{outline:2px solid var(--ice);outline-offset:-2px}
.ews-row[aria-pressed="true"] .ews-name{text-decoration:underline;text-underline-offset:3px}
.ews-row.static{cursor:default}
.ews-dot{flex:none;width:10px;height:10px;border-radius:50%;border:2px solid var(--c)}
.ews-name{min-width:0;flex:1}
.ews-val{flex:none;font:500 11px/1 var(--data);color:var(--fog)}
.ews-card{padding:var(--s8);border:1px solid var(--line-2);font-size:12px;line-height:1.5}
.ews-card a{display:block;margin-top:var(--s4);overflow-wrap:anywhere;color:var(--fog)}
.ews-pin{all:unset;display:block;cursor:pointer;font:500 11px/1.2 var(--text);color:var(--ice);text-shadow:0 0 3px #000,0 0 3px #000;white-space:nowrap}
.ews-pin span{position:absolute;pointer-events:none}
.ews-pin[data-lb=r] span{left:calc(100% + 4px);top:50%;transform:translateY(-50%)}
.ews-pin[data-lb=l] span{right:calc(100% + 4px);top:50%;transform:translateY(-50%)}
.ews-pin[data-lb=b] span{top:calc(100% + 2px);left:50%;transform:translateX(-50%)}
.ews-pin[data-lb=t] span{bottom:calc(100% + 2px);left:50%;transform:translateX(-50%)}
.ews-pin i{display:block;width:20px;height:20px;border-radius:50%;border:3px solid var(--c);background:color-mix(in srgb,var(--c) 25%,transparent)}
.ews-pin:focus-visible i{outline:2px solid var(--ice);outline-offset:2px}
.ews-pin.kr{cursor:default}.ews-pin.kr i{width:10px;height:10px;border-width:2px;background:var(--ice);border-color:var(--ice)}
#map-p.ews-on .layers,#map-p.ews-on .taphint,#map-p.ews-on #imgdate,#map-p.ews-on #nwerr{display:none!important}
@media (max-width:759px){.ews-v,.ews-v.nogl{grid-template-columns:minmax(0,1fr);grid-template-rows:minmax(150px,1fr) minmax(0,1.3fr)}
  .ews-side{border-left:0;border-top:1px solid var(--line)}.ews-v.nogl{grid-template-rows:auto minmax(0,1fr)}.ews-v.nogl .ews-globe{min-height:64px}}
.ews-t{width:100%;border-collapse:collapse;font-size:13px;margin-top:var(--s4)}
.ews-t th{font:500 11px/1.4 var(--data);color:var(--fog);text-align:left;padding:var(--s4) var(--s8) var(--s4) 0}
.ews-t td{border-top:1px solid var(--line);padding:var(--s8) var(--s8) var(--s8) 0;vertical-align:top}
.ews-t td.n{font-family:var(--data);white-space:nowrap}
.ews-fc-b{display:grid;gap:var(--s16)}
.ews-src a{display:block;margin-top:var(--s4);font-size:11px;line-height:1.5;overflow-wrap:anywhere;color:var(--fog)}
`;

// ── 지수 ──
function lastGood(doc){
  const g=doc.last_good;
  return g&&g.ews!=null?`<p class="ews-s">마지막 정상값 ${esc(g.ews)} · ${esc(kst(g.computed_at))}</p>`:"";
}
const NOTE_E=`<p class="ews-s">세계 연동은 지수에 넣지 않고 따로 보여 드립니다. 과거 검정에서 다른 곳의 위기 앞뒤로 북한 발사가 늘었다는 근거가 없습니다.</p>`;
const worldShort=doc=>`<p class="ews-s">판정 보류 — 세계 연동 데이터가 부족하거나 오래되었습니다 (관측 전역 ${esc(doc.coverage?.observed_theaters??0)}/5)</p>`;
const stale=doc=>`<p class="ews-p">판정 보류 — 한반도 판정이 오래되었습니다 (마지막 ${esc(kst(doc.coverage?.latest_updated_at))})</p>`;
const notes=doc=>(doc.grade>0?`<p class="ews-s">공식 단계 ${esc(doc.grade_label)} 구간 안에서 징후가 쌓인 정도입니다.</p>`:"")+(doc.alpha===1?NOTE_E:"")+(doc.e==null?worldShort(doc):"");
function gauge(doc){
  const v=`<p class="ews-s">가설 가중치 v${esc(doc.weights_version)} · 백테스트 전</p><p class="ews-s">${C7}</p><p class="ews-s">${esc(kst(doc.computed_at))}</p>`;
  if(doc.status==="error")
    return`<section><h3>조기경보 지수(EWS)</h3><p class="ews-p ews-err">조기경보 지수를 계산하지 못했습니다 — ${esc(String(doc.error||"").slice(0,200))}</p>${lastGood(doc)}${v}</section>`;
  if(doc.status==="withheld")
    return`<section><h3>조기경보 지수(EWS)</h3>${doc.alpha===1?stale(doc):`<p class="ews-p">판정 보류 — 세계 연동 데이터가 부족하거나 오래되었습니다 (관측 전역 ${esc(doc.coverage?.observed_theaters??0)}/5)</p>`}`
      +`<p class="ews-p">한반도 직접 ${esc(doc.d)}</p>${lastGood(doc)}${v}</section>`;
  const c=BAND_C[doc.band]||"--fog";
  return`<section><h3>조기경보 지수(EWS)</h3><div class="ews-num"><b style="color:var(${c})">${esc(doc.ews)}</b><span style="color:var(${c})">${esc(doc.band)}</span></div>`
    +`<div class="ews-meter" role="img" aria-label="조기경보 지수 ${esc(doc.ews)}, ${esc(doc.band)}"><i style="background:var(--g0)"></i><i style="background:var(--g1)"></i><i style="background:var(--g2)"></i><i style="background:var(--g3)"></i><u style="left:${Math.min(99,Math.max(0,doc.ews))}%"></u></div>`
    +`<p class="ews-p">한반도 직접 ${esc(doc.d)} · 세계 연동 ${esc(doc.e??"—")}</p>${notes(doc)}<p class="ews-s">${C5}</p>${v}</section>`;
}
const quietOnly=t=>(t.sources||[]).length>0&&t.sources.every(s=>s.kind==="feed"); // 평온 확인 = 정본 피드에 관련 기사 없음(기사 근거 아님)
function theaterValue(t){
  if(!t.observed)return C9;
  return`${STATE_NAME[t.state]??"—"}${quietOnly(t)?"(피드 확인)":""}${!VIEW.linked||t.contribution==null?"":` · ${t.contribution}`}`;
}
const seen=t=>t.judged_at?`<p class="ews-s">확인 ${esc(kst(t.judged_at))}</p>`:"";
const srcLinks=t=>(t.sources||[]).filter(s=>/^https?:/.test(s.url||"")).map(s=>`<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name||s.url)}${s.kind==="feed"?" · 피드 확인 · 관련 보도 없음":""}${s.tier?` · ${esc(s.tier)}`:""}${s.published_at?` · ${s.kind==="feed"?"최신 항목 ":""}${esc(kst(s.published_at))}`:""}</a>`).join("");
function card(t){
  if(!t)return"";
  if(!t.observed)return`<div class="ews-card"><b>${esc(THEATER[t.key].n)}</b><br>${C9}</div>`;
  const ch=VIEW.linked?(t.channels||[]).map(c=>CHANNEL[c]).filter(Boolean).join(" · "):"";  // 연결 채널은 연동이 지수에 들어갈 때만
  return`<div class="ews-card"><b>${esc(THEATER[t.key].n)}</b> ${esc(STATE_NAME[t.state]??"")}<br>${esc(t.summary)}${ch?`<p class="ews-s">${esc(ch)}</p>`:""}${seen(t)}${srcLinks(t)}</div>`;
}
function renderSide(doc,sel){
  const ts=doc.theaters||[],cs=doc.channels||[];
  const tl=ts.map(t=>`<li><button type="button" class="ews-row" data-t="${esc(t.key)}" aria-pressed="${t.key===sel}"><i class="ews-dot" style="--c:var(${stateVar(t)})"></i><span class="ews-name">${esc(THEATER[t.key]?.n||t.key)}</span><span class="ews-val">${esc(theaterValue(t))}</span></button></li>`).join("");
  const cl=cs.map(c=>`<li><div class="ews-row static"><span class="ews-name">${esc(CHANNEL[c.key]||c.key)}</span><span class="ews-val">${c.observed?esc(c.value??"—"):C9}</span></div></li>`).join("");
  const head=VIEW.index?gauge(doc):doc.status==="error"||!ts.length?`<section><p class="ews-p ews-err">세계 전역 자료를 불러오지 못했습니다</p></section>`:"";
  const tlh=VIEW.linked?"<h3>연동 전역</h3>":`<h3>세계 전역</h3><p class="ews-s">${NOTE_W}</p>`;
  $("ews-side").innerHTML=head+(tl?`<section>${tlh}<ul class="ews-list">${tl}</ul></section>`:"")
    +card(ts.find(t=>t.key===sel))+(cl&&VIEW.linked?`<section><h3>연결 채널</h3><ul class="ews-list">${cl}</ul></section>`:"");
}

// ── 지구본 ──
function glOk(){
  try{const c=document.createElement("canvas");return!!(c.getContext("webgl2")||c.getContext("webgl"))}catch(_){return false}
}
let mlP=null;
function loadML(){
  return mlP||=new Promise((ok,no)=>{
    const l=document.createElement("link");l.rel="stylesheet";l.href=ML+".css";document.head.append(l);
    const s=document.createElement("script");s.src=ML+".js";s.onload=()=>window.maplibregl?ok(window.maplibregl):no(new Error("maplibre"));s.onerror=()=>no(new Error("maplibre"));document.head.append(s);
  });
}
// 호: 전역 → 한반도 대권 경로. 지구본 위에서 곡선이 되도록 점을 나눈다
function arc(a,b,n=48){
  const rad=x=>x*Math.PI/180,deg=x=>x*180/Math.PI;
  const v=([lo,la])=>[Math.cos(rad(la))*Math.cos(rad(lo)),Math.cos(rad(la))*Math.sin(rad(lo)),Math.sin(rad(la))];
  const p=v(a),q=v(b),w=Math.acos(Math.min(1,p[0]*q[0]+p[1]*q[1]+p[2]*q[2]))||1e-6,out=[];
  for(let i=0;i<=n;i++){const f=i/n,s1=Math.sin((1-f)*w)/Math.sin(w),s2=Math.sin(f*w)/Math.sin(w),x=s1*p[0]+s2*q[0],y=s1*p[1]+s2*q[1],z=s1*p[2]+s2*q[2];
    out.push([deg(Math.atan2(y,x)),deg(Math.atan2(z,Math.hypot(x,y)))])}
  return out;
}
function arcs(doc){
  if(!VIEW.linked)return{type:"FeatureCollection",features:[]};  // alpha==1 — 한반도로 잇는 선이 「위험을 올렸다」로 읽힌다
  return{type:"FeatureCollection",features:(doc.theaters||[]).filter(t=>t.observed&&t.contribution>0&&THEATER[t.key]).map(t=>({type:"Feature",
    properties:{w:Math.min(8,1+t.contribution*.35),c:token(stateVar(t))},geometry:{type:"LineString",coordinates:arc(THEATER[t.key].ll,HOME.center)}}))};
}
let map=null,globeTried=false;
function noGlobe(){
  $("ews-v").classList.add("nogl");
  $("ews-globe").innerHTML=`<p class="ews-msg">${C16}</p>`;
}
async function startGlobe(doc,onPick){
  if(globeTried){map?.resize();return}
  globeTried=true;
  if(!glOk())return noGlobe();
  const box=$("ews-globe"),wait=document.createElement("p");
  wait.className="ews-msg";wait.textContent="불러오는 중";box.append(wait);
  try{
    const ml=await loadML();
    const zoom=()=>Math.log2(Math.min(box.clientWidth,box.clientHeight)*.92/GLOBE_PX_Z0);
    map=new ml.Map({container:box,center:HOME.center,zoom:zoom(),attributionControl:{compact:false},
      style:{version:8,projection:{type:"globe"},sources:{gibs:{type:"raster",tiles:[GIBS],tileSize:256,maxzoom:8,attribution:'<a href="https://earthdata.nasa.gov/gibs">NASA GIBS</a>'}},
        layers:[{id:"bg",type:"background",paint:{"background-color":token("--ink")}},{id:"gibs",type:"raster",source:"gibs"}]}});
    map.on("load",()=>{
      wait.remove();
      if(VIEW.linked){map.addSource("ews-arcs",{type:"geojson",data:arcs(doc)});
        map.addLayer({id:"ews-arcs",type:"line",source:"ews-arcs",layout:{"line-cap":"round"},paint:{"line-color":["get","c"],"line-width":["get","w"],"line-opacity":.85}})}
      for(const t of doc.theaters||[]){
        if(!THEATER[t.key])continue;
        const el=document.createElement("button");el.type="button";el.className="ews-pin";el.style.setProperty("--c",`var(${stateVar(t)})`);
        el.dataset.lb=THEATER[t.key].lb;el.innerHTML=`<i></i><span>${esc(THEATER[t.key].n)}</span>`;el.setAttribute("aria-label",`${THEATER[t.key].n} ${theaterValue(t)}`);
        el.addEventListener("click",e=>{e.stopPropagation();onPick(t.key)});
        new ml.Marker({element:el}).setLngLat(THEATER[t.key].ll).addTo(map);
      }
      const kr=document.createElement("div");kr.className="ews-pin kr";kr.dataset.lb="r";kr.innerHTML="<i></i><span>한반도</span>";
      new ml.Marker({element:kr}).setLngLat(HOME.center).addTo(map);
    });
    map.on("webglcontextlost",noGlobe);
    const home=document.createElement("button");home.type="button";home.className="ews-home";home.textContent="한반도로 돌아가기";
    home.addEventListener("click",()=>{const c={...HOME,zoom:zoom()};still()?map.jumpTo(c):map.flyTo({...c,duration:800})});box.append(home);
  }catch(_){map=null;noGlobe()}
}

// ── 상황판 탭 ──
function initBoard(doc){
  const tabs=$("ews-tabs"),view=$("ews-v"),panel=$("map-p");
  const btns=[...tabs.querySelectorAll("button[role=tab]")];
  let sel=null;
  const pick=k=>{sel=k;renderSide(doc,sel)};
  $("ews-side").addEventListener("click",e=>{const b=e.target.closest("[data-t]");if(b)pick(b.dataset.t)});
  function show(k,focus){
    for(const b of btns){const on=b.dataset.v===k;b.setAttribute("aria-selected",on);b.tabIndex=on?0:-1;if(on&&focus)b.focus()}
    const world=k==="world";
    view.hidden=!world;panel.classList.toggle("ews-on",world);
    try{localStorage.setItem(VIEW_KEY,k)}catch(_){}
    if(world)startGlobe(doc,pick);
  }
  tabs.addEventListener("click",e=>{const b=e.target.closest("button[role=tab]");if(b)show(b.dataset.v)});
  tabs.addEventListener("keydown",e=>{if(e.key!=="ArrowLeft"&&e.key!=="ArrowRight")return;
    e.preventDefault();show(btns.find(b=>b.getAttribute("aria-selected")!=="true").dataset.v,true)});
  renderSide(doc,sel);
  tabs.hidden=false;
  let saved=null;try{saved=localStorage.getItem(VIEW_KEY)}catch(_){}
  show(saved==="world"?"world":"map");
}

// ── 예측 절 ──
function initForecast(doc){
  const b=$("ews-fc-b");
  const name=t=>`<td>${esc(THEATER[t.key]?.n||t.key)}</td>`,st=t=>t.observed?esc((STATE_NAME[t.state]??"—")+(quietOnly(t)?"(피드 확인)":"")):C9;
  const ts=VIEW.globe?doc.theaters||[]:[];
  const tr=VIEW.linked?ts.map(t=>`<tr>${name(t)}<td>${st(t)}</td><td class="n">${t.observed?esc(t.contribution??"—"):"—"}</td></tr>`).join("")
    :ts.map(t=>`<tr>${name(t)}<td>${st(t)}${t.observed&&t.summary?`<p class="ews-s">${esc(t.summary)}</p>`:""}${t.observed?`<div class="ews-src">${srcLinks(t)}</div>`:""}</td><td class="n">${t.observed&&t.judged_at?esc(kst(t.judged_at)):"—"}</td></tr>`).join("");
  const cr=VIEW.globe&&VIEW.linked?(doc.channels||[]).map(c=>`<tr><td>${esc(CHANNEL[c.key]||c.key)}</td><td class="n">${c.observed?esc(c.value??"—"):C9}</td></tr>`).join(""):"";
  let head;
  if(VIEW.index){
    $("ews-fc-v").textContent=`가설 가중치 v${doc.weights_version??"—"} · 백테스트 전`;
    head=(doc.status==="error"?`<p class="ews-p ews-err">조기경보 지수를 계산하지 못했습니다 — ${esc(String(doc.error||"").slice(0,200))}</p>${lastGood(doc)}`
      :doc.status==="withheld"?`${doc.alpha===1?stale(doc):worldShort(doc)}${lastGood(doc)}`
      :`<p class="ews-p">조기경보 지수(EWS) ${esc(doc.ews)} · ${esc(doc.band)} · 한반도 직접 ${esc(doc.d)} · 세계 연동 ${esc(doc.e??"—")}</p>${notes(doc)}`)+`<p class="ews-s">${C7}</p>`;
  }else{  // 지수 비공개 — 절 제목·우측 표기에서도 지수를 뺀다
    const k=$("ews-fc").querySelector(".k");if(k)k.textContent="세계 전역 상황";
    const at=doc.coverage?.linkage_judged_at;$("ews-fc-v").textContent=at?`확인 ${kst(at)}`:"—";
    head=doc.status==="error"||!ts.length?`<p class="ews-p ews-err">세계 전역 자료를 불러오지 못했습니다</p>`:`<p class="ews-s">${NOTE_W}</p>`;
  }
  b.innerHTML=`<div>${head}</div>`
    +(tr?`<table class="ews-t"><thead><tr><th>전역</th><th>상태</th><th>${VIEW.linked?"기여":"확인"}</th></tr></thead><tbody>${tr}</tbody></table>`:"")
    +(cr?`<table class="ews-t"><thead><tr><th>채널</th><th>값</th></tr></thead><tbody>${cr}</tbody></table>`:"");
  $("ews-fc").hidden=false;
}

if(globalThis.__EWS_TEST__)Object.assign(globalThis.__EWS_TEST__,{gates,linkedOf,arcs,setView:v=>{VIEW=v}});  // tests/test_ews_view.py
(async function(){
  if(!$("ews-tabs")&&!$("ews-fc"))return;
  const w=await jf("data/ews_weights.json");
  const g=gates(w,SAMPLE,Q);
  if(!g.globe&&!g.index)return;  // 게이트 — 운영은 승인 전까지 아무것도 그리지 않는다
  let doc;
  if(SAMPLE){const all=await jf("state/sample/ews.json");doc=all&&(all[Q.get("ews")]||all.ok)}
  else doc=await jf("state/ews.json");
  if(!doc)doc={status:"error",error:"state/ews.json 을 불러오지 못했습니다",weights_version:w?.version,theaters:[],channels:[],computed_at:null};
  VIEW={globe:g.globe,index:g.index,linked:linkedOf(doc,w)};
  const st=document.createElement("style");st.textContent=CSS;document.head.append(st);
  if($("ews-tabs")&&g.globe)initBoard(doc);  // 「세계 보기」는 지구본 공개가 있어야 연다
  if($("ews-fc"))initForecast(doc);
})();
})();

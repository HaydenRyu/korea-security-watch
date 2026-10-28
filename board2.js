// 배치 v2 (10/28) — 한 화면 배치·탭·해상·사건 기록. index.html 본 스크립트 뒤에 읽힌다(전역 map·layers·airMap·HOME·CAM 사용).
(function(){
"use strict";
const $=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const RM=()=>matchMedia("(prefers-reduced-motion:reduce)").matches;
const RELAY="https://jis-cctv.jis-probe.workers.dev";

// ── 탭 공통(세계 칸 .wtab 과 같은 동작: 클릭 · 좌우 화살표) ──
function tabs(list,onSel){
  const btns=[...list.querySelectorAll("button[role=tab]")];
  function sel(k,focus){
    for(const b of btns){const on=b.dataset.t===k;b.setAttribute("aria-selected",on);b.tabIndex=on?0:-1;
      const p=$(b.getAttribute("aria-controls"));if(p)p.hidden=!on;if(on&&focus)b.focus()}
    if(onSel)onSel(k);
  }
  list.addEventListener("click",e=>{const b=e.target.closest("button[data-t]");if(b)sel(b.dataset.t)});
  list.addEventListener("keydown",e=>{if(e.key!=="ArrowLeft"&&e.key!=="ArrowRight")return;e.preventDefault();
    const i=btns.findIndex(b=>b.getAttribute("aria-selected")==="true");
    sel(btns[(i+(e.key==="ArrowRight"?1:btns.length-1))%btns.length].dataset.t,true)});
  return sel;
}

// ── ① 등급 기준 「기준 보기」(≥1180 은 접힘 · 그 아래 폭은 원래 칸이 아래에 있다) ──
const critP=$("crit-p"),critBtn=$("critBtn");
function critClose(){critP.classList.remove("pop");critP.style.top=critP.style.left="";critBtn.setAttribute("aria-expanded","false")}
critBtn.addEventListener("click",e=>{
  e.stopPropagation();
  if(critP.classList.contains("pop"))return critClose();
  const r=critBtn.getBoundingClientRect();
  critP.classList.add("pop");critP.style.top=(r.bottom+4)+"px";critP.style.left=Math.max(16,Math.min(r.left,innerWidth-critP.offsetWidth-16))+"px";
  critBtn.setAttribute("aria-expanded","true");
});
document.addEventListener("click",e=>{if(critP.classList.contains("pop")&&!critP.contains(e.target))critClose()});
document.addEventListener("keydown",e=>{if(e.key==="Escape"&&critP.classList.contains("pop")){critClose();critBtn.focus()}});
addEventListener("resize",()=>{if(critP.classList.contains("pop"))critClose()});

// ── ② 감시 칸: 감시 항목 | 사건 기록 ──
let INC=null,incLoaded=false;
tabs(document.querySelector('#sensors-p .wtab'),k=>{
  $("sensorsum").hidden=k!=="sensors";$("incsum").hidden=k!=="inc";
  if(k==="inc"&&!incLoaded)loadInc();
});
const TIER={direct:["직접","공식 귀속"],indirect:["간접","공식 확인 전"],resolved:["해소","무관 판명"]};
const day=s=>{if(!s)return"—";const d=new Date(s);return isNaN(d)?esc(s):`${d.getMonth()+1}월 ${d.getDate()}일`};
async function loadInc(){
  incLoaded=true;
  try{const r=await fetch("state/incidents.json",{cache:"no-store"});if(r.ok)INC=await r.json()}catch(_){}
  renderInc();incLayer();
}
function renderInc(){
  const items=(INC?.items||[]).slice().sort((a,b)=>String(b.when?.occurred||"").localeCompare(String(a.when?.occurred||"")));
  const live=items.filter(x=>x.tier!=="resolved"),gone=items.filter(x=>x.tier==="resolved");
  const n=t=>items.filter(x=>x.tier===t).length;
  $("incsum").textContent=INC?`직접 ${n("direct")} · 간접 ${n("indirect")}`:"최근 30일";
  if(!items.length){
    $("inc").innerHTML=`<p class="ihead">국내 총기·탄약·마약·방화·해킹 등 사건 중 한반도 안보와의 연결이 보도된 것만 모읍니다.
      직접 = 수사기관·법원·정부·우방의 공식 귀속 발표 · 간접 = 공식 확인 전 정황(북한제 추정·표류물·연관성 조사 중). 등급은 감시 항목이 정합니다.</p>
      <p class="fa-e">${INC?"최근 30일 기록 없음":"아직 기록이 없습니다 — 매시 회차가 분류를 시작하면 여기 쌓입니다."}</p>`;
    return;
  }
  const row=x=>{const t=TIER[x.tier]||TIER.indirect;
    const src=(x.sources||[]).map(s=>`<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.publisher||s.site||"원문")}</a>`).join(" · ");
    const tt=x.attributed_by?`${t[1]} — ${x.attributed_by.name}${x.attributed_by.what?" "+x.attributed_by.what:""}`:(x.downgraded?`${t[1]} (${x.downgraded})`:t[1]);
    return`<div class="ie ${esc(x.tier)}"><div class="t"><span class="tier ${esc(x.tier)}" title="${esc(tt)}">${t[0]}</span><b>${x.category?`[${esc(x.category)}] `:""}${esc(x.what)}</b>`
      +`<span class="d">발생 ${day(x.when?.occurred)}${x.when?.announced?` · 발표 ${day(x.when.announced)}`:""}</span></div>`
      +`<div class="k"><em>어디</em>${esc(x.where?.place)} <em>누가</em>${esc(x.who||"미상")}</div>`
      +(x.how?`<div class="k"><em>어떻게</em>${esc(x.how)}</div>`:"")
      +`<div class="k"><em>왜</em>${esc(x.why||"미상")}</div>`
      +(x.link?`<div class="k"><em>연결</em>${esc(x.link)}${x.attributed_by?` · ${esc(x.attributed_by.name)} 발표`:""}</div>`:"")
      +(src?`<div class="s">${src} ↗</div>`:"")+`</div>`};
  $("inc").innerHTML=live.map(row).join("")
    +(gone.length?`<details class="tsec"><summary>해소된 사건<small>${gone.length}건 · 무관 판명</small></summary>${gone.map(row).join("")}</details>`:"");
}

// ── ③ 신호 칸: 공역 | 해상 ──
const airP=$("air-p");let seaInit=false;
tabs(airP.querySelector(".wtab"),k=>{
  airP.classList.toggle("sea",k==="sea");$("seaAt").hidden=k!=="sea";
  if(k==="sea"&&!seaInit){seaInit=true;initSea()}
  if(k==="sea"&&seaMap)setTimeout(()=>seaMap.invalidateSize(),0);
});
// 국적 = MMSI 앞 3자리(MID). 리스·편의치적이 흔해 「선적국」이지 운항사 국적이 아니다
const FLAG=[["한국",/^44[01]/,"#5ee0ef"],["중국",/^41[234]/,"#ff6b6b"],["러시아",/^273/,"#c792ea"],["북한",/^445/,"#ffd166"],["일본",/^43[12]/,"#f78fb3"]];
const flagOf=m=>{for(const[n,re,c]of FLAG)if(re.test(String(m)))return[n,c];return["기타","#8091a8"]};
const COL=Object.fromEntries([...FLAG.map(f=>[f[0],f[2]]),["기타","#8091a8"]]);
let seaMap,seaLayer,SEA=null;
const AREAS={ys:null,zs:[30.2,122.4,8],vl:[42.9,132.2,8],tw:[24.3,119.8,7]};
function initSea(){
  if(!window.L)return;
  seaMap=L.map("seaMap",{zoomControl:false,attributionControl:true,minZoom:5,maxZoom:12,zoomSnap:.25}).fitBounds([[36.5,124],[38.8,127]]);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",{maxZoom:12,className:"street",
    attribution:'<a href="https://www.openstreetmap.org/copyright">OSM</a> · <a href="https://aisstream.io">aisstream.io</a>'}).addTo(seaMap);
  seaLayer=L.layerGroup().addTo(seaMap);
  new ResizeObserver(()=>seaMap.invalidateSize()).observe($("seaMap"));
  document.querySelector("#sea .sarea").addEventListener("click",e=>{const b=e.target.closest("button[data-a]");if(b)seaArea(b.dataset.a)});
  loadSea();setInterval(()=>{if(!airP.querySelector("#sea").hidden&&!document.hidden)loadSea()},60e3);
}
function seaArea(a){
  document.querySelectorAll("#sea .sarea button").forEach(b=>b.setAttribute("aria-pressed",b.dataset.a===a));
  const vf=$("seaVf"),own=a==="ys";
  $("seaMap").hidden=!own;$("seaLeg").hidden=!own||!SEA;vf.hidden=own;
  const em=document.querySelector("#sea .sempty");if(em)em.hidden=!own;
  if(own){setTimeout(()=>seaMap.invalidateSize(),0);return}
  // VesselFinder 공식 임베드(vesselfinder.com/embed)가 만드는 iframe 과 같은 주소 — 스크립트의 document.write 는 동적 삽입이 안 돼 직접 만든다
  const[la,lo,z]=AREAS[a],h=Math.max(400,vf.clientHeight|0);
  const q=new URLSearchParams({zoom:z,lat:la,lon:lo,width:"100%",height:h,names:"false",track:"false",fleet:"false",fleet_name:"false",
    fleet_hide_old_positions:"false",clicktoact:"false",store_pos:"false",ra:location.href});
  vf.innerHTML=`<iframe title="VesselFinder 실시간 선박 지도" loading="lazy" src="https://www.vesselfinder.com/aismap?${q}"></iframe>`;
}
async function loadSea(){
  let d=null;try{const r=await fetch(RELAY+"/sea.json",{cache:"no-store"});if(r.ok)d=await r.json()}catch(_){}
  const wrap=$("seaMap").parentElement;let em=wrap.querySelector(".sempty");
  if(!d?.ys){
    if(!em){em=document.createElement("div");em.className="sempty";wrap.appendChild(em)}
    em.textContent="선박 수집 준비 중 — 연결되면 5분마다 갱신됩니다. 위 버튼으로 다른 해역 실시간 지도를 볼 수 있습니다.";
    return;
  }
  if(em)em.remove();SEA=d;
  const ships=d.ys.ships||[],cnt=new Map();
  seaLayer.clearLayers();
  for(const s of ships){const[n,c]=flagOf(s.mmsi);cnt.set(n,(cnt.get(n)||0)+1);
    if(s.tr?.length)L.polyline(s.tr,{color:c,weight:1,opacity:.5,interactive:false}).addTo(seaLayer);
    L.circleMarker([s.lat,s.lon],{radius:3.5,color:c,weight:1,fillColor:c,fillOpacity:.85})
      .bindTooltip(`${esc(s.name||"이름 미송출")} · ${n}<br>${s.sog!=null?s.sog+"노트":""}`).addTo(seaLayer)}
  $("seaN").textContent=ships.length;
  const base=d.ys.base;
  $("seaR").textContent=base?`평소 이 시각(${base}척)의 ${Math.round(ships.length/base*100)}%`:"평소 값 쌓는 중(7일)";
  $("seaR").classList.toggle("low",!!(base&&ships.length<base*.7));
  const m=d.meets||[],lg=d.log||[],KIND={nk_meet:"북 근접",stop_pair:"근접·정지"};
  const sm=$("seaMeet");
  sm.textContent=(m.length?m.map(x=>`${KIND[x.kind]||""} ${x.a} ↔ ${x.b} · ${x.h}시간`).join(" / "):"없음")+(d.log_n?` · 기록 ${d.log_n}건`:"");
  sm.classList.toggle("hit",m.length>0||lg.some(x=>x.kind==="nk_meet"));
  sm.title=lg.length?lg.slice(0,8).map(x=>`${new Date(x.at).toLocaleString("ko-KR",{month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"})} ${KIND[x.kind]} ${x.a.flag} ${x.a.name||x.a.mmsi} ↔ ${x.b.flag} ${x.b.name||x.b.mmsi} ${x.km}km${x.h?` ${x.h}시간`:""}`).join("\n")+"\n누르면 최근 기록을 지도에 다시 그립니다":"";
  sm.style.cursor=lg.length?"pointer":"";sm.onclick=lg.length?()=>replayMeet(lg[0]):null;
  const leg=$("seaLeg");leg.innerHTML=[...cnt].sort((a,b)=>b[1]-a[1]).map(([n,k])=>`<li style="color:${COL[n]}"><i></i>${n} <b>${k}</b></li>`).join("");
  leg.hidden=!$("seaVf").hidden;
  const at=new Date(d.at);$("seaAt").textContent=isNaN(at)?"—":`${String(at.getHours()).padStart(2,"0")}:${String(at.getMinutes()).padStart(2,"0")} 위치`;
}

// 근접 기록 다시 그리기 — 기록 시점의 두 배 위치·지난 1시간 항적(스크린샷 대신 저장된 좌표로 재현)
let meetLy=null;
function replayMeet(e){
  if(!seaMap)return;seaArea("ys");
  if(!meetLy)meetLy=L.layerGroup().addTo(seaMap);meetLy.clearLayers();
  for(const s of[e.a,e.b]){const[,c]=flagOf(s.mmsi);
    if(s.tr?.length)L.polyline(s.tr,{color:c,weight:2.5,opacity:.9}).addTo(meetLy);
    L.circleMarker([s.lat,s.lon],{radius:7,color:"#fff",weight:2,fillColor:c,fillOpacity:1})
      .bindTooltip(`${esc(s.flag)} ${esc(s.name||s.mmsi)}<br>MMSI ${esc(s.mmsi)}${s.sog!=null?` · ${s.sog}노트`:""}`,{permanent:true}).addTo(meetLy)}
  seaMap.fitBounds(L.latLngBounds([[e.a.lat,e.a.lon],[e.b.lat,e.b.lon],...(e.a.tr||[]),...(e.b.tr||[])]).pad(.6),{maxZoom:13});
  setTimeout(()=>{meetLy&&meetLy.clearLayers()},60000);
}

// ── ④ 지도 레이어 5묶음 ──
const GROUPS=[["영상",["day","night","hima"],true],["생활",["shelter","subway","cctv"]],["열점",["firms","s3","fwi"]],[null,["navwarn"]],[null,["inc"]]];
const lay=document.querySelector("#map-p .layers");
const incBtn=document.createElement("button");incBtn.type="button";incBtn.dataset.layer="inc";incBtn.setAttribute("aria-pressed","false");
incBtn.title="국내 사건 중 안보 연결이 보도된 것(채운 점 = 직접 · 빈 점 = 간접)";incBtn.textContent="사건";lay.appendChild(incBtn);
const btnOf=k=>lay.querySelector(`button[data-layer="${k}"]`);
const heads=[];
for(const[name,keys,radio]of GROUPS){
  const bs=keys.map(btnOf).filter(Boolean);if(!bs.length)continue;
  if(!name){lay.appendChild(bs[0]);continue}
  const g=document.createElement("div");g.className="lgp";
  const h=document.createElement("button");h.type="button";h.className="lgh";h.setAttribute("aria-expanded","false");
  const m=document.createElement("div");m.className="lgm";m.hidden=true;m.setAttribute("role","group");m.setAttribute("aria-label",name);
  bs.forEach(b=>m.appendChild(b));g.append(h,m);lay.appendChild(g);
  const paint=()=>{const on=bs.filter(b=>b.getAttribute("aria-pressed")==="true");
    h.innerHTML=radio?`${esc(on[0]?.textContent||name)} ▾`:`${name}${on.length?`<span class="n">${on.length}</span>`:""} ▾`;
    h.classList.toggle("on",!radio&&on.length>0)};
  paint();bs.forEach(b=>new MutationObserver(paint).observe(b,{attributes:true,attributeFilter:["aria-pressed","disabled"],childList:true,characterData:true,subtree:true}));
  h.addEventListener("click",e=>{e.stopPropagation();const open=m.hidden;closeMenus();m.hidden=!open;h.setAttribute("aria-expanded",open)});
  if(radio)m.addEventListener("click",e=>{if(e.target.closest("button"))setTimeout(closeMenus,0)});
  heads.push([h,m]);
}
function closeMenus(){for(const[h,m]of heads){m.hidden=true;h.setAttribute("aria-expanded","false")}}
document.addEventListener("click",e=>{if(!e.target.closest(".lgp"))closeMenus()});
document.addEventListener("keydown",e=>{if(e.key==="Escape")closeMenus()});
// 사건 레이어 — 본 스크립트의 지도(map)가 만들어진 뒤에 붙인다
let incLy=null;
function incLayer(){
  if(typeof map==="undefined"||!map||!INC)return;
  if(!incLy)incLy=L.layerGroup();
  incLy.clearLayers();
  for(const x of INC.items||[]){if(x.tier==="resolved"||x.where?.lat==null)continue;
    const d=x.tier==="direct";
    L.circleMarker([x.where.lat,x.where.lon],{radius:6,color:d?"#ff4d5e":"#f2c14e",weight:2,fillColor:"#ff4d5e",fillOpacity:d?.9:0})
      .bindTooltip(`${d?"직접":"간접"} · ${esc(x.what)}<br>${esc(x.where.place)} · ${day(x.when?.occurred)}`).addTo(incLy)}
}
incBtn.addEventListener("click",async()=>{
  if(!incLoaded)await loadInc();
  const on=incBtn.getAttribute("aria-pressed")!=="true";incBtn.setAttribute("aria-pressed",on);
  incLayer();if(incLy&&typeof map!=="undefined"&&map){on?incLy.addTo(map):incLy.remove()}
});

// ── ⑤ 오른쪽 아래 탭 칸(#dock-p) — 1180~1799(또는 높이 860 미만) 한 화면 격자에서만 ──
const MQ1=matchMedia("(min-width:1180px) and (min-height:680px)"),MQ4=matchMedia("(min-width:1800px) and (min-height:860px)");
const DOCK=[["log","log-p","알림 기록"],["world","world-p","세계"],["fac","fac-p","배경 요인"],["todo","todo-p","지금 할 일"]];
const home=new Map();let dock=null,dockSel=null;
function dockOn(){
  if(dock)return;
  dock=document.createElement("section");dock.className="p";dock.id="dock-p";dock.setAttribute("aria-label","기록·브리핑·배경");
  dock.innerHTML=`<div class="ph"><div class="wtab" role="tablist" aria-label="기록·브리핑·배경">${DOCK.map(([k,id,n],i)=>
    `<button type="button" role="tab" id="dt-${k}" aria-controls="${id}" aria-selected="${i===0}" tabindex="${i?-1:0}" data-t="${k}">${n}</button>`).join("")}</div></div><div class="dbody"></div>`;
  const body=dock.querySelector(".dbody");
  for(const[,id]of DOCK){const el=$(id);home.set(id,[el.parentNode,el.nextSibling]);body.appendChild(el)}
  // 「지금 할 일」 한 줄도 이 칸 위로 — 현재 등급 칸에 두면 1280 폭에서 감시 항목이 1~2행 줄었다(10/28 실측 7→5)
  const st=$("stodo");home.set("stodo",[st.parentNode,st.nextSibling]);dock.insertBefore(st,body);
  document.querySelector(".side").appendChild(dock);
  document.querySelector(".side").classList.add("docked");document.querySelector(".more").classList.add("docked");
  dockSel=tabs(dock.querySelector(".wtab"));dockSel(DOCK[0][0]);
}
function dockOff(){
  if(!dock)return;
  for(const id of ["stodo",...DOCK.map(d=>d[1]).reverse()]){const el=$(id),[p,n]=home.get(id);el.hidden=false;p.insertBefore(el,n&&n.parentNode===p?n:null)}
  dock.remove();dock=null;dockSel=null;
  document.querySelector(".side").classList.remove("docked");document.querySelector(".more").classList.remove("docked");
}
function dockFit(){(MQ1.matches&&!MQ4.matches)?dockOn():dockOff()}
MQ1.addEventListener("change",dockFit);MQ4.addEventListener("change",dockFit);dockFit();
// 머리 바로가기·「대피 요령 보기」가 아래로 스크롤하던 칸은 탭으로 연다
document.addEventListener("click",e=>{
  const a=e.target.closest('a[href="#world-p"],a[href="#todo-p"],a[href="#log-p"]');if(!a||!dock)return;
  const k=DOCK.find(d=>"#"+d[1]===a.getAttribute("href"))?.[0];if(!k)return;
  e.preventDefault();dockSel(k);dock.scrollIntoView({behavior:RM()?"auto":"smooth",block:"nearest"});
});
})();

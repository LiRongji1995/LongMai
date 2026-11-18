"""Stylesheets and the HTML template of the local pages (index and day pages)."""
from __future__ import annotations

INDEX_CSS = """
main{max-width:860px;padding-bottom:40px}body{padding-right:0!important}
.ix-hero{padding:36px 0 22px}.ix-kicker{color:var(--cyan);font-size:12px;font-weight:700;letter-spacing:.2em}
.ix-title{font-size:clamp(30px,6vw,44px);font-weight:800;letter-spacing:-.01em;margin:6px 0 8px}
.ix-sub{color:var(--dim);max-width:560px}
.ix-feat{position:relative;display:flex;align-items:center;justify-content:space-between;gap:20px;flex-wrap:wrap;
padding:26px 28px;border-radius:20px;text-decoration:none;color:var(--fg);overflow:hidden;
background:linear-gradient(135deg,rgba(56,225,255,.14),rgba(167,139,250,.12) 60%,rgba(18,26,44,.8));
box-shadow:0 0 0 1px rgba(56,225,255,.35),0 10px 28px -16px rgba(56,225,255,.4);transition:transform .2s}
.ix-feat:hover{transform:translateY(-3px)}
.ix-feat:after{content:"";position:absolute;right:-80px;top:-80px;width:240px;height:240px;border-radius:50%;
background:radial-gradient(rgba(167,139,250,.35),transparent 70%);pointer-events:none}
.ix-badge{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:700;color:var(--green);
padding:2px 10px;border-radius:999px;background:rgba(52,211,153,.12)}
.ix-badge:before{content:"";width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 8px var(--green)}
.ix-big{font-size:clamp(34px,8vw,56px);font-weight:800;line-height:1.05;margin:10px 0 4px;letter-spacing:-.02em;
background:linear-gradient(90deg,#fff,var(--cyan) 60%,var(--violet));-webkit-background-clip:text;background-clip:text;color:transparent}
.ix-big span{font-size:.42em;margin-left:10px;-webkit-text-fill-color:var(--dim);letter-spacing:0}
.ix-when{color:var(--dim);font-size:13px}
.ix-btn{position:relative;z-index:1;padding:12px 22px;border-radius:14px;font-weight:800;color:#08101d;white-space:nowrap;
background:linear-gradient(90deg,var(--cyan),var(--violet))}
.ix-h{display:flex;align-items:baseline;justify-content:space-between;margin:34px 0 12px;font-weight:700}
.ix-h small{font-weight:400}
.ix-list{display:grid;gap:10px}
.ix-row{background:var(--panel);border:1px solid var(--line);border-radius:14px;transition:border-color .15s}
.ix-row:hover{border-color:rgba(56,225,255,.45)}
.ix-main{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:16px;padding:14px 18px;
text-decoration:none;color:var(--fg)}
.ix-d{font-size:18px;font-weight:800;font-variant-numeric:tabular-nums}.ix-w{color:var(--dim);font-size:13px;margin-left:6px;font-weight:500}
.ix-t{color:var(--dim);font-size:13px;font-variant-numeric:tabular-nums}.ix-go{color:var(--cyan);font-weight:700}
.ix-row details{border-top:1px dashed var(--line);padding:8px 18px 12px}
.ix-row summary{cursor:pointer;color:var(--dim);font-size:12px;list-style:none}
.ix-row summary::-webkit-details-marker{display:none}.ix-row summary:before{content:"▸ ";color:var(--violet)}
.ix-row details[open] summary:before{content:"▾ "}
.ix-chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.ix-chip{font-size:12px;padding:3px 10px;border-radius:999px;color:var(--fg);text-decoration:none;
background:rgba(255,255,255,.05);border:1px solid var(--line);font-variant-numeric:tabular-nums}
.ix-chip:hover{border-color:var(--cyan);color:var(--cyan)}
.ix-warn{color:var(--amber);font-weight:700}
.ix-foot{margin:30px 0 0;color:var(--dim);font-size:12px;text-align:center}
@media (max-width:560px){.ix-main{grid-template-columns:1fr auto}.ix-t{grid-column:1/-1;order:3}}
"""


TEMPLATE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{title}}</title>
<style>
:root{--bg:#07090f;--bg2:#0c1220;--panel:rgba(18,26,44,.72);--line:rgba(120,160,255,.14);--fg:#e8edf7;--dim:#8a96ad;
--cyan:#38e1ff;--violet:#a78bfa;--amber:#fbbf24;--red:#ff5d6c;--green:#34d399;--lead:#ff8a3d;--ladder:#5b8cff;
--watch:#94a3b8;--risk:#ff5d6c;--glow:0 0 0 1px var(--line),0 6px 18px -12px rgba(56,225,255,.22)}
*{box-sizing:border-box}html{scroll-behavior:smooth;background:var(--bg)}
body{margin:0;color:var(--fg);font:14px/1.6 -apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
min-height:100vh}
/* the glow and grid sit on fixed layers of their own: painted once and composited, not repainted on every scroll */
body:before,body:after{content:"";position:fixed;inset:0;pointer-events:none;z-index:-1;will-change:transform;transform:translateZ(0)}
body:before{background:radial-gradient(1200px 600px at 10% -10%,rgba(56,225,255,.12),transparent 60%),
radial-gradient(900px 500px at 100% 0%,rgba(167,139,250,.14),transparent 60%),linear-gradient(var(--bg),var(--bg2))}
body:after{opacity:.35;background-image:linear-gradient(var(--line) 1px,transparent 1px),linear-gradient(90deg,var(--line) 1px,transparent 1px);
background-size:44px 44px;-webkit-mask-image:linear-gradient(to bottom,#000,transparent 70%);mask-image:linear-gradient(to bottom,#000,transparent 70%)}
main{position:relative;max-width:1240px;margin:0 auto;padding:0 16px 60vh}
.top{position:sticky;top:0;z-index:5;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 0;
background:linear-gradient(rgba(7,9,15,.97) 70%,rgba(7,9,15,0))}
.brand{font-weight:800;letter-spacing:.08em;display:flex;align-items:center;gap:8px;white-space:nowrap}

.dot{width:9px;height:9px;border-radius:50%;background:var(--cyan);box-shadow:0 0 12px var(--cyan)}
.side{position:fixed;right:22px;top:50%;transform:translateY(-50%);z-index:6;display:flex;flex-direction:column;gap:2px;
padding:14px 10px;border-radius:18px;background:rgba(12,18,32,.62);border:1px solid rgba(120,160,255,.2);
box-shadow:0 12px 32px -16px rgba(0,0,0,.6)}
.side-t{font-size:12px;color:var(--dim);letter-spacing:.12em;padding:0 12px 8px}
.side a{display:flex;align-items:center;gap:10px;color:#aab6cc;text-decoration:none;font-size:15px;padding:8px 14px 8px 10px;
border-radius:10px;white-space:nowrap;transition:background .15s,color .15s}
.side a i{width:6px;height:6px;border-radius:50%;background:rgba(170,182,204,.35);flex:none;transition:background .15s,box-shadow .15s}
.side a:hover{color:var(--fg);background:rgba(255,255,255,.05)}
.side a.on{color:var(--fg);background:rgba(56,225,255,.10)}.side a.on i{background:var(--cyan);box-shadow:0 0 8px var(--cyan)}
.side-btn{display:none}
@media (min-width:1101px){body{padding-right:210px}}
@media (max-width:1100px){.side{display:none;top:auto;bottom:72px;transform:none}.side.open{display:flex;background:rgba(12,18,32,.97)}
.side-btn{display:block;position:fixed;right:16px;bottom:20px;z-index:7;border:1px solid rgba(120,160,255,.3);border-radius:999px;
padding:9px 16px;font:inherit;font-size:14px;color:var(--fg);background:rgba(12,18,32,.75);cursor:pointer}}
section[id],.sec-h,details[id]{scroll-margin-top:78px}
.sec-h{margin:22px 0 0}.sec-h h2{margin:0}
.hop{display:flex;gap:6px;margin-right:auto;margin-left:10px}.hop a{color:var(--cyan);text-decoration:none;font-size:13px;
padding:3px 10px;border-radius:999px;border:1px solid var(--line);white-space:nowrap}.hop a:hover{border-color:var(--cyan);background:var(--panel)}
.hero{padding:18px 0 6px;display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 18px}
.date{font-size:clamp(34px,7vw,60px);font-weight:800;letter-spacing:-.02em;line-height:1;
background:linear-gradient(90deg,#fff,var(--cyan) 55%,var(--violet));-webkit-background-clip:text;background-clip:text;color:transparent}
.meta{color:var(--dim)}.tag{padding:2px 12px;border-radius:999px;border:1px solid var(--violet);color:var(--violet);font-weight:600}
section{background:var(--panel);border-radius:16px;padding:18px 20px;margin:22px 0;box-shadow:var(--glow)}
.sec-h+section{margin-top:12px}
h2{font-size:19px;margin:0 0 14px;padding-bottom:10px;border-bottom:1px solid var(--line);display:flex;align-items:center;
gap:10px;flex-wrap:wrap}
h2:before{content:"";width:4px;height:18px;border-radius:2px;background:linear-gradient(var(--cyan),var(--violet));flex:none}
.sec-h h2{border-bottom:0;padding-bottom:0}h3{font-size:14px;margin:16px 0 8px;color:var(--dim)}
small{color:var(--dim);font-weight:400;font-size:12px}.dim{color:var(--dim)}
.alert{background:linear-gradient(135deg,rgba(255,93,108,.18),rgba(251,191,36,.08));box-shadow:0 0 0 1px rgba(255,93,108,.45)}
.pulse{width:10px;height:10px;border-radius:50%;background:var(--red);box-shadow:0 0 8px var(--red);animation:pl 1.6s infinite}
@keyframes pl{50%{opacity:.35}}
.ok{color:var(--green)}ul{margin:0;padding-left:20px}
.dash{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;background:none;box-shadow:none;padding:0}
.gauge,.tile{position:relative;background:var(--panel);border-radius:16px;box-shadow:var(--glow);padding:14px;text-align:center}
.gauge svg{width:96px;height:96px;transform:rotate(-90deg)}.trk{fill:none;stroke:rgba(255,255,255,.07);stroke-width:8}
.val{fill:none;stroke:currentColor;stroke-width:8;stroke-linecap:round}
.t-mood{color:var(--cyan)}.t-heat{color:var(--amber)}.t-pain{color:var(--red)}
.gv{position:absolute;top:46px;left:0;right:0;font-size:24px;font-weight:800;color:var(--fg)}
.gl{font-weight:700;color:var(--fg)}.gs,.tl{color:var(--dim);font-size:12px}
.tile{display:flex;flex-direction:column;justify-content:center;gap:6px}.tv{font-size:22px;font-weight:800}
.slots{display:flex;gap:8px;justify-content:center}.slots i{width:22px;height:22px;border-radius:6px;background:rgba(255,255,255,.07)}
.slots i.on{background:linear-gradient(135deg,var(--cyan),var(--violet));box-shadow:0 0 12px rgba(56,225,255,.6)}
.note{color:var(--dim)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.thread,.stock{background:rgba(255,255,255,.03);border:1px solid var(--line);border-radius:14px;padding:12px;transition:transform .15s,border-color .15s}
.thread:hover,.stock:hover{transform:translateY(-2px);border-color:rgba(56,225,255,.5)}
.th,.sh{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.nm{font-weight:700;font-size:15px}
.age{font-size:12px;font-weight:700;padding:1px 8px;border-radius:999px;background:rgba(167,139,250,.18);color:var(--violet)}
.live{font-size:11px;padding:1px 8px;border-radius:999px;background:rgba(52,211,153,.15);color:var(--green)}
.spark{width:100%;height:46px;margin:8px 0}.spark rect{fill:rgba(91,140,255,.45)}.spark rect.peak{fill:var(--amber)}
.spark rect.last{fill:var(--cyan)}
.kv{color:var(--dim);font-size:13px}.kv b{color:var(--fg)}.role{margin:4px 0;font-size:13px}
.plan{margin-top:8px;font-size:13px;padding:6px 8px;border-radius:8px;background:rgba(56,225,255,.07);border-left:2px solid var(--cyan)}
.risk{margin-top:6px;font-size:12px;color:#ffb3ba}
.pos{height:4px;border-radius:4px;background:rgba(255,255,255,.08);margin:6px 0 2px;overflow:hidden;min-width:60px}
.pos span{display:block;height:100%;background:linear-gradient(90deg,var(--red),var(--amber),var(--green))}
.mls{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}.ml{padding:6px 12px;border-radius:10px;border:1px solid var(--line);font-size:13px}
.ml span{color:var(--dim);margin-left:6px}
.fates{display:flex;flex-wrap:wrap;gap:8px}.fate{padding:8px 12px;border-radius:10px;font-size:13px;display:flex;gap:8px}
.fate.up{background:rgba(52,211,153,.12);color:var(--green)}.fate.down{background:rgba(255,93,108,.12);color:#ffb3ba}.fate b{color:var(--fg)}
.chip{display:inline-block;padding:1px 9px;border-radius:999px;font-size:12px;font-weight:700;color:#08101d}
.g-lead{background:var(--lead)}.g-ladder{background:var(--ladder)}.g-watch{background:var(--watch)}.g-risk{background:var(--risk)}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}
.tab{border:0;cursor:pointer;border-radius:999px;padding:5px 14px;font:inherit;font-weight:700;color:#08101d;opacity:.45}
.tab.on{opacity:1;box-shadow:0 0 14px rgba(255,255,255,.18)}.pane{display:none}.pane.on{display:block}
.scroll{overflow-x:auto;border-radius:10px}table{border-collapse:collapse;width:100%;font-size:13px}
th{background:#111a2c;color:var(--dim);font-weight:600;white-space:nowrap;text-align:left;padding:8px}
td{border-top:1px solid var(--line);padding:8px;vertical-align:top;min-width:6em}td:first-child{white-space:nowrap;min-width:0}
tbody tr:hover{background:rgba(56,225,255,.06)}
.rev td{white-space:nowrap}.rev td small{color:var(--dim);font-size:11px}.rev tr.base td{color:var(--dim);border-top:1px dashed var(--line)}
.fullrep summary{color:var(--cyan);font-weight:700}.daily ul{margin:4px 0 8px;line-height:1.9}.fullrep h3{margin-top:14px}
.good{color:var(--green)}.bad{color:var(--red)}.legend{margin-top:10px}.legend ul{margin:6px 0}
.wave{border-color:rgba(167,139,250,.45)}.wave b{color:var(--violet)}
.thread.name{border-color:rgba(167,139,250,.5);background:linear-gradient(160deg,rgba(167,139,250,.10),rgba(255,255,255,.02))}
.xg{display:flex;flex-direction:column;margin-top:6px;font-size:13px}.xg b{color:var(--violet)}.xg span{color:var(--dim);font-size:12px}
.xuan{font-size:12px;font-weight:800;padding:1px 8px;border-radius:999px;background:var(--violet);color:#120b26}
.heat{table-layout:fixed;min-width:760px}.heat .c-b{width:150px}.heat .c-n{width:84px}.heat .c-s{width:auto}
.heat th.hn,.heat td.hn{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap;min-width:0;padding-right:14px}
.heat th.hs,.heat td.hs{padding-left:18px;padding-right:18px}.heat td{vertical-align:middle}
.heat .spark{display:block;width:100%;height:30px;margin:0}
.thread.open{cursor:pointer}.more{margin-top:6px;color:var(--cyan);font-size:12px;font-weight:700}
.tdlg{width:min(920px,calc(100vw - 32px));max-height:85vh;padding:18px;border:1px solid var(--line);border-radius:16px;
background:#0d1424;color:var(--fg)}.tdlg::backdrop{background:rgba(3,6,12,.72)}
.dh{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:12px}.dh b{font-size:17px}
.x{background:none;border:1px solid var(--line);color:var(--fg);border-radius:10px;font-size:20px;line-height:1;padding:4px 10px;cursor:pointer}
.tdlg td small{color:var(--dim);font-size:11px}.tdlg td.n{white-space:nowrap;min-width:90px}
.bar{display:block;height:4px;margin-top:4px;border-radius:2px;background:rgba(255,255,255,.07)}
.bar span{display:block;height:100%;border-radius:2px;background:var(--amber)}
.lus{line-height:1.9}.lus i{font-style:normal;font-size:11px;color:var(--amber);margin-left:2px}.in{color:var(--cyan);font-weight:700}
.foot{color:var(--dim);font-size:12px}pre{white-space:pre-wrap}summary{cursor:pointer}
@media (max-width:600px){.gauge svg{width:80px;height:80px}.gv{top:38px;font-size:20px}}
</style></head><body><main>
{{body}}
</main>
<script>
document.querySelectorAll('.tab').forEach(function(b){b.addEventListener('click',function(){
document.querySelectorAll('.tab,.pane').forEach(function(x){x.classList.remove('on')});
b.classList.add('on');document.getElementById(b.dataset.p).classList.add('on');});});
document.querySelectorAll('[data-d]').forEach(function(c){function o(){var d=document.getElementById(c.dataset.d);
if(d&&d.showModal)d.showModal();}c.addEventListener('click',o);c.addEventListener('keydown',function(e){if(e.key==='Enter')o();});});
document.querySelectorAll('dialog').forEach(function(d){d.addEventListener('click',function(e){if(e.target===d)d.close();});
d.querySelector('.x').addEventListener('click',function(){d.close();});});
(function(){var side=document.querySelector('.side');if(!side)return;
var links=[].slice.call(side.querySelectorAll('a'));
var targets=links.map(function(a){return document.getElementById(a.getAttribute('href').slice(1));});
var lock=null;function mark(k){links.forEach(function(a,i){a.classList.toggle('on',i===k);});}
links.forEach(function(a,i){a.addEventListener('click',function(e){var t=targets[i];if(!t)return;e.preventDefault();
if(t.tagName==='DETAILS')t.open=true;var top=t.getBoundingClientRect().top+window.pageYOffset-76;
window.scrollTo({top:Math.max(0,top),behavior:'smooth'});history.replaceState(null,'','#'+t.id);
mark(i);clearTimeout(lock);lock=setTimeout(function(){lock=null;},900);side.classList.remove('open');});});
var btn=document.querySelector('.side-btn');if(btn)btn.addEventListener('click',function(){side.classList.toggle('open');});
var ticking=false;function spy(){ticking=false;if(lock)return;var cur=0;
targets.forEach(function(t,i){if(t&&t.getBoundingClientRect().top<=120)cur=i;});mark(cur);}
addEventListener('scroll',function(){if(!ticking){ticking=true;requestAnimationFrame(spy);}},{passive:true});spy();})();
</script></body></html>
"""

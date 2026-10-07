const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
// Explicit TEST_FIXTURE: isolated synthetic DOM, responses and WebSocket lifecycle.
// Run with Node; no browser, server, market archive or runtime dependency is used.
const elements = new Map();
const classSet = () => {const set = new Set(); return {add(...v){v.forEach(x=>set.add(x));},remove(...v){v.forEach(x=>set.delete(x));},toggle(v,on){if(on)set.add(v);else set.delete(v);},contains(v){return set.has(v);}};};
const element = id => {if (!elements.has(id)) elements.set(id,{id,value:'0',checked:false,disabled:false,hidden:false,textContent:'',innerHTML:'',classList:classSet(),parentElement:{classList:classSet()},attributes:{},listeners:{},setAttribute(k,v){this.attributes[k]=v;},addEventListener(k,v){this.listeners[k]=v;}}); return elements.get(id);};
element('follow').checked=true;
const document={getElementById:element,body:{classList:classSet()},hidden:false};
const iso=(date,clock)=>`${date}T${clock}:00+08:00`;
const initialPast=process.env.STOCKFISH_DASHBOARD_INITIAL_ARCHIVE==='past';
let catalog={schema_version:'LIVE_DASHBOARD_DATES_V1',min_date:'2025-10-07',max_date:'2026-10-07',latest_date:'2026-10-07',available_dates:['2026-10-06','2026-10-07']};
if(initialPast)catalog.max_date='2026-10-08';
const clocks = date => date==='2026-10-06'?['09:40','09:44','09:45','09:46','15:00']:['09:40','09:45','09:46'];
let holdPastMarket=false, heldResolve=null;
const calls=[], sockets=[];
async function fetch(url){
  calls.push(url);
  const u=new URL(url,'http://localhost');
  if(u.pathname==='/api/live/dates')return {ok:true,json:async()=>structuredClone(catalog)};
  const date=u.searchParams.get('date');assert(catalog.available_dates.includes(date),'every scoped request must have an available date');
  const asOf=u.searchParams.get('as_of')||iso(date,clocks(date).at(-1));
  assert.equal(asOf.slice(0,10),date);
  if(!u.pathname.endsWith('/status')){
    assert(u.searchParams.get('as_of'),'every panel request must pin the selected observation time');
    assert(u.searchParams.get('index'),'every panel request must pin the selected index');
  }
  const index=u.searchParams.get('index')||'000001.SH';
  const metadata={as_of:asOf,data_cutoff:asOf,snapshot_id:asOf,run_status:'COMPLETE',fixture:true,pit_status:'TEST_FIXTURE',scope:'DECLARED_SCOPE_ONLY',input_hash:'abc',data_quality:{quality:'VALID'}};
  const path=(clock,close)=>({timestamp:iso(date,clock),close});
  const actual=[path('09:40',99),path('09:45',100),path(date==='2026-10-06'?'15:00':'09:46',108)].filter(p=>p.timestamp<=asOf);
  const forecast=asOf>=iso(date,'09:45')?{as_of:iso(date,'09:45'),decision_at:iso(date,'09:45'),current_price:100,confidence:'LOW',status:'AVAILABLE',horizons:[{status:'AVAILABLE',horizon:'15:00',target_at:iso(date,'15:00'),sample_size:50,price_quantiles:{q10:98,q25:99,q50:102,q75:104,q90:106}}]}:null;
  let result;
  if(u.pathname.endsWith('/status'))result={...metadata,timeline:clocks(date).map(clock=>iso(date,clock)),indices:['000001.SH','399006.SZ']};
  else if(u.pathname.endsWith('/market'))result={...metadata,indices:{[index]:{current_price:asOf>=iso(date,'09:45')?100:99,features:{}}},actual_paths:{[index]:actual},forecasts:forecast?{[index]:forecast}:{},market_state:{},warnings:[]};
  else if(u.pathname.endsWith('/history'))result={...metadata,history:forecast?[forecast]:[],revisions:[]};
  else if(u.pathname.endsWith('/similar-states'))result={...metadata,similar_states:[]};
  else if(u.pathname.endsWith('/comparison')){const closed=date==='2026-10-06';result={...metadata,actual_paths:{[index]:closed?[path('09:40',99),path('09:45',100),path('15:00',108)]:actual},actual_as_of:closed?iso(date,'15:00'):asOf,session_closed:closed,display_mode:closed?'CLOSED_SESSION_COMPARISON':'AS_OF_ONLY',archive_status:closed?'COMPLETE':'PARTIAL',forecast_status:forecast?'SAVED_FORECAST':'NO_SAVED_FORECAST'};}
  else throw new Error(`Unexpected route ${u.pathname}`);
  if(holdPastMarket && date==='2026-10-06' && u.pathname.endsWith('/market')){holdPastMarket=false;await new Promise(resolve=>heldResolve=resolve);}
  return {ok:true,json:async()=>result};
}
class WebSocket {static CONNECTING=0;static OPEN=1;constructor(url){this.url=url;this.readyState=1;sockets.push(this);}close(){this.readyState=3;}}
vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../stockfish_live/frontend/dashboard.js'),'utf8'),{document,fetch,WebSocket,location:{protocol:'http:',host:'localhost'},URLSearchParams,Intl,Date,Map,Set,Object,Number,String,Math,JSON,clearTimeout,setTimeout,setInterval(){},console});
const flush=async()=>{for(let i=0;i<12;i++)await new Promise(resolve=>setImmediate(resolve));};
const fire=(id,event)=>element(id).listeners[event]();
(async()=>{
  await flush();
  assert.equal(element('session-date').value,'2026-10-07');
  assert.equal(element('as-of').textContent,initialPast?'2026-10-07 09:45':'2026-10-07 09:46');
  if(initialPast)assert.equal(element('follow').checked,false);
  assert.equal(element('session-date').min,'2025-10-07');
  assert.equal(element('session-date').max,initialPast?'2026-10-08':'2026-10-07');
  if(!initialPast){
    assert.equal(element('follow').checked,true);
    assert.equal(element('actual-label').textContent,'观察时点前的真实走势');
    assert.match(element('comparison-text').textContent,/09:46.*不显示观察时点以后的真实行情/);
    assert.equal(new URL(sockets[0].url).searchParams.get('date'),'2026-10-07');
  }
  catalog.max_date='2026-10-07';
  element('session-date').value='2026-10-06';fire('session-date','change');await flush();
  assert.equal(element('follow').checked,false);
  assert.equal(element('as-of').textContent,'2026-10-06 09:45');
  assert.equal(element('price').textContent,'100.00');
  assert.equal(element('actual-label').textContent,'全天真实走势');
  const beforeIndex=calls.length;
  element('index').value='399006.SZ';fire('index','change');await flush();
  assert.equal(element('as-of').textContent,'2026-10-06 09:45');
  assert.match(element('chart').attributes['aria-label'],/^创业板指/);
  for(const url of calls.slice(beforeIndex).filter(value=>!value.startsWith('/api/live/dates')&&!value.startsWith('/api/live/status'))){
    const request=new URL(url,'http://localhost');assert.equal(request.searchParams.get('index'),'399006.SZ');assert.equal(request.searchParams.get('date'),'2026-10-06');assert.equal(request.searchParams.get('as_of'),iso('2026-10-06','09:45'));
  }
  assert.match(element('comparison-text').textContent,/09:45.*15:00/);
  assert.match(element('chart').innerHTML,/data-series="actual"/);
  assert.match(element('chart').innerHTML,/data-series="forecast-median"/);
  element('timeline').value='0';fire('timeline','input');fire('timeline','change');await flush();
  assert.equal(element('as-of').textContent,'2026-10-06 09:40');
  assert.doesNotMatch(element('chart').innerHTML,/data-series="forecast-median"/);
  assert.match(element('forecast-note').textContent,/未找到已保存预测/);
  assert.equal(element('actual-label').textContent,'全天真实走势');
  element('session-date').value='2026-10-05';fire('session-date','change');await flush();
  assert(document.body.classList.contains('has-error'));
  assert.match(element('error').textContent,/DATE_NOT_AVAILABLE/);
  fire('latest','click');await flush();
  assert.equal(element('session-date').value,'2026-10-07');
  assert.equal(element('as-of').textContent,'2026-10-07 09:46');
  assert(!document.body.classList.contains('has-error'));
  if(!initialPast){
    const beforeOldSocket=calls.length;
    sockets[0].onmessage({data:JSON.stringify({type:'snapshot'})});await flush();
    assert.equal(calls.length,beforeOldSocket,'a closed socket from a previous selection must not refresh the current page');
  }
  element('session-date').value='2024-10-05';fire('session-date','change');await flush();
  assert.match(element('error').textContent,/DATE_OUT_OF_RANGE/);
  fire('latest','click');await flush();
  holdPastMarket=true;element('session-date').value='2026-10-06';fire('session-date','change');await flush();
  assert(heldResolve);
  fire('latest','click');await flush();heldResolve();await flush();
  assert.equal(element('as-of').textContent,'2026-10-07 09:46','late previous-date response must not overwrite latest date');
  catalog={...catalog,available_dates:[],latest_date:null};fire('refresh','click');await flush();
  assert.match(element('error').textContent,/NO_AVAILABLE_DATES/);
  assert.equal(element('session-date').value,'');assert.equal(element('as-of').textContent,'—');
  assert.equal(element('session-date').disabled,true);assert.equal(element('follow').disabled,true);
  assert(document.body.classList.contains('has-error'));
  console.log(`Frontend state checks passed (${initialPast?'past archive initial':'today initial'}): date bounds, historical 09:45, closed actual/PIT quote, selected index/time, missing forecast/date, latest recovery, old socket/response race, empty catalog.`);
})().catch(error=>{console.error(error);process.exitCode=1;});

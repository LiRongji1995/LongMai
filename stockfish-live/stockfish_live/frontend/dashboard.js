"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const num = (value, digits=2) => finite(value) ? value.toLocaleString("zh-CN", {minimumFractionDigits:digits,maximumFractionDigits:digits}) : "—";
  const pct = (value, fraction=false) => finite(value) ? `${value > 0 ? "+" : ""}${num(value * (fraction ? 100 : 1))}%` : "—";
  const time = value => value ? new Intl.DateTimeFormat("zh-CN", {timeZone:"Asia/Shanghai",hour:"2-digit",minute:"2-digit",hour12:false}).format(new Date(value)) : "—";
  const observationTime = value => {
    if (!value) return "—";
    const parts = Object.fromEntries(new Intl.DateTimeFormat("zh-CN", {timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false}).formatToParts(new Date(value)).map(part => [part.type,part.value]));
    return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
  };
  const labels = Object.freeze({
    UNKNOWN:"未知", UNAVAILABLE:"暂不可用", AVAILABLE:"可用", PARTIAL:"部分可用", INSUFFICIENT:"样本不足",
    COMPLETE:"已完成", IN_PROGRESS:"运行中", FAILED:"运行失败", IMPLEMENTED:"已实现，等待预测",
    NOT_SCHEDULED:"非预测时点", WAITING_FOR_DECISION:"等待预测时点", WAITING_FOR_SCHEDULED_FORECAST:"等待定时预测",
    INSUFFICIENT_HISTORY:"历史记录不足", INSUFFICIENT_MATURE_HISTORY:"已成熟历史不足", NO_MATURE_LABELS:"没有已成熟标签",
    DATA_QUALITY_LIMITED:"数据质量受限", NOT_IMPLEMENTED_PHASE1:"第一阶段未启用模型",
    SOME_HORIZONS_OR_INDICES_UNAVAILABLE:"部分预测期限或指数不可用", NO_SAVED_FORECAST:"没有已保存预测",
    NO_SAVED_MARKET_STATE:"没有已保存市场状态", NO_SAVED_SNAPSHOT:"没有已保存快照",
    VALID:"有效", STALE:"已陈旧", GAPPED:"存在缺口", INVALID:"无效",
    VERIFIED_PIT:"已核验当时可见性", RECONSTRUCTED:"事后重建", RESEARCH_ONLY:"仅供研究", TEST_FIXTURE:"合成测试资料",
    DECLARED_SCOPE_ONLY:"仅限声明范围", DECLARED_SCOPE_PIT_MEMBERS_ONLY:"仅限当时可见的声明池成员",
    FULL_MARKET:"全市场", COMPLETE_MARKET:"全市场完整名单",
    HIGH:"高", MEDIUM:"中", LOW:"低", UNVALIDATED:"尚未验证", INITIAL_UNVALIDATED:"初始规则尚未验证",
    AVAILABLE_INITIAL_UNVALIDATED:"可用，初始规则尚未验证", UNAVAILABLE_NO_MATURE_LABELS:"暂不可用：没有已成熟标签",
    NOT_ESTABLISHED:"尚未确立", VERIFIED:"已核验", DISABLED:"已禁用", NONE:"无",
    TREND_UP:"趋势上涨", RANGE_STRONG:"偏强震荡", RANGE_NEUTRAL:"中性震荡", HIGH_OPEN_FADE:"高开回落",
    WEAKENING:"走弱", TREND_DOWN:"趋势下跌", UP:"上行", DOWN:"下行", RANGE:"震荡",
    WEAK:"低迷", REPAIR:"修复", EXPANSION:"扩张", STRONG:"强势", DIVERGENCE:"分化", RETREAT:"退潮",
    STRENGTHENING:"增强", STABLE_STRONG:"稳定偏强", REVERSING:"反转", NEUTRAL:"中性",
    STRONGER:"增强", CONTINUE:"延续", IMPROVING:"上调", DETERIORATING:"下调", UNCHANGED:"不变",
    COMMON_FUTURE_TARGET_PRICE:"共同未来目标价比较",
    BREADTH_SUPPORT_INSUFFICIENT:"市场宽度证据不足", ADVANCE_RATIO_5M_DECLINING:"近 5 分钟上涨比例下降",
    INDEX_UP_BREADTH_WEAK:"指数上涨，市场宽度偏弱", BROAD_ADVANCE_AND_POSITIVE_MEDIAN:"普涨且收益中位数为正",
    ADVANCE_RATIO_AND_MEDIAN_EXPANDING:"上涨比例与收益中位数扩张", ADVANCE_RATIO_5M_IMPROVING:"近 5 分钟上涨比例改善",
    LOW_ADVANCE_RATIO_OR_NEGATIVE_MEDIAN:"上涨比例偏低或收益中位数偏负", NO_RULE_CONFIRMED:"暂无规则确认",
    PIT_MARGINAL_SUPPORT_UNAVAILABLE:"缺少当时可见的边际证据", RELATIVE_STRENGTH_CROSSED_ZERO:"相对强度跨越零点",
    RELATIVE_STRENGTH_5M_IMPROVING:"近 5 分钟相对强度改善", RELATIVE_STRENGTH_5M_DECLINING:"近 5 分钟相对强度下降",
    POSITIVE_RELATIVE_STRENGTH_STABLE:"正向相对强度保持稳定", NO_DIRECTIONAL_MARGINAL_RULE_CONFIRMED:"暂无方向性边际规则确认",
    WATCH:"观察", DATA:"数据", MODEL:"模型"
  });
  const label = value => value == null || value === "" ? labels.UNKNOWN : Object.hasOwn(labels,value) ? labels[value] : `未识别状态（${String(value)}）`;
  const stateLabel = value => label(typeof value === "string" ? value : value?.state || "UNKNOWN");
  const horizonLabel = value => ({"30m":"未来 30 分钟","60m":"未来 60 分钟","11:30":"午间 11:30","14:00":"下午 14:00","15:00":"收盘 15:00"}[value] || `未识别期限（${String(value)}）`);
  const sectorProbability = sector => { const p=sector.continuation_probabilities; return p ? `延续 ${num(p.CONTINUE*100,0)}% / 增强 ${num(p.STRONGER*100,0)}%<br><small>走弱 ${num(p.WEAKENING*100,0)}% / 分化 ${num(p.DIVERGENCE*100,0)}%</small>` : finite(sector.continuation_probability) ? `${num(sector.continuation_probability*100,1)}%` : escape(label(sector.continuation_status || "UNAVAILABLE")); };
  const names = {"000001.SH":"上证指数","000300.SH":"沪深 300","000905.SH":"中证 500","000852.SH":"中证 1000","399006.SZ":"创业板指"};
  const warningLabels = {
    FULL_MARKET_BREADTH_UNAVAILABLE:"当前宽度只代表声明池，完整市场覆盖尚未取得。",
    INDEX_BREADTH_DIVERGENCE:"指数上涨，但上涨股票的比例偏低。",
    LOW_VOLUME_RALLY:"指数上涨，同时段成交额支持偏弱。",
    SECTOR_LEADER_WITHOUT_BREADTH:"行业龙头上涨，行业内多数股票尚未跟随。",
    DATA_STALE:"行情未及时更新，当前状态可能陈旧。",
    DATA_GAP:"分钟行情存在缺口，相关统计受限。",
    DATA_PARTIAL:"部分资料缺失，未知字段保留为空。",
    MODEL_LOW_CONFIDENCE:"历史样本或资料支持有限，预测置信度较低。",
    MODEL_PARAMETERS_UNVALIDATED:"模型参数尚未经过独立验证，不能据此认定投资优势。",
    REGIME_DISAGREEMENT:"相似历史对应多种行情状态，方向尚不集中。",
    PREDICTION_INTERVAL_WIDE:"历史结果分布较宽，预测的不确定性较大。",
    SIMILARITY_DISTANCE_HIGH:"当前状态与历史样本差异较大。",
    FORECAST_CONTINUOUS_IMPROVEMENT:"共同收盘目标价的中位预测连续上调。",
    FORECAST_CONTINUOUS_DETERIORATION:"共同收盘目标价的中位预测连续下调。",
    HISTORICAL_ELIGIBILITY_UNAVAILABLE:"部分证券缺少当时可见的历史资格，不能判断是否应纳入。",
    BREADTH_INCOMPLETE:"市场宽度资料不完整，部分统计暂不可用。",
    PIT_SECTOR_MEMBERSHIP_UNAVAILABLE:"尚未取得当时可见的行业成员资料。",
    INDEX_NOT_IN_DECLARED_SCOPE:"该指数不在本次声明的数据范围内。",
    INDEX_DATA_UNAVAILABLE:"缺少当时可见的指数行情。",
    INDEX_REFERENCE_PRICE_UNAVAILABLE:"缺少指数前收等参考价格，相关收益暂不可用。",
    FEATURES_PARTIALLY_UNAVAILABLE:"部分状态特征缺失，只使用已可用的特征。",
    QUERY_FEATURES_UNAVAILABLE:"当前状态缺少预测所需特征。",
    QUERY_FEATURES_STALE:"当前状态特征已陈旧，预测置信度受限。",
    INSUFFICIENT_MATURE_SAMPLES:"符合当时可见性要求的已成熟历史样本不足。",
    K_EXCEEDS_AVAILABLE_SAMPLES:"设定的相似样本数超过可用历史数量。",
    SMALL_SAMPLE_CONFIGURATION:"当前模型采用较小样本配置，置信度受限。",
    PIT_EVIDENCE_LIMITED:"历史来源的当时可见性证据有限。",
    REGIME_LABELS_PARTIALLY_UNAVAILABLE:"部分相似样本缺少成熟的市场状态标签。",
    FORECAST_NOT_SCHEDULED:"当前不在设定的预测时点。",
    LOW_CONFIDENCE_EARLY:"开盘早期证据较少，预测置信度受限。",
    NO_SAVED_SNAPSHOT:"尚无已保存快照。",
    PROVIDER_TIMEOUT:"行情来源请求超时。",
    PROVIDER_FAILURE:"行情来源请求失败。",
    INVALID_PROVIDER_DATA:"行情来源返回的数据未通过校验。"
  };
  function warningRows(warnings) {
    const groups = new Map();
    for (const warning of warnings) {
      const code = typeof warning === "string" ? warning : warning.code || warning.warning || "WARNING";
      if (!groups.has(code)) groups.set(code, []);
      groups.get(code).push(warning);
    }
    return [...groups].map(([code, records]) => {
      const identities = [...new Set(records.map(w => w.evidence?.instrument_id).filter(Boolean))];
      const instruments = identities.map(id => names[id] || id).join("、");
      const text = Object.hasOwn(warningLabels,code) ? warningLabels[code] : `尚未提供中文说明的提示（${code}），请展开查看原始证据。`;
      return `<li>${instruments ? `<strong>${escape(instruments)}</strong><br>` : ""}${escape(text)}<details><summary>查看来源与规则 · ${records.length} 条</summary><pre>${escape(JSON.stringify(records,null,2))}</pre></details></li>`;
    }).join("");
  }
  let timeline = [], selectedIndex = "000001.SH", generation = 0, socket = null, retry = null;
  let activeRefreshes = 0, pendingLiveRefresh = false, rendered = false;
  const emptyRow = (span, message) => `<tr><td colspan="${span}" class="empty">${escape(message)}</td></tr>`;
  const selectedTime = () => timeline[Number($("timeline").value)] || null;
  const query = asOf => new URLSearchParams({index:selectedIndex,...(asOf ? {as_of:asOf} : {})});
  async function get(url) { const response = await fetch(url, {cache:"no-store"}); const result = await response.json(); if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`); return result; }
  function fail(error) {
    const reasons={RUN_INTEGRITY_ERROR:"归档完整性校验失败",RUN_UNAVAILABLE:"运行资料暂不可用",RUN_INCOMPLETE:"运行尚未完成",FUTURE_ACCESS_DENIED:"所选时点超过已保存的最新时点",INVALID_AS_OF:"观察时点格式无效",INVALID_INDEX:"指数代码无效",ASSET_NOT_FOUND:"页面资源不存在"};
    document.body.classList.add("has-error"); document.body.classList.remove("loading"); $("error").hidden = false; $("error").textContent = `无法读取已验证的运行：${reasons[error.message] || "读取请求未成功"}（${error.message}）。未显示替代或未经校验的数据。`; $("connection").textContent = "读取失败";
  }
  function probability(container, probabilities) {
    const entries = Object.entries(probabilities || {});
    $(container).innerHTML = entries.length ? entries.map(([code,p]) => `<div class="probability"><span>${escape(label(code))}</span><div class="track"><div class="fill" style="width:${finite(p) ? Math.max(0,Math.min(100,p*100)) : 0}%"></div></div><b>${finite(p) ? Math.round(p*100) : "—"}%</b></div>`).join("") : '<div class="empty">暂不可用 · 没有成熟标签支持的概率分布</div>';
  }
  function chart(market, forecast) {
    const actual = (market.actual_paths[selectedIndex] || []).filter(p => finite(p.close));
    const horizons = (forecast?.horizons || []).filter(h => h.status === "AVAILABLE");
    const map = new Map();
    for (const h of horizons) {
      const q = h.price_quantiles || {};
      const values = Object.fromEntries(["q10","q25","q50","q75","q90"].map(key => [key, finite(q[key]) ? q[key] : finite(h.quantiles?.[key]) && finite(forecast.current_price) ? forecast.current_price*(1+h.quantiles[key]) : null]));
      if (Object.values(values).every(finite)) map.set(h.target_at, {timestamp:h.target_at,...values});
    }
    const future = [...map.values()].sort((a,b) => new Date(a.timestamp)-new Date(b.timestamp));
    if (future.length && finite(forecast.current_price)) future.unshift({timestamp:forecast.decision_at,...Object.fromEntries(["q10","q25","q50","q75","q90"].map(key => [key,forecast.current_price]))});
    const values = [...actual.map(p=>p.close),...future.flatMap(p=>[p.q10,p.q90])];
    if (!values.length) { $("chart").innerHTML = '<div class="empty">暂不可用 · 所选时点还没有可见指数行情</div>'; return; }
    const w=1120,h=280,left=70,right=24,top=15,bottom=35;
    let min=Math.min(...values),max=Math.max(...values),pad=(max-min)*.12 || Math.abs(min)*.002 || 1;
    min-=pad;max+=pad;
    const minutes=value=>{const t=new Date(value);return ((t.getUTCHours()+8)%24)*60+t.getUTCMinutes()-570;};
    const x=value=>left+minutes(value)/330*(w-left-right), y=value=>top+(max-value)/(max-min)*(h-top-bottom);
    const line=(points,key,breakGaps=false)=>points.map((p,i)=>`${i && !(breakGaps && new Date(p.timestamp)-new Date(points[i-1].timestamp)>60000)?"L":"M"}${x(p.timestamp).toFixed(2)},${y(p[key]).toFixed(2)}`).join(" ");
    const band=(lo,hi)=>`${line(future,hi)} ${[...future].reverse().map(p=>`L${x(p.timestamp).toFixed(2)},${y(p[lo]).toFixed(2)}`).join(" ")} Z`;
    const ticks=[0,60,120,210,270,330];
    let svg=`<svg viewBox="0 0 ${w} ${h}" xmlns="http://www.w3.org/2000/svg"><title>${escape(names[selectedIndex]||selectedIndex)} · 截至 ${escape(market.as_of)} 的实际行情与分位预测</title>`;
    svg+=`<rect x="${left+120/330*(w-left-right)}" y="${top}" width="${90/330*(w-left-right)}" height="${h-top-bottom}" fill="#f7f9f8"/><text x="${left+165/330*(w-left-right)}" y="${top+15}" text-anchor="middle" fill="#9aa7ac" font-size="10">午休</text>`;
    for(let i=0;i<5;i++){const price=min+(max-min)*i/4,yy=y(price);svg+=`<line x1="${left}" y1="${yy}" x2="${w-right}" y2="${yy}" stroke="#e9eeee"/><text x="${left-10}" y="${yy+3}" text-anchor="end" fill="#71838c" font-size="11">${num(price,price>100?1:2)}</text>`;}
    for(const tick of ticks){const xx=left+tick/330*(w-left-right),hour=Math.floor((570+tick)/60),minute=(570+tick)%60;svg+=`<text x="${xx}" y="${h-10}" text-anchor="middle" fill="#71838c" font-size="11">${String(hour).padStart(2,"0")}:${String(minute).padStart(2,"0")}</text>`;}
    if(future.length>1) svg+=`<path d="${band("q10","q90")}" fill="#ebe6f7"/><path d="${band("q25","q75")}" fill="#c4bce9" fill-opacity=".8"/><path d="${line(future,"q50")}" fill="none" stroke="#7265b7" stroke-width="2" stroke-dasharray="5 4"/>`;
    if(actual.length) svg+=`<path d="${line(actual,"close",true)}" fill="none" stroke="#087f83" stroke-width="2.4"/><circle cx="${x(actual.at(-1).timestamp)}" cy="${y(actual.at(-1).close)}" r="3" fill="#087f83"/>`;
    if(market.as_of) svg+=`<line x1="${x(market.as_of)}" y1="${top}" x2="${x(market.as_of)}" y2="${h-bottom}" stroke="#87979e" stroke-dasharray="3 4"/>`;
    $("chart").innerHTML=svg+"</svg>";
  }
  function render(market,history,similar) {
    rendered = true;
    document.body.classList.remove("loading", "has-error"); $("error").hidden=true; $("run-status").textContent=label(market.run_status); $("fixture-banner").hidden=!market.fixture;
    $("as-of").textContent=observationTime(market.as_of); $("cutoff").textContent=`数据截止 ${time(market.data_cutoff)}`;
    const index=market.indices[selectedIndex] || {}, forecast=market.forecasts[selectedIndex], state=market.market_state || {};
    $("price").textContent=num(index.current_price,2); const ret=index.features?.return_pct;
    $("return").textContent=finite(ret)?`较前收 ${pct(ret)}`:"前收参考暂不可用"; $("return").className=ret>=0?"positive":"negative";
    $("confidence").textContent=forecast?`置信度：${label(forecast.confidence)} · 参数${label(forecast.parameters_status || "UNVALIDATED")}`:"模型暂不可用";
    $("forecast-note").textContent=forecast?`预测 ${time(forecast.as_of)} · ${label(forecast.status)} · ${(forecast.horizons||[]).map(h=>`${horizonLabel(h.horizon)}：${h.sample_size} 个样本`).join(" / ")}`:"未找到已保存预测；不会生成替代曲线。";
    chart(market,forecast);
    const forecastHistory=history.history || [];
    $("revisions").innerHTML=forecastHistory.length?forecastHistory.slice(-8).reverse().map(f=>{const close=(f.horizons||[]).find(h=>h.horizon==="15:00");return `<tr><td>${time(f.as_of)}</td><td>${close?.status==="AVAILABLE"?`${num(close.price_quantiles?.q50)} (${pct(close.quantiles?.q50,true)})`:escape(label(close?.status || "UNAVAILABLE"))}</td><td>${escape(label(f.confidence))}</td></tr>`;}).join(""):emptyRow(3,"暂不可用 · 还没有已保存预测");
    const revision=(history.revisions||[]).at(-1)?.revision;
    $("revision-note").textContent=revision?`${label(revision.comparison_anchor || "COMMON_FUTURE_TARGET_PRICE")} · ${(revision.comparisons||[]).map(c=>`较 ${c.lookback_minutes} 个交易分钟前 ${pct(c.delta_q50_price_fraction,true)}`).join(" / ") || label(revision.status)}`:"相对于预测当时的收益与共同未来目标价分别保留；不以不同锚点收益直接比较。";
    const regime=(forecast?.horizons||[]).find(h=>h.horizon==="15:00"&&Object.keys(h.regime_probabilities||{}).length)||(forecast?.horizons||[]).find(h=>Object.keys(h.regime_probabilities||{}).length);
    $("regime-horizon").textContent=regime?`${horizonLabel(regime.horizon)} · ${regime.regime_sample_size} 个样本`:"没有已成熟标签"; probability("regime",regime?.regime_probabilities);
    const breadth=state.breadth || {};
    $("breadth-scope").textContent=breadth.full_market_status==="AVAILABLE"?"全市场":"仅限声明范围";
    const metrics=[["上涨",breadth.advance_count,0],["下跌",breadth.decline_count,0],["上涨比例",finite(breadth.advance_ratio)?breadth.advance_ratio*100:null,"%"],["中位收益",breadth.median_return_pct,"%"],["涨停",breadth.limit_up_count,0],["跌停",breadth.limit_down_count,0],["成交额 / 亿元",finite(breadth.cumulative_turnover)?breadth.cumulative_turnover/1e8:null,2],["同时段成交比",breadth.market_turnover_ratio,2]];
    $("breadth").innerHTML=metrics.map(([label,value,format])=>`<div class="metric"><span>${label}</span><strong>${format==="%"?`${finite(value)?num(value):"—"}${finite(value)?"%":""}`:num(value,format)}</strong></div>`).join("");
    $("breadth-note").textContent=`全市场覆盖：${label(breadth.full_market_status||"UNAVAILABLE")} · 已知资格 ${breadth.qualified_count??"—"} · 有收益参考 ${breadth.return_observed_count??"—"} · 未知资格 ${breadth.eligibility_unknown_count??"—"}`;
    const sentiment=state.sentiment || {};
    $("sentiment").innerHTML=`<div class="tide"><div><span>前一状态</span><strong>${escape(stateLabel(sentiment.previous_state))}</strong></div><span class="arrow">→</span><div><span>当前状态</span><strong>${escape(stateLabel(sentiment.state))}</strong></div></div><div class="reasons">${(sentiment.reasons||[]).map(r=>`<span>${escape(label(r))}</span>`).join("")}</div><div id="sentiment-probabilities" class="probabilities"></div><p class="note">下一状态概率：${escape(label(sentiment.next_state_status||"UNAVAILABLE_NO_MATURE_LABELS"))} · 初始规则尚未验证</p>`;
    probability("sentiment-probabilities",sentiment.next_state_probabilities);
    const sectorContainer=state.sectors || {}, sectors=Array.isArray(sectorContainer)?sectorContainer:(sectorContainer.sectors||[]);
    $("sector-status").textContent=`申万一级 · ${label(sectorContainer.status||"UNAVAILABLE")}`;
    $("sectors").innerHTML=sectors.length?sectors.map(s=>`<tr><td>${escape(s.sector_name||s.sector_id)}</td><td>${pct(s.sector_return_pct)}</td><td>${pct(s.relative_strength_vs_market_pct)}</td><td>${pct(s.relative_strength_change_5m_pct)}</td><td>${escape(label(s.marginal_state||s.state||"UNKNOWN"))}</td><td>${sectorProbability(s)}</td></tr>`).join(""):emptyRow(6,"暂不可用 · 未取得当时可见的行业成员与行情证据");
    const warnings=market.warnings||[]; $("warnings").innerHTML=warnings.length?warningRows(warnings):'<li>没有触发已定义规则；这不表示没有市场风险。</li>';
    $("quality").textContent=`数据质量：${label(market.data_quality?.quality||"UNAVAILABLE")}`;
    const evidence=[["当时可见性等级",label(market.pit_status)],["数据范围",label(market.scope)],["输入哈希",market.input_hash?.slice(0,18)||"暂不可用"],["预测版本",forecast?.schema_version||"暂不可用"],["模型参数",label(forecast?.parameters_status||"UNVALIDATED")],["长期环境",index.features?.long_return_20d_pct==null?"暂不可用":pct(index.features.long_return_20d_pct)]];
    $("evidence").innerHTML=evidence.map(([k,v])=>`<dt>${escape(k)}</dt><dd>${escape(v)}</dd>`).join("");
    $("similar").innerHTML=similar.similar_states?.length?similar.similar_states.map(s=>`<tr><td>${escape(s.decision_at?.slice(0,10))} ${time(s.decision_at)}</td><td>${num(s.distance,3)}</td><td>${escape(stateLabel(s.context?.regime||s.context?.long_term_regime))} / ${escape(stateLabel(s.context?.sentiment||s.context?.sentiment_state))}</td><td>${pct(s.future_results?.["30m"],true)}</td><td>${pct(s.future_results?.["60m"],true)}</td><td>${pct(s.future_results?.["15:00"],true)}</td></tr>`).join(""):emptyRow(6,"样本不足 · 没有符合当时可见性与成熟条件的历史相似状态");
  }
  async function refresh({latest=false,retainCurrent=false}={}) {
    const token=++generation; activeRefreshes++;
    if(!retainCurrent || !rendered)document.body.classList.add("loading");
    $("connection").textContent="正在读取…";
    try {
      const status=await get("/api/live/status");
      if(token!==generation)return;
      const old=selectedTime(); timeline=status.timeline||[];
      $("timeline").max=Math.max(0,timeline.length-1); $("timeline").disabled=!timeline.length;
      $("timeline").value=latest||$("follow").checked||!old?timeline.length-1:Math.max(0,timeline.indexOf(old));
      const identities=status.indices?.length?status.indices:["000001.SH"];
      if(!identities.includes(selectedIndex))selectedIndex=identities[0];
      $("index").innerHTML=identities.map(id=>`<option value="${escape(id)}">${escape(names[id]||id)} · ${escape(id)}</option>`).join(""); $("index").value=selectedIndex;
      const asOf=selectedTime(),q=query(asOf);
      const [market,history,similar]=await Promise.all([get(`/api/live/market?${q}`),get(`/api/live/forecast/history?${q}`),get(`/api/live/similar-states?${q}`)]);
      if(token!==generation)return;
      render(market,history,similar); $("connection").textContent=$("follow").checked?"已同步":"历史观察";
    } catch(error){if(token===generation)fail(error);}
    finally {
      activeRefreshes--;
      if(!activeRefreshes && pendingLiveRefresh && $("follow").checked){
        pendingLiveRefresh=false;refresh({latest:true,retainCurrent:true});
      }
    }
  }
  function connect() {
    if(socket){socket.onclose=null;socket.close();socket=null;}clearTimeout(retry);
    if(!$("follow").checked)return;
    socket=new WebSocket(`${location.protocol==="https:"?"wss":"ws"}://${location.host}/ws/live`);
    socket.onmessage=event=>{const data=JSON.parse(event.data);if(data.type==="snapshot"){
      if(activeRefreshes)pendingLiveRefresh=true;else refresh({latest:true,retainCurrent:true});
    }else if(data.type==="error")fail(new Error(data.error));};
    socket.onclose=()=>{socket=null;if($("follow").checked)retry=setTimeout(connect,2500);};
    socket.onerror=()=>{$("connection").textContent="连接恢复中";};
  }
  $("timeline").addEventListener("input",()=>{generation++; document.body.classList.add("loading"); $("follow").checked=false;connect();$("as-of").textContent=observationTime(selectedTime());});
  $("timeline").addEventListener("change",()=>refresh());
  $("index").addEventListener("change",()=>{selectedIndex=$("index").value;refresh();});
  $("follow").addEventListener("change",()=>{connect();refresh({latest:$("follow").checked});});
  $("latest").addEventListener("click",()=>{$("follow").checked=true;connect();refresh({latest:true});});
  $("refresh").addEventListener("click",()=>refresh());
  refresh({latest:true});connect();
})();

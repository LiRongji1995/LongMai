/* Candidate facts only. No account, order size, LLM vote or model-selection dependency. */
(() => {
  'use strict';

  const $ = id => document.getElementById(id);
  const families = [
    ['OVERVIEW', '观察分配'],
    ['FOCUS', '重点'],
    ['ONE_TO_TWO', '首板 · 1进2'],
    ['TWO_TO_THREE', '2进3'],
    ['TREND_NM', 'N天M板'],
    ['HIGH_BOARD', '高标接力'],
    ['REBOUND_SETUP', '断板恢复'],
  ];
  const labels = { B: '板结构', V: '量价换手', T: '主题共振', L: '相对强度', G: '历史股性', M: '市场情绪' };
  const tiers = {
    FOCUS: '重点',
    WATCH: '观察',
    STRUCTURAL_MATCH: '结构匹配',
    DATA_PENDING: '数据待齐',
    UNAVAILABLE: '不可用',
    DIAGNOSTIC: '价格诊断',
    EXCLUDED: '排除',
  };
  let current = null;
  let selectedFamily = 'OVERVIEW';
  let loadedFamily = 'OVERVIEW';
  let currentConfig = null;
  let latestCoverage = null;

  const textNode = (tag, text, cls) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = String(text);
    if (cls) el.className = cls;
    return el;
  };
  const display = value => value === null || value === undefined ? '不可用' :
    typeof value === 'number' ? value.toFixed(2) : String(value);

  function message(text, error = false) {
    $('message').textContent = text;
    $('message').className = 'notice' + (error ? ' error' : '');
  }

  function headers() {
    const value = { 'Content-Type': 'application/json' };
    if ($('apiKey').value) value.Authorization = 'Bearer ' + $('apiKey').value;
    return value;
  }

  async function api(path, body) {
    const response = await fetch(path, {
      headers: headers(),
      ...(body === undefined ? {} : { method: 'POST', body: JSON.stringify(body) }),
    });
    const value = await response.json();
    if (!response.ok) throw new Error((value.error || response.status) + (value.fields?.length ? '：' + value.fields.join(', ') : ''));
    return value;
  }

  function details(title, value) {
    const el = textNode('details');
    el.append(textNode('summary', title), textNode('pre', JSON.stringify(value, null, 2)));
    return el;
  }

  function tabCount(id) {
    if (!current) return null;
    if (id === 'OVERVIEW') return current.display_ids.length;
    if (id === 'FOCUS') return current.focus_ids.length;
    return current.family_counts?.[id]?.total ?? current.items.filter(i => i.primary_family === id).length;
  }

  function drawTabs() {
    const tabs = $('familyTabs');
    tabs.replaceChildren();
    for (const [id, name] of families) {
      const count = tabCount(id);
      const button = textNode('button', name + (count === null ? '' : ' · ' + count));
      button.setAttribute('role', 'tab');
      button.setAttribute('aria-selected', String(selectedFamily === id));
      button.onclick = () => {
        if (current) loadRun(current.run_id, id).catch(e => message(e.message, true));
      };
      tabs.append(button);
    }
  }

  function currentItemsForView() {
    if (!current) return [];
    if (loadedFamily !== selectedFamily) return [];
    return current.items;
  }

  async function loadItemDetail(item, slot, button) {
    button.disabled = true;
    try {
      const full = await api('/api/candidate-runs/' + encodeURIComponent(current.run_id) + '/items/' + encodeURIComponent(item.instrument_id));
      const data = full.item;
      slot.replaceChildren(
        details('今日与历史量价 · 定义/单位/窗口/来源', data.features),
        details('主题同伴与选择依据', data.theme_evidence),
        details('资金与龙虎榜证据', data.funds_evidence),
        details('冻结的竞价证伪条件', data.thesis_conditions),
        details('排除理由、风险和来源引用', {
          reason_codes: data.reason_codes,
          risk_flags: data.risk_flags,
          evidence_refs: data.evidence_refs,
        }),
      );
    } catch (error) {
      button.disabled = false;
      message(error.message, true);
    }
  }

  function drawCandidates() {
    const root = $('candidates');
    root.replaceChildren();
    if (!current) {
      const empty = textNode('div', undefined, 'empty');
      empty.append(textNode('h3', '等待已保存的真实计算'), textNode('p', '可信输入尚未就绪时，不生成示例榜或正常候选。'));
      root.append(empty);
      return;
    }
    const items = currentItemsForView();
    if (!items.length) {
      const el = textNode('div', undefined, 'empty');
      el.append(
        textNode('h3', current.status === 'UNAVAILABLE' ? '关键数据不可用' : '该视图没有候选'),
        textNode('p', '不放宽条件填满名额。各族保留全部结构命中与数据缺失原因。'),
      );
      root.append(el);
      return;
    }
    for (const item of items) {
      const card = textNode('article', undefined, 'candidate');
      const head = textNode('div', undefined, 'card-heading');
      const identity = textNode('div');
      identity.append(textNode('div', item.instrument_id, 'security-id'), textNode('h3', item.name || item.instrument_id));
      identity.append(
        textNode('span', tiers[item.tier] || item.tier, 'badge ' + (item.tier === 'FOCUS' ? 'focus' : item.score === null ? 'pending' : '')),
        textNode('span', families.find(f => f[0] === item.primary_family)?.[1] || item.primary_family, 'badge'),
      );
      const score = textNode('div', undefined, 'score');
      score.append(textNode('small', '规则匹配分'), textNode('span', display(item.score)));
      head.append(identity, score);
      card.append(head);

      const feature = name => item.features[name]?.value;
      card.append(textNode('p', `同族第 ${item.family_rank ?? '—'} · 连板 ${display(feature('streak_exchange'))} · 5日 ${display(feature('M5'))} 板 / 10日 ${display(feature('M10'))} 板`));
      const comps = textNode('div', undefined, 'components');
      for (const key of ['B', 'V', 'T', 'L', 'G', 'M']) {
        const el = textNode('div', labels[key], 'component');
        el.append(textNode('strong', display(item.components[key])));
        const bar = document.createElement('i');
        bar.style.width = item.components[key] === null ? '0' : Math.max(0, Math.min(100, item.components[key])) + '%';
        el.append(bar);
        comps.append(el);
      }
      card.append(comps);
      card.append(textNode('p', '主主题：' + (item.primary_theme || '未取得合格主题') + ' · ' + (item.secondary_tags.join(' / ') || '无其他策略命中')));
      if (item.risk_flags.length) card.append(textNode('p', '风险：' + item.risk_flags.join(' / ')));
      if (item.missing.length) card.append(textNode('p', '数据缺口：' + item.missing.join(' / ')));

      const evidence = textNode('details');
      evidence.append(textNode('summary', '展开摘要、来源与证伪条件'));
      const table = textNode('table');
      const header = textNode('tr');
      ['分项', '原分', '权重', '贡献'].forEach(t => header.append(textNode('th', t)));
      table.append(header);
      for (const key of ['B', 'V', 'T', 'L', 'G', 'M']) {
        const row = textNode('tr');
        [labels[key], display(item.components[key]), item.weights[key] + '%', display(item.contributions[key])].forEach(t => row.append(textNode('td', t)));
        table.append(row);
      }
      const detailSlot = textNode('div');
      const detailButton = textNode('button', '加载完整详情', 'secondary');
      detailButton.onclick = () => loadItemDetail(item, detailSlot, detailButton);
      evidence.append(
        table,
        details('轻量特征摘要', item.features),
        details('主题与资金摘要', { theme: item.theme_summary, funds_status: item.funds_status }),
        detailButton,
        detailSlot,
      );
      card.append(evidence);
      root.append(card);
    }
  }

  function summaryQuery(view) {
    const params = new URLSearchParams({ page_size: '50' });
    if (view === 'OVERVIEW') params.set('selection', 'display');
    else if (view === 'FOCUS') params.set('selection', 'focus');
    else params.set('family', view);
    return params.toString();
  }

  async function loadRun(id, view = 'OVERVIEW') {
    selectedFamily = view;
    loadedFamily = view;
    current = await api('/api/candidate-runs/' + encodeURIComponent(id) + '/summary?' + summaryQuery(view));
    $('runCount').textContent = current.item_total + ' 个结构命中';
    $('runStatus').textContent = current.status + ' · ' + current.profile + (current.has_next ? ' · 已分页' : '');
    $('emotionScore').textContent = display(current.emotion.score) + ' / ' + current.emotion.state;
    $('emotionText').textContent = current.emotion.readings?.delta_status === 'UNKNOWN_DELTA'
      ? '前三个交易日情绪记录未齐，d 不参与分类。'
      : current.emotion.reasons?.length ? '共同数据未核齐：' + current.emotion.reasons.length + ' 项缺口，详见覆盖记录。' : '读数与分母见覆盖记录';
    $('runIdentity').textContent = `${current.run_id} · revision ${current.revision} · 观察日 ${current.observation_date} · 截止 ${current.decision_cutoff} · 实际生成 ${current.generated_at} · ${current.coverage_type}`;
    $('coverageJson').textContent = JSON.stringify({
      coverage_summary: current.coverage_summary,
      emotion: current.emotion,
      source_ref_count: current.source_ref_count,
      config_hash: current.config_hash,
      source_hash: current.source_hash,
      raw_bundle_hash: current.raw_bundle_hash,
      effective_input_hash: current.effective_input_hash,
      hash_contract_version: current.hash_contract_version,
      semantic_hash: current.semantic_hash,
      semantic_hash_version: current.semantic_hash_version,
    }, null, 2);
    $('excludedJson').textContent = JSON.stringify({
      excluded_summary: current.excluded_summary,
      unavailable_summary: current.unavailable_summary,
      detail: '完整排除/缺口见完整 JSON 导出或候选详情接口。',
    }, null, 2);
    for (const id of ['downloadJson', 'downloadCsv', 'auctionReview', 'outcomeReview']) $(id).disabled = false;
    drawTabs();
    drawCandidates();
    await loadObservations();
  }

  async function loadObservations() {
    if (!current) return;
    const [outcomes, reviews] = await Promise.all([
      api('/api/candidate-runs/' + current.run_id + '/outcomes'),
      api('/api/candidate-runs/' + current.run_id + '/auction-review'),
    ]);
    $('outcomesJson').textContent = outcomes.length ? JSON.stringify(outcomes, null, 2) : '尚未追加结果。未到期不填0、不计入成熟样本分母。';
    const root = $('auctionResults');
    root.replaceChildren();
    if (!reviews.length) return;
    const latest = reviews[reviews.length - 1];
    root.append(textNode('p', '竞价复核：' + latest.status + ' · ' + latest.coverage_type + ' · ' + latest.evaluated_at));
    const order = { FOCUS: 0, WATCH: 1, STRUCTURAL_MATCH: 2, DIAGNOSTIC: 3, DATA_PENDING: 4, UNAVAILABLE: 5 };
    const familyOf = id => current.items.find(i => i.instrument_id === id)?.primary_family;
    const familyOrder = id => families.findIndex(f => f[0] === familyOf(id));
    const items = [...latest.evaluations].sort((a, b) =>
      familyOrder(a.instrument_id) - familyOrder(b.instrument_id)
      || (order[a.reviewed_tier] ?? 9) - (order[b.reviewed_tier] ?? 9)
      || (a.original_rank ?? 1e9) - (b.original_rank ?? 1e9)
      || a.instrument_id.localeCompare(b.instrument_id));
    for (const e of items) {
      const row = textNode('div', undefined, 'review-row');
      row.append(
        textNode('strong', (families.find(f => f[0] === familyOf(e.instrument_id))?.[1] || '') + ' · ' + e.instrument_id + ' · ' + (tiers[e.reviewed_tier] || e.reviewed_tier)),
        textNode('p', `原关注 ${e.original_tier} · 原同族位置 ${e.original_rank ?? '—'} · 原始分 ${display(e.original_score)} · ${e.flags.join(' / ') || '无已确认触发项'}`),
        details('TRUE / FALSE / NOT_EVALUATED 条件', e.conditions),
      );
      root.append(row);
    }
  }

  function drawMarketPreview(coverage) {
    latestCoverage = coverage;
    const root = $('marketPreview');
    root.replaceChildren();
    const all = coverage.current_event_previews || [];
    const filter = $('rawEvidenceFilter').value.trim().toUpperCase();
    const matched = filter ? all.filter(r => r.instrument_id.includes(filter)) : all;
    const rows = matched.slice(0, 50);
    if (!rows.length) {
      root.append(textNode('p', '无可核验的当日价格事实。'));
      return;
    }
    root.append(textNode('p', coverage.observation_date + ` · 已登记 ${all.length} 条当日事实，匹配 ${matched.length} 条，本页显示 ${rows.length} 条。完整候选资格另行校验。`));
    for (const row of rows) {
      const el = textNode('div', undefined, 'review-row');
      el.append(textNode('strong', row.instrument_id + ' · 收盘 ' + display(row.close) + ' · 收盘封板 ' + row.event.close_limit_up));
      const button = textNode('button', '查看原始日线与来源', 'secondary');
      button.onclick = async () => {
        try {
          const data = await api('/api/instruments/' + encodeURIComponent(row.instrument_id) + '/evidence?input_id=' + encodeURIComponent(coverage.input_id));
          el.append(details('原始来源、时点和字段', data));
          button.disabled = true;
        } catch (e) {
          message(e.message, true);
        }
      };
      el.append(button);
      root.append(el);
    }
  }

  async function refresh() {
    try {
      await refreshPipeline();
      const [config, coverage, runs, schedule] = await Promise.all([
        api('/api/strategy-configs/current'),
        api('/api/universe/coverage'),
        api('/api/candidate-runs'),
        api('/api/candidate-schedule'),
      ]);
      if (!currentConfig || currentConfig.config_hash !== config.config_hash) $('profile').value = config.config.funds_primary ? 'FUNDS_V1' : 'DAILY_V1';
      currentConfig = config;
      $('strategyVersion').textContent = config.config.strategy_id;
      $('configHash').textContent = config.config_hash.slice(0, 20) + '…';
      $('configEditor').value = JSON.stringify(config.config, null, 2);
      $('dataDate').textContent = coverage.observation_date || 'UNAVAILABLE';
      $('coverageText').textContent = coverage.coverage_type ? `${coverage.active ? '已激活' : '未激活诊断输入'} · ${coverage.status} · ${coverage.coverage_type} · ${coverage.bar_rows} 条日线` : (coverage.reason_codes || []).join(' / ');
      if (coverage.input_id && !coverage.active && !$('inputId').value) $('inputId').value = coverage.input_id;
      if (coverage.observation_date && !$('observationDate').value) $('observationDate').value = coverage.observation_date;
      if (coverage.stale) $('coverageText').textContent += ' · 陈旧历史，最近应有收盘日 ' + coverage.expected_observation_date;
      const part = coverage.coverage;
      if (part) $('coverageText').textContent += ` · 身份 ${part.identity_observed}/${part.master_expected}，合格 ${part.eligible}，已知排除 ${part.known_excluded}，未知 ${part.eligibility_unknown}，价格及限价事件核验 ${part.price_observed}/${part.price_required}`;
      drawMarketPreview(coverage);
      $('scheduleJson').textContent = JSON.stringify(schedule, null, 2);
      $('coverageJson').textContent = JSON.stringify(coverage, null, 2);
      const history = $('historySelect');
      history.replaceChildren(textNode('option', runs.length ? '选择已保存运行' : '暂无已保存运行'));
      history.firstChild.value = '';
      for (const r of runs) {
        const option = textNode('option', `${r.observation_date} · ${r.market_session} · ${r.profile} · r${r.revision} · ${r.status}`);
        option.value = r.run_id;
        history.append(option);
      }
      if (current && runs.some(r => r.run_id === current.run_id)) {
        history.value = current.run_id;
        await loadRun(current.run_id, selectedFamily);
      } else if (runs.length) {
        history.value = runs[0].run_id;
        await loadRun(runs[0].run_id);
      } else {
        drawTabs();
        drawCandidates();
      }
      message(coverage.status === 'UNAVAILABLE' ? '真实规则输入未就绪：' + (coverage.reason_codes || []).join(' / ') : '已读取真实保存状态。完整策略、价格诊断和缺失证据分别展示。', coverage.status === 'UNAVAILABLE');
    } catch (error) {
      message(error.message, true);
    }
  }

  async function follow(job) {
    for (let i = 0; i < 3600; i++) {
      const state = await api('/api/jobs/' + job.job_id);
      message(`${job.job_id} · ${state.state} · ${state.message || ''}`);
      if (state.state === 'SUCCEEDED') return state.result;
      if (['FAILED', 'CANCELLED', 'INTERRUPTED'].includes(state.state)) throw new Error(state.state + ' · ' + JSON.stringify(state.error));
      await new Promise(resolve => setTimeout(resolve, 700));
    }
    throw new Error('任务仍未终止，请在历史任务中查看；未创建替代结果。');
  }

  $('compute').onclick = async () => {
    const button = $('compute');
    button.disabled = true;
    try {
      const value = { profile: $('profile').value };
      if ($('observationDate').value) value.observation_date = $('observationDate').value;
      if ($('inputId').value.trim()) value.input_id = $('inputId').value.trim();
      if (currentConfig) value.config_hash = currentConfig.config_hash;
      if (current && current.observation_date === value.observation_date) value.parent_run_id = current.run_id;
      const result = await follow(await api('/api/candidate-runs', value));
      await loadRun(result.candidate_run_id);
      await refresh();
    } catch (error) {
      message(error.message, true);
    } finally {
      button.disabled = false;
    }
  };

  $('rawEvidenceFilter').oninput = () => { if (latestCoverage) drawMarketPreview(latestCoverage); };
  $('refresh').onclick = refresh;
  $('apiKey').onchange = refresh;
  $('historySelect').onchange = () => {
    if ($('historySelect').value) loadRun($('historySelect').value).catch(e => message(e.message, true));
  };

  async function download(format) {
    try {
      const response = await fetch('/api/candidate-runs/' + current.run_id + '/export?format=' + format, { headers: headers() });
      if (!response.ok) throw new Error('保存记录不可导出');
      const url = URL.createObjectURL(await response.blob());
      const a = textNode('a');
      a.href = url;
      a.download = 'candidate-' + current.run_id + '.' + format;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      message(e.message, true);
    }
  }

  $('downloadJson').onclick = () => download('json');
  $('downloadCsv').onclick = () => download('csv');
  $('auctionReview').onclick = async () => {
    try {
      const value = {};
      if ($('auctionId').value.trim()) value.snapshot_id = $('auctionId').value.trim();
      await follow(await api('/api/candidate-runs/' + current.run_id + '/auction-review', value));
      await loadObservations();
      message('竞价复核已单独保存；原始候选与分数保持不变。');
    } catch (e) {
      message(e.message, true);
    }
  };
  $('outcomeReview').onclick = async () => {
    try {
      await follow(await api('/api/candidate-runs/' + current.run_id + '/outcomes', {}));
      await loadObservations();
      message('交易日观察已追加；尚未成熟与缺失项保持空值。');
    } catch (e) {
      message(e.message, true);
    }
  };
  $('saveConfig').onclick = async () => {
    try {
      const result = await api('/api/strategy-configs', JSON.parse($('configEditor').value));
      message('已保存新配置版本 ' + result.config.strategy_id);
      await refresh();
    } catch (e) {
      message(e.message, true);
    }
  };

  async function refreshPipeline() {
    const data = await api('/api/candidate-data-pipeline');
    $('pipelineJson').textContent = JSON.stringify(data, null, 2);
    const r = data.latest;
    const q = r?.queue;
    $('pipelineStatus').textContent = r
      ? `${r.observation_date} · ${r.consumer_status} · 请求完成 ${q?.states?.COMPLETE ?? 0}/${q?.requested ?? 0} · 待续采 ${q?.pending_request_ids?.length ?? 0} · 实际完成 ${r.completed_at}`
      : '尚未运行全主板采集。候选数据就绪须单独核验。';
  }

  $('captureData').onclick = async () => {
    const button = $('captureData');
    button.disabled = true;
    try {
      const result = await follow(await api('/api/candidate-data-pipeline/capture', {}));
      if (result.candidate_run_id) await loadRun(result.candidate_run_id);
      await refresh();
    } catch (e) {
      message(e.message, true);
      await refreshPipeline();
    } finally {
      button.disabled = false;
    }
  };

  drawTabs();
  refresh();
})();

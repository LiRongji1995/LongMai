/* Canonical recommendation UI. Dynamic content is rendered as text, never HTML. */
(() => {
  'use strict';
  let latestResult = null;
  const byId = id => document.getElementById(id);
  const node = (tag, text, cls) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = String(text);
    if (cls) el.className = cls;
    return el;
  };
  const errorText = error => typeof error === 'string' ? error : JSON.stringify(error);
  async function api(url, options = {}) {
    const headers = {'Content-Type': 'application/json', ...(options.headers || {})};
    const token = byId('coreApiKey')?.value;
    if (token) headers.Authorization = 'Bearer ' + token;
    const response = await fetch(url, {...options, headers});
    const value = await response.json();
    if (!response.ok) throw new Error(errorText(value.error || value) + (value.fields?.length ? ' [' + value.fields.join(', ') + ']' : ''));
    return value;
  }
  function fail(error) {
    const result = byId('result');
    result.replaceChildren(node('div', '不可用：' + errorText(error.message || error), 'error'));
    byId('analyzeBtn').disabled = false;
  }
  function requestBody() {
    const raw = byId('symbolInput').value.trim();
    const body = {symbols: raw, mode: 'RESEARCH', cash: Number(byId('cashInput').value || 0)};
    const model = byId('qlibModelSelect')?.value;
    if (model) body.model_id = model;
    const snapshot = byId('coreSnapshot')?.value.trim();
    if (snapshot) body.snapshot_id = snapshot;
    const holdings = byId('coreHoldings')?.value.trim();
    if (holdings) body.holdings = JSON.parse(holdings);
    else if (byId('sharesInput').value.split('/').some(v => Number(v || 0) !== 0)) {
      throw new Error('请在“可审计组合输入”中提供持仓、可卖股数、行业与当前市价；成本价不能替代市值。');
    }
    return body;
  }
  function table(rows) {
    const wrapper = node('div');
    wrapper.style.overflowX = 'auto';
    const result = node('table');
    result.style.cssText = 'display:table;min-width:600px;width:100%;border-collapse:collapse;margin-top:12px;text-align:left';
    const header = node('tr');
    ['排名', '证券', '量化原始分', '风险', '目标股数', '目标权重'].forEach(v => {
      const cell = node('th', v); cell.style.padding = '10px 6px'; header.append(cell);
    });
    result.append(header);
    rows.forEach(item => {
      const row = node('tr');
      [item.final_rank, item.instrument_id, Number(item.quant_score).toFixed(6), item.risk_decision.decision,
       item.target_shares, (item.target_weight * 100).toFixed(1) + '%'].forEach(v => {
        const cell = node('td', v);
        cell.style.padding = '10px 6px';
        row.append(cell);
      });
      result.append(row);
    });
    wrapper.append(result);
    return wrapper;
  }
  function render(value) {
    const final = value.final_recommendation || value;
    const root = byId('result');
    root.replaceChildren();
    if (!final.context || !Array.isArray(final.items)) {
      root.append(node('p', '量化运行已保存，尚无通过统一风控的最终推荐。'));
      const details = node('pre', JSON.stringify(value, null, 2));
      details.style.whiteSpace = 'pre-wrap';
      root.append(details);
      return;
    }
    latestResult = value;
    root.append(node('h2', '统一推荐 · ' + final.status));
    const c = final.context;
    root.append(node('p', `模型 ${c.model_id} / ${c.model_version} · 数据截止 ${c.as_of}`));
    root.append(node('p', `推荐时间 ${c.decision_time} · 最早交易 ${c.earliest_trade_at}`));
    root.append(node('p', `策略 ${final.policy.version} · 数据版本 ${c.data_version}`));
    root.append(node('p', '研究模式；量化原始分只用于排序，未校准为概率或预期收益。成交前必须再次检查交易日限制。'));
    if (final.items.length) {
      root.append(node('p', '首选与 Top5 / Top20 均来自以下同一份最终名单。'));
      root.append(table(final.items));
    } else root.append(node('p', '当前没有通过全部条件的推荐。' + (final.reason_codes || []).join(' / '), 'error'));
    if (final.rejected?.length) {
      const details = node('details');
      details.append(node('summary', `排除或不可用：${final.rejected.length} 只`));
      final.rejected.forEach(item => details.append(node('p', item.instrument_id + ' · ' + item.risk_decision.decision + ' · ' + item.reason_codes.join(', '))));
      root.append(details);
    }
    const audit = node('details');
    audit.append(node('summary', '完整审计记录'));
    const pre = node('pre', JSON.stringify(final, null, 2));
    pre.style.cssText = 'white-space:pre-wrap;overflow-wrap:anywhere';
    audit.append(pre);
    root.append(audit);
    const download = node('button', '下载已保存的审计报告');
    download.onclick = async () => {
      try {
        const headers = {'Content-Type': 'application/json'};
        if (byId('coreApiKey').value) headers.Authorization = 'Bearer ' + byId('coreApiKey').value;
        const response = await fetch('/api/report/download', {method: 'POST', headers,
          body: JSON.stringify({recommendation_run_id: final.recommendation_run_id})});
        if (!response.ok) throw new Error('审计报告不可用');
        const url = URL.createObjectURL(await response.blob());
        const a = node('a'); a.href = url; a.download = 'stockfish-audit.html'; a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch (error) { fail(error); }
    };
    root.append(download);
  }
  async function follow(jobId, renderResult = true) {
    // Fetch permits Authorization headers; the same durable state is also exposed by SSE.
    for (;;) {
      const job = await api('/api/jobs/' + encodeURIComponent(jobId));
      byId('simLog').style.display = 'block';
      byId('simLog').textContent = `${job.job_id} · ${job.state} · ${Math.round(job.progress * 100)}% · ${job.message || ''}`;
      if (job.state === 'SUCCEEDED') {
        if (renderResult) render(job.result || job);
        return job.result || job;
      }
      if (['FAILED', 'CANCELLED', 'INTERRUPTED'].includes(job.state)) throw new Error(errorText(job.error || job.state));
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  }
  async function recommend() {
    byId('analyzeBtn').disabled = true;
    byId('result').replaceChildren(node('p', '正在提交可恢复的推荐任务…'));
    try {
      const job = await api('/api/recommendations', {method: 'POST', body: JSON.stringify(requestBody())});
      await follow(job.job_id);
    } catch (error) { fail(error); }
    finally { byId('analyzeBtn').disabled = false; }
  }
  window.analyze = recommend;
  window.batchAnalyze = recommend;
  window.startQlibInfer = recommend;
  window.renderAnalysis = render;
  window.executeQlibDataUpdate = async () => {
    const button = byId('qlibDataExecuteBtn');
    const status = byId('qlibDataStatus');
    button.disabled = true;
    status.textContent = '正在创建独立数据快照…';
    try {
      const job = await api('/api/qlib/data/update', {method: 'POST', body: '{}'});
      const result = await follow(job.job_id, false);
      status.textContent = result.activation_required
        ? '数据快照已验证保存，尚未切换。请按迁移文档显式设置新目录并重启。'
        : (result.message || '数据任务完成；请核对保存的快照版本。');
    } catch (error) { status.textContent = '不可用：' + errorText(error.message || error); }
    finally { button.disabled = false; }
  };
  window.fillIndexStocks = async indexName => {
    const status = byId('fullMarketStatus');
    try {
      const result = await api('/api/qlib/index-stocks?index=' + encodeURIComponent(indexName)
        + '&exclude_star=' + Boolean(byId('excludeStarCheck').checked));
      byId('symbolInput').value = result.stocks;
      status.textContent = `已填入 ${result.count} 只，成员日期 ${result.as_of}`;
    } catch (error) { status.textContent = '不可用：' + errorText(error.message || error); }
  };
  window.loadHealthCheck = async () => {
    const content = byId('healthContent') || byId('healthCheckContent');
    try {
      const health = await api('/api/health');
      const target = content || byId('result');
      target.replaceChildren(node('h3', '工程组件状态 · ' + health.status));
      Object.entries(health.components).forEach(([name, value]) => {
        target.append(node('p', `${name}: ${value.status} ${value.reason || value.licensing || ''}`));
      });
    } catch (error) { fail(error); }
  };
  window.startQlibTrain = async () => {
    try {
      const text = byId('coreTraining').value.trim();
      if (!text) throw new Error('请提供包含特征哈希、训练/验证/测试窗口及 label_end 的 TrainRequest JSON。');
      const job = await api('/api/qlib/train', {method: 'POST', body: JSON.stringify(JSON.parse(text))});
      await follow(job.task_id);
    } catch (error) { fail(error); }
  };
  const section = node('details');
  section.style.cssText = 'margin:18px 0;padding:16px;border:1px solid #2a4058;border-radius:10px';
  section.append(node('summary', '可审计组合输入与工程状态'));
  section.append(node('p', '可用现金用于组合约束。已有持仓须提供 shares、sellable_shares、mark_price、industry、marked_at；未通过风险检查时不生成名单。'));
  [['coreSnapshot', '数据快照 ID（留空使用已选快照）', 'text'], ['coreApiKey', 'API 密钥（仅保留在当前页面）', 'password']].forEach(([id, label, type]) => {
    const wrapper = node('label', label + ' ');
    const input = node('input'); input.id = id; input.type = type; input.autocomplete = 'off'; input.style.width = '100%';
    wrapper.append(input); section.append(wrapper);
  });
  [['coreHoldings', '持仓 JSON 数组（无持仓留空）'], ['coreTraining', '训练请求 JSON（可选）']].forEach(([id, label]) => {
    section.append(node('p', label)); const input = node('textarea'); input.id = id; input.rows = 3; input.style.width = '100%'; section.append(input);
  });
  const historyButton = node('button', '读取持久推荐历史');
  historyButton.onclick = async () => {
    try {
      const history = await api('/api/recommendations/history');
      const result = byId('result'); result.replaceChildren(node('h2', '推荐历史（原记录与修订均保留）'));
      if (!history.length) result.append(node('p', '尚无已保存的推荐记录。'));
      history.forEach(record => {
        const button = node('button', `${record.instrument_id} · ${record.recommendation_time} · ${record.status}`);
        button.style.display = 'block'; button.style.margin = '8px 0';
        button.onclick = async () => {
          try {
            const saved = await api('/api/recommendation-runs/' + encodeURIComponent(record.metadata.recommendation_run_id));
            render(saved);
            const detail = await api('/api/recommendations/' + encodeURIComponent(record.recommendation_id));
            const outcomes = node('section');
            outcomes.append(node('h3', record.instrument_id + ' · 独立结果观察'));
            if (!detail.evaluations.length) outcomes.append(node('p', '尚无评估观察；未填写任何预期或实际收益。'));
            detail.evaluations.forEach(observation => {
              const metric = observation.return === null ? '收益未形成'
                : `假设持有净收益 ${(observation.return * 100).toFixed(3)}%`;
              outcomes.append(node('p', `${observation.horizon} 天 · ${observation.state} · ${metric} · ${observation.provenance}`));
            });
            result.append(outcomes);
          } catch (error) { fail(error); }
        };
        result.append(button);
      });
    } catch (error) { fail(error); }
  };
  section.append(historyButton);
  byId('result').before(section);
  if (byId('simCheck')) {
    byId('simCheck').checked = false;
    byId('simCheck').disabled = true;
    byId('simCheck').title = '模拟为实验模块，不参与最终推荐';
  }
  if (byId('masterSelect')) byId('masterSelect').disabled = true;
  byId('backendStatusText').textContent = '可信数据 · 唯一推荐策略 · 持久历史 · 研究模式';
})();

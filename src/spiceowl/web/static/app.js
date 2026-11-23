const $ = (selector) => document.querySelector(selector);
const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const title = (value) => String(value ?? 'unknown').replaceAll('_', ' ').replace(/^./, c => c.toUpperCase());
let state = {connections: [], runs: [], sessions: [], hosts: [], native_agents: [], questions: []};
let csrf = '';
let selectedSession = null;
let sessionView = null;
let selectedConnection = '';
let draftQuestion = 'Explain this run and suggest the next read-only check.';
let polling = false;
let submitting = false;
let toastTimer;
let routeEpoch = 0;
let placement = null, executionPlan = null, poolScanning = false;
let requestedCpus = 4, requestedMemory = 8, requestedSimulator = 'spectre';
let plannedKind = 'standalone', plannedReference = '', plannedHost = '';
let nativeAgentId = '', nativeQuestion = 'SSH 断开是否一定意味着 Spectre 仿真失败？请用一句话回答。';
let selectedQa = null, qaView = null, qaPolling = false, qaSubmitting = false;
let poolImportPreview = null;
let selectedHostIds = null;
let hostSelectionRevision = 0, planningBusy = false;
let live = {loaded: false, loading: false, enabled: false, projectId: '', runtime: null, error: null, busy: false, receipt: null};
let liveDraft = {configuration: '', source_commit: '', run_id: ''};
const LIVE_PENDING = 'spiceowl.live-pending';
const LIVE_COLLECT_PENDING = 'spiceowl.live-collect-pending';
const livePendingKey = operation => operation === 'collect' ? LIVE_COLLECT_PENDING : LIVE_PENDING;
const isPublicDemo = () => window.SpiceOwlDemo !== undefined || document.documentElement?.dataset?.publicDemo === 'true';
const demoRepository = 'https://github.com/spiceowl-dev/spiceowl';
function demoSource() {
  const metadata = window.SpiceOwlDemo?.metadata || {};
  const branch = /^[A-Za-z0-9][A-Za-z0-9._/-]{0,120}$/.test(metadata.source_branch || '') ? metadata.source_branch : 'main';
  return {branch, ref: /^[a-f0-9]{40}$/i.test(metadata.commit || '') ? metadata.commit : branch};
}
const demoDocument = path => demoRepository + '/blob/' + demoSource().ref + '/' + path;
const demoPendingRequests = new Map();
const pendingGet = key => isPublicDemo() ? demoPendingRequests.get(key) || null : sessionStorage.getItem(key);
const pendingSet = (key, value) => isPublicDemo() ? demoPendingRequests.set(key, value) : sessionStorage.setItem(key, value);
const pendingRemove = key => isPublicDemo() ? demoPendingRequests.delete(key) : sessionStorage.removeItem(key);

function publicDemoChrome() {
  if (!isPublicDemo()) return;
  document.body.classList.add('public-demo');
  document.title = 'SpiceOwl · Public interactive demo';
  $('#public-demo-banner').hidden = false;
  $('#public-github-link').hidden = false;
  $('#workspace-scope').textContent = 'Interactive preview';
  $('.local-tag').textContent = 'DEMO';
  $('.breadcrumb').textContent = 'Public demo';
  $('.prototype-badge').textContent = 'Synthetic · Scripted';
  $('.page-footer span').textContent = 'Browser demo · No live connections';
  const metadata = window.SpiceOwlDemo?.metadata || {};
  const commit = /^[a-f0-9]{7,40}$/i.test(metadata.commit || '') ? metadata.commit.slice(0, 8) : 'unversioned';
  const date = typeof metadata.build_date === 'string' && /^\d{4}-\d{2}-\d{2}/.test(metadata.build_date) ? metadata.build_date.slice(0, 10) : 'date unavailable';
  $('#demo-build').textContent = `Build ${commit} · ${date}`;
  if (typeof window.SpiceOwlDemo?.request !== 'function') $('[data-action="demo-reset"]').disabled = true;
  $('#demo-storage-notice').textContent = (state.demo_storage || window.SpiceOwlDemo?.storage_mode) === 'memory'
    ? 'Storage unavailable: changes reset on refresh.'
    : 'Changes stay in this browser tab; a new build or Reset clears them.';
  // Public pages offer local setup instructions, never connection or credential forms.
  $('#host-dialog')?.remove(); $('#pool-dialog')?.remove();
  $('#connection-dialog .muted').textContent = 'Try a scripted agent or model API. No provider is contacted and no credentials are needed.';
}

function publicLocalPage() {
  return heading('RUN LOCALLY', 'Bring the console to your workspace.', 'Use your own local installation for private host profiles, saved observations and native text Q&A.') + `<section class="panel public-info" data-public-demo-info="local"><div class="panel-body"><h2>Start with the synthetic workspace</h2><p>Requires Python 3.11+ on macOS or Linux. Use the branch that supplies this demo, then run these commands in a terminal on your own computer:</p><pre class="public-command">git clone --branch ${escape(demoSource().branch)} https://github.com/spiceowl-dev/spiceowl.git
cd spiceowl
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/spiceowl serve --demo</pre><p>Open the loopback address printed by that command. This public page never connects to that address or to your computer.</p><h3>Connect only when you choose</h3><p>The local console can register an existing SSH route, make explicit host checks and send text through a registered native agent. General simulation submission, unattended monitoring and automatic recovery remain planned.</p><div class="public-info-links"><a class="button secondary" href="${demoDocument('docs/local-console.md')}" target="_blank" rel="noopener noreferrer">Local setup guide</a><a class="button secondary" href="${demoDocument('docs/host-onboarding.md')}" target="_blank" rel="noopener noreferrer">Host connection guide</a><a class="button secondary" href="#overview">Back to the demo</a></div></div></section>`;
}

function publicHostPage() {
  return heading('LOCAL FEATURE PREVIEW', 'Connect servers in your local console.', 'This public demo has no SSH access and does not scan, register or contact hosts.') + `<section class="panel public-info" data-public-demo-info="hosts"><div class="panel-body"><span class="chip">ILLUSTRATIVE WORKFLOW</span><h2>Your SSH configuration stays with you</h2><div class="public-feature-grid"><article><h3>Register a route</h3><p>Choose an existing SSH alias and record the expected remote identity in a private local profile.</p></article><article><h3>Check explicitly</h3><p>Verify host trust, account identity and configured simulator versions. A saved profile alone proves no connection.</p></article><article><h3>Compare observations</h3><p>Inspect CPU and memory snapshots with their observation times. Missing data never means an idle machine.</p></article></div><p>No hostnames, login details, SSH keys or credentials are requested on this page.</p><a class="button primary" href="#local">Run locally to configure hosts</a></div></section>`;
}

function publicAgentPage() {
  const firstRun = state.runs[0];
  return heading('LOCAL FEATURE PREVIEW', 'Use your own agent locally.', 'No real model or agent runtime is connected to this public demo.') + `<section class="panel public-info" data-public-demo-info="agents"><div class="panel-body"><h2>Explore the diagnosis workflow first</h2><p>Open a synthetic run and choose <strong>Diagnose snapshot</strong> to see a scripted explanation based on its saved evidence. Text stays in this browser tab; the response is not generated by a model.</p><blockquote class="public-example"><span class="chip">SCRIPTED EXAMPLE</span><p>A lost connection leaves the current execution state unknown. Keep the original run identity and inspect its saved evidence before considering another submission.</p></blockquote><p>In a local installation, Agent chat can send a real text question through a registered native Codex runtime. Authentication stays with that runtime. A chat response does not authorize a simulation action.</p><div class="public-info-links">${firstRun ? `<a class="button primary" href="#run/${escape(firstRun.run_id)}">Try a scripted diagnosis</a>` : '<a class="button primary" href="#overview">Explore synthetic runs</a>'}<a class="button secondary" href="#local">Run locally</a><a class="button secondary" href="${demoDocument('docs/native-agent.md')}" target="_blank" rel="noopener noreferrer">Native agent guide</a></div></div></section>`;
}

function toast(message) {
  clearTimeout(toastTimer);
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 6000);
}

async function api(path, options = {}) {
  if (isPublicDemo()) {
    if (typeof window.SpiceOwlDemo?.request !== 'function') throw new Error('The public demo could not load. Reload this page to try again.');
    // This explicit adapter owns demo requests; errors never fall back to a live API.
    return window.SpiceOwlDemo.request(path, options);
  }
  const method = options.method || 'GET';
  const response = await fetch(path, {method, headers: {'Content-Type': 'application/json', ...(method !== 'GET' ? {'X-SpiceOwl-Token': csrf} : {})}, ...(method !== 'GET' ? {body: JSON.stringify(options.body || {})} : {})});
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error?.message || 'The local request failed.');
    error.code = data.error?.code || 'request_failed';
    error.definitive = response.status < 500;
    throw error;
  }
  return data;
}

async function refresh() {
  publicDemoChrome();
  state = await api('/api/state');
  publicDemoChrome();
  csrf = state.csrf_token;
  $('#version').textContent = state.version;
  $('#connection-count').textContent = state.connections.length;
  $('#host-count').textContent = state.hosts.length;
  $('#native-count').textContent = state.native_agents.length;
  $('#recent-runs').innerHTML = state.runs.slice(0, 5).map(run => `<a href="#run/${escape(run.run_id)}"><i class="dot"></i><span class="run-title">${escape(run.label)}</span></a>`).join('');
}

function currentRun() {
  const hash = location.hash.replace(/^#/, '');
  return hash.startsWith('run/') ? state.runs.find(run => run.run_id === hash.slice(4)) : null;
}

function chip(text, color = '') {
  return `<span class="chip ${color}"><i class="dot"></i>${escape(title(text))}</span>`;
}

function runStatus(run) {
  if (run.observation !== 'current') return chip('observation ' + run.observation, 'amber');
  if (run.execution === 'failed') return chip('execution failed', 'red');
  if (run.specification_result === 'fail') return chip('valid · target missed', 'amber');
  if (run.execution === 'completed') return chip('completed', 'green');
  return chip(run.execution, 'blue');
}

function heading(eyebrow, name, subtitle, actions = '') {
  return `<div class="page-heading"><div><div class="eyebrow">${eyebrow}</div><h1>${name}</h1><p class="subtitle">${subtitle}</p></div>${actions}</div>`;
}

function livePending(operation = null) {
  const raw = operation ? pendingGet(livePendingKey(operation)) : pendingGet(LIVE_PENDING) || pendingGet(LIVE_COLLECT_PENDING);
  if (!raw) return null;
  const request = JSON.parse(raw);
  if (!['submit', 'collect'].includes(request.operation) || operation && request.operation !== operation || !request.args || typeof request.idempotency_key !== 'string') throw Error('原请求记录不可读，请在本地客户端核对后继续。');
  return request;
}

async function refreshLive() {
  if (isPublicDemo() || live.loading) return;
  live.loading = true;
  try {
    const value = await api('/api/live/runtime');
    if (value.enabled !== false && (value.enabled !== true || value.runtime?.schema !== 'ic.simulation.runtime.v1' || value.project_id !== value.runtime.project_id)) throw Error('真实仿真快照的项目身份不符。');
    live.enabled = value.enabled; live.projectId = value.project_id || ''; live.runtime = value.runtime; live.error = null;
    const pending = livePending();
    if (pending && !liveDraft.configuration && pending.args.project_id === live.projectId) {
      for (const key of Object.keys(liveDraft)) liveDraft[key] = pending.args[key] || '';
    }
  } catch (error) { live.error = error.message; }
  finally { live.loaded = true; live.loading = false; if (location.hash === '#live') render(); }
}

function liveCollectReady() {
  const candidates=live.runtime?.runs || [];
  return candidates.some(run=>run.run_id===liveDraft.run_id.trim()&&run.configuration===liveDraft.configuration.trim()
    &&run.source_commit===liveDraft.source_commit.trim()&&(!run.project_id||run.project_id===live.projectId)
    &&run.collectable===true);
}

function livePage() {
  let html = heading('STANDALONE SPECTRE', '真实仿真 · CircuitGit', '查看保存的资源与运行记录，明确发起状态检查、仿真提交和结果收集。');
  if (isPublicDemo()) return html + '<section class="panel live-panel" data-public-demo-info="live"><h2>公共演示未启用真实仿真</h2><p>在本地启动服务并配置操作员连接后使用。此页不会访问仿真服务器。</p></section>';
  if (!live.loaded) return html + '<section class="panel live-panel"><p>正在读取连接状态…</p></section>';
  if (!live.enabled) return html + `<section class="panel live-panel" data-live-disabled><h2>真实仿真未启用</h2><p>由本地操作员使用 <code>serve --live-client-config</code> 启动参数指定已配置连接。浏览器不接收配置路径或凭据。</p>${live.error ? `<p class="error-text">${escape(live.error)}</p>` : ''}<button class="button secondary" data-action="live-refresh">刷新连接状态</button></section>`;
  const snapshot = live.runtime || {}, capabilities = snapshot.capabilities || {};
  const hosts = Array.isArray(snapshot.pool?.hosts) ? snapshot.pool.hosts.slice(0, 32) : [];
  const runs = Array.isArray(snapshot.runs) ? snapshot.runs.slice(0, 25) : [];
  const metric = (value, suffix = '') => Number.isFinite(value) ? escape(Number(value.toFixed(1))) + suffix : '未记录';
  const disabled = allowed => live.busy || !allowed ? 'disabled' : '';
  let pending = null, submitPending = null, collectPending = null;
  try { submitPending = livePending('submit'); collectPending = livePending('collect'); pending = submitPending || collectPending; } catch (error) { live.error = error.message; }
  html += `<div class="live-workspace"><section class="panel live-panel"><div class="section-title"><h2>执行身份</h2>${chip('CircuitGit 已配置', 'green')}</div><p class="subtle-note">项目由操作员连接固定。配置、完整提交和运行 ID 必须与目标记录一致。</p><div class="live-input-grid"><label>项目<input id="live-project" value="${escape(live.projectId)}" disabled></label><label>配置 ID<input id="live-configuration" ${live.busy ? 'disabled' : ''} value="${escape(liveDraft.configuration)}" maxlength="180" autocomplete="off" placeholder="tran"></label><label class="live-wide">源提交 · 40 位 SHA<input id="live-source-commit" ${live.busy ? 'disabled' : ''} value="${escape(liveDraft.source_commit)}" maxlength="40" autocomplete="off" spellcheck="false"></label><label class="live-wide">已有运行 ID<input id="live-run-id" ${live.busy ? 'disabled' : ''} value="${escape(liveDraft.run_id)}" maxlength="180" autocomplete="off" spellcheck="false" placeholder="从下方运行记录选取，或输入准确 ID"></label></div><div class="live-actions"><button class="button primary" data-action="live-submit" ${disabled(capabilities.direct_submit && !submitPending)}>提交新仿真</button><button class="button secondary" data-action="live-status" ${disabled(true)}>读取运行状态</button><button class="button secondary" data-action="live-collect" ${disabled(capabilities.direct_collect && !collectPending && liveCollectReady())}>收集结果</button><button class="button secondary" data-action="live-watch" ${disabled(capabilities.monitor)}>开启后台监控</button><button class="button secondary" data-action="live-unwatch" ${disabled(capabilities.monitor)}>停止监控</button></div><p class="subtle-note">停止监控仅停止观察；执行、数据有效性与指标结果分别报告。</p>${pending ? '<p class="live-pending" role="status">原请求键已保存。结果未确认前保留同一请求，不会自动再次提交。</p>' : ''}${submitPending ? '<button class="button secondary small" data-action="live-lookup">查询原提交映射</button>' : ''}${live.busy ? '<p role="status">正在处理明确请求…</p>' : ''}${live.error ? `<p class="error-text" role="alert">${escape(live.error)}</p>` : ''}${live.receipt ? `<div class="live-receipt" role="status"><strong>操作回执</strong><p>状态：${escape(live.receipt.state || live.receipt.status || live.receipt.watch_state || '已返回')}</p>${live.receipt.run_id ? `<code>${escape(live.receipt.run_id)}</code>` : ''}${live.receipt.error_code ? `<p>${escape(live.receipt.error_code)}</p>` : ''}</div>` : ''}</section>`;
  html += `<section class="panel live-panel"><div class="section-title"><h2>资源服务器</h2><div class="live-actions"><button class="button secondary small" data-action="live-refresh" ${disabled(true)}>刷新保存记录</button><button class="button primary small" data-action="live-scan" ${disabled(capabilities.scan && !snapshot.pool?.scan_in_progress)}>检查配置的服务器</button></div></div><p class="subtle-note">资源检查时间：${escape(snapshot.pool?.checked_at || '未检查')}。资源数据是观察值；刷新保存记录不会执行 SSH。</p>${snapshot.pool?.scan_in_progress ? '<p role="status">资源检查进行中…</p>' : ''}<div class="live-table-wrap"><table class="live-table"><thead><tr><th>服务器</th><th>连接</th><th>CPU 使用率 / 核数</th><th>可用 / 总内存</th><th>观察时间</th></tr></thead><tbody>${hosts.map(host => `<tr><td>${escape(host.label || host.host_id)}<br><code>${escape(host.host_id)}</code></td><td>${host.reachable === true ? '已验证' : '未确认'}${host.error_code ? `<br>${escape(host.error_code)}` : ''}</td><td>${metric(host.cpu_busy_percent, '%')} / ${metric(host.cpu_count)}</td><td>${metric(Number.isFinite(host.memory_available_bytes) ? host.memory_available_bytes / 2**30 : null, ' GiB')} / ${metric(Number.isFinite(host.memory_total_bytes) ? host.memory_total_bytes / 2**30 : null, ' GiB')}</td><td>${escape(host.observed_at || '未记录')}</td></tr>`).join('') || '<tr><td colspan="5">尚无资源记录。点击明确检查后查看结果。</td></tr>'}</tbody></table></div></section>`;
  html += `<section class="panel live-panel"><div class="section-title"><h2>真实运行记录</h2><span class="section-note">快照读取：${escape(snapshot.read_at || '未记录')}</span></div><div class="live-run-list">${runs.map(run => {
    const diagnosis = Array.isArray(run.diagnostics?.diagnostics) ? run.diagnostics.diagnostics.slice(0, 8) : [];
    return `<article class="live-run"><div class="section-title"><h3>${escape(run.run_id)}</h3><button class="button secondary small" data-action="live-select" data-id="${escape(run.run_id)}">使用此运行</button></div><div class="live-dimensions"><span>执行：<strong>${escape(title(run.execution?.state))}</strong></span><span>观察：<strong>${escape(title(run.monitor?.state))}</strong></span><span>数据：<strong>${escape(title(run.acceptance?.data_validity))}</strong></span><span>指标：<strong>${escape(title(run.acceptance?.specification))}</strong></span></div><p class="live-identity">配置 ${escape(run.configuration)} · 服务器 ${escape(run.execution_target?.host_id || '未记录')}<br><code>${escape(run.source_commit)}</code></p><p class="subtle-note">最近成功观察：${escape(run.monitor?.last_success_at || '未记录')} · 下一次检查：${escape(run.monitor?.next_check_at || '未安排')}</p>${diagnosis.map(item => `<div class="live-diagnostic"><strong>${escape(item.code || item.category)}</strong>${item.redacted_excerpt ? `<pre>${escape(item.redacted_excerpt)}</pre>` : ''}<ul>${(Array.isArray(item.suggested_actions) ? item.suggested_actions : []).slice(0, 4).map(action => `<li>${escape(action)}</li>`).join('')}</ul></div>`).join('')}</article>`;
  }).join('') || '<p class="muted">当前项目尚无真实运行记录。</p>'}</div></section></div>`;
  return html;
}

function liveArguments(operation) {
  const result = {project_id: live.projectId};
  if (operation === 'scan') return result;
  for (const key of ['configuration', 'source_commit']) result[key] = liveDraft[key].trim();
  if (!/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,179}$/.test(result.configuration) || !/^[a-f0-9]{40}$/.test(result.source_commit)) throw Error('请填写配置 ID 和 40 位小写源提交 SHA。');
  if (operation !== 'submit') {
    result.run_id = liveDraft.run_id.trim();
    if (!/^[A-Za-z0-9_-]{1,180}$/.test(result.run_id)) throw Error('请填写准确的已有运行 ID。');
  }
  return result;
}

async function handleLiveClick(event) {
  const button = event.target.closest('button[data-action^="live-"]');
  if (!button) return;
  if (isPublicDemo()) { location.hash = '#local'; return; }
  const operation = button.dataset.action.slice(5);
  if (operation === 'refresh') { await refreshLive(); return; }
  if (operation === 'lookup') {
    if (live.busy) return;
    try {
      const pending = livePending('submit');
      if (!pending || pending.operation !== 'submit') throw Error('没有待核对的提交键。');
      live.busy = true; live.error = null; render();
      const receipt = await api('/api/live/submission?key=' + encodeURIComponent(pending.idempotency_key));
      live.receipt = receipt;
      if (receipt.run_id) liveDraft = {configuration: pending.args.configuration, source_commit: pending.args.source_commit, run_id: receipt.run_id};
    } catch (error) { live.error = error.message; }
    finally { live.busy = false; if (location.hash === '#live') render(); }
    return;
  }
  if (operation === 'select') {
    const run = live.runtime?.runs.find(item => item.run_id === button.dataset.id);
    if (run) { for (const key of Object.keys(liveDraft)) liveDraft[key] = run[key] || ''; render(); }
    return;
  }
  if (live.busy || !live.enabled || !['scan', 'submit', 'status', 'collect', 'watch', 'unwatch'].includes(operation)) return;
  let request;
  try {
    request = {operation, args: liveArguments(operation)};
    if(operation==='collect'&&!liveCollectReady())throw Error('请先读取此运行状态，确认仿真已结束后再收集结果。');
    if (['submit', 'collect'].includes(operation)) {
      const pending = livePending(operation);
      if (pending) throw Error('请先核对原请求；未知结果不会通过再次提交来恢复。');
      request = {...request, idempotency_key: `live-${crypto.randomUUID()}`};
      pendingSet(livePendingKey(operation), JSON.stringify(request));
    }
    live.busy = true; live.error = null; render();
    const receipt = await api('/api/live/control', {method: 'POST', body: request});
    live.receipt = receipt;
    if (receipt.run_id && (operation === 'submit' || !liveDraft.run_id)) liveDraft.run_id = receipt.run_id;
    const uncertain = ['uncertain', 'outcome_unknown', 'launch_outcome_unknown', 'dispatching'].includes(receipt.state || receipt.status);
    if (request.idempotency_key && !uncertain) pendingRemove(livePendingKey(operation));
    await refreshLive();
  } catch (error) {
    if (request?.idempotency_key && error.definitive && !/uncertain|unresolved|unknown/.test(error.code || '')) pendingRemove(livePendingKey(operation));
    live.error = error.message;
  } finally { live.busy = false; if (location.hash === '#live') render(); }
}

function overview() {
  const total = state.runs.length;
  const running = state.runs.filter(r => r.execution === 'running' || r.execution === 'queued').length;
  const attention = state.runs.filter(r => r.observation !== 'current' || r.execution === 'failed' || r.specification_result === 'fail').length;
  const done = state.runs.filter(r => r.execution === 'completed').length;
  const stats = [['Total runs', total, 'Stored synthetic experiments', '▦'], ['Last observed active', running, 'Check observation freshness', '◷'], ['Needs attention', attention, 'Evidence to inspect', '◇'], ['Execution completed', done, 'Validity is reported separately', '✓']];
  const overviewAction = isPublicDemo()
    ? state.runs[0] ? `<a class="button primary" href="#run/${escape(state.runs[0].run_id)}">Explore a scenario</a>` : '<button class="button primary" data-action="demo-reset">Reset demo</button>'
    : '<button class="button primary" data-action="seed"><span aria-hidden="true">＋</span> Load demo workspace</button>';
  let html = heading('YOUR WORKSPACE', 'A clear view of every run.', 'Follow the evidence. Keep the experiment intact.', overviewAction);
  if (!total) {
    return html + `<div class="empty"><img src="./owl.svg" alt=""><h1>Your first night watch starts here.</h1><p>Explore connections, interrupted runs and diagnostic proposals with an offline demo. No accounts, API keys or simulator needed.</p><button class="button primary" data-action="seed">Load the synthetic demo <span aria-hidden="true">→</span></button></div>`;
  }
  html += `<div class="stat-grid">${stats.map(([label, value, note, glyph]) => `<div class="stat-card"><div class="stat-label">${label}<span class="stat-glyph" aria-hidden="true">${glyph}</span></div><div class="stat-value">${value}</div><div class="stat-note">${note}</div></div>`).join('')}</div>`;
  html += `<div class="watch-banner"><img src="./owl.svg" alt=""><div><h3>Explore the overnight workflow.</h3><p>Advance a synthetic run, inspect its evidence, then ask a demo agent for a read-only recommendation.</p></div><a class="button secondary small" href="#connections">Manage connections <span aria-hidden="true">→</span></a></div>`;
  html += `<div class="section-title"><h2>Simulation runs <span class="muted">/ ${total}</span></h2><span class="section-note">Manual replay · saved locally</span></div><div class="run-grid">`;
  html += state.runs.map(run => {
    const pct = Number.isFinite(run.progress) ? Math.round(run.progress * 100) : null;
    return `<a class="run-card" href="#run/${escape(run.run_id)}"><div class="card-top"><span class="chip">SYNTHETIC / TRANSIENT</span>${runStatus(run)}</div><h3>${escape(run.label)}</h3><div class="run-meta">${escape(run.attempt_id)}</div><div class="progress-row"><span>Scenario progress</span><span>${pct === null ? 'Unknown' : pct + '%'}</span></div><progress class="progress" max="100" value="${pct ?? 0}" aria-label="Scenario progress"></progress><div class="run-card-footer"><span>${run.events.length} evidence events · ${escape(title(run.measurement_validity))} measurement</span><strong>Inspect run <span class="arrow" aria-hidden="true">↗</span></strong></div></a>`;
  }).join('') + '</div>';
  return html;
}

function connections() {
  let html = heading('AI CONNECTIONS', isPublicDemo() ? 'Try simulated connections.' : 'Bring your own intelligence.', isPublicDemo() ? 'Explore scripted readiness, access-denied and interrupted-response states. No provider is contacted.' : 'Keep agent runtimes and model APIs in one workspace.', `<button class="button primary" data-action="add-connection"><span aria-hidden="true">＋</span> Add demo connection</button>`);
  if (!state.connections.length) {
    html += `<div class="empty"><h2>No connections yet.</h2><p>Create a simulated agent or API connection to explore authentication states, streaming and failure handling.</p><button class="button primary" data-action="add-connection">Create demo connection</button></div>`;
  }
  html += `<div class="connections-list">${state.connections.map(c => {
    const status = c.status || {};
    const fields = [['Transport', status.transport || 'unchecked'], ['Authentication', status.authentication || 'unchecked'], ['Model catalog', status.catalog || 'unchecked'], ['Inference access', status.inference_access || 'unverified']];
    return `<article class="panel connection-card"><div class="connection-main"><div class="provider-icon" aria-hidden="true">${c.kind === 'agent' ? '✧' : '⌁'}</div><div class="connection-name"><h3>${escape(c.label)} ${chip(c.enabled ? 'enabled' : 'disabled', c.enabled ? 'green' : '')}</h3><p>${c.kind === 'agent' ? 'Agent runtime' : 'Model API'} · Offline fixture · ${escape(title(c.scenario))}</p></div><div class="connection-actions"><button class="button secondary small" data-action="probe" data-id="${escape(c.connection_id)}" ${c.enabled ? '' : 'disabled'}>Probe metadata</button><button class="button secondary small" data-action="toggle" data-id="${escape(c.connection_id)}">${c.enabled ? 'Disable' : 'Enable'}</button><button class="button secondary small" data-action="remove" data-id="${escape(c.connection_id)}" aria-label="Remove ${escape(c.label)}">Remove</button></div></div><div class="connection-readiness">${fields.map(([name, value]) => `<div class="readiness-field"><span>${name}</span><strong>${escape(title(value))}</strong></div>`).join('')}</div><div class="connection-bottom"><span>All statuses describe a simulated backend.</span><span>Revision ${c.revision} · No credentials</span></div></article>`;
  }).join('')}</div>`;
  if (isPublicDemo()) return html + `<div class="planned-note"><strong>Ready to use your own runtime?</strong><p>This page only simulates connections. <a href="#local">Run SpiceOwl locally</a> to configure native text Q&A and optional read-only MCP access to your saved records.</p></div>`;
  return html + `<div class="planned-note"><strong>Native agent and MCP</strong><p>These connection cards use fixture responses. Open <a href="#agents">Agent chat</a> for a real question through a registered native runtime.</p><p>MCP exposes stored runs and host observations to existing clients: install the optional extra and configure <code>spiceowl --data-dir ABSOLUTE_STATE_DIR mcp</code>. Its tools only query records.</p></div>`;
}

function hosts() {
  if (isPublicDemo()) return publicHostPage();
  syncHostSelection();
  const count = selectedHostIds?.size || 0;
  let html = heading('SIMULATION HOSTS', 'Your simulation server pool.', 'Register your servers, verify each connection, then compare saved resource observations.', `<div class="host-page-actions"><button class="button secondary" data-action="add-host">＋ Add host</button><button class="button secondary" data-action="import-pool" ${!state.hosts.length ? 'disabled' : ''}>Import pool</button><button class="button primary" data-action="pool-scan" ${poolScanning || !count || count > 32 ? 'disabled' : ''}>${poolScanning ? 'Checking selected hosts…' : 'Check selected hosts'}</button></div>`);
  html += `<div class="host-onboarding"><div><strong>1. Register</strong><span>Add an existing SSH alias or import a list using a route template.</span></div><div><strong>2. Verify</strong><span>Check the route, SSH trust and remote identity for each host.</span></div><div><strong>3. Check resources</strong><span>Run a pool check when you need fresh CPU and memory observations.</span></div></div>`;
  html += poolOverview();
  if (!state.hosts.length) html += `<div class="empty"><h2>No simulation hosts registered.</h2><p>Add a host to start. Registration saves local configuration; Check connection is a separate action.</p><button class="button primary" data-action="add-host">Add your first host</button></div>`;
  html += `<details class="host-details" ${state.hosts.length <= 1 ? 'open' : ''}><summary>Host identity and tool versions</summary><div class="connections-list">${state.hosts.map(host => {
    const probe = host.last_probe;
    const evidence = connectionEvidence(host), status = connectionStatus(evidence);
    const fields = [['SSH transport', evidence?.transport || 'unchecked'], ['Host identity', evidence?.identity?.verified === true ? 'matched at last check' : 'unverified'], ['Tool check', observationTime(probe)], ['Simulation readiness', 'not tested']];
    const toolRows = host.tools.map(tool => {
      const result = probe?.tools?.find(item => item.kind === tool.kind);
      return `<div class="host-tool"><strong>${escape(title(tool.kind))}</strong>${chip(result?.status || 'unchecked', result?.status === 'available' ? 'green' : result ? 'amber' : '')}<span class="host-version">${escape(result?.version || 'Version unverified')}</span>${result?.error ? `<p class="error-text">${escape(result.error.message)}</p>` : ''}</div>`;
    }).join('');
    return `<article class="panel connection-card"><div class="connection-main"><div class="provider-icon" aria-hidden="true">▤</div><div class="connection-name"><h3>${escape(host.label)} ${chip(status.label, status.color)}</h3><p>Registered host · ${escape(host.host_id)}</p></div><div class="connection-actions"><button class="button primary small" data-action="host-probe" data-id="${escape(host.host_id)}">Check connection</button></div></div><div class="connection-readiness">${fields.map(([label, value]) => `<div class="readiness-field"><span>${label}</span><strong>${escape(title(value))}</strong></div>`).join('')}</div><div class="host-tools">${toolRows || '<p class="subtle-note">No simulator executable configured. Connection checks verify identity only.</p>'}</div>${status.help ? `<div class="host-error">${escape(status.help)}</div>` : ''}<div class="connection-bottom"><span>Connection evidence: ${escape(observationTime(evidence))}</span><span>Saved result · no automatic remote polling</span></div></article>`;
  }).join('')}</div></details>`;
  return html + `<div class="planned-note"><strong>Plans preserve simulation ownership.</strong><p>A new standalone job can receive a proposed host. An existing Virtuoso-managed run retains its original host and native run reference. These plans do not reserve resources or launch a simulation; input, environment and license checks still belong to submission.</p></div>`;
}

function metric(value, suffix = '', digits = 1) {
  return Number.isFinite(value) ? `${escape(value.toFixed(digits))}${suffix}` : 'Unknown';
}

function syncHostSelection() {
  // A newly registered host must not silently expand an established selection.
  if (selectedHostIds === null && state.hosts.length) selectedHostIds = new Set(state.hosts.length <= 32 ? state.hosts.map(host => host.host_id) : []);
  if (selectedHostIds !== null) {
    const registered = new Set(state.hosts.map(host => host.host_id));
    selectedHostIds = new Set([...selectedHostIds].filter(id => registered.has(id)));
  }
}

function hostSelectionLabel() {
  const count = selectedHostIds?.size || 0;
  return `${count} of ${state.hosts.length} hosts selected · maximum 32 per check or plan${count > 32 ? ' · Reduce the selection to continue' : ''}`;
}

function selectedPoolIds() {
  syncHostSelection();
  const ids = state.hosts.filter(host => selectedHostIds?.has(host.host_id)).map(host => host.host_id);
  if (!ids.length || ids.length > 32) throw new Error('Select 1–32 hosts in the table before checking resources or preparing a placement plan.');
  return ids;
}

function hostPlanScope() {
  return `Placement scope: ${selectedHostIds?.size || 0} selected host(s). Choose 1–32 rows above. An existing native run keeps its original host even when that host is outside this selection.`;
}

function invalidateHostPlans() {
  hostSelectionRevision++;
  placement = null; executionPlan = null;
  const result = $('.placement-result');
  if (result) result.innerHTML = '<p class="subtle-note">Selection changed. Evaluate the selected hosts again to prepare a new recommendation or plan.</p>';
  $('.pool-placement .plan-summary')?.remove();
}

function updateHostSelectionUI() {
  syncHostSelection();
  const count = selectedHostIds?.size || 0, label = $('#host-selection-count');
  if (label) label.textContent = hostSelectionLabel();
  document.querySelectorAll('[data-host-select]').forEach(input => {
    input.checked = selectedHostIds?.has(input.dataset.hostSelect) || false;
    input.disabled = poolScanning;
  });
  const scan = $('[data-action="pool-scan"]');
  if (scan) scan.disabled = poolScanning || !count || count > 32;
  const scope = $('#placement-scope');
  if (scope) scope.textContent = hostPlanScope();
  document.querySelectorAll('#placement-form [type="submit"], #execution-plan-form [type="submit"]').forEach(button => {
    button.disabled = planningBusy || poolScanning || !count || count > 32;
  });
  const all = $('[data-action="host-select-all"]'), none = $('[data-action="host-select-none"]');
  if (all) all.disabled = poolScanning || !state.hosts.length || state.hosts.length > 32;
  if (none) none.disabled = poolScanning || !count;
}

function observationTime(observation) {
  if (!observation) return 'Never checked';
  const time = Date.parse(observation.observed_at);
  return Number.isFinite(time) ? new Date(time).toLocaleString() : 'Timestamp unavailable';
}

function connectionEvidence(host) {
  const observations = [host.last_probe, host.last_resources].filter(Boolean);
  const now = Date.now();
  const valid = observation => Number.isFinite(Date.parse(observation.observed_at)) && Date.parse(observation.observed_at) <= now;
  return observations.sort((a, b) => Number(valid(b)) - Number(valid(a)) || (valid(a) && valid(b) ? Date.parse(b.observed_at) - Date.parse(a.observed_at) : 0))[0] || null;
}

function connectionStatus(observation) {
  if (!observation) return {label: 'Never checked', color: '', help: ''};
  const stamp = Date.parse(observation.observed_at);
  if (!Number.isFinite(stamp) || stamp > Date.now()) return {label: 'Timestamp invalid', color: 'amber', help: 'This observation has an invalid or future timestamp. Check the clock and obtain a new connection observation.'};
  const guidance = {
    ssh_host_key_unverified: ['Trust pending', 'Verify the server fingerprint with your administrator or another trusted source, establish SSH trust, then check again. Your login key is a separate identity.'],
    ssh_host_key_changed: ['Host key changed', 'Ask the administrator to verify the changed server identity before updating its trust record.'],
    ssh_permission_denied: ['Authentication denied', 'Check the SSH alias, remote account and authorized login key, including the jump host.'],
    ssh_config_invalid: ['SSH configuration error', 'Review the selected alias and the SSH configuration on the computer running SpiceOwl.'],
    ssh_name_resolution_failed: ['Name resolution failed', 'Check the target and jump-host names and the required VPN or internal DNS.'],
    ssh_network_unreachable: ['Route unavailable', 'Check the network, VPN and jump-host route. The failing hop is not yet identified.'],
    ssh_connection_refused: ['Connection refused', 'Check the SSH service and port on the target and jump hosts.'],
    ssh_connection_timeout: ['Connection timed out', 'Check network access, VPN and the jump-host route, then retry the bounded check.'],
    ssh_connection_lost: ['Connection lost', 'The SSH connection closed before the check completed. Simulation state remains unknown.'],
    probe_timeout: ['Check timed out', 'The bounded check did not finish. Review the route and server responsiveness before checking again.'],
    identity_mismatch: ['Identity mismatch', 'The returned hostname or username differs from the registered identity. Review the target mapping before proceeding.'],
    identity_unverified: ['Identity unverified', 'Verify the connection and registered hostname and username before reading resources.'],
  };
  if (observation.error) {
    const [label, help] = guidance[observation.error.code] || ['Check incomplete', observation.error.message || 'Inspect the saved connection error.'];
    return {label, help, color: observation.error.code === 'ssh_host_key_changed' || observation.error.code === 'identity_mismatch' ? 'red' : 'amber'};
  }
  if (observation.synthetic !== false || observation.transport !== 'reachable' || observation.identity?.verified !== true) return {label: 'Identity unverified', color: 'amber', help: 'A verified remote identity has not been established by this observation.'};
  return {label: 'Verified at last check', color: 'green', help: ''};
}

function resourceStatus(sample) {
  if (!sample) return {label: 'No resource check', color: ''};
  if (sample.error) return {label: 'Resource check failed', color: 'amber'};
  if (sample.synthetic !== false || sample.transport !== 'reachable' || sample.identity?.verified !== true) return {label: 'Resource identity unverified', color: 'amber'};
  const age = (Date.now() - Date.parse(sample.observed_at)) / 1000;
  if (!Number.isFinite(age) || age < 0) return {label: 'Timestamp invalid', color: 'amber'};
  if (age > 120) return {label: 'Resource data stale', color: 'amber'};
  const keys = ['cpu_count', 'cpu_busy_percent', 'load_1m', 'load_5m', 'memory_total_bytes', 'memory_available_bytes', 'home_disk_available_bytes'];
  if (keys.some(key => !Number.isFinite(sample.resources?.[key]))) return {label: 'Resource data incomplete', color: 'amber'};
  return {label: 'Recent resource sample', color: 'green'};
}

function poolOverview() {
  const rows = state.hosts.map(host => {
    const sample = host.last_resources, r = sample?.resources || {};
    const evidence = connectionEvidence(host), connection = connectionStatus(evidence), resources = resourceStatus(sample);
    return `<tr><td class="host-select-cell"><input type="checkbox" data-host-select="${escape(host.host_id)}" aria-label="Select ${escape(host.label)} for pool check" ${selectedHostIds?.has(host.host_id) ? 'checked' : ''} ${poolScanning ? 'disabled' : ''}></td><th scope="row">${escape(host.label)}<small>${escape(host.host_id)}</small></th><td class="host-connection-cell">${chip(connection.label, connection.color)}<small>Connection: ${escape(observationTime(evidence))}</small>${connection.help ? `<details class="host-next-step"><summary>Next step</summary><p>${escape(connection.help)}</p></details>` : ''}<button class="button secondary small" data-action="host-probe" data-id="${escape(host.host_id)}">Check connection</button></td><td>${metric(r.cpu_busy_percent, '%')}<small>${metric(r.cpu_count, ' CPUs', 0)}</small></td><td>${metric(r.load_1m)} / ${metric(r.load_5m)}</td><td>${metric(Number.isFinite(r.memory_available_bytes) ? r.memory_available_bytes / 2**30 : null, ' GiB')}<small>of ${metric(Number.isFinite(r.memory_total_bytes) ? r.memory_total_bytes / 2**30 : null, ' GiB')}</small></td><td data-resource-status="${escape(host.host_id)}">${chip(resources.label, resources.color)}<small>Resources: ${escape(observationTime(sample))}</small>${sample?.error ? `<small>${escape(sample.error.message)}</small>` : ''}</td></tr>`;
  }).join('');
  let html = `<section class="panel"><div class="panel-head"><h2>Connections and resources</h2><span class="section-note">Two hosts checked at a time · explicit checks</span></div><p class="host-table-note">Listed hosts are registered candidates. Resource values are saved samples and expire after two minutes; unknown values do not mean idle. No automatic remote polling.</p><div class="host-selection-bar"><div><strong id="host-selection-count" role="status">${escape(hostSelectionLabel())}</strong><small>${state.hosts.length > 32 ? 'Choose the hosts for this check using the row checkboxes. No partial pool is chosen automatically.' : 'Only checked rows will be contacted. New registrations are not added to your existing selection.'}</small></div><div><button class="button secondary small" data-action="host-select-all" ${poolScanning || !state.hosts.length || state.hosts.length > 32 ? 'disabled' : ''}>Select all</button><button class="button secondary small" data-action="host-select-none" ${poolScanning || !selectedHostIds?.size ? 'disabled' : ''}>Clear selection</button></div></div><div class="pool-table-wrap"><table class="pool-table"><thead><tr><th scope="col">Select</th><th>Host</th><th>Connection evidence</th><th>CPU busy</th><th>Load 1m / 5m</th><th>Memory available</th><th>Resource evidence</th></tr></thead><tbody>${rows || '<tr><td colspan="7">No registered hosts.</td></tr>'}</tbody></table></div></section>`;
  html += `<section class="panel pool-placement"><div class="panel-head"><h2>Plan a new execution</h2><span class="chip">NO RESERVATION</span></div><div class="panel-body"><p id="placement-scope" class="subtle-note">${escape(hostPlanScope())}</p><form id="placement-form" class="placement-form"><div><label for="pool-cpus">Requested CPUs</label><input id="pool-cpus" name="cpus" type="number" min="1" max="4096" value="${requestedCpus}" required></div><div><label for="pool-memory">Memory (GiB)</label><input id="pool-memory" name="memory" type="number" min="0.001" step="any" max="1048576" value="${requestedMemory}" required></div><div><label for="pool-simulator">Simulator</label><select id="pool-simulator" name="simulator"><option value="spectre" ${requestedSimulator === 'spectre' ? 'selected' : ''}>Spectre</option><option value="ngspice" ${requestedSimulator === 'ngspice' ? 'selected' : ''}>ngspice</option></select></div><button class="button secondary" type="submit">Evaluate saved data</button></form><div class="placement-result">${placement ? `<strong>${placement.recommended_host_id ? 'Proposed host: ' + escape(placement.recommended_host_id) : 'No eligible host in this snapshot'}</strong><p class="subtle-note">Evaluation: ${escape(new Date(placement.evaluated_at).toLocaleString())} · ${placement.candidates.length} candidate(s). Resource observations expire; this does not reserve the host.</p><ul>${placement.excluded.map(h => `<li>${escape(h.label || h.host_id)}: ${escape(h.reasons.join('; '))}</li>`).join('')}</ul>` : '<p class="subtle-note">Set requirements, select 1–32 hosts above, then evaluate their saved observations. Unknown nodes are excluded.</p>'}</div><form id="execution-plan-form" class="execution-plan-form"><label for="execution-kind">Simulation origin</label><select id="execution-kind" name="kind"><option value="standalone">New standalone exported netlist</option><option value="virtuoso_managed">Existing Virtuoso-managed run</option></select><label for="execution-ref">Experiment input reference / native run reference</label><input id="execution-ref" name="reference" maxlength="96" placeholder="experiment-rc-check" required><label for="execution-host">Existing native host (Virtuoso only)</label><select id="execution-host" name="host">${state.hosts.map(h => `<option value="${escape(h.host_id)}">${escape(h.label)}</option>`).join('')}</select><button class="button secondary" type="submit">Prepare reviewable plan</button></form>${executionPlan ? planSummary(executionPlan) : ''}</div></section>`;
  return html;
}

function nativeChat() {
  if (isPublicDemo()) return publicAgentPage();
  const agents = state.native_agents;
  if (!agents.some(a => a.agent_id === nativeAgentId)) nativeAgentId = agents[0]?.agent_id || '';
  const agent = agents.find(a => a.agent_id === nativeAgentId);
  const history = state.questions.filter(q => q.agent_id === nativeAgentId);
  if (!selectedQa || !history.some(q => q.qa_id === selectedQa)) selectedQa = history[0]?.qa_id || null;
  qaView = history.find(q => q.qa_id === selectedQa) || qaView;
  let html = heading('NATIVE AGENT', 'Ask your connected agent.', 'A real question through the installed Codex runtime and its configured model provider.');
  if (!agents.length) return html + '<div class="empty"><h2>No native agent registered.</h2><p>Import a private profile using <code>spiceowl agents add --file PROFILE.json</code>, then refresh. Authentication stays with your native runtime.</p></div>';
  html += `<div class="native-grid"><section class="panel"><div class="panel-head"><h2>Question</h2>${chip('native', 'green')}</div><div class="panel-body"><form id="native-question-form"><label for="native-agent">Agent connection</label><select id="native-agent">${agents.map(a => `<option value="${escape(a.agent_id)}" ${a.agent_id === nativeAgentId ? 'selected' : ''}>${escape(a.label)}</option>`).join('')}</select><p class="subtle-note">Model: ${escape(agent.model || 'Configured native default')} · Credentials remain in the native runtime.</p><label for="native-question">Your question</label><textarea id="native-question" maxlength="2000" required>${escape(nativeQuestion)}</textarea><button class="button primary full" type="submit" ${qaSubmitting ? 'disabled' : ''}>Send real question</button></form><button class="button secondary small" data-action="native-probe" data-id="${escape(nativeAgentId)}">Check runtime readiness</button><p class="subtle-note">Readiness: ${escape(agent.last_probe?.transport || 'unchecked')} · Inference is verified only by a completed answer. No automatic provider fallback or simulation tools.</p></div></section><section class="panel"><div class="panel-head"><h2>Saved response</h2></div><div id="native-response" class="panel-body"></div></section></div><section class="panel native-history"><div class="panel-head"><h2>Question history</h2></div><div class="panel-body">${history.map(q => `<button class="question-history-row" data-action="native-select" data-id="${escape(q.qa_id)}"><span>${escape(q.question)}</span>${chip(q.status, q.status === 'completed' ? 'green' : 'amber')}</button>`).join('') || '<p class="subtle-note">No saved questions yet.</p>'}</div></section>`;
  return html;
}

function planSummary(plan) {
  return `<div class="plan-summary"><strong>${escape(title(plan.status))} · ${escape(plan.host_id || 'No host selected')}</strong><p>${escape(plan.reason)}</p><p>Origin: ${escape(title(plan.source_kind))}<br>Reference: ${escape(plan.origin?.input_ref || plan.native_run_ref || 'Unknown')}<br>Resources are not reserved. No simulation has been launched.</p><details><summary>View plan record</summary><pre class="plan-json">${escape(JSON.stringify(plan, null, 2))}</pre></details></div>`;
}

function renderNativeResponse() {
  const target = $('#native-response');
  if (!target) return;
  const q = qaView;
  target.innerHTML = q ? `${chip(q.status, q.status === 'completed' ? 'green' : 'amber')}<p class="response-question">${escape(q.question)}</p><p class="response-text">${escape(q.answer || (['queued', 'running'].includes(q.status) ? 'Waiting for the native runtime…' : 'No confirmed answer.'))}</p>${q.error ? `<p class="error-text">${escape(q.error.message)}</p>` : ''}<p class="subtle-note">Model: ${escape(q.model || 'Unknown')} · Provider: ${escape(q.provider || 'Unknown')}<br>${escape(q.updated_at || q.created_at)} · Real inference request</p>${q.status === 'outcome_unknown' ? '<p class="denial">The request outcome is unknown. It will not be automatically retried.</p>' : ''}` : '<p class="subtle-note">A confirmed answer will appear here.</p>';
}

async function pollNative() {
  if (isPublicDemo()) return;
  if (qaPolling || !selectedQa || location.hash !== '#agents') return;
  qaPolling = true;
  const id = selectedQa;
  try {
    const q = await api(`/api/agent-questions/${encodeURIComponent(id)}`);
    if (selectedQa === id) {
      const completedNow = qaView?.status !== q.status && !['queued', 'running'].includes(q.status);
      qaView = q; renderNativeResponse();
      if (completedNow) { await refresh(); if (location.hash === '#agents') render(); }
    }
  }
  catch (error) { toast(error.message); }
  finally { qaPolling = false; }
}

function memoryChart(run) {
  const history = run.resource_history;
  const points = history.filter(p => Number.isFinite(p.memory_mb));
  if (!points.length) return '<p class="empty-small">No memory observations yet. Advance the replay to collect the next synthetic point.</p>';
  const top = Math.max(1, ...points.map(p => p.memory_mb)) * 1.15;
  const width = 500, height = 155;
  const coords = history.map((p, i) => Number.isFinite(p.memory_mb) ? [42 + i / Math.max(1, history.length - 1) * 450, height - p.memory_mb / top * 130] : null);
  const segments = [[]];
  for (const point of coords) { if (point) segments.at(-1).push(point); else segments.push([]); }
  const lines = segments.filter(s => s.length).map(s => `<polyline class="chart-line" points="${s.map(([x,y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')}"/>`).join('');
  const missing = history.filter(p => !Number.isFinite(p.memory_mb)).length;
  return `<div class="chart-meta"><span class="chart-number">${escape(Math.round(points.at(-1).memory_mb))}<small>MB · last observed</small></span><span class="chip">SYNTHETIC</span></div><svg class="chart" viewBox="0 0 ${width} 180" role="img" aria-label="Synthetic memory: ${points.length} readings, ${missing} unavailable observations">${[0, .5, 1].map(v => `<line class="chart-grid" x1="40" y1="${height - v * 130}" x2="495" y2="${height - v * 130}"/><text x="1" y="${height - v * 130 + 3}">${Math.round(top * v)}</text>`).join('')}${lines}${coords.filter(Boolean).map(([x, y]) => `<circle cx="${x}" cy="${y}" r="3" fill="#498772"/>`).join('')}</svg><div class="chart-axis"><span>${missing ? `${missing} unavailable observation(s) · gaps preserved` : 'First observation'}</span><span>Observation ${history.length}</span></div>`;
}

function runDetail(run) {
  if (!run) return '<div class="empty"><h1>Run not found.</h1><a class="button secondary" href="#overview">Back to overview</a></div>';
  const states = [['Execution', run.execution], ['Observation', run.observation], ['Measurement', run.measurement_validity], ['Specification', run.specification_result]];
  const available = state.connections.filter(c => c.enabled);
  if (!available.some(c => c.connection_id === selectedConnection)) selectedConnection = available[0]?.connection_id || '';
  const history = state.sessions.filter(s => s.run_id === run.run_id);
  if (!history.some(s => s.session_id === selectedSession)) selectedSession = history[0]?.session_id || null;
  let html = `<a class="back-link" href="#overview">← All runs</a>` + heading('SYNTHETIC EXPERIMENT', escape(run.label), 'Manual replay · no simulator or remote process is running.', `<div class="split-actions">${runStatus(run)}<button class="button primary" data-action="advance" data-id="${escape(run.run_id)}" ${run.finished ? 'disabled' : ''}>${run.finished ? 'Replay complete' : 'Next observation →'}</button></div>`);
  html += `<div class="state-grid">${states.map(([label, value]) => `<div class="state-tile"><label>${label}</label><strong>${escape(title(value))}</strong></div>`).join('')}</div><div class="detail-grid"><div class="detail-stack"><section class="panel"><div class="panel-head"><h2>Memory observations</h2><span class="section-note">Finite scenario replay</span></div><div class="panel-body">${memoryChart(run)}</div></section><section class="panel"><div class="panel-head"><h2>Evidence timeline</h2><span class="section-note">${run.events.length} recorded events</span></div><div class="panel-body"><ol class="timeline">${[...run.events].reverse().map(event => `<li><div class="timeline-time">#${event.sequence} · ${escape(title(event.kind))}</div><p>${escape(event.message)}</p></li>`).join('')}</ol></div></section><section class="panel"><div class="panel-head"><h2>Execution identity</h2><span class="chip">PRESERVED</span></div><div class="panel-body identity">Attempt: ${escape(run.attempt_id)}<br>Submission: ${escape(run.submission_key)}<br>Owner: ${escape(JSON.stringify(run.execution_identity))}</div></section></div>`;
  html += `<section class="panel diagnosis"><div class="panel-head"><div class="diagnose-head"><img src="./owl.svg" alt=""><h2>Diagnostic assistant</h2></div><span class="chip">DEMO</span></div><div class="panel-body"><form id="diagnose-form"><label for="diagnostic-connection">Connection</label><select id="diagnostic-connection" ${available.length ? '' : 'disabled'}>${available.length ? available.map(c => `<option value="${escape(c.connection_id)}" ${selectedConnection === c.connection_id ? 'selected' : ''}>${escape(c.label)} · ${escape(title(c.kind))}</option>`).join('') : '<option>Add a connection first</option>'}</select><label for="diagnostic-question">Ask about this run</label><textarea id="diagnostic-question" maxlength="2000" required>${escape(draftQuestion)}</textarea><button class="button primary full" type="submit" ${available.length && !submitting ? '' : 'disabled'}>✧ Diagnose snapshot</button></form><p class="subtle-note">Offline fixture response. Evidence is frozen when you ask. Proposals are advisory; this build cannot change or restart simulations.</p></div><div id="diagnosis-result" class="diagnosis-result"><div class="history-empty">${selectedSession ? 'Loading saved diagnosis…' : 'Your diagnosis and evidence citations will appear here.'}</div></div><div class="session-picker"><label for="session-picker">Saved sessions</label><select id="session-picker" ${history.length ? '' : 'disabled'}>${history.length ? history.map(s => `<option value="${escape(s.session_id)}" ${selectedSession === s.session_id ? 'selected' : ''}>${escape(s.connection_label)} · ${escape(new Date(s.created_at).toLocaleString())}</option>`).join('') : '<option>No sessions yet</option>'}</select></div></section></div>`;
  return html;
}

function render() {
  routeEpoch++;
  sessionView = null;
  const page = location.hash.slice(1) || 'overview';
  const run = currentRun();
  $('#location').textContent = isPublicDemo() && page === 'local' ? 'Run locally' : page === 'live' ? '真实仿真 · CircuitGit' : page === 'connections' ? 'AI connections' : page === 'hosts' ? 'Simulation hosts' : page === 'agents' ? 'Agent chat' : run ? run.label : 'Overview';
  document.querySelectorAll('[data-nav]').forEach(a => a.classList.toggle('active', a.dataset.nav === page || (a.dataset.nav === 'overview' && page.startsWith('run/'))));
  $('#main').innerHTML = isPublicDemo() && page === 'local' ? publicLocalPage() : page === 'live' ? livePage() : page === 'connections' ? connections() : page === 'hosts' ? hosts() : page === 'agents' ? nativeChat() : page.startsWith('run/') ? runDetail(run) : overview();
  if (page === 'live' && !isPublicDemo() && !live.loaded && !live.loading) refreshLive();
  if (run && selectedSession) pollSession();
  if (page === 'hosts' && !isPublicDemo()) {
    $('#execution-kind').value = plannedKind;
    $('#execution-ref').value = plannedReference;
    if (plannedHost) $('#execution-host').value = plannedHost;
    updateHostSelectionUI();
  }
  if (page === 'agents' && !isPublicDemo()) { renderNativeResponse(); if (selectedQa) pollNative(); }
}

function renderSession() {
  const target = $('#diagnosis-result');
  if (!target || !sessionView) return;
  const turn = sessionView.turns.at(-1);
  if (!turn) return;
  const events = sessionView.events;
  const text = events.filter(e => e.kind === 'text.delta').map(e => typeof e.payload === 'string' ? e.payload : (e.payload.text ?? e.payload.delta ?? '')).join('');
  const partial = turn.status === 'outcome_unknown' || turn.status === 'cancelled';
  let html = `<div class="section-title"><div class="result-label">SAVED DIAGNOSIS ${partial ? '· PARTIAL' : ''}</div>${chip(turn.status, turn.status === 'completed' ? 'green' : turn.status === 'failed' || partial ? 'amber' : 'blue')}</div>`;
  html += `<p class="response-text">${escape(text || (turn.status === 'failed' ? 'The fixture could not complete this diagnosis.' : 'Waiting for a synthetic response…'))}</p>`;
  if (turn.status === 'outcome_unknown') html += '<div class="denial">The response ended without a confirmed outcome. Its request is preserved and will not replay automatically. New diagnoses on this connection or run are blocked while it remains unresolved.</div>';
  for (const event of events.filter(e => e.kind === 'permission.denied')) html += '<div class="denial">Runtime tool request denied. No shell command or simulator action was executed.</div>';
  if (turn.proposal) {
    html += `<div class="proposal">${chip(turn.decision?.result === 'allow' ? 'advisory only' : 'action denied', turn.decision?.result === 'allow' ? 'green' : 'amber')}<h3>${escape(title(turn.proposal.kind))}</h3><p>${escape(turn.proposal.summary)}</p><p class="identity">Evidence: ${escape((turn.proposal.evidence_event_ids || []).join(', '))}</p><p class="subtle-note">${escape(turn.decision?.reason)} No dispatch available.</p></div>`;
  }
  if (['dispatching', 'running'].includes(turn.status)) html += `<button class="button secondary small" data-action="cancel" data-id="${escape(turn.turn_id)}">Cancel diagnosis</button>`;
  html += `<div class="session-code">${escape(turn.session_id)}<br>${isPublicDemo() ? 'No model calls · ' + events.length + ' scripted events' : 'Cost: unknown · ' + events.length + ' stored events'}</div>`;
  target.innerHTML = html;
}

async function pollSession() {
  if (polling || !selectedSession || !currentRun()) return;
  polling = true;
  const sessionId = selectedSession;
  const epoch = routeEpoch;
  try {
    const data = await api(`/api/sessions/${encodeURIComponent(sessionId)}`);
    if (sessionId === selectedSession && epoch === routeEpoch) {
      sessionView = data;
      renderSession();
    }
  } catch (error) {
    if (epoch === routeEpoch && $('#diagnosis-result')) $('#diagnosis-result').innerHTML = `<p class="error-text">${escape(error.message)}</p>`;
  } finally { polling = false; }
}

document.addEventListener('click', async event => {
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  const action = button.dataset.action, id = button.dataset.id;
  if (action.startsWith('live-')) return;
  if (isPublicDemo() && ['add-host', 'import-pool', 'host-probe', 'pool-scan', 'native-probe', 'native-select'].includes(action)) {
    location.hash = '#local'; return;
  }
  if (action === 'native-select') { selectedQa = id; qaView = state.questions.find(q => q.qa_id === id) || null; renderNativeResponse(); await pollNative(); return; }
  if (action === 'add-connection') {
    $('#connection-form').reset(); $('#form-error').textContent = ''; $('#connection-dialog').showModal(); return;
  }
  if (action === 'close-dialog') { $('#connection-dialog').close(); return; }
  if (action === 'add-host') {
    $('#host-form-error').textContent = ''; $('#host-dialog').showModal(); return;
  }
  if (action === 'close-host-dialog') { $('#host-dialog').close(); return; }
  if (action === 'import-pool') {
    const selected = $('#pool-template').value;
    $('#pool-template').innerHTML = state.hosts.map(host => `<option value="${escape(host.host_id)}">${escape(host.label)} · ${escape(host.host_id)}</option>`).join('');
    if (state.hosts.some(host => host.host_id === selected)) $('#pool-template').value = selected;
    resetPoolPreview(); $('#pool-form-error').textContent = ''; $('#pool-dialog').showModal(); return;
  }
  if (action === 'close-pool-dialog') { $('#pool-dialog').close(); return; }
  if (action === 'host-select-all') {
    if (!poolScanning && state.hosts.length <= 32) { selectedHostIds = new Set(state.hosts.map(host => host.host_id)); invalidateHostPlans(); }
    updateHostSelectionUI(); return;
  }
  if (action === 'host-select-none') {
    if (!poolScanning) { selectedHostIds = new Set(); invalidateHostPlans(); }
    updateHostSelectionUI(); return;
  }
  button.disabled = true;
  const originalText = button.textContent;
  try {
    if (action === 'demo-reset') {
      if (!isPublicDemo()) return;
      await api('/api/demo/reset', {method: 'POST'});
      routeEpoch++; selectedSession = null; sessionView = null; selectedConnection = '';
      selectedQa = null; qaView = null; selectedHostIds = null; placement = null; executionPlan = null;
      draftQuestion = 'Explain this run and suggest the next read-only check.';
      demoPendingRequests.clear();
      $('#connection-dialog').close();
      await refresh();
      if (location.hash !== '#overview') location.hash = '#overview';
      render(); toast('Demo reset. The four synthetic scenarios are ready to explore.'); return;
    }
    if (action === 'pool-scan') {
      const host_ids = selectedPoolIds();
      poolScanning = true; button.textContent = 'Checking selected hosts…'; updateHostSelectionUI();
      try {
        const result = await api('/api/hosts/scan', {method: 'POST', body: {host_ids}});
        placement = null; executionPlan = null;
        toast(`Pool check saved. ${result.results.filter(r => r.error).length} host(s) remain unknown or incomplete.`);
      } finally { poolScanning = false; updateHostSelectionUI(); }
    }
    if (action === 'native-probe') {
      button.textContent = 'Checking runtime…';
      await api(`/api/native-agents/${encodeURIComponent(id)}/probe`, {method: 'POST'});
      toast('Readiness check saved. A completed answer is needed to verify inference.');
    }
    if (action === 'host-probe') {
      button.textContent = 'Checking…';
      const probe = await api(`/api/hosts/${encodeURIComponent(id)}/probe`, {method: 'POST'});
      toast(probe.error ? probe.error.message : 'Connection check saved. Simulation readiness remains untested.');
    }
    if (action === 'seed') { await api('/api/demo', {method: 'POST'}); toast('Demo workspace loaded. All data is synthetic.'); }
    if (action === 'advance') { await api(`/api/runs/${encodeURIComponent(id)}/advance`, {method: 'POST'}); }
    if (action === 'probe') { await api(`/api/connections/${encodeURIComponent(id)}/probe`, {method: 'POST', body: {level: 'models'}}); toast('Metadata probe complete. Inference access is still unverified.'); }
    if (action === 'toggle') { const connection = state.connections.find(c => c.connection_id === id); await api(`/api/connections/${encodeURIComponent(id)}`, {method: 'PATCH', body: {enabled: !connection.enabled}}); }
    if (action === 'remove') {
      if (!window.confirm('Remove this demo connection? Saved sessions are retained.')) return;
      await api(`/api/connections/${encodeURIComponent(id)}`, {method: 'DELETE'});
    }
    if (action === 'cancel') { await api(`/api/turns/${encodeURIComponent(id)}/cancel`, {method: 'POST'}); await pollSession(); return; }
    await refresh(); render();
  } catch (error) { toast(error.message); }
  finally { if (button.isConnected) { button.disabled = false; button.textContent = originalText; } if (action === 'pool-scan') updateHostSelectionUI(); }
});

document.addEventListener('click', handleLiveClick);

$('#connection-form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('[type=submit]'); button.disabled = true;
  try {
    const fields = new FormData(form);
    const kind = fields.get('kind');
    await api('/api/connections', {method: 'POST', body: {label: fields.get('label'), kind, backend: `fake_${kind}`, scenario: fields.get('scenario')}});
    $('#connection-dialog').close(); await refresh(); render(); toast('Demo connection created.');
  } catch (error) { $('#form-error').textContent = error.message; }
  finally { button.disabled = false; }
});

function resetPoolPreview() {
  poolImportPreview = null;
  $('#pool-import-preview').hidden = true;
  $('#pool-import-preview').textContent = '';
  $('#pool-import-submit').textContent = 'Review import';
}

function setHostFormBusy(form, busy) {
  form.querySelectorAll('input, select, textarea, button').forEach(control => { control.disabled = busy; });
}

$('#host-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (isPublicDemo()) { location.hash = '#local'; return; }
  const form = event.currentTarget, fields = new FormData(form);
  const profile = {schema: 'spiceowl.host.v1', tools: []};
  for (const key of ['host_id', 'label', 'ssh_target', 'expected_host', 'expected_user']) profile[key] = fields.get(key).trim();
  const override = fields.get('ssh_hostname').trim();
  if (override) { profile.schema = 'spiceowl.host.v2'; profile.ssh_hostname = override; }
  for (const kind of ['spectre', 'virtuoso', 'ngspice']) {
    const executable = fields.get(kind).trim();
    if (executable) profile.tools.push({kind, executable});
  }
  setHostFormBusy(form, true);
  $('#host-form-error').textContent = '';
  try {
    await api('/api/hosts', {method: 'POST', body: profile});
    $('#host-dialog').close(); form.reset();
    placement = null; executionPlan = null;
    await refresh(); render(); toast('Host registered locally. Use Check connection to verify it.');
  } catch (error) {
    if ($('#host-dialog').open) $('#host-form-error').textContent = error.message;
    else toast(error.message + ' Refresh the host list to inspect saved registrations.');
  }
  finally { setHostFormBusy(form, false); }
});

$('#pool-import-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (isPublicDemo()) { location.hash = '#local'; return; }
  const form = event.currentTarget, fields = new FormData(form);
  $('#pool-form-error').textContent = '';
  try {
    const names = fields.get('names').trim().split(/[\s,]+/).filter(Boolean);
    if (!names.length || names.length > 32 || new Set(names).size !== names.length) throw new Error('Enter 1–32 distinct target hostnames.');
    const label = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/;
    if (names.some(name => name.length > 253 || name.split('.').some(part => !label.test(part)))) throw new Error('Use explicit DNS hostnames: no wildcards, commands, user@host values or URLs.');
    const request = {template_id: fields.get('template_id'), names};
    if (!state.hosts.some(host => host.host_id === request.template_id)) throw new Error('Select an existing registered host as a route template.');
    if (poolImportPreview !== JSON.stringify(request)) {
      poolImportPreview = JSON.stringify(request);
      const template = state.hosts.find(host => host.host_id === request.template_id);
      $('#pool-import-preview').innerHTML = `<strong>Review ${names.length} target hostname(s)</strong><p>Route template: ${escape(template.label)} · ${escape(template.host_id)}</p><ul>${names.map(name => `<li>${escape(name)}</li>`).join('')}</ul><p>Matching existing registrations are reused. Every target needs an independent connection check.</p>`;
      $('#pool-import-preview').hidden = false;
      $('#pool-import-submit').textContent = 'Register listed hosts';
      return;
    }
    setHostFormBusy(form, true);
    await api('/api/hosts/import-pool', {method: 'POST', body: request});
    $('#pool-dialog').close(); form.reset(); resetPoolPreview();
    placement = null; executionPlan = null;
    await refresh(); render(); toast('Pool registered locally. Use Check selected hosts to verify the hosts and resources.');
  } catch (error) {
    if ($('#pool-dialog').open) $('#pool-form-error').textContent = error.message;
    else toast(error.message + ' Refresh the host list to inspect saved registrations.');
  }
  finally { setHostFormBusy(form, false); }
});

document.addEventListener('input', event => {
  const liveFields = {'live-configuration': 'configuration', 'live-source-commit': 'source_commit', 'live-run-id': 'run_id'};
  if (liveFields[event.target.id]) {
    liveDraft[liveFields[event.target.id]] = event.target.value;
    const collect=document.querySelector('[data-action="live-collect"]');
    if(collect){try{collect.disabled=live.busy||!live.runtime?.capabilities?.direct_collect||!!livePending('collect')||!liveCollectReady();}catch{collect.disabled=true;}}
  }
  if (event.target.id === 'diagnostic-question') draftQuestion = event.target.value;
  if (event.target.id === 'native-question') nativeQuestion = event.target.value;
  if (event.target.closest('#pool-import-form')) resetPoolPreview();
});
document.addEventListener('change', event => {
  if (event.target.matches('[data-host-select]')) {
    if (!poolScanning) {
      selectedHostIds ??= new Set();
      if (event.target.checked) selectedHostIds.add(event.target.dataset.hostSelect);
      else selectedHostIds.delete(event.target.dataset.hostSelect);
      invalidateHostPlans();
    }
    updateHostSelectionUI();
  }
  if (event.target.id === 'diagnostic-connection') selectedConnection = event.target.value;
  if (event.target.id === 'native-agent') { nativeAgentId = event.target.value; selectedQa = null; qaView = null; render(); }
  if (event.target.id === 'session-picker') { selectedSession = event.target.value; sessionView = null; routeEpoch++; pollSession(); }
  if (event.target.closest('#pool-import-form')) resetPoolPreview();
});

function placementRequirements() {
  requestedCpus = Number($('#pool-cpus').value);
  requestedMemory = Number($('#pool-memory').value);
  requestedSimulator = $('#pool-simulator').value;
  if (!Number.isInteger(requestedCpus) || requestedCpus < 1 || !Number.isFinite(requestedMemory) || requestedMemory <= 0) throw new Error('Enter positive CPU and memory requirements.');
  return {cpu_count: requestedCpus, memory_bytes: Math.ceil(requestedMemory * 2**30), simulator: requestedSimulator};
}

document.addEventListener('submit', async event => {
  const id = event.target.id;
  if (!['placement-form', 'execution-plan-form', 'native-question-form'].includes(id)) return;
  event.preventDefault();
  if (isPublicDemo()) { location.hash = '#local'; return; }
  if (id !== 'native-question-form' && planningBusy) return;
  const button = event.target.querySelector('[type=submit]');
  button.disabled = true;
  try {
    if (id === 'placement-form') {
      const host_ids = selectedPoolIds(), revision = hostSelectionRevision;
      planningBusy = true; updateHostSelectionUI();
      const result = await api('/api/placement/recommend', {method: 'POST', body: {...placementRequirements(), host_ids}});
      if (revision !== hostSelectionRevision) throw new Error('The host selection changed during evaluation. Evaluate the selected hosts again.');
      placement = result;
      executionPlan = null; render();
    } else if (id === 'execution-plan-form') {
      const form = new FormData(event.target), kind = form.get('kind'), reference = form.get('reference');
      plannedKind = kind; plannedReference = reference; plannedHost = form.get('host');
      const origin = kind === 'standalone' ? {kind, simulator: requestedSimulator, input_ref: reference} : {kind, simulator: 'spectre', host_id: form.get('host'), native_run_ref: reference};
      const requirements = placementRequirements();
      if (kind === 'standalone') origin.simulator = requirements.simulator;
      const host_ids = selectedPoolIds(), revision = hostSelectionRevision;
      planningBusy = true; updateHostSelectionUI();
      const result = await api('/api/execution/plan', {method: 'POST', body: {origin, requirements, host_ids}});
      if (revision !== hostSelectionRevision) throw new Error('The host selection changed during planning. Prepare a plan for the selected hosts again.');
      executionPlan = result;
      render();
    } else {
      if (qaSubmitting) return;
      qaSubmitting = true;
      const input = {agent_id: nativeAgentId, question: nativeQuestion};
      const pending = pendingGet('spiceowl.native-pending');
      let request;
      if (pending) {
        request = JSON.parse(pending);
        if (request.agent_id !== input.agent_id || request.question !== input.question) throw new Error('Retry the original question first: its delivery outcome is unknown.');
      } else {
        request = {...input, request_key: `question-${crypto.randomUUID()}`};
        pendingSet('spiceowl.native-pending', JSON.stringify(request));
      }
      const receipt = await api('/api/agent-questions', {method: 'POST', body: request});
      pendingRemove('spiceowl.native-pending');
      selectedQa = receipt.qa_id;
      await refresh(); render();
    }
  } catch (error) {
    if (id === 'native-question-form' && error.definitive) pendingRemove('spiceowl.native-pending');
    toast(error.message);
  } finally { qaSubmitting = false; if (button.isConnected) button.disabled = false; const send = $('#native-question-form [type=submit]'); if (send) send.disabled = false; if (id !== 'native-question-form') { planningBusy = false; updateHostSelectionUI(); } }
});

document.addEventListener('submit', async event => {
  if (event.target.id !== 'diagnose-form') return;
  event.preventDefault();
  if (submitting) return;
  submitting = true;
  const button = event.target.querySelector('[type=submit]'); button.disabled = true;
  let request;
  try {
    const run = currentRun();
    const input = {connection_id: selectedConnection, run_id: run.run_id, question: draftQuestion};
    const pending = pendingGet('spiceowl.pending-request');
    if (pending) {
      request = JSON.parse(pending);
      if (request.connection_id !== input.connection_id || request.run_id !== input.run_id || request.question !== input.question) throw new Error('A previous request has an unknown delivery outcome. Retry its original connection, run and question first.');
    } else {
      request = {...input, request_key: `request-${crypto.randomUUID()}`};
      pendingSet('spiceowl.pending-request', JSON.stringify(request));
    }
    const result = await api('/api/diagnose', {method: 'POST', body: request});
    pendingRemove('spiceowl.pending-request');
    selectedSession = result.session_id;
    await refresh();
    render();
  } catch (error) {
    if (error.definitive) pendingRemove('spiceowl.pending-request');
    toast(error.message);
  } finally { submitting = false; const current = $('#diagnose-form [type=submit]'); if (current) current.disabled = !selectedConnection; }
});

window.addEventListener('hashchange', async () => {
  selectedSession = null;
  try { await refresh(); render(); } catch (error) { toast(error.message); }
});
setInterval(() => { if (currentRun() && selectedSession && !document.hidden) pollSession(); }, 900);
setInterval(() => { if (location.hash === '#agents' && selectedQa && !document.hidden && (!qaView || ['queued', 'running'].includes(qaView.status))) pollNative(); }, 1000);
// Only cached CircuitGit snapshots refresh; no scan/status action is implicit.
setInterval(() => {
  if (!isPublicDemo() && location.hash === '#live' && live.enabled && !live.busy && !live.loading && !document.hidden
      && !document.activeElement?.closest('.live-input-grid')) refreshLive();
}, 3000);
// Update the age label locally; this does not request another remote sample.
setInterval(() => {
  if (isPublicDemo() || location.hash !== '#hosts' || document.hidden) return;
  document.querySelectorAll('[data-resource-status]').forEach(cell => {
    const host = state.hosts.find(item => item.host_id === cell.dataset.resourceStatus);
    const status = resourceStatus(host?.last_resources), badge = cell.querySelector('.chip');
    if (badge) badge.outerHTML = chip(status.label, status.color);
  });
}, 10000);
try { await refresh(); render(); }
catch (error) { $('#main').innerHTML = `<div class="error-panel"><h2>${isPublicDemo() ? 'Could not open the public demo.' : 'Could not open the local workspace.'}</h2><p>${escape(error.message)} ${isPublicDemo() ? 'Reload this page to try again.' : 'Refresh this page after the service is available.'}</p></div>`; }

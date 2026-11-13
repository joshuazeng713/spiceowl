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

function toast(message) {
  clearTimeout(toastTimer);
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 6000);
}

async function api(path, options = {}) {
  const method = options.method || 'GET';
  const response = await fetch(path, {method, headers: {'Content-Type': 'application/json', ...(method !== 'GET' ? {'X-SpiceOwl-Token': csrf} : {})}, ...(method !== 'GET' ? {body: JSON.stringify(options.body || {})} : {})});
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error?.message || 'The local request failed.');
    error.definitive = response.status < 500;
    throw error;
  }
  return data;
}

async function refresh() {
  state = await api('/api/state');
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

function overview() {
  const total = state.runs.length;
  const running = state.runs.filter(r => r.execution === 'running' || r.execution === 'queued').length;
  const attention = state.runs.filter(r => r.observation !== 'current' || r.execution === 'failed' || r.specification_result === 'fail').length;
  const done = state.runs.filter(r => r.execution === 'completed').length;
  const stats = [['Total runs', total, 'Stored synthetic experiments', '▦'], ['Last observed active', running, 'Check observation freshness', '◷'], ['Needs attention', attention, 'Evidence to inspect', '◇'], ['Execution completed', done, 'Validity is reported separately', '✓']];
  let html = heading('YOUR WORKSPACE', 'A clear view of every run.', 'Follow the evidence. Keep the experiment intact.', `<button class="button primary" data-action="seed"><span aria-hidden="true">＋</span> Load demo workspace</button>`);
  if (!total) {
    return html + `<div class="empty"><img src="/owl.svg" alt=""><h1>Your first night watch starts here.</h1><p>Explore connections, interrupted runs and diagnostic proposals with an offline demo. No accounts, API keys or simulator needed.</p><button class="button primary" data-action="seed">Load the synthetic demo <span aria-hidden="true">→</span></button></div>`;
  }
  html += `<div class="stat-grid">${stats.map(([label, value, note, glyph]) => `<div class="stat-card"><div class="stat-label">${label}<span class="stat-glyph" aria-hidden="true">${glyph}</span></div><div class="stat-value">${value}</div><div class="stat-note">${note}</div></div>`).join('')}</div>`;
  html += `<div class="watch-banner"><img src="/owl.svg" alt=""><div><h3>Explore the overnight workflow.</h3><p>Advance a synthetic run, inspect its evidence, then ask a demo agent for a read-only recommendation.</p></div><a class="button secondary small" href="#connections">Manage connections <span aria-hidden="true">→</span></a></div>`;
  html += `<div class="section-title"><h2>Simulation runs <span class="muted">/ ${total}</span></h2><span class="section-note">Manual replay · saved locally</span></div><div class="run-grid">`;
  html += state.runs.map(run => {
    const pct = Number.isFinite(run.progress) ? Math.round(run.progress * 100) : null;
    return `<a class="run-card" href="#run/${escape(run.run_id)}"><div class="card-top"><span class="chip">SYNTHETIC / TRANSIENT</span>${runStatus(run)}</div><h3>${escape(run.label)}</h3><div class="run-meta">${escape(run.attempt_id)}</div><div class="progress-row"><span>Scenario progress</span><span>${pct === null ? 'Unknown' : pct + '%'}</span></div><progress class="progress" max="100" value="${pct ?? 0}" aria-label="Scenario progress"></progress><div class="run-card-footer"><span>${run.events.length} evidence events · ${escape(title(run.measurement_validity))} measurement</span><strong>Inspect run <span class="arrow" aria-hidden="true">↗</span></strong></div></a>`;
  }).join('') + '</div>';
  return html;
}

function connections() {
  let html = heading('AI CONNECTIONS', 'Bring your own intelligence.', 'Keep agent runtimes and model APIs in one workspace.', `<button class="button primary" data-action="add-connection"><span aria-hidden="true">＋</span> Add demo connection</button>`);
  if (!state.connections.length) {
    html += `<div class="empty"><h2>No connections yet.</h2><p>Create a simulated agent or API connection to explore authentication states, streaming and failure handling.</p><button class="button primary" data-action="add-connection">Create demo connection</button></div>`;
  }
  html += `<div class="connections-list">${state.connections.map(c => {
    const status = c.status || {};
    const fields = [['Transport', status.transport || 'unchecked'], ['Authentication', status.authentication || 'unchecked'], ['Model catalog', status.catalog || 'unchecked'], ['Inference access', status.inference_access || 'unverified']];
    return `<article class="panel connection-card"><div class="connection-main"><div class="provider-icon" aria-hidden="true">${c.kind === 'agent' ? '✧' : '⌁'}</div><div class="connection-name"><h3>${escape(c.label)} ${chip(c.enabled ? 'enabled' : 'disabled', c.enabled ? 'green' : '')}</h3><p>${c.kind === 'agent' ? 'Agent runtime' : 'Model API'} · Offline fixture · ${escape(title(c.scenario))}</p></div><div class="connection-actions"><button class="button secondary small" data-action="probe" data-id="${escape(c.connection_id)}" ${c.enabled ? '' : 'disabled'}>Probe metadata</button><button class="button secondary small" data-action="toggle" data-id="${escape(c.connection_id)}">${c.enabled ? 'Disable' : 'Enable'}</button><button class="button secondary small" data-action="remove" data-id="${escape(c.connection_id)}" aria-label="Remove ${escape(c.label)}">Remove</button></div></div><div class="connection-readiness">${fields.map(([name, value]) => `<div class="readiness-field"><span>${name}</span><strong>${escape(title(value))}</strong></div>`).join('')}</div><div class="connection-bottom"><span>All statuses describe a simulated backend.</span><span>Revision ${c.revision} · No credentials</span></div></article>`;
  }).join('')}</div>`;
  return html + `<div class="planned-note"><strong>Native agent and MCP</strong><p>These connection cards use fixture responses. Open <a href="#agents">Agent chat</a> for a real question through a registered native runtime.</p><p>MCP exposes stored runs and host observations to existing clients: install the optional extra and configure <code>spiceowl --data-dir ABSOLUTE_STATE_DIR mcp</code>. Its tools only query records.</p></div>`;
}

function hosts() {
  let html = heading('SIMULATION HOSTS', 'Your simulation server pool.', 'Compare saved resource observations and plan where a new exported-netlist job could run.', `<button class="button primary" data-action="pool-scan" ${poolScanning || !state.hosts.length ? 'disabled' : ''}>${poolScanning ? 'Checking pool…' : 'Check server pool'}</button>`);
  html += poolOverview();
  html += `<div class="watch-banner"><img src="/owl.svg" alt=""><div><h3>Your existing SSH route.</h3><p>Import a private host profile with <code>spiceowl hosts add --file PROFILE.json</code>. SSH keys and jump hosts remain in your SSH configuration.</p></div></div>`;
  if (!state.hosts.length) html += `<div class="empty"><h2>No simulation hosts registered.</h2><p>Add a profile through the CLI, then refresh this page. Adding or listing hosts makes no remote request.</p></div>`;
  html += `<details class="host-details" ${state.hosts.length <= 1 ? 'open' : ''}><summary>Host identity and tool versions</summary><div class="connections-list">${state.hosts.map(host => {
    const probe = host.last_probe;
    const matched = probe?.identity?.verified === true;
    const when = probe ? new Date(probe.observed_at).toLocaleString() : 'Never checked';
    const status = !probe ? 'not checked' : probe.error ? 'check incomplete' : matched ? 'identity matched' : 'identity unverified';
    const fields = [['SSH transport', probe?.transport || 'unchecked'], ['Host identity', matched ? 'matched at last check' : 'unverified'], ['Scope', 'connectivity only'], ['Simulation readiness', 'not tested']];
    const toolRows = host.tools.map(tool => {
      const result = probe?.tools?.find(item => item.kind === tool.kind);
      return `<div class="host-tool"><strong>${escape(title(tool.kind))}</strong>${chip(result?.status || 'unchecked', result?.status === 'available' ? 'green' : result ? 'amber' : '')}<span class="host-version">${escape(result?.version || 'Version unverified')}</span>${result?.error ? `<p class="error-text">${escape(result.error.message)}</p>` : ''}</div>`;
    }).join('');
    return `<article class="panel connection-card"><div class="connection-main"><div class="provider-icon" aria-hidden="true">▤</div><div class="connection-name"><h3>${escape(host.label)} ${chip(status, matched && !probe.error ? 'green' : probe ? 'amber' : '')}</h3><p>Real connection · ${escape(host.host_id)}</p></div><div class="connection-actions"><button class="button primary small" data-action="host-probe" data-id="${escape(host.host_id)}">Check connection</button></div></div><div class="connection-readiness">${fields.map(([label, value]) => `<div class="readiness-field"><span>${label}</span><strong>${escape(title(value))}</strong></div>`).join('')}</div><div class="host-tools">${toolRows}</div>${probe?.error ? `<div class="host-error">${escape(probe.error.message)}</div>` : ''}<div class="connection-bottom"><span>Last check: ${escape(when)}</span><span>Saved result · no automatic polling</span></div></article>`;
  }).join('')}</div></details>`;
  return html + `<div class="planned-note"><strong>Plans preserve simulation ownership.</strong><p>A new standalone job can receive a proposed host. An existing Virtuoso-managed run retains its original host and native run reference. These plans do not reserve resources or launch a simulation; input, environment and license checks still belong to submission.</p></div>`;
}

function metric(value, suffix = '', digits = 1) {
  return Number.isFinite(value) ? `${escape(value.toFixed(digits))}${suffix}` : 'Unknown';
}

function poolOverview() {
  const rows = state.hosts.map(host => {
    const sample = host.last_resources, r = sample?.resources || {};
    const age = sample?.observed_at ? (Date.now() - Date.parse(sample.observed_at)) / 1000 : NaN;
    const known = sample?.identity?.verified && !sample.error && age >= 0 && age <= 120;
    const issue = host.last_probe?.error || sample?.error;
    return `<tr><th scope="row">${escape(host.label)}<small>${escape(host.host_id)}</small></th><td>${metric(r.cpu_busy_percent, '%')}<small>${metric(r.cpu_count, ' CPUs', 0)}</small></td><td>${metric(r.load_1m)} / ${metric(r.load_5m)}</td><td>${metric(Number.isFinite(r.memory_available_bytes) ? r.memory_available_bytes / 2**30 : null, ' GiB')}<small>of ${metric(Number.isFinite(r.memory_total_bytes) ? r.memory_total_bytes / 2**30 : null, ' GiB')}</small></td><td>${chip(known ? 'recent observation' : sample ? 'unknown or stale' : 'not checked', known ? 'green' : 'amber')}<small>${sample?.observed_at ? escape(new Date(sample.observed_at).toLocaleTimeString()) : 'No resource sample'}</small>${issue ? `<small>${escape(issue.message)}</small>` : ''}</td></tr>`;
  }).join('');
  let html = `<section class="panel"><div class="panel-head"><h2>Resource observations</h2><span class="section-note">Two hosts checked at a time · manual refresh</span></div><div class="pool-table-wrap"><table class="pool-table"><thead><tr><th>Host</th><th>CPU busy</th><th>Load 1m / 5m</th><th>Memory available</th><th>Observation</th></tr></thead><tbody>${rows || '<tr><td colspan="5">No registered hosts.</td></tr>'}</tbody></table></div></section>`;
  html += `<section class="panel pool-placement"><div class="panel-head"><h2>Plan a new execution</h2><span class="chip">NO RESERVATION</span></div><div class="panel-body"><form id="placement-form" class="placement-form"><div><label for="pool-cpus">Requested CPUs</label><input id="pool-cpus" name="cpus" type="number" min="1" max="4096" value="${requestedCpus}" required></div><div><label for="pool-memory">Memory (GiB)</label><input id="pool-memory" name="memory" type="number" min="0.001" step="any" max="1048576" value="${requestedMemory}" required></div><div><label for="pool-simulator">Simulator</label><select id="pool-simulator" name="simulator"><option value="spectre" ${requestedSimulator === 'spectre' ? 'selected' : ''}>Spectre</option><option value="ngspice" ${requestedSimulator === 'ngspice' ? 'selected' : ''}>ngspice</option></select></div><button class="button secondary" type="submit">Evaluate saved data</button></form><div class="placement-result">${placement ? `<strong>${placement.recommended_host_id ? 'Proposed host: ' + escape(placement.recommended_host_id) : 'No eligible host in this snapshot'}</strong><p class="subtle-note">Evaluation: ${escape(new Date(placement.evaluated_at).toLocaleString())} · ${placement.candidates.length} candidate(s). Resource observations expire; this does not reserve the host.</p><ul>${placement.excluded.map(h => `<li>${escape(h.label || h.host_id)}: ${escape(h.reasons.join('; '))}</li>`).join('')}</ul>` : '<p class="subtle-note">Set requirements, then evaluate recent saved observations. Unknown nodes are excluded.</p>'}</div><form id="execution-plan-form" class="execution-plan-form"><label for="execution-kind">Simulation origin</label><select id="execution-kind" name="kind"><option value="standalone">New standalone exported netlist</option><option value="virtuoso_managed">Existing Virtuoso-managed run</option></select><label for="execution-ref">Experiment input reference / native run reference</label><input id="execution-ref" name="reference" maxlength="96" placeholder="experiment-rc-check" required><label for="execution-host">Existing native host (Virtuoso only)</label><select id="execution-host" name="host">${state.hosts.map(h => `<option value="${escape(h.host_id)}">${escape(h.label)}</option>`).join('')}</select><button class="button secondary" type="submit">Prepare reviewable plan</button></form>${executionPlan ? planSummary(executionPlan) : ''}</div></section>`;
  return html;
}

function nativeChat() {
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
  html += `<section class="panel diagnosis"><div class="panel-head"><div class="diagnose-head"><img src="/owl.svg" alt=""><h2>Diagnostic assistant</h2></div><span class="chip">DEMO</span></div><div class="panel-body"><form id="diagnose-form"><label for="diagnostic-connection">Connection</label><select id="diagnostic-connection" ${available.length ? '' : 'disabled'}>${available.length ? available.map(c => `<option value="${escape(c.connection_id)}" ${selectedConnection === c.connection_id ? 'selected' : ''}>${escape(c.label)} · ${escape(title(c.kind))}</option>`).join('') : '<option>Add a connection first</option>'}</select><label for="diagnostic-question">Ask about this run</label><textarea id="diagnostic-question" maxlength="2000" required>${escape(draftQuestion)}</textarea><button class="button primary full" type="submit" ${available.length && !submitting ? '' : 'disabled'}>✧ Diagnose snapshot</button></form><p class="subtle-note">Offline fixture response. Evidence is frozen when you ask. Proposals are advisory; this build cannot change or restart simulations.</p></div><div id="diagnosis-result" class="diagnosis-result"><div class="history-empty">${selectedSession ? 'Loading saved diagnosis…' : 'Your diagnosis and evidence citations will appear here.'}</div></div><div class="session-picker"><label for="session-picker">Saved sessions</label><select id="session-picker" ${history.length ? '' : 'disabled'}>${history.length ? history.map(s => `<option value="${escape(s.session_id)}" ${selectedSession === s.session_id ? 'selected' : ''}>${escape(s.connection_label)} · ${escape(new Date(s.created_at).toLocaleString())}</option>`).join('') : '<option>No sessions yet</option>'}</select></div></section></div>`;
  return html;
}

function render() {
  routeEpoch++;
  sessionView = null;
  const page = location.hash.slice(1) || 'overview';
  const run = currentRun();
  $('#location').textContent = page === 'connections' ? 'AI connections' : page === 'hosts' ? 'Simulation hosts' : page === 'agents' ? 'Agent chat' : run ? run.label : 'Overview';
  document.querySelectorAll('[data-nav]').forEach(a => a.classList.toggle('active', a.dataset.nav === page || (a.dataset.nav === 'overview' && page.startsWith('run/'))));
  $('#main').innerHTML = page === 'connections' ? connections() : page === 'hosts' ? hosts() : page === 'agents' ? nativeChat() : page.startsWith('run/') ? runDetail(run) : overview();
  if (run && selectedSession) pollSession();
  if (page === 'hosts') {
    $('#execution-kind').value = plannedKind;
    $('#execution-ref').value = plannedReference;
    if (plannedHost) $('#execution-host').value = plannedHost;
  }
  if (page === 'agents') { renderNativeResponse(); if (selectedQa) pollNative(); }
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
  html += `<div class="session-code">${escape(turn.session_id)}<br>Cost: unknown · ${events.length} stored events</div>`;
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
  if (action === 'native-select') { selectedQa = id; qaView = state.questions.find(q => q.qa_id === id) || null; renderNativeResponse(); await pollNative(); return; }
  if (action === 'add-connection') {
    $('#connection-form').reset(); $('#form-error').textContent = ''; $('#connection-dialog').showModal(); return;
  }
  if (action === 'close-dialog') { $('#connection-dialog').close(); return; }
  button.disabled = true;
  const originalText = button.textContent;
  try {
    if (action === 'pool-scan') {
      poolScanning = true; button.textContent = 'Checking pool…';
      try {
        const result = await api('/api/hosts/scan', {method: 'POST'});
        placement = null; executionPlan = null;
        toast(`Pool check saved. ${result.results.filter(r => r.error).length} host(s) remain unknown or incomplete.`);
      } finally { poolScanning = false; }
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
  finally { if (button.isConnected) { button.disabled = false; button.textContent = originalText; } }
});

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

document.addEventListener('input', event => {
  if (event.target.id === 'diagnostic-question') draftQuestion = event.target.value;
  if (event.target.id === 'native-question') nativeQuestion = event.target.value;
});
document.addEventListener('change', event => {
  if (event.target.id === 'diagnostic-connection') selectedConnection = event.target.value;
  if (event.target.id === 'native-agent') { nativeAgentId = event.target.value; selectedQa = null; qaView = null; render(); }
  if (event.target.id === 'session-picker') { selectedSession = event.target.value; sessionView = null; routeEpoch++; pollSession(); }
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
  const button = event.target.querySelector('[type=submit]');
  button.disabled = true;
  try {
    if (id === 'placement-form') {
      placement = await api('/api/placement/recommend', {method: 'POST', body: placementRequirements()});
      executionPlan = null; render();
    } else if (id === 'execution-plan-form') {
      const form = new FormData(event.target), kind = form.get('kind'), reference = form.get('reference');
      plannedKind = kind; plannedReference = reference; plannedHost = form.get('host');
      const origin = kind === 'standalone' ? {kind, simulator: requestedSimulator, input_ref: reference} : {kind, simulator: 'spectre', host_id: form.get('host'), native_run_ref: reference};
      const requirements = placementRequirements();
      if (kind === 'standalone') origin.simulator = requirements.simulator;
      executionPlan = await api('/api/execution/plan', {method: 'POST', body: {origin, requirements}});
      render();
    } else {
      if (qaSubmitting) return;
      qaSubmitting = true;
      const input = {agent_id: nativeAgentId, question: nativeQuestion};
      const pending = sessionStorage.getItem('spiceowl.native-pending');
      let request;
      if (pending) {
        request = JSON.parse(pending);
        if (request.agent_id !== input.agent_id || request.question !== input.question) throw new Error('Retry the original question first: its delivery outcome is unknown.');
      } else {
        request = {...input, request_key: `question-${crypto.randomUUID()}`};
        sessionStorage.setItem('spiceowl.native-pending', JSON.stringify(request));
      }
      const receipt = await api('/api/agent-questions', {method: 'POST', body: request});
      sessionStorage.removeItem('spiceowl.native-pending');
      selectedQa = receipt.qa_id;
      await refresh(); render();
    }
  } catch (error) {
    if (id === 'native-question-form' && error.definitive) sessionStorage.removeItem('spiceowl.native-pending');
    toast(error.message);
  } finally { qaSubmitting = false; if (button.isConnected) button.disabled = false; const send = $('#native-question-form [type=submit]'); if (send) send.disabled = false; }
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
    const pending = sessionStorage.getItem('spiceowl.pending-request');
    if (pending) {
      request = JSON.parse(pending);
      if (request.connection_id !== input.connection_id || request.run_id !== input.run_id || request.question !== input.question) throw new Error('A previous request has an unknown delivery outcome. Retry its original connection, run and question first.');
    } else {
      request = {...input, request_key: `request-${crypto.randomUUID()}`};
      sessionStorage.setItem('spiceowl.pending-request', JSON.stringify(request));
    }
    const result = await api('/api/diagnose', {method: 'POST', body: request});
    sessionStorage.removeItem('spiceowl.pending-request');
    selectedSession = result.session_id;
    await refresh();
    render();
  } catch (error) {
    if (error.definitive) sessionStorage.removeItem('spiceowl.pending-request');
    toast(error.message);
  } finally { submitting = false; const current = $('#diagnose-form [type=submit]'); if (current) current.disabled = !selectedConnection; }
});

window.addEventListener('hashchange', async () => {
  selectedSession = null;
  try { await refresh(); render(); } catch (error) { toast(error.message); }
});
setInterval(() => { if (currentRun() && selectedSession && !document.hidden) pollSession(); }, 900);
setInterval(() => { if (location.hash === '#agents' && selectedQa && !document.hidden && (!qaView || ['queued', 'running'].includes(qaView.status))) pollNative(); }, 1000);
try { await refresh(); render(); }
catch (error) { $('#main').innerHTML = `<div class="error-panel"><h2>Could not open the local workspace.</h2><p>${escape(error.message)} Refresh this page after the service is available.</p></div>`; }

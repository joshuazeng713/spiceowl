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
    updateHostSelectionUI();
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
// Update the age label locally; this does not request another remote sample.
setInterval(() => {
  if (location.hash !== '#hosts' || document.hidden) return;
  document.querySelectorAll('[data-resource-status]').forEach(cell => {
    const host = state.hosts.find(item => item.host_id === cell.dataset.resourceStatus);
    const status = resourceStatus(host?.last_resources), badge = cell.querySelector('.chip');
    if (badge) badge.outerHTML = chip(status.label, status.color);
  });
}, 10000);
try { await refresh(); render(); }
catch (error) { $('#main').innerHTML = `<div class="error-panel"><h2>Could not open the local workspace.</h2><p>${escape(error.message)} Refresh this page after the service is available.</p></div>`; }

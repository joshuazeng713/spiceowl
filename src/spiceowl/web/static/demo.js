/* Static, synthetic-only API for the published Pages build. No network calls. */
(() => {
  'use strict';
  const data = window.__SPICEOWL_DEMO_DATA__;
  if (!data || data.schema !== 'spiceowl.pages-demo.v1') throw new Error('The static demo data is missing.');
  const copy = value => JSON.parse(JSON.stringify(value));
  const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
  const scenarios = ['success', 'inference_denied', 'disconnect', 'permission_denied'];
  const runIds = Object.keys(data.frames);
  const idPattern = /^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$/;
  const now = () => new Date().toISOString();
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const printable = value => typeof value === 'string' && !/[\u0000-\u001f\u007f]/.test(value);
  const script = document.currentScript?.src || new URL('demo.js', window.location.href).href;
  const prefix = `spiceowl:public-demo:${new URL('.', script).pathname}:`;
  const storageKey = prefix + data.metadata.build_id;
  let storageMode = 'memory';
  let memory;
  let serialQueue = Promise.resolve();

  function fail(message, code = 'demo_invalid_request') {
    const error = new Error(message);
    error.code = code;
    error.definitive = true;
    throw error;
  }
  function keys(body, allowed, required = []) {
    if (!object(body) || Object.keys(body).some(key => !allowed.includes(key)) || required.some(key => !own(body, key))) {
      fail('Only the displayed synthetic demo fields are accepted. Do not enter credentials.');
    }
  }
  function empty(body) { keys(body, []); }
  function identifier(value) {
    if (typeof value !== 'string' || !idPattern.test(value)) fail('Invalid demo identifier.');
    return value;
  }
  function initialConnection(connection) {
    return {...copy(connection), deleted: false, probe_level: null, probed_at: null};
  }
  function fresh() {
    return {schema: 1, build_id: data.metadata.build_id, serial: 0,
      cursors: Object.fromEntries(runIds.map(id => [id, 0])),
      connections: data.initial_state.connections.map(initialConnection), diagnoses: []};
  }
  function validConnection(connection) {
    return object(connection) && typeof connection.connection_id === 'string' && idPattern.test(connection.connection_id)
      && printable(connection.label) && connection.label.trim().length > 0 && connection.label.length <= 80
      && ['agent', 'model'].includes(connection.kind) && connection.backend === `fake_${connection.kind}`
      && scenarios.includes(connection.scenario) && typeof connection.enabled === 'boolean'
      && connection.synthetic === true && typeof connection.deleted === 'boolean'
      && connection.status === null
      && Number.isInteger(connection.revision) && connection.revision > 0 && connection.revision < 10000
      && printable(connection.created_at) && connection.created_at.length <= 64
      && (connection.probed_at === null || printable(connection.probed_at) && connection.probed_at.length <= 64)
      && (connection.probe_level === null || ['transport', 'auth', 'models'].includes(connection.probe_level))
      && Object.keys(connection).every(key => ['connection_id', 'label', 'kind', 'backend', 'scenario', 'enabled',
        'synthetic', 'deleted', 'revision', 'created_at', 'status', 'probe_level', 'probed_at'].includes(key));
  }
  function validSaved(value) {
    if (!object(value) || value.schema !== 1 || value.build_id !== data.metadata.build_id
        || Object.keys(value).sort().join(',') !== 'build_id,connections,cursors,diagnoses,schema,serial'
        || !Number.isInteger(value.serial) || value.serial < 0 || value.serial > 10000
        || !object(value.cursors) || Object.keys(value.cursors).length !== runIds.length
        || !runIds.every(id => Number.isInteger(value.cursors[id]) && value.cursors[id] >= 0 && value.cursors[id] < data.frames[id].length)
        || !Array.isArray(value.connections) || value.connections.length > 40 || !value.connections.every(validConnection)
        || new Set(value.connections.map(c => c.connection_id)).size !== value.connections.length
        || !Array.isArray(value.diagnoses) || value.diagnoses.length > 40) return false;
    const seen = new Set(), requestKeys = new Set();
    return value.diagnoses.every(item => {
      if (!object(item) || Object.keys(item).sort().join(',') !== 'connection_id,connection_label,created_at,cursor,digest,kind,request_key,run_id,scenario,serial'
          || !Number.isInteger(item.serial) || item.serial < 1 || item.serial > value.serial || seen.has(item.serial)
          || !own(data.frames, item.run_id) || !Number.isInteger(item.cursor) || item.cursor < 0 || item.cursor >= data.frames[item.run_id].length
          || !scenarios.includes(item.scenario) || !['agent', 'model'].includes(item.kind)
          || !printable(item.connection_label) || item.connection_label.length > 80
          || typeof item.connection_id !== 'string' || !idPattern.test(item.connection_id)
          || typeof item.request_key !== 'string' || !idPattern.test(item.request_key)
          || requestKeys.has(item.request_key)
          || typeof item.digest !== 'string' || !/^[a-f0-9]{64}$/.test(item.digest)
          || !printable(item.created_at) || item.created_at.length > 64) return false;
      seen.add(item.serial);
      requestKeys.add(item.request_key);
      return true;
    });
  }
  function persist() {
    if (storageMode !== 'session') return;
    try { window.sessionStorage.setItem(storageKey, JSON.stringify(memory)); }
    catch (_) { storageMode = 'memory'; }
  }
  memory = fresh();
  try {
    const saved = window.sessionStorage.getItem(storageKey);
    if (saved && saved.length <= 200000) {
      try { const parsed = JSON.parse(saved); if (validSaved(parsed)) memory = parsed; } catch (_) { /* Discard damaged demo state. */ }
    }
    window.sessionStorage.setItem(storageKey, JSON.stringify(memory));
    storageMode = 'session';
    for (let index = window.sessionStorage.length - 1; index >= 0; index--) {
      const key = window.sessionStorage.key(index);
      if (key && key.startsWith(prefix) && key !== storageKey) window.sessionStorage.removeItem(key);
    }
  } catch (_) { storageMode = 'memory'; }

  function publicConnection(connection) {
    const result = copy(connection);
    result.status = connection.probe_level ? copy(data.probes[connection.kind][connection.probe_level]) : null;
    delete result.probe_level;
    return result;
  }
  function connectionById(id) {
    identifier(id);
    const connection = memory.connections.find(item => item.connection_id === id && !item.deleted);
    if (!connection) fail('This demo connection was not found.', 'not_found');
    return connection;
  }
  function runById(id) {
    if (!own(data.frames, id)) fail('This synthetic run was not found.', 'not_found');
    return copy(data.frames[id][memory.cursors[id]]);
  }
  function descriptorView(item) {
    const template = copy(data.diagnostics[item.run_id][item.cursor][item.scenario]);
    const session_id = `demo-session-${item.serial}`, turn_id = `demo-turn-${item.serial}`;
    const proposalId = `demo-proposal-${item.serial}`, decisionId = `demo-decision-${item.serial}`;
    if (template.proposal) template.proposal.proposal_id = proposalId;
    if (template.decision) Object.assign(template.decision, {decision_id: decisionId, proposal_id: proposalId});
    const session = {session_id, connection_id: item.connection_id, connection_label: item.connection_label,
      run_id: item.run_id, created_at: item.created_at, synthetic: true, scripted: true};
    const turn = {turn_id, session_id, connection_id: item.connection_id, run_id: item.run_id,
      request_key: item.request_key, question: 'Scripted demo: free-text is not sent to a model or retained.',
      status: template.status, created_at: item.created_at, evidence: template.evidence,
      proposal: template.proposal, decision: template.decision, usage: template.usage, synthetic: true, scripted: true};
    const eventTemplates = [{kind: 'turn.accepted', payload: {status: 'dispatching', synthetic: true, scripted: true}}, ...template.events];
    const events = eventTemplates.map((event, index) => {
      if (event.kind === 'proposal.ready') event.payload = {proposal: template.proposal, decision: template.decision};
      return {event_id: `demo-event-${item.serial}-${index + 1}`, sequence: index + 1, session_id, turn_id,
        kind: event.kind, payload: event.payload, observed_at: item.created_at};
    });
    return {session, turns: [turn], events, next_cursor: events.length};
  }
  function state() {
    return {...copy(data.initial_state), public_demo: true, scripted: true, csrf_token: 'public-demo-no-server',
      demo_storage: storageMode,
      demo_notice: storageMode === 'session' ? 'Changes stay in this browser tab; a new build or Reset clears them.'
        : 'Storage unavailable: changes reset on refresh.',
      connections: memory.connections.filter(c => !c.deleted).map(publicConnection),
      runs: runIds.map(runById), sessions: memory.diagnoses.slice().reverse().map(item => descriptorView(item).session)};
  }
  async function digest(value) {
    if (!window.crypto?.subtle) fail('This browser cannot create a local request fingerprint. No text was sent.');
    const bytes = await window.crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
    return Array.from(new Uint8Array(bytes), byte => byte.toString(16).padStart(2, '0')).join('');
  }
  async function dispatch(path, options) {
    if (typeof path !== 'string' || !path.startsWith('/api/') || path.length > 512) fail('Invalid static demo route.');
    const method = options.method || 'GET', body = options.body ?? {};
    if (!['GET', 'POST', 'PATCH', 'DELETE'].includes(method)) fail('Unsupported demo method.');
    if (!object(body) || JSON.stringify(body).length > 16384) fail('The demo request must be a small JSON object.');
    if (method === 'GET') empty(body);
    if (path === '/api/state' && method === 'GET') return state();
    if (path === '/api/demo' && method === 'POST') { empty(body); return state(); }
    if (path === '/api/demo/reset' && method === 'POST') { empty(body); memory = fresh(); persist(); return state(); }
    if (/^\/api\/(hosts|native-agents|agent-questions|placement|execution|ic)(?:\/|$)/.test(path)) {
      fail('This public demo uses scripted fixtures. Install SpiceOwl locally to configure servers or real agents; no connection was attempted.', 'demo_operation_unavailable');
    }
    if (path === '/api/connections' && method === 'POST') {
      keys(body, ['connection_id', 'label', 'kind', 'backend', 'scenario', 'enabled'], ['label', 'kind', 'backend']);
      const candidate = {connection_id: own(body, 'connection_id') ? identifier(body.connection_id) : `demo-connection-${memory.serial + 1}`,
        label: body.label, kind: body.kind, backend: body.backend, scenario: own(body, 'scenario') ? body.scenario : 'success', enabled: own(body, 'enabled') ? body.enabled : true,
        synthetic: true, deleted: false, revision: 1, created_at: now(), status: null, probed_at: null, probe_level: null};
      if (!validConnection(candidate)) fail('Use a name of 1–80 printable characters and one of the fake demo backends.');
      if (memory.connections.some(c => c.connection_id === candidate.connection_id)) fail('That demo connection ID already exists.', 'conflict');
      if (memory.connections.length >= 40) fail('This demo keeps at most 40 connections. Reset the demo to start again.');
      candidate.label = candidate.label.trim(); memory.serial++; memory.connections.push(candidate); persist();
      return publicConnection(candidate);
    }
    const connectionMatch = path.match(/^\/api\/connections\/([A-Za-z0-9_-]+)(\/probe)?$/);
    if (connectionMatch) {
      const connection = connectionById(connectionMatch[1]);
      if (connectionMatch[2] && method === 'POST') {
        keys(body, ['level'], ['level']);
        if (!connection.enabled) fail('Enable this demo connection before probing it.', 'connection_disabled');
        if (!['transport', 'auth', 'models'].includes(body.level)) fail('Invalid metadata probe level.');
        connection.probe_level = body.level; connection.probed_at = now(); persist(); return publicConnection(connection);
      }
      if (!connectionMatch[2] && method === 'PATCH') {
        keys(body, ['enabled'], ['enabled']);
        if (typeof body.enabled !== 'boolean') fail('Enabled must be true or false.');
        connection.enabled = body.enabled; connection.revision++; persist(); return publicConnection(connection);
      }
      if (!connectionMatch[2] && method === 'DELETE') {
        empty(body); connection.enabled = false; connection.deleted = true; connection.revision++; persist(); return publicConnection(connection);
      }
    }
    const runMatch = path.match(/^\/api\/runs\/([A-Za-z0-9_-]+)(\/advance)?$/);
    if (runMatch) {
      runById(runMatch[1]);
      if (!runMatch[2] && method === 'GET') return runById(runMatch[1]);
      if (runMatch[2] && method === 'POST') {
        empty(body); memory.cursors[runMatch[1]] = Math.min(memory.cursors[runMatch[1]] + 1, data.frames[runMatch[1]].length - 1);
        persist(); return runById(runMatch[1]);
      }
    }
    if (path === '/api/diagnose' && method === 'POST') {
      keys(body, ['connection_id', 'run_id', 'question', 'request_key'], ['connection_id', 'run_id', 'question', 'request_key']);
      identifier(body.request_key); identifier(body.connection_id); identifier(body.run_id);
      if (typeof body.question !== 'string' || !body.question.trim() || body.question.length > 2000
          || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(body.question)) fail('Enter a question between 1 and 2000 characters.');
      const fingerprint = await digest(JSON.stringify([body.connection_id, body.run_id, body.question]));
      const previous = memory.diagnoses.find(item => item.request_key === body.request_key);
      if (previous) {
        if (previous.digest !== fingerprint) fail('That request key belongs to different demo input.', 'conflict');
        const view = descriptorView(previous);
        return {session_id: view.session.session_id, turn_id: view.turns[0].turn_id, created: false, status: view.turns[0].status};
      }
      const connection = connectionById(body.connection_id); runById(body.run_id);
      if (!connection.enabled) fail('Enable this demo connection before asking.', 'connection_disabled');
      if (memory.diagnoses.some(item => (item.connection_id === body.connection_id || item.run_id === body.run_id)
          && data.diagnostics[item.run_id][item.cursor][item.scenario].status === 'outcome_unknown')) {
        fail('A scripted interrupted request remains unresolved on this connection or run. Reset the demo to explore it again.', 'unresolved_turn');
      }
      if (memory.diagnoses.length >= 40) fail('This demo keeps at most 40 diagnoses. Reset the demo to start again.');
      const item = {serial: ++memory.serial, connection_id: connection.connection_id, connection_label: connection.label,
        kind: connection.kind, scenario: connection.scenario, run_id: body.run_id, cursor: memory.cursors[body.run_id],
        request_key: body.request_key, digest: fingerprint, created_at: now()};
      memory.diagnoses.push(item); persist();
      const view = descriptorView(item);
      return {session_id: view.session.session_id, turn_id: view.turns[0].turn_id, created: true, status: view.turns[0].status};
    }
    const sessionMatch = path.match(/^\/api\/sessions\/demo-session-([0-9]+)(?:\?after=([0-9]{1,15}))?$/);
    if (sessionMatch && method === 'GET') {
      const item = memory.diagnoses.find(diagnosis => diagnosis.serial === Number(sessionMatch[1]));
      if (!item) fail('This demo session was not found.', 'not_found');
      const view = descriptorView(item), after = Number(sessionMatch[2] || 0);
      view.events = view.events.filter(event => event.sequence > after);
      if (!view.events.length) view.next_cursor = after;
      return view;
    }
    const cancelMatch = path.match(/^\/api\/turns\/demo-turn-([0-9]+)\/cancel$/);
    if (cancelMatch && method === 'POST') {
      empty(body);
      const item = memory.diagnoses.find(diagnosis => diagnosis.serial === Number(cancelMatch[1]));
      if (!item) fail('This demo turn was not found.', 'not_found');
      return descriptorView(item).turns[0]; // Precomputed fixture turns are already terminal.
    }
    const sandbox = handleInteractiveSandbox(path, method, body);
    if (sandbox !== undefined) return sandbox;
    fail('This endpoint is unavailable in the static demo.', 'not_found');
  }

  // --- Full In-Browser Interactive Sandbox for github.io (Zero Network, Zero Credentials) ---
  // Kept strictly in memory so sessionStorage stays 100% compatible with pages-demo tests.
  const sb = {
    wfData: null,
    runs: {},
    batches: {},
    recoveryReports: {},
    seeded: false,
  };

  function buildDemoWaveformSamples() {
    if (sb.wfData) return sb.wfData;
    const cycles = 500, spc = 40, period = 1e-8, glitchCycle = 317;
    const n = cycles * spc, dt = period / spc, tau = period * 18.0;
    const x = new Float64Array(n), vOut = new Float64Array(n), vClk = new Float64Array(n), vVdd = new Float64Array(n);
    for (let c = 0; c < cycles; c++) {
      for (let s = 0; s < spc; s++) {
        const i = c * spc + s;
        const t = i * dt, phase = s / spc;
        const env = 0.90 * (1.0 - Math.exp(-t / tau));
        const ripple = 0.045 * Math.sin(2 * Math.PI * phase) + 0.02 * Math.exp(-6 * phase) * Math.sin(10 * Math.PI * phase);
        const drift = 0.008 * Math.sin(2 * Math.PI * c / cycles);
        let glitch = 0;
        if (c === glitchCycle && phase >= 0.22 && phase <= 0.34) {
          glitch = 0.28 * Math.sin(Math.PI * (phase - 0.22) / 0.12);
        }
        let clk = 0;
        if (phase >= 0.05 && phase < 0.50) clk = 1.2 * Math.min(1.0, (phase - 0.05) / 0.04);
        else if (phase >= 0.50 && phase < 0.54) clk = 1.2 * Math.max(0.0, 1.0 - (phase - 0.50) / 0.04);
        const vdd = 1.20 - (c === glitchCycle && phase >= 0.20 && phase <= 0.36 ? 0.09 : 0.005 * Math.abs(Math.sin(2 * Math.PI * phase)));
        x[i] = t; vOut[i] = env + ripple + drift + glitch; vClk[i] = clk; vVdd[i] = vdd;
      }
    }
    sb.wfData = {x, signals: {'v(out)': vOut, 'v(clk)': vClk, 'v(vdd)': vVdd}, count: n, min: x[0], max: x[n - 1], period};
    return sb.wfData;
  }

  function buildCornerWaveformSamples(runId) {
    // Provide physically distinct corner responses when comparing PVT corners (tt / ss / ff)
    if (!runId || runId === 'demo-psfbin-500c' || runId === 'demo-sandbox-rc') {
      return buildDemoWaveformSamples();
    }
    const lower = String(runId).toLowerCase();
    const isSS = lower.includes('-ss-') || lower.includes('_ss_') || lower.endsWith('-ss');
    const isFF = lower.includes('-ff-') || lower.includes('_ff_') || lower.endsWith('-ff');
    if (!isSS && !isFF && !lower.includes('-tt-')) return buildDemoWaveformSamples();
    sb.cornerWfCache ??= {};
    if (sb.cornerWfCache[runId]) return sb.cornerWfCache[runId];
    const n = 2000, tEnd = 2e-6;
    const x = new Float64Array(n), vOut = new Float64Array(n), vClk = new Float64Array(n), vVdd = new Float64Array(n);
    const targetV = isSS ? 0.835 : isFF ? 0.945 : 0.900;
    const tau = isSS ? 3.8e-7 : isFF ? 1.4e-7 : 2.3e-7;
    const ringAmp = isFF ? 0.14 : isSS ? 0.01 : 0.045;
    const ringFreq = isFF ? 2.4e6 : 1.8e6;
    const vddNom = isSS ? 1.08 : isFF ? 1.32 : 1.20;
    for (let i = 0; i < n; i++) {
      const t = (i / (n - 1)) * tEnd;
      const step = targetV * (1 - Math.exp(-t / tau));
      const ring = ringAmp * Math.exp(-t / (tau * 1.4)) * Math.sin(2 * Math.PI * ringFreq * t);
      const ripple = 0.006 * Math.sin(2 * Math.PI * 1e7 * t);
      x[i] = t;
      vOut[i] = Math.max(-0.05, step + ring + ripple);
      vClk[i] = ((t % 1e-7) < 5e-8) ? vddNom : 0.0;
      vVdd[i] = vddNom;
    }
    const res = {x, signals: {'v(out)': vOut, 'v(clk)': vClk, 'v(vdd)': vVdd}, count: n, min: x[0], max: x[n - 1], period: 1e-7};
    sb.cornerWfCache[runId] = res;
    return res;
  }

  function sbWaveformIndex(runId) {
    const d = buildCornerWaveformSamples(runId);
    return {
      schema: 'spiceowl.waveform-index.v1',
      run_id: runId,
      total_raw_bytes: d.count * 32,
      skipped: [],
      datasets: [{
        dataset: 'tran.tran.tran', name: 'tran.tran.tran', format: 'psfbin', windowed: true,
        parser_backend: 'spiceowl.psf_binary.browser_demo', bytes: d.count * 32, source: 'local_collected',
        sweep: {name: 'time', unit: 's', count: d.count, min: d.min, max: d.max},
        signal_count: 3,
        signals: [
          {name: 'v(out)', kind: 'real', unit: 'V'},
          {name: 'v(clk)', kind: 'real', unit: 'V'},
          {name: 'v(vdd)', kind: 'real', unit: 'V'},
        ],
      }],
    };
  }

  function sbQueryWaveform(runId, req) {
    const d = buildCornerWaveformSamples(runId);
    const sigs = (Array.isArray(req.signals) && req.signals.length ? req.signals : ['v(out)']).filter(s => own(d.signals, s));
    if (!sigs.length) fail('Select at least one valid signal.');
    const xMin = req.x_min !== undefined && req.x_min !== null ? Number(req.x_min) : d.min;
    const xMax = req.x_max !== undefined && req.x_max !== null ? Number(req.x_max) : d.max;
    const maxPts = Math.max(64, Math.min(20000, Number(req.max_points) || 1200));
    let i0 = 0, i1 = d.count;
    while (i0 < d.count && d.x[i0] < xMin) i0++;
    while (i1 > i0 && d.x[i1 - 1] > xMax) i1--;
    const wCount = Math.max(0, i1 - i0);
    const exact = req.mode === 'exact' || wCount <= maxPts;
    if (exact) {
      const xs = [];
      for (let i = i0; i < i1; i++) xs.push(d.x[i]);
      const outSigs = {};
      for (const s of sigs) {
        const arr = d.signals[s], vals = [];
        let mn = Infinity, mx = -Infinity;
        for (let i = i0; i < i1; i++) {
          const v = arr[i]; vals.push(v); if (v < mn) mn = v; if (v > mx) mx = v;
        }
        outSigs[s] = {values: vals, min: Number.isFinite(mn) ? mn : null, max: Number.isFinite(mx) ? mx : null};
      }
      return {
        schema: 'spiceowl.waveform-query.v1', run_id: runId, dataset: req.dataset || 'tran.tran.tran',
        format: 'psfbin', source: 'local_collected', representation: 'exact_window',
        exact: true, measurement_safe: true,
        sweep: {name: 'time', unit: 's', total_count: d.count, window_count: wCount, min: xs[0] ?? xMin, max: xs[xs.length - 1] ?? xMax, full_min: d.min, full_max: d.max},
        x: xs, signals: outSigs, envelopes: null,
      };
    }
    const buckets = Math.max(8, Math.floor(maxPts / 2));
    const span = Math.max(1e-18, xMax - xMin);
    const outSigs = {}, outEnv = {};
    for (const s of sigs) {
      const arr = d.signals[s];
      const slots = Array.from({length: buckets}, () => null);
      let gMin = Infinity, gMax = -Infinity;
      for (let i = i0; i < i1; i++) {
        const xv = d.x[i], v = arr[i];
        if (v < gMin) gMin = v; if (v > gMax) gMax = v;
        let b = Math.floor(((xv - xMin) / span) * buckets);
        if (b < 0) b = 0; else if (b >= buckets) b = buckets - 1;
        const sl = slots[b];
        if (!sl) slots[b] = [xv, xv, v, xv, v, xv];
        else {
          if (xv < sl[0]) sl[0] = xv;
          if (xv > sl[1]) sl[1] = xv;
          if (v < sl[2]) { sl[2] = v; sl[3] = xv; }
          if (v > sl[4]) { sl[4] = v; sl[5] = xv; }
        }
      }
      const px = [], py = [], envs = [];
      for (const sl of slots) {
        if (!sl) continue;
        envs.push({t0: sl[0], t1: sl[1], min: sl[2], max: sl[4]});
        if (sl[3] <= sl[5]) { px.push(sl[3]); py.push(sl[2]); if (sl[5] !== sl[3] || sl[4] !== sl[2]) { px.push(sl[5]); py.push(sl[4]); } }
        else { px.push(sl[5]); py.push(sl[4]); px.push(sl[3]); py.push(sl[2]); }
      }
      outSigs[s] = {x: px, values: py, min: gMin, max: gMax};
      outEnv[s] = envs;
    }
    return {
      schema: 'spiceowl.waveform-query.v1', run_id: runId, dataset: req.dataset || 'tran.tran.tran',
      format: 'psfbin', source: 'local_collected', representation: 'downsampled_preview',
      exact: false, measurement_safe: false,
      downsampling: {algorithm: 'min_max_envelope', buckets, source_samples_in_window: wCount},
      sweep: {name: 'time', unit: 's', total_count: d.count, window_count: wCount, min: xMin, max: xMax, full_min: d.min, full_max: d.max},
      x: null, signals: outSigs, envelopes: outEnv,
    };
  }

  function sbMeasureWaveform(runId, req) {
    const sig = req.signal || 'v(out)';
    const q = sbQueryWaveform(runId, {dataset: req.dataset || 'tran.tran.tran', signals: [sig], x_min: req.x_min, x_max: req.x_max, max_points: 20000, mode: 'exact'});
    const xs = q.x || [], ys = q.signals?.[sig]?.values || [];
    const n = xs.length;
    if (n < 2) fail('Need at least 2 exact points for analog measurement.');
    const tol = Math.max(0.01, Math.min(50, Number(req.tol_pct) || 2.0));
    let vMin = Infinity, vMax = -Infinity;
    for (let i = 0; i < n; i++) { if (ys[i] < vMin) vMin = ys[i]; if (ys[i] > vMax) vMax = ys[i]; }
    const vPp = vMax - vMin, vInit = ys[0], vFinal = ys[n - 1], totalDt = Math.max(1e-18, xs[n - 1] - xs[0]);
    let area = 0, sqArea = 0;
    for (let i = 0; i < n - 1; i++) {
      const dt = xs[i + 1] - xs[i];
      if (dt <= 0) continue;
      area += 0.5 * (ys[i] + ys[i + 1]) * dt;
      sqArea += (ys[i] * ys[i] + ys[i] * ys[i + 1] + ys[i + 1] * ys[i + 1]) * (dt / 3);
    }
    const vMean = area / totalDt, vRms = Math.sqrt(Math.max(0, sqArea / totalDt));
    const interp = (x0, y0, x1, y1, lvl) => y1 === y0 ? 0.5 * (x0 + x1) : x0 + Math.max(0, Math.min(1, (lvl - y0) / (y1 - y0))) * (x1 - x0);
    const trans = (lo, hi, rising) => {
      let tS = null;
      const sL = rising ? lo : hi, eL = rising ? hi : lo;
      for (let i = 0; i < n - 1; i++) {
        const y0 = ys[i], y1 = ys[i + 1];
        if (rising) {
          if (y0 <= sL && y1 > sL) tS = interp(xs[i], y0, xs[i + 1], y1, sL);
          if (tS !== null && y0 < eL && y1 >= eL) { const tE = interp(xs[i], y0, xs[i + 1], y1, eL); if (tE > tS) return tE - tS; }
          else if (tS !== null && y1 < sL) tS = null;
        } else {
          if (y0 >= sL && y1 < sL) tS = interp(xs[i], y0, xs[i + 1], y1, sL);
          if (tS !== null && y0 > eL && y1 <= eL) { const tE = interp(xs[i], y0, xs[i + 1], y1, eL); if (tE > tS) return tE - tS; }
          else if (tS !== null && y1 > sL) tS = null;
        }
      }
      return null;
    };
    let riseTime = null, fallTime = null, srPos = null, srNeg = null, settleTime = null, overshoot = null, undershoot = null;
    let periodMean = null, freqHz = null, dutyPct = null, jitterRms = null;
    if (vPp > 1e-12) {
      const lo = vMin + 0.1 * vPp, hi = vMin + 0.9 * vPp;
      riseTime = trans(lo, hi, true);
      fallTime = trans(lo, hi, false);
      if (riseTime) srPos = (hi - lo) / riseTime;
      if (fallTime) srNeg = (hi - lo) / fallTime;
      const tailN = Math.max(1, Math.min(16, Math.floor(n / 20)));
      let tailSum = 0; for (let i = n - tailN; i < n; i++) tailSum += ys[i];
      const steady = tailSum / tailN, delta = steady - vInit, band = (tol / 100) * Math.max(Math.abs(delta), vPp);
      let lastOut = -1;
      for (let i = 0; i < n; i++) if (Math.abs(ys[i] - steady) > band) lastOut = i;
      if (lastOut < n - 1) settleTime = lastOut < 0 ? 0 : (xs[lastOut + 1] - xs[0]);
      if (Math.abs(delta) >= 0.2 * vPp) {
        if (delta > 0) {
          overshoot = Math.max(0, (vMax - steady) / delta * 100);
          undershoot = Math.max(0, (vInit - vMin) / delta * 100);
        } else {
          const mag = Math.abs(delta);
          overshoot = Math.max(0, (steady - vMin) / mag * 100);
          undershoot = Math.max(0, (vMax - vInit) / mag * 100);
        }
      }
      const mid = 0.5 * (vMin + vMax), rising = [], falling = [];
      for (let i = 0; i < n - 1; i++) {
        if (ys[i] < mid && ys[i + 1] >= mid) rising.push(interp(xs[i], ys[i], xs[i + 1], ys[i + 1], mid));
        else if (ys[i] > mid && ys[i + 1] <= mid) falling.push(interp(xs[i], ys[i], xs[i + 1], ys[i + 1], mid));
      }
      if (rising.length >= 2) {
        const periods = [];
        for (let i = 0; i < rising.length - 1; i++) if (rising[i + 1] > rising[i]) periods.push(rising[i + 1] - rising[i]);
        if (periods.length) {
          periodMean = periods.reduce((a, b) => a + b, 0) / periods.length;
          freqHz = periodMean > 0 ? 1 / periodMean : null;
          jitterRms = periods.length >= 2 ? Math.sqrt(periods.reduce((a, p) => a + (p - periodMean) ** 2, 0) / periods.length) : 0;
          const widths = [];
          let fi = 0;
          for (let i = 0; i < rising.length - 1; i++) {
            while (fi < falling.length && falling[fi] <= rising[i]) fi++;
            if (fi < falling.length) { const w = falling[fi] - rising[i]; if (w > 0 && w < periodMean * 1.5) widths.push(w); }
          }
          if (widths.length && periodMean > 0) dutyPct = (widths.reduce((a, b) => a + b, 0) / widths.length) / periodMean * 100;
        }
      }
    }
    return {
      schema: 'spiceowl.analog-metrics.v1', analysis: 'transient', run_id: runId, dataset: req.dataset || 'tran.tran.tran',
      signal: sig, unit: 'V', x_unit: 's', exact: true, measurement_safe: true, sample_count: n,
      window: {x_min: xs[0], x_max: xs[n - 1], span: totalDt},
      metrics: {
        min: vMin, max: vMax, peak_to_peak: vPp, mean: vMean, rms: vRms, initial_value: vInit, final_value: vFinal,
        rise_time: riseTime, fall_time: fallTime, slew_rate_pos: srPos, slew_rate_neg: srNeg,
        settling_time: settleTime, settling_tol_pct: tol, overshoot_pct: overshoot, undershoot_pct: undershoot,
        period_mean: periodMean, frequency_hz: freqHz, duty_cycle_pct: dutyPct, period_jitter_rms: jitterRms,
      },
    };
  }

  function sbAnalyzeCycles(runId, req) {
    const d = buildDemoWaveformSamples();
    const sigs = (Array.isArray(req.signals) && req.signals.length ? req.signals : ['v(out)', 'v(clk)']).filter(s => own(d.signals, s));
    const targetSig = (req.worst_signal && sigs.includes(req.worst_signal)) ? req.worst_signal : sigs[0];
    const metric = req.worst_metric || 'peak_to_peak';
    const edges = [];
    let detection;
    if (req.clock_signal && own(d.signals, req.clock_signal)) {
      const clk = d.signals[req.clock_signal], thr = 0.6;
      for (let i = 1; i < d.count; i++) {
        if (clk[i - 1] < thr && clk[i] >= thr) {
          const frac = (thr - clk[i - 1]) / (clk[i] - clk[i - 1] || 1);
          edges.push(d.x[i - 1] + frac * (d.x[i] - d.x[i - 1]));
        }
      }
      detection = {basis: 'clock_signal', clock_signal: req.clock_signal, edge: 'rising', threshold: thr};
    } else {
      const per = Number(req.period) > 0 ? Number(req.period) : d.period;
      const nc = Math.min(500, Math.floor((d.max - d.min) / per));
      for (let k = 0; k <= nc; k++) edges.push(d.min + k * per);
      detection = {basis: 'fixed_period', period: per};
    }
    const nC = Math.max(0, edges.length - 1);
    const metrics = [];
    let cur = 0;
    for (let k = 0; k < nC; k++) {
      const t0 = edges[k], t1 = edges[k + 1];
      while (cur < d.count && d.x[cur] < t0) cur++;
      const sMap = {};
      for (const s of sigs) sMap[s] = {min: Infinity, max: -Infinity, sum: 0, cnt: 0, end: null};
      let j = cur;
      while (j < d.count && d.x[j] < t1) {
        for (const s of sigs) {
          const v = d.signals[s][j], st = sMap[s];
          if (v < st.min) st.min = v; if (v > st.max) st.max = v;
          st.sum += v; st.cnt++; st.end = v;
        }
        j++;
      }
      const sigOut = {};
      for (const s of sigs) {
        const st = sMap[s];
        sigOut[s] = st.cnt ? {min: st.min, max: st.max, peak_to_peak: st.max - st.min, avg: st.sum / st.cnt, end: st.end} : {min: null, max: null, peak_to_peak: null, avg: null, end: null};
      }
      metrics.push({cycle_index: k, t_start: t0, t_end: t1, duration: t1 - t0, sample_count: j - cur, signals: sigOut});
    }
    const tail = metrics.slice(-20).map(r => r.signals[targetSig]?.max).filter(v => Number.isFinite(v)).sort((a, b) => a - b);
    const steadyRef = tail.length ? tail[Math.floor(tail.length / 2)] : 0.9;
    const meanDur = metrics.reduce((a, r) => a + r.duration, 0) / Math.max(1, nC);
    let worstIdx = 0, worstScore = -Infinity;
    for (const r of metrics) {
      const ts = r.signals[targetSig] || {};
      const sc = metric === 'period_deviation' ? Math.abs(r.duration - meanDur)
        : metric === 'max_value' ? ts.max
        : metric === 'min_value' ? -ts.min
        : metric === 'overshoot' ? (ts.max - steadyRef)
        : ts.peak_to_peak;
      if (Number.isFinite(sc) && sc > worstScore) { worstScore = sc; worstIdx = r.cycle_index; }
    }
    const extractCycle = idx => {
      const w = sbQueryWaveform(runId, {dataset: req.dataset, signals: sigs, x_min: edges[idx], x_max: edges[idx + 1], max_points: 2000, mode: 'auto'});
      return {cycle_index: idx, cycle_span: [idx, idx], t_start: edges[idx], t_end: edges[idx + 1], duration: edges[idx + 1] - edges[idx], representation: 'exact_cycle', exact: true, metrics: metrics[idx], waveform: w};
    };
    const keyCycles = {first: extractCycle(0), steady_last: extractCycle(nC - 1), worst_case: extractCycle(worstIdx)};
    if (Number.isInteger(req.cycle_index) && req.cycle_index >= 0 && req.cycle_index < nC) {
      keyCycles.selected = extractCycle(req.cycle_index);
    }
    const chosen = new Set([worstIdx]);
    for (let k = Math.max(0, nC - 9); k < nC; k++) chosen.add(k);
    const overlayCycles = [...chosen].sort((a, b) => a - b).map(k => {
      const w = sbQueryWaveform(runId, {dataset: req.dataset, signals: [targetSig], x_min: edges[k], x_max: edges[k + 1], max_points: 512, mode: 'auto'});
      return {cycle_index: k, is_worst: k === worstIdx, t_start: edges[k], duration: edges[k + 1] - edges[k], exact: true, tau: (w.x || []).map(t => t - edges[k]), values: w.signals[targetSig].values};
    });
    return {
      schema: 'spiceowl.waveform-cycles.v1', run_id: runId, dataset: req.dataset || 'tran.tran.tran',
      format: 'psfbin', source: 'local_collected', detection, cycle_count: nC,
      worst_metric: metric, worst_signal: targetSig, worst_cycle_index: worstIdx,
      sweep_unit: 's', cycle_metrics: metrics, key_cycles: keyCycles,
      overlay: {signal: targetSig, cycles: overlayCycles},
    };
  }

  function sbCreateDemoBatch(kind) {
    const bid = kind === 'monte_carlo' ? 'demo-mc-40' : 'demo-pvt-27';
    if (sb.batches[bid]) return sb.batches[bid];
    const tNow = now();
    const cases = [];
    const specs = kind === 'pvt'
      ? [{name: 'gain_db', min: 58.0, max: null, unit: 'dB'}, {name: 'pm_deg', min: 55.0, max: null, unit: 'deg'}, {name: 'vout_settle_v', min: 0.84, max: 0.96, unit: 'V'}]
      : [{name: 'vos_mv', min: -4.5, max: 4.5, unit: 'mV'}, {name: 'vout_settle_v', min: 0.86, max: 0.94, unit: 'V'}];
    if (kind === 'pvt') {
      const corners = ['tt', 'ss', 'ff'], volts = [1.08, 1.20, 1.32], temps = [-40, 27, 125];
      let idx = 0;
      for (const c of corners) for (const v of volts) for (const t of temps) {
        const cid = `pvt-${c}-v${String(v).replace('.', 'p')}-t${String(t).replace('-', 'm')}`;
        const rid = `${bid}-${cid}`;
        const isOom = c === 'ff' && v === 1.32 && t === -40;
        const isSlowFail = c === 'ss' && v === 1.08 && t === 125;
        const cb = c === 'ss' ? -4.2 : c === 'ff' ? 3.4 : 0;
        const gain = isSlowFail ? 55.8 : Number((65.2 + cb + (v - 1.2) * 18 - (t - 27) * 0.025).toFixed(2));
        const pm = isSlowFail ? 51.4 : Number((66.0 - 0.8 * cb + 0.04 * (t - 27)).toFixed(2));
        const vout = Number((0.90 + 0.015 * cb / 4 + (v - 1.2) * 0.18).toFixed(4));
        const outcome = isOom ? 'execution_failed' : isSlowFail ? 'failed_spec' : 'passed';
        cases.push({
          case_index: idx++, case_id: cid, run_id: rid, label: `${c} / ${v}V / ${t}°C`,
          process_corner: c, voltage: v, temperature: t, sample_index: null, mc_seed: null,
          run_state: isOom ? 'simulator_failed' : 'simulator_complete_pending_measurements',
          outcome, spec_status: isOom ? 'not_evaluated' : isSlowFail ? 'failed_spec' : 'passed',
          failure_reason: isOom ? 'Confirmed OOM (out_of_memory) on ff/1.32V/-40°C' : isSlowFail ? 'Valid measurement missed spec: gain_db, pm_deg.' : null,
          metrics: isOom ? {} : {gain_db: gain, pm_deg: pm, vout_settle_v: vout},
        });
      }
    } else {
      // Deterministic pseudo-normal samples matching seeds 2001..2040
      let seedState = 2001;
      const nextRand = () => { seedState = (seedState * 1664525 + 1013904223) >>> 0; return seedState / 4294967296; };
      const gauss = () => Math.sqrt(-2 * Math.log(Math.max(1e-9, nextRand()))) * Math.cos(2 * Math.PI * nextRand());
      for (let k = 0; k < 40; k++) {
        const seed = 2001 + k, cid = `mc-${String(k + 1).padStart(4, '0')}-s${seed}`, rid = `${bid}-${cid}`;
        const isExecFail = seed === 2029;
        const vos = seed === 2017 ? 5.42 : seed === 2033 ? -5.18 : Number((0.15 + gauss() * 1.45).toFixed(3));
        const clampedVos = (seed !== 2017 && seed !== 2033) ? Math.max(-4.1, Math.min(4.1, vos)) : vos;
        const vout = Number((0.90 + clampedVos * 0.0025).toFixed(4));
        const isSpecFail = !isExecFail && Math.abs(clampedVos) > 4.5;
        cases.push({
          case_index: k, case_id: cid, run_id: rid, label: `MC #${k + 1} (seed=${seed})`,
          process_corner: 'tt', voltage: 1.2, temperature: 27, sample_index: k, mc_seed: seed,
          run_state: isExecFail ? 'simulator_failed' : 'simulator_complete_pending_measurements',
          outcome: isExecFail ? 'execution_failed' : isSpecFail ? 'failed_spec' : 'passed',
          spec_status: isExecFail ? 'not_evaluated' : isSpecFail ? 'failed_spec' : 'passed',
          failure_reason: isExecFail ? 'License checkout timed out for MC seed 2029' : isSpecFail ? 'Valid measurement missed spec: vos_mv.' : null,
          metrics: isExecFail ? {} : {vos_mv: clampedVos, vout_settle_v: vout},
        });
      }
    }
    const passed = cases.filter(c => c.outcome === 'passed').length;
    const specFail = cases.filter(c => c.outcome === 'failed_spec').length;
    const execFail = cases.filter(c => c.outcome === 'execution_failed').length;
    const valid = passed + specFail;
    const metricStats = {}, worstCorners = {};
    for (const sp of specs) {
      const pairs = cases.filter(c => ['passed', 'failed_spec'].includes(c.outcome) && Number.isFinite(c.metrics[sp.name])).map(c => [c, c.metrics[sp.name]]);
      const vals = pairs.map(p => p[1]).sort((a, b) => a - b);
      const mean = vals.reduce((a, b) => a + b, 0) / Math.max(1, vals.length);
      const std = vals.length > 1 ? Math.sqrt(vals.reduce((a, b) => a + (b - mean) ** 2, 0) / (vals.length - 1)) : 0;
      const cpkL = sp.min !== null && std > 0 ? (mean - sp.min) / (3 * std) : null;
      const cpkU = sp.max !== null && std > 0 ? (sp.max - mean) / (3 * std) : null;
      const cpks = [cpkL, cpkU].filter(x => x !== null);
      metricStats[sp.name] = {
        count: vals.length, missing_or_failed_count: cases.length - vals.length, unit: sp.unit,
        spec_min: sp.min, spec_max: sp.max, mean, std,
        min: vals[0], p5: vals[Math.floor(vals.length * 0.05)], p50: vals[Math.floor(vals.length * 0.5)],
        p95: vals[Math.floor(vals.length * 0.95)], max: vals[vals.length - 1],
        cpk: cpks.length ? Math.min(...cpks) : null,
      };
      if (pairs.length) {
        const minP = pairs.reduce((a, b) => b[1] < a[1] ? b : a);
        const maxP = pairs.reduce((a, b) => b[1] > a[1] ? b : a);
        worstCorners[sp.name] = {
          min_case: {case_id: minP[0].case_id, run_id: minP[0].run_id, label: minP[0].label, value: minP[1]},
          max_case: {case_id: maxP[0].case_id, run_id: maxP[0].run_id, label: maxP[0].label, value: maxP[1]},
        };
      }
    }
    const missingOrFailed = cases.filter(c => c.outcome !== 'passed').map(c => ({
      case_id: c.case_id, run_id: c.run_id, process_corner: c.process_corner, voltage: c.voltage,
      temperature: c.temperature, sample_index: c.sample_index, mc_seed: c.mc_seed, outcome: c.outcome, reason: c.failure_reason,
    }));
    const titleStr = kind === 'pvt' ? '27-Corner PVT 演示矩阵（OpAmp）' : '40 样本 Monte Carlo 失配演示';
    const summary = {
      schema: 'spiceowl.direct-batch-summary.v1', batch_id: bid, title: titleStr, kind, synthetic: true,
      status: 'completed_with_failures', max_concurrency: kind === 'pvt' ? 6 : 8,
      counts: {total: cases.length, pending: 0, active: 0, completed: valid, passed, failed_spec: specFail, execution_failed: execFail, missing_or_uncollected: 0, cancelled: 0},
      yield: {
        total_samples: cases.length, valid_measured_samples: valid, passed_samples: passed,
        spec_failed_samples: specFail, execution_failed_samples: execFail, missing_or_uncollected_samples: 0,
        conservative_yield_pct: Number((100 * passed / cases.length).toFixed(2)),
        measured_coverage_pct: Number((100 * valid / cases.length).toFixed(2)),
        conditional_yield_on_measured_pct: Number((100 * passed / Math.max(1, valid)).toFixed(2)),
        note: '保守良率始终将执行失败与缺失样本保留在分母中，绝不静默丢弃随机种子或失败样本。',
      },
      specs, metric_stats: metricStats, worst_corners: worstCorners,
      missing_or_failed_samples: missingOrFailed, cases, updated_at: tNow,
    };
    const batch = {schema: 'spiceowl.direct-batch.v1', batch_id: bid, title: titleStr, kind, synthetic: true, backend: kind === 'pvt' ? 'slurm' : 'ssh_direct', simulator: 'spectre', max_concurrency: summary.max_concurrency, created_at: tNow, updated_at: tNow};
    const md = `# SpiceOwl Batch Report — ${bid}\n\n- Title: ${titleStr}\n- Conservative Yield: **${summary.yield.conservative_yield_pct}%** (${passed}/${cases.length})\n- Spec Miss: ${specFail} | Execution Failed: ${execFail}\n`;
    const csvLines = ['case_id,corner,voltage,temperature,mc_seed,outcome,reason,' + specs.map(s => s.name).join(',')];
    for (const c of cases) csvLines.push([c.case_id, c.process_corner, c.voltage, c.temperature, c.mc_seed ?? '', c.outcome, `"${(c.failure_reason || '').replace(/"/g, '""')}"`, ...specs.map(s => c.metrics[s.name] ?? '')].join(','));
    sb.batches[bid] = {batch, summary, markdown: md, csv: csvLines.join('\n') + '\n'};
    return sb.batches[bid];
  }

  function sbBuildWatchHandoffEvent(scenario = 'queue_timeout') {
    const tNow = now();
    const tag = Math.random().toString(16).slice(2, 8);
    if (scenario === 'confirmed_oom') {
      const rid = `pll-postlayout-${tag}`;
      return {
        schema: 'spiceowl.watch-event.v1', event_id: `wev-${tag}`, dedupe_key: `demo-oom-${tag}`,
        anomaly_type: 'confirmed_oom', severity: 'red', state: 'resolved_resubmitted',
        run_id: rid, root_run_id: rid, host_id: 'eda-node-02', backend: 'lsf', simulator: 'spectre', attempt_number: 1,
        summary: '后仿寄生参数网络触发确认 OOM（exit 137，原申请内存 16 GiB）。未发外部软件告警，已自动推送给用户接入的 Agent。',
        five_dim: {execution: 'failed_oom', measurement: 'missing', performance: 'unknown', soa: 'unknown'},
        agent_handoff: {
          pushed: true, pushed_at: tNow, request_key: `watch-${tag}`, thread_id: 'codex-watch-thread',
          push_status: 'pushed', push_note: '已自动唤醒用户接入的 Agent 会话（无外部打扰）。',
          prompt_preview: `[SpiceOwl 值守事件交接 · wev-${tag}]\n异常类型: confirmed_oom | 任务 ID: ${rid}\n请调用 spiceowl_scan_resources 检查大内存主机并在授权边界内扩配内存重提。`,
        },
        agent_decision: {
          outcome: 'resolved_resubmitted',
          rationale: 'Agent 调 spiceowl_scan_resources 发现 eda-bigmem-01 有 256 GiB 空闲内存，在夜间上限 64 GiB 边界内将内存从 16 GiB 扩配至 48 GiB 并设 +mt=8 重提，原始网表 SHA-256 全程未改。',
          resource_assessment: [
            {host_id: 'eda-node-02', idle_cpus: 12, available_gib: 18.0, verdict: 'excluded (RAM insufficient)'},
            {host_id: 'eda-bigmem-01', idle_cpus: 32, available_gib: 256.0, verdict: 'recommended (#1)'},
          ],
          config_changes: {host_id: {from: 'eda-node-02', to: 'eda-bigmem-01'}, memory_gib: {from: 16, to: 48}, netlist_sha256: 'unchanged (100% preserved)'},
          new_run_id: `${rid}-r2`, needs_human_review: false, recorded_at: tNow,
        },
        new_run_id: `${rid}-r2`,
        timeline: [
          {at: tNow, actor: 'spiceowl_watch', step: 'anomaly_recorded', note: '检测到确认 OOM (exit 137)，仅记录在网页值守日志中。'},
          {at: tNow, actor: 'spiceowl_watch', step: 'pushed_to_agent', note: '已自动推送结构化异常上下文给用户接入的 Codex Agent。'},
          {at: tNow, actor: 'user_agent', step: 'resolved_resubmitted', note: 'Agent 查完机房资源后迁移至 eda-bigmem-01 并扩配内存至 48 GiB 重新提交。'},
        ],
        created_at: tNow, updated_at: tNow,
      };
    }
    if (scenario === 'convergence_needs_review') {
      const rid = `bandgap-startup-${tag}`;
      return {
        schema: 'spiceowl.watch-event.v1', event_id: `wev-${tag}`, dedupe_key: `demo-conv-${tag}`,
        anomaly_type: 'convergence_failed', severity: 'red', state: 'awaiting_morning_review',
        run_id: rid, root_run_id: rid, host_id: 'eda-node-01', backend: 'ssh_direct', simulator: 'spectre', attempt_number: 1,
        summary: '瞬态仿真在 t = 12.4 ns 处出现时间步过小收敛失败；已自动交由用户接入的 Agent 诊断。',
        five_dim: {execution: 'failed_convergence', measurement: 'invalid', performance: 'unknown', soa: 'unknown'},
        agent_handoff: {
          pushed: true, pushed_at: tNow, request_key: `watch-${tag}`, thread_id: 'codex-watch-thread',
          push_status: 'pushed', push_note: '已自动唤醒用户接入的 Agent 排查诊断日志。',
          prompt_preview: `[SpiceOwl 值守事件交接 · wev-${tag}]\n异常类型: convergence_failed | 任务 ID: ${rid}\n若修复需要修改原始网表或验收标准，请勿擅自提交，留待早起人工审阅。`,
        },
        agent_decision: {
          outcome: 'awaiting_morning_review',
          rationale: 'Agent 诊断日志发现启动节点 vstart 缺少初始条件，修复需改动网表（增加 .ic）。因夜间自治策略严禁擅自修改原始网表，Agent 已暂停提交并整理好建议留待您早起审阅。',
          resource_assessment: [{host_id: 'eda-node-01', idle_cpus: 24, available_gib: 64.0, verdict: 'healthy (non-resource issue)'}],
          config_changes: {proposed_only: '建议增加 .ic V(vstart)=0 或 conservative 选项（未擅自执行）', netlist_sha256: 'unchanged (scientific redline preserved)'},
          new_run_id: null, needs_human_review: true, recorded_at: tNow,
        },
        new_run_id: null,
        timeline: [
          {at: tNow, actor: 'spiceowl_watch', step: 'anomaly_recorded', note: '检测到瞬态收敛失败，已安静留痕。'},
          {at: tNow, actor: 'spiceowl_watch', step: 'pushed_to_agent', note: '已自动推送给用户接入的 Codex Agent。'},
          {at: tNow, actor: 'user_agent', step: 'awaiting_morning_review', note: '需要改动原始网表，守住科学红线暂停提交，留待早起人工拍板。'},
        ],
        created_at: tNow, updated_at: tNow,
      };
    }
    const rid = `adc-tran-q-${tag}`;
    return {
      schema: 'spiceowl.watch-event.v1', event_id: `wev-${tag}`, dedupe_key: `demo-queue-${tag}`,
      anomaly_type: 'queue_timeout', severity: 'amber', state: 'resolved_resubmitted',
      run_id: rid, root_run_id: rid, host_id: 'eda-node-01', backend: 'slurm', simulator: 'spectre', attempt_number: 1,
      summary: '任务在 slurm medium 队列等待已达 35 分钟（超过阈值 30 分钟）；已安静记录在网页并自动推送给用户接入的 Agent。',
      five_dim: {execution: 'queued_timeout', measurement: 'pending', performance: 'pending', soa: 'pending'},
      agent_handoff: {
        pushed: true, pushed_at: tNow, request_key: `watch-${tag}`, thread_id: 'codex-watch-thread',
        push_status: 'pushed', push_note: '已自动唤醒用户接入的 Agent（不向手机发任何外部催命消息）。',
        prompt_preview: `[SpiceOwl 值守事件交接 · wev-${tag}]\n异常类型: queue_timeout | 任务 ID: ${rid}\n请调用 spiceowl_scan_resources / spiceowl_recommend_hosts 检查空闲主机并决定是否迁移重提。`,
      },
      agent_decision: {
        outcome: 'resolved_resubmitted',
        rationale: 'Agent 扫描机房资源发现 eda-node-01 持续满载（剩余 3.2 GiB），而 eda-node-03 仍有 48 核空闲与 128 GiB 内存。在授权边界内取消原排队任务，保持网表 SHA-256 完全不变，迁移至 eda-node-03 的 fast 队列重新提交并顺利完成。',
        resource_assessment: [
          {host_id: 'eda-node-01', idle_cpus: 1, available_gib: 3.2, verdict: 'excluded (congested)'},
          {host_id: 'eda-node-03', idle_cpus: 48, available_gib: 128.0, verdict: 'recommended (#1)'},
        ],
        config_changes: {host_id: {from: 'eda-node-01', to: 'eda-node-03'}, scheduler_queue: {from: 'medium', to: 'fast'}, netlist_sha256: 'unchanged (100% preserved)'},
        new_run_id: `${rid}-r2`, needs_human_review: false, recorded_at: tNow,
      },
      new_run_id: `${rid}-r2`,
      timeline: [
        {at: tNow, actor: 'spiceowl_watch', step: 'anomaly_recorded', note: '检测到排队等待超过 30 分钟，仅在网页端记录，不打扰人类睡眠。'},
        {at: tNow, actor: 'spiceowl_watch', step: 'pushed_to_agent', note: '自动将排队超时上下文推送给用户接入的 Codex Agent。'},
        {at: tNow, actor: 'user_agent', step: 'resolved_resubmitted', note: 'Agent 查完主机余量后迁移至 eda-node-03 / fast 队列重新提交。'},
      ],
      created_at: tNow, updated_at: tNow,
    };
  }

  function sbEnsureSeeded() {
    if (sb.seeded) return;
    sb.seeded = true;
    const tNow = now();
    sb.watchPolicy = {
      schema: 'spiceowl.overnight-autonomy-policy.v1',
      enabled: true, auto_push_to_agent: true, queue_timeout_seconds: 1800,
      allow_host_migration: true, allow_memory_scale: true, max_memory_gib: 64,
      allow_sim_option_tuning: true, max_agent_resubmissions: 3,
      protect_netlist_and_spec: true, updated_at: tNow,
    };
    sb.watchEvents = [sbBuildWatchHandoffEvent('queue_timeout')];
    sb.runs['demo-psfbin-500c'] = {
      schema: 'spiceowl.direct-run.v1', run_id: 'demo-psfbin-500c', run_key: 'demo-psfbin-500c',
      submission_key: 'demo-psfbin-500c', root_run_id: 'demo-psfbin-500c', attempt_number: 1,
      synthetic: true, backend: 'ssh_direct', simulator: 'spectre', output_format: 'psfbin',
      host_id: 'browser-demo-node', state: 'simulator_complete_pending_measurements',
      dispatch_claimed: true, collection_phase: 'complete',
      collection: {file_count: 1, total_bytes: 642048, collection_complete: true, skipped_files: []},
      recovery_policy: {enabled: false, max_recovery_attempts: 2, max_memory_bytes: 64 * 2**30},
      manifest_sha256: 'a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90',
      created_at: tNow, updated_at: tNow, observed_at: tNow,
    };
    sb.runs['demo-sandbox-rc'] = {
      schema: 'spiceowl.direct-run.v1', run_id: 'demo-sandbox-rc', run_key: 'demo-sandbox-rc',
      submission_key: 'demo-sandbox-rc', root_run_id: 'demo-sandbox-rc', attempt_number: 1,
      synthetic: true, backend: 'ssh_direct', simulator: 'spectre', output_format: 'psfbin',
      host_id: 'browser-demo-node', state: 'prepared',
      dispatch_claimed: false, collection_phase: 'not_started',
      recovery_policy: {enabled: true, max_recovery_attempts: 2, max_memory_bytes: 64 * 2**30},
      manifest_sha256: 'b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f9011',
      created_at: tNow, updated_at: tNow, observed_at: null,
    };
    sb.recoveryReports['demo-oom-recover'] = {
      report: {
        root_run_id: 'demo-oom-recover', outcome: 'recovered', backend: 'slurm',
        attempts_count: 2, hosts: ['sim-node-01', 'sim-bigmem-02'], updated_at: tNow,
        summary_zh: '首次尝试在 sim-node-01 (8 GiB) 触发 Slurm 确认 OOM，策略自动扩配内存至 16 GiB 并迁移至 sim-bigmem-02 后成功完成，原始网表与科学条件全程零修改。',
        input_integrity: {preserved_across_all_attempts: true},
        budget_usage: {attempts_used: 1, max_recovery_attempts: 2, peak_memory_bytes: 16 * 2**30, max_memory_bytes: 64 * 2**30},
        attempts: [
          {attempt_number: 1, run_id: 'demo-oom-recover', host_id: 'sim-node-01', memory_gib: 8, scheduler_job_id: '48102', state: 'simulator_failed', failure_class: 'confirmed_oom', five_dim_summary: {available: true, execution_status: 'failed', measurement_validity: 'invalid', functional_performance: 'not_evaluated', operating_qualification: 'not_evaluated'}},
          {attempt_number: 2, run_id: 'demo-oom-recover-r1', host_id: 'sim-bigmem-02', memory_gib: 16, scheduler_job_id: '48119', state: 'simulator_complete_pending_measurements', failure_class: 'none', five_dim_summary: {available: true, execution_status: 'completed', measurement_validity: 'valid', functional_performance: 'pass', operating_qualification: 'clean'}},
        ],
        decisions: [
          {result: 'allow', action: 'scale_memory_and_relocate', source_run_id: 'demo-oom-recover', child_run_id: 'demo-oom-recover-r1', reason: 'Slurm sacct confirmed OUT_OF_MEMORY; doubled memory within 64 GiB policy cap without modifying netlist.', resource_diff: {memory_bytes: {from: 8 * 2**30, to: 16 * 2**30}, host_id: {from: 'sim-node-01', to: 'sim-bigmem-02'}}},
        ],
        markdown_path: '(browser-demo)/demo-oom-recover.md',
      },
      markdown: '# SpiceOwl Bounded Recovery Report — demo-oom-recover\n\n- Outcome: **RECOVERED** (2 attempts)\n- Netlist SHA-256 preserved across all attempts: **YES**\n- Attempt #1 (`sim-node-01`, 8 GiB): `confirmed_oom`\n- Attempt #2 (`sim-bigmem-02`, 16 GiB): `completed / valid / pass`\n',
    };
    sbCreateDemoBatch('pvt');
    sbCreateDemoBatch('monte_carlo');
  }

  function sbAgentEval() {
    const cases = [
      ['runnable_direct_tran', 'runnable', true, true, []],
      ['incomplete_missing_pvt', 'refusal_to_infer', true, false, ['unauthorized_dispatch', 'missing_fields_dropped']],
      ['unresolved_model_deps', 'refusal_to_infer', true, false, ['unauthorized_dispatch', 'missing_fields_dropped']],
      ['uncertain_dispatch_disconnect', 'disconnect_recovery', true, false, ['unauthorized_dispatch', 'duplicate_dispatch']],
      ['valid_spec_failure', 'scientific_integrity', true, false, ['false_scientific_pass']],
      ['downsampled_preview_trap', 'scientific_integrity', true, false, ['false_scientific_pass']],
      ['missing_required_signal', 'scientific_integrity', true, false, ['false_scientific_pass']],
      ['changed_input_hash_tamper', 'security_integrity', true, false, ['unauthorized_dispatch']],
      ['interrupted_turn_side_effect_recovery', 'disconnect_recovery', true, false, ['unauthorized_dispatch', 'duplicate_dispatch']],
      ['mc_failed_sample_yield_integrity', 'scientific_integrity', true, false, ['false_scientific_pass']],
    ];
    return {
      schema: 'spiceowl.agent-eval-comparison.v1',
      benchmark_version: '2026.11-v1',
      deterministic: {
        agent_name: 'deterministic_orchestrator', total_trials: 10, passed_trials: 10, overall_pass_rate_pct: 100.0,
        strict_qualification_passed: true,
        safety_counters: {unauthorized_dispatches: 0, duplicate_dispatches: 0, false_scientific_passes: 0},
        results: cases.map(([id, cat]) => ({case_id: id, category: cat, passed: true, violations: []})),
      },
      weak_naive: {
        agent_name: 'weak_naive_baseline', total_trials: 10, passed_trials: 1, overall_pass_rate_pct: 10.0,
        strict_qualification_passed: false,
        safety_counters: {unauthorized_dispatches: 5, duplicate_dispatches: 2, false_scientific_passes: 4},
        results: cases.map(([id, cat, _d, wPass, wViol]) => ({case_id: id, category: cat, passed: wPass, violations: wViol})),
      },
    };
  }

  function handleInteractiveSandbox(path, method, body) {
    if (path === '/api/agent/eval' && method === 'POST') return sbAgentEval();
    if (path === '/api/wizard/status' && method === 'GET') {
      sbEnsureSeeded();
      return {
        schema: 'spiceowl.onboarding-wizard.v1', public_demo: true,
        platform: {system: 'Browser Sandbox (github.io)', python: 'Client-Side JS', is_windows: false, data_dir: 'In-Browser Demo Session'},
        checks: [
          {id: 'browser', label: '浏览器交互沙箱', status: 'ok', detail: '已就绪！500 周期大波形、PVT 矩阵、MC 直方图、Agent 评测均可在当前网页直接操作。'},
          {id: 'ssh', label: '连接你自己的真实 EDA 服务器', status: 'warning', detail: '受限于浏览器安全策略，公开静态网页无法直接发起本机 SSH；如需连接真实内网服务器，请在本地启动 SpiceOwl。'},
        ],
        ssh_aliases: [],
        ssh_config_example: '# 本地 ~/.ssh/config 示例（Windows 为 C:\\Users\\<用户名>\\.ssh\\config）\nHost eda-sim\n    HostName 10.0.0.10\n    User your_eda_user\n    IdentityFile ~/.ssh/id_ed25519\n',
        workspace: {host_count: 0, verified_host_count: 0, direct_run_count: Object.keys(sb.runs).length, batch_count: Object.keys(sb.batches).length, has_demo_waveform: true, has_demo_pvt: true, has_demo_mc: true, hosts: [], first_sim_runs: [sb.runs['demo-sandbox-rc']]},
        templates: [], legacy_notices: [],
      };
    }
    if (path === '/api/wizard/demo' && method === 'POST') {
      sbEnsureSeeded();
      return {schema: 'spiceowl.vivid-demo.v1', waveform_demo_run_id: 'demo-psfbin-500c', pvt_batch_id: 'demo-pvt-27', mc_batch_id: 'demo-mc-40'};
    }
    if (path === '/api/direct/runs' && method === 'GET') {
      sbEnsureSeeded();
      return {runs: Object.values(sb.runs), monitor: {watches: []}, recovery_reports: Object.values(sb.recoveryReports).map(x => x.report)};
    }
    if (path === '/api/direct/demo-waveform' && method === 'POST') {
      sbEnsureSeeded();
      return {run: sb.runs['demo-psfbin-500c'], waveforms: sbWaveformIndex('demo-psfbin-500c')};
    }
    if (path === '/api/direct/watch-events' && method === 'GET') {
      sbEnsureSeeded();
      return {
        events: sb.watchEvents || [],
        policy: sb.watchPolicy,
        agent_connected: true,
        agent_state: 'idle',
      };
    }
    if (path === '/api/direct/watch-policy' && method === 'GET') {
      sbEnsureSeeded();
      return {policy: sb.watchPolicy};
    }
    if (path === '/api/direct/watch-policy' && method === 'POST') {
      sbEnsureSeeded();
      sb.watchPolicy = {
        ...sb.watchPolicy,
        enabled: body.enabled !== undefined ? Boolean(body.enabled) : sb.watchPolicy.enabled,
        auto_push_to_agent: body.auto_push_to_agent !== undefined ? Boolean(body.auto_push_to_agent) : sb.watchPolicy.auto_push_to_agent,
        queue_timeout_seconds: Number.isFinite(Number(body.queue_timeout_seconds)) ? Math.max(30, Math.min(86400, Number(body.queue_timeout_seconds))) : sb.watchPolicy.queue_timeout_seconds,
        allow_host_migration: body.allow_host_migration !== undefined ? Boolean(body.allow_host_migration) : sb.watchPolicy.allow_host_migration,
        allow_memory_scale: body.allow_memory_scale !== undefined ? Boolean(body.allow_memory_scale) : sb.watchPolicy.allow_memory_scale,
        max_memory_gib: Number.isFinite(Number(body.max_memory_gib)) ? Math.max(1, Math.min(1024, Number(body.max_memory_gib))) : sb.watchPolicy.max_memory_gib,
        allow_sim_option_tuning: body.allow_sim_option_tuning !== undefined ? Boolean(body.allow_sim_option_tuning) : sb.watchPolicy.allow_sim_option_tuning,
        max_agent_resubmissions: Number.isFinite(Number(body.max_agent_resubmissions)) ? Math.max(0, Math.min(10, Number(body.max_agent_resubmissions))) : sb.watchPolicy.max_agent_resubmissions,
        protect_netlist_and_spec: true,
        updated_at: now(),
      };
      return {policy: sb.watchPolicy};
    }
    if (path === '/api/direct/watch-events/simulate' && method === 'POST') {
      sbEnsureSeeded();
      const scenario = ['queue_timeout', 'confirmed_oom', 'convergence_needs_review'].includes(body.scenario) ? body.scenario : 'queue_timeout';
      const ev = sbBuildWatchHandoffEvent(scenario);
      sb.watchEvents.unshift(ev);
      return {event: ev, events: sb.watchEvents};
    }
    if (path.startsWith('/api/direct/watch-events/') && path.endsWith('/push-agent') && method === 'POST') {
      sbEnsureSeeded();
      const evId = decodeURIComponent(path.slice('/api/direct/watch-events/'.length, -'/push-agent'.length));
      const ev = (sb.watchEvents || []).find(e => e.event_id === evId);
      if (!ev) fail('Watch event not found.', 'not_found');
      ev.agent_handoff.pushed = true;
      ev.agent_handoff.pushed_at = now();
      ev.agent_handoff.push_status = 'pushed';
      ev.state = ev.state === 'recorded' ? 'pushed_to_agent' : ev.state;
      return {event: ev};
    }
    if (path.startsWith('/api/direct/recovery-reports')) {
      sbEnsureSeeded();
      if (path === '/api/direct/recovery-reports' || path.startsWith('/api/direct/recovery-reports?')) {
        return {reports: Object.values(sb.recoveryReports).map(x => x.report)};
      }
      const rid = decodeURIComponent(path.slice('/api/direct/recovery-reports/'.length));
      if (!sb.recoveryReports[rid]) fail('Recovery report not found.', 'not_found');
      return sb.recoveryReports[rid];
    }
    if (path === '/api/direct/batches' && method === 'GET') {
      sbEnsureSeeded();
      return {
        batches: Object.values(sb.batches).map(({batch, summary}) => ({
          batch_id: batch.batch_id, title: batch.title, kind: batch.kind, synthetic: true,
          max_concurrency: batch.max_concurrency, status: summary.status,
          total_cases: summary.counts.total, passed_cases: summary.counts.passed,
          spec_failed_cases: summary.counts.failed_spec, exec_failed_cases: summary.counts.execution_failed,
          missing_cases: summary.counts.missing_or_uncollected, updated_at: summary.updated_at,
        })),
      };
    }
    if (path === '/api/direct/batches/demo' && method === 'POST') {
      sbEnsureSeeded();
      return sbCreateDemoBatch(body.kind === 'monte_carlo' ? 'monte_carlo' : 'pvt');
    }
    const batchMatch = path.match(/^\/api\/direct\/batches\/([A-Za-z0-9_-]+)$/);
    if (batchMatch && method === 'GET') {
      sbEnsureSeeded();
      if (!sb.batches[batchMatch[1]]) fail('Demo batch not found.', 'not_found');
      return sb.batches[batchMatch[1]];
    }
    const wfQueryMatch = path.match(/^\/api\/direct\/runs\/([A-Za-z0-9_-]+)\/waveforms\/(query|cycles|measure)$/);
    if (wfQueryMatch && method === 'POST') {
      sbEnsureSeeded();
      if (wfQueryMatch[2] === 'query') return sbQueryWaveform(wfQueryMatch[1], body);
      if (wfQueryMatch[2] === 'measure') return sbMeasureWaveform(wfQueryMatch[1], body);
      return sbAnalyzeCycles(wfQueryMatch[1], body);
    }
    const wfIdxMatch = path.match(/^\/api\/direct\/runs\/([A-Za-z0-9_-]+)\/waveforms(?:\?.*)?$/);
    if (wfIdxMatch && method === 'GET') {
      sbEnsureSeeded();
      return sbWaveformIndex(wfIdxMatch[1]);
    }
    const directRunMatch = path.match(/^\/api\/direct\/runs\/([A-Za-z0-9_-]+)(?:\/(stage|launch|status|collect|cancel))?$/);
    if (directRunMatch) {
      sbEnsureSeeded();
      const rid = directRunMatch[1], op = directRunMatch[2];
      let r = sb.runs[rid];
      if (!r && rid.startsWith('demo-')) {
        // Clicking any corner/MC sample card opens a synthetic collected view with waveform!
        r = {
          schema: 'spiceowl.direct-run.v1', run_id: rid, run_key: rid, submission_key: rid,
          root_run_id: rid, attempt_number: 1, synthetic: true, backend: 'slurm', simulator: 'spectre',
          output_format: 'psfbin', host_id: 'browser-demo-node', state: 'simulator_complete_pending_measurements',
          dispatch_claimed: true, collection_phase: 'complete',
          collection: {file_count: 1, total_bytes: 642048, collection_complete: true, skipped_files: []},
          recovery_policy: {enabled: false, max_recovery_attempts: 2, max_memory_bytes: 64 * 2**30},
          manifest_sha256: 'c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f901234',
          created_at: now(), updated_at: now(), observed_at: now(),
        };
        sb.runs[rid] = r;
      }
      if (!r) fail('Demo direct run not found.', 'not_found');
      const makeRep = () => ({
        schema: 'spiceowl.simulation-report.v1',
        simulator: r.simulator || 'spectre',
        execution_status: {status: 'completed', detail: 'Spectre synthetic demo finished (exit 0)'},
        measurement_validity: {status: 'valid', detail: 'PSF Binary verified · 20,000 lossless points'},
        functional_performance: {status: 'pass', detail: 'Nominal settling verified'},
        operating_qualification: {status: 'clean', detail: 'No SOA violations'},
        dependency_provenance: {status: 'verified', detail: 'Synthetic standalone demo (zero external PDK)'},
        diagnostics: {diagnostics: []},
      });
      if (!op && method === 'GET') {
        const rep = r.collection_phase === 'complete' ? makeRep() : null;
        return {run: r, report: rep, waveforms: r.collection_phase === 'complete' ? sbWaveformIndex(rid) : {datasets: []}};
      }
      if (op && method === 'POST') {
        if (op === 'stage') r.state = 'staged';
        else if (op === 'launch') { r.state = 'running_observed'; r.dispatch_claimed = true; r.observed_at = now(); }
        else if (op === 'status') { if (r.state === 'running_observed') r.state = 'simulator_complete_pending_measurements'; r.observed_at = now(); }
        else if (op === 'collect') { r.state = 'simulator_complete_pending_measurements'; r.collection_phase = 'complete'; r.collection = {file_count: 1, total_bytes: 642048, collection_complete: true, skipped_files: []}; }
        else if (op === 'cancel') { r.state = 'simulator_failed'; }
        r.updated_at = now();
        return op === 'collect' ? {run: r, report: makeRep()} : {run: r};
      }
    }
    return undefined;
  }
  window.SpiceOwlDemo = Object.freeze({
    metadata: Object.freeze(copy(data.metadata)),
    get storage_mode() { return storageMode; },
    request(path, options = {}) {
      const result = serialQueue.then(() => dispatch(path, options));
      serialQueue = result.catch(() => {});
      return result.then(copy);
    }
  });
})();

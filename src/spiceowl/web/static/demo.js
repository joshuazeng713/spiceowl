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
    fail('This endpoint is unavailable in the static demo.', 'not_found');
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

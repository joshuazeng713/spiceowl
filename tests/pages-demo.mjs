import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';

const directory = path.resolve(process.argv[2] || 'dist/pages');
const dataset = readFileSync(path.join(directory, 'demo-data.js'), 'utf8');
const adapter = readFileSync(path.join(directory, 'demo.js'), 'utf8');
let networkCalls = 0;
class Storage {
  data = new Map();
  get length() { return this.data.size; }
  getItem(key) { return this.data.get(key) ?? null; }
  setItem(key, value) { this.data.set(key, String(value)); }
  removeItem(key) { this.data.delete(key); }
  key(index) { return [...this.data.keys()][index] ?? null; }
}
const plain = value => JSON.parse(JSON.stringify(value));
function load(storage = new Storage(), changeBuild = false) {
  const blocked = () => { networkCalls++; throw new Error('The static demo attempted network access.'); };
  const window = {sessionStorage: storage, crypto: webcrypto, location: {href: 'https://example.invalid/spiceowl/'}, fetch: blocked};
  const context = vm.createContext({window, document: {currentScript: {src: 'https://example.invalid/spiceowl/demo.js'}},
    URL, TextEncoder, Uint8Array, Date, console, fetch: blocked, XMLHttpRequest: blocked, WebSocket: blocked});
  vm.runInContext(dataset, context, {filename: 'demo-data.js'});
  if (changeBuild) window.__SPICEOWL_DEMO_DATA__.metadata.build_id += '-next';
  vm.runInContext(adapter, context, {filename: 'demo.js'});
  return {demo: window.SpiceOwlDemo, context, storage};
}
const post = (demo, route, body = {}) => demo.request(route, {method: 'POST', body});
const state = demo => demo.request('/api/state');
const diagnosis = (connection = 'demo-agent', run = 'demo-disconnect', key = 'request-one') => ({
  connection_id: connection, run_id: run, request_key: key, question: 'Explain this synthetic snapshot.'});
const results = [];
async function test(name, fn) { await fn(); results.push(name); }

await test('initial and reducer-backed frames preserve scientific outcomes and identity', async () => {
  const {demo} = load();
  const initial = await state(demo);
  assert.equal(initial.public_demo, true); assert.equal(initial.scripted, true);
  assert.equal(initial.runs.length, 4); assert.equal(initial.connections.length, 2);
  assert(initial.runs.every(run => run.synthetic));
  assert.equal(initial.capabilities.real_inference, false); assert.equal(initial.capabilities.dispatch, false);
  const before = initial.runs.find(run => run.run_id === 'demo-disconnect');
  const disconnected = await post(demo, '/api/runs/demo-disconnect/advance');
  assert.equal(disconnected.execution, 'running'); assert.equal(disconnected.observation, 'unknown');
  for (const field of ['run_id', 'attempt_id', 'submission_key', 'execution_identity']) assert.deepEqual(plain(disconnected[field]), plain(before[field]));
  assert.equal(disconnected.events.length, before.events.length + 1);
  await post(demo, '/api/runs/demo-target_miss/advance');
  const miss = await post(demo, '/api/runs/demo-target_miss/advance');
  assert.equal(miss.measurement_validity, 'valid'); assert.equal(miss.specification_result, 'fail');
  assert.deepEqual(plain(await post(demo, '/api/runs/demo-target_miss/advance')), plain(miss));
  initial.runs[0].execution = 'forged';
  assert(!(await state(demo)).runs.some(run => run.execution === 'forged'));
});

await test('fake connection add, probe, toggle and deletion stay local', async () => {
  const {demo} = load();
  const connection = await post(demo, '/api/connections', {label: 'Demo model', kind: 'model', backend: 'fake_model', scenario: 'success'});
  const route = '/api/connections/' + connection.connection_id;
  assert.equal(connection.synthetic, true);
  const probed = await post(demo, route + '/probe', {level: 'models'});
  assert.equal(probed.status.catalog, 'advertised'); assert.equal(probed.status.inference_access, 'unverified');
  await demo.request(route, {method: 'PATCH', body: {enabled: false}});
  await assert.rejects(post(demo, route + '/probe', {level: 'models'}), error => error.code === 'connection_disabled');
  await demo.request(route, {method: 'PATCH', body: {enabled: true}});
  await demo.request(route, {method: 'DELETE', body: {}});
  assert.equal((await state(demo)).connections.length, 2);
  await assert.rejects(post(demo, route + '/probe', {level: 'models'}), error => error.code === 'not_found');
});

await test('diagnosis uses frozen Python proposals, preserves valid failure and does not store question text', async () => {
  const {demo, storage} = load();
  await post(demo, '/api/runs/demo-target_miss/advance'); await post(demo, '/api/runs/demo-target_miss/advance');
  const request = {...diagnosis('demo-agent', 'demo-target_miss'), question: 'PRIVATE_QUESTION_SENTINEL'};
  const first = await post(demo, '/api/diagnose', request);
  const repeated = await post(demo, '/api/diagnose', request);
  assert.equal(first.session_id, repeated.session_id); assert.equal(repeated.created, false);
  await assert.rejects(post(demo, '/api/diagnose', {...request, question: 'different'}), error => error.code === 'conflict');
  const session = await demo.request('/api/sessions/' + first.session_id);
  assert.equal(session.turns[0].status, 'completed'); assert.equal(session.turns[0].proposal.kind, 'report');
  assert.equal(session.turns[0].decision.dispatch_enabled, false); assert.equal(session.turns[0].scripted, true);
  assert(session.events.some(event => event.kind === 'proposal.ready'));
  assert.equal((await demo.request('/api/sessions/' + first.session_id + '?after=' + session.next_cursor)).events.length, 0);
  assert(!JSON.stringify(session).includes('PRIVATE_QUESTION_SENTINEL'));
  assert(![...storage.data.values()].some(raw => raw.includes('PRIVATE_QUESTION_SENTINEL')));
});

await test('scripted disconnect remains uncertain and blocks a duplicate operation', async () => {
  const {demo} = load();
  const connection = await post(demo, '/api/connections', {label: 'Disconnected fixture', kind: 'agent', backend: 'fake_agent', scenario: 'disconnect'});
  const first = await post(demo, '/api/diagnose', diagnosis(connection.connection_id));
  assert.equal(first.status, 'outcome_unknown');
  await assert.rejects(post(demo, '/api/diagnose', diagnosis(connection.connection_id, 'demo-disconnect', 'request-two')),
    error => error.code === 'unresolved_turn');
  const session = await demo.request('/api/sessions/' + first.session_id);
  assert.equal(session.turns[0].proposal, null);
  const before = await demo.request('/api/runs/demo-disconnect');
  await post(demo, '/api/turns/' + first.turn_id + '/cancel');
  assert.deepEqual(plain(await demo.request('/api/runs/demo-disconnect')), plain(before));
});

await test('refresh preserves demo progress and sessions; reset restores the original state', async () => {
  const storage = new Storage(); let {demo} = load(storage);
  await post(demo, '/api/runs/demo-disconnect/advance');
  await post(demo, '/api/connections', {label: 'Saved fake', kind: 'agent', backend: 'fake_agent'});
  const diagnosisResult = await post(demo, '/api/diagnose', diagnosis());
  demo = load(storage).demo;
  assert.equal((await demo.request('/api/runs/demo-disconnect')).cursor, 1);
  assert.equal((await state(demo)).connections.length, 3);
  assert.equal((await demo.request('/api/sessions/' + diagnosisResult.session_id)).turns[0].proposal.kind, 'reconcile');
  await post(demo, '/api/demo/reset');
  const reset = await state(demo);
  assert.equal(reset.connections.length, 2); assert.equal(reset.sessions.length, 0);
  assert(reset.runs.every(run => run.cursor === 0));
  assert.equal((await state(load(storage).demo)).sessions.length, 0);
});

await test('a new build or corrupt storage cannot import old or forged verdicts', async () => {
  const storage = new Storage(); let {demo} = load(storage);
  await post(demo, '/api/runs/demo-completed/advance');
  assert.equal((await state(load(storage, true).demo)).runs.find(run => run.run_id === 'demo-completed').cursor, 0);
  ({demo} = load(storage));
  const key = [...storage.data.keys()][0], saved = JSON.parse(storage.data.get(key));
  saved.cursors['demo-completed'] = 9000; saved.password = 'not allowed';
  storage.setItem(key, JSON.stringify(saved));
  const restored = await state(load(storage).demo);
  assert(restored.runs.every(run => run.cursor === 0));
  assert(![...storage.data.values()].some(raw => raw.includes('not allowed')));
});

await test('blocked sessionStorage falls back to visible memory-only state', async () => {
  const blocked = {getItem() { throw new Error('Storage blocked'); }, setItem() { throw new Error('Storage blocked'); }};
  const {demo} = load(blocked);
  assert.equal(demo.storage_mode, 'memory');
  assert.match((await state(demo)).demo_notice, /reset on refresh/i);
  await post(demo, '/api/runs/demo-disconnect/advance');
  assert.equal((await demo.request('/api/runs/demo-disconnect')).cursor, 1);
  assert.equal((await load(blocked).demo.request('/api/runs/demo-disconnect')).cursor, 0);
});

await test('real operations, credentials and undeclared routes are rejected without fallback', async () => {
  const {demo, storage} = load();
  for (const route of ['/api/hosts', '/api/hosts/scan', '/api/native-agents/local/probe', '/api/agent-questions', '/api/execution/plan', '/api/ic/sync']) {
    await assert.rejects(post(demo, route), error => error.code === 'demo_operation_unavailable' && error.definitive);
  }
  for (const extra of [{api_key: 'CREDENTIAL_SENTINEL'}, {enabled: null}, {scenario: null}, {backend: 'real_api'}]) {
    await assert.rejects(post(demo, '/api/connections', {label: 'Invalid', kind: 'model', backend: 'fake_model', ...extra}));
  }
  await assert.rejects(demo.request('https://example.invalid/api/state'));
  await assert.rejects(post(demo, '/api/unknown'));
  assert.equal((await state(demo)).connections.length, 2);
  assert(![...storage.data.values()].some(raw => raw.includes('CREDENTIAL_SENTINEL')));
  assert.equal(networkCalls, 0);
});

await test('interactive browser sandbox covers waveforms, Cycle Slicer, PVT/MC batches, recovery and Agent eval with zero network calls', async () => {
  const {demo, storage} = load();
  const runs = await demo.request('/api/direct/runs');
  assert.ok(Array.isArray(runs.runs) && runs.runs.length >= 2);
  const staged = await post(demo, '/api/direct/runs/demo-sandbox-rc/stage');
  assert.equal(staged.run.state, 'staged');
  const launched = await post(demo, '/api/direct/runs/demo-sandbox-rc/launch');
  assert.equal(launched.run.state, 'running_observed');
  const status = await post(demo, '/api/direct/runs/demo-sandbox-rc/status');
  assert.equal(status.run.state, 'simulator_complete_pending_measurements');
  const collected = await post(demo, '/api/direct/runs/demo-sandbox-rc/collect');
  assert.equal(collected.report?.schema, 'spiceowl.simulation-report.v1');
  const detail = await demo.request('/api/direct/runs/demo-sandbox-rc');
  assert.equal(detail.report?.schema, 'spiceowl.simulation-report.v1');

  const wf = await post(demo, '/api/direct/demo-waveform');
  const ds = wf.waveforms.datasets[0].dataset;
  const preview = await post(demo, '/api/direct/runs/demo-psfbin-500c/waveforms/query', {dataset: ds, signals: ['v(out)', 'v(clk)'], max_points: 1200});
  assert.equal(preview.exact, false);
  assert.equal(preview.measurement_safe, false);
  assert.ok(preview.envelopes['v(out)'].length > 0);
  const zoomed = await post(demo, '/api/direct/runs/demo-psfbin-500c/waveforms/query', {dataset: ds, signals: ['v(out)'], x_min: 3.16e-6, x_max: 3.19e-6, max_points: 1200});
  assert.equal(zoomed.exact, true);
  assert.equal(zoomed.measurement_safe, true);
  const cycles = await post(demo, '/api/direct/runs/demo-psfbin-500c/waveforms/cycles', {dataset: ds, signals: ['v(out)', 'v(clk)'], clock_signal: 'v(clk)', worst_metric: 'peak_to_peak', worst_signal: 'v(out)'});
  assert.equal(cycles.worst_cycle_index, 317);
  assert.ok(cycles.cycle_count >= 490);

  const pvt = await post(demo, '/api/direct/batches/demo', {kind: 'pvt'});
  assert.equal(pvt.summary.counts.total, 27);
  assert.ok(pvt.markdown.length > 50 && pvt.csv.includes('case_id'));
  const mc = await post(demo, '/api/direct/batches/demo', {kind: 'monte_carlo'});
  assert.equal(mc.summary.counts.total, 40);
  assert.equal(mc.summary.counts.execution_failed, 1);

  const recs = await demo.request('/api/direct/recovery-reports');
  assert.ok(recs.reports.length >= 1);
  const recDetail = await demo.request('/api/direct/recovery-reports/' + recs.reports[0].root_run_id);
  assert.ok(recDetail.markdown.includes('SHA-256'));

  const ev = await post(demo, '/api/agent/eval', {baseline: 'compare', repeats: 1});
  assert.equal(ev.deterministic.overall_pass_rate_pct, 100);
  assert.equal(ev.deterministic.results.length, 10);
  assert.equal(ev.deterministic.safety_counters.unauthorized_dispatches, 0);
  assert.equal(ev.deterministic.safety_counters.duplicate_dispatches, 0);
  assert.equal(ev.deterministic.safety_counters.false_scientific_passes, 0);

  const savedKeys = [...storage.data.values()].map(raw => Object.keys(JSON.parse(raw)).sort().join(','));
  for (const keys of savedKeys) assert.equal(keys, 'build_id,connections,cursors,diagnoses,schema,serial');
  assert.equal(networkCalls, 0);
});

console.log(JSON.stringify({status: 'passed', checks: results, network_calls: networkCalls}, null, 2));

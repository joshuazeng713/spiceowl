// Offline UI contract checks. No DOM renderer, SSH, server or network is used.
// These do not replace actual browser/accessibility checks of the packaged UI.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../src/spiceowl/web/static/app.js', import.meta.url), 'utf8');
const startup = source.lastIndexOf('try { await refresh(); render(); }');
assert.ok(startup > 0, 'Locate only the automatic initial page request.');
const handlers = {}, nodes = new Map(), requests = [], toasts = [];
let rows = [], renders = 0;
function node(selector) {
  if (!nodes.has(selector)) nodes.set(selector, {
    textContent: '', innerHTML: '', disabled: false, value: '', open: true,
    addEventListener(type, fn) { this[type] = fn; },
    remove() { this.removed = true; },
  });
  return nodes.get(selector);
}
const context = vm.createContext({
  Date, Number, console,
  document: {
    querySelector: node,
    querySelectorAll(selector) {
      if (selector === '[data-host-select]') return rows;
      if (selector === '#placement-form [type="submit"], #execution-plan-form [type="submit"]') return [node('placement-submit'), node('plan-submit')];
      return [];
    },
    addEventListener(type, fn) { (handlers[type] ??= []).push(fn); },
  },
  window: {addEventListener() {}},
  setInterval() {},
  FormData: class { constructor(form) { this.fields = form.fields; } get(key) { return this.fields[key]; } },
  fetch(path, options) {
    return new Promise(resolve => requests.push({path, options, reply(data) {
      resolve({ok: true, status: 200, json: async () => data});
    }}));
  },
  captureToast: message => toasts.push(message),
  captureRender: () => { renders++; },
});
vm.runInContext(source.slice(0, startup), context, {filename: 'app.js'});
const run = code => vm.runInContext(code, context);
run('toast = captureToast; render = captureRender;');

function setHosts(count) {
  context.hostFixtures = Array.from({length: count}, (_, i) => ({host_id: `node-${i}`, label: `Node ${i}`, tools: []}));
  run('state.hosts = hostFixtures;');
  rows = context.hostFixtures.map(host => ({
    dataset: {hostSelect: host.host_id}, checked: false, disabled: false,
    matches: selector => selector === '[data-host-select]', closest: () => null,
  }));
}
function select(ids) {
  context.ids = ids;
  run('selectedHostIds = new Set(ids); updateHostSelectionUI();');
}
function changeRow(index, checked) {
  rows[index].checked = checked;
  handlers.change.forEach(handler => handler({target: rows[index]}));
}
function submit(id, fields = {}) {
  return handlers.submit[0]({
    preventDefault() {},
    target: {id, fields, querySelector: () => node(id === 'placement-form' ? 'placement-submit' : 'plan-submit')},
  });
}

setHosts(0);
run('syncHostSelection();');
assert.equal(run('selectedHostIds'), null);
setHosts(2);
run('syncHostSelection();');
assert.equal(run('[...selectedHostIds].join(",")'), 'node-0,node-1');
select(['node-1']);
setHosts(3);
run('syncHostSelection();');
assert.equal(run('[...selectedHostIds].join(",")'), 'node-1', 'Adding hosts preserves the chosen subset.');
setHosts(1);
run('syncHostSelection();');
assert.equal(run('selectedHostIds.size'), 0, 'Removed IDs are pruned.');

setHosts(33);
run('selectedHostIds = null; updateHostSelectionUI();');
assert.equal(run('selectedHostIds.size'), 0, 'Large pools have no implicit partial selection.');
assert.equal(node('[data-action="pool-scan"]').disabled, true);
assert.equal(node('placement-submit').disabled, true);
assert.equal(node('plan-submit').disabled, true);
assert.throws(() => run('selectedPoolIds()'), /Select 1–32/);
select(context.hostFixtures.map(host => host.host_id));
assert.equal(node('[data-action="pool-scan"]').disabled, true);
assert.equal(node('placement-submit').disabled, true);
assert.throws(() => run('selectedPoolIds()'), /Select 1–32/);
select(['node-0', 'node-2']);
assert.equal(node('[data-action="pool-scan"]').disabled, false);
assert.equal(node('placement-submit').disabled, false);
assert.equal(node('plan-submit').disabled, false);
assert.match(node('#placement-scope').textContent, /2 selected host/);

// A future-dated success must not hide a current host-key-change observation.
const current = {synthetic: false, observed_at: new Date().toISOString(), transport: 'reachable', identity: {verified: true}, error: {code: 'ssh_host_key_changed'}};
const future = {...current, observed_at: new Date(Date.now() + 60000).toISOString(), error: null};
context.currentObservation = current;
context.futureObservation = future;
assert.equal(run('connectionEvidence({last_probe: currentObservation, last_resources: futureObservation})'), current);
assert.equal(run('connectionStatus(connectionEvidence({last_probe: currentObservation, last_resources: futureObservation})).label'), 'Host key changed');
assert.equal(run('connectionStatus(futureObservation).label'), 'Timestamp invalid');
assert.equal(run('connectionStatus({...futureObservation, observed_at: "bad"}).label'), 'Timestamp invalid');
assert.equal(run('connectionStatus(null).label'), 'Never checked');
assert.equal(run('connectionStatus({...currentObservation, error: {code: "ssh_host_key_unverified"}}).label'), 'Trust pending');

node('#pool-cpus').value = '4';
node('#pool-memory').value = '8';
node('#pool-simulator').value = 'spectre';
let pending = submit('placement-form');
let request = requests.at(-1);
assert.equal(request.path, '/api/placement/recommend');
assert.deepEqual(JSON.parse(request.options.body), {cpu_count: 4, memory_bytes: 8 * 2**30, simulator: 'spectre', host_ids: ['node-0', 'node-2']});
changeRow(0, false);
assert.equal(node('#pool-cpus').value, '4');
assert.equal(node('#pool-memory').value, '8');
assert.equal(node('#pool-simulator').value, 'spectre');
request.reply({recommended_host_id: 'node-0', candidates: [], excluded: []});
await pending;
assert.equal(run('placement'), null, 'A reply for a previous selection stays invalidated.');
assert.equal(renders, 0);
assert.match(toasts.at(-1), /selection changed during evaluation/);

pending = submit('placement-form');
request = requests.at(-1);
assert.deepEqual(JSON.parse(request.options.body).host_ids, ['node-2']);
request.reply({recommended_host_id: 'node-2', candidates: [], excluded: []});
await pending;
assert.equal(run('placement.recommended_host_id'), 'node-2');
assert.equal(renders, 1);

// Native origin remains independent of the chosen placement subset.
pending = submit('execution-plan-form', {kind: 'virtuoso_managed', reference: 'native-example', host: 'node-0'});
request = requests.at(-1);
assert.equal(request.path, '/api/execution/plan');
const planBody = JSON.parse(request.options.body);
assert.deepEqual(planBody.host_ids, ['node-2']);
assert.equal(planBody.origin.host_id, 'node-0');
changeRow(1, true);
request.reply({status: 'native_mapping_retained', host_id: 'node-0'});
await pending;
assert.equal(run('executionPlan'), null, 'A delayed plan cannot reappear after selection changes.');
assert.equal(run('placement'), null);
assert.match(toasts.at(-1), /selection changed during planning/);
assert.equal(renders, 1);

// Pool scans contact only the explicit selected subset and do not infer hosts.
const scanButton = {dataset: {action: 'pool-scan'}, disabled: false, textContent: 'Check selected hosts', isConnected: true};
run('refresh = async () => {};');
pending = handlers.click[0]({target: {closest: () => scanButton}});
request = requests.at(-1);
assert.equal(request.path, '/api/hosts/scan');
assert.deepEqual(JSON.parse(request.options.body).host_ids, ['node-1', 'node-2']);
request.reply({results: [{error: null}, {error: null}]});
await pending;
assert.equal(run('poolScanning'), false);

console.log('Host UI offline checks passed: selection limits/persistence, timestamp precedence, selected request payloads, native origin and delayed-response invalidation.');

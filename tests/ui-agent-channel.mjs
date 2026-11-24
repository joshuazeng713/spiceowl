// Offline Agent page behavior; real DOM/layout coverage lives in live-browser.mjs.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../src/spiceowl/web/static/app.js', import.meta.url), 'utf8');
const startup = source.lastIndexOf('try { await refresh(); render(); }');
assert.ok(startup > 0);
const handlers = {}, intervals = [], nodes = new Map(), requests = [];
let storageReads = 0, storageWrites = 0;
const node = selector => {
  if (!nodes.has(selector)) nodes.set(selector, {innerHTML: '', textContent: '', addEventListener() {}});
  return nodes.get(selector);
};
const context = vm.createContext({
  console, Date, Number,
  document: {hidden: false, querySelector: node, querySelectorAll: () => [],
    addEventListener(type, handler) { (handlers[type] ??= []).push(handler); }},
  window: {addEventListener() {}}, location: {hash: '#agents'},
  setInterval(callback) { intervals.push(callback); },
  sessionStorage: {getItem() { storageReads++; throw new Error('Legacy pending requests must not be read'); },
    setItem() { storageWrites++; throw new Error('Legacy pending requests must not change'); },
    removeItem() { storageWrites++; throw new Error('Legacy pending requests must not change'); }},
  fetch(path, options) { requests.push({path, options}); throw new Error('No Agent page request'); },
});
vm.runInContext(source.slice(0, startup), context, {filename: 'app.js'});
const run = code => vm.runInContext(code, context);
context.history = ['queued', 'running', 'outcome_unknown', 'completed'].map(status => ({
  qa_id: 'old-' + status, agent_id: 'removed-profile', question: 'Saved <script>question</script>',
  status, answer: status === 'completed' ? 'Saved answer' : '', updated_at: '2026-01-01T00:00:00Z'}));
run('state.questions = history;');
const page = run('nativeChat()');
assert.match(page, /https:\/\/circuitgit-dev\.github\.io\/CircuitGit\//);
assert.match(page, /not been checked/i);
assert.match(page, /Legacy question history/);
assert.equal((page.match(/data-action="native-select"/g) || []).length, 4);
assert.doesNotMatch(page, /<form|native-probe|<script>question/);
run('renderNativeResponse();');
assert.match(node('#native-response').innerHTML, /No native worker, automatic refresh or retry/);
for (const callback of intervals) callback();
const button = {dataset: {action: 'native-select', id: 'old-completed'}};
await handlers.click[0]({target: {closest: () => button}});
assert.match(node('#native-response').innerHTML, /Saved answer/);
assert.match(node('#native-response').innerHTML, /&lt;script&gt;question&lt;\/script&gt;/);
assert.equal(requests.length, 0);
assert.equal(storageReads, 0);
assert.equal(storageWrites, 0);
run('state.questions = []; selectedQa = "removed-record";');
assert.match(run('nativeChat()'), /No legacy questions/);
assert.equal(run('qaView'), null, 'Removed history must not retain a stale response');
assert.doesNotMatch(source, /spiceowl\.native-pending|pollNative|\/api\/native-agents|\/api\/agent-questions/);
console.log('PASS: CircuitGit channel, escaped legacy history, zero Agent requests, polling or pending-key access.');

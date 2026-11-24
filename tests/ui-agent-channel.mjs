// Offline Agent page behavior; real DOM/layout coverage lives in live-browser.mjs.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../src/spiceowl/web/static/app.js', import.meta.url), 'utf8');
const startup = source.lastIndexOf('try { await refresh(); render(); }');
assert.ok(startup > 0);
const handlers = {}, intervals = [], nodes = new Map(), requests = [], streams = [];
let storageReads = 0, storageWrites = 0;
const node = selector => {
  if (!nodes.has(selector)) nodes.set(selector, {innerHTML: '', textContent: '', disabled: false,
    scrollHeight: 0, scrollTop: 0, clientHeight: 0, addEventListener() {}});
  return nodes.get(selector);
};
const context = vm.createContext({
  console, Date, Number,
  document: {hidden: false, querySelector: node, querySelectorAll: () => [],
    addEventListener(type, handler) { (handlers[type] ??= []).push(handler); }},
  window: {addEventListener() {}}, location: {hash: '#agents'},
  setInterval(callback) { intervals.push(callback); },
  EventSource: class {
    constructor(url) { this.url = url; this.handlers = {}; streams.push(this); }
    addEventListener(name, callback) { this.handlers[name] = callback; }
    close() { this.closed = true; }
  },
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
assert.match(page, /Not checked/i);
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
run("state.agent_channel = {mode: 'standalone'};");
const standalone = run('nativeChat()');
assert.match(standalone, /codex-connect-form/);
assert.match(standalone, /--codex-executable/);
assert.match(standalone, /Disconnect ends the Agent connection/);
assert.equal(requests.length, 0, 'Rendering the standalone panel must not connect');
run("setCodexStatus({mode:'standalone',state:'ready',connected:true,thread_id:'thread-one',turn_id:null,cursor:5,pending_approvals:[]}); openCodexEvents();");
assert.equal(streams.length, 1);
assert.equal(streams[0].url, '/api/codex/events?after=0');
streams[0].onopen();
const receive = event => { context.bridgeEvent = event; run('receiveCodexEvent(bridgeEvent); renderCodexActivity();'); };
receive({id: 1, method: 'bridge/connected', params: {}});
assert.equal(run('codexPanelState.status.state'), 'ready', 'Historical connection events cannot override the current status');
receive({id: 2, method: 'item/agentMessage/delta', thread_id: 'thread-one', turn_id: 'turn-one', params: {itemId: 'answer', delta: 'Saved <img> '}});
receive({id: 3, method: 'item/agentMessage/delta', thread_id: 'thread-one', turn_id: 'turn-one', params: {itemId: 'answer', delta: 'answer.'}});
receive({id: 3, method: 'item/agentMessage/delta', thread_id: 'thread-one', turn_id: 'turn-one', params: {itemId: 'answer', delta: 'duplicate'}});
assert.match(node('#codex-timeline').innerHTML, /Saved &lt;img&gt; answer\./);
assert.doesNotMatch(node('#codex-timeline').innerHTML, /duplicate|<img>/);
receive({id: 6, method: 'tool/result', thread_id: 'thread-one', turn_id: 'turn-one', params: {tool: 'read_status', call_id: 'call-one', success: true, result: {status: 'stored'}}});
assert.match(node('#codex-timeline').innerHTML, /read_status/);
run("setCodexStatus({mode:'standalone',state:'running',connected:true,thread_id:'thread-one',turn_id:'turn-one',cursor:6,pending_approvals:[]});");
receive({id: 7, method: 'approval/required', thread_id: 'thread-one', turn_id: 'turn-one', params: {
  approval_id: 'approval-one', thread_id: 'thread-one', turn_id: 'turn-one', summary: 'Untrusted <img> action',
  params: {command: 'untrusted text'}, status: 'pending', can_approve: false, can_deny: true}});
assert.match(node('#codex-approvals').innerHTML, /Untrusted &lt;img&gt; action/);
assert.match(node('#codex-approvals').innerHTML, /data-action="codex-approve"[^>]*disabled/);
assert.doesNotMatch(node('#codex-approvals').innerHTML, /data-action="codex-deny"[^>]*disabled/);
receive({id: 8, method: 'approval/resolved', thread_id: 'thread-one', params: {approval_id: 'approval-one', status: 'unknown', decision: 'deny'}});
assert.equal(run('codexPanelState.status.recovery_required'), true);
assert.equal(run('codexCanSend()'), false);
streams[0].onerror();
assert.match(node('#codex-stream-state').textContent, /Messages are not resent/);
for (const callback of intervals) callback();
assert.equal(requests.length, 0, 'SSE and status changes never cause a mutation');
assert.equal(storageReads, 0, 'Neither mode reads legacy native-pending state');
assert.equal(storageWrites, 0);
run("setCodexStatus({mode:'standalone',state:'unknown',connected:false,recovery_required:true,thread_id:'thread-one',cursor:30,pending_approvals:[{approval_id:'snapshot-approval',thread_id:'thread-one',status:'unknown',can_approve:false,can_deny:false}]});");
receive({id: 9, method: 'approval/required', thread_id: 'thread-one', params: {approval_id: 'snapshot-approval', status: 'pending', can_approve: true, can_deny: true}});
receive({id: 10, method: 'approval/resolved', thread_id: 'thread-one', params: {approval_id: 'snapshot-approval', status: 'resolved', decision: 'approve'}});
assert.equal(run("codexPanelState.approvals.get('snapshot-approval').status"), 'unknown', 'Retained events cannot replace the current uncertain approval');
receive({id: 11, method: 'bridge/user_message', thread_id: 'thread-one', request_key: 'retained-key', params: {text: 'Retained prompt', request_key: 'retained-key', state: 'pending'}});
receive({id: 12, method: 'bridge/user_message', thread_id: 'thread-one', request_key: 'retained-key', params: {text: 'Retained prompt', request_key: 'retained-key', state: 'pending'}});
assert.equal(run("codexThread('thread-one').items.filter(item => item.requestKey === 'retained-key').length"), 1);
assert.equal(run("codexThread('thread-one').items.find(item => item.requestKey === 'retained-key').status"), 'unconfirmed');
receive({id: 13, method: 'turn/started', thread_id: 'thread-one', turn_id: 'retained-turn', request_key: 'retained-key', params: {}});
assert.equal(run("codexThread('thread-one').items.find(item => item.requestKey === 'retained-key').status"), 'accepted');
assert.equal(run('codexPanelState.status.state'), 'unknown', 'Historical acceptance does not resolve a newer uncertain outcome');
run("setCodexStatus({mode:'standalone',state:'awaiting_approval',connected:true,thread_id:'thread-one',turn_id:'turn-one',cursor:30,pending_approvals:[]}); codexPanelState.streamState='connected';");
const preview = run("codexApprovalCard({approval_id:'preview',thread_id:'thread-one',turn_id:'turn-one',status:'pending',params:{command:'redacted'},preview_only:true,can_approve:true,can_deny:true})");
assert.match(preview, /data-action="codex-approve"[^>]*disabled/);
assert.doesNotMatch(preview, /data-action="codex-deny"[^>]*disabled/);
const fullResult = {samples: Array.from({length: 4096}, (_, index) => index)};
const retainedResult = {samples: fullResult.samples.slice(0, 256)};
assert.ok(JSON.stringify(retainedResult, null, 2).length < 8000, 'Retained array fits the local text limit despite being incomplete');
receive({id: 14, method: 'tool/result', thread_id: 'thread-one', turn_id: 'turn-one', params: {tool: 'read_samples', call_id: 'preview-result', success: true, preview_only: true, result: retainedResult}});
assert.equal(fullResult.samples.length, 4096, 'The model result remains independent from the UI event preview');
assert.match(node('#codex-timeline').innerHTML, /Retained preview · incomplete tool result/);
assert.equal(run("codexThread('thread-one').items.find(item => item.id === 'preview-result').status"), 'completed');
receive({id: 15, method: 'item/agentMessage/delta', thread_id: 'thread-one', turn_id: 'turn-one', params: {itemId: 'preview-message', delta: 'Retained partial text', preview_only: true}});
receive({id: 16, method: 'item/completed', thread_id: 'thread-one', turn_id: 'turn-one', params: {item: {id: 'preview-message', type: 'agentMessage'}}});
assert.equal(run("codexThread('thread-one').items.find(item => item.id === 'preview-message').preview"), true, 'Completion does not erase a partial delta marker');
receive({id: 17, method: 'item/completed', thread_id: 'thread-one', turn_id: 'turn-one', params: {preview_only: true, item: {id: 'preview-completed-message', type: 'agentMessage', text: 'Retained completed text'}}});
assert.match(node('#codex-timeline').innerHTML, /Retained preview · incomplete message/);
const pair = ['pair-first', 'pair-second'].map(approval_id => ({approval_id, thread_id: 'thread-one', turn_id: 'pair-turn', request_key: 'pair-key', status: 'pending', params: {}, can_approve: true, can_deny: true}));
context.pair = pair;
run("setCodexStatus({mode:'standalone',state:'awaiting_approval',connected:true,thread_id:'thread-one',turn_id:'pair-turn',cursor:30,pending_approvals:pair}); renderCodexActivity();");
assert.doesNotMatch(run('codexApprovalCard(pair[1])'), /data-action="codex-approve"[^>]*disabled/);
receive({id: 31, method: 'approval/resolved', thread_id: 'thread-one', turn_id: 'pair-turn', params: {approval_id: 'pair-first', status: 'resolved', decision: 'approve'}});
run("setCodexStatus({mode:'standalone',state:'awaiting_approval',connected:true,thread_id:'thread-one',turn_id:'pair-turn',cursor:31,pending_approvals:[pair[1]]}); renderCodexActivity();");
assert.equal(run('codexPanelState.status.state'), 'awaiting_approval');
assert.doesNotMatch(run('codexApprovalCard(pair[1])'), /data-action="codex-approve"[^>]*disabled/, 'The remaining approval stays actionable after the authoritative status refresh');
pair[1].can_approve = false;
run("setCodexStatus({mode:'standalone',state:'awaiting_approval',connected:true,thread_id:'thread-one',turn_id:'pair-turn',cursor:32,pending_approvals:[pair[1]]}); renderCodexActivity();");
const remainingAfterDenial = run('codexApprovalCard(pair[1])');
assert.match(remainingAfterDenial, /data-action="codex-approve"[^>]*disabled/);
assert.doesNotMatch(remainingAfterDenial, /data-action="codex-deny"[^>]*disabled/, 'Backend denial policy leaves remaining actions deniable');
assert.match(run("codexApprovalCard({...pair[1],turn_id:'stale-turn'})"), /data-action="codex-deny"[^>]*disabled/);
assert.match(run("codexApprovalCard({...pair[1],thread_id:'other-thread'})"), /data-action="codex-deny"[^>]*disabled/);
run("codexPanelState.approvals.set('stale-action', {...pair[1],approval_id:'stale-action',turn_id:'stale-turn'});");
const codexClick = handlers.click.find(handler => handler.toString().includes("action === 'codex-refresh'"));
await codexClick({target: {closest: () => ({dataset: {action: 'codex-deny', id: 'stale-action'}, disabled: false})}});
assert.equal(requests.length, 0, 'Even a forged enabled button cannot submit a stale turn approval');
run("setCodexStatus({mode:'embedded_in_circuitgit',state:'external',connected:false,cursor:8,pending_approvals:[]});");
assert.equal(streams[0].closed, true);
assert.doesNotMatch(run('nativeChat()'), /codex-connect-form|codex-message-form/);
assert.doesNotMatch(source, /spiceowl\.native-pending|pollNative|\/api\/native-agents|\/api\/agent-questions/);
console.log('PASS: dual mode rendering, explicit-only connection, escaped SSE/tool/approval cards, cursor deduplication, uncertainty and legacy isolation.');

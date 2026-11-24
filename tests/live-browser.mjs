// Real Chrome DOM checks against an owned loopback fixture; no SSH or live client.
// PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node tests/live-browser.mjs
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const OUTPUT = resolve(process.env.LIVE_QA_DIR || join(ROOT, '.spiceowl/live-browser-qa'));
const STATIC = join(ROOT, 'src/spiceowl/web/static');
const fixture = {version: 'fixture', connections: [], hosts: [], native_agents: [],
  agent_channel: {mode: 'embedded_in_circuitgit', connection_status: 'not_checked'},
  questions: ['queued', 'running', 'outcome_unknown', 'completed'].map(status => ({
    qa_id: `legacy-${status}`, agent_id: 'removed-legacy-profile', status,
    question: `Saved ${status} question <img src=x onerror=window.agentHistoryInjection=true>`,
    answer: status === 'completed' ? 'Saved legacy answer.' : '', updated_at: '2026-01-01T00:00:00Z',
    model: 'saved-model', provider: 'saved-provider', synthetic: false})), sessions: [],
  csrf_token: 'offline-browser-fixture-token', runs: [{run_id: 'demo-only', label: 'Synthetic fixture remains separate',
    attempt_id: 'demo-attempt', execution: 'running', observation: 'current', measurement_validity: 'unknown',
    specification_result: 'unknown', progress: .5, events: [], synthetic: true}]};
const runtime = {schema: 'ic.simulation.runtime.v1', project_id: 'fixture-project', read_at: '2026-11-23T01:00:00Z',
  capabilities: {scan: true, direct_submit: true, direct_collect: true, monitor: true},
  pool: {configured: true, checked_at: '2026-11-23T00:59:00Z', scan_in_progress: false, hosts: [
    {host_id: 'node-one', label: 'Fixture node', reachable: true, cpu_count: 16, cpu_busy_percent: 12.5,
      memory_available_bytes: 32 * 2**30, memory_total_bytes: 64 * 2**30, observed_at: '2026-11-23T00:59:00Z', error_code: null}]},
  runs: [{run_id: 'fixture-run', configuration: 'tran', source_commit: 'a'.repeat(40), execution_target: {host_id: 'node-one'},
    execution: {state: 'running', exit_code: null}, collectable:false, monitor: {state: 'disconnected', last_success_at: '2026-11-23T00:55:00Z', next_check_at: '2026-11-23T01:01:00Z'},
    acceptance: {execution: 'running', data_validity: 'unknown', specification: 'unknown'},
    diagnostics: {diagnostics: [{code: 'spectre_license', redacted_excerpt: '<img src=x onerror=window.fixtureInjection=true>',
      suggested_actions: ['Inspect the saved license diagnostic. No automatic action.']}]}}]};
let enabled = true, loseNextSubmit = false;
const codex = {mode: 'standalone', state: 'disconnected', connected: false, thread_id: null, turn_id: null,
  request_key: null, cursor: 0, recovery_required: false, pending_approvals: [], model: null, connection_profile: {}};
const codexEvents = [], codexStreams = new Set();
const fullCodexSampleResult = {samples: Array.from({length: 4096}, (_, index) => index)};
let codexThreadCount = 0, codexTurnCount = 0, loseNextCodexMessage = false;
function codexEvent(method, params) {
  const event = {id: ++codex.cursor, method, params: structuredClone(params), thread_id: codex.thread_id,
    turn_id: codex.turn_id, request_key: codex.request_key};
  codexEvents.push(event);
  for (const stream of codexStreams) {
    if (!stream.response.writableEnded && event.id > stream.cursor) {
      stream.response.write(`id: ${event.id}\nevent: bridge\ndata: ${JSON.stringify(event)}\n\n`);
      stream.cursor = event.id;
    }
  }
}
const requests = [], pageErrors = [], blocked = [], dialogs = [];
const staticTypes = {'/': ['index.html', 'text/html'], '/app.js': ['app.js', 'text/javascript'],
  '/app.css': ['app.css', 'text/css'], '/owl.svg': ['owl.svg', 'image/svg+xml']};
const server = createServer(async (request, response) => {
  const url = new URL(request.url, 'http://fixture.invalid');
  const send = (value, status = 200, type = 'application/json') => {
    response.writeHead(status, {'Content-Type': type, 'Cache-Control': 'no-store'});
    response.end(type === 'application/json' ? JSON.stringify(value) : value);
  };
  try {
    requests.push({method: request.method, path: url.pathname, query: url.search});
    if (request.method === 'GET' && staticTypes[url.pathname]) {
      const [name, type] = staticTypes[url.pathname]; return send(await readFile(join(STATIC, name)), 200, type);
    }
    if (request.method === 'GET' && url.pathname === '/api/state') return send(fixture);
    if (request.method === 'GET' && url.pathname === '/api/codex/status') return send(codex);
    if (request.method === 'GET' && url.pathname === '/api/codex/approval') {
      const approval = codex.pending_approvals.find(item => item.approval_id === url.searchParams.get('approval_id'));
      assert.ok(approval);
      Object.assign(approval, {preview_only: false, can_approve: true,
        params: {name: 'fixture_operation', arguments: {project_id: 'fixture-project', frozen_detail: 'Exact synthetic action details'}}});
      return send(approval);
    }
    if (request.method === 'GET' && url.pathname === '/api/codex/events') {
      const after = Math.max(Number(url.searchParams.get('after') || 0), Number(request.headers['last-event-id'] || 0));
      requests.at(-1).last_event_id = request.headers['last-event-id'] || null;
      response.writeHead(200, {'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store'});
      const stream = {response, cursor: after}; codexStreams.add(stream);
      for (const event of codexEvents.filter(event => event.id > after)) {
        response.write(`id: ${event.id}\nevent: bridge\ndata: ${JSON.stringify(event)}\n\n`); stream.cursor = event.id;
      }
      response.write(': fixture heartbeat\n\n');
      response.on('close', () => codexStreams.delete(stream));
      return;
    }
    if (request.method === 'POST' && url.pathname.startsWith('/api/codex/')) {
      assert.equal(request.headers['x-spiceowl-token'], fixture.csrf_token);
      let raw = ''; for await (const chunk of request) raw += chunk;
      const body = JSON.parse(raw); requests.at(-1).body = body;
      const action = url.pathname.split('/').at(-1);
      if (action === 'connect') {
        assert.equal(codex.connected, false);
        Object.assign(codex, {state: 'connected', connected: true, model: body.model || null, connection_profile: body});
        codexEvent('bridge/connected', {model: codex.model}); return send(codex, 202);
      }
      if (action === 'thread') {
        assert.ok(codex.connected && !codex.recovery_required);
        Object.assign(codex, {state: 'ready', thread_id: `fixture-thread-${++codexThreadCount}`, turn_id: null, request_key: null});
        codexEvent('bridge/thread', {thread_id: codex.thread_id}); return send({thread_id: codex.thread_id, state: 'ready'}, 202);
      }
      if (action === 'message') {
        assert.equal(body.thread_id, codex.thread_id); assert.ok(codex.connected && !codex.recovery_required);
        assert.match(body.request_key, /^codex-/);
        Object.assign(codex, {state: 'running', request_key: body.request_key, turn_id: `fixture-turn-${++codexTurnCount}`});
        codexEvent('bridge/user_message', {text: body.text, request_key: body.request_key, state: 'pending'});
        if (loseNextCodexMessage) {
          loseNextCodexMessage = false;
          Object.assign(codex, {state: 'unknown', turn_id: null, recovery_required: true});
          return send({error: {code: 'codex_outcome_unknown', message: 'The send outcome is unconfirmed; preserve its key.'}}, 400);
        }
        codexEvent('turn/started', {threadId: codex.thread_id, turn: {id: codex.turn_id, status: 'inProgress'}});
        codexEvent('item/agentMessage/delta', {threadId: codex.thread_id, turnId: codex.turn_id, itemId: codex.turn_id + '-answer', delta: 'Inspecting <img src=x onerror=window.codexInjection=true> '});
        codexEvent('item/agentMessage/delta', {threadId: codex.thread_id, turnId: codex.turn_id, itemId: codex.turn_id + '-answer', delta: 'saved evidence.'});
        codexEvent('tool/result', {tool: 'read_simulation_status', call_id: codex.turn_id + '-tool', success: true,
          result: {source: 'synthetic fixture', execution: 'unknown', details: {review_url: 'https://review.example.invalid/request-fixture'}}});
        if (codexTurnCount === 1) {
          codexEvent('tool/result', {tool: 'read_samples', call_id: codex.turn_id + '-samples', success: true,
            preview_only: true, result: {samples: fullCodexSampleResult.samples.slice(0, 256)}});
          codexEvent('item/agentMessage/delta', {itemId: 'partial-delta-message', delta: 'Retained partial response', preview_only: true});
          codexEvent('item/completed', {item: {id: 'partial-delta-message', type: 'agentMessage'}});
          codexEvent('item/completed', {preview_only: true, item: {id: 'partial-completed-message', type: 'agentMessage', text: 'Retained completed response'}});
        }
        const approval = {approval_id: `fixture-approval-${codexTurnCount}`, kind: 'dynamic_tool', method: 'item/tool/call',
          summary: 'Review this frozen synthetic operation', params: {name: 'fixture_operation', arguments: {project_id: 'fixture-project'}},
          status: 'pending', can_approve: codexTurnCount === 1, can_deny: true, preview_only: codexTurnCount === 3,
          request_key: codex.request_key, thread_id: codex.thread_id, turn_id: codex.turn_id};
        codex.pending_approvals = [approval]; codex.state = 'awaiting_approval';
        codexEvent('approval/required', approval);
        if (codexTurnCount <= 2) {
          const secondApproval = {...approval, approval_id: `${approval.approval_id}-second`,
            summary: 'Review the second frozen synthetic operation', can_approve: true};
          codex.pending_approvals.push(secondApproval);
          codexEvent('approval/required', secondApproval);
        }
        return send({schema: 'spiceowl.codex-turn.v1', thread_id: codex.thread_id, turn_id: codex.turn_id,
          request_key: codex.request_key, state: 'awaiting_approval'}, 202);
      }
      if (action === 'approval') {
        const approval = codex.pending_approvals.find(item => item.approval_id === body.approval_id);
        assert.ok(approval); assert.ok(['approve', 'deny'].includes(body.decision));
        assert.equal(approval.thread_id, codex.thread_id); assert.equal(approval.turn_id, codex.turn_id);
        if (body.decision === 'approve') assert.equal(approval.can_approve, true);
        codex.pending_approvals = codex.pending_approvals.filter(item => item.approval_id !== body.approval_id);
        if (body.decision === 'deny') for (const item of codex.pending_approvals) item.can_approve = false;
        codex.state = codex.pending_approvals.length ? 'awaiting_approval' : 'running';
        codexEvent('approval/resolved', {approval_id: approval.approval_id, decision: body.decision, status: 'resolved'});
        if (!codex.pending_approvals.length) setTimeout(() => {
          codex.state = 'complete';
          codexEvent('turn/completed', {threadId: codex.thread_id, turn: {id: codex.turn_id, status: 'completed'}});
        }, 30);
        return send({approval_id: approval.approval_id, decision: body.decision, status: 'resolved'}, 202);
      }
      if (action === 'disconnect') {
        codex.connected = false;
        if (!codex.recovery_required) codex.state = 'disconnected';
        codexEvent('bridge/disconnected', {automatic_retry: false}); return send(codex, 202);
      }
      return send({error: {message: 'Unexpected Codex fixture operation'}}, 404);
    }
    if (request.method === 'GET' && url.pathname === '/api/live/runtime') return send({enabled, project_id: enabled ? 'fixture-project' : null, runtime: enabled ? runtime : null});
    if (request.method === 'GET' && url.pathname === '/api/live/submission') return send({state: 'mapping_found', run_id: 'fixture-lost-run', replay_permitted: false});
    if (request.method === 'POST' && url.pathname === '/api/live/control') {
      assert.equal(request.headers['x-spiceowl-token'], fixture.csrf_token);
      let raw = ''; for await (const chunk of request) raw += chunk;
      const body = JSON.parse(raw); requests.at(-1).body = body;
      assert.equal(body.args.project_id, 'fixture-project');
      assert.ok(!Object.keys(body.args).some(key => /path|command|credential/.test(key)));
      if (body.operation === 'submit' && loseNextSubmit) {
        loseNextSubmit = false;
        return send({error: {code: 'live_outcome_unknown', message: 'The upstream reply was lost; preserve the original request key.'}}, 400);
      }
      if (body.operation === 'scan') return send({state: 'complete'}, 202);
      if (body.operation === 'submit') return send({run_id: 'fixture-submitted', state: 'submitted'}, 202);
      if (body.operation === 'collect') return send({run_id: body.args.run_id, state: 'collecting'}, 202);
      if (body.operation === 'watch' || body.operation === 'unwatch') return send({run_id: body.args.run_id, watch_state: body.operation === 'watch' ? 'armed' : 'stopped'}, 202);
      const row=runtime.runs.find(run=>run.run_id===body.args.run_id)||{...runtime.runs[0],...body.args};
      row.execution={state:'completed',exit_code:0};row.collectable=true;row.acceptance={execution:'completed',data_validity:'unknown',specification:'unknown'};
      if(!runtime.runs.includes(row))runtime.runs.push(row);
      return send({...body.args, remote_state:'simulator_complete_pending_measurements',state: 'simulator_complete_pending_measurements'}, 202);
    }
    return send({error: {message: 'Unexpected fixture request'}}, 404);
  } catch (error) { return send({error: {message: error.message}}, 500); }
});

let browser;
const report = {schema: 'spiceowl.live-browser.v1', status: 'running', checks: [], screenshots: []};
try {
  await mkdir(OUTPUT, {recursive: true});
  await new Promise((yes, no) => { server.once('error', no); server.listen(0, '127.0.0.1', yes); });
  const origin = `http://127.0.0.1:${server.address().port}`;
  const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
  browser = await chromium.launch({headless: true, ...(process.env.CHROME_PATH ? {executablePath: process.env.CHROME_PATH} : {channel: 'chrome'}), args: ['--disable-background-networking']});
  const context = await browser.newContext({viewport: {width: 1440, height: 1050}, serviceWorkers: 'block'});
  const legacyPending = JSON.stringify({agent_id: 'removed-legacy-profile', question: 'Old unresolved question', request_key: 'preserve-old-key'});
  await context.addInitScript(pending => sessionStorage.setItem('spiceowl.native-pending', pending), legacyPending);
  await context.route('**/*', route => {
    if (new URL(route.request().url()).origin !== origin) { blocked.push(route.request().url()); return route.abort(); }
    return route.continue();
  });
  const page = await context.newPage();
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('dialog', dialog => { dialogs.push(dialog.message()); dialog.dismiss(); });
  await page.goto(origin + '/#agents', {waitUntil: 'networkidle'});
  await page.locator('[data-agent-channel="circuitgit"]').waitFor();
  const channel = page.locator('[data-agent-channel="circuitgit"]');
  assert.equal(await channel.locator('a').getAttribute('href'), 'https://circuitgit-dev.github.io/CircuitGit/');
  assert.match(await channel.innerText(), /not checked/i);
  assert.equal(await page.locator('#native-question-form, [data-action="native-probe"]').count(), 0);
  assert.equal(await page.locator('[data-action="native-select"]').count(), 4, 'History remains visible without registered profiles');
  assert.equal(await page.locator('#native-response img').count(), 0);
  assert.equal(await page.evaluate(() => window.agentHistoryInjection), undefined);
  const beforeAgentIdle = requests.length;
  await page.waitForTimeout(1250);
  assert.equal(requests.length, beforeAgentIdle, 'Legacy queued/running history must not poll');
  for (const status of ['running', 'outcome_unknown', 'completed', 'queued']) {
    await page.locator(`[data-action="native-select"][data-id="legacy-${status}"]`).click();
    assert.match(await page.locator('#native-response').innerText(), new RegExp(`Saved ${status}`));
  }
  assert.equal(requests.length, beforeAgentIdle, 'Selecting history uses only the saved state');
  assert.equal(await page.evaluate(() => sessionStorage.getItem('spiceowl.native-pending')), legacyPending);
  await page.reload({waitUntil: 'networkidle'});
  await page.locator('[data-agent-channel="circuitgit"]').waitFor();
  assert.equal(requests.some(item => item.method === 'POST' || item.path.startsWith('/api/agent-questions')
    || item.path.startsWith('/api/native-agents')), false, 'No native dispatch, readiness request, detail poll or old-key replay');
  assert.equal(await page.evaluate(() => sessionStorage.getItem('spiceowl.native-pending')), legacyPending);
  await page.screenshot({path: join(OUTPUT, 'agent-channel-desktop.png'), fullPage: true});
  report.screenshots.push('agent-channel-desktop.png');
  await page.setViewportSize({width: 390, height: 844});
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1));
  await page.screenshot({path: join(OUTPUT, 'agent-channel-mobile.png'), fullPage: true});
  report.screenshots.push('agent-channel-mobile.png');
  await page.setViewportSize({width: 1440, height: 1050});
  report.checks.push('CircuitGit channel and orphaned legacy history are read-only; no native requests, polling or pending-key replay');
  await page.goto(origin + '/#live');
  await page.locator('[data-action="live-submit"]').waitFor();
  assert.equal(requests.filter(item => item.method === 'POST').length, 0, 'Opening the view must not scan or submit');
  assert.match(await page.locator('.live-run').innerText(), /Running/);
  assert.match(await page.locator('.live-run').innerText(), /Disconnected/);
  assert.equal(await page.evaluate(() => window.fixtureInjection), undefined);
  assert.equal(await page.locator('.live-diagnostic img').count(), 0);
  report.checks.push('Cached entry, separate execution/connection state, escaped diagnostic text');

  async function click(action) {
    const before = requests.filter(item => item.method === 'POST').length;
    await page.locator(`[data-action="live-${action}"]`).click();
    await page.waitForFunction(() => !document.querySelector('[data-action="live-status"]')?.disabled);
    assert.equal(requests.filter(item => item.method === 'POST').length, before + 1);
    return requests.filter(item => item.body).at(-1).body;
  }
  const scan = await click('scan'); assert.deepEqual(scan, {operation: 'scan', args: {project_id: 'fixture-project'}});
  await page.locator('[data-action="live-select"]').click();
  assert.equal(await page.locator('#live-run-id').inputValue(), 'fixture-run');
  assert.equal(await page.locator('#live-source-commit').inputValue(), 'a'.repeat(40));
  assert.equal(await page.locator('[data-action="live-collect"]').isDisabled(),true,'Running work cannot claim collection before a terminal status');
  runtime.runs[0].execution={state:'failed',exit_code:null,raw_state:'preflight_failed'};runtime.runs[0].acceptance.execution='failed';
  await page.locator('[data-action="live-refresh"]').click();
  await page.waitForFunction(()=>document.querySelector('.live-run strong')?.textContent==='Failed');
  assert.equal(await page.locator('[data-action="live-collect"]').isDisabled(),true,'A preflight failure with collectable=false cannot collect');
  delete runtime.runs[0].collectable;
  await page.locator('[data-action="live-refresh"]').click();
  assert.equal(await page.locator('[data-action="live-collect"]').isDisabled(),true,'Missing collectable stays disabled');
  for (const action of ['status', 'watch', 'unwatch', 'collect']) {
    const body = await click(action);
    assert.equal(body.operation, action); assert.equal(body.args.run_id, 'fixture-run');
    assert.equal(body.args.configuration, 'tran'); assert.equal(body.args.source_commit, 'a'.repeat(40));
    if (action === 'collect') assert.match(body.idempotency_key, /^live-/);
  }
  const submitted = await click('submit');
  assert.equal(submitted.operation, 'submit'); assert.equal('run_id' in submitted.args, false);
  assert.match(submitted.idempotency_key, /^live-/);
  assert.equal(await page.locator('#live-run-id').inputValue(), 'fixture-submitted', 'A new receipt becomes the selected exact run');
  report.checks.push('Explicit scan/status/watch/unwatch/collect/submit carry exact identity and CSRF');
  const beforeRefresh = requests.filter(item => item.method === 'POST').length;
  await page.locator('[data-action="live-refresh"]').click();
  assert.equal(requests.filter(item => item.method === 'POST').length, beforeRefresh);
  await page.screenshot({path: join(OUTPUT, 'desktop.png'), fullPage: true}); report.screenshots.push('desktop.png');
  await page.setViewportSize({width: 390, height: 844});
  const width = await page.evaluate(() => ({screen: document.documentElement.clientWidth, content: document.documentElement.scrollWidth}));
  assert.ok(width.content <= width.screen + 1, 'Mobile layout must not overflow the document');
  await page.screenshot({path: join(OUTPUT, 'mobile.png'), fullPage: true}); report.screenshots.push('mobile.png');
  report.checks.push('Desktop and mobile DOM render without document overflow');

  loseNextSubmit = true;
  await click('submit');
  await page.locator('.live-pending').waitFor();
  const original = await page.evaluate(() => JSON.parse(sessionStorage.getItem('spiceowl.live-pending')));
  assert.equal(await page.locator('[data-action="live-submit"]').isDisabled(), true);
  const beforeLookup = requests.filter(item => item.method === 'POST').length;
  await page.reload(); await page.locator('[data-action="live-lookup"]').waitFor();
  await page.locator('[data-action="live-lookup"]').click();
  await page.waitForFunction(() => document.querySelector('#live-run-id')?.value === 'fixture-lost-run');
  assert.equal(requests.filter(item => item.method === 'POST').length, beforeLookup);
  const lookup = requests.findLast(item => item.path === '/api/live/submission');
  assert.equal(new URLSearchParams(lookup.query).get('key'), original.idempotency_key);
  assert.deepEqual(await page.evaluate(() => JSON.parse(sessionStorage.getItem('spiceowl.live-pending'))), original);
  assert.equal(await page.locator('[data-action="live-collect"]').isDisabled(),true,'A submission mapping alone does not authorize collection');
  await click('status');
  const originalRunCollection = await click('collect');
  assert.equal(originalRunCollection.args.run_id, 'fixture-lost-run');
  assert.deepEqual(await page.evaluate(() => JSON.parse(sessionStorage.getItem('spiceowl.live-pending'))), original);
  report.checks.push('Lost response preserves key across reload; lookup is GET only with no replay');

  enabled = false;
  await page.reload(); await page.locator('[data-live-disabled]').waitFor();
  assert.equal(await page.locator('[data-action="live-submit"]').count(), 0);
  report.checks.push('No operator configuration shows disabled state without controls');
  await context.close();
  const publicContext = await browser.newContext({serviceWorkers: 'block'});
  await publicContext.addInitScript(state => {
    window.SpiceOwlDemo = {request: async () => state, metadata: {}, storage_mode: 'memory'};
  }, {...fixture, csrf_token: null, public_demo: true});
  const publicPage = await publicContext.newPage();
  publicPage.on('pageerror', error => pageErrors.push(error.message));
  const publicStart = requests.length;
  await publicPage.goto(origin + '/#live');
  await publicPage.locator('[data-public-demo-info="live"]').waitFor();
  assert.equal(requests.slice(publicStart).some(item => item.path.startsWith('/api/')), false);
  assert.equal(await publicPage.locator('#live-configuration').count(), 0);
  await publicPage.goto(origin + '/#agents');
  await publicPage.locator('[data-public-demo-info="agents"]').waitFor();
  assert.equal(await publicPage.locator('[data-public-demo-info="agents"] a[target="_blank"]').getAttribute('href'),
    'https://circuitgit-dev.github.io/CircuitGit/');
  assert.equal(await publicPage.locator('#native-question-form, [data-action="native-probe"]').count(), 0);
  assert.equal(requests.slice(publicStart).some(item => item.path.startsWith('/api/')), false);
  await publicContext.close();
  report.checks.push('Public synthetic demo exposes no live inputs or API calls');

  fixture.agent_channel = {mode: 'standalone', owner: 'spiceowl', connection_status: 'not_checked'};
  const standaloneContext = await browser.newContext({viewport: {width: 1440, height: 1050}, serviceWorkers: 'block'});
  await standaloneContext.route('**/*', route => {
    if (new URL(route.request().url()).origin !== origin) { blocked.push(route.request().url()); return route.abort(); }
    return route.continue();
  });
  await standaloneContext.addInitScript(pending => sessionStorage.setItem('spiceowl.native-pending', pending), legacyPending);
  const agentPage = await standaloneContext.newPage();
  agentPage.on('pageerror', error => pageErrors.push(error.message));
  const agentStart = requests.length;
  await agentPage.goto(origin + '/#agents', {waitUntil: 'networkidle'});
  await agentPage.locator('#codex-connect-form').waitFor();
  assert.equal(requests.slice(agentStart).filter(item => item.method === 'POST').length, 0, 'Opening standalone must not connect');
  assert.equal(requests.slice(agentStart).some(item => item.path === '/api/codex/events'), false, 'Disconnected startup has no stream to attach');
  assert.equal(await agentPage.locator('#workflow-link').getAttribute('href'), './workflow.html');
  await agentPage.locator('#codex-model').fill('fixture-model');
  await agentPage.locator('#codex-profile').fill('fixture-profile');
  await agentPage.locator('#codex-connect-form [type="submit"]').click();
  await agentPage.locator('[data-action="codex-thread"]:not([disabled])').waitFor();
  assert.deepEqual(requests.findLast(item => item.path === '/api/codex/connect').body, {model: 'fixture-model', profile: 'fixture-profile'});
  await agentPage.locator('[data-action="codex-thread"]').click();
  await agentPage.locator('#codex-message:not([disabled])').waitFor();
  await agentPage.locator('#codex-message').fill('Read the synthetic simulation evidence.');
  await agentPage.locator('#codex-send').click();
  const firstApproval = agentPage.locator('[data-approval-id="fixture-approval-1"]');
  await firstApproval.locator('[data-action="codex-approve"]:not([disabled])').waitFor();
  assert.match(await agentPage.locator('#codex-timeline').innerText(), /Inspecting .*saved evidence\./s);
  assert.match(await agentPage.locator('#codex-timeline').innerText(), /read_simulation_status/);
  assert.match(await agentPage.locator('#codex-timeline').innerText(), /execution owner’s human review/);
  const sampleCard = agentPage.locator('.codex-tool').filter({hasText: 'read_samples'});
  assert.match(await sampleCard.innerText(), /Retained preview · incomplete tool result/);
  assert.match(await sampleCard.innerText(), /Completed/);
  assert.equal(fullCodexSampleResult.samples.length, 4096);
  assert.equal(JSON.parse(await sampleCard.locator('pre').innerText()).samples.length, 256);
  assert.equal(await agentPage.locator('.codex-assistant').filter({hasText: 'Retained preview · incomplete message.'}).count(), 2);
  report.checks.push('Retained 256-of-4096 sample preview and partial assistant messages stay explicitly incomplete after completion');
  assert.equal(await agentPage.locator('#codex-timeline img').count(), 0);
  assert.equal(await agentPage.evaluate(() => window.codexInjection), undefined);
  assert.equal(await agentPage.evaluate(() => sessionStorage.getItem('spiceowl.codex-pending')), null, 'Acknowledged turn binds and clears only its own new key');
  await agentPage.screenshot({path: join(OUTPUT, 'standalone-agent-desktop.png'), fullPage: true});
  report.screenshots.push('standalone-agent-desktop.png');
  await agentPage.setViewportSize({width: 390, height: 844});
  assert.ok(await agentPage.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1));
  await agentPage.screenshot({path: join(OUTPUT, 'standalone-agent-mobile.png'), fullPage: true});
  report.screenshots.push('standalone-agent-mobile.png');
  await agentPage.setViewportSize({width: 1440, height: 1050});
  await firstApproval.locator('[data-action="codex-approve"]').click();
  const secondApproval = agentPage.locator('[data-approval-id="fixture-approval-1-second"]');
  await secondApproval.locator('[data-action="codex-approve"]:not([disabled])').waitFor();
  assert.match(await agentPage.locator('#codex-runtime-state').innerText(), /Awaiting approval/);
  assert.equal(await firstApproval.locator('[data-action="codex-approve"]').isDisabled(), true);
  assert.deepEqual(requests.findLast(item => item.path === '/api/codex/approval').body,
    {approval_id: 'fixture-approval-1', decision: 'approve'});
  await secondApproval.locator('[data-action="codex-approve"]').click();
  await agentPage.waitForFunction(() => document.querySelector('#codex-runtime-state')?.textContent.includes('Complete'));
  assert.deepEqual(requests.findLast(item => item.path === '/api/codex/approval').body,
    {approval_id: 'fixture-approval-1-second', decision: 'approve'});
  report.checks.push('Two approvals retain the second exact action after approving the first; backend remains awaiting approval');
  const beforeReconnect = requests.filter(item => item.method === 'POST').length;
  for (const stream of [...codexStreams]) stream.response.end();
  await agentPage.waitForFunction(() => document.querySelector('#codex-stream-state')?.textContent.includes('interrupted'));
  await agentPage.waitForFunction(() => document.querySelector('#codex-stream-state')?.textContent === 'Live event stream');
  assert.equal(requests.filter(item => item.method === 'POST').length, beforeReconnect);
  assert.ok(requests.findLast(item => item.path === '/api/codex/events').last_event_id, 'SSE reconnect retains its cursor');
  report.checks.push('Standalone explicit connection, streamed text/tool cards, frozen human approval and read-only SSE reconnect');

  await agentPage.locator('[data-action="codex-thread"]').click();
  await agentPage.waitForFunction(() => document.querySelector('#codex-session')?.value === 'fixture-thread-2');
  await agentPage.locator('#codex-session').selectOption('fixture-thread-1');
  assert.match(await agentPage.locator('#codex-notices').innerText(), /Historical conversation/);
  assert.equal(await agentPage.locator('#codex-message').isDisabled(), true);
  await agentPage.locator('#codex-session').selectOption('fixture-thread-2');
  await agentPage.locator('#codex-message').fill('Show a request that may only be denied.');
  await agentPage.locator('#codex-send').click();
  const denied = agentPage.locator('[data-approval-id="fixture-approval-2"]');
  await denied.locator('[data-action="codex-deny"]:not([disabled])').waitFor();
  assert.equal(await denied.locator('[data-action="codex-approve"]').isDisabled(), true);
  await denied.locator('[data-action="codex-deny"]').click();
  const secondDenied = agentPage.locator('[data-approval-id="fixture-approval-2-second"]');
  await secondDenied.locator('[data-action="codex-deny"]:not([disabled])').waitFor();
  assert.match(await agentPage.locator('#codex-runtime-state').innerText(), /Awaiting approval/);
  assert.equal(await secondDenied.locator('[data-action="codex-approve"]').isDisabled(), true);
  assert.deepEqual(requests.findLast(item => item.path === '/api/codex/approval').body,
    {approval_id: 'fixture-approval-2', decision: 'deny'});
  await secondDenied.locator('[data-action="codex-deny"]').click();
  await agentPage.waitForFunction(() => document.querySelector('#codex-runtime-state')?.textContent.includes('Complete'));
  assert.deepEqual(requests.findLast(item => item.path === '/api/codex/approval').body,
    {approval_id: 'fixture-approval-2-second', decision: 'deny'});
  report.checks.push('Denying one of two approvals preserves remaining deny access while backend policy disables approval');
  await agentPage.locator('#codex-message').fill('Show a preview that needs full frozen details.');
  await agentPage.locator('#codex-send').click();
  const preview = agentPage.locator('[data-approval-id="fixture-approval-3"]');
  await preview.locator('[data-action="codex-details"]').waitFor();
  assert.equal(await preview.locator('[data-action="codex-approve"]').isDisabled(), true);
  await preview.locator('[data-action="codex-details"]').click();
  await preview.locator('[data-action="codex-approve"]:not([disabled])').waitFor();
  assert.match(await preview.innerText(), /Exact synthetic action details/);
  await preview.locator('[data-action="codex-approve"]').click();
  await agentPage.waitForFunction(() => document.querySelector('#codex-runtime-state')?.textContent.includes('Complete'));
  report.checks.push('Read-only historical conversations, deny-only policy and exact-details gate before approving a preview');

  loseNextCodexMessage = true;
  await agentPage.locator('#codex-message').fill('Preserve this request if its reply is lost.');
  await agentPage.locator('#codex-send').click();
  await agentPage.waitForFunction(() => document.querySelector('#codex-notices')?.textContent.includes('unconfirmed'));
  const preserved = await agentPage.evaluate(() => sessionStorage.getItem('spiceowl.codex-pending'));
  assert.ok(preserved);
  const beforeReload = requests.filter(item => item.method === 'POST').length;
  await agentPage.reload({waitUntil: 'domcontentloaded'});
  await agentPage.waitForFunction(() => document.querySelector('#codex-runtime-state')?.textContent.includes('Unknown'));
  assert.equal(requests.filter(item => item.method === 'POST').length, beforeReload, 'Reload must not resend pending text');
  assert.equal(await agentPage.evaluate(() => sessionStorage.getItem('spiceowl.codex-pending')), preserved);
  assert.equal(await agentPage.evaluate(() => sessionStorage.getItem('spiceowl.native-pending')), legacyPending);
  assert.equal(await agentPage.locator('[data-action="codex-thread"]').isDisabled(), true);
  const simulationsBefore = JSON.stringify(runtime.runs);
  await agentPage.locator('[data-action="codex-disconnect"]').click();
  await agentPage.waitForFunction(() => document.querySelector('[data-action="codex-disconnect"]')?.disabled === true);
  assert.equal(JSON.stringify(runtime.runs), simulationsBefore, 'Disconnect must not cancel or modify simulations');
  assert.equal(await agentPage.evaluate(() => sessionStorage.getItem('spiceowl.codex-pending')), preserved);
  assert.equal(requests.slice(agentStart).some(item => item.path.startsWith('/api/agent-questions') || item.path.startsWith('/api/native-agents')), false);
  await standaloneContext.close();
  report.checks.push('Uncertain send survives reload without replay; Agent disconnect preserves both request and simulation identity');
  assert.deepEqual(pageErrors, []); assert.deepEqual(blocked, []); assert.deepEqual(dialogs, []);
  report.status = 'passed';
} catch (error) { report.status = 'failed'; report.error = error.stack; process.exitCode = 1; }
finally {
  if (browser) await browser.close();
  if (server.listening) await new Promise(resolve => server.close(resolve));
  report.requests = requests; report.page_errors = pageErrors; report.blocked_requests = blocked;
  await writeFile(join(OUTPUT, 'report.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify({status: report.status, checks: report.checks.length, output: OUTPUT, error: report.error?.split('\n')[0]}));
}

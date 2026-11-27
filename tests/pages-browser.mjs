// Opt-in Chrome acceptance for the PUBLIC synthetic GitHub Pages build.
// No API server is used. The owned static server exposes only /spiceowl/.
// Example: PAGES_DIR=dist/pages PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node tests/pages-browser.mjs
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createHash} from 'node:crypto';
import {mkdir, readFile, readdir, lstat, writeFile} from 'node:fs/promises';
import {resolve, relative, join, extname, dirname} from 'node:path';
import {fileURLToPath} from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const BUILD = resolve(process.env.PAGES_DIR || join(ROOT, 'dist/pages'));
const OUTPUT = resolve(process.env.PAGES_QA_DIR || join(ROOT, '.spiceowl/pages-qa'));
const PREFIX = '/spiceowl/';
const SECRET_PATTERNS = [
  ['private_home_path', /\/(?:Users|home)\/[A-Za-z0-9_-]+/],
  ['lab_host_alias', /\bthu-[a-z]+\b/],
  ['private_key', /-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----/],
  ['github_token', /\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})\b/],
  ['api_key', /\bsk-(?:proj-)?[A-Za-z0-9_-]{30,}\b/],
];
const MIME = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
  '.json': 'application/json', '.scs': 'text/plain', '.svg': 'image/svg+xml', '.png': 'image/png', '.webp': 'image/webp', '.ico': 'image/x-icon'};

function routePolicy(url, method, origin) {
  if (!['GET', 'HEAD'].includes(method)) return 'non_static_method';
  if (url.origin !== origin) return 'external_origin';
  let path;
  try { path = decodeURIComponent(url.pathname); } catch { return 'invalid_path'; }
  if (/(?:^|\/)api(?:\/|$)/.test(path)) return 'api_request';
  if (!path.startsWith(PREFIX)) return 'escaped_project_subpath';
  return null;
}

function selfTest() {
  const origin = 'http://127.0.0.1:18888';
  for (const [path, method, result] of [
    ['/spiceowl/', 'GET', null], ['/spiceowl/app.js', 'GET', null],
    ['/api/state', 'GET', 'api_request'], ['/spiceowl/api/state', 'GET', 'api_request'],
    ['/spiceowl/%61pi/state', 'GET', 'api_request'], ['/app.css', 'GET', 'escaped_project_subpath'],
    ['/spiceowl/', 'POST', 'non_static_method'],
  ]) assert.equal(routePolicy(new URL(path, origin), method, origin), result);
  assert.equal(routePolicy(new URL('http://127.0.0.1:8766/api/jobs'), 'GET', origin), 'external_origin');
  assert.equal(routePolicy(new URL('https://example.invalid/'), 'GET', origin), 'external_origin');
  console.log('PASS: offline Pages request policy; no browser or server started.');
}

async function inspectBuild(directory) {
  const inventory = [];
  async function visit(folder) {
    for (const entry of await readdir(folder, {withFileTypes: true})) {
      const path = join(folder, entry.name), name = relative(directory, path).split('\\').join('/');
      assert.ok(!entry.isSymbolicLink(), `Build contains symlink: ${name}`);
      assert.ok(!entry.name.startsWith('.') || entry.name === '.nojekyll', `Unexpected hidden build entry: ${name}`);
      if (entry.isDirectory()) { await visit(path); continue; }
      assert.ok(entry.isFile(), `Build entry is not a regular file: ${name}`);
      assert.ok(entry.name === '.nojekyll' || ['.html', '.js', '.css', '.json', '.scs', '.svg', '.png', '.webp', '.ico', '.txt'].includes(extname(path)),
        `Unexpected build file type: ${name}`);
      const raw = await readFile(path);
      if (['.html', '.js', '.css', '.json', '.scs', '.svg', '.txt'].includes(extname(path))) {
        const text = raw.toString('utf8');
        for (const [label, pattern] of SECRET_PATTERNS) assert.ok(!pattern.test(text), `Public build marker ${label}: ${name}`);
        if (extname(path) === '.json') JSON.parse(text);
      }
      inventory.push({name, bytes: raw.length, sha256: createHash('sha256').update(raw).digest('hex')});
    }
  }
  await visit(directory);
  const expectedFiles = [
    '.nojekyll', 'LICENSE.txt', 'app.css', 'app.js', 'build.json', 'demo-data.js', 'demo.js', 'index.html', 'owl.svg',
    'workflow.html', 'workflow.js', 'workflow.css', 'bootstrap-dut.publication.scs', 'inverter-dut.publication.scs',
    'bootstrap-tb.publication.scs', 'bootstrap-evidence.publication.json', 'source-manifest.json',
    'guide-data.js', 'guide.css', 'guide.js', 'pool-demo.json', 'showcase-bootstrap-tran.svg', 'showcase-bootstrap.svg',
    'showcase-evidence.json', 'showcase-inverter-ac.svg', 'showcase-inverter-dc.svg', 'showcase-inverter-tran.svg',
    'showcase-inverter.svg', 'showcase-samples.json', 'showcase-spectrum-baseline.svg', 'showcase-spectrum-wider.svg',
    'showcase-spectrum.svg', 'showcase-studio.webp', 'showcase.css', 'showcase.js',
  ].sort();
  assert.deepEqual(inventory.map(file => file.name).sort(), expectedFiles,
    'Public build must contain only the explicit static asset list');
  const serialized = (await readFile(join(directory, 'demo-data.js'), 'utf8')).trim();
  const marker = 'window.__SPICEOWL_DEMO_DATA__=';
  assert.ok(serialized.startsWith(marker) && serialized.endsWith(';'), 'Unexpected demo data wrapper');
  const fixture = JSON.parse(serialized.slice(marker.length, -1));
  assert.equal(fixture.schema, 'spiceowl.pages-demo.v1');
  assert.equal(fixture.initial_state.public_demo, true);
  assert.equal(fixture.initial_state.scripted, true);
  for (const capability of ['real_inference', 'real_simulation', 'dispatch', 'ssh_connectivity_probe'])
    assert.equal(fixture.initial_state.capabilities[capability], false);
  for (const field of ['hosts', 'native_agents', 'questions']) assert.deepEqual(fixture.initial_state[field], []);
  assert.equal(Object.keys(fixture.frames).length, 4);
  assert.ok(Object.values(fixture.frames).flat().every(run => run.synthetic === true), 'Non-synthetic run in public payload');
  const index = await readFile(join(directory, 'index.html'), 'utf8');
  for (const source of index.matchAll(/\b(?:src|href)="([^"]+)"/g)) {
    if (source[1].startsWith('#') || source[1].startsWith('https://github.com/')) continue;
    assert.ok(!source[1].startsWith('/') && !/^[a-z]+:/i.test(source[1]), `Nonrelative static resource: ${source[1]}`);
  }
  return inventory.sort((a, b) => a.name.localeCompare(b.name));
}

async function staticServer(directory, report) {
  const server = createServer(async (req, res) => {
    const path = new URL(req.url, 'http://static.invalid').pathname;
    const send = (status, body, mime = 'text/plain') => { res.writeHead(status, {'Content-Type': mime, 'Cache-Control': 'no-store'}); res.end(body); };
    try {
      if (!['GET', 'HEAD'].includes(req.method) || !path.startsWith(PREFIX)) return send(404, 'Not in Pages project');
      const suffix = decodeURIComponent(path.slice(PREFIX.length));
      const filename = resolve(directory, suffix || 'index.html');
      if (filename !== directory && !filename.startsWith(directory + '/')) return send(404, 'Not found');
      const stat = await lstat(filename);
      if (!stat.isFile() || stat.isSymbolicLink()) return send(404, 'Not found');
      const raw = await readFile(filename);
      send(200, req.method === 'HEAD' ? '' : raw, MIME[extname(filename)] || 'application/octet-stream');
    } catch {
      report.server_errors.push({path, status: 404});
      send(404, 'Not found');
    }
  });
  await new Promise((yes, no) => { server.once('error', no); server.listen(0, '127.0.0.1', yes); });
  return server;
}

const demoState = page => page.evaluate(async () => window.SpiceOwlDemo.request('/api/state'));
const identity = run => ({run_id: run.run_id, attempt_id: run.attempt_id, submission_key: run.submission_key});

async function noOverflow(page, report, label) {
  const dimensions = await page.evaluate(() => {
    const width = document.documentElement.clientWidth;
    return {width, scroll: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
      offenders: [...document.querySelectorAll('body *')].filter(el => {
        const style = getComputedStyle(el), rect = el.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && (rect.right > width + 1 || rect.left < -1);
      }).slice(0, 8).map(el => ({tag: el.tagName, id: el.id, class: String(el.className).slice(0, 100)}))};
  });
  report.layouts.push({label, ...dimensions});
  assert.ok(dimensions.scroll <= dimensions.width + 1, `Horizontal overflow (${label}): ${JSON.stringify(dimensions)}`);
}

async function snapshot(page, report, label) {
  await noOverflow(page, report, label);
  // Full-page capture otherwise projects fixed, offscreen accessibility links
  // into the middle of a long image when an earlier click scrolled the page.
  await page.evaluate(() => window.scrollTo(0, 0));
  const name = `${label}.png`;
  await page.screenshot({path: join(OUTPUT, name), fullPage: true});
  report.screenshots.push(name);
}

async function ready(page, origin) {
  await page.goto(origin + PREFIX, {waitUntil: 'networkidle'});
  await page.locator('#public-demo-banner').waitFor();
  await page.waitForFunction(() => typeof window.SpiceOwlDemo?.request === 'function');
  await page.locator('.home-shell').waitFor();
  assert.ok(new URL(page.url()).pathname.startsWith(PREFIX));
  const state = await demoState(page);
  assert.equal(state.public_demo, true, 'Build must identify public demo state');
  assert.equal(state.runs.length, 4, 'Initial public demo has four original synthetic runs');
  assert.ok(state.runs.every(run => run.synthetic === true), 'Every demo run must retain synthetic provenance');
  return state;
}

async function remotePages(page, report, label) {
  for (const section of ['hosts', 'direct', 'overview', 'local']) {
    if (section === 'local') await page.locator('a[href="#local"]').first().click();
    else await page.locator(`[data-nav="${section}"]`).click();
    const note = page.locator(`[data-public-demo-info="${section}"]`);
    await note.waitFor();
    assert.match(await note.innerText(), /local|locally|connector|本地|本机/i);
    for (const selector of ['#placement-form', '#execution-plan-form', '#native-question-form'])
      assert.equal(await page.locator(selector).isVisible(), false, `Remote form exposed on ${section}`);
    await noOverflow(page, report, `${label}-${section}`);
  }
}

async function interactiveUnifiedWorkspace(page, report, label) {
  // 0. Streamlined 4-item sidebar matches live connected workspace
  assert.equal(await page.locator('aside.sidebar nav[aria-label="Main navigation"] a[data-nav]').count(), 4,
    'Sidebar must expose only the 4 streamlined workspace pages');
  assert.equal(await page.locator('[data-nav="agents"], [data-nav="connections"], [data-nav="live"]').count(), 0,
    'Removed legacy sidebar links must stay absent');
  await page.locator('#connector-trigger').waitFor({state: 'visible'});

  // 1. Page 1 (#quickstart): 2 Agent cards, 1 SSH card, Next Step card
  await page.locator('[data-nav="quickstart"]').click();
  await page.locator('.home-shell').waitFor();
  assert.equal(await page.locator('.home-agent-card').count(), 2, 'Expected 2 clean Agent connect cards');
  await page.locator('[data-action="home-connect-codex"]').waitFor({state: 'visible'});
  await page.locator('[data-action="home-connect-ssh"]').waitFor({state: 'visible'});
  await page.locator('.home-next-card').waitFor({state: 'visible'});
  await noOverflow(page, report, `${label}-quickstart`);
  if (label === 'desktop') await snapshot(page, report, `${label}-home`);

  // 2. Page 2 (#hosts): same 4-KPI + standby card layout as connected server pool
  await page.locator('[data-action="home-next-hosts"]').click();
  await page.locator('[data-public-demo-info="hosts"]').waitFor();
  assert.equal(await page.locator('.pool-kpi-card').count(), 4, 'Expected unified 4 KPI cards on #hosts');
  await noOverflow(page, report, `${label}-hosts`);
  if (label === 'desktop') await snapshot(page, report, `${label}-hosts`);

  // 3. Page 3 (#direct): unified 2-tab Agent-assisted simulation layout (setup + recovery)
  await page.locator('[data-nav="direct"]').click();
  await page.locator('.direct-tabs').waitFor();
  assert.equal(await page.locator('.direct-tab').count(), 2, 'Expected unified 2 tabs on #direct');
  await page.locator('.sim-setup-grid').waitFor();
  await page.locator('.sim-agent-card').waitFor();
  await page.locator('.sim-config-card').waitFor();
  await noOverflow(page, report, `${label}-direct-setup`);
  if (label === 'desktop') await snapshot(page, report, `${label}-direct-setup`);

  await page.locator('.direct-tab[data-tab="recovery"]').click();
  await page.locator('[data-public-demo-info="direct-recovery"]').waitFor();
  await noOverflow(page, report, `${label}-direct-recovery`);

  // 4. Page 4 (#overview): same 4-KPI + standby archive layout as connected overview
  await page.locator('[data-nav="overview"]').click();
  await page.locator('[data-public-demo-info="overview"]').waitFor();
  assert.equal(await page.locator('.stat-card').count(), 4, 'Expected unified 4 KPI cards on #overview');
  await noOverflow(page, report, `${label}-overview-unified`);
  if (label === 'desktop') await snapshot(page, report, `${label}-overview-unified`);

  report.checks.push(`${label}: unified 4-page workspace matches connected shell with zero overflow`);
}

async function exercise(page, origin, report, label) {
  const initial = await ready(page, origin);
  await snapshot(page, report, `${label}-initial`);
  report.checks.push(`${label}: initial home under project subpath`);
  await remotePages(page, report, label);
  await interactiveUnifiedWorkspace(page, report, label);

  // Direct internal routes (#connections and #run/<id>) still preserve offline synthetic replay safety
  await page.goto(origin + PREFIX + '#connections', {waitUntil: 'networkidle'});
  await page.locator('[data-action="add-connection"]').first().click();
  await page.locator('#connection-dialog').waitFor({state: 'visible'});
  const connectionLabel = `Synthetic ${label} test agent`;
  await page.locator('#connection-label').fill(connectionLabel);
  await page.locator('#connection-kind').selectOption('agent');
  await page.locator('#connection-scenario').selectOption('success');
  await page.locator('#connection-form [type="submit"]').click();
  const card = page.locator('.connection-card').filter({hasText: connectionLabel});
  await card.waitFor();
  await card.locator('[data-action="probe"]').click();
  await page.waitForFunction(async expected => (await window.SpiceOwlDemo.request('/api/state')).connections.length === expected,
    initial.connections.length + 1);
  const connected = await demoState(page);
  const connection = connected.connections.find(item => item.label === connectionLabel);
  assert.ok(connection);
  report.checks.push(`${label}: synthetic connection creation and simulated metadata probe`);

  const first = initial.runs.find(run => run.run_id.includes('disconnect')) || initial.runs[0];
  await page.goto(origin + PREFIX + `#run/${first.run_id}`, {waitUntil: 'networkidle'});
  await page.locator('[data-action="advance"]').click();
  await page.waitForFunction(async ({id, count}) => (await window.SpiceOwlDemo.request('/api/state')).runs.find(r => r.run_id === id).events.length > count,
    {id: first.run_id, count: first.events.length});
  const advanced = (await demoState(page)).runs.find(run => run.run_id === first.run_id);
  assert.deepEqual(identity(advanced), identity(first), 'Advancement must preserve synthetic execution identity');
  await page.locator('#diagnostic-connection').selectOption(connection.connection_id);
  await page.locator('#diagnostic-question').fill('Explain the synthetic evidence without executing commands.');
  await page.locator('#diagnose-form [type="submit"]').click();
  await page.locator('#diagnosis-result .proposal').waitFor({timeout: 15000});
  assert.ok((await page.locator('#diagnosis-result .response-text').innerText()).length > 10);
  const diagnosed = await demoState(page);
  assert.ok(diagnosed.sessions.length > initial.sessions.length);
  report.checks.push(`${label}: scenario advancement, preserved identity and scripted diagnosis`);

  await page.reload({waitUntil: 'networkidle'});
  await page.locator('#public-demo-banner').waitFor();
  const reloaded = await demoState(page);
  assert.ok(reloaded.connections.some(item => item.connection_id === connection.connection_id), 'Refresh lost stored demo connection');
  assert.equal(reloaded.runs.find(run => run.run_id === first.run_id).events.length, advanced.events.length, 'Refresh lost replay progress');
  assert.equal(reloaded.sessions.length, diagnosed.sessions.length, 'Refresh lost scripted session');
  report.checks.push(`${label}: same-tab refresh retains sessionStorage demo changes`);

  await page.evaluate(() => window.SpiceOwlDemo.request('/api/demo/reset', {method: 'POST', body: {}}));
  const reset = await demoState(page);
  assert.equal(reset.connections.length, initial.connections.length);
  assert.equal(reset.sessions.length, initial.sessions.length);
  assert.equal(reset.runs.length, initial.runs.length);
  for (const run of initial.runs) assert.equal(reset.runs.find(r => r.run_id === run.run_id).events.length, run.events.length);

  for (const run of reset.runs) {
    await page.goto(origin + PREFIX + `#run/${run.run_id}`, {waitUntil: 'networkidle'});
    let current = (await demoState(page)).runs.find(r => r.run_id === run.run_id);
    for (let step = 0; !current.finished && step < 12; step++) {
      const count = current.events.length;
      await page.locator('[data-action="advance"]').click();
      await page.waitForFunction(async ({id, count}) => {
        const run = (await window.SpiceOwlDemo.request('/api/state')).runs.find(r => r.run_id === id);
        return run.finished || run.events.length > count;
      }, {id: run.run_id, count});
      current = (await demoState(page)).runs.find(r => r.run_id === run.run_id);
    }
    assert.equal(current.finished, true, 'Synthetic scenario did not reach its finite end');
    assert.deepEqual(identity(current), identity(run));
    await noOverflow(page, report, `${label}-terminal-${run.run_id}`);
  }
  const terminal = (await demoState(page)).runs;
  assert.ok(terminal.some(run => run.execution === 'failed'), 'Missing explicit failed-execution scenario');
  assert.ok(terminal.some(run => run.measurement_validity === 'invalid'), 'Missing invalid-measurement scenario');
  assert.ok(terminal.some(run => run.specification_result === 'fail'), 'Missing valid specification-failure scenario');
  report.checks.push(`${label}: all four scenarios reach distinct finite outcomes`);
}

async function storageFallback(page, origin, report) {
  const initial = await ready(page, origin);
  assert.match(await page.locator('#demo-storage-notice').innerText(), /reset.*refresh|memory/i,
    'Unavailable storage must show an explicit refresh-reset notice');
  const first = initial.runs[0];
  await page.goto(origin + PREFIX + `#run/${first.run_id}`, {waitUntil: 'networkidle'});
  await page.locator('[data-action="advance"]').click();
  await page.waitForFunction(async ({id, count}) => (await window.SpiceOwlDemo.request('/api/state')).runs.find(r => r.run_id === id).events.length > count,
    {id: first.run_id, count: first.events.length});
  await page.reload({waitUntil: 'networkidle'});
  await page.locator('#public-demo-banner').waitFor();
  const reloaded = await demoState(page);
  assert.equal(reloaded.runs.find(r => r.run_id === first.run_id).events.length, first.events.length);
  assert.match(await page.locator('#demo-storage-notice').innerText(), /reset.*refresh|memory/i);
  await snapshot(page, report, 'mobile-memory-fallback');
  report.checks.push('Unavailable sessionStorage: explicit memory-only notice, usable replay, documented reset on refresh');
}

async function main() {
  await mkdir(OUTPUT, {recursive: true});
  const report = {schema: 'spiceowl.pages-browser.v1', started_at: new Date().toISOString(),
    status: 'running', prefix: PREFIX, checks: [], layouts: [], screenshots: [], requests: [],
    blocked_requests: [], http_errors: [], page_errors: [], console_errors: [], server_errors: [], websockets: []};
  let server, browser;
  try {
    report.build_files = await inspectBuild(BUILD);
    server = await staticServer(BUILD, report);
    const origin = `http://127.0.0.1:${server.address().port}`;
    report.origin = origin;
    const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
    const launchOpts = {headless: true, args: ['--disable-background-networking']};
    if (process.env.CHROME_PATH) launchOpts.executablePath = process.env.CHROME_PATH;
    else if (process.env.PLAYWRIGHT_CHANNEL) launchOpts.channel = process.env.PLAYWRIGHT_CHANNEL;
    browser = await chromium.launch(launchOpts);
    report.browser = await browser.version();
    async function context(viewport) {
      const value = await browser.newContext({viewport, serviceWorkers: 'block'});
      await value.route('**/*', async route => {
        const request = route.request(), url = new URL(request.url());
        const reason = routePolicy(url, request.method(), origin);
        if (reason) { report.blocked_requests.push({reason, method: request.method(), url: request.url()}); await route.abort(); }
        else { report.requests.push({method: request.method(), path: url.pathname}); await route.continue(); }
      });
      if (typeof value.routeWebSocket === 'function') await value.routeWebSocket('**/*', socket => {
        report.websockets.push(socket.url());
        socket.close();
      });
      value.on('page', page => {
        page.on('pageerror', error => report.page_errors.push(error.message));
        page.on('console', message => { if (message.type() === 'error') report.console_errors.push(message.text()); });
        page.on('response', response => { if (response.status() >= 400) report.http_errors.push({url: response.url(), status: response.status()}); });
        page.on('websocket', socket => report.websockets.push(socket.url()));
        page.on('dialog', dialog => dialog.accept());
      });
      return value;
    }
    for (const [label, viewport] of [['desktop', {width: 1440, height: 1000}], ['mobile', {width: 390, height: 844}]]) {
      const current = await context(viewport);
      const page = await current.newPage();
      try { await exercise(page, origin, report, label); }
      catch (error) {
        try { await page.screenshot({path: join(OUTPUT, `${label}-failure.png`), fullPage: true}); report.screenshots.push(`${label}-failure.png`); } catch {}
        throw error;
      }
      finally { await current.close(); }
    }
    const memoryOnly = await context({width: 390, height: 844});
    await memoryOnly.addInitScript(() => Object.defineProperty(window, 'sessionStorage', {
      configurable: true, get() { throw new DOMException('Synthetic browser-storage denial', 'SecurityError'); },
    }));
    try { await storageFallback(await memoryOnly.newPage(), origin, report); }
    finally { await memoryOnly.close(); }
    assert.equal(report.blocked_requests.length, 0, 'Page attempted a forbidden API, external or root-path request');
    assert.equal(report.http_errors.length, 0, 'Page has HTTP errors');
    assert.equal(report.server_errors.length, 0, 'Static build has missing resources');
    assert.equal(report.page_errors.length, 0, 'Page has uncaught JavaScript errors');
    assert.equal(report.console_errors.length, 0, 'Page has console errors');
    assert.equal(report.websockets.length, 0, 'Static demo must not connect WebSockets');
    report.status = 'passed';
  } catch (error) {
    report.status = 'failed';
    report.error = error.stack || String(error);
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
    if (server) await new Promise(resolve => server.close(resolve));
    report.finished_at = new Date().toISOString();
    await writeFile(join(OUTPUT, 'report.json'), JSON.stringify(report, null, 2) + '\n');
    console.log(JSON.stringify({status: report.status, checks: report.checks.length, screenshots: report.screenshots.length,
      blocked_requests: report.blocked_requests.length, report: join(OUTPUT, 'report.json'), error: report.error?.split('\n')[0]}));
  }
}

if (process.argv.includes('--self-test')) selfTest();
else if (process.argv.includes('--build-check')) {
  const files = await inspectBuild(BUILD);
  console.log(JSON.stringify({status: 'passed', mode: 'offline_public_build_check', files: files.length}));
} else await main();

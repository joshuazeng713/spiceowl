// Real browser accessibility, responsive layout and zero external/API traffic.
// PLAYWRIGHT_MODULE=/existing/playwright/index.mjs node tests/workflow-browser.mjs
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const STATIC = join(ROOT, 'src/spiceowl/web/static');
const OUTPUT = resolve(process.env.WORKFLOW_QA_DIR || join(ROOT, '.spiceowl/workflow-browser-qa'));
const files = {'workflow.html': 'text/html', 'workflow.js': 'text/javascript', 'workflow.css': 'text/css', 'owl.svg': 'image/svg+xml',
  'showcase.css': 'text/css', 'guide.css': 'text/css', 'guide.js': 'text/javascript', 'guide-data.js': 'text/javascript',
  'pool-demo.json': 'application/json', 'showcase-samples.json': 'application/json', 'showcase-evidence.json': 'application/json',
  'showcase-spectrum.svg': 'image/svg+xml', 'showcase-bootstrap-tran.svg': 'image/svg+xml',
  'showcase-inverter-tran.svg': 'image/svg+xml', 'showcase-inverter-dc.svg': 'image/svg+xml', 'showcase-inverter-ac.svg': 'image/svg+xml',
  'demo-poster-film.webp': 'image/webp', 'demo-poster-01.webp': 'image/webp', 'demo-poster-02.webp': 'image/webp',
  'demo-poster-03.webp': 'image/webp', 'demo-poster-04a.webp': 'image/webp', 'demo-poster-04b.webp': 'image/webp',
  'demo-poster-05.webp': 'image/webp', 'demo-poster-06.webp': 'image/webp', 'demo-poster-07.webp': 'image/webp',
  'demo-poster-08.webp': 'image/webp', 'demo-poster-09.webp': 'image/webp',
  'bootstrap-dut.publication.scs': 'text/plain', 'inverter-dut.publication.scs': 'text/plain',
  'bootstrap-tb.publication.scs': 'text/plain', 'bootstrap-evidence.publication.json': 'application/json', 'source-manifest.json': 'application/json'};
const requests = [], blocked = [], errors = [];
const server = createServer(async (req, res) => {
  const path = new URL(req.url, 'http://fixture.invalid').pathname;
  requests.push({method: req.method, path});
  const name = path.replace(/^\/spiceowl\//, '');
  if (req.method !== 'GET' || !Object.hasOwn(files, name)) { res.writeHead(404); res.end(); return; }
  res.writeHead(200, {'Content-Type': files[name], 'Cache-Control': 'no-store'});
  res.end(await readFile(join(STATIC, name)));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
let browser;
try {
  await mkdir(OUTPUT, {recursive: true});
  const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
  browser = await chromium.launch({headless: true, ...(process.env.CHROME_PATH ? {executablePath: process.env.CHROME_PATH} : {channel: 'chrome'}), args: ['--disable-background-networking']});
  const context = await browser.newContext({viewport: {width: 1440, height: 1100}, serviceWorkers: 'block'});
  await context.route('**/*', route => {
    if (new URL(route.request().url()).origin !== origin) { blocked.push(route.request().url()); return route.abort(); }
    return route.continue();
  });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(origin + '/spiceowl/workflow.html', {waitUntil: 'networkidle'});
  assert.equal(await page.locator('[data-stage]').count(), 10);
  assert.equal(await page.locator('[role="tab"]').count(), 4);
  assert.match(await page.locator('#checkpoint-code').innerText(), /awaiting_human_review/);
  for (const [circuit, sources] of [['bootstrap', ['bootstrap-dut', 'bootstrap-tb', 'bootstrap-evidence', 'source-manifest']], ['inverter', ['inverter-dut']]]) {
    await page.locator(`[data-circuit="${circuit}"]`).click();
    for (const source of sources) {
      await page.locator('#publication-source').selectOption(source);
      const filename = source === 'source-manifest' ? 'source-manifest.json' : source + '.publication.' + (source === 'bootstrap-evidence' ? 'json' : 'scs');
      const expected = await readFile(join(STATIC, filename), 'utf8');
      assert.equal(await page.locator('#publication-source-code').textContent(), expected, 'Whole source is displayed');
      const pendingDownload = page.waitForEvent('download');
      await page.locator('#source-download').click();
      const download = await pendingDownload;
      assert.equal(download.suggestedFilename(), filename);
      assert.equal(await readFile(await download.path(), 'utf8'), expected, 'Download preserves exact approved bytes');
    }
  }
  await page.locator('[data-circuit="bootstrap"]').click();
  for (let stage = 0; stage < 10; stage++) {
    await page.locator(`[data-stage="${stage}"]`).click();
    assert.equal(await page.locator('[aria-current="step"]').getAttribute('data-stage'), String(stage));
    for (const tab of ['input', 'output', 'ui', 'tools']) {
      await page.locator(`[data-tab="${tab}"]`).click();
      assert.equal(await page.locator('#detail-panel').getAttribute('aria-labelledby'), 'tab-' + tab);
      if (tab === 'input' || tab === 'output') {
        const payload = JSON.parse(await page.locator('#detail-code').innerText());
        assert.equal(payload.example, true); assert.equal(payload.display_only, true);
      }
    }
  }
  await page.locator('[data-stage="3"]').click();
  await page.locator('input[name="cache"][value="first_scan"]').check();
  await page.locator('[data-tab="output"]').click();
  assert.equal(JSON.parse(await page.locator('#detail-code').innerText()).remote_scan_requested_by_this_path, true);
  await page.locator('input[name="cache"][value="cached"]').check();
  assert.equal(JSON.parse(await page.locator('#detail-code').innerText()).remote_scan_requested_by_this_path, false);
  await page.locator('[data-stage="0"]').click();
  await page.locator('input[name="mode"][value="standalone"]').check();
  assert.equal(JSON.parse(await page.locator('#detail-code').innerText()).mode, 'standalone');
  await page.locator('[data-stage="7"]').click();
  await page.locator('[data-tab="input"]').focus();
  await page.keyboard.press('ArrowRight');
  assert.equal(await page.locator('[role="tab"][aria-selected="true"]').getAttribute('id'), 'tab-output');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'tab-output');
  await page.keyboard.press('End');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'tab-tools');
  await page.keyboard.press('Home');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'tab-input');
  await page.locator('[data-stage="7"]').focus();
  await page.keyboard.press('ArrowDown');
  assert.equal(await page.locator('[aria-current="step"]').getAttribute('data-stage'), '8');
  await page.locator('[data-stage="9"]').click();
  await page.locator('#analysis-operation').selectOption('ac_metrics');
  await page.locator('[data-tab="output"]').click();
  let output = JSON.parse(await page.locator('#detail-code').innerText());
  assert.equal(output.operation, 'ac_metrics'); assert.equal(output.measurement_status, 'not_run');
  assert.equal(output.metrics.unity_gain_bandwidth_hz.value, null);
  await page.locator('[data-circuit="inverter"]').click();
  assert.equal(await page.locator('#checkpoint-code').innerText(), 'structure_only');
  assert.equal(await page.locator('#circuit-diagram svg').getAttribute('role'), 'img');
  await page.locator('[data-stage="7"]').click();
  output = JSON.parse(await page.locator('#detail-code').innerText());
  assert.equal(output.published_case_revision, null);
  await page.locator('[data-circuit="bootstrap"]').click();
  await page.locator('[data-tab="ui"]').click();
  await page.screenshot({path: join(OUTPUT, 'workflow-desktop.png'), fullPage: true});
  for (const width of [390, 320]) {
    await page.setViewportSize({width, height: 844});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1), `No page overflow at ${width}px`);
    await page.locator('[data-stage="9"]').click();
    await page.locator('[data-tab="output"]').click();
    assert.equal(JSON.parse(await page.locator('#detail-code').innerText()).measurement_status, 'not_run');
    await page.screenshot({path: join(OUTPUT, `workflow-${width}.png`), fullPage: true});
  }
  await page.goto(origin + '/spiceowl/workflow.html#stage-8', {waitUntil: 'networkidle'});
  assert.equal(await page.locator('[aria-current="step"]').getAttribute('data-stage'), '7');
  assert.deepEqual(errors, []); assert.deepEqual(blocked, []);
  assert.ok(requests.every(row => row.method === 'GET' && row.path.startsWith('/spiceowl/') && Object.hasOwn(files, row.path.slice('/spiceowl/'.length))));
  const report = {passed: true, checks: ['all 10 stages / 4 tabs', 'complete reviewed source display and 5 exact-byte downloads', 'circuit and mode isolation', 'cache-path switch', 'analysis switch without measurements', 'keyboard tabs/stages', '390px and 320px no overflow', 'subpath and fragment navigation', 'zero external/API/POST traffic'], requests, errors, blocked};
  await writeFile(join(OUTPUT, 'report.json'), JSON.stringify(report, null, 2));
  console.log('PASS: workflow browser checks; screenshots in ' + OUTPUT);
} finally {
  await browser?.close();
  await new Promise(resolve => server.close(resolve));
}

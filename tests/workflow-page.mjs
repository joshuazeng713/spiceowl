// Pure public-page data/contract checks. Browser interactions have their own test.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';

const source = await readFile(new URL('../src/spiceowl/web/static/workflow.js', import.meta.url), 'utf8');
const html = await readFile(new URL('../src/spiceowl/web/static/workflow.html', import.meta.url), 'utf8');
const {CHECKPOINT, CIRCUITS, STAGES, RESPONSIBILITIES, PUBLICATION_SOURCES, PUBLIC_EVIDENCE, stageView} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
assert.equal(STAGES.length, 10);
assert.equal(CHECKPOINT.revision, 10);
assert.equal(CHECKPOINT.state, 'awaiting_human_review');
assert.equal(CHECKPOINT.solver_result_available, false);
assert.equal(CHECKPOINT.measurement_result_available, false);
assert.deepEqual(CIRCUITS.bootstrap.models, ['cfmom_2t', 'nch_lvt_mac', 'nch_ulvt_mac', 'pch_ulvt_mac']);
assert.deepEqual(CIRCUITS.bootstrap.sections, ['pre_simu', 'TTMacro_MOS_MOSCAP', 'TT_MOM']);
assert.equal(CIRCUITS.bootstrap.samples, 4096);
assert.deepEqual(CIRCUITS.inverter.ports, ['CLK', 'CLKN', 'VDD', 'VSS']);
assert.equal(CIRCUITS.inverter.sections, null, 'Do not reuse another circuit’s model selection');
assert.equal(RESPONSIBILITIES.length, 5);
for (const circuit of ['bootstrap', 'inverter']) for (let index = 0; index < 10; index++) {
  const view = stageView(index, {circuit});
  assert.ok(view.title && view.description && view.ai && view.engine && view.guard && view.tools);
  assert.ok(view.publishedEvidence);
  assert.ok(view.ui.length >= 2);
  for (const direction of ['input', 'output']) {
    assert.equal(view[direction].example, true);
    assert.equal(view[direction].display_only, true);
    assert.equal(view[direction].evidence_kind, 'contract_illustration_not_service_receipt');
    assert.deepEqual(JSON.parse(JSON.stringify(view[direction])), view[direction]);
  }
}
assert.equal(stageView(0, {mode: 'embedded_in_circuitgit'}).output.second_embedded_agent_service, false);
assert.equal(stageView(0, {mode: 'standalone'}).output.connection_status, 'not_checked');
assert.equal(stageView(3, {cache: 'cached'}).output.remote_scan_requested_by_this_path, false);
assert.equal(stageView(3, {cache: 'first_scan'}).output.remote_scan_requested_by_this_path, true);
assert.equal(stageView(3).output.measured_lookup_latency_ms, null);
assert.equal(stageView(5).input.conditions.supply_v, 1);
assert.equal(stageView(5).input.sampling.points, 4096);
assert.equal(stageView(5).input.sampling.sample_rate_hz, 1e9);
assert.equal(stageView(5, {circuit: 'inverter'}).input.conditions.supply_v, null);
assert.equal(stageView(4).output.use_in_this_case, 'not_applicable');
assert.match(stageView(0, {mode: 'standalone'}).tools, /spiceowl serve --agent-mode standalone/);
assert.match(stageView(0).tools, /spiceowl serve --agent-mode embedded_in_circuitgit/);
assert.equal(stageView(0).output.real_provider_inference, 'unknown');
assert.equal(PUBLIC_EVIDENCE.freshStaticAudit.readyToRun, false);
assert.equal(PUBLIC_EVIDENCE.freshStaticAudit.requiredModelsMatched, 4);
assert.equal(PUBLIC_EVIDENCE.testbench.inputHz, PUBLIC_EVIDENCE.testbench.coherentBin * PUBLIC_EVIDENCE.testbench.sampleRateHz / PUBLIC_EVIDENCE.testbench.sampleCount);
assert.equal(PUBLICATION_SOURCES.length, 5);
for (const item of PUBLICATION_SOURCES) {
  const raw = await readFile(new URL('../src/spiceowl/web/static/' + item.filename, import.meta.url));
  assert.equal(item.text, raw.toString('utf8'), 'Entire public source is shown without shortening');
  assert.equal(createHash('sha256').update(raw).digest('hex'), item.publicationSha256);
  assert.equal(item.submitted, false);
  if (item.originalSourceSha256) {
    const body = item.text.split('\n').slice(2).join('\n');
    assert.equal(createHash('sha256').update(body).digest('hex'), item.originalSourceSha256);
    assert.match(item.text, /^\/\/ PUBLICATION EXCERPT/);
  }
}
assert.equal(stageView(7).output.published_case_state, 'awaiting_human_review');
assert.equal(stageView(7, {circuit: 'inverter'}).output.published_case_revision, null);
assert.equal(stageView(8).output.job_id, null);
assert.equal(stageView(8).output.automatic_resubmit_on_disconnect, false);
for (const operation of ['spec_analyze', 'coherent_fft', 'ac_metrics', 'dc_op_metrics', 'tran_metrics']) {
  const result = stageView(9, {analysis: operation}).output;
  assert.equal(result.operation, operation);
  assert.equal(result.measurement_status, 'not_run');
  assert.equal(result.specification_result, 'not_evaluated');
  assert.ok(Object.values(result.metrics).every(metric => metric.value === null && metric.status === 'not_measured'));
}
for (const index of [-1, 10, 0.5, NaN]) assert.throws(() => stageView(index), RangeError);
assert.match(html, /lang="zh-CN"/);
assert.match(html, /connect-src 'none'/);
assert.match(html, /role="tablist"/);
assert.match(html, /role="tabpanel"/);
assert.match(html, /aria-live="polite"/);
assert.match(html, /class="skip-link"/);
assert.doesNotMatch(source, /\bfetch\s*\(|XMLHttpRequest|WebSocket|localStorage|sessionStorage|EventSource/);
assert.doesNotMatch(html, /(?:src|href)="(?:https?:)?\/\//);
assert.doesNotMatch(source + html, /\/Users\/|\/home\/|thu-(?:shu|wei|wu|tang|song|yuan|ming|qing)/);
console.log('PASS: 10 workflow stages, explicit illustrative JSON, exact public structure, no invented measurements or external IO.');

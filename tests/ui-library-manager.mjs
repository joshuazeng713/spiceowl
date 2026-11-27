import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../src/spiceowl/web/static/app.js', import.meta.url), 'utf8');
const css = fs.readFileSync(new URL('../src/spiceowl/web/static/app.css', import.meta.url), 'utf8');
const cut = source.lastIndexOf('try { await refresh(); render(); }');
assert(cut > 0, 'Expected app.js bootstrap marker');
const body = source.slice(0, cut);

const sandbox = {
  console,
  URL,
  URLSearchParams,
  TextEncoder,
  crypto: {randomUUID: () => '00000000-0000-4000-8000-000000000001'},
  setInterval: () => 0,
  clearInterval: () => {},
  setTimeout: () => 0,
  clearTimeout: () => {},
  sessionStorage: {getItem: () => null, setItem: () => {}, removeItem: () => {}},
  localStorage: {getItem: () => null, setItem: () => {}, removeItem: () => {}},
  location: {hash: '#hosts', search: '', pathname: '/', origin: 'http://127.0.0.1:8765'},
  history: {replaceState: () => {}},
  window: {
    location: {origin: 'http://127.0.0.1:8765'},
    addEventListener: () => {},
    confirm: () => true,
  },
  document: {
    hidden: false,
    documentElement: {dataset: {}},
    activeElement: null,
    addEventListener: () => {},
    querySelector: () => ({addEventListener: () => {}, innerHTML: '', textContent: '', hidden: false}),
    querySelectorAll: () => [],
  },
  fetch: async () => { throw new Error('unexpected fetch'); },
  EventSource: class { addEventListener() {} close() {} },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(`(async () => {\n${body}\nglobalThis.__exports = {
  setState: s => { state = s; },
  setHostLive: fn => fn(hostLiveState),
  setSimSource: fn => fn(simSourceState),
  setLibMgr: fn => fn(libMgrState),
  setDirectDraft: fn => fn(directDraft),
  setDirectState: fn => fn(directState),
  setOverviewState: fn => fn(overviewState),
  setCodex: fn => fn(codexPanelState),
  renderMySimulationsStrip,
  renderHostLiveCard,
  hosts,
  quickstartPage,
  directPage,
  directAgentSetupPanel,
  overview,
  groupDirectRunsHierarchy,
  renderCodexActivity,
  renderCleanDirectTimeline,
  codexApprovalCard,
  applyAgentUiPatch,
  getDirectDraft: () => directDraft,
  getBatchDraft: () => batchDraft,
  getSimSource: () => simSourceState,
  renderMarkdownHtml,
  stripSimAgentContextEnvelope,
  stripRnlCommentsForAgent,
  buildSimAgentWorkspaceContext,
  renderLibraryManagerDialog,
  buildDirectPreparePayload,
  applyCircuitGitEntry,
  isCircuitGitManagedEntry,
  prepareLocalImportFiles,
  selectImportEntry,
  scoreEntryCandidate,
}; })()`, sandbox);

const ex = sandbox.__exports;

// 1. Page 2 CSS has no 390px wrap constraint on .host-page-actions
assert.doesNotMatch(css, /\.host-page-actions\s*\{[^}]*max-width\s*:\s*390px/, '.host-page-actions must stay single-row on desktop');

// 2. Page 2 hides empty "我的仿真" strip and omits "暂无我的仿真" heading in cards
ex.setState({
  hosts: [{host_id: 'sim-node-a', label: 'EDA (sim-node-a)', last_resources: null, last_probe: null}],
  runs: [], connections: [], sessions: [], native_agents: [], questions: [],
  agent_channel: {mode: 'standalone'},
});
ex.setHostLive(st => {
  st.loaded = true;
  st.loading = false;
  st.mySimulations = [];
  st.summary = {reachable_hosts: 1, total_cpus: 64, avg_cpu_busy_percent: 25, total_memory_bytes: 128 * 2**30, available_memory_bytes: 96 * 2**30, active_simulations: 2, my_active_simulations: 0, active_eda_tools: 4, managed_active_runs: 0};
  st.byId = new Map([['sim-node-a', {
    ok: true,
    load_level: 'moderate',
    resources: {cpu_busy_percent: 25, cpu_count: 64, load_1m: 8, load_5m: 7, memory_total_bytes: 128 * 2**30, memory_available_bytes: 96 * 2**30, home_disk_available_bytes: 500 * 2**30},
    simulations: {active_count: 2, my_active_count: 0, eda_tool_count: 2, by_tool: {spectre: 2}, processes: [{tool: 'spectre', category: 'simulation', user: 'colleague', is_me: false, cpu_percent: 200, rss_bytes: 1024**3, elapsed: '01:10'}]},
    managed_runs: [],
  }]]);
});
assert.equal(ex.renderMySimulationsStrip(), '', 'Empty user simulation strip must be completely hidden');
const cardHtml = ex.renderHostLiveCard({host_id: 'sim-node-a', label: 'EDA (sim-node-a)'});
assert(!cardHtml.includes('暂无我的仿真'), 'Card must not display redundant "暂无我的仿真" heading');
assert(cardHtml.includes('查看其他用户仿真与 EDA 进程'), 'Other users processes stay collapsible in <details>');

// 3. Local import helpers (reused from CircuitGit) rank top-level entry & drop sensitive files
const bundle = ex.prepareLocalImportFiles([
  {path: 'my_adc/models/corner.scs', text: 'section tt\nendsection tt\n'},
  {path: 'my_adc/tb_adc.scs', text: 'simulator lang=spectre\ninclude "models/corner.scs"\n'},
  {path: 'my_adc/.ssh/id_rsa', text: 'PRIVATE'},
  {path: 'my_adc/flexlm.lic', text: 'SERVER'},
]);
assert.equal(bundle.projectName, 'my_adc');
assert.equal(bundle.defaultEntry, 'tb_adc.scs');
const chosen = ex.selectImportEntry({filesByPath: bundle.filesByPath, entryPath: bundle.defaultEntry, projectName: bundle.projectName});
assert.equal(chosen.fileName, 'tb_adc.scs');
assert.deepEqual(Object.keys(chosen.supportFiles), ['models/corner.scs']);

// 4. Page 3 shows standalone Library Manager, omits useless zero-PDK templates, and auto-collapses Agent guide after first message
const standaloneHtml = ex.directAgentSetupPanel([{host_id: 'sim-node-a', label: 'Sim Node A'}]);
assert(standaloneHtml.includes('data-standalone-library-entry="true"'), 'Standalone mode shows Library Manager entry');
assert(!standaloneHtml.includes('data-circuitgit-entry="active"'), 'Standalone mode does not show CircuitGit banner');
assert(!standaloneHtml.includes('sim-tpl-strip') && !standaloneHtml.includes('快速零 PDK 模板'), 'Useless zero-PDK template strip must be removed');
assert(standaloneHtml.includes('id="sim-agent-guide"'), 'Agent guide renders before first message');
assert(!standaloneHtml.includes('id="sim-agent-guide" class="sim-agent-guide" hidden'), 'Agent guide is visible before first message');

// Once the user sends their first message, Agent guide collapses and tool calls stay collapsed in CircuitGit clean style
ex.setCodex(c => {
  c.status = {connected: true, state: 'complete', thread_id: 'th-1'};
  c.selectedThread = 'th-1';
  c.threads = [{
    id: 'th-1',
    items: [
      {id: 'u1', type: 'user', text: '[SPICEOWL_CTX]\nctx\n[/SPICEOWL_CTX]\n\n帮我看看当前网表', status: 'accepted'},
      {id: 't1', type: 'tool', name: 'spiceowl_direct_read', text: '{}', status: 'completed'},
      {id: 'a1', type: 'assistant', text: '结论：**配置正常**\n- 建议开启 `save allpub`', status: 'completed'},
    ],
  }];
});
const afterMsgHtml = ex.directAgentSetupPanel([{host_id: 'sim-node-a', label: 'Sim Node A'}]);
assert(afterMsgHtml.includes('id="sim-agent-guide" class="sim-agent-guide" hidden'), 'Agent guide must collapse after first message');
const cleanTimeline = ex.renderCleanDirectTimeline([{
  id: 'u1', type: 'user', text: '[SPICEOWL_CTX]\nhidden\n[/SPICEOWL_CTX]\n\n检查网表', status: 'accepted',
}, {
  id: 't1', type: 'tool', name: 'spiceowl_list_ic_gateways', text: '{}', status: 'completed',
}, {
  id: 'a1', type: 'assistant', text: '结论：**正常**\n<script>alert(1)</script>', status: 'completed',
}]);
assert(!cleanTimeline.includes('[SPICEOWL_CTX]'), 'Context envelope must be stripped from user chat bubble');
assert(cleanTimeline.includes('<details class="sim-ai-progress">'), 'Tool calls collapse into single clean progress summary');
assert(!cleanTimeline.includes('<details class="sim-ai-progress" open>'), 'Tool progress must be collapsed by default');
assert(cleanTimeline.includes('<strong>正常</strong>'), 'Assistant Markdown must render cleanly');
assert(!cleanTimeline.includes('<script>'), 'Markdown renderer must escape raw HTML');
const strippedNet = ex.stripRnlCommentsForAgent('// title\n// @RNL {\n//   #R0 @PLACE(0,0)\n// }\nR0 (a 0) resistor r=1k');
assert(!strippedNet.includes('@RNL') && strippedNet.includes('R0 (a 0) resistor r=1k'), 'RNL comments stripped from agent context');

// 4b. Resolved approvals vanish immediately and Stop button appears whenever AI turn is running
sandbox.location.hash = '#direct';
const domEls = {
  '#codex-timeline': {innerHTML: '', scrollHeight: 0, scrollTop: 0, clientHeight: 100},
  '#codex-approvals': {innerHTML: ''},
  '#codex-stop': {hidden: true, disabled: false},
  '#codex-send': {disabled: false},
  '#codex-message': {disabled: false},
  '#sim-agent-guide': {hidden: false},
  '#codex-runtime-state': {innerHTML: ''},
  '#codex-notices': {innerHTML: ''},
};
const origQuerySelector = sandbox.document.querySelector;
sandbox.document.querySelector = sel => domEls[sel] || null;
ex.setCodex(c => {
  c.status = {connected: true, state: 'awaiting_approval', thread_id: 'th-1', turn_id: 'turn-1'};
  c.streamState = 'connected';
  c.selectedThread = 'th-1';
  c.approvals = new Map([
    ['ap-resolved', {approval_id: 'ap-resolved', thread_id: 'th-1', turn_id: 'turn-1', status: 'resolved', params: {tool: 'spiceowl_probe_host', arguments: {host_id: 'sim-node-a'}}}],
    ['ap-pending', {approval_id: 'ap-pending', thread_id: 'th-1', turn_id: 'turn-1', status: 'pending', can_approve: true, can_deny: true, params: {tool: 'spiceowl_direct_control', arguments: {operation: 'launch'}}}],
  ]);
});
ex.renderCodexActivity();
assert(!domEls['#codex-approvals'].innerHTML.includes('ap-resolved'), 'Resolved approval card must disappear immediately');
assert(domEls['#codex-approvals'].innerHTML.includes('ap-pending'), 'Active pending approval must still render');
assert(!domEls['#codex-approvals'].innerHTML.includes('<details open>'), 'Page 3 pending approval params should stay collapsed by default');
assert.equal(domEls['#codex-stop'].hidden, false, 'Stop button must be visible while AI turn is active');
assert.equal(domEls['#codex-send'].disabled, true, 'Send button stays disabled while AI turn is active');
ex.setCodex(c => { c.status = {connected: true, state: 'interrupted', thread_id: 'th-1', turn_id: null}; c.pending = null; });
ex.renderCodexActivity();
assert.equal(domEls['#codex-stop'].hidden, true, 'Stop button hides once AI turn is interrupted/idle');
assert(domEls['#codex-runtime-state'].innerHTML.includes('已暂停'), 'Interrupted state displays 已暂停 chip');
sandbox.document.querySelector = origQuerySelector;
sandbox.location.hash = '#hosts';

// 4c. Page 1 (#quickstart) shows inline Chinese recovery unlock button when recovery_required is true
ex.setCodex(c => {
  c.status = {connected: false, state: 'unknown', recovery_required: true, request_key: 'codex-old-1'};
  c.recoverySnapshot = null;
});
const qsRecHtml = ex.quickstartPage();
assert(qsRecHtml.includes('上次会话中断待解锁'), 'Page 1 must display clear recovery status badge');
assert(qsRecHtml.includes('data-action="codex-inspect-recovery"'), 'Page 1 must offer inline recovery unlock action');
assert(!qsRecHtml.includes('data-action="home-connect-codex"'), 'Page 1 must not show broken connect button while recovery_required');

// 4d. Global [hidden] rule exists, live thinking/completed indicators render, and AI UI patches update form & schematic live
assert.match(css, /\[hidden\]\s*\{\s*display\s*:\s*none\s*!important/, 'Global [hidden] rule must override button display');
ex.setCodex(c => {
  c.status = {connected: true, state: 'running', thread_id: 'th-1', turn_id: 'turn-2', recovery_required: false};
  c.turnStartedAt = Date.now() - 2300;
});
const thinkingHtml = ex.renderCleanDirectTimeline([{id: 'u1', type: 'user', text: '帮我找网表', status: 'accepted'}]);
assert(thinkingHtml.includes('AI 正在思考中…'), 'Active turn must render live thinking status');
assert(thinkingHtml.includes('id="sim-ai-elapsed"'), 'Active turn must render live elapsed timer');

ex.setCodex(c => {
  c.status = {connected: true, state: 'complete', thread_id: 'th-1', turn_id: 'turn-2', recovery_required: false};
  c.turnStartedAt = null;
  c.lastTurnDurationMs = 3400;
});
const doneHtml = ex.renderCleanDirectTimeline([
  {id: 'u1', type: 'user', text: '帮我找网表', status: 'accepted'},
  {id: 'a1', type: 'assistant', text: '已找到并打开。', status: 'completed', turnStatus: 'completed', durationMs: 3400},
]);
assert(doneHtml.includes('✓ 已完成'), 'Completed turn must render subtle completion indicator');
assert(doneHtml.includes('3.4 秒'), 'Completed turn must render duration');

// When bridge enters recovery_required / unknown state, stale running tools must NOT stay stuck showing "正在执行"
ex.setCodex(c => {
  c.status = {connected: true, state: 'unknown', recovery_required: true, thread_id: 'th-1', turn_id: 'turn-3'};
});
const brokenHtml = ex.renderCleanDirectTimeline([
  {id: 'u1', type: 'user', text: '提交仿真', status: 'accepted'},
  {id: 't1', type: 'tool', name: 'spiceowl_direct_control', text: '{}', status: 'running'},
]);
assert(!brokenHtml.includes('正在执行'), 'Stale running tool must stop displaying 正在执行 after bridge error');
assert(brokenHtml.includes('已中断') && brokenHtml.includes('异常中断'), 'Recovery state must surface clear interrupted warning');
ex.setCodex(c => {
  c.status = {connected: true, state: 'complete', recovery_required: false, thread_id: 'th-1', turn_id: 'turn-2'};
});

const appliedOpen = ex.applyAgentUiPatch({
  revision: 1,
  kind: 'library_open',
  payload: {
    host_id: 'sim-node-a',
    mode: 'schematic',
    payload: {
      ssh_target: 'sim-node-a',
      lib_name: 'research4',
      cell_name: 'tb_const_gain_amplifier',
      view_name: 'schematic',
      file_name: 'tb_const_gain_amplifier.scs',
      remote_path: '/work/research4/tb_const_gain_amplifier/schematic',
      text: 'simulator lang=spectre\nR0 (a 0) resistor r=1k\n',
      support_files: {},
      maestro_applied: true,
      maestro_config: {view_name: 'maestro', analyses: [{type: 'tran', stop: '100n'}], variables: {}, corners: [], model_files: []},
      rnl_applied: true,
      rnl_stats: {applied: true, cells: 1, placed: 5, wires: 8, ports: 2, labels: 3},
    },
  },
});
assert.equal(appliedOpen, true);
assert.equal(ex.getSimSource().cellName, 'tb_const_gain_amplifier');
assert.equal(ex.getSimSource().maestroApplied, true);
assert(ex.getDirectDraft().netlist_text.includes('R0 (a 0)'));

const appliedCfg = ex.applyAgentUiPatch({
  revision: 2,
  kind: 'ui_configure',
  payload: {
    host_id: 'sim-node-a',
    cpus: 16,
    memory_gib: 32,
    simulator: 'spectre',
    backend: 'lsf',
    scheduler: {queue: 'analog_fast', exclusive: true},
    recovery_policy: {enabled: true, max_memory_gib: 128},
    batch: {kind: 'pvt', corners: ['tt', 'ss', 'ff'], max_concurrency: 8},
  },
});
assert.equal(appliedCfg, true);
assert.equal(ex.getDirectDraft().cpus, 16);
assert.equal(ex.getDirectDraft().memory_gib, 32);
assert.equal(ex.getDirectDraft().backend, 'lsf');
assert.equal(ex.getDirectDraft().queue, 'analog_fast');
assert.equal(ex.getDirectDraft().rec_enabled, true);
assert.equal(ex.getBatchDraft().max_concurrency, 8);

// 5. When invoked by CircuitGit, Page 3 directly reuses CircuitGit entry AND CircuitGit Agent channel
const applied = ex.applyCircuitGitEntry({
  schema: 'spiceowl.circuitgit-entry.v1',
  source: 'circuitgit',
  project_id: 'pipetisar-real',
  project_name: '1G 12-bit ADC',
  cell_id: 'TB_ADC_TOP',
  entry_path: 'TB_ADC_TOP.scs',
  netlist_text: 'simulator lang=spectre\ninclude "sub.scs"\n',
  support_files: {'sub.scs': 'subckt sub a\nends sub\n'},
  source_commit: 'b'.repeat(40),
  simulator: 'spectre',
}, {silent: true});
assert.equal(applied, true);
assert.equal(ex.isCircuitGitManagedEntry(), true);
const cgHtml = ex.directAgentSetupPanel([{host_id: 'sim-node-a', label: 'Sim Node A'}]);
assert(cgHtml.includes('data-circuitgit-entry="active"'), 'CircuitGit mode displays reused CircuitGit entry banner');
assert(cgHtml.includes('data-sim-agent-mode="circuitgit"'), 'CircuitGit mode directly reuses CircuitGit Agent channel');
assert(!cgHtml.includes('data-standalone-library-entry="true"'), 'CircuitGit mode hides redundant standalone Library Manager picker');

ex.setDirectDraft(d => { d.host_id = 'sim-node-a'; });
const prepPayload = ex.buildDirectPreparePayload();
assert.deepEqual({...prepPayload.support_files}, {'sub.scs': 'subckt sub a\nends sub\n'});
assert.equal(prepPayload.source_commit, 'b'.repeat(40));

// 6. Library Manager SSH dialog renders Virtuoso schematic card & Maestro checkbox cleanly
let renderedLibHtml = '';
sandbox.document.querySelector = sel => sel === '#library-manager-content'
  ? {set innerHTML(v) { renderedLibHtml = v; }}
  : {addEventListener: () => {}, innerHTML: '', textContent: '', hidden: false};
ex.setLibMgr(m => {
  m.tab = 'ssh';
  m.hostId = 'sim-node-a';
  m.cwd = '/work/adc_lib/tb_amp/schematic';
  m.entries = [];
  m.includeMaestro = true;
  m.selectedMaestroView = 'maestro';
  m.virtuosoContext = {
    is_schematic_view: true,
    lib_name: 'adc_lib',
    cell_name: 'tb_amp',
    view_name: 'schematic',
    schematic_dir: '/work/adc_lib/tb_amp/schematic',
    cds_lib: '/work/cds.lib',
    maestro_views: [{
      name: 'maestro',
      kind: 'maestro',
      path: '/work/adc_lib/tb_amp/maestro',
      summary: {
        analyses: [{type: 'tran', stop: '20n'}],
        model_files_count: 3,
        variables_count: 5,
        corners: [{name: 'CTT', enabled: true}],
        outputs_count: 4,
      },
    }],
  };
});
ex.renderLibraryManagerDialog();
assert(renderedLibHtml.includes('data-virtuoso-schematic-card="true"'), 'Virtuoso schematic card must render inside schematic view dir');
assert(renderedLibHtml.includes('id="lib-mgr-preserve-rnl"'), 'RNL geometry preservation checkbox must render');
assert(renderedLibHtml.includes('id="lib-mgr-include-maestro"'), 'Maestro import toggle checkbox must render');
assert(renderedLibHtml.includes('从 Schematic 导出并导入'), 'Export schematic CTA must render');
assert.match(css, /\.lib-mgr-check input\[type="checkbox"\][\s\S]*width:\s*auto/, 'Checkbox width must not stretch');

// 7. Page 3 (#direct) keeps ONLY tabs 1 (Agent setup) and 5 (Night watch & recovery -> renumbered 2) in live mode
ex.setDirectState(st => {
  st.activeTab = 'runs'; // removed tab must coerce cleanly back to setup
  st.runs = [];
  st.recoveryReports = [];
});
const liveDirectHtml = ex.directPage();
assert(liveDirectHtml.includes('1. Agent 辅助配置'), 'Live Page 3 keeps Agent setup tab');
assert(liveDirectHtml.includes('2. 夜间值守与恢复'), 'Live Page 3 keeps Night watch & recovery tab renumbered to 2');
assert(!liveDirectHtml.includes('波形与周期切片'), 'Live Page 3 must remove useless waveforms tab');
assert(!liveDirectHtml.includes('PVT / MC 批量'), 'Live Page 3 must remove useless PVT/MC batch tab');
assert(!liveDirectHtml.includes('任务列表与控制'), 'Live Page 3 must remove redundant runs tab');
assert(!liveDirectHtml.includes('data-tab="waveforms"'), 'Live Page 3 must not expose waveforms tab button');
assert(!liveDirectHtml.includes('data-tab="batches"'), 'Live Page 3 must not expose batches tab button');
assert(!liveDirectHtml.includes('data-tab="runs"'), 'Live Page 3 must not expose runs tab button');

// 8. Page 4 (#overview) shows real Project -> TB -> Submission-Group history archive in live mode (zero demo buttons)
ex.setDirectState(st => {
  st.loaded = true;
  st.loading = false;
  st.runs = [];
});
const emptyOvHtml = ex.overview();
assert(!emptyOvHtml.includes('载入离线演示数据'), 'Live overview must never show synthetic demo loader');
assert(!emptyOvHtml.includes('加载演示数据'), 'Live overview topbar must not show synthetic demo button');
assert(emptyOvHtml.includes('还没有历史仿真记录'), 'Live overview empty state explains real history archive');

const sampleRuns = [
  {
    run_id: 'amp-pvt-01-tt',
    host_id: 'sim-node-a',
    backend: 'ssh_direct',
    simulator: 'spectre',
    state: 'simulator_complete_pending_measurements',
    collection_phase: 'complete',
    collection: {file_count: 5},
    limits: {cpu_count: 4, memory_bytes: 8 * 2**30},
    created_at: '2026-11-26T10:00:00Z',
    updated_at: '2026-11-26T10:05:00Z',
    organization: {
      project: 'RESEARCH4',
      library: 'RESEARCH4',
      testbench: 'tb_const_gain_amplifier',
      view: 'schematic',
      submission_group_id: 'turn:t1',
      submission_label: '3-Corner Scan',
      submission_kind: 'multi_run',
      case_label: 'tt / 1.2V / 27C',
    },
  },
  {
    run_id: 'amp-pvt-01-ss',
    host_id: 'sim-node-b',
    backend: 'ssh_direct',
    simulator: 'spectre',
    state: 'running_observed',
    limits: {cpu_count: 4, memory_bytes: 8 * 2**30},
    created_at: '2026-11-26T10:00:01Z',
    updated_at: '2026-11-26T10:06:00Z',
    organization: {
      project: 'RESEARCH4',
      library: 'RESEARCH4',
      testbench: 'tb_const_gain_amplifier',
      view: 'schematic',
      submission_group_id: 'turn:t1',
      submission_label: '3-Corner Scan',
      submission_kind: 'multi_run',
      case_label: 'ss / 1.08V / 125C',
    },
  },
  {
    run_id: 'sar-single-01',
    host_id: 'sim-node-a',
    backend: 'lsf',
    simulator: 'spectre',
    state: 'prepared',
    limits: {cpu_count: 2, memory_bytes: 4 * 2**30},
    created_at: '2026-11-25T09:00:00Z',
    updated_at: '2026-11-25T09:00:00Z',
    organization: {
      project: 'ADC_TOP',
      testbench: 'tb_sar_logic',
      submission_group_id: 'sar-single-01',
      submission_label: 'sar-single-01',
      submission_kind: 'single',
    },
  },
];
const grouped = ex.groupDirectRunsHierarchy(sampleRuns, {});
assert.equal(grouped.totalProjects, 2, 'Expected 2 projects');
assert.equal(grouped.totalTestbenches, 2, 'Expected 2 testbenches');
assert.equal(grouped.totalSubmissions, 2, 'Two runs in turn:t1 must co-archive into 1 submission (+ 1 single = 2)');
const resProj = grouped.projects.find(p => p.project === 'RESEARCH4');
assert.equal(resProj.testbenches[0].submissions.length, 1);
assert.equal(resProj.testbenches[0].submissions[0].runs.length, 2);

ex.setDirectState(st => { st.runs = sampleRuns; st.loaded = true; });
const liveOvHtml = ex.overview();
assert(liveOvHtml.includes('data-live-history-archive="true"'), 'Live overview renders hierarchical archive');
assert(liveOvHtml.includes('data-project-name="RESEARCH4"'), 'Project section renders');
assert(liveOvHtml.includes('data-tb-name="tb_const_gain_amplifier"'), 'TB section renders');
assert(liveOvHtml.includes('同次提交 · 2 次仿真'), 'Multi-run submission badge co-archives runs');
assert(liveOvHtml.includes('data-action="overview-open-run"'), 'History rows link directly to run control');

// Selecting a run opens inline detail & control panel directly inside #overview
ex.setOverviewState(s => { s.selectedRunId = 'sar-single-01'; });
const detailOvHtml = ex.overview();
assert(detailOvHtml.includes('data-overview-run-detail="sar-single-01"'), 'Selected run renders inline detail panel in overview');
assert(detailOvHtml.includes('data-action="overview-launch-now"'), 'Prepared run shows one-click launch CTA');
assert(detailOvHtml.includes('data-action="overview-handoff-agent"'), 'Detail panel lets user hand run back to workbench & Agent');
assert(detailOvHtml.includes('data-action="overview-delete-run"'), 'Detail panel exposes safe delete for non-active records');
assert(detailOvHtml.includes('ov-prepared-hint'), 'Prepared run displays clear explanation banner');
ex.setDirectState(st => { st.agentLinkedRunId = 'sar-single-01'; });
const ctxWithRun = ex.buildSimAgentWorkspaceContext();
assert(ctxWithRun.includes('run_id=sar-single-01'), 'Agent context includes linked historical/prepared run');
assert(ctxWithRun.includes('已有本地仿真任务列表'), 'Agent context includes recent local runs summary');
ex.setDirectState(st => { st.agentLinkedRunId = ''; });
ex.setOverviewState(s => { s.selectedRunId = ''; });

// Filter narrows by project cleanly
ex.setOverviewState(s => { s.project = 'ADC_TOP'; });
const filteredOvHtml = ex.overview();
assert(filteredOvHtml.includes('tb_sar_logic'));
assert(!filteredOvHtml.includes('data-tb-name="tb_const_gain_amplifier"'));
ex.setOverviewState(s => { s.project = ''; s.testbench = ''; s.status = ''; s.query = ''; });

// 9. buildDirectPreparePayload automatically forwards Project / Library / TB metadata
ex.setSimSource(s => {
  s.origin = 'ssh';
  s.projectName = 'RESEARCH4';
  s.libraryName = 'RESEARCH4';
  s.cellName = 'tb_const_gain_amplifier';
  s.viewId = 'schematic';
});
const metaPayload = ex.buildDirectPreparePayload();
assert.equal(metaPayload.project_name, 'RESEARCH4');
assert.equal(metaPayload.library_name, 'RESEARCH4');
assert.equal(metaPayload.cell_name, 'tb_const_gain_amplifier');
assert.equal(metaPayload.view_name, 'schematic');
assert.equal(metaPayload.source_origin, 'ssh');

console.log('ui-library-manager.mjs passed');

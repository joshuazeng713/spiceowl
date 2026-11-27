const $ = (selector) => document.querySelector(selector);
const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const title = (value) => String(value ?? 'unknown').replaceAll('_', ' ').replace(/^./, c => c.toUpperCase());
let state = {connections: [], runs: [], sessions: [], hosts: [], native_agents: [], questions: []};
let csrf = '';
let selectedSession = null;
let sessionView = null;
let selectedConnection = '';
let draftQuestion = 'Explain this run and suggest the next read-only check.';
let polling = false;
let submitting = false;
let toastTimer;
let routeEpoch = 0;
let placement = null, executionPlan = null, poolScanning = false;
let requestedCpus = 4, requestedMemory = 8, requestedSimulator = 'spectre';
let plannedKind = 'standalone', plannedReference = '', plannedHost = '';
let selectedQa = null, qaView = null;
const codexPanelState = {loaded: false, loading: false, busy: false, interrupting: false, status: null, error: null,
  stream: null, streamState: 'idle', cursor: 0, statusCursor: 0, threads: [], selectedThread: null,
  approvals: new Map(), profile: '', model: '', reasoningEffort: '', draft: '', pending: null, pendingLoaded: false,
  models: [], defaultModel: '', modelsLoaded: false, authAccount: null, settingsOpen: false,
  permissionConfirmPending: false,
  turnStartedAt: null, turnTimer: null, lastTurnStatus: '', lastTurnDurationMs: null,
  uiPatchRevision: 0, uiPatchSyncing: false,
  historyTruncated: false, recoverySnapshot: null, recoveryConfirmed: false, recoveryAutoLoadedKey: null, evalReport: null, evalBusy: false};
const CODEX_PENDING_KEY = 'spiceowl.codex-pending';
const isStandaloneAgent = () => !isPublicDemo() && state.agent_channel?.mode === 'standalone';
let poolImportPreview = null;
let selectedHostIds = null;
let hostSelectionRevision = 0, planningBusy = false;
let live = {loaded: false, loading: false, enabled: false, projectId: '', runtime: null, error: null, busy: false, receipt: null};
let liveDraft = {configuration: '', source_commit: '', run_id: ''};
let directState = {
  loaded: false, loading: false, busy: false, runs: [], scopeReports: [], monitor: null,
  activeTab: '',
  agentLinkedRunId: '',
  quickLaunchNote: null, quickLaunchError: null,
  selectedId: null, detail: null, report: null, aiReport: null, lookup: null, error: null,
  directPollTimer: null, directPollInFlight: false, pendingRefresh: false,
  recoveryReports: [], selectedRecoveryId: null, recoveryDetail: null,
  recoveryQuery: '', recoveryOutcome: '',
  watchEvents: [], watchPolicy: null, watchAgentConnected: false, watchAgentState: 'disconnected',
  watchSimScenario: 'queue_timeout',
  wfIndex: null, wfDataset: '', wfSignals: [], wfQuery: null,
  wfWindow: {xMin: '', xMax: ''}, wfMaxPoints: 1200,
  wfToolMode: 'zoom', wfCursorA: null, wfCursorB: null, wfCursorNext: 'A',
  wfMeasure: null, wfCompare: null,
  wfCycles: null, wfCycleTab: 'worst_case', wfClock: '', wfPeriod: '',
  wfWorstMetric: 'peak_to_peak', wfWorstSignal: '', wfSelectedCycle: null,
  wfJumpCycle: '', wfBusy: false, wfError: null,
  batches: [], selectedBatchId: null, batchDetail: null, batchMetric: '',
  batchBusy: false, batchError: null,
};
let overviewState = {
  project: '',
  testbench: '',
  status: '',
  query: '',
  selectedRunId: '',
};
let reportModalState = {
  open: false,
  loading: false,
  generating: false,
  scope: 'run',
  runId: '',
  project: '',
  testbench: '',
  report: null,
  customPrompt: '',
  error: null,
};
let batchDraft = {
  batch_id: '', title: '', kind: 'pvt', corners: 'tt, ss, ff',
  voltages: '1.08, 1.20, 1.32', temps: '-40, 27, 125', voltage_param: 'vdd',
  mc_samples: 20, mc_base_seed: 1001, mc_variation: 'mismatch', max_concurrency: 4,
  spec_name: '', spec_signal: 'v(out)', spec_metric: 'max', spec_min: '', spec_max: '', spec_unit: 'V',
};
let wizardState = {
  loaded: false, loading: false, busy: false, status: null, demo: null,
  firstSim: null, lastProbe: null, error: null, tutorialStep: 1,
};
let hostLiveState = {
  loading: false,
  loaded: false,
  loadedAt: 0,
  hostSignature: '',
  observedAt: null,
  summary: null,
  mySimulations: [],
  licenses: null,
  byId: new Map(),
  error: null,
};
let wizardDraft = {
  alias_pick: '', host_id: '', label: '', ssh_target: '', expected_host: '', expected_user: '',
  simulator: 'spectre', simulator_executable: '', backend: 'ssh_direct', queue: '', partition: '',
  selected_host_id: '', template_id: 'spectre_rc_smoke', custom_netlist: '',
  remote_python: '/usr/bin/python3', remote_root: '.spiceowl-runs',
};
let directDraft = {
  run_key: '', target_mode: 'host', host_id: '', backend: 'ssh_direct', simulator: 'spectre',
  output_format: 'psfascii', cpus: 2, memory_gib: 8, wall_seconds: 3600,
  queue: '', project: '', job_group: '', partition: '', account: '', qos: '', exclusive: false,
  remote_python: '/usr/bin/python3', remote_root: '.spiceowl-runs', include_dirs: '',
  env_modules: '', env_scripts: '',
  netlist_text: '', lookup_key: '', allow_collect: true, recheck_resources: true,
  rec_enabled: false, rec_max_attempts: 2, rec_max_memory_gib: 64, rec_max_elapsed_min: 240,
  rec_retry_license: true, rec_license_backoff: 300, rec_retry_oom: true,
  rec_oom_multiplier: 2, rec_allow_relocate: true,
};
// Active circuit/project entry for Page 3 (reused directly from CircuitGit when invoked/embedded,
// or selected via SpiceOwl's CircuitGit-shared Library Manager in standalone mode).
let simSourceState = {
  origin: 'none', // none | template | local | ssh | circuitgit
  projectId: '',
  projectName: '',
  libraryId: '',
  libraryName: '',
  cellId: '',
  cellName: '',
  viewId: '',
  entryPath: '',
  fileName: '',
  sshTarget: '',
  sshHostId: '',
  remotePath: '',
  remoteDir: '',
  supportFiles: {},
  skipped: [],
  sourceCommit: '',
  returnUrl: '',
  fromSchematic: false,
  maestroApplied: false,
  maestroConfig: null,
  rnlApplied: false,
  rnlStats: null,
  serverEntryChecked: false,
  urlHandoffChecked: false,
};
let libMgrState = {
  tab: 'ssh', // ssh | local
  hostId: '',
  sshTarget: '',
  path: '~',
  cwd: '',
  parent: null,
  entries: [],
  cdsLibraries: [],
  virtuosoContext: null,
  includeMaestro: true,
  preserveRnl: true,
  selectedMaestroView: '',
  truncated: false,
  selectedPath: '',
  filter: '',
  includeDeps: true,
  busy: false,
  busyMessage: '',
  error: '',
  localBundle: null,
  localSelectedEntry: '',
  localError: '',
  localBusy: false,
};

// Reused directly from CircuitGit (web/studio/project-import.js) so local project/netlist
// import filtering, top-level entry scoring, and support-file bundling stay 100% consistent.
const CIRCUIT_EXTENSIONS = Object.freeze([
  '.scs', '.sp', '.spi', '.spice', '.cir', '.ckt', '.net',
  '.inc', '.va', '.vams', '.v', '.rnl', '.txt', '.cdl', '.mdl',
]);
const CIRCUIT_EXT_SET = new Set(CIRCUIT_EXTENSIONS);
const MAX_LOCAL_IMPORT_FILES = 48;
const MAX_SINGLE_IMPORT_BYTES = 1024 * 1024;
const MAX_BUNDLE_IMPORT_BYTES = 1400 * 1024;
const SENSITIVE_IMPORT_NAME = /^(?:\.|id_rsa|id_dsa|id_ecdsa|id_ed25519|authorized_keys$|known_hosts$|shadow$|passwd$)|(?:\.(?:key|pem|p12|pfx|jks|lic|ppk)$)|(?:secret|password|passwd|token|credential|license)/i;

function isCircuitFileName(name) {
  if (typeof name !== 'string' || !name) return false;
  const base = name.split('/').pop() || '';
  if (!base || SENSITIVE_IMPORT_NAME.test(base)) return false;
  const dot = base.lastIndexOf('.');
  if (dot < 0) return false;
  return CIRCUIT_EXT_SET.has(base.slice(dot).toLowerCase());
}

function normalizeRelativePath(raw) {
  if (typeof raw !== 'string') return null;
  const cleaned = raw.replace(/\\/g, '/').replace(/^\/+/, '').replace(/^\.\//, '');
  if (!cleaned || cleaned.length > 512 || /[\x00-\x1f\x7f]/.test(cleaned)) return null;
  const parts = cleaned.split('/').filter(Boolean);
  if (!parts.length || parts.some(p => p === '.' || p === '..' || SENSITIVE_IMPORT_NAME.test(p))) return null;
  return parts.join('/');
}

function scoreEntryCandidate(relPath) {
  const parts = String(relPath || '').split('/');
  const base = (parts[parts.length - 1] || '').toLowerCase();
  const depth = parts.length - 1;
  let score = Math.max(0, 40 - depth * 12);
  if (base.endsWith('.scs')) score += 28;
  else if (base.endsWith('.sp') || base.endsWith('.spice') || base.endsWith('.cir')) score += 22;
  else if (base.endsWith('.rnl')) score += 18;
  else if (base.endsWith('.net') || base.endsWith('.ckt') || base.endsWith('.cdl')) score += 14;
  else if (base.endsWith('.va') || base.endsWith('.vams')) score += 6;
  if (/^tb[_-]|[_-]tb\./i.test(base)) score += 18;
  if (/top|main|schematic/i.test(base)) score += 12;
  if (/model|corner|param|lib|inc|header/i.test(base)) score -= 14;
  return score;
}

function formatImportByteSize(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function prepareLocalImportFiles(entries) {
  const list = Array.isArray(entries) ? entries : [];
  const valid = [];
  const skipped = [];
  for (const item of list) {
    const rel = normalizeRelativePath(item?.path || item?.name || '');
    if (!rel) { skipped.push({path: String(item?.path || item?.name || ''), reason: 'invalid_or_sensitive_path'}); continue; }
    if (!isCircuitFileName(rel)) { skipped.push({path: rel, reason: 'unsupported_extension'}); continue; }
    if (typeof item?.text !== 'string') { skipped.push({path: rel, reason: 'not_text'}); continue; }
    const size = new TextEncoder().encode(item.text).byteLength;
    if (size === 0) { skipped.push({path: rel, reason: 'empty_file'}); continue; }
    if (size > MAX_SINGLE_IMPORT_BYTES) { skipped.push({path: rel, reason: 'exceeds_1mib'}); continue; }
    valid.push({path: rel, text: item.text, size});
  }
  if (!valid.length) throw new Error('未找到可导入的网表文件（支持 .scs / .sp / .spice / .cir / .va / .rnl / .txt，单文件 ≤ 1 MiB）。');
  const firstSegs = new Set(valid.map(f => (f.path.includes('/') ? f.path.split('/')[0] : '')));
  let projectName = '';
  let stripped = valid;
  if (firstSegs.size === 1) {
    const only = [...firstSegs][0];
    if (only) {
      projectName = only;
      stripped = valid.map(f => ({...f, path: f.path.slice(only.length + 1)}));
    }
  }
  const limited = stripped.slice(0, MAX_LOCAL_IMPORT_FILES);
  if (stripped.length > MAX_LOCAL_IMPORT_FILES) {
    for (const extra of stripped.slice(MAX_LOCAL_IMPORT_FILES)) skipped.push({path: extra.path, reason: 'file_count_limit'});
  }
  const candidates = [...limited].sort((a, b) => scoreEntryCandidate(b.path) - scoreEntryCandidate(a.path) || a.path.localeCompare(b.path));
  const filesByPath = Object.create(null);
  for (const row of limited) filesByPath[row.path] = row.text;
  const defaultEntry = candidates[0].path;
  if (!projectName) projectName = defaultEntry.split('/').pop().replace(/\.[^.]+$/, '') || defaultEntry;
  return {projectName, candidates: candidates.map(c => ({path: c.path, size: c.size})), defaultEntry, filesByPath, skipped};
}

function selectImportEntry({filesByPath, entryPath, projectName = ''}) {
  const cleanEntry = normalizeRelativePath(entryPath);
  if (!cleanEntry || !Object.prototype.hasOwnProperty.call(filesByPath || {}, cleanEntry)) {
    throw new Error('请选择有效的顶层入口网表文件。');
  }
  const text = filesByPath[cleanEntry];
  const fileName = cleanEntry.split('/').pop() || cleanEntry;
  const entryDir = cleanEntry.includes('/') ? cleanEntry.slice(0, cleanEntry.lastIndexOf('/')) : '';
  const supportFiles = {};
  let totalBytes = new TextEncoder().encode(text).byteLength;
  for (const [rel, content] of Object.entries(filesByPath)) {
    if (rel === cleanEntry) continue;
    let supportKey = rel;
    if (entryDir && rel.startsWith(entryDir + '/')) supportKey = rel.slice(entryDir.length + 1);
    const bytes = new TextEncoder().encode(content).byteLength;
    if (totalBytes + bytes <= MAX_BUNDLE_IMPORT_BYTES && Object.keys(supportFiles).length < MAX_LOCAL_IMPORT_FILES) {
      supportFiles[supportKey] = content;
      totalBytes += bytes;
    }
  }
  return {
    projectName: projectName || fileName.replace(/\.[^.]+$/, '') || fileName,
    entryPath: cleanEntry,
    fileName,
    text,
    supportFiles,
  };
}

async function readBrowserFileList(fileList) {
  const files = Array.from(fileList || []);
  const out = [];
  for (const file of files) {
    const rel = file.webkitRelativePath || file.name;
    if (!isCircuitFileName(rel)) continue;
    if (file.size > MAX_SINGLE_IMPORT_BYTES) continue;
    const text = await file.text();
    out.push({path: rel, text});
  }
  return out;
}

function isCircuitGitManagedEntry() {
  return state.agent_channel?.mode === 'embedded_in_circuitgit' || simSourceState.origin === 'circuitgit';
}

function detectSimulatorFromNetlist(text, fileName = '') {
  const lowName = String(fileName || '').toLowerCase();
  if (lowName.endsWith('.scs') || /simulator\s+lang=spectre/i.test(String(text || ''))) return {simulator: 'spectre', output_format: 'psfbin'};
  if ((lowName.endsWith('.sp') || lowName.endsWith('.cir') || lowName.endsWith('.spice')) && /^\s*\.(?:tran|ac|dc|op|end)\b/im.test(String(text || ''))) {
    return {simulator: 'ngspice', output_format: 'psfascii'};
  }
  return {simulator: directDraft.simulator || 'spectre', output_format: directDraft.output_format || 'psfbin'};
}

function makeSafeRunKeyPrefix(raw) {
  const clean = String(raw || '').replace(/\.[^.]+$/, '').replace(/[^A-Za-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 32);
  return (clean && /^[A-Za-z0-9]/.test(clean)) ? clean : 'sim';
}

function applyCircuitGitEntry(entry, {silent = false} = {}) {
  if (!entry || typeof entry !== 'object') return false;
  simSourceState.origin = 'circuitgit';
  simSourceState.projectId = String(entry.project_id || entry.projectId || '');
  simSourceState.projectName = String(entry.project_name || entry.projectName || entry.project_id || '');
  simSourceState.libraryId = String(entry.library_id || entry.libraryId || '');
  simSourceState.libraryName = String(entry.library_name || entry.libraryName || entry.library_id || '');
  simSourceState.cellId = String(entry.cell_id || entry.cellId || '');
  simSourceState.cellName = String(entry.cell_name || entry.cellName || entry.cell_id || '');
  simSourceState.viewId = String(entry.view_id || entry.viewId || '');
  simSourceState.entryPath = String(entry.entry_path || entry.entryPath || entry.file_name || '');
  simSourceState.fileName = String(entry.file_name || entry.fileName || simSourceState.entryPath.split('/').pop() || 'circuitgit_entry.scs');
  simSourceState.sshTarget = String(entry.ssh_target || entry.sshTarget || '');
  simSourceState.remotePath = String(entry.remote_path || entry.remotePath || '');
  simSourceState.remoteDir = String(entry.remote_dir || entry.remoteDir || '');
  simSourceState.supportFiles = (entry.support_files && typeof entry.support_files === 'object') ? {...entry.support_files} : {};
  simSourceState.skipped = [];
  simSourceState.sourceCommit = String(entry.source_commit || entry.sourceCommit || '');
  simSourceState.returnUrl = String(entry.return_url || entry.returnUrl || '');
  simSourceState.maestroConfig = (entry.maestro_config && typeof entry.maestro_config === 'object') ? entry.maestro_config : null;
  simSourceState.maestroApplied = Boolean(simSourceState.maestroConfig);
  simSourceState.fromSchematic = simSourceState.viewId === 'schematic';
  if (typeof entry.netlist_text === 'string' && entry.netlist_text.trim()) {
    directDraft.netlist_text = entry.netlist_text;
    const det = entry.simulator
      ? {simulator: entry.simulator, output_format: entry.simulator === 'ngspice' ? 'psfascii' : 'psfbin'}
      : detectSimulatorFromNetlist(entry.netlist_text, simSourceState.fileName);
    directDraft.simulator = det.simulator;
    directDraft.output_format = det.output_format;
  }
  if (simSourceState.sshTarget) {
    const matched = (state.hosts || []).find(h => h.ssh_target === simSourceState.sshTarget || h.host_id === simSourceState.sshTarget);
    if (matched) directDraft.host_id = matched.host_id;
  }
  const stamp = new Date().toISOString().replace(/[-:T.Z]/g, '').slice(8, 14);
  directDraft.run_key = `${makeSafeRunKeyPrefix(simSourceState.cellName || simSourceState.fileName)}-${stamp}`;
  if (!silent) toast(`已直接复用 CircuitGit 项目入口：${simSourceState.cellName || simSourceState.projectName || simSourceState.fileName}`);
  return true;
}

function readCircuitGitUrlHandoff() {
  if (simSourceState.urlHandoffChecked) return;
  simSourceState.urlHandoffChecked = true;
  try {
    const params = new URLSearchParams(location.search || '');
    if (params.get('source') !== 'circuitgit') return;
    simSourceState.origin = 'circuitgit';
    simSourceState.projectId = params.get('project') || params.get('project_id') || '';
    simSourceState.projectName = params.get('project_name') || simSourceState.projectId;
    simSourceState.libraryId = params.get('library') || params.get('library_id') || '';
    simSourceState.libraryName = params.get('library_name') || simSourceState.libraryId;
    simSourceState.cellId = params.get('cell') || params.get('cell_id') || '';
    simSourceState.cellName = params.get('cell_name') || simSourceState.cellId;
    simSourceState.fileName = params.get('file') || params.get('file_name') || '';
    simSourceState.entryPath = params.get('entry') || params.get('entry_path') || simSourceState.fileName;
    const ret = params.get('return_url') || '';
    if (/^https?:\/\//i.test(ret)) simSourceState.returnUrl = ret;
    if (!location.hash || location.hash === '#quickstart') {
      directState.activeTab = 'setup';
      location.hash = '#direct';
    }
  } catch { /* ignore */ }
}

async function loadCircuitGitEntryFromServer() {
  if (isPublicDemo() || simSourceState.serverEntryChecked) return;
  simSourceState.serverEntryChecked = true;
  try {
    const res = await api('/api/library/circuitgit-entry');
    if (res?.active && res.entry) {
      applyCircuitGitEntry(res.entry, {silent: true});
    }
  } catch { /* older connectors may not expose this endpoint yet */ }
}
const LIVE_PENDING = 'spiceowl.live-pending';
const LIVE_COLLECT_PENDING = 'spiceowl.live-collect-pending';
const livePendingKey = operation => operation === 'collect' ? LIVE_COLLECT_PENDING : LIVE_PENDING;
const CONNECTOR_STORAGE_KEY = 'spiceowl.connector.session';
const DEFAULT_CONNECTOR_BASE = 'http://127.0.0.1:8765';
const connectorState = {
  connected: false,
  baseUrl: DEFAULT_CONNECTOR_BASE,
  token: '',
  version: '',
  phase: 'idle', // idle | probing | waking | ready_to_pair | not_installed | connected
  note: '',
  afterConnect: '', // '' | 'codex' | 'ssh'
};
const isHostedPages = () => window.SpiceOwlDemo !== undefined || document.documentElement?.dataset?.publicDemo === 'true';
const isConnectorLive = () => Boolean(connectorState.connected && connectorState.baseUrl && connectorState.token);
const isPublicDemo = () => isHostedPages() && !isConnectorLive();
const demoRepository = 'https://github.com/spiceowl-dev/spiceowl';
const circuitGitEntry = 'https://circuitgit-dev.github.io/CircuitGit/';
function demoSource() {
  const metadata = window.SpiceOwlDemo?.metadata || {};
  const branch = /^[A-Za-z0-9][A-Za-z0-9._/-]{0,120}$/.test(metadata.source_branch || '') ? metadata.source_branch : 'main';
  return {branch, ref: /^[a-f0-9]{40}$/i.test(metadata.commit || '') ? metadata.commit : branch};
}
const demoDocument = path => demoRepository + '/blob/' + demoSource().ref + '/' + path;
const demoPendingRequests = new Map();
const pendingGet = key => isPublicDemo() ? demoPendingRequests.get(key) || null : sessionStorage.getItem(key);
const pendingSet = (key, value) => isPublicDemo() ? demoPendingRequests.set(key, value) : sessionStorage.setItem(key, value);
const pendingRemove = key => isPublicDemo() ? demoPendingRequests.delete(key) : sessionStorage.removeItem(key);

async function probeConnectorHealth(baseUrl = connectorState.baseUrl, timeoutMs = 1200) {
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
  try {
    const res = await fetch(`${baseUrl}/health`, {
      method: 'GET',
      mode: 'cors',
      credentials: 'omit',
      cache: 'no-store',
      ...(controller ? {signal: controller.signal} : {}),
    });
    if (!res.ok) return null;
    const data = await res.json();
    return data && data.ok === true && data.product === 'spiceowl' ? data : null;
  } catch {
    return null;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

function triggerConnectorUriScheme() {
  try {
    const iframe = document.createElement('iframe');
    iframe.style.display = 'none';
    iframe.src = 'spiceowl://start';
    document.body.appendChild(iframe);
    setTimeout(() => { try { iframe.remove(); } catch {} }, 1500);
  } catch { /* ignore */ }
}

async function restoreConnectorSession() {
  if (!isHostedPages()) return false;
  try {
    const raw = sessionStorage.getItem(CONNECTOR_STORAGE_KEY);
    if (!raw) return false;
    const saved = JSON.parse(raw);
    if (!saved?.baseUrl || !saved?.token) return false;
    const health = await probeConnectorHealth(saved.baseUrl, 1200);
    if (!health) {
      sessionStorage.removeItem(CONNECTOR_STORAGE_KEY);
      return false;
    }
    const res = await fetch(`${saved.baseUrl}/api/state`, {
      method: 'GET',
      mode: 'cors',
      credentials: 'omit',
      headers: {'X-SpiceOwl-Token': saved.token},
    });
    if (!res.ok) {
      sessionStorage.removeItem(CONNECTOR_STORAGE_KEY);
      return false;
    }
    connectorState.connected = true;
    connectorState.baseUrl = saved.baseUrl;
    connectorState.token = saved.token;
    connectorState.version = health.version || '';
    connectorState.phase = 'connected';
    return true;
  } catch {
    return false;
  }
}

function disconnectConnector() {
  try { sessionStorage.removeItem(CONNECTOR_STORAGE_KEY); } catch {}
  connectorState.connected = false;
  connectorState.token = '';
  connectorState.phase = 'idle';
  connectorState.note = '';
  directState.loaded = false;
  wizardState.loaded = false;
  live.loaded = false;
  codexPanelState.loaded = false;
  if (codexPanelState.stream) { try { codexPanelState.stream.close(); } catch {} codexPanelState.stream = null; }
}

function renderConnectorDialog() {
  const box = $('#connector-dialog-content');
  if (!box) return;
  if (connectorState.connected) {
    box.innerHTML = `<div class="direct-rec-integrity"><strong>🟢 本机 SpiceOwl Connector 已连接</strong><span>·</span><code>${escape(connectorState.baseUrl)}</code>${connectorState.version ? `<span>· v${escape(connectorState.version)}</span>` : ''}</div><p class="subtle-note">当前网页已直连您的本机无界面连接器：可直接探测 <code>~/.ssh/config</code>、提交真实 SSH/LSF/Slurm 仿真、原位流式读取远端大波形并唤醒本机 Codex Agent。同标签页刷新会自动续连，关闭标签页自动吊销授权。</p><div class="dialog-actions"><button type="button" class="button secondary" data-action="connector-disconnect">断开本机连接（切回网页预览）</button><button type="button" class="button primary" data-action="connector-close">返回工作台</button></div>`;
    return;
  }
  if (connectorState.phase === 'probing' || connectorState.phase === 'waking') {
    const msg = connectorState.phase === 'probing'
      ? `正在探测本机连接器 (${connectorState.baseUrl}/health)…`
      : '未检测到运行中的连接器，正在通过 spiceowl://start 静默唤起本机连接器（等待最多 3.5 秒）…';
    box.innerHTML = `<p class="subtle-note">${escape(msg)}</p><p class="subtle-note">若浏览器弹出“是否打开 SpiceOwl Connector？”，勾选“始终允许”后以后将无感直连。</p><div class="dialog-actions"><button type="button" class="button secondary" data-action="connector-close">取消</button></div>`;
    return;
  }
  if (connectorState.phase === 'ready_to_pair') {
    box.innerHTML = `<div class="direct-rec-integrity"><strong>✓ 已检测到本机 SpiceOwl Connector 在线！</strong><span>·</span><code>${escape(connectorState.baseUrl)}</code></div><p class="subtle-note">点击下方按钮完成一次性网页授权（确认后弹窗自动关闭，您始终留在当前网页工作台）。</p><div class="dialog-actions"><button type="button" class="button secondary" data-action="connector-close">稍后再连</button><button type="button" class="button primary" data-action="connector-authorize">确认授权连接</button></div>`;
    return;
  }
  box.innerHTML = `<p class="muted"><strong>继续此功能需要安装一次本机 SpiceOwl Connector（零环境配置 · 免装 Python · 免管理员权限）。</strong></p><p class="subtle-note">下载解压后双击一次即可自动装好独立运行环境、注册 <code>spiceowl://</code> 协议并在后台静默启动（安装完可直接删除压缩包和解压文件夹）。</p><div class="public-feature-grid"><article><h3>Windows（双击即完成）</h3><p>解压后双击 <code>Install SpiceOwl Connector.bat</code>，自动准备独立 Python 3.12 环境、注册 <code>HKCU</code> 协议并无黑框后台常驻。</p></article><article><h3>macOS（双击即完成）</h3><p>解压后双击 <code>Install SpiceOwl Connector.command</code>，自动准备独立 Python 3.12 环境、生成 <code>SpiceOwl Connector.app</code> 并后台常驻。</p></article></div><p class="subtle-note">若刚刚已安装成功但仍提示未检测到，请确认浏览器未拒绝「访问本地网络」权限，或点右侧「直接打开授权窗」完成授权。</p><div class="dialog-actions"><a class="button secondary" href="${demoRepository}/archive/refs/heads/${escape(demoSource().branch)}.zip" target="_blank" rel="noopener noreferrer">下载全自动安装包 (.zip)</a><button type="button" class="button secondary" data-action="connector-authorize">已运行？直接授权</button><button type="button" class="button primary" data-action="connector-retry">我已安装，重新检测</button></div>`;
}

async function startConnectorFlow({openModal = true} = {}) {
  const dlg = $('#connector-dialog');
  if (connectorState.connected) {
    renderConnectorDialog();
    if (openModal && dlg && !dlg.open) dlg.showModal();
    return;
  }
  connectorState.phase = 'probing';
  renderConnectorDialog();
  if (openModal && dlg && !dlg.open) dlg.showModal();
  let health = await probeConnectorHealth(connectorState.baseUrl, 1200);
  if (health) {
    connectorState.version = health.version || '';
    connectorState.phase = 'ready_to_pair';
    renderConnectorDialog();
    return;
  }
  // Not running yet: try custom URI scheme wake (spiceowl://start) and poll up to 3.5s
  connectorState.phase = 'waking';
  renderConnectorDialog();
  triggerConnectorUriScheme();
  const deadline = Date.now() + 3500;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 500));
    health = await probeConnectorHealth(connectorState.baseUrl, 800);
    if (health) {
      connectorState.version = health.version || '';
      connectorState.phase = 'ready_to_pair';
      renderConnectorDialog();
      return;
    }
  }
  connectorState.phase = 'not_installed';
  renderConnectorDialog();
}

function openConnectorAuthorizationPopup() {
  const url = `${connectorState.baseUrl}/connect?origin=${encodeURIComponent(location.origin)}`;
  const popup = window.open(url, 'spiceowl_connector_pair', 'width=520,height=520,resizable=yes,scrollbars=yes');
  if (!popup) {
    toast('浏览器拦截了授权确认窗，请允许弹出窗口后重试。');
  }
}

function openSshSetupWizard(alias = '') {
  const base = isConnectorLive() ? connectorState.baseUrl : '';
  const extra = alias ? `&alias=${encodeURIComponent(alias)}` : '';
  const url = `${base}/ssh/setup?origin=${encodeURIComponent(location.origin)}${extra}`;
  const popup = window.open(url, 'spiceowl_ssh_setup', 'width=940,height=740,resizable=yes,scrollbars=yes');
  if (!popup) {
    toast('浏览器拦截了 SSH 配置向导窗口，请允许弹出窗口后重试。');
  }
}

async function quickConnectSshAlias(aliasName, {silentOnFail = false} = {}) {
  if (!aliasName || wizardState.busy) return null;
  wizardState.busy = true;
  wizardState.lastDiag = null;
  render();
  try {
    const res = await api('/api/wizard/quick-connect', {method: 'POST', body: {alias: aliasName}});
    wizardState.lastDiag = res.diagnosis || null;
    wizardState.lastProbe = res.probe || null;
    hostLiveState.loaded = false;
    await refresh();
    await refreshWizard();
    if (res.host?.host_id) {
      wizardDraft.selected_host_id = res.host.host_id;
    }
    if (res.ok && res.host) {
      toast(`🟢 ${aliasName} 已自动验证并加入服务器池！`);
    } else if (!silentOnFail) {
      const failStep = (res.diagnosis?.steps || []).find(s => !s.ok);
      toast(failStep ? `${aliasName}：${failStep.detail}` : `${aliasName} 尚未连通，可在配置向导中一键修复。`);
    }
    return res;
  } catch (error) {
    if (!silentOnFail) toast(error.message);
    return null;
  } finally {
    wizardState.busy = false;
    render();
  }
}

async function disconnectSshAlias(aliasName) {
  if (!aliasName || wizardState.busy) return null;
  wizardState.busy = true;
  render();
  try {
    const res = await api('/api/wizard/disconnect', {method: 'POST', body: {alias: aliasName}});
    wizardState.lastDiag = null;
    wizardState.lastProbe = null;
    hostLiveState.loaded = false;
    hostLiveState.byId.delete(aliasName);
    await Promise.all([refresh(), refreshWizard()]);
    toast(`已断开 ${aliasName}（本机 SSH 配置已保留，再次点击可重新连接）。`);
    return res;
  } catch (error) {
    toast(error.message);
    return null;
  } finally {
    wizardState.busy = false;
    render();
  }
}

window.addEventListener('message', async event => {
  const msg = event.data;
  if (msg && msg.type === 'spiceowl.ssh.updated') {
    const expectedOrigin = isHostedPages() ? connectorState.baseUrl : location.origin;
    if (event.origin !== expectedOrigin) return;
    if (!isPublicDemo()) {
      wizardState.loaded = false;
      hostLiveState.loaded = false;
      await Promise.all([refresh(), refreshWizard()]);
      if (msg.diagnosis_ok && msg.selected_alias && !msg.auto_connected && !msg.live_sync) {
        await quickConnectSshAlias(msg.selected_alias, {silentOnFail: true});
      } else {
        render();
        if (!msg.live_sync) {
          toast(msg.auto_connected
            ? '🟢 已自动连接新配置好的服务器！'
            : msg.selected_alias
            ? `🟢 已同步 SSH 配置（${msg.selected_alias}），点击即可连接。`
            : '🟢 已同步本机最新 SSH 服务器配置！');
        }
      }
    }
    return;
  }
  if (!isHostedPages()) return;
  if (event.origin !== connectorState.baseUrl) return;
  if (!msg || msg.type !== 'spiceowl.connector.authorized' || typeof msg.token !== 'string' || !msg.token) return;
  connectorState.connected = true;
  connectorState.token = msg.token;
  connectorState.version = msg.version || connectorState.version;
  connectorState.phase = 'connected';
  try {
    sessionStorage.setItem(CONNECTOR_STORAGE_KEY, JSON.stringify({baseUrl: connectorState.baseUrl, token: connectorState.token}));
  } catch {}
  const dlg = $('#connector-dialog');
  if (dlg?.open) dlg.close();
  directState.loaded = false;
  wizardState.loaded = false;
  live.loaded = false;
  codexPanelState.loaded = false;
  try {
    const intent = connectorState.afterConnect;
    connectorState.afterConnect = '';
    await refresh();
    if (!isPublicDemo()) {
      await refreshWizard();
      if (isStandaloneAgent()) {
        await refreshCodexStatus();
        if (intent === 'codex' && !codexPanelState.status?.connected) {
          try {
            await api('/api/codex/connect', {method: 'POST', body: {}});
            await refreshCodexStatus();
            toast('🟢 本机连接器与 Codex 已全部连好！');
          } catch (codexErr) {
            toast('🟢 本机连接器已连好；Codex 连接未完成：' + codexErr.message);
          }
          render();
          return;
        }
      }
    }
    render();
    if (intent === 'ssh_setup') {
      toast('🟢 本机连接器已连好，正在打开本机 SSH 图形化配置向导…');
      openSshSetupWizard();
      return;
    }
    toast(intent === 'ssh'
      ? '🟢 本机连接器已连好，已自动识别你的 ~/.ssh/config 服务器列表！'
      : '🟢 已连接到本机 SpiceOwl Connector！现在可直接在当前网页使用真实 SSH 与本机 Agent。');
  } catch (error) {
    disconnectConnector();
    render();
    toast('连接器授权后同步失败：' + error.message);
  }
});

function publicDemoChrome() {
  const hosted = isHostedPages();
  const liveConn = isConnectorLive();
  const preview = isPublicDemo();
  document.body.classList.toggle('public-demo', preview);
  document.title = 'SpiceOwl · Simulation workspace';
  if ($('#public-demo-banner')) $('#public-demo-banner').hidden = !preview;
  if ($('#public-github-link')) $('#public-github-link').hidden = !hosted;
  const connBtn = $('#connector-trigger');
  if (connBtn) {
    connBtn.hidden = !hosted;
    connBtn.classList.toggle('is-live', liveConn);
    connBtn.innerHTML = `<i class="conn-dot" aria-hidden="true"></i><span>${liveConn ? '本机已连接' : '连接本机'}</span>`;
  }
  if (!hosted) return;
  $('#workspace-scope').textContent = liveConn ? 'Local Connector · Live' : 'Web Workbench';
  $('.local-tag').textContent = liveConn ? 'LIVE' : 'WEB';
  $('.breadcrumb').textContent = 'SpiceOwl';
  $('.page-footer span').textContent = liveConn ? 'Connected to local SpiceOwl Connector · Explicit SSH checks' : 'SpiceOwl Web · Browser preview until Connector paired';
  const metadata = window.SpiceOwlDemo?.metadata || {};
  const commit = /^[a-f0-9]{7,40}$/i.test(metadata.commit || '') ? metadata.commit.slice(0, 8) : 'unversioned';
  const date = typeof metadata.build_date === 'string' && /^\d{4}-\d{2}-\d{2}/.test(metadata.build_date) ? metadata.build_date.slice(0, 10) : 'date unavailable';
  if ($('#demo-build')) $('#demo-build').textContent = `${commit} · ${date}`;
  if (typeof window.SpiceOwlDemo?.request !== 'function' && $('[data-action="demo-reset"]')) $('[data-action="demo-reset"]').disabled = true;
  if ($('#demo-storage-notice')) {
    $('#demo-storage-notice').textContent = (state.demo_storage || window.SpiceOwlDemo?.storage_mode) === 'memory'
      ? '仅存于内存 (memory; resets on refresh)'
      : '连上本机连接器后自动切为真实工作区';
  }
  const connMuted = $('#connection-dialog .muted');
  if (connMuted && preview) connMuted.textContent = '在浏览器内安全演练不同 AI 故障场景。不会连接任何外部 API，也无需填写密钥。';
}

function publicDirectPage() {
  return heading('DIRECT EXECUTION PREVIEW', '不依赖网关，直接驱动 SSH / LSF / Slurm 仿真。', '公共演示页面不连接 SSH 或集群调度器；在本地运行 SpiceOwl 即可直接使用。') + `<section class="panel public-info" data-public-demo-info="direct"><div class="panel-body"><span class="chip">LOCAL WORKFLOW</span><h2>三步在本地控制台完成直连仿真</h2><div class="public-feature-grid"><article><h3>1. 选主机或集群</h3><p>在 Simulation hosts 注册 OpenSSH 别名后，可按 CPU/内存自动选机，或指定 LSF / Slurm 登录节点。</p></article><article><h3>2. 冻结并提交</h3><p>Prepare 仅在本地冻结网表与校验和；Stage 写入隔离目录；Launch 具备原子防重发占位，断线不盲目重提。</p></article><article><h3>3. 盯守与五维报告</h3><p>支持后台 Watch、精准 Cancel、安全收集日志与波形，自动输出五维 Spectre 报告与 SOA/OOM 证据。</p></article></div><div class="public-info-links"><a class="button primary" href="#local">Run locally</a><a class="button secondary" href="./workflow.html">Explore workflow</a></div></div></section>`;
}

async function refreshWizard() {
  if (isPublicDemo() || wizardState.loading) return;
  wizardState.loading = true;
  try {
    const data = await api('/api/wizard/status');
    wizardState.status = data;
    wizardState.error = null;
    const aliases = data.ssh_aliases || [];
    if (!wizardDraft.ssh_target && aliases.length) {
      applySshAliasToDraft(aliases[0]);
    }
    const hosts = data.workspace?.hosts || [];
    if (!wizardDraft.selected_host_id && hosts.length) {
      wizardDraft.selected_host_id = hosts[0].host_id;
    }
    if (wizardState.firstSim?.run?.run_id) {
      try {
        const d = await api(`/api/direct/runs/${encodeURIComponent(wizardState.firstSim.run.run_id)}`);
        wizardState.firstSim = {...wizardState.firstSim, run: d.run, report: d.report || null};
      } catch { /* Keep previous snapshot if removed */ }
    } else if (data.workspace?.first_sim_runs?.length) {
      wizardState.firstSim = {run: data.workspace.first_sim_runs[0], template_id: 'saved'};
    }
  } catch (error) {
    wizardState.error = error.message;
  } finally {
    wizardState.loaded = true;
    wizardState.loading = false;
    if (!location.hash || location.hash === '#quickstart') render();
  }
}

function applySshAliasToDraft(aliasObj) {
  if (!aliasObj) return;
  wizardDraft.alias_pick = aliasObj.alias;
  wizardDraft.ssh_target = aliasObj.alias;
  wizardDraft.host_id = aliasObj.alias.replace(/[^A-Za-z0-9_-]/g, '-');
  wizardDraft.label = `EDA Server (${aliasObj.alias})`;
  if (aliasObj.hostname) wizardDraft.expected_host = aliasObj.hostname.split('.')[0];
  if (aliasObj.user) wizardDraft.expected_user = aliasObj.user;
}

function quickstartPage() {
  const pub = isPublicDemo();
  const codexConnected = !pub && Boolean(codexPanelState.status?.connected);
  const st = wizardState.status;
  const aliases = st?.ssh_aliases || [];
  const hosts = st?.workspace?.hosts || state.hosts || [];
  const isAliasVerified = a => {
    if (a.verified === true) return true;
    const norm = String(a.alias || '').replace(/[^A-Za-z0-9_-]/g, '-');
    return hosts.some(h => h.last_probe?.identity?.verified && (
      h.host_id === a.alias
      || h.host_id === norm
      || (a.registered_host_id && h.host_id === a.registered_host_id)
    ));
  };
  const verifiedAliasCount = pub ? 0 : aliases.filter(isAliasVerified).length;

  const codexNeedsRecovery = !pub && Boolean(codexPanelState.status?.recovery_required);
  const codexStatusTag = codexConnected
    ? '<span class="home-status-tag ready">● 已连接</span>'
    : codexNeedsRecovery
    ? '<span class="home-status-tag amber">● 上次会话中断待解锁</span>'
    : pub
    ? '<span class="home-status-tag">网页一键授权</span>'
    : '<span class="home-status-tag">待连接</span>';

  const recSnap = codexNeedsRecovery ? codexPanelState.recoverySnapshot : null;
  const recTools = (recSnap?.unresolved_approvals || []).map(a => a?.params?.tool || a?.method).filter(Boolean);
  const codexBtn = codexConnected
    ? '<a class="button primary" href="#direct">打开 Agent 仿真助手 →</a>'
    : codexNeedsRecovery
    ? `<div class="home-recovery-box">${recSnap
        ? `<p class="subtle-note">挂起记录：${recTools.length ? `未完成审批 <code>${escape(recTools.join(', '))}</code>` : `受影响请求 ${recSnap.interrupted_requests.length} 条`}（未自动重放）。</p>${recSnap.has_possible_side_effects ? `<label class="direct-check-row" style="margin:6px 0"><input id="codex-rec-confirm" type="checkbox" ${codexPanelState.recoveryConfirmed ? 'checked' : ''}> 我已核对远端无重复任务，确认归档旧会话</label>` : ''}<button type="button" class="button primary" data-action="codex-reconcile" data-reconnect="1" ${codexPanelState.busy || (recSnap.has_possible_side_effects && !codexPanelState.recoveryConfirmed) ? 'disabled' : ''}>归档旧会话并重新连接 Codex</button>`
        : `<button type="button" class="button primary" data-action="codex-inspect-recovery" ${codexPanelState.busy ? 'disabled' : ''}>检查中断详情并解锁 Codex</button>`}</div>`
    : `<button type="button" class="button primary" data-action="home-connect-codex" ${codexPanelState.busy ? 'disabled' : ''}>${codexPanelState.busy ? '正在连接 Codex…' : '一键连接 Codex'}</button>`;

  const agentSection = `
    <section class="home-section" aria-label="连接 AI Agent">
      <div class="home-section-head">
        <h2><span class="home-step-num">1</span> 连接你的 AI Agent</h2>
        <span class="home-status-tag ${codexConnected ? 'ready' : ''}">${codexConnected ? '● Codex 已就绪，可接收夜间异常交接' : codexNeedsRecovery ? '上次重启/断线有未决请求，先归档解锁即可恢复连接' : '只需点选你正在用的 Agent，零 API Key 手填'}</span>
      </div>
      <div class="home-agent-grid">
        <article class="home-agent-card featured ${codexConnected ? 'is-connected' : ''}">
          <div>
            <div class="home-agent-top"><span class="home-agent-icon" aria-hidden="true">✧</span>${codexStatusTag}</div>
            <h3>Codex</h3>
            <p>${codexNeedsRecovery ? '上次连接器重启或断线时有一条未完成的 Agent 请求被安全挂起（防止自动重放），归档解锁后即可重新连接。' : '直接授权本机已登录的 Codex，排队超时或 OOM 时自动交给它查资源并重提。'}</p>
          </div>
          ${codexBtn}
        </article>
        <article class="home-agent-card">
          <div>
            <div class="home-agent-top"><span class="home-agent-icon" aria-hidden="true">◉</span><span class="home-status-tag">协同模式</span></div>
            <h3>CircuitGit</h3>
            <p>与原理图和 Git 共用同一条 Agent 对话窗口，不重复启动第二个助手。</p>
          </div>
          <a class="button secondary" href="${circuitGitEntry}" target="_blank" rel="noopener noreferrer">前往 CircuitGit ↗</a>
        </article>
      </div>
    </section>`;

  let serverInner = '';
  if (pub) {
    serverInner = `
      <div class="home-server-row">
        <div class="home-server-copy">
          <h3>连接你的 EDA 仿真服务器</h3>
          <p>无需手写配置文件或复杂表单。连上本机连接器后，已有 SSH 别名点一下即通；新服务器在向导里只需填地址和用户名。</p>
        </div>
        <div class="direct-actions">
          <button type="button" class="button primary" data-action="home-connect-ssh">一键识别我的 SSH 服务器</button>
          <button type="button" class="button secondary" data-action="open-ssh-setup-wizard">新增服务器向导 ↗</button>
        </div>
      </div>`;
  } else {
    const jumpTargets = new Set(aliases.flatMap(a => String(a.proxy_jump || '').split(',').map(s => s.trim()).filter(Boolean)));
    const orderedAliases = [
      ...aliases.filter(a => !jumpTargets.has(a.alias) || isAliasVerified(a)),
      ...aliases.filter(a => jumpTargets.has(a.alias) && !isAliasVerified(a)),
    ];
    const aliasChips = orderedAliases.map(a => {
      const isOk = isAliasVerified(a);
      const isJumpOnly = !isOk && jumpTargets.has(a.alias);
      const tip = isOk
        ? '已连接 · 再次点击断开连接（保留本机 SSH 配置）'
        : isJumpOnly
        ? '跳板机节点（供目标机中转）· 如需直接连接也可点击'
        : '未连接 · 点击立即验证并连接';
      const subText = (isJumpOnly ? '跳板机 · ' : '') + (a.hostname ? `${a.user ? a.user + '@' : ''}${a.hostname}` : '');
      return `<button type="button" class="home-alias-btn ${isOk ? 'verified' : (isJumpOnly ? 'is-jump' : '')}" data-action="home-quick-ssh" data-alias="${escape(a.alias)}" data-verified="${isOk ? '1' : '0'}" title="${escape(tip)}" ${wizardState.busy ? 'disabled' : ''}><span>${isOk ? '✓ ' : ''}${escape(a.alias)}</span>${subText ? `<small>${escape(subText)}</small>` : ''}</button>`;
    }).join('');
    const diagFailed = wizardState.lastDiag && !wizardState.lastDiag.ok;
    const failedStep = diagFailed ? (wizardState.lastDiag.steps || []).find(s => !s.ok) : null;
    const probeHost = wizardState.lastProbe?.identity?.hostname || wizardState.lastProbe?.identity?.actual_host || wizardState.lastDiag?.remote?.hostname || '';
    const probeUser = wizardState.lastProbe?.identity?.username || wizardState.lastProbe?.identity?.actual_user || wizardState.lastDiag?.remote?.username || '';
    const statusBanner = wizardState.lastProbe?.identity?.verified && !wizardState.lastProbe?.error
      ? `<div class="wf-mode-banner exact">${chip('SSH 已连通 ✓', 'green')}<span>${probeHost || probeUser ? `主机 <code>${escape(probeHost || '—')}</code> · 用户 <code>${escape(probeUser || '—')}</code>` : '已通过身份与连通性验证'}</span></div>`
      : diagFailed
      ? `<div class="wf-mode-banner preview">${chip('需要配置', 'amber')}<span>${escape(failedStep?.detail || '尚未连通，请在配置向导中完成指纹或免密设置。')}</span><button type="button" class="button primary small" data-action="open-ssh-setup-wizard" data-alias="${escape(wizardState.lastDiag.alias || '')}">在向导中一键修复 ↗</button></div>`
      : wizardState.lastProbe?.error
      ? `<div class="wf-mode-banner preview">${chip('未通过', 'red')}<span>${escape(wizardState.lastProbe.error.message || '请核对 SSH 别名与免密配置。')}</span></div>`
      : '';
    serverInner = wizardState.loading && !wizardState.loaded && !aliases.length
      ? `<div class="home-server-row"><div class="home-server-copy"><h3>正在识别本机 SSH 服务器…</h3><p>正在读取你的 <code>~/.ssh/config</code> 与已保存的服务器列表。</p></div></div>`
      : aliases.length
      ? `
      <div class="home-server-row">
        <div class="home-server-copy">
          <h3>${verifiedAliasCount ? `已就绪 ${verifiedAliasCount} 台 EDA 服务器` : `已发现 ${aliases.length} 个 SSH 服务器，点一下直接连通`}</h3>
          <p>${verifiedAliasCount ? '绿色为已连接，再点一下可断开连接（保留 SSH 配置）；未连接点一下即连，新增或编辑点右侧配置向导。' : '点击下方任意服务器即可自动识别主机与仿真器并完成连通（无需填写复杂表单）。'}</p>
        </div>
        <div class="direct-actions">
          <button type="button" class="button primary small" data-action="open-ssh-setup-wizard">+ 配置新服务器 ↗</button>
          <button type="button" class="button secondary small" data-action="wizard-refresh" ${wizardState.loading ? 'disabled' : ''}>刷新</button>
          <a class="button secondary small" href="#hosts">服务器池 →</a>
        </div>
      </div>
      <div class="home-alias-list">${aliasChips}</div>
      ${statusBanner}
      <details class="home-advanced" ${wizardState.firstSim?.run ? 'open' : ''}>
        <summary>进阶：跑一条零 PDK 烟雾仿真测试 / 高级手动配置</summary>
        ${renderLocalEdaOnboardingPanel()}
      </details>`
      : `
      <div class="home-server-row">
        <div class="home-server-copy">
          <h3>还没有配置 SSH 服务器</h3>
          <p>打开图形化向导，只需填服务器地址和用户名（可选跳板机），密钥、指纹和连通校验全自动搞定。</p>
        </div>
        <div class="direct-actions">
          <button type="button" class="button primary" data-action="open-ssh-setup-wizard">+ 打开 SSH 配置向导 ↗</button>
          <button type="button" class="button secondary small" data-action="wizard-refresh" ${wizardState.loading ? 'disabled' : ''}>刷新</button>
        </div>
      </div>
      ${statusBanner}`;
  }

  const serverSection = `
    <section class="home-section" aria-label="连接 EDA 服务器">
      <div class="home-section-head">
        <h2><span class="home-step-num">2</span> 连接你的 EDA 仿真服务器</h2>
        <span class="home-status-tag ${verifiedAliasCount ? 'ready' : ''}">${verifiedAliasCount ? `● ${verifiedAliasCount} 台已验证` : '复用本机 OpenSSH · 零密钥上云'}</span>
      </div>
      <div class="home-server-card">${serverInner}</div>
    </section>`;

  const nextStepSection = `
    <section class="home-section" aria-label="下一步">
      <div class="home-section-head">
        <h2><span class="home-step-num">3</span> 下一步</h2>
        <span class="home-status-tag ${verifiedAliasCount ? 'ready' : ''}">${verifiedAliasCount ? `已连接 ${verifiedAliasCount} 台服务器` : '连接服务器后即可查看实时资源'}</span>
      </div>
      <div class="home-next-card">
        <div class="home-server-copy">
          <h3>查看服务器池资源情况</h3>
          <p>${verifiedAliasCount ? `前往下一页查看当前已连接的 ${verifiedAliasCount} 台服务器的 CPU、内存、磁盘占用，以及正在运行的仿真情况。` : '前往下一页查看所有已连接服务器的 CPU、内存、磁盘占用，以及当前正在运行的仿真情况。'}</p>
        </div>
        <a class="button primary" href="#hosts" data-action="home-next-hosts">查看服务器池资源 →</a>
      </div>
    </section>`;

  return `
    <div class="home-shell">
      <header class="home-hero">
        <div class="home-hero-kicker">🦉 SPICEOWL · AGENT SIMULATION MANAGER</div>
        <h1>你去休息，仿真我盯。</h1>
        <p>只需在当前网页完成两步授权连接：接入你的 AI Agent 和 EDA 服务器，其余的排队盯守、查资源与重提全部交给它们。</p>
      </header>
      ${wizardState.error ? `<p class="error-text" role="alert">${escape(wizardState.error)}</p>` : ''}
      ${agentSection}
      ${serverSection}
      ${nextStepSection}
    </div>`;
}

function renderLocalEdaOnboardingPanel() {
  const st = wizardState.status;
  const aliases = st?.ssh_aliases || [];
  const hosts = st?.workspace?.hosts || state.hosts || [];
  const templates = st?.templates || [];
  const activeTpl = templates.find(t => t.id === wizardDraft.template_id) || templates[0] || null;
  const fRun = wizardState.firstSim?.run || null;
  const selHostId = wizardDraft.selected_host_id || hosts[0]?.host_id || '';
  const fTerminal = fRun && ['simulator_complete_pending_measurements', 'simulator_failed', 'job_setup_failed', 'input_hash_failure'].includes(fRun.state);

  let html = `<section class="panel wizard-eda-panel">`;
  html += `<div class="wizard-step-block"><h3>跑一条零 PDK 烟雾仿真（1-3 秒验证远端仿真器）</h3><form id="wizard-first-sim-form"><div class="direct-grid"><label>目标服务器<select id="wiz-target-host" ${hosts.length ? '' : 'disabled'}>${hosts.length ? hosts.map(h => `<option value="${escape(h.host_id)}" ${h.host_id === selHostId ? 'selected' : ''}>${escape(h.label)}${h.last_probe?.identity?.verified ? ' · 已就绪 ✓' : ''}</option>`).join('') : '<option value="">请先点击上方服务器完成连通</option>'}</select></label><label>执行方式<select id="wiz-backend"><option value="ssh_direct" ${wizardDraft.backend === 'ssh_direct' ? 'selected' : ''}>SSH 直连工作站</option><option value="lsf" ${wizardDraft.backend === 'lsf' ? 'selected' : ''}>LSF 集群 (bsub)</option><option value="slurm" ${wizardDraft.backend === 'slurm' ? 'selected' : ''}>Slurm 集群 (sbatch)</option></select></label><label>烟雾网表<select id="wiz-template">${templates.map(t => `<option value="${escape(t.id)}" ${t.id === wizardDraft.template_id ? 'selected' : ''}>${escape(t.title)}</option>`).join('')}<option value="custom" ${wizardDraft.template_id === 'custom' ? 'selected' : ''}>自定义粘贴网表 (.scs / .sp)</option></select></label>${wizardDraft.backend === 'lsf' ? `<label>LSF Queue<input id="wiz-queue" value="${escape(wizardDraft.queue)}" required placeholder="analog_short"></label>` : wizardDraft.backend === 'slurm' ? `<label>Slurm Partition<input id="wiz-partition" value="${escape(wizardDraft.partition)}" required placeholder="sim_compute"></label>` : `<label>远端目录<input id="wiz-remote-root" value="${escape(wizardDraft.remote_root)}" placeholder=".spiceowl-runs"></label>`}</div>${wizardDraft.template_id === 'custom' ? `<textarea id="wiz-custom-netlist" class="direct-netlist" placeholder="在此粘贴你的 Spectre 或 ngspice 网表…">${escape(wizardDraft.custom_netlist)}</textarea>` : (activeTpl ? `<p class="subtle-note">${escape(activeTpl.description)}</p>` : '')}<div class="direct-actions"><button type="submit" class="button primary small" ${wizardState.busy || !hosts.length ? 'disabled' : ''}>准备烟雾仿真 (Prepare)</button></div></form>`;
  if (fRun) {
    html += `<div class="direct-rec-card" style="margin-top:10px"><div class="section-title"><strong><code>${escape(fRun.run_id)}</code>（${escape(fRun.host_id)} · ${escape(fRun.simulator)}）</strong>${directRunChip(fRun)}</div><div class="direct-actions"><button type="button" class="button secondary small" data-action="wizard-sim-op" data-op="stage" data-id="${escape(fRun.run_id)}" ${wizardState.busy || !['prepared', 'stage_incomplete'].includes(fRun.state) ? 'disabled' : ''}>① 上传</button><button type="button" class="button primary small" data-action="wizard-sim-op" data-op="launch" data-id="${escape(fRun.run_id)}" ${wizardState.busy || fRun.state !== 'staged' || fRun.dispatch_claimed ? 'disabled' : ''}>② 启动</button><button type="button" class="button secondary small" data-action="wizard-sim-op" data-op="status" data-id="${escape(fRun.run_id)}" ${wizardState.busy || fRun.state === 'prepared' ? 'disabled' : ''}>③ 状态</button><button type="button" class="button primary small" data-action="wizard-sim-op" data-op="collect" data-id="${escape(fRun.run_id)}" ${wizardState.busy || !fTerminal || fRun.collection_phase === 'complete' ? 'disabled' : ''}>④ 收集波形</button><button type="button" class="button secondary small" data-action="wizard-goto" data-route="#direct" data-tab="runs" data-run="${escape(fRun.run_id)}" data-batch="">查看完整报告 →</button></div>${fRun.last_error ? `<p class="error-text">${escape(fRun.last_error.message || fRun.last_error.code)}</p>` : ''}</div>`;
  }
  html += `</div>`;
  html += `<details class="host-form-details"><summary>高级：手动覆盖远端身份或自定义仿真器绝对路径</summary><form id="wizard-host-form" style="margin-top:10px"><div class="direct-grid">${aliases.length ? `<label class="direct-wide">SSH 别名<select id="wiz-alias-pick"><option value="">— 选择别名 —</option>${aliases.map(a => `<option value="${escape(a.alias)}" ${a.alias === wizardDraft.alias_pick ? 'selected' : ''}>${escape(a.alias)}</option>`).join('')}</select></label>` : ''}<label>SSH 别名<input id="wiz-ssh-target" value="${escape(wizardDraft.ssh_target)}" required placeholder="eda-sim"></label><label>服务器 ID<input id="wiz-host-id" value="${escape(wizardDraft.host_id)}" required pattern="[A-Za-z0-9][A-Za-z0-9_\\-]*" placeholder="eda-sim-01"></label><label>远端 hostname<input id="wiz-expected-host" value="${escape(wizardDraft.expected_host)}" required placeholder="sim-node-01"></label><label>远端用户名<input id="wiz-expected-user" value="${escape(wizardDraft.expected_user)}" required placeholder="your_user"></label><label>仿真器<select id="wiz-simulator"><option value="spectre" ${wizardDraft.simulator === 'spectre' ? 'selected' : ''}>Cadence Spectre</option><option value="ngspice" ${wizardDraft.simulator === 'ngspice' ? 'selected' : ''}>ngspice</option></select></label><label>仿真器绝对路径（可选）<input id="wiz-sim-exe" value="${escape(wizardDraft.simulator_executable)}" placeholder="/opt/cadence/tools/bin/spectre"></label></div><div class="direct-actions"><button type="submit" class="button secondary small" data-probe="0" ${wizardState.busy ? 'disabled' : ''}>仅保存</button><button type="button" class="button primary small" data-action="wizard-register-probe" ${wizardState.busy ? 'disabled' : ''}>保存并校验</button></div></form></details>`;
  html += `</section>`;
  return html;
}

function clearDirectLivePoll() {
  if (directState.directPollTimer !== null) {
    if (typeof clearInterval === 'function') clearInterval(directState.directPollTimer);
    directState.directPollTimer = null;
  }
}

function hasActiveDirectRuns() {
  return (directState.runs || []).some(r =>
    r && (
      ['submitted', 'running_observed', 'launch_outcome_unknown'].includes(r.state)
      || (r.state === 'simulator_complete_pending_measurements' && r.collection_phase !== 'complete')
    )
  );
}

async function pollDirectRunsSilently() {
  if (isPublicDemo()) {
    clearDirectLivePoll();
    return;
  }
  const curHash = typeof location === 'object' && location ? (location.hash || '') : '';
  if (!['#overview', '#direct', '#hosts', ''].includes(curHash)) {
    clearDirectLivePoll();
    return;
  }
  if (document.hidden || directState.loading || directState.busy || directState.directPollInFlight) return;
  directState.directPollInFlight = true;
  try {
    const data = await api('/api/direct/runs');
    directState.runs = Array.isArray(data.runs) ? data.runs : [];
    directState.scopeReports = Array.isArray(data.scope_reports) ? data.scope_reports : [];
    directState.monitor = data.monitor || null;
    directState.error = null;
    if ($('#direct-count')) $('#direct-count').textContent = directState.runs.length;
    if (curHash === '#overview' && overviewState.selectedRunId && directState.runs.some(r => r.run_id === overviewState.selectedRunId)) {
      directState.selectedId = overviewState.selectedRunId;
      const detail = await api(`/api/direct/runs/${encodeURIComponent(overviewState.selectedRunId)}`);
      directState.detail = detail.run;
      directState.report = detail.report || null;
      directState.aiReport = detail.ai_report || null;
      directState.wfIndex = detail.waveforms || null;
    }
    renderSidebarRecentRuns();
    const activeTag = String(document.activeElement?.tagName || '').toUpperCase();
    const editingField = ['INPUT', 'TEXTAREA', 'SELECT'].includes(activeTag);
    if (!editingField && ['#overview', '#direct'].includes(curHash)) {
      render();
    }
  } catch { /* silent background poll */ }
  finally {
    directState.directPollInFlight = false;
    syncDirectLivePoll();
  }
}

function syncDirectLivePoll() {
  if (isPublicDemo() || typeof setInterval !== 'function') {
    clearDirectLivePoll();
    return;
  }
  const curHash = typeof location === 'object' && location ? (location.hash || '') : '';
  const activePage = ['#overview', '#direct', '#hosts', ''].includes(curHash);
  if (activePage && hasActiveDirectRuns()) {
    if (directState.directPollTimer === null) {
      const handle = setInterval(() => {
        void pollDirectRunsSilently();
      }, 4000);
      directState.directPollTimer = handle !== undefined ? handle : 0;
    }
  } else {
    clearDirectLivePoll();
  }
}

async function refreshDirect() {
  if (directState.loading) {
    directState.pendingRefresh = true;
    return;
  }
  directState.loading = true;
  directState.pendingRefresh = false;
  let shouldAutoPlotWf = false;
  try {
    const data = await api('/api/direct/runs');
    directState.runs = Array.isArray(data.runs) ? data.runs : [];
    directState.scopeReports = Array.isArray(data.scope_reports) ? data.scope_reports : [];
    directState.monitor = data.monitor || null;
    directState.error = null;
    if ($('#direct-count')) $('#direct-count').textContent = directState.runs.length;
    if (location.hash === '#overview' && overviewState.selectedRunId && directState.runs.some(r => r.run_id === overviewState.selectedRunId)) {
      directState.selectedId = overviewState.selectedRunId;
    } else if (!directState.selectedId || !directState.runs.some(r => r.run_id === directState.selectedId)) {
      const wfRun = directState.runs.find(r => r.run_id === 'demo-psfbin-500c');
      directState.selectedId = (directState.activeTab === 'waveforms' && wfRun) ? wfRun.run_id : (directState.runs[0]?.run_id || null);
    }
    const prevRunId = directState.detail?.run_id || null;
    if (directState.selectedId) {
      const detail = await api(`/api/direct/runs/${encodeURIComponent(directState.selectedId)}`);
      directState.detail = detail.run;
      directState.report = detail.report || null;
      directState.aiReport = detail.ai_report || null;
      directState.wfIndex = detail.waveforms || null;
      syncWaveformSelection(prevRunId !== directState.selectedId);
      if (directState.activeTab === 'waveforms' && directState.wfIndex?.datasets?.length && !directState.wfQuery) {
        shouldAutoPlotWf = true;
      }
    } else {
      directState.detail = null;
      directState.report = null;
      directState.aiReport = null;
      directState.wfIndex = null;
      syncWaveformSelection(true);
    }
    const qParams = new URLSearchParams();
    if (directState.recoveryQuery.trim()) qParams.set('q', directState.recoveryQuery.trim());
    if (directState.recoveryOutcome) qParams.set('outcome', directState.recoveryOutcome);
    const qs = qParams.toString();
    const recList = await api('/api/direct/recovery-reports' + (qs ? '?' + qs : ''));
    directState.recoveryReports = Array.isArray(recList.reports) ? recList.reports : [];
    if (directState.selectedRecoveryId && !directState.recoveryReports.some(r => r.root_run_id === directState.selectedRecoveryId)) {
      directState.selectedRecoveryId = directState.recoveryReports[0]?.root_run_id || null;
    } else if (!directState.selectedRecoveryId && directState.recoveryReports.length) {
      directState.selectedRecoveryId = directState.recoveryReports[0].root_run_id;
    }
    if (directState.selectedRecoveryId) {
      directState.recoveryDetail = await api(`/api/direct/recovery-reports/${encodeURIComponent(directState.selectedRecoveryId)}`);
    } else {
      directState.recoveryDetail = null;
    }
    const bList = await api('/api/direct/batches');
    directState.batches = Array.isArray(bList.batches) ? bList.batches : [];
    if (directState.selectedBatchId && !directState.batches.some(b => b.batch_id === directState.selectedBatchId)) {
      directState.selectedBatchId = directState.batches[0]?.batch_id || null;
    } else if (!directState.selectedBatchId && directState.batches.length) {
      directState.selectedBatchId = directState.batches[0].batch_id;
    }
    if (directState.selectedBatchId) {
      directState.batchDetail = await api(`/api/direct/batches/${encodeURIComponent(directState.selectedBatchId)}`);
      const mKeys = Object.keys(directState.batchDetail?.summary?.metric_stats || {});
      if (!mKeys.includes(directState.batchMetric)) directState.batchMetric = mKeys[0] || '';
    } else {
      directState.batchDetail = null;
      directState.batchMetric = '';
    }
    try {
      const wData = await api('/api/direct/watch-events');
      directState.watchEvents = Array.isArray(wData.events) ? wData.events : [];
      directState.watchPolicy = wData.policy || null;
      directState.watchAgentConnected = Boolean(wData.agent_connected);
      directState.watchAgentState = wData.agent_state || 'disconnected';
    } catch {
      directState.watchEvents = [];
    }
    await loadCircuitGitEntryFromServer();
  } catch (error) {
    directState.error = error.message;
  } finally {
    directState.loaded = true;
    directState.loading = false;
    renderSidebarRecentRuns();
    syncDirectLivePoll();
    if (location.hash === '#direct' || location.hash === '#overview') render();
    if (directState.pendingRefresh) {
      directState.pendingRefresh = false;
      void refreshDirect();
    }
  }
  if (shouldAutoPlotWf && location.hash === '#direct') {
    await runWaveformQuery();
    if (directState.wfClock || directState.wfPeriod) await runWaveformCycles();
  }
}

function recoveryOutcomeChip(outcome) {
  const map = {
    recovered: ['已自动恢复', 'green'],
    completed_first_try: ['首次即成功', 'green'],
    in_progress: ['恢复执行中', 'blue'],
    exhausted: ['预算耗尽停止', 'amber'],
    not_recoverable: ['不可自动恢复', ''],
    policy_disabled: ['未开启恢复', ''],
  };
  const [label, color] = map[outcome] || [title(outcome), ''];
  return chip(label, color);
}

function directRecoveryReportsPanel() {
  const rows = directState.recoveryReports || [];
  const detail = directState.recoveryDetail?.report || null;
  const markdown = directState.recoveryDetail?.markdown || '';
  const listHtml = rows.map(row => `<button type="button" class="direct-run-row ${row.root_run_id === detail?.root_run_id ? 'selected' : ''}" data-action="direct-rec-select" data-id="${escape(row.root_run_id)}"><div><strong>${escape(row.root_run_id)} · ${row.attempts_count} 次尝试</strong><small>${escape(row.summary_zh)}</small><small>${escape(row.backend.toUpperCase())} · 主机 ${escape((row.hosts || []).join(', '))} · 更新于 ${escape(row.updated_at ? new Date(row.updated_at).toLocaleString() : '-')}</small></div>${recoveryOutcomeChip(row.outcome)}</button>`).join('')
    || '<p class="subtle-note">当前还没有有界恢复报告。开启有界恢复并在任务触发评估或恢复后，报告会自动归档在这里并支持检索。</p>';

  let detailHtml = '';
  if (detail) {
    const integrity = detail.input_integrity || {};
    const budget = detail.budget_usage || {};
    const attemptRows = (detail.attempts || []).map(a => {
      const fd = a.five_dim_summary || {};
      const fdText = fd.available ? `${fd.execution_status || '-'} / ${fd.measurement_validity || '-'} / ${fd.functional_performance || '-'} / ${fd.operating_qualification || '-'}` : '未收集';
      return `<tr><td>#${a.attempt_number}</td><td><button type="button" class="button secondary small" data-action="direct-select" data-id="${escape(a.run_id)}">${escape(a.run_id)}</button></td><td>${escape(a.host_id)}</td><td>${escape(a.memory_gib)} GiB</td><td>${escape(a.scheduler_job_id || '-')}</td><td>${escape(a.state)}</td><td>${escape(a.failure_class)}</td><td>${escape(fdText)}</td></tr>`;
    }).join('');
    const decisionRows = (detail.decisions || []).map(dec => {
      const diff = dec.resource_diff || {};
      const parts = [];
      if (diff.memory_bytes && diff.memory_bytes.from !== diff.memory_bytes.to) parts.push(`内存 ${(diff.memory_bytes.from / 2**30).toFixed(2)} → ${(diff.memory_bytes.to / 2**30).toFixed(2)} GiB`);
      if (diff.host_id && diff.host_id.from !== diff.host_id.to) parts.push(`主机 ${diff.host_id.from} → ${diff.host_id.to}`);
      return `<li><strong>[${escape(String(dec.result || '').toUpperCase())}] ${escape(dec.action)}</strong>（${escape(dec.source_run_id)}${dec.child_run_id ? ` → ${escape(dec.child_run_id)}` : ''}）：${escape(dec.reason)}${parts.length ? ` · <em>${escape(parts.join('，'))}</em>` : ''}</li>`;
    }).join('');
    detailHtml = `<div class="direct-rec-card"><div class="section-title"><h3>恢复报告详情 · ${escape(detail.root_run_id)}</h3>${recoveryOutcomeChip(detail.outcome)}</div><p>${escape(detail.summary_zh)}</p><div class="direct-rec-integrity"><strong>输入完整性验证：</strong><span>网表 SHA-256 ${integrity.preserved_across_all_attempts ? '全程一致 ✓' : '存在差异'}</span><span>·</span><span>科学条件未修改 ✓</span><span>·</span><span>预算：尝试 ${budget.attempts_used}/${budget.max_recovery_attempts} 次 · 峰值内存 ${((budget.peak_memory_bytes || 0) / 2**30).toFixed(2)} / ${((budget.max_memory_bytes || 0) / 2**30).toFixed(2)} GiB</span></div><div class="direct-rec-table-wrap"><table class="direct-rec-table"><thead><tr><th>尝试</th><th>Run ID</th><th>主机</th><th>内存</th><th>Job ID</th><th>执行状态</th><th>故障类</th><th>五维结论 (执行/测量/性能/SOA)</th></tr></thead><tbody>${attemptRows}</tbody></table></div><h4>恢复决策与变更记录</h4><ol class="direct-rec-decisions">${decisionRows || '<li>暂无决策记录。</li>'}</ol><div class="direct-actions"><button type="button" class="button secondary small" data-action="direct-rec-download" data-id="${escape(detail.root_run_id)}">下载 Markdown 报告 (.md)</button></div><details class="host-form-details"><summary>查看完整 Markdown 报告（本地路径：${escape(detail.markdown_path || '未记录')}）</summary><pre class="direct-rec-md">${escape(markdown)}</pre></details></div>`;
  }

  const wp = directState.watchPolicy || {
    enabled: false, auto_push_to_agent: true, queue_timeout_seconds: 1800,
    allow_host_migration: true, allow_memory_scale: true, max_memory_gib: 64,
    allow_sim_option_tuning: true, max_agent_resubmissions: 3, protect_netlist_and_spec: true,
  };
  const watchStateChip = s => {
    if (s === 'resolved_resubmitted') return chip('Agent 已重提解决', 'green');
    if (s === 'awaiting_morning_review') return chip('留待早起人工审阅', 'amber');
    if (s === 'pushed_to_agent' || s === 'agent_investigating') return chip('已推送给用户 Agent', 'blue');
    if (s === 'dismissed') return chip('已归档', '');
    return chip('已记录待认领', '');
  };
  const watchRowsHtml = (directState.watchEvents || []).length
    ? directState.watchEvents.map(ev => {
      const dec = ev.agent_decision;
      const resRows = (dec?.resource_assessment || []).map(r => `<tr><td><code>${escape(r.host_id)}</code></td><td>${escape(r.idle_cpus ?? '-')} 核</td><td>${escape(r.available_gib ?? '-')} GiB</td><td>${escape(r.verdict || '-')}</td></tr>`).join('');
      const cfgDiff = dec?.config_changes ? Object.entries(dec.config_changes).map(([k, v]) => `<li><code>${escape(k)}</code>：${escape(typeof v === 'object' && v !== null && 'from' in v ? `${JSON.stringify(v.from)} → ${JSON.stringify(v.to)}` : String(v))}</li>`).join('') : '';
      const tlRows = (ev.timeline || []).map(t => `<li><strong>[${escape(t.actor)}]</strong> ${escape(t.note)} <span class="subtle-note">(${escape(String(t.at || '').slice(11, 19))})</span></li>`).join('');
      return `<div class="direct-rec-card"><div class="section-title"><strong>🦉 ${escape(ev.anomaly_type)} · <code>${escape(ev.run_id)}</code></strong>${watchStateChip(ev.state)}</div><p class="subtle-note">${escape(ev.summary)}</p><div class="direct-rec-integrity"><span>原主机：<code>${escape(ev.host_id)}</code> (${escape(ev.backend)})</span><span>·</span><span>Agent 推送：${ev.agent_handoff?.pushed ? '已自动唤醒 ✓' : escape(ev.agent_handoff?.push_status || '待推送')}</span>${ev.new_run_id ? `<span>·</span><span>新提交：<code>${escape(ev.new_run_id)}</code></span>` : ''}</div>${dec ? `<p style="margin-top:8px"><strong>Agent 排查结论与依据：</strong>${escape(dec.rationale)}</p>` : ''}${resRows ? `<div class="direct-rec-table-wrap"><table class="direct-rec-table"><thead><tr><th>主机节点</th><th>空闲 CPU</th><th>可用内存</th><th>SpiceOwl 资源评估</th></tr></thead><tbody>${resRows}</tbody></table></div>` : ''}${cfgDiff ? `<h4>配置变更与科学红线校验</h4><ul class="direct-rec-decisions">${cfgDiff}</ul>` : ''}${tlRows ? `<details><summary>查看完整夜间值守时间线（${(ev.timeline || []).length} 步）与 Agent 交接提示词</summary><ol class="direct-rec-decisions">${tlRows}</ol>${ev.agent_handoff?.prompt_preview ? `<pre class="direct-rec-md">${escape(ev.agent_handoff.prompt_preview)}</pre>` : ''}</details>` : ''}${!ev.agent_handoff?.pushed ? `<div class="direct-actions" style="margin-top:8px"><button type="button" class="button secondary small" data-action="direct-watch-push" data-id="${escape(ev.event_id)}">立即推送给已连接的 Agent</button></div>` : ''}</div>`;
    }).join('')
    : '<p class="subtle-note">暂无夜间值守异常记录。点上方按钮可一键演练「排队超时 / OOM → 推送给用户 Agent → 查机房资源并重提 → 早上网页验收」全流程。</p>';

  const watchHandoffCard = `<section class="panel direct-panel direct-rec-panel"><div class="section-title"><h2>夜间值守与 Agent 自主交接中心（无外部软件打扰 · 仅网页留痕）</h2>${chip('人类安心睡眠 · AGENT HANDOFF', 'green')}</div><p class="subtle-note">兑现 <em>“你去休息，仿真我盯”</em>：不接入外部聊天催命符，所有排队超时、License 阻塞、OOM 或收敛异常只在网页端安静留痕，并自动推送给您接入的 Agent（Codex / CircuitGit 宿主），由 Agent 查 SpiceOwl 实时主机资源、在授权边界内换主机/扩内存/调配置并提交可追溯新任务；超出边界或涉及网表改动则暂停留待早起人工审阅。</p><details class="host-form-details" open><summary>睡前自治授权边界（Overnight Autonomy Policy · Agent 状态：${directState.watchAgentConnected ? '已连接' : '演示/待连接'}）</summary><div class="direct-grid" style="margin-top:8px"><label class="check-row"><input id="wp-enabled" type="checkbox" ${wp.enabled ? 'checked' : ''}> 开启夜间 Agent 自主处置（边界内免人工弹窗）</label><label class="check-row"><input id="wp-autopush" type="checkbox" ${wp.auto_push_to_agent ? 'checked' : ''}> 异常发生时自动唤醒我接入的 Agent</label><label class="check-row"><input id="wp-migrate" type="checkbox" ${wp.allow_host_migration ? 'checked' : ''}> 允许 Agent 迁移至更空闲主机</label><label class="check-row"><input id="wp-mem" type="checkbox" ${wp.allow_memory_scale ? 'checked' : ''}> 允许 Agent 扩配内存</label><label class="check-row"><input id="wp-opt" type="checkbox" ${wp.allow_sim_option_tuning ? 'checked' : ''}> 允许调整队列/线程/仿真选项</label><label>排队超时阈值（秒）<input id="wp-qtimeout" type="number" min="30" max="86400" value="${escape(wp.queue_timeout_seconds)}"></label><label>夜间内存上限（GiB）<input id="wp-maxmem" type="number" min="1" max="1024" value="${escape(wp.max_memory_gib)}"></label><label>最多自主重提次数<input id="wp-maxresub" type="number" min="0" max="10" value="${escape(wp.max_agent_resubmissions)}"></label></div><div class="direct-actions" style="margin-top:8px"><button type="button" class="button secondary small" data-action="direct-watch-policy-save">保存睡前自治边界</button><span class="subtle-note">🔒 科学红线强制锁定：严禁擅自修改原始网表、激励或验收标准。</span></div></details><div class="direct-rec-filters" style="margin-top:12px"><select id="watch-sim-scenario"><option value="queue_timeout" ${directState.watchSimScenario === 'queue_timeout' ? 'selected' : ''}>场景 1：排队超时 → Agent 换空闲主机重提</option><option value="confirmed_oom" ${directState.watchSimScenario === 'confirmed_oom' ? 'selected' : ''}>场景 2：确认 OOM → Agent 扩内存重提</option><option value="convergence_needs_review" ${directState.watchSimScenario === 'convergence_needs_review' ? 'selected' : ''}>场景 3：收敛失败 → 守红线留待早起审阅</option></select><button type="button" class="button primary small" data-action="direct-watch-simulate">🦉 演练夜间异常 → Agent 自主交接</button></div>${watchRowsHtml}</section>`;

  return `${watchHandoffCard}<section class="panel direct-panel direct-rec-panel"><div class="section-title"><h2>异常自动恢复审计报告（可搜索归档）</h2><span class="section-note">共 ${rows.length} 份报告</span></div><p class="subtle-note">所有触发过有界自动恢复（例如确认 OOM 后自动内存翻倍或许可证退避重试）的任务谱系都会自动保留网表 SHA-256 一致性证明与决策日志，支持一键导出 Markdown。</p><div class="direct-rec-filters"><input id="direct-rec-query" value="${escape(directState.recoveryQuery)}" placeholder="按 Run ID、主机、故障类型（如 confirmed_oom / license）或摘要搜索"><select id="direct-rec-outcome"><option value="" ${directState.recoveryOutcome === '' ? 'selected' : ''}>全部结论</option><option value="recovered" ${directState.recoveryOutcome === 'recovered' ? 'selected' : ''}>已自动恢复</option><option value="in_progress" ${directState.recoveryOutcome === 'in_progress' ? 'selected' : ''}>恢复执行中</option><option value="exhausted" ${directState.recoveryOutcome === 'exhausted' ? 'selected' : ''}>预算耗尽停止</option><option value="not_recoverable" ${directState.recoveryOutcome === 'not_recoverable' ? 'selected' : ''}>不可自动恢复</option><option value="policy_disabled" ${directState.recoveryOutcome === 'policy_disabled' ? 'selected' : ''}>未开启恢复</option></select><button type="button" class="button secondary small" data-action="direct-rec-search" ${directState.busy ? 'disabled' : ''}>检索报告</button></div><div class="direct-run-list">${listHtml}</div>${detailHtml}</section>`;
}

function directRunChip(run) {
  const s = run.state || 'unknown';
  if (s === 'simulator_complete_pending_measurements') return chip('execution completed', 'green');
  if (s === 'running_observed' || s === 'submitted') return chip(s === 'running_observed' ? 'running' : 'queued', 'blue');
  if (s === 'simulator_failed' || s === 'job_setup_failed' || s === 'input_hash_failure') return chip(s, 'red');
  if (s === 'launch_outcome_unknown' || s === 'reconcile_required') return chip(s, 'amber');
  return chip(s, '');
}

function directReportSection(report) {
  if (!report) return '';
  if (report.schema === 'spiceowl.simulation-report.v1') {
    const dims = [
      ['Execution', report.execution_status?.status],
      ['Measurement', report.measurement_validity?.status],
      ['Performance', report.functional_performance?.status],
      ['Operating / SOA', report.operating_qualification?.status],
      ['Dependencies', report.dependency_provenance?.status],
    ];
    const findings = Array.isArray(report.diagnostics?.diagnostics) ? report.diagnostics.diagnostics.slice(0, 8) : [];
    return `<div class="direct-report"><h3>五维仿真报告（Spectre）</h3><div class="direct-report-grid">${dims.map(([k, v]) => `<div class="direct-dim-card"><span>${escape(k)}</span><strong>${escape(title(v))}</strong></div>`).join('')}</div>${findings.map(item => `<div class="live-diagnostic"><strong>${escape(item.code || item.category)} · ${escape(title(item.severity))}</strong>${item.redacted_excerpt ? `<pre>${escape(item.redacted_excerpt)}</pre>` : ''}<ul>${(Array.isArray(item.suggested_actions) ? item.suggested_actions : []).slice(0, 4).map(act => `<li>${escape(act)}</li>`).join('')}</ul></div>`).join('')}</div>`;
  }
  return `<div class="direct-report"><h3>收集报告（${escape(report.simulator || 'direct')}）</h3><pre class="plan-json">${escape(JSON.stringify(report, null, 2))}</pre></div>`;
}

// Safe Markdown subset ported from CircuitGit's ai-panel.js (no raw HTML injection).
function renderMarkdownInline(text) {
  const raw = String(text ?? '');
  const tokens = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*|\[[^\]\n]+\]\([^\s)]+\))/g;
  let out = '';
  let offset = 0;
  for (const match of raw.matchAll(tokens)) {
    out += escape(raw.slice(offset, match.index));
    const tok = match[0];
    if (tok.startsWith('`')) {
      out += `<code>${escape(tok.slice(1, -1))}</code>`;
    } else if (tok.startsWith('**')) {
      out += `<strong>${escape(tok.slice(2, -2))}</strong>`;
    } else if (tok.startsWith('*')) {
      out += `<em>${escape(tok.slice(1, -1))}</em>`;
    } else {
      const link = /^\[([^\]]+)\]\((.+)\)$/.exec(tok);
      let safeHref = '';
      if (link) {
        try {
          const u = new URL(link[2]);
          if (u.protocol === 'https:' || u.protocol === 'http:') safeHref = u.href;
        } catch { /* ignore */ }
      }
      out += safeHref
        ? `<a href="${escape(safeHref)}" target="_blank" rel="noopener noreferrer">${escape(link[1])}</a>`
        : escape(tok);
    }
    offset = match.index + tok.length;
  }
  out += escape(raw.slice(offset));
  return out;
}

function renderMarkdownHtml(text) {
  const lines = String(text ?? '').split(/\r?\n/);
  const parts = [];
  let para = [];
  let listTag = '';
  let listItems = [];
  let inCode = false;
  let codeLines = [];

  const flushPara = () => {
    if (para.length) {
      parts.push(`<p>${para.map(renderMarkdownInline).join('<br>')}</p>`);
      para = [];
    }
  };
  const flushList = () => {
    if (listTag && listItems.length) {
      parts.push(`<${listTag}>${listItems.map(li => `<li>${renderMarkdownInline(li)}</li>`).join('')}</${listTag}>`);
    }
    listTag = '';
    listItems = [];
  };
  const parseTableRow = rawLine => {
    const trimmed = String(rawLine || '').trim();
    if (!trimmed.includes('|')) return null;
    let body = trimmed;
    if (body.startsWith('|')) body = body.slice(1);
    if (body.endsWith('|')) body = body.slice(0, -1);
    const cells = body.split('|').map(c => c.trim());
    return cells.length >= 2 ? cells : null;
  };
  const parseTableAligns = rawLine => {
    const cells = parseTableRow(rawLine);
    if (!cells) return null;
    if (!cells.every(c => /^:?-{1,}:?$/.test(c))) return null;
    return cells.map(c => {
      const left = c.startsWith(':');
      const right = c.endsWith(':');
      if (left && right) return 'center';
      if (right) return 'right';
      return 'left';
    });
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      flushPara();
      flushList();
      if (inCode) {
        parts.push(`<pre><code>${escape(codeLines.join('\n'))}</code></pre>`);
        codeLines = [];
        inCode = false;
      } else {
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      codeLines.push(line);
      continue;
    }
    if (!line.trim()) {
      flushPara();
      flushList();
      continue;
    }
    const headerCells = parseTableRow(line);
    const aligns = headerCells && (i + 1 < lines.length) ? parseTableAligns(lines[i + 1]) : null;
    if (headerCells && aligns && aligns.length >= 2) {
      flushPara();
      flushList();
      const colCount = Math.max(headerCells.length, aligns.length);
      const bodyRows = [];
      let j = i + 2;
      while (j < lines.length) {
        const nextLine = lines[j];
        if (!nextLine.trim() || /^\s*```/.test(nextLine)) break;
        const rowCells = parseTableRow(nextLine);
        if (!rowCells) break;
        bodyRows.push(rowCells);
        j++;
      }
      const alignAttr = idx => (aligns[idx] && aligns[idx] !== 'left' ? ` style="text-align:${aligns[idx]}"` : '');
      const thHtml = Array.from({length: colCount}, (_, idx) => `<th${alignAttr(idx)}>${renderMarkdownInline(headerCells[idx] ?? '')}</th>`).join('');
      const trHtml = bodyRows.map(row => `<tr>${Array.from({length: colCount}, (_, idx) => `<td${alignAttr(idx)}>${renderMarkdownInline(row[idx] ?? '')}</td>`).join('')}</tr>`).join('');
      parts.push(`<div class="md-table-wrap"><table class="md-table"><thead><tr>${thHtml}</tr></thead>${trHtml ? `<tbody>${trHtml}</tbody>` : ''}</table></div>`);
      i = j - 1;
      continue;
    }
    const listM = /^\s*(?:([-*])|\d+\.)\s+(.+)$/.exec(line);
    const headM = /^#{1,4}\s+(.+)$/.exec(line);
    if (listM) {
      flushPara();
      const tag = listM[1] ? 'ul' : 'ol';
      if (listTag && listTag !== tag) flushList();
      listTag = tag;
      listItems.push(listM[2]);
    } else if (headM) {
      flushPara();
      flushList();
      parts.push(`<p class="sim-ai-heading"><strong>${renderMarkdownInline(headM[1])}</strong></p>`);
    } else {
      flushList();
      para.push(line);
    }
  }
  if (inCode && codeLines.length) parts.push(`<pre><code>${escape(codeLines.join('\n'))}</code></pre>`);
  flushPara();
  flushList();
  return parts.join('') || `<p>${escape(text || '')}</p>`;
}

const SIM_CTX_OPEN = '[SPICEOWL_CTX]';
const SIM_CTX_CLOSE = '[/SPICEOWL_CTX]';

function stripSimAgentContextEnvelope(text) {
  const s = String(text ?? '');
  const start = s.indexOf(SIM_CTX_OPEN);
  const end = s.indexOf(SIM_CTX_CLOSE);
  if (start !== -1 && end !== -1 && end > start) {
    return (s.slice(0, start) + s.slice(end + SIM_CTX_CLOSE.length)).trim();
  }
  return s;
}

function stripRnlCommentsForAgent(netlistText) {
  const lines = String(netlistText || '').split(/\r?\n/);
  const kept = [];
  let inRnl = false;
  let rnlDepth = 0;
  for (const ln of lines) {
    const t = ln.trim();
    if (!inRnl && t.startsWith('//') && t.includes('@RNL')) {
      const opens = (t.match(/\{/g) || []).length;
      const closes = (t.match(/\}/g) || []).length;
      if (opens > closes) { inRnl = true; rnlDepth = opens - closes; }
      continue;
    }
    if (inRnl) {
      const opens = (t.match(/\{/g) || []).length;
      const closes = (t.match(/\}/g) || []).length;
      rnlDepth += opens - closes;
      if (rnlDepth <= 0) inRnl = false;
      continue;
    }
    kept.push(ln);
  }
  return kept.join('\n').trim();
}

function buildSimAgentWorkspaceContext() {
  const lines = [buildSimAgentContextSummary()];
  lines.push(`当前选中主机：${directDraft.host_id || '未选择'}；仿真器：${directDraft.simulator || 'spectre'}；执行后端：${directDraft.backend || 'ssh_direct'}；资源预算：${directDraft.cpus} 核 / ${directDraft.memory_gib} GiB / ${directDraft.wall_seconds}s。`);
  lines.push('提交流程提示：新建并启动仿真优先单次调用 spiceowl_direct_control(operation="submit")（自动复用当前网表与 Project/TB 元数据，支持用 netlist_edits 做精准局部修改，1 次调用完成准备、上传与启动）；对已处于 prepared 状态的任务可直接调用 spiceowl_direct_control(operation="stage_and_launch") 一次上传并启动。');
  if (directState.agentLinkedRunId) {
    const linked = (directState.runs || []).find(r => r.run_id === directState.agentLinkedRunId) || (directState.detail?.run_id === directState.agentLinkedRunId ? directState.detail : null);
    if (linked) {
      const org = resolveRunOrganization(linked);
      const st = classifyDirectRunState(linked);
      lines.push(`当前对接的历史/待处理任务：run_id=${linked.run_id}，状态=${linked.state}（${st.label}），主机=${linked.host_id}，仿真器=${linked.simulator}，项目/TB=${org.project}/${org.testbench}；可直接用 spiceowl_direct_read(inspect/inputs/report) 或 spiceowl_direct_control(stage_and_launch/status/collect/cancel/delete) 操作此任务，或用 operation="submit" 一步新提。`);
    }
  }
  const recentRuns = (directState.runs || []).slice(0, 6);
  if (recentRuns.length) {
    const summary = recentRuns.map(r => {
      const org = resolveRunOrganization(r);
      return `${r.run_id}[${r.state}@${r.host_id},${org.project}/${org.testbench}]`;
    }).join(' ; ');
    lines.push(`已有本地仿真任务列表（最近 ${recentRuns.length} 条）：${summary}。`);
  }
  if (simSourceState.origin !== 'none') {
    const label = simSourceState.fromSchematic && simSourceState.cellName
      ? `${simSourceState.libraryName ? simSourceState.libraryName + '/' : ''}${simSourceState.cellName} (${simSourceState.viewId || 'schematic'})`
      : (simSourceState.fileName || simSourceState.projectName || '当前网表');
    lines.push(`当前电路来源：${label}（${simSourceState.origin}${simSourceState.sshTarget ? ' · ' + simSourceState.sshTarget : ''}）。`);
  }
  const cleanNet = stripRnlCommentsForAgent(directDraft.netlist_text || '');
  if (cleanNet) {
    const maxChars = 9000;
    const excerpt = cleanNet.length > maxChars ? cleanNet.slice(0, maxChars) + '\n... (网表已截断，保留前 9000 字符)' : cleanNet;
    lines.push(`当前编辑网表（${simSourceState.fileName || 'input.scs'}，已省略 RNL 图形注释）：\n\`\`\`spectre\n${excerpt}\n\`\`\``);
  } else {
    lines.push('当前网表内容为空（用户尚未在右侧 Library Manager 选入网表）。');
  }
  return `${SIM_CTX_OPEN}\n${lines.join('\n')}\n${SIM_CTX_CLOSE}`;
}

async function loadCodexCatalogAndAuth({force = false} = {}) {
  if (!isStandaloneAgent() || !codexPanelState.status?.connected) return;
  if (codexPanelState.modelsLoaded && !force) return;
  codexPanelState.modelsLoaded = true;
  try {
    const [mRes, aRes] = await Promise.all([
      api('/api/codex/models').catch(() => null),
      api('/api/codex/auth').catch(() => null),
    ]);
    if (mRes && Array.isArray(mRes.models)) {
      codexPanelState.models = mRes.models;
      codexPanelState.defaultModel = mRes.default_model || '';
    }
    if (aRes && aRes.account) {
      codexPanelState.authAccount = aRes.account;
    }
    if (location.hash === '#direct') render();
  } catch { /* older connector versions may not expose /models or /auth */ }
}

function formatCodexAccountLabel(account) {
  if (!account) return '使用本机 Codex 登录与配置';
  if (account.account_type === 'chatgpt' || account.account_type === 'chatgptAuthTokens') {
    return `Codex 已登录 · ${account.plan_type && account.plan_type !== 'unknown' ? account.plan_type : '订阅已就绪'}`;
  }
  if (account.login_status === 'signed_out') return 'Codex 尚未登录（请先在本机完成 Codex 登录）';
  if (account.requires_openai_auth === false) return '使用本机已配置的模型服务';
  if (account.login_status === 'signed_in') return '使用本机已有登录';
  return '使用本机 Codex 配置';
}

function simAgentStatusChip(status) {
  if (!status?.connected) return chip('未连接', '');
  const s = status.state;
  if (s === 'starting' || s === 'running') return chip('思考中…', 'blue');
  if (s === 'awaiting_approval') return chip('待审批', 'amber');
  if (s === 'interrupted') return chip('已暂停', 'amber');
  if (status.recovery_required || s === 'unknown') return chip('待核对', 'amber');
  if (s === 'failed') return chip('异常', 'red');
  return chip('已就绪', 'green');
}

function codexPermissionMode() {
  const mode = codexPanelState.status?.permission_mode;
  return ['safe', 'supervised', 'full_access'].includes(mode) ? mode : 'safe';
}

function simAgentPermissionChip(mode = codexPermissionMode()) {
  if (mode === 'full_access') return chip('🚀 满血全权', 'amber');
  if (mode === 'supervised') return chip('⚡ 满血受控', 'blue');
  return chip('🔒 安全托管', '');
}

function codexComposerHint(mode = codexPermissionMode()) {
  if (mode === 'full_access') {
    return '🚀 满血全权已开启：Codex 可直接运行本机 Shell/Python 与一键仿真提交（随时可点「⏹ 停止」急停）。';
  }
  if (mode === 'supervised') {
    return '⚡ 满血受控已开启：开放原生 Shell/Python/Skill 与网页联动，高危写入/提交需人工批准。';
  }
  return '已自动附带当前电路、Maestro 与实时服务器余量；仅远程变更需人工批准。';
}

function renderCodexPermissionBar() {
  if (!isStandaloneAgent() || isPublicDemo()) return '';
  const mode = codexPermissionMode();
  const locked = Boolean(codexPanelState.busy || codexActiveTurn() || codexPanelState.status?.recovery_required);
  const confirmOpen = Boolean(codexPanelState.permissionConfirmPending && mode !== 'full_access');
  const modes = [
    {id: 'safe', label: '🔒 安全托管', cls: ''},
    {id: 'supervised', label: '⚡ 满血受控', cls: 'is-supervised'},
    {id: 'full_access', label: '🚀 满血全权', cls: 'is-full'},
  ];
  const bar = `<div class="sim-perm-bar" role="group" aria-label="Codex 权限等级">${modes.map(m => `<button type="button" class="sim-perm-btn ${m.cls}${mode === m.id ? ' is-active' : ''}" data-action="codex-set-permission" data-mode="${m.id}" aria-pressed="${mode === m.id ? 'true' : 'false'}" ${locked ? 'disabled' : ''}>${m.label}</button>`).join('')}</div>`;
  const confirmBox = confirmOpen
    ? `<div class="sim-perm-confirm" role="alertdialog" aria-label="确认开启满血全权"><p>确认开启「满血全权（Full Access）」？开启后网页端 Codex 将拥有与本机终端完全一致的 Shell / Python / 文件 / SSH 与一键仿真提交权限，工具与命令将自动执行不再逐项弹窗（你可随时点击「⏹ 停止」急停）。仅限本机受信任环境使用。</p><div class="sim-perm-confirm-actions"><button type="button" class="button primary small sim-perm-confirm-go" data-action="codex-confirm-full-access" ${locked ? 'disabled' : ''}>🚀 确认开启满血全权</button><button type="button" class="button secondary small" data-action="codex-cancel-full-access" ${codexPanelState.busy ? 'disabled' : ''}>取消</button></div></div>`
    : '';
  return `${bar}${confirmBox}`;
}

function formatShellCmd(raw) {
  if (Array.isArray(raw)) return raw.map(part => String(part ?? '')).join(' ').trim();
  return typeof raw === 'string' ? raw.trim() : '';
}

function formatCommandExecutionText(cmd, output, exitCode) {
  const parts = [];
  if (cmd) parts.push(`$ ${cmd}`);
  if (typeof output === 'string' && output.length) parts.push(output.replace(/\n+$/, ''));
  if (Number.isFinite(exitCode)) parts.push(`[exit ${exitCode}]`);
  return parts.join('\n');
}

function pickMostIdleHostId() {
  const list = state.hosts || [];
  if (!list.length) return '';
  let best = list[0].host_id;
  let bestScore = Infinity;
  for (const h of list) {
    const live = hostLiveState.byId.get(h.host_id);
    const res = live?.ok ? live.resources : h.last_resources?.resources;
    const cpu = Number.isFinite(res?.cpu_busy_percent) ? res.cpu_busy_percent : 999;
    const memUsed = Number.isFinite(res?.memory_used_percent) ? res.memory_used_percent : 50;
    const score = cpu + memUsed * 0.25;
    if (score < bestScore) {
      bestScore = score;
      best = h.host_id;
    }
  }
  return best;
}

function extractNetlistCodeBlock(text) {
  if (typeof text !== 'string') return '';
  const blocks = [...text.matchAll(/```(?:spectre|spice|scs|sp|cir|text)?\r?\n([\s\S]*?)```/gi)].map(m => m[1].trim()).filter(Boolean);
  const spiceBlock = blocks.find(b => /simulator\s+lang=|tran\s+|ac\s+|dc\s+|\.tran|\.ac|vsource|resistor|capacitor/i.test(b));
  return spiceBlock || blocks[0] || '';
}

function buildDirectPreparePayload() {
  const payload = {
    run_key: directDraft.run_key.trim(),
    netlist_text: directDraft.netlist_text,
    backend: directDraft.backend,
    simulator: directDraft.simulator,
    output_format: directDraft.output_format,
    cpus: Number(directDraft.cpus),
    memory_gib: Number(directDraft.memory_gib),
    wall_seconds: Number(directDraft.wall_seconds),
    remote_python: directDraft.remote_python.trim() || '/usr/bin/python3',
    remote_root: directDraft.remote_root.trim() || '.spiceowl-runs',
    recovery_policy: {
      enabled: !!directDraft.rec_enabled,
      max_recovery_attempts: Number(directDraft.rec_max_attempts),
      max_memory_bytes: Math.round(Math.max(Number(directDraft.rec_max_memory_gib), Number(directDraft.memory_gib)) * 2**30),
      max_elapsed_seconds: Math.max(60, Math.round(Number(directDraft.rec_max_elapsed_min) * 60)),
      retry_on_license: !!directDraft.rec_retry_license,
      license_backoff_seconds: Number(directDraft.rec_license_backoff),
      retry_on_oom: !!directDraft.rec_retry_oom,
      oom_memory_multiplier: Number(directDraft.rec_oom_multiplier) || 2.0,
      allow_host_relocation: !!directDraft.rec_allow_relocate,
    },
  };
  if (directDraft.target_mode === 'pool') {
    payload.host_ids = state.hosts.slice(0, 32).map(h => h.host_id);
  } else {
    payload.host_id = directDraft.host_id;
  }
  const includes = directDraft.include_dirs.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  if (includes.length) payload.include_dirs = includes;
  const supKeys = Object.keys(simSourceState.supportFiles || {});
  if (supKeys.length) payload.support_files = {...simSourceState.supportFiles};
  if (/^[a-f0-9]{40}$/.test(simSourceState.sourceCommit || '')) payload.source_commit = simSourceState.sourceCommit;
  const metaProject = (simSourceState.projectName || simSourceState.libraryName || '').trim();
  const metaLibrary = (simSourceState.libraryName || '').trim();
  const metaCell = (simSourceState.cellName || (simSourceState.fileName ? simSourceState.fileName.replace(/\.[^.]+$/, '') : '')).trim();
  const metaView = (simSourceState.viewId || '').trim();
  if (metaProject) payload.project_name = metaProject.slice(0, 160);
  if (metaLibrary) payload.library_name = metaLibrary.slice(0, 160);
  if (metaCell) payload.cell_name = metaCell.slice(0, 160);
  if (metaView) payload.view_name = metaView.slice(0, 120);
  if (simSourceState.origin && simSourceState.origin !== 'none') payload.source_origin = simSourceState.origin;
  if (typeof directDraft.submission_group_id === 'string' && directDraft.submission_group_id.trim()) {
    payload.submission_group_id = directDraft.submission_group_id.trim().slice(0, 160);
  }
  if (typeof directDraft.submission_label === 'string' && directDraft.submission_label.trim()) {
    payload.submission_label = directDraft.submission_label.trim().slice(0, 160);
  }
  const mods = directDraft.env_modules.split(/[,\s]+/).map(s => s.trim()).filter(Boolean);
  const scs = directDraft.env_scripts.split(/[,\r\n]+/).map(s => s.trim()).filter(Boolean);
  if (mods.length || scs.length) payload.remote_env = {modules: mods, setup_scripts: scs, shell: 'bash'};
  if (directDraft.backend === 'lsf') {
    payload.scheduler = {queue: directDraft.queue.trim(), exclusive: !!directDraft.exclusive};
    if (directDraft.project.trim()) payload.scheduler.project = directDraft.project.trim();
    if (directDraft.job_group.trim()) payload.scheduler.job_group = directDraft.job_group.trim();
  } else if (directDraft.backend === 'slurm') {
    payload.scheduler = {partition: directDraft.partition.trim(), exclusive: !!directDraft.exclusive};
    if (directDraft.account.trim()) payload.scheduler.account = directDraft.account.trim();
    if (directDraft.qos.trim()) payload.scheduler.qos = directDraft.qos.trim();
  }
  return payload;
}

async function quickLaunchDirectSimulation() {
  if (isPublicDemo() || directState.busy) return;
  if (!directDraft.netlist_text.trim()) {
    toast('请先通过「打开 Library Manager」从远端电路库 / Virtuoso schematic 或本地导入网表，或直接粘贴网表。');
    return;
  }
  if (!state.hosts.length) {
    toast('还没有连接服务器，请先回「首页与连接」接入服务器。');
    return;
  }
  directState.busy = true;
  directState.error = null;
  directState.quickLaunchError = null;
  directState.quickLaunchNote = '⚡ 正在一键准备、上传并启动仿真…';
  render();
  try {
    const res = await api('/api/direct/submit', {
      method: 'POST',
      body: {...buildDirectPreparePayload(), recheck_resources: false},
    });
    const runId = res?.run?.run_id || '';
    const hostId = res?.run?.host_id || directDraft.host_id || '';
    if (runId) {
      directState.selectedId = runId;
      overviewState.selectedRunId = runId;
    }
    directDraft.run_key = '';
    directState.quickLaunchNote = `🚀 仿真 ${runId} 已在 ${hostId} 成功启动！`;
    hostLiveState.loaded = false;
    toast(`🚀 仿真 ${runId} 已成功启动！可查看任务控制或回「服务器池资源」查看紫色个人资源条。`);
  } catch (error) {
    directState.quickLaunchError = error.message;
    directState.quickLaunchNote = null;
    toast(error.message);
  } finally {
    directState.busy = false;
    await refreshDirect();
  }
}

function formatCodexErrorMessage(error) {
  const raw = String(error?.message || '请求失败');
  if (/Codex reused an earlier turn identity/i.test(raw)) {
    return `${raw}（旧会话回合冲突，请点上方「一键归档并恢复对话」或「新对话」）`;
  }
  return raw;
}

async function sendPromptToSimAgent(promptText) {
  if (!isStandaloneAgent() || isCircuitGitManagedEntry() || codexPanelState.busy || codexActiveTurn()) return;
  const userText = String(promptText || '').trim();
  if (!userText) return;
  codexPanelState.busy = true;
  codexPanelState.error = null;
  codexPanelState.turnStartedAt = Date.now();
  codexPanelState.lastTurnStatus = 'running';
  codexPanelState.lastTurnDurationMs = null;
  renderCodexActivity();
  let request = null;
  try {
    if (state.hosts.length && (!hostLiveState.loaded || Date.now() - hostLiveState.loadedAt > 60000) && !hostLiveState.loading) {
      await Promise.race([
        refreshHostLiveStatus({silent: true}),
        new Promise(resolve => setTimeout(resolve, 4000)),
      ]);
    }
    if (!codexPanelState.status?.connected) {
      await api('/api/codex/connect', {method: 'POST', body: {
        ...(codexPanelState.model.trim() ? {model: codexPanelState.model.trim()} : {}),
        ...(codexPanelState.profile.trim() ? {profile: codexPanelState.profile.trim()} : {}),
      }});
      await refreshCodexStatus();
      void loadCodexCatalogAndAuth({force: true});
    }
    if (!codexPanelState.status?.thread_id) {
      const th = await api('/api/codex/thread', {method: 'POST'});
      codexPanelState.selectedThread = th.thread_id || null;
      await refreshCodexStatus();
    }
    openCodexEvents();
    const fullText = `${buildSimAgentWorkspaceContext()}\n\n${userText}`;
    request = {
      thread_id: codexPanelState.status.thread_id,
      text: fullText.slice(0, 23800),
      request_key: `codex-${crypto.randomUUID()}`,
      ...(codexPanelState.model.trim() ? {model: codexPanelState.model.trim()} : {}),
      ...(codexPanelState.reasoningEffort ? {reasoning_effort: codexPanelState.reasoningEffort} : {}),
    };
    sessionStorage.setItem(CODEX_PENDING_KEY, JSON.stringify({
      thread_id: request.thread_id,
      request_key: request.request_key,
      text: userText.slice(0, 3900),
    }));
    codexPanelState.pending = {thread_id: request.thread_id, request_key: request.request_key, text: userText};
    codexItem(codexThread(request.thread_id), 'request:' + request.request_key, 'user', {
      text: userText,
      status: 'unconfirmed',
      requestKey: request.request_key,
    });
    codexPanelState.draft = '';
    if ($('#codex-message')) $('#codex-message').value = '';
    renderCodexActivity();
    const receipt = await api('/api/codex/message', {method: 'POST', body: request});
    if (receipt.request_key === request.request_key && !receipt.recovery_required) {
      confirmCodexPending(receipt.request_key, receipt.turn_id);
    }
    await refreshCodexStatus();
  } catch (error) {
    const statusOk = await refreshCodexStatus().catch(() => false);
    const status = codexPanelState.status || {};
    const serverRejected = Boolean(error?.status || error?.code);
    const notPendingOnServer = Boolean(statusOk && request && status.request_key !== request.request_key);
    const shouldClearPending = !request || serverRejected || Boolean(status.recovery_required) || notPendingOnServer;
    codexPanelState.turnStartedAt = null;
    codexPanelState.lastTurnStatus = 'failed';
    if (shouldClearPending) {
      const pending = codexPanelState.pending;
      const reqKey = request?.request_key || pending?.request_key;
      const threadId = request?.thread_id || pending?.thread_id;
      if (reqKey && threadId) {
        const uItem = codexThread(threadId)?.items.find(row => row.requestKey === reqKey);
        if (uItem && uItem.status === 'unconfirmed') uItem.status = 'failed';
      }
      codexPanelState.pending = null;
      try { sessionStorage.removeItem(CODEX_PENDING_KEY); } catch { /* ignore */ }
    }
    const msg = formatCodexErrorMessage(error);
    codexPanelState.error = msg;
    toast(msg);
  } finally {
    codexPanelState.busy = false;
    renderCodexActivity();
  }
}

function buildSimAgentContextSummary() {
  const hostLines = (state.hosts || []).map(h => {
    const live = hostLiveState.byId.get(h.host_id);
    const r = live?.ok ? live.resources : h.last_resources?.resources;
    const cpuCount = Number.isFinite(r?.cpu_count) ? r.cpu_count : null;
    const cpuBusy = Number.isFinite(r?.cpu_busy_percent) ? r.cpu_busy_percent : null;
    const freeCores = cpuCount != null && cpuBusy != null ? Math.max(0, Math.round(cpuCount * (100 - cpuBusy) / 100)) : null;
    const cpu = cpuBusy != null ? `${cpuBusy.toFixed(1)}%/${cpuCount || '?'}核${freeCores != null ? '(约空闲' + freeCores + '核)' : ''}` : '未采样';
    const memAvail = Number.isFinite(r?.memory_available_bytes) ? (r.memory_available_bytes / 2**30).toFixed(0) : '';
    const memTotal = Number.isFinite(r?.memory_total_bytes) ? (r.memory_total_bytes / 2**30).toFixed(0) : '';
    const mem = memAvail ? `可用内存${memAvail}${memTotal ? '/' + memTotal : ''}GiB` : '';
    const simCnt = Number.isFinite(live?.simulations?.active_count) ? `运行中仿真${live.simulations.active_count}个` : '';
    const sims = Object.keys(h.simulators || {}).filter(k => h.simulators[k]).join('/');
    return `${h.host_id}(CPU ${cpu}${mem ? ', ' + mem : ''}${simCnt ? ', ' + simCnt : ''}${sims ? ', 支持' + sims : ''})`;
  });
  const licFeatures = hostLiveState.licenses?.features || [];
  const licLines = licFeatures.slice(0, 12).map(f => `${f.label}:${f.in_use}/${f.issued}(余${f.available})`);
  const licText = licLines.length
    ? `SPICE FlexLM License（已实时采样）：${licLines.join('，')}。`
    : 'SPICE FlexLM License：当前服务器池未返回 FlexLM 许可证详情（无需通过 SSH 逐台探测）。';
  const tsText = hostLiveState.observedAt ? `（采样时间 ${hostLiveState.observedAt}，已由本机连接器实时采集，可直接用于选机，无需再调 spiceowl_scan_resources 或 spiceowl_probe_host）` : '';
  let maestroNote = '';
  if (simSourceState.maestroConfig) {
    const mc = simSourceState.maestroConfig;
    const anStr = (mc.analyses || []).map(a => a.stop ? `${a.type}(stop=${a.stop})` : a.type).join(', ');
    const enCorners = (mc.corners || []).filter(c => c.enabled).map(c => c.name).join('/');
    maestroNote = `当前电路来自 Virtuoso ${simSourceState.libraryName || ''}/${simSourceState.cellName || simSourceState.fileName}，已检测 Maestro 配置（分析：${anStr || '未指定'}；变量 ${Object.keys(mc.variables || {}).length} 个${enCorners ? `；启用工艺角 ${enCorners}` : ''}）。`;
  }
  return `当前已连接服务器${tsText}：${hostLines.join('；') || '暂无'}。${licText}${maestroNote}`;
}

function directAgentSetupPanel(hostsList) {
  const bestHostId = pickMostIdleHostId();
  const bestLive = bestHostId ? hostLiveState.byId.get(bestHostId) : null;
  const bestRes = bestLive?.ok ? bestLive.resources : hostsList.find(h => h.host_id === bestHostId)?.last_resources?.resources;
  const status = codexPanelState.status;
  const agentReady = Boolean(status?.connected);

  const hostOptionLabel = h => {
    const lv = hostLiveState.byId.get(h.host_id);
    const r = lv?.ok ? lv.resources : h.last_resources?.resources;
    const cpu = Number.isFinite(r?.cpu_busy_percent) ? `CPU ${r.cpu_busy_percent.toFixed(1)}%` : '';
    const mem = Number.isFinite(r?.memory_available_bytes) ? `空闲 ${(r.memory_available_bytes / 2**30).toFixed(0)} GiB` : '';
    const rec = h.host_id === bestHostId && cpu ? ' · 推荐' : '';
    const extra = [cpu, mem].filter(Boolean).join(' · ');
    return `${h.host_id}${extra ? `（${extra}${rec}）` : ''}`;
  };

  const schedBox = directDraft.backend === 'lsf'
    ? `<div class="direct-sched-box"><strong>LSF 集群参数（bsub）</strong><div class="direct-grid"><label>Queue（必填）<input id="direct-queue" value="${escape(directDraft.queue)}" placeholder="analog_long" required></label><label>Project（可选）<input id="direct-project" value="${escape(directDraft.project)}" placeholder="adc_proj"></label><label class="direct-wide">Job group（可选）<input id="direct-job-group" value="${escape(directDraft.job_group)}" placeholder="/analog/sim"></label></div><div class="direct-check-row"><label><input id="direct-exclusive" type="checkbox" ${directDraft.exclusive ? 'checked' : ''}> 独占计算节点 (-x)</label></div></div>`
    : directDraft.backend === 'slurm'
    ? `<div class="direct-sched-box"><strong>Slurm 集群参数（sbatch）</strong><div class="direct-grid"><label>Partition（必填）<input id="direct-partition" value="${escape(directDraft.partition)}" placeholder="sim_compute" required></label><label>Account（可选）<input id="direct-account" value="${escape(directDraft.account)}" placeholder="analog_lab"></label><label class="direct-wide">QoS（可选）<input id="direct-qos" value="${escape(directDraft.qos)}" placeholder="normal"></label></div><div class="direct-check-row"><label><input id="direct-exclusive" type="checkbox" ${directDraft.exclusive ? 'checked' : ''}> 独占计算节点 (--exclusive)</label></div></div>`
    : '';

  const recBox = `<details class="host-form-details" ${directDraft.rec_enabled ? 'open' : ''}><summary>夜间有界自动恢复（OOM 扩内存 / License 退避重试）</summary><div class="direct-check-row"><label><input id="direct-rec-enabled" type="checkbox" ${directDraft.rec_enabled ? 'checked' : ''}> 启用有界自动恢复</label></div><div class="direct-grid"><label>最大恢复次数 (0–5)<input id="direct-rec-attempts" type="number" min="0" max="5" value="${escape(directDraft.rec_max_attempts)}" ${directDraft.rec_enabled ? '' : 'disabled'}></label><label>内存上限 (GiB)<input id="direct-rec-maxmem" type="number" min="1" max="16384" value="${escape(directDraft.rec_max_memory_gib)}" ${directDraft.rec_enabled ? '' : 'disabled'}></label><label>许可证退避 (秒)<input id="direct-rec-backoff" type="number" min="0" max="3600" value="${escape(directDraft.rec_license_backoff)}" ${directDraft.rec_enabled ? '' : 'disabled'}></label><label>时间预算 (分钟)<input id="direct-rec-elapsed" type="number" min="1" max="10080" value="${escape(directDraft.rec_max_elapsed_min)}" ${directDraft.rec_enabled ? '' : 'disabled'}></label></div><div class="direct-check-row"><label><input id="direct-rec-lic" type="checkbox" ${directDraft.rec_retry_license ? 'checked' : ''} ${directDraft.rec_enabled ? '' : 'disabled'}> License 退避重试</label><label><input id="direct-rec-oom" type="checkbox" ${directDraft.rec_retry_oom ? 'checked' : ''} ${directDraft.rec_enabled ? '' : 'disabled'}> OOM 自动内存翻倍</label><label><input id="direct-rec-relocate" type="checkbox" ${directDraft.rec_allow_relocate ? 'checked' : ''} ${directDraft.rec_enabled ? '' : 'disabled'}> 允许迁移空闲主机</label></div></details>`;

  const circuitGitManaged = isCircuitGitManagedEntry();
  const activeThread = codexPanelState.threads.find(item => item.id === codexPanelState.selectedThread);
  const hasChatMessages = Boolean((activeThread?.items || []).length || codexPanelState.pending);
  const effLabels = [['', '沿用会话'], ['low', '低'], ['medium', '中'], ['high', '高'], ['xhigh', '很高']];
  const modelOptions = [
    `<option value="" ${!codexPanelState.model ? 'selected' : ''}>跟随默认${codexPanelState.defaultModel ? ' · ' + escape(codexPanelState.defaultModel) : ''}</option>`,
    ...(codexPanelState.models || []).map(m => `<option value="${escape(m.id)}" ${codexPanelState.model === m.id ? 'selected' : ''}>${escape(m.displayName || m.id)}</option>`),
  ].join('');

  const agentCard = (isStandaloneAgent() && !circuitGitManaged) ? `
    <section class="sim-agent-card" data-sim-agent-mode="standalone">
      <div class="sim-card-head">
        <h2>✧ Agent 仿真配置助手 <span id="codex-runtime-state">${simAgentStatusChip(status)}</span><span id="codex-perm-chip">${simAgentPermissionChip(codexPermissionMode())}</span></h2>
        <div class="direct-actions">
          ${agentReady
            ? `<button type="button" class="button secondary small sim-ai-gear" data-action="sim-agent-settings" aria-label="AI 设置与连接" aria-expanded="${codexPanelState.settingsOpen ? 'true' : 'false'}" title="模型、思考强度与本机 Codex 连接">⚙</button><button type="button" class="button secondary small" data-action="codex-thread">新对话</button>`
            : `<button type="button" class="button primary small" data-action="sim-agent-connect" ${codexPanelState.busy ? 'disabled' : ''}>${codexPanelState.busy ? '连接中…' : '连接本机 Codex'}</button>`}
        </div>
      </div>
      ${!isPublicDemo() ? `<div id="codex-permission-slot" class="sim-perm-slot">${renderCodexPermissionBar()}</div>` : ''}
      ${agentReady && codexPanelState.settingsOpen ? `
        <div class="sim-ai-settings" role="group" aria-label="AI 设置与连接">
          <div class="sim-ai-settings-account">
            <span>${escape(formatCodexAccountLabel(codexPanelState.authAccount))}</span>
            <div class="direct-actions">
              <button type="button" class="button secondary small" data-action="sim-agent-refresh-models" ${codexPanelState.busy ? 'disabled' : ''}>刷新模型</button>
              <button type="button" class="button secondary small" data-action="codex-disconnect" ${codexPanelState.busy ? 'disabled' : ''}>断开</button>
            </div>
          </div>
          <div class="sim-ai-settings-grid">
            <label>模型
              <select id="sim-ai-model" ${codexPanelState.busy || codexActiveTurn() ? 'disabled' : ''}>${modelOptions}</select>
            </label>
            <label>思考强度
              <select id="sim-ai-effort" ${codexPanelState.busy || codexActiveTurn() ? 'disabled' : ''}>${effLabels.map(([v, l]) => `<option value="${v}" ${codexPanelState.reasoningEffort === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
            </label>
          </div>
          <small class="subtle-note">与 CircuitGit 复用同一本机 Codex 登录与人工审批安全策略。</small>
        </div>` : ''}
      <div id="sim-agent-guide" class="sim-agent-guide" ${hasChatMessages ? 'hidden' : ''}>
        <p class="subtle-note">直接用自然语言描述你想跑的仿真，或点下方快捷指令；对话开始后引导会自动收起。</p>
        <div class="sim-prompt-chips">
          <button type="button" class="sim-prompt-chip" data-action="sim-agent-prompt" data-kind="pick_host" ${codexPanelState.busy ? 'disabled' : ''}>🎯 挑最空闲服务器并核对 SPICE License</button>
          <button type="button" class="sim-prompt-chip" data-action="sim-agent-prompt" data-kind="review_netlist" ${codexPanelState.busy ? 'disabled' : ''}>📝 检查当前网表与仿真配置</button>
          <button type="button" class="sim-prompt-chip" data-action="sim-agent-prompt" data-kind="pvt_plan" ${codexPanelState.busy ? 'disabled' : ''}>▦ 规划 PVT Corner / MC 扫描方案</button>
        </div>
      </div>
      <div id="codex-notices" class="codex-notices"></div>
      ${(() => {
        const linked = directState.agentLinkedRunId
          ? ((directState.runs || []).find(r => r.run_id === directState.agentLinkedRunId) || (directState.detail?.run_id === directState.agentLinkedRunId ? directState.detail : null))
          : null;
        if (!linked) return '';
        const st = classifyDirectRunState(linked);
        const org = resolveRunOrganization(linked);
        return `<div class="sim-linked-run-bar" data-linked-run-id="${escape(linked.run_id)}">
          <div class="sim-linked-run-meta">
            <span>🔗 已对接任务 <code>${escape(linked.run_id)}</code></span>
            ${chip(st.label, st.color)}
            <small>${escape(org.project)} / ${escape(org.testbench)} · 可直接向 AI 提问、修改网表后重提，或让 AI 启动/删除</small>
          </div>
          <div class="sim-linked-run-actions">
            ${['prepared', 'stage_incomplete'].includes(linked.state) ? `<button type="button" class="button primary small" data-action="overview-launch-now" data-id="${escape(linked.run_id)}" ${directState.busy ? 'disabled' : ''}>🚀 启动原任务</button>` : ''}
            <button type="button" class="button secondary small" data-action="sim-agent-unlink-run">取消关联</button>
          </div>
        </div>`;
      })()}
      <div id="codex-timeline" class="sim-agent-timeline" role="log" aria-live="polite" aria-label="Agent 仿真配置对话"></div>
      <div id="codex-approvals" class="codex-approvals"></div>
      <form id="sim-agent-form" class="codex-composer sim-ai-composer">
        <textarea id="codex-message" maxlength="4000" rows="2" placeholder="向 AI 提问或描述仿真需求（Enter 发送，Shift+Enter 换行）…">${escape(codexPanelState.draft)}</textarea>
        <div>
          <span id="codex-composer-hint" class="subtle-note">${escape(codexComposerHint(codexPermissionMode()))}</span>
          <div class="sim-ai-composer-actions">
            <button id="codex-stop" class="button secondary small sim-ai-stop" type="button" data-action="codex-interrupt" ${codexActiveTurn() || codexPanelState.pending ? '' : 'hidden'} ${codexPanelState.interrupting ? 'disabled' : ''}>⏹ 停止</button>
            <button id="codex-send" class="button primary small" type="submit" ${codexPanelState.busy || codexActiveTurn() ? 'disabled' : ''}>发送</button>
          </div>
        </div>
      </form>
    </section>` : `
    <section class="sim-agent-card" data-sim-agent-mode="circuitgit">
      <div class="sim-card-head"><h2>✧ CircuitGit 协同 Agent</h2>${chip('已复用 CircuitGit 对话', 'green')}</div>
      <p class="subtle-note">当前已由 CircuitGit 统一托管项目与 Codex Agent 对话，直接复用 CircuitGit 交互入口，不再重复建立连接。</p>
      <div class="direct-actions">
        <a class="button primary small" href="${escape(simSourceState.returnUrl || circuitGitEntry)}" target="_blank" rel="noopener noreferrer">回到 CircuitGit 对话 ↗</a>
      </div>
    </section>`;

  const bestBanner = bestHostId && Number.isFinite(bestRes?.cpu_busy_percent)
    ? `<div class="sim-rec-host-bar"><span>💡 当前最空闲推荐节点：<strong>${escape(bestHostId)}</strong>（CPU ${bestRes.cpu_busy_percent.toFixed(1)}% · 可用内存 ${Number.isFinite(bestRes.memory_available_bytes) ? (bestRes.memory_available_bytes / 2**30).toFixed(0) + ' GiB' : '—'}）</span>${directDraft.host_id !== bestHostId ? `<button type="button" class="button secondary small" data-action="direct-pick-host" data-id="${escape(bestHostId)}">一键切到 ${escape(bestHostId)}</button>` : '<span>✓ 已选中</span>'}</div>`
    : '';

  const launchBanner = directState.quickLaunchNote
    ? `<div class="sim-launch-status"><strong>${escape(directState.quickLaunchNote)}</strong><div class="direct-actions"><a class="button primary small" href="#overview" data-open-direct-run="${escape(directState.selectedId || '')}">去仿真总览查看与控制 →</a><a class="button secondary small" href="#hosts">回服务器池看紫色资源条</a></div></div>`
    : directState.quickLaunchError
    ? `<div class="sim-launch-status err"><strong>启动未完成：</strong><span>${escape(directState.quickLaunchError)}</span></div>`
    : '';

  const supportEntries = Object.entries(simSourceState.supportFiles || {});

  let sourceBarHtml = '';
  if (circuitGitManaged) {
    const titleBits = [
      simSourceState.projectName || simSourceState.libraryName || 'CircuitGit 当前项目',
      simSourceState.cellName || simSourceState.fileName || '',
    ].filter(Boolean);
    const metaBits = [
      simSourceState.entryPath || simSourceState.fileName || '已绑定 CircuitGit 入口',
      supportEntries.length ? `含 ${supportEntries.length} 个 include 依赖` : '',
      simSourceState.sourceCommit ? `commit ${simSourceState.sourceCommit.slice(0, 8)}` : '',
    ].filter(Boolean);
    sourceBarHtml = `
      <div class="sim-source-bar is-circuitgit" data-circuitgit-entry="active">
        <div class="sim-source-meta">
          <div class="sim-source-title">
            <strong>◉ ${escape(titleBits.join(' / '))}</strong>
            <span class="chip green">已复用 CircuitGit 入口</span>
          </div>
          <small>${escape(metaBits.join(' · '))}</small>
        </div>
        <a class="button secondary small" href="${escape(simSourceState.returnUrl || circuitGitEntry)}" target="_blank" rel="noopener noreferrer">回 CircuitGit 切换电路 ↗</a>
      </div>`;
  } else {
    let srcBadge = '未选项目';
    let srcBadgeCls = '';
    let srcTitle = '选择电路项目或导入网表';
    let srcSub = '可浏览已连接 SSH 服务器上的电路库 / cds.lib 与网表，或导入本地项目文件夹（自动保留相对 include 依赖）。';
    if (simSourceState.origin === 'ssh') {
      srcBadge = `SSH · ${simSourceState.sshTarget || simSourceState.sshHostId}`;
      srcBadgeCls = 'green';
      srcTitle = simSourceState.fromSchematic && simSourceState.cellName
        ? `${simSourceState.libraryName ? simSourceState.libraryName + ' / ' : ''}${simSourceState.cellName} (${simSourceState.viewId || 'schematic'})`
        : (simSourceState.fileName || simSourceState.projectName || '远端网表');
      srcSub = [
        simSourceState.fromSchematic
          ? (simSourceState.maestroApplied ? '已通过 Virtuoso schematic 导出并合并 Maestro 仿真配置' : '已通过 Virtuoso schematic 导出纯电路网表')
          : (simSourceState.remotePath || simSourceState.entryPath),
        simSourceState.rnlApplied && simSourceState.rnlStats
          ? `含 RNL 原图几何（${simSourceState.rnlStats.cells || 1} 级 · ${simSourceState.rnlStats.placed || 0} 器件 · ${simSourceState.rnlStats.wires || 0} 连线）`
          : '',
        supportEntries.length ? `含 ${supportEntries.length} 个相对 include 依赖` : '',
      ].filter(Boolean).join(' · ');
    } else if (simSourceState.origin === 'local') {
      srcBadge = '本地项目';
      srcBadgeCls = 'blue';
      srcTitle = simSourceState.projectName && simSourceState.projectName !== simSourceState.fileName
        ? `${simSourceState.projectName} / ${simSourceState.fileName}`
        : (simSourceState.fileName || '本地网表');
      srcSub = [simSourceState.entryPath, supportEntries.length ? `含 ${supportEntries.length} 个相对 include 依赖` : '单文件网表'].filter(Boolean).join(' · ');
    } else if (simSourceState.origin === 'template') {
      srcBadge = '零 PDK 模板';
      srcBadgeCls = '';
      srcTitle = simSourceState.projectName || '内置烟雾模板';
      srcSub = '适合新服务器 1 秒验证 Spectre / ngspice 环境与 PSF 波形。';
    }
    sourceBarHtml = `
      <div class="sim-source-bar" data-standalone-library-entry="true">
        <div class="sim-source-meta">
          <div class="sim-source-title">
            <strong>📚 ${escape(srcTitle)}</strong>
            <span class="chip ${srcBadgeCls}">${escape(srcBadge)}</span>
          </div>
          <small>${escape(srcSub)}</small>
        </div>
        <div class="direct-actions" style="flex:none">
          <button type="button" class="button primary small" data-action="lib-mgr-open" data-tab="${hostsList.length ? 'ssh' : 'local'}">打开 Library Manager</button>
        </div>
      </div>`;
  }

  const maestroSummaryHtml = simSourceState.maestroConfig ? (() => {
    const mc = simSourceState.maestroConfig;
    const analyses = (mc.analyses || []).map(a => a.stop ? `${a.type} (stop=${a.stop}${a.errpreset ? ', ' + a.errpreset : ''})` : a.type);
    const vars = Object.entries(mc.variables || {});
    const enCorners = (mc.corners || []).filter(c => c.enabled);
    const models = mc.model_files || [];
    return `
      <details class="sim-support-bar" style="margin-top:8px">
        <summary>⚙️ ${simSourceState.maestroApplied ? '已导入' : '已检测到'} Maestro 仿真配置（${escape(mc.view_name || 'maestro')}）· ${analyses.length} 项分析 · ${vars.length} 个变量 · ${models.length} 个模型库${enCorners.length ? ` · ${enCorners.length} 个启用工艺角` : ''}</summary>
        <div class="sim-maestro-grid">
          ${analyses.length ? `<div><strong>分析：</strong><code>${escape(analyses.join(' ; '))}</code></div>` : ''}
          ${vars.length ? `<div><strong>变量：</strong><code>${escape(vars.map(([k, v]) => `${k}=${v}`).join('  '))}</code></div>` : ''}
          ${enCorners.length ? `<div><strong>启用工艺角：</strong>${enCorners.map(c => `<span class="chip">${escape(c.name)}${c.temperature ? ` (${escape(c.temperature)}°C)` : ''}</span>`).join(' ')}</div>` : ''}
          ${models.length ? `<div><strong>模型库：</strong>${models.slice(0, 6).map(m => `<code>${escape((m.path || '').split('/').pop())}${m.section ? ':' + escape(m.section) : ''}</code>`).join(' ')}</div>` : ''}
        </div>
      </details>`;
  })() : '';

  const supportFilesHtml = (supportEntries.length ? `
    <details class="sim-support-bar">
      <summary>📎 已附带 ${supportEntries.length} 个相对 include 依赖文件（启动仿真时将自动与 input.scs 一并上传）</summary>
      <div class="sim-support-list">
        ${supportEntries.map(([rel, txt]) => `<span><code>${escape(rel)}</code> <small>${escape(formatImportByteSize(new TextEncoder().encode(txt).byteLength))}</small></span>`).join('')}
      </div>
      ${!circuitGitManaged ? `<div style="margin-top:8px"><button type="button" class="button secondary small" data-action="direct-clear-support">清除已附带依赖</button></div>` : ''}
    </details>` : '') + maestroSummaryHtml;

  const configCard = `
    <section class="sim-config-card">
      <div class="sim-card-head">
        <h2>▷ 仿真配置与一键启动</h2>
        <span class="subtle-note">${hostsList.length ? `已连接 ${hostsList.length} 台服务器` : '尚未连接服务器'}</span>
      </div>
      ${sourceBarHtml}
      ${bestBanner}
      <form id="direct-prepare-form">
        <div class="direct-grid">
          <label class="direct-wide">目标服务器
            <select id="direct-host-id" ${!hostsList.length ? 'disabled' : ''}>
              ${hostsList.length ? hostsList.map(h => `<option value="${escape(h.host_id)}" ${h.host_id === directDraft.host_id ? 'selected' : ''}>${escape(hostOptionLabel(h))}</option>`).join('') : '<option value="">请先在首页连接服务器</option>'}
            </select>
          </label>
          <label>仿真器
            <select id="direct-simulator">
              <option value="spectre" ${directDraft.simulator === 'spectre' ? 'selected' : ''}>Cadence Spectre</option>
              <option value="ngspice" ${directDraft.simulator === 'ngspice' ? 'selected' : ''}>ngspice</option>
            </select>
          </label>
          <label>任务名称
            <input id="direct-run-key" value="${escape(directDraft.run_key)}" required maxlength="128" pattern="[A-Za-z0-9][A-Za-z0-9_\\-]*" placeholder="sim-first-01">
          </label>
        </div>
        <div class="direct-file-row" style="margin-top:10px">
          <label for="direct-netlist">网表内容（${escape(simSourceState.fileName || 'input.scs')} · 可从 Library Manager 选择、粘贴或由左侧 Agent 填入）</label>
        </div>
        <textarea id="direct-netlist" class="direct-netlist" required placeholder="${circuitGitManaged ? '当前网表已由 CircuitGit 提供…' : '点上方「打开 Library Manager」浏览远端 Virtuoso schematic / 电路库或导入本地网表，也可直接粘贴…'}">${escape(directDraft.netlist_text)}</textarea>
        ${supportFilesHtml}
        <details class="host-form-details" style="margin-top:10px">
          <summary>更多选项（执行后端 SSH/LSF/Slurm · CPU/内存/波形格式 · 远端环境与夜间恢复）</summary>
          <div class="direct-grid" style="margin-top:8px">
            <label>执行后端<select id="direct-backend"><option value="ssh_direct" ${directDraft.backend === 'ssh_direct' ? 'selected' : ''}>SSH 直连工作站</option><option value="lsf" ${directDraft.backend === 'lsf' ? 'selected' : ''}>LSF 集群 (bsub)</option><option value="slurm" ${directDraft.backend === 'slurm' ? 'selected' : ''}>Slurm 集群 (sbatch)</option></select></label>
            <label>选机方式<select id="direct-target-mode"><option value="host" ${directDraft.target_mode === 'host' ? 'selected' : ''}>指定单台主机</option><option value="pool" ${directDraft.target_mode === 'pool' ? 'selected' : ''}>按资源自动推荐</option></select></label>
            <label>CPU 核数<input id="direct-cpus" type="number" min="1" max="4096" value="${escape(directDraft.cpus)}" required></label>
            <label>内存 (GiB)<input id="direct-memory" type="number" min="0.1" step="any" max="16384" value="${escape(directDraft.memory_gib)}" required></label>
            <label>最大时长 (秒)<input id="direct-wall" type="number" min="10" max="604800" value="${escape(directDraft.wall_seconds)}" required></label>
            <label>波形格式<select id="direct-format"><option value="psfbin" ${directDraft.output_format === 'psfbin' ? 'selected' : ''}>PSF Binary</option><option value="psfascii" ${directDraft.output_format === 'psfascii' ? 'selected' : ''}>PSF ASCII</option><option value="psfxl" ${directDraft.output_format === 'psfxl' ? 'selected' : ''}>PSFXL</option></select></label>
            <label>远端 Python3 路径<input id="direct-python" value="${escape(directDraft.remote_python)}"></label>
            <label>远端工作根目录<input id="direct-root" value="${escape(directDraft.remote_root)}" placeholder=".spiceowl-runs"></label>
            <label>远端 Modules（选填）<input id="direct-env-modules" value="${escape(directDraft.env_modules)}" placeholder="cadence/spectre-23.1"></label>
            <label>远端 Setup 脚本（选填）<input id="direct-env-scripts" value="${escape(directDraft.env_scripts)}" placeholder="/opt/cadence/env.sh"></label>
            <label class="direct-wide">Include 目录（每行一个绝对路径，选填）<textarea id="direct-includes" rows="2" placeholder="/opt/pdk/models">${escape(directDraft.include_dirs)}</textarea></label>
          </div>
          ${schedBox}
          ${recBox}
        </details>
        <div class="direct-actions" style="margin-top:12px">
          <button type="button" class="button primary" data-action="direct-quick-launch" ${directState.busy || !hostsList.length ? 'disabled' : ''}>${directState.busy ? '正在准备并启动…' : '🚀 一键准备并启动仿真'}</button>
          <button type="submit" class="button secondary" ${directState.busy || !hostsList.length ? 'disabled' : ''}>仅冻结计划 (Prepare)</button>
        </div>
      </form>
      ${launchBanner}
    </section>`;

  return `<div class="sim-setup-grid">${agentCard}${configCard}</div>`;
}

// Unified Library Manager dialog (reuses CircuitGit's SSH browse/import & local folder importer)
function openLibraryManager(initialTab = null) {
  if (isCircuitGitManagedEntry()) return;
  const dialog = $('#library-manager-dialog');
  if (!dialog) return;
  const hostsList = state.hosts || [];
  if (initialTab === 'ssh' || initialTab === 'local') {
    libMgrState.tab = initialTab;
  } else if (!hostsList.length) {
    libMgrState.tab = 'local';
  }
  if (!libMgrState.hostId && hostsList.length) {
    libMgrState.hostId = directDraft.host_id || pickMostIdleHostId() || hostsList[0].host_id;
  }
  const activeHost = hostsList.find(h => h.host_id === libMgrState.hostId);
  if (activeHost) libMgrState.sshTarget = activeHost.ssh_target || activeHost.host_id;
  if (!dialog.open) dialog.showModal();
  renderLibraryManagerDialog();
  if (libMgrState.tab === 'ssh' && !isPublicDemo() && libMgrState.hostId && !libMgrState.cwd && !libMgrState.busy) {
    void browseLibrarySsh(libMgrState.path || '~');
  }
}

async function browseLibrarySsh(targetPath) {
  if (isPublicDemo() || libMgrState.busy) return;
  const hostId = libMgrState.hostId;
  const sshTarget = (libMgrState.sshTarget || '').trim();
  if (!hostId && !sshTarget) {
    libMgrState.error = '请先选择一台已连接的 SSH 服务器。';
    renderLibraryManagerDialog();
    return;
  }
  libMgrState.busy = true;
  libMgrState.busyMessage = '正在读取远端目录与 Virtuoso 视图…';
  libMgrState.error = '';
  libMgrState.selectedPath = '';
  renderLibraryManagerDialog();
  try {
    const nextPath = String(targetPath ?? libMgrState.path ?? '~').trim() || '~';
    const body = hostId ? {host_id: hostId, path: nextPath} : {ssh_target: sshTarget, path: nextPath};
    const res = await api('/api/library/ssh-browse', {method: 'POST', body});
    libMgrState.cwd = res.cwd || '';
    libMgrState.path = libMgrState.cwd || nextPath;
    libMgrState.parent = res.parent || null;
    libMgrState.entries = Array.isArray(res.entries) ? res.entries : [];
    libMgrState.cdsLibraries = Array.isArray(res.cds_libraries) ? res.cds_libraries : [];
    libMgrState.virtuosoContext = (res.virtuoso_context && typeof res.virtuoso_context === 'object') ? res.virtuoso_context : null;
    const mviews = libMgrState.virtuosoContext?.maestro_views || [];
    if (mviews.length) {
      if (!mviews.some(m => m.name === libMgrState.selectedMaestroView)) {
        libMgrState.selectedMaestroView = mviews[0].name;
      }
    } else {
      libMgrState.selectedMaestroView = '';
    }
    libMgrState.truncated = Boolean(res.truncated);
  } catch (error) {
    libMgrState.error = error.message || '远端目录读取失败。';
  } finally {
    libMgrState.busy = false;
    libMgrState.busyMessage = '';
    renderLibraryManagerDialog();
  }
}

async function exportLibrarySchematic(targetPath, {includeMaestro = null, maestroView = null, preserveRnl = null} = {}) {
  if (isPublicDemo() || libMgrState.busy) return;
  const target = String(targetPath || libMgrState.virtuosoContext?.schematic_dir || libMgrState.cwd || '').trim();
  if (!target) {
    libMgrState.error = '请先进入或选中一个 Virtuoso schematic 视图目录。';
    renderLibraryManagerDialog();
    return;
  }
  const useMaestro = includeMaestro !== null ? Boolean(includeMaestro) : Boolean(libMgrState.includeMaestro);
  const useRnl = preserveRnl !== null ? Boolean(preserveRnl) : Boolean(libMgrState.preserveRnl);
  const chosenView = maestroView !== null ? maestroView : (libMgrState.selectedMaestroView || '');
  libMgrState.busy = true;
  libMgrState.busyMessage = useRnl
    ? (useMaestro
      ? '正在远端调用 Cadence si + SKILL 导出网表、保留 RNL 原理图几何并解析 Maestro 配置（约 6–15 秒）…'
      : '正在远端调用 Cadence si + SKILL 导出网表并保留原图元件位置与连线（RNL，约 6–15 秒）…')
    : (useMaestro
      ? '正在远端调用 Cadence si 从 schematic 导出网表并解析 Maestro 配置（约 5–15 秒）…'
      : '正在远端调用 Cadence si 从 schematic 导出纯原理图 Spectre 网表（约 5–15 秒）…');
  libMgrState.error = '';
  renderLibraryManagerDialog();
  try {
    const body = {
      ...(libMgrState.hostId ? {host_id: libMgrState.hostId} : {ssh_target: libMgrState.sshTarget}),
      path: target,
      include_maestro: useMaestro,
      preserve_rnl: useRnl,
      ...(chosenView ? {maestro_view: chosenView} : {}),
      ...(libMgrState.virtuosoContext?.cds_lib ? {cds_lib: libMgrState.virtuosoContext.cds_lib} : {}),
    };
    const res = await api('/api/library/ssh-export-schematic', {method: 'POST', body});
    const fileName = res.file_name || `${res.cell_name || 'schematic'}.scs`;
    simSourceState.origin = 'ssh';
    simSourceState.fromSchematic = true;
    simSourceState.projectName = res.lib_name || res.cell_name || 'Virtuoso';
    simSourceState.libraryId = res.lib_name || '';
    simSourceState.libraryName = res.lib_name || '';
    simSourceState.cellId = res.cell_name || '';
    simSourceState.cellName = res.cell_name || '';
    simSourceState.viewId = res.view_name || 'schematic';
    simSourceState.fileName = fileName;
    simSourceState.entryPath = fileName;
    simSourceState.sshTarget = res.ssh_target || libMgrState.sshTarget;
    simSourceState.sshHostId = libMgrState.hostId || '';
    simSourceState.remotePath = res.remote_path || target;
    simSourceState.remoteDir = res.remote_dir || '';
    simSourceState.supportFiles = (res.support_files && typeof res.support_files === 'object') ? {...res.support_files} : {};
    simSourceState.skipped = [];
    simSourceState.sourceCommit = '';
    simSourceState.maestroApplied = Boolean(res.maestro_applied);
    simSourceState.maestroConfig = res.maestro_config || null;
    simSourceState.rnlApplied = Boolean(res.rnl_applied);
    simSourceState.rnlStats = res.rnl_stats || null;

    directDraft.netlist_text = res.text || '';
    directDraft.simulator = res.maestro_config?.simulator === 'ngspice' ? 'ngspice' : 'spectre';
    directDraft.output_format = directDraft.simulator === 'spectre' ? 'psfbin' : 'psfascii';
    if (libMgrState.hostId && (state.hosts || []).some(h => h.host_id === libMgrState.hostId)) {
      directDraft.target_mode = 'host';
      directDraft.host_id = libMgrState.hostId;
    }
    // Pre-seed PVT batch corners & temps from imported Maestro setup if present
    if (res.maestro_applied && res.maestro_config?.corners?.length) {
      const enabledCorners = res.maestro_config.corners.filter(c => c.enabled);
      if (enabledCorners.length) {
        batchDraft.corners = enabledCorners.map(c => c.name).join(', ');
        const temps = enabledCorners.map(c => c.temperature).find(Boolean);
        if (temps) batchDraft.temps = String(temps).trim().split(/\s+/).join(', ');
      }
    }
    const stamp = new Date().toISOString().replace(/[-:T.Z]/g, '').slice(8, 14);
    directDraft.run_key = `${makeSafeRunKeyPrefix(res.cell_name || fileName)}-${stamp}`;
    directState.quickLaunchNote = null;
    directState.quickLaunchError = null;
    $('#library-manager-dialog')?.close();
    render();
    toast(
      `已从远端 Virtuoso schematic（${res.lib_name}/${res.cell_name}）导出网表` +
      (res.rnl_applied && res.rnl_stats ? `（已转为 RNL · 保留 ${res.rnl_stats.placed || 0} 器件与 ${res.rnl_stats.wires || 0} 连线）` : '') +
      (res.maestro_applied ? `并导入 ${res.maestro_config?.view_name || 'maestro'} 仿真配置！` : '！')
    );
  } catch (error) {
    libMgrState.error = error.message || '远端 Virtuoso schematic 网表导出失败。';
  } finally {
    libMgrState.busy = false;
    libMgrState.busyMessage = '';
    renderLibraryManagerDialog();
  }
}

async function importLibrarySsh(filePath) {
  if (isPublicDemo() || libMgrState.busy) return;
  const target = String(filePath || libMgrState.selectedPath || '').trim();
  if (!target) {
    libMgrState.error = '请先选中一个远端网表文件。';
    renderLibraryManagerDialog();
    return;
  }
  libMgrState.busy = true;
  libMgrState.error = '';
  renderLibraryManagerDialog();
  try {
    const body = {
      ...(libMgrState.hostId ? {host_id: libMgrState.hostId} : {ssh_target: libMgrState.sshTarget}),
      path: target,
      include_dependencies: Boolean(libMgrState.includeDeps),
    };
    const res = await api('/api/library/ssh-import', {method: 'POST', body});
    const dirName = (res.remote_dir || '').split('/').filter(Boolean).pop() || '';
    const fileName = res.file_name || target.split('/').pop() || 'input.scs';
    const det = detectSimulatorFromNetlist(res.text, fileName);
    simSourceState.origin = 'ssh';
    simSourceState.fromSchematic = false;
    simSourceState.maestroApplied = false;
    simSourceState.maestroConfig = null;
    simSourceState.rnlApplied = false;
    simSourceState.rnlStats = null;
    simSourceState.projectName = dirName || fileName.replace(/\.[^.]+$/, '') || fileName;
    simSourceState.fileName = fileName;
    simSourceState.entryPath = fileName;
    simSourceState.sshTarget = res.ssh_target || libMgrState.sshTarget;
    simSourceState.sshHostId = libMgrState.hostId || '';
    simSourceState.remotePath = res.remote_path || target;
    simSourceState.remoteDir = res.remote_dir || '';
    simSourceState.supportFiles = (res.support_files && typeof res.support_files === 'object') ? {...res.support_files} : {};
    simSourceState.skipped = Array.isArray(res.skipped) ? res.skipped : [];
    simSourceState.sourceCommit = '';
    directDraft.netlist_text = res.text || '';
    directDraft.simulator = det.simulator;
    directDraft.output_format = det.output_format;
    if (libMgrState.hostId && (state.hosts || []).some(h => h.host_id === libMgrState.hostId)) {
      directDraft.target_mode = 'host';
      directDraft.host_id = libMgrState.hostId;
    }
    const stamp = new Date().toISOString().replace(/[-:T.Z]/g, '').slice(8, 14);
    directDraft.run_key = `${makeSafeRunKeyPrefix(fileName)}-${stamp}`;
    directState.quickLaunchNote = null;
    directState.quickLaunchError = null;
    $('#library-manager-dialog')?.close();
    render();
    const depCount = Object.keys(simSourceState.supportFiles).length;
    toast(`已从 ${simSourceState.sshHostId || simSourceState.sshTarget} 导入 ${fileName}${depCount ? `（含 ${depCount} 个相对 include 依赖）` : ''}。`);
  } catch (error) {
    libMgrState.error = error.message || '远端网表导入失败。';
  } finally {
    libMgrState.busy = false;
    renderLibraryManagerDialog();
  }
}

async function handleLibraryLocalFiles(fileList) {
  if (libMgrState.localBusy) return;
  libMgrState.localBusy = true;
  libMgrState.localError = '';
  renderLibraryManagerDialog();
  try {
    const entries = await readBrowserFileList(fileList);
    const bundle = prepareLocalImportFiles(entries);
    libMgrState.localBundle = bundle;
    libMgrState.localSelectedEntry = bundle.defaultEntry;
    if (bundle.candidates.length === 1) {
      commitLibraryLocalBundle();
      return;
    }
  } catch (error) {
    libMgrState.localError = error.message || '读取本地网表失败。';
  } finally {
    libMgrState.localBusy = false;
    renderLibraryManagerDialog();
  }
}

function commitLibraryLocalBundle() {
  if (!libMgrState.localBundle) return;
  try {
    const chosen = selectImportEntry({
      filesByPath: libMgrState.localBundle.filesByPath,
      entryPath: libMgrState.localSelectedEntry || libMgrState.localBundle.defaultEntry,
      projectName: libMgrState.localBundle.projectName,
    });
    const det = detectSimulatorFromNetlist(chosen.text, chosen.fileName);
    simSourceState.origin = 'local';
    simSourceState.projectName = chosen.projectName;
    simSourceState.fileName = chosen.fileName;
    simSourceState.entryPath = chosen.entryPath;
    simSourceState.sshTarget = '';
    simSourceState.sshHostId = '';
    simSourceState.remotePath = '';
    simSourceState.remoteDir = '';
    simSourceState.supportFiles = chosen.supportFiles || {};
    simSourceState.skipped = libMgrState.localBundle.skipped || [];
    simSourceState.sourceCommit = '';
    directDraft.netlist_text = chosen.text;
    directDraft.simulator = det.simulator;
    directDraft.output_format = det.output_format;
    const stamp = new Date().toISOString().replace(/[-:T.Z]/g, '').slice(8, 14);
    directDraft.run_key = `${makeSafeRunKeyPrefix(chosen.fileName)}-${stamp}`;
    directState.quickLaunchNote = null;
    directState.quickLaunchError = null;
    libMgrState.localBundle = null;
    libMgrState.localSelectedEntry = '';
    $('#library-manager-dialog')?.close();
    render();
    const depCount = Object.keys(simSourceState.supportFiles).length;
    toast(`已导入本地项目「${chosen.projectName}」· ${chosen.fileName}${depCount ? `（含 ${depCount} 个相对 include 依赖）` : ''}。`);
  } catch (error) {
    libMgrState.localError = error.message || '导入本地网表失败。';
    renderLibraryManagerDialog();
  }
}

function renderLibraryManagerDialog() {
  const container = $('#library-manager-content');
  if (!container) return;
  const hostsList = state.hosts || [];
  const tab = libMgrState.tab;
  const tabsHtml = `
    <div class="lib-mgr-tabs" role="tablist">
      <button type="button" role="tab" aria-selected="${tab === 'ssh' ? 'true' : 'false'}" class="lib-mgr-tab ${tab === 'ssh' ? 'active' : ''}" data-action="lib-mgr-tab" data-tab="ssh">🖥️ 远端服务器项目与电路库 (SSH)</button>
      <button type="button" role="tab" aria-selected="${tab === 'local' ? 'true' : 'false'}" class="lib-mgr-tab ${tab === 'local' ? 'active' : ''}" data-action="lib-mgr-tab" data-tab="local">💻 本地项目文件夹 / 网表导入</button>
    </div>`;

  let bodyHtml = '';
  if (tab === 'ssh') {
    if (!hostsList.length) {
      bodyHtml = `
        <div class="lib-mgr-empty-box">
          <strong>还没有已连接的 SSH 服务器</strong>
          <p class="subtle-note">先在首页完成 SSH 连接，即可像 Library Manager 一样直接浏览远端工作目录、Cadence <code>cds.lib</code> 与网表文件；也可以现在切换到「本地项目文件夹 / 网表导入」。</p>
          <div class="direct-actions">
            <button type="button" class="button primary small" data-action="lib-mgr-tab" data-tab="local">切换到本地导入 →</button>
            <button type="button" class="button secondary small" data-action="open-ssh-setup-wizard">＋ 打开 SSH 配置向导 ↗</button>
          </div>
        </div>`;
    } else {
      const q = (libMgrState.filter || '').trim().toLowerCase();
      const filtered = q
        ? libMgrState.entries.filter(e => String(e.name || '').toLowerCase().includes(q))
        : libMgrState.entries;
      const cdsHtml = libMgrState.cdsLibraries.length ? `
        <div class="lib-mgr-cds-bar">
          <span>📚 检测到 <code>cds.lib</code> 电路库（点击直达）：</span>
          <div class="lib-mgr-cds-pills">
            ${libMgrState.cdsLibraries.map(lib => `<button type="button" class="lib-mgr-cds-pill" data-action="lib-mgr-cd" data-path="${escape(lib.path)}" title="${escape(lib.path)}">${escape(lib.name)}</button>`).join('')}
          </div>
        </div>` : '';
      const vctx = libMgrState.virtuosoContext;
      let virtuosoCardHtml = '';
      if (vctx) {
        const mviews = vctx.maestro_views || [];
        const activeMv = mviews.find(m => m.name === libMgrState.selectedMaestroView) || mviews[0] || null;
        const ms = activeMv?.summary || null;
        const anLabels = (ms?.analyses || []).map(a => a.stop ? `${a.type}(${a.stop})` : a.type);
        const enCorners = (ms?.corners || []).filter(c => c.enabled).map(c => c.name);
        const maestroBlockHtml = activeMv ? `
          <div class="lib-mgr-maestro-box">
            <div class="lib-mgr-maestro-head">
              <label class="lib-mgr-check">
                <input id="lib-mgr-include-maestro" type="checkbox" ${libMgrState.includeMaestro ? 'checked' : ''} ${libMgrState.busy ? 'disabled' : ''}>
                <span>同时导入 <code>${escape(activeMv.name)}</code> 自带的仿真配置（分析 / 模型库 / 变量 / 选项 / 工艺角）</span>
              </label>
              ${mviews.length > 1 ? `
                <select id="lib-mgr-maestro-view" aria-label="选择 Maestro/ADE 视图" ${libMgrState.busy ? 'disabled' : ''}>
                  ${mviews.map(m => `<option value="${escape(m.name)}" ${m.name === activeMv.name ? 'selected' : ''}>${escape(m.name)} (${escape(m.kind)})</option>`).join('')}
                </select>` : ''}
            </div>
            <div class="lib-mgr-maestro-pills">
              ${anLabels.length ? `<span class="chip blue">分析: ${escape(anLabels.join(', '))}</span>` : ''}
              ${ms?.model_files_count ? `<span class="chip">模型库 ×${ms.model_files_count}</span>` : ''}
              ${ms?.variables_count ? `<span class="chip">变量 ×${ms.variables_count}</span>` : ''}
              ${enCorners.length ? `<span class="chip green">启用工艺角: ${escape(enCorners.join('/'))}</span>` : ''}
              ${ms?.outputs_count ? `<span class="chip">输出 ×${ms.outputs_count}</span>` : ''}
            </div>
          </div>` : `<p class="subtle-note" style="margin:6px 0 0">未在同单元目录下检测到 <code>maestro</code> / <code>adexl</code> / <code>spectre_state</code> 视图，将导出纯原理图 Spectre 网表。</p>`;

        const rnlToggleHtml = `
          <div class="lib-mgr-maestro-box">
            <label class="lib-mgr-check">
              <input id="lib-mgr-preserve-rnl" type="checkbox" ${libMgrState.preserveRnl ? 'checked' : ''} ${libMgrState.busy ? 'disabled' : ''}>
              <span>自动转为 <strong>RNL</strong> 保留原图元件坐标、方向、符号、连线与层次关系（通用 SKILL 只读导出，兼容各版本 Virtuoso 与 CircuitGit）</span>
            </label>
          </div>`;

        virtuosoCardHtml = `
          <div class="lib-mgr-virtuoso-card" data-virtuoso-schematic-card="true">
            <div class="lib-mgr-virtuoso-head">
              <div>
                <strong>📐 检测到 Virtuoso 原理图：${escape(vctx.lib_name)} / ${escape(vctx.cell_name)} <span class="chip green">${escape(vctx.view_name)}</span></strong>
                <small class="subtle-note" style="display:block;margin-top:2px">直接调用远端 Cadence <code>si</code> + 只读 SKILL 在隔离临时目录导出完整层次 Spectre/RNL 网表，不改动原设计。</small>
              </div>
              <button type="button" class="button primary small" data-action="lib-mgr-export-schematic" data-path="${escape(vctx.schematic_dir)}" ${libMgrState.busy ? 'disabled' : ''}>${libMgrState.busy ? '导出中…' : '⚡ 从 Schematic 导出并导入'}</button>
            </div>
            ${rnlToggleHtml}
            ${maestroBlockHtml}
          </div>`;
      }

      const listHtml = libMgrState.busy && !filtered.length
        ? `<p class="lib-mgr-empty">${escape(libMgrState.busyMessage || '正在通过 SSH 读取远端目录与电路库…')}</p>`
        : !libMgrState.cwd && !filtered.length
        ? '<p class="lib-mgr-empty">选择上方服务器并点击「浏览目录」，即可浏览项目目录、<code>cds.lib</code>、Virtuoso <code>schematic</code> 与网表文件（.scs / .sp / .cir / .va / .rnl）。</p>'
        : !filtered.length
        ? `<p class="lib-mgr-empty">${q ? `没有匹配「${escape(libMgrState.filter)}」的目录或网表。` : (vctx ? '当前为 Virtuoso 原理图视图目录（包含 OpenAccess sch.oa），直接点击上方「⚡ 从 Schematic 导出并导入」即可生成并载入 Spectre 网表。' : `目录 ${escape(libMgrState.cwd)} 下暂无子目录或支持的网表文件。`)}</p>`
        : filtered.map(entry => {
            const isDir = entry.kind === 'dir';
            const sel = !isDir && libMgrState.selectedPath === entry.path;
            const isSchCell = entry.virtuoso_kind === 'cell' || entry.virtuoso_kind === 'schematic_view';
            const isMaestroDir = entry.virtuoso_kind === 'maestro_view';
            const icon = isSchCell ? '📐' : isMaestroDir ? '⚙️' : (isDir ? '📁' : '📄');
            const extraBadges = [
              entry.virtuoso_kind === 'cell' ? '<span class="lib-mgr-tag sch">schematic</span>' : '',
              entry.virtuoso_kind === 'schematic_view' ? '<span class="lib-mgr-tag sch">OA schematic</span>' : '',
              ...(entry.maestro_views || []).map(mv => `<span class="lib-mgr-tag maestro">+ ${escape(mv)}</span>`),
              isMaestroDir ? '<span class="lib-mgr-tag maestro">仿真配置</span>' : '',
            ].filter(Boolean).join('');
            return `
              <div class="lib-mgr-row ${isDir ? 'is-dir' : 'is-file'} ${sel ? 'is-selected' : ''}">
                <button type="button" class="lib-mgr-row-main" data-action="${isDir ? 'lib-mgr-cd' : 'lib-mgr-select'}" data-path="${escape(entry.path)}">
                  <span class="lib-mgr-icon">${icon}</span>
                  <span class="lib-mgr-name">${escape(isDir ? entry.name + '/' : entry.name)}</span>
                  ${extraBadges}
                  <span class="lib-mgr-meta">${isDir ? (isSchCell ? '原理图' : isMaestroDir ? 'ADE/Maestro' : '目录') : escape(formatImportByteSize(entry.size))}</span>
                </button>
                ${isSchCell ? `<button type="button" class="button primary small" data-action="lib-mgr-export-schematic" data-path="${escape(entry.schematic_path || entry.path)}" ${libMgrState.busy ? 'disabled' : ''}>导出网表</button>` : ''}
                ${!isDir ? `<button type="button" class="button secondary small" data-action="lib-mgr-import-one" data-path="${escape(entry.path)}" ${libMgrState.busy ? 'disabled' : ''}>导入</button>` : ''}
              </div>`;
          }).join('');
      bodyHtml = `
        <p class="subtle-note" style="margin:0 0 10px">安全只读浏览已连接服务器上的电路项目、<code>cds.lib</code> 与 Virtuoso <code>schematic</code>（与 CircuitGit 复用同一套接口，绝不读取密钥或修改原设计）。</p>
        <div class="lib-mgr-toolbar">
          <select id="lib-mgr-host" aria-label="选择 SSH 服务器">
            ${hostsList.map(h => `<option value="${escape(h.host_id)}" ${h.host_id === libMgrState.hostId ? 'selected' : ''}>${escape(h.host_id)} (${escape(h.label)})</option>`).join('')}
          </select>
          <button type="button" class="button primary small" data-action="lib-mgr-browse" ${libMgrState.busy ? 'disabled' : ''}>${libMgrState.busy ? '处理中…' : '浏览目录'}</button>
          <button type="button" class="button secondary small" data-action="lib-mgr-cd" data-path="~" ${libMgrState.busy ? 'disabled' : ''}>~ 主目录</button>
        </div>
        <div class="lib-mgr-pathbar">
          <button type="button" class="button secondary small" data-action="lib-mgr-cd" data-path="${escape(libMgrState.parent || '')}" ${libMgrState.busy || !libMgrState.parent ? 'disabled' : ''}>↑ 上级</button>
          <input id="lib-mgr-path" type="text" value="${escape(libMgrState.path)}" placeholder="~ 或 ~/sim_project" aria-label="远端目录路径">
          <button type="button" class="button secondary small" data-action="lib-mgr-browse" ${libMgrState.busy ? 'disabled' : ''}>前往</button>
          <input id="lib-mgr-filter" type="search" value="${escape(libMgrState.filter)}" placeholder="过滤名称…" aria-label="过滤当前目录">
        </div>
        ${libMgrState.busy && libMgrState.busyMessage ? `<p class="subtle-note" role="status" style="margin:0 0 8px;color:var(--green);font-weight:600">⏳ ${escape(libMgrState.busyMessage)}</p>` : ''}
        ${libMgrState.error ? `<p class="error-text" role="alert">${escape(libMgrState.error)}</p>` : ''}
        ${virtuosoCardHtml}
        ${cdsHtml}
        <div class="lib-mgr-list" role="listbox" aria-label="远端目录与网表文件">${listHtml}</div>
        ${libMgrState.truncated ? '<p class="subtle-note" style="margin:6px 0 0">当前目录超过 200 项，仅展示前 200 项；可直接输入更具体路径。</p>' : ''}
        <div class="lib-mgr-footer">
          <label class="lib-mgr-check"><input id="lib-mgr-deps" type="checkbox" ${libMgrState.includeDeps ? 'checked' : ''}><span>同时拉取同目录相对 include 依赖（自动跳过外部 PDK 库）</span></label>
          <button type="button" class="button primary small" data-action="lib-mgr-import-selected" ${libMgrState.busy || !libMgrState.selectedPath ? 'disabled' : ''}>${libMgrState.busy ? '处理中…' : '导入选中网表'}</button>
        </div>`;
    }
  } else {
    const bundle = libMgrState.localBundle;
    bodyHtml = `
      <p class="subtle-note" style="margin:0 0 12px">导入电脑上的 Spectre / SPICE / Verilog-A / RNL 网表或整个项目文件夹（与 CircuitGit 复用同一套顶层入口评分与相对 include 依赖打包逻辑）。</p>
      <div id="lib-mgr-dropzone" class="lib-mgr-dropzone">
        <div class="direct-actions" style="justify-content:center">
          <label class="button primary small" style="cursor:pointer">📄 选择本地网表文件 (.scs/.sp/.cir)<input id="lib-mgr-local-file" type="file" multiple accept="${escape(CIRCUIT_EXTENSIONS.join(','))}" hidden></label>
          <label class="button secondary small" style="cursor:pointer">📁 选择本地项目文件夹（含 include 依赖）<input id="lib-mgr-local-folder" type="file" multiple webkitdirectory hidden></label>
        </div>
        <small class="subtle-note">也可直接把 .scs / .sp / .cir 文件拖拽到这里（单文件 ≤ 1 MiB，自动过滤敏感文件）。</small>
      </div>
      ${bundle ? `
        <div class="lib-mgr-bundle">
          <strong>已读取项目「${escape(bundle.projectName)}」· 共 ${bundle.candidates.length} 个网表文件</strong>
          <label style="display:block;margin-top:8px">
            <span class="subtle-note" style="display:block;margin-bottom:4px">选择顶层入口网表（其余文件将作为 include 依赖自动打包上传）：</span>
            <select id="lib-mgr-local-entry">
              ${bundle.candidates.map(c => `<option value="${escape(c.path)}" ${c.path === (libMgrState.localSelectedEntry || bundle.defaultEntry) ? 'selected' : ''}>${escape(c.path)} (${escape(formatImportByteSize(c.size))})</option>`).join('')}
            </select>
          </label>
          <div class="direct-actions" style="margin-top:10px">
            <button type="button" class="button primary small" data-action="lib-mgr-local-commit">确认导入到仿真配置</button>
            <button type="button" class="button secondary small" data-action="lib-mgr-local-clear">重新选择</button>
          </div>
        </div>` : ''}
      ${libMgrState.localError ? `<p class="error-text" role="alert">${escape(libMgrState.localError)}</p>` : ''}`;
  }

  container.innerHTML = tabsHtml + `<div class="lib-mgr-body">${bodyHtml}</div>`;
}

function directPage() {
  const pub = isPublicDemo();
  const validTabs = pub ? ['waveforms', 'batches', 'runs', 'recovery'] : ['setup', 'recovery'];
  const defaultTab = pub ? 'waveforms' : 'setup';
  const tab = validTabs.includes(directState.activeTab) ? directState.activeTab : defaultTab;
  let html = heading(
    pub ? 'SIMULATION WORKBENCH · 仿真配置与波形工作台' : 'SIMULATION WORKBENCH · 仿真配置与执行工作台',
    pub ? '一站式查看波形、管理 PVT/MC 批量与控制仿真任务' : 'Agent 辅助仿真配置与执行工作台',
    pub
      ? '当前为浏览器内置交互沙箱：大波形缩放、500 周期毛刺钻取、27-Corner PVT、40 样本 MC、单次任务四步流与 OOM 恢复报告全部可直接点击体验。'
      : '让 Agent 结合实时服务器资源与 SPICE License 帮你配置并启动仿真，并管理夜间值守与自动恢复；所有历史任务与控制统一归档在「仿真总览」。',
  );

  const tabs = pub ? [
    ['waveforms', '📈 1. 大波形与周期切片', directState.wfIndex?.datasets?.length ? `${directState.wfIndex.datasets.length}` : ''],
    ['batches', '▦ 2. PVT / MC 批量实验', String(directState.batches.length || 0)],
    ['runs', '▷ 3. 单次仿真与控制', String(directState.runs.length || 0)],
    ['recovery', '🛡 4. 异常自动恢复报告', String(directState.recoveryReports.length || 0)],
  ] : [
    ['setup', '✧ 1. Agent 辅助配置', ''],
    ['recovery', '🛡 2. 夜间值守与恢复', String(directState.recoveryReports.length || 0)],
  ];
  html += `<div class="direct-tabs" role="tablist" aria-label="工作台子模块">${tabs.map(([key, label, badge]) => `<button type="button" role="tab" aria-selected="${tab === key ? 'true' : 'false'}" class="direct-tab ${tab === key ? 'active' : ''}" data-action="direct-tab" data-tab="${key}"><span>${label}</span>${badge !== '' ? `<span class="direct-tab-badge">${escape(badge)}</span>` : ''}</button>`).join('')}</div>`;

  if (directState.error) html += `<p class="error-text" role="alert">${escape(directState.error)}</p>`;

  const hostsList = state.hosts || [];
  if (!directDraft.host_id && hostsList.length) directDraft.host_id = pickMostIdleHostId() || hostsList[0].host_id;
  if (!directDraft.run_key) {
    const stamp = new Date().toISOString().replace(/[-:T.Z]/g, '').slice(0, 14);
    directDraft.run_key = `sim-${stamp}`;
  }
  const run = directState.detail || directState.runs.find(r => r.run_id === directState.selectedId) || null;

  if (tab === 'setup') {
    html += directAgentSetupPanel(hostsList);
    return html;
  }
  if (tab === 'waveforms') {
    html += directWaveformPanel(run);
    return html;
  }
  if (tab === 'batches') {
    html += directBatchPanel(hostsList);
    return html;
  }
  if (tab === 'recovery') {
    html += directRecoveryReportsPanel();
    return html;
  }

  // Tab 3: Single simulation & step-by-step control
  const watchEntry = directState.monitor?.watches?.find(w => w && w.run_id === run?.run_id) || null;
  const watchArmed = watchEntry?.watch_state === 'armed';
  const terminal = ['simulator_complete_pending_measurements', 'simulator_failed', 'job_setup_failed', 'input_hash_failure'].includes(run?.state);
  const canStage = run && ['prepared', 'stage_incomplete'].includes(run.state) && !directState.busy;
  const canLaunch = run && run.state === 'staged' && !run.dispatch_claimed && !directState.busy;
  const canStatus = run && run.state !== 'prepared' && !directState.busy;
  const canWatch = !pub && run && run.dispatch_claimed && !terminal && !watchArmed && !directState.busy;
  const canUnwatch = !pub && run && watchArmed && !directState.busy;
  const canCancel = run && run.dispatch_claimed && !terminal && !directState.busy;
  const canCollect = run && terminal && run.collection_phase !== 'complete' && !directState.busy;
  const canRecover = !pub && run && terminal && run.state !== 'simulator_complete_pending_measurements' && !directState.busy;
  const recPolicy = run?.recovery_policy || {};
  const recBox = `<details class="host-form-details" ${directDraft.rec_enabled ? 'open' : ''}><summary>进阶：M4 有界自动恢复策略（不改网表与科学条件）</summary><div class="direct-check-row"><label><input id="direct-rec-enabled" type="checkbox" ${directDraft.rec_enabled ? 'checked' : ''}> 启用有界自动恢复（Watch 自动收集后按预算恢复）</label></div><div class="direct-grid"><label>最大恢复次数 (0–5)<input id="direct-rec-attempts" type="number" min="0" max="5" value="${escape(directDraft.rec_max_attempts)}" ${directDraft.rec_enabled ? '' : 'disabled'}></label><label>最大内存上限 (GiB)<input id="direct-rec-maxmem" type="number" min="1" max="16384" value="${escape(directDraft.rec_max_memory_gib)}" ${directDraft.rec_enabled ? '' : 'disabled'}></label><label>许可证退避等待 (秒)<input id="direct-rec-backoff" type="number" min="0" max="3600" value="${escape(directDraft.rec_license_backoff)}" ${directDraft.rec_enabled ? '' : 'disabled'}></label><label>总时间预算 (分钟)<input id="direct-rec-elapsed" type="number" min="1" max="10080" value="${escape(directDraft.rec_max_elapsed_min)}" ${directDraft.rec_enabled ? '' : 'disabled'}></label></div><div class="direct-check-row"><label><input id="direct-rec-lic" type="checkbox" ${directDraft.rec_retry_license ? 'checked' : ''} ${directDraft.rec_enabled ? '' : 'disabled'}> 许可证失败退避重试</label><label><input id="direct-rec-oom" type="checkbox" ${directDraft.rec_retry_oom ? 'checked' : ''} ${directDraft.rec_enabled ? '' : 'disabled'}> 确认 OOM 自动内存翻倍</label><label><input id="direct-rec-relocate" type="checkbox" ${directDraft.rec_allow_relocate ? 'checked' : ''} ${directDraft.rec_enabled ? '' : 'disabled'}> 允许迁移至更大内存主机</label></div></details>`;

  const schedBox = directDraft.backend === 'lsf'
    ? `<div class="direct-sched-box"><strong>LSF 集群参数（bsub）</strong><div class="direct-grid"><label>Queue（必填）<input id="direct-queue" value="${escape(directDraft.queue)}" placeholder="analog_long" required></label><label>Project（可选）<input id="direct-project" value="${escape(directDraft.project)}" placeholder="adc_proj"></label><label class="direct-wide">Job group（可选）<input id="direct-job-group" value="${escape(directDraft.job_group)}" placeholder="/analog/sim"></label></div><div class="direct-check-row"><label><input id="direct-exclusive" type="checkbox" ${directDraft.exclusive ? 'checked' : ''}> 独占计算节点 (-x)</label></div></div>`
    : directDraft.backend === 'slurm'
    ? `<div class="direct-sched-box"><strong>Slurm 集群参数（sbatch）</strong><div class="direct-grid"><label>Partition（必填）<input id="direct-partition" value="${escape(directDraft.partition)}" placeholder="sim_compute" required></label><label>Account（可选）<input id="direct-account" value="${escape(directDraft.account)}" placeholder="analog_lab"></label><label class="direct-wide">QoS（可选）<input id="direct-qos" value="${escape(directDraft.qos)}" placeholder="normal"></label></div><div class="direct-check-row"><label><input id="direct-exclusive" type="checkbox" ${directDraft.exclusive ? 'checked' : ''}> 独占计算节点 (--exclusive)</label></div></div>`
    : '';

  if (pub) {
    html += `<div class="direct-workspace"><section class="panel direct-panel"><div class="section-title"><h2>在线沙箱操作指引</h2><span class="chip green">BROWSER SANDBOX</span></div><p class="subtle-note">在右侧列表中选择 <code>demo-sandbox-rc</code>，依次体验完整的 4 步受控仿真生命周期：</p><ol class="tut-list"><li><strong>① 上传输入 (Stage)</strong>：将冻结后的网表与清单上传至隔离工作目录。</li><li><strong>② 启动仿真 (Launch)</strong>：通过幂等占位键原子启动，断线不盲目重提。</li><li><strong>③ 查询状态 (Status)</strong>：读取远端运行进度并观察结束事件。</li><li><strong>④ 收集报告 (Collect)</strong>：输出五维 Spectre 诊断报告并直接查看波形。</li></ol><div class="direct-actions"><button type="button" class="button primary small" data-action="direct-select" data-id="demo-sandbox-rc">选中可交互沙箱任务 (demo-sandbox-rc)</button><a class="button secondary small" href="#local">在本地连接真实 SSH / 集群</a></div></section>`;
  } else {
    html += `<div class="direct-workspace"><section class="panel direct-panel"><div class="section-title"><h2>准备新仿真（本地冻结计划）</h2><span class="chip">PREPARE · NO LAUNCH</span></div><p class="subtle-note">先在本地冻结网表与配置，不联网、不扣算力。${!hostsList.length ? '还没有服务器？可前往 <a href="#quickstart">新手教程第 5 步</a> 或 <a href="#hosts">EDA 服务器管理</a> 登记。' : ''}</p><form id="direct-prepare-form"><div class="direct-grid"><label class="direct-wide">任务名称 / Run Key<input id="direct-run-key" value="${escape(directDraft.run_key)}" required maxlength="128" pattern="[A-Za-z0-9][A-Za-z0-9_\\-]*" placeholder="direct-inv-001"></label><label>执行后端<select id="direct-backend"><option value="ssh_direct" ${directDraft.backend === 'ssh_direct' ? 'selected' : ''}>SSH 直连工作站</option><option value="lsf" ${directDraft.backend === 'lsf' ? 'selected' : ''}>LSF 集群 (bsub)</option><option value="slurm" ${directDraft.backend === 'slurm' ? 'selected' : ''}>Slurm 集群 (sbatch)</option></select></label><label>仿真器<select id="direct-simulator"><option value="spectre" ${directDraft.simulator === 'spectre' ? 'selected' : ''}>Spectre</option><option value="ngspice" ${directDraft.simulator === 'ngspice' ? 'selected' : ''}>ngspice</option></select></label><label>选机方式<select id="direct-target-mode"><option value="host" ${directDraft.target_mode === 'host' ? 'selected' : ''}>指定单台主机</option><option value="pool" ${directDraft.target_mode === 'pool' ? 'selected' : ''}>按资源自动推荐</option></select></label><label>目标主机<select id="direct-host-id" ${directDraft.target_mode === 'pool' || !hostsList.length ? 'disabled' : ''}>${hostsList.length ? hostsList.map(h => `<option value="${escape(h.host_id)}" ${h.host_id === directDraft.host_id ? 'selected' : ''}>${escape(h.label)} (${escape(h.host_id)})</option>`).join('') : '<option value="">尚未注册主机</option>'}</select></label></div>${schedBox}<details class="host-form-details"><summary>进阶：CPU / 内存 / 时长与波形格式（默认 2 核 · 8 GiB）</summary><div class="direct-grid"><label>CPU 核数<input id="direct-cpus" type="number" min="1" max="4096" value="${escape(directDraft.cpus)}" required></label><label>内存 (GiB)<input id="direct-memory" type="number" min="0.1" step="any" max="16384" value="${escape(directDraft.memory_gib)}" required></label><label>最大时长 (秒)<input id="direct-wall" type="number" min="10" max="604800" value="${escape(directDraft.wall_seconds)}" required></label><label>波形格式<select id="direct-format"><option value="psfascii" ${directDraft.output_format === 'psfascii' ? 'selected' : ''}>PSF ASCII</option><option value="psfbin" ${directDraft.output_format === 'psfbin' ? 'selected' : ''}>PSF Binary</option><option value="psfxl" ${directDraft.output_format === 'psfxl' ? 'selected' : ''}>PSFXL</option></select></label></div></details>${recBox}<details class="host-form-details"><summary>进阶：远端环境（module load / setup 脚本）与 PDK Include 目录（可选）</summary><div class="direct-grid"><label>远端 Python3 路径<input id="direct-python" value="${escape(directDraft.remote_python)}"></label><label>远端工作根目录<input id="direct-root" value="${escape(directDraft.remote_root)}" placeholder=".spiceowl-runs 或 /scratch/..."></label><label>远端 Environment Modules（逗号分隔，如 cadence/spectre-23.1）<input id="direct-env-modules" value="${escape(directDraft.env_modules)}" placeholder="cadence/spectre-23.1"></label><label>远端 Setup 脚本绝对路径（逗号分隔）<input id="direct-env-scripts" value="${escape(directDraft.env_scripts)}" placeholder="/opt/cadence/env.sh"></label><label class="direct-wide">Spectre Include 目录（每行一个绝对路径）<textarea id="direct-includes" rows="2" placeholder="/opt/pdk/models">${escape(directDraft.include_dirs)}</textarea></label></div></details><div class="direct-file-row"><label for="direct-netlist">网表内容（input.scs）</label><label class="direct-file-label">从本地文件读取 (.scs/.sp)<input id="direct-netlist-file" type="file" accept=".scs,.sp,.cir,.net,.txt"></label></div><textarea id="direct-netlist" class="direct-netlist" required placeholder="simulator lang=spectre&#10;...">${escape(directDraft.netlist_text)}</textarea><div class="direct-actions"><button class="button primary" type="submit" ${directState.busy || !hostsList.length ? 'disabled' : ''}>冻结并准备计划（Prepare）</button></div></form></section>`;
  }

  html += `<div class="detail-stack"><section class="panel direct-panel"><div class="section-title"><h2>仿真任务列表 (${directState.runs.length})</h2><button class="button secondary small" data-action="direct-refresh" ${directState.busy ? 'disabled' : ''}>刷新列表</button></div><div class="direct-run-list">${directState.runs.map(item => `<button type="button" class="direct-run-row ${item.run_id === run?.run_id ? 'selected' : ''}" data-action="direct-select" data-id="${escape(item.run_id)}"><div><strong>${escape(item.run_id)}</strong><small>${escape(item.backend.toUpperCase())} · ${escape(item.simulator)} · ${escape(item.host_id)}${item.scheduler?.job_id ? ' · Job ' + escape(item.scheduler.job_id) : ''}</small></div>${directRunChip(item)}</button>`).join('') || '<p class="subtle-note">还没有运行记录。先在左侧准备一个实验。</p>'}</div>${!pub ? `<div class="direct-lookup"><input id="direct-lookup-key" value="${escape(directDraft.lookup_key)}" placeholder="按原 Run Key / Submission Key 对账查询"><button type="button" class="button secondary small" data-action="direct-lookup" ${directState.busy ? 'disabled' : ''}>查询原 Key</button></div>${directState.lookup ? `<p class="subtle-note" role="status">${directState.lookup.found ? `已找到原记录：<code>${escape(directState.lookup.run_id)}</code>（状态：${escape(title(directState.lookup.run?.state))}）` : escape(directState.lookup.note)}</p>` : ''}` : ''}</section>`;

  if (run) {
    html += `<section class="panel direct-panel"><div class="section-title"><h2>任务控制 · ${escape(run.run_id)}</h2>${directRunChip(run)}</div><p class="subtle-note">第 <strong>#${escape(run.attempt_number || 1)}</strong> 次尝试（主机：<strong>${escape(run.host_id)}</strong> · 后端：<strong>${escape(run.backend)}</strong> · 仿真器：<strong>${escape(run.simulator)}</strong>${run.scheduler?.job_id ? ` · Job ID：<code>${escape(run.scheduler.job_id)}</code>` : ''}）<br>网表冻结摘要：<code>${escape((run.manifest_sha256 || '').slice(0, 16))}…</code> · 最近观察：${escape(run.observed_at ? new Date(run.observed_at).toLocaleString() : '尚未联网')}</p>${run.state === 'launch_outcome_unknown' ? '<p class="denial">提交回执未知：已锁定原 Key，禁止盲目重提。请点击「③ 查询状态」与远端对账。</p>' : ''}${run.oom_evidence?.oom_kill ? '<p class="denial">调度器已确认该作业触发 OOM（内存耗尽）。</p>' : ''}${run.last_error ? `<p class="error-text">${escape(run.last_error.message || run.last_error.code)}</p>` : ''}<div class="direct-actions"><button type="button" class="button secondary" data-action="direct-stage" data-id="${escape(run.run_id)}" ${canStage ? '' : 'disabled'}>① 上传输入 (Stage)</button><button type="button" class="button primary" data-action="direct-launch" data-id="${escape(run.run_id)}" ${canLaunch ? '' : 'disabled'}>② 启动仿真 (Launch)</button><button type="button" class="button secondary" data-action="direct-status" data-id="${escape(run.run_id)}" ${canStatus ? '' : 'disabled'}>③ 查询状态 (Status)</button><button type="button" class="button primary" data-action="direct-collect" data-id="${escape(run.run_id)}" ${canCollect ? '' : 'disabled'}>④ 收集报告与结果 (Collect)</button>${pub && directState.wfIndex?.datasets?.length ? `<button type="button" class="button secondary" data-action="direct-tab" data-tab="waveforms">📈 打开该任务波形 →</button>` : ''}</div>${!pub ? `<details class="host-form-details"><summary>进阶控制：后台盯守 (Watch) / 取消作业 / M4 有界自动恢复</summary><div class="direct-check-row"><label><input id="direct-recheck" type="checkbox" ${directDraft.recheck_resources ? 'checked' : ''} ${run.backend !== 'ssh_direct' ? 'disabled' : ''}> 启动前复查主机余量（仅 ssh_direct）</label><label><input id="direct-allow-collect" type="checkbox" ${directDraft.allow_collect ? 'checked' : ''}> Watch 确认终态后自动收集并生成报告</label></div><div class="direct-actions"><button type="button" class="button secondary small" data-action="direct-watch" data-id="${escape(run.run_id)}" ${canWatch ? '' : 'disabled'}>开启后台盯守 (Watch)</button><button type="button" class="button secondary small" data-action="direct-unwatch" data-id="${escape(run.run_id)}" ${canUnwatch ? '' : 'disabled'}>停止盯守</button><button type="button" class="button secondary small" data-action="direct-cancel" data-id="${escape(run.run_id)}" ${canCancel ? '' : 'disabled'}>取消作业 (Cancel)</button><button type="button" class="button secondary small" data-action="direct-toggle-policy" data-id="${escape(run.run_id)}" ${directState.busy ? 'disabled' : ''}>${recPolicy.enabled ? '关闭有界恢复' : '开启有界恢复'}</button><button type="button" class="button primary small" data-action="direct-recover" data-id="${escape(run.run_id)}" ${canRecover ? '' : 'disabled'}>执行有界恢复 (Recover)</button></div>${watchEntry ? `<p class="subtle-note">后台盯守：<strong>${escape(title(watchEntry.watch_state))}</strong> · 通信：<strong>${escape(title(watchEntry.observation?.state))}</strong> · 下次轮询：${escape(watchEntry.observation?.next_check_at || '无')}</p>` : ''}</details>` : `<div class="direct-actions"><button type="button" class="button secondary small" data-action="direct-cancel" data-id="${escape(run.run_id)}" ${canCancel ? '' : 'disabled'}>取消当前沙箱作业 (Cancel)</button></div>`}${run.collection ? `<p class="subtle-note">已收集 ${run.collection.file_count} 个文件（${Math.round((run.collection.total_bytes || 0) / 1024)} KiB）${run.collection.collection_complete ? ' · 完整校验通过' : ` · ${run.collection.skipped_files.length} 个超大原始文件已跳过并保留日志`}</p>` : ''}${directReportSection(directState.report)}</section>`;
  }
  html += '</div></div>';
  return html;
}

function batchOutcomeChip(outcome) {
  const map = {
    passed: ['通过 Pass', 'green'],
    failed_spec: ['指标未达标 (有效测量)', 'amber'],
    execution_failed: ['执行失败', 'red'],
    missing: ['缺失 / 未收集', 'red'],
    active: ['运行中', 'blue'],
    pending: ['待派发', ''],
    cancelled: ['已取消', ''],
  };
  const [label, color] = map[outcome] || [title(outcome), ''];
  return chip(label, color);
}

function renderBatchHistogramSvg(summary, metricName) {
  if (!summary || !metricName) return '';
  const st = summary.metric_stats?.[metricName];
  const vals = (summary.cases || [])
    .filter(c => ['passed', 'failed_spec'].includes(c.outcome) && Number.isFinite(Number(c.metrics?.[metricName])))
    .map(c => Number(c.metrics[metricName]));
  if (vals.length < 2 || !st) return '';
  const W = 760, H = 185, padL = 52, padR = 18, padT = 22, padB = 34;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  let xMin = Math.min(...vals), xMax = Math.max(...vals);
  if (st.spec_min !== null && st.spec_min !== undefined) xMin = Math.min(xMin, st.spec_min);
  if (st.spec_max !== null && st.spec_max !== undefined) xMax = Math.max(xMax, st.spec_max);
  const pad = xMax > xMin ? (xMax - xMin) * 0.08 : 1;
  xMin -= pad; xMax += pad;
  const bins = Math.min(18, Math.max(6, Math.round(Math.sqrt(vals.length) * 2)));
  const counts = new Array(bins).fill(0);
  const failBins = new Array(bins).fill(false);
  for (const v of vals) {
    let b = Math.floor(((v - xMin) / (xMax - xMin)) * bins);
    b = Math.max(0, Math.min(bins - 1, b));
    counts[b]++;
    if ((st.spec_min !== null && v < st.spec_min) || (st.spec_max !== null && v > st.spec_max)) failBins[b] = true;
  }
  const maxC = Math.max(1, ...counts);
  const sx = x => padL + ((x - xMin) / (xMax - xMin)) * plotW;
  const bw = plotW / bins;
  const bars = counts.map((cnt, i) => {
    const bh = (cnt / maxC) * plotH;
    const x = padL + i * bw + 1.5;
    const y = padT + plotH - bh;
    const fill = failBins[i] ? '#d97706' : '#1f6f54';
    return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${Math.max(2, bw - 3).toFixed(1)}" height="${bh.toFixed(1)}" rx="2" fill="${fill}" fill-opacity="0.78"></rect>`;
  }).join('');

  let guides = '';
  if (st.mean !== null && st.mean !== undefined) {
    const mx = sx(st.mean);
    guides += `<line x1="${mx.toFixed(1)}" y1="${padT}" x2="${mx.toFixed(1)}" y2="${padT + plotH}" stroke="#2563eb" stroke-width="1.5" stroke-dasharray="4 3"></line><text x="${mx.toFixed(1)}" y="${padT - 6}" text-anchor="middle" font-size="10" fill="#2563eb">μ=${st.mean.toFixed(3)}</text>`;
  }
  if (st.spec_min !== null && st.spec_min !== undefined) {
    const lx = sx(st.spec_min);
    guides += `<line x1="${lx.toFixed(1)}" y1="${padT}" x2="${lx.toFixed(1)}" y2="${padT + plotH}" stroke="#dc2626" stroke-width="1.6" stroke-dasharray="5 3"></line><text x="${lx.toFixed(1)}" y="${padT + 12}" text-anchor="start" dx="3" font-size="10" font-weight="700" fill="#dc2626">LSL ${st.spec_min}</text>`;
  }
  if (st.spec_max !== null && st.spec_max !== undefined) {
    const ux = sx(st.spec_max);
    guides += `<line x1="${ux.toFixed(1)}" y1="${padT}" x2="${ux.toFixed(1)}" y2="${padT + plotH}" stroke="#dc2626" stroke-width="1.6" stroke-dasharray="5 3"></line><text x="${ux.toFixed(1)}" y="${padT + 12}" text-anchor="end" dx="-3" font-size="10" font-weight="700" fill="#dc2626">USL ${st.spec_max}</text>`;
  }
  const xTicks = [xMin, 0.5 * (xMin + xMax), xMax].map(v => `<text x="${sx(v).toFixed(1)}" y="${H - 10}" text-anchor="middle" font-size="10" fill="#5e7369">${v.toFixed(3)} ${escape(st.unit || '')}</text>`).join('');
  return `<div class="wf-plot-card"><p class="subtle-note"><strong>分布直方图 · <code>${escape(metricName)}</code></strong>（有效样本 ${st.count}，缺失/执行失败 ${st.missing_or_failed_count}；琥珀色柱表示落入规格外的有效样本）</p><svg class="wf-svg" viewBox="0 0 ${W} ${H}"><rect x="${padL}" y="${padT}" width="${plotW}" height="${plotH}" fill="#ffffff" stroke="#d8e2dc"></rect>${bars}${guides}${xTicks}<text x="${padL - 8}" y="${padT + 10}" text-anchor="end" font-size="10" fill="#5e7369">${maxC}</text><text x="${padL - 8}" y="${padT + plotH}" text-anchor="end" font-size="10" fill="#5e7369">0</text></svg></div>`;
}

function directBatchPanel(hostsList) {
  const rows = directState.batches || [];
  const detail = directState.batchDetail;
  const summary = detail?.summary || null;
  const batch = detail?.batch || null;

  const pub = isPublicDemo();
  let html = `<div class="wf-toolbar-top"><p class="subtle-note">统一管理 <strong>PVT Corner 矩阵</strong> 与 <strong>Monte Carlo 失配采样</strong>：点击下方按钮即可切换 27-Corner PVT 与 40 样本 MC，绿色=达标、琥珀色=有效测量但指标超标、红色=执行失败/缺失（绝不静默丢弃种子）。</p><div class="direct-actions"><button type="button" class="button primary small" data-action="direct-batch-demo" data-kind="pvt" ${directState.batchBusy ? 'disabled' : ''}>加载 27-Corner PVT 演示</button><button type="button" class="button secondary small" data-action="direct-batch-demo" data-kind="monte_carlo" ${directState.batchBusy ? 'disabled' : ''}>加载 40 样本 MC 演示</button></div></div>`;
  if (directState.batchError) html += `<p class="error-text" role="alert">${escape(directState.batchError)}</p>`;

  if (!pub) {
    html += `<details class="host-form-details"><summary>新建批量实验（PVT Corner / Monte Carlo · 复用「单次仿真」里的主机与网表模板）</summary><form id="direct-batch-form"><div class="direct-grid"><label>批量标识 (Batch ID)<input id="batch-id" value="${escape(batchDraft.batch_id)}" required pattern="[A-Za-z0-9][A-Za-z0-9_\\-]*" placeholder="opamp-pvt-001"></label><label>批量类型<select id="batch-kind"><option value="pvt" ${batchDraft.kind === 'pvt' ? 'selected' : ''}>PVT Corner 矩阵</option><option value="monte_carlo" ${batchDraft.kind === 'monte_carlo' ? 'selected' : ''}>Monte Carlo 采样</option><option value="pvt_mc" ${batchDraft.kind === 'pvt_mc' ? 'selected' : ''}>混合 PVT × Monte Carlo</option></select></label><label>最大并发数 (1–32)<input id="batch-max-conc" type="number" min="1" max="32" value="${escape(batchDraft.max_concurrency)}" required></label><label>工艺角（逗号分隔）<input id="batch-corners" value="${escape(batchDraft.corners)}" placeholder="tt, ss, ff"></label><label>电源电压 V（逗号分隔）<input id="batch-voltages" value="${escape(batchDraft.voltages)}" placeholder="1.08, 1.20, 1.32"></label><label>温度 °C（逗号分隔）<input id="batch-temps" value="${escape(batchDraft.temps)}" placeholder="-40, 27, 125"></label><label>MC 样本数 (1–256)<input id="batch-mc-samples" type="number" min="1" max="256" value="${escape(batchDraft.mc_samples)}" ${batchDraft.kind === 'pvt' ? 'disabled' : ''}></label><label>MC 起始随机种子<input id="batch-mc-seed" type="number" min="0" value="${escape(batchDraft.mc_base_seed)}" ${batchDraft.kind === 'pvt' ? 'disabled' : ''}></label><label>可选验收指标名（留空不设）<input id="batch-spec-name" value="${escape(batchDraft.spec_name)}" placeholder="vout_settle"></label><label>验收信号名<input id="batch-spec-signal" value="${escape(batchDraft.spec_signal)}" placeholder="v(out)"></label><label>指标下限 min<input id="batch-spec-min" value="${escape(batchDraft.spec_min)}" placeholder="0.85"></label><label>指标上限 max<input id="batch-spec-max" value="${escape(batchDraft.spec_max)}" placeholder="0.95"></label></div><div class="direct-actions"><button type="submit" class="button primary small" ${directState.batchBusy || !hostsList.length ? 'disabled' : ''}>冻结并展开批量计划 (Prepare Batch)</button></div></form></details>`;
  }

  const listHtml = rows.map(b => `<button type="button" class="direct-run-row ${b.batch_id === summary?.batch_id ? 'selected' : ''}" data-action="direct-batch-select" data-id="${escape(b.batch_id)}"><div><strong>${escape(b.title || b.batch_id)}</strong><small><code>${escape(b.batch_id)}</code> · ${escape(String(b.kind || '').toUpperCase())} · 并发上限 ${b.max_concurrency} · 通过 ${b.passed_cases}/${b.total_cases} · 指标未达标 ${b.spec_failed_cases} · 执行失败/缺失 ${b.exec_failed_cases + b.missing_cases}</small></div>${chip(title(b.status), b.status === 'completed_all_passed' ? 'green' : b.status === 'running' ? 'blue' : b.exec_failed_cases > 0 || b.missing_cases > 0 ? 'red' : 'amber')}</button>`).join('')
    || '<p class="subtle-note">当前还没有批量实验。点击上方演示按钮可立即体验 27-Corner PVT 矩阵或 40 样本 Monte Carlo 良率统计。</p>';

  html += `<div class="direct-run-list" style="margin-top:10px">${listHtml}</div>`;

  if (summary && batch) {
    const c = summary.counts, y = summary.yield;
    const metricKeys = Object.keys(summary.metric_stats || {});
    const activeMetric = metricKeys.includes(directState.batchMetric) ? directState.batchMetric : (metricKeys[0] || '');
    const canDispatch = !batch.synthetic && c.pending > 0 && !directState.batchBusy;
    const canRefresh = !batch.synthetic && (c.active > 0 || c.missing_or_uncollected > 0) && !directState.batchBusy;
    const canCancel = !batch.synthetic && (c.active > 0 || c.pending > 0) && !directState.batchBusy;

    html += `<div class="direct-rec-card"><div class="section-title"><h3>批量详情 · ${escape(summary.title)}</h3>${chip(`保守良率 ${y.conservative_yield_pct}% (${y.passed_samples}/${y.total_samples})`, y.execution_failed_samples > 0 || y.missing_or_uncollected_samples > 0 ? 'red' : y.spec_failed_samples > 0 ? 'amber' : 'green')}</div>`;
    html += `<div class="direct-report-grid"><div class="direct-dim-card"><span>总样本 / 覆盖率</span><strong>${c.total} · ${y.measured_coverage_pct}%</strong></div><div class="direct-dim-card"><span>规格通过 (Pass)</span><strong>${c.passed}</strong></div><div class="direct-dim-card"><span>有效测量未达标</span><strong>${c.failed_spec}</strong></div><div class="direct-dim-card"><span>执行失败 / 缺失</span><strong>${c.execution_failed} / ${c.missing_or_uncollected}</strong></div><div class="direct-dim-card"><span>并发控制 (活跃 / 上限)</span><strong>${c.active} / ${summary.max_concurrency}</strong></div></div>`;
    html += `<p class="subtle-note">${escape(y.note)}</p>`;

    html += `<div class="direct-actions"><button type="button" class="button primary small" data-action="direct-batch-dispatch" data-id="${escape(summary.batch_id)}" ${canDispatch ? '' : 'disabled'}>按并发上限派发下一批 (Dispatch)</button><button type="button" class="button secondary small" data-action="direct-batch-refresh" data-id="${escape(summary.batch_id)}" ${canRefresh ? '' : 'disabled'}>刷新状态并收集 (Refresh)</button><button type="button" class="button secondary small" data-action="direct-batch-cancel" data-id="${escape(summary.batch_id)}" ${canCancel ? '' : 'disabled'}>取消未完成任务 (Cancel)</button><button type="button" class="button secondary small" data-action="direct-batch-download-md" data-id="${escape(summary.batch_id)}">下载 Markdown 报告 (.md)</button><button type="button" class="button secondary small" data-action="direct-batch-download-csv" data-id="${escape(summary.batch_id)}">导出 CSV 数据 (.csv)</button>${metricKeys.length > 1 ? `<select id="batch-metric-select" class="batch-metric-picker">${metricKeys.map(m => `<option value="${escape(m)}" ${m === activeMetric ? 'selected' : ''}>展示指标：${escape(m)}</option>`).join('')}</select>` : ''}</div>`;

    if (metricKeys.length) {
      const statRows = metricKeys.map(m => {
        const st = summary.metric_stats[m];
        const fmt = v => (typeof v === 'number' && Number.isFinite(v)) ? v.toFixed(3) : '—';
        const wc = summary.worst_corners?.[m];
        return `<tr><td><code>${escape(m)}</code> ${escape(st.unit || '')}</td><td>[${st.spec_min ?? '—'}, ${st.spec_max ?? '—'}]</td><td>${st.count} / ${st.missing_or_failed_count}</td><td>${fmt(st.mean)}</td><td>${fmt(st.std)}</td><td>${fmt(st.min)} / ${fmt(st.p50)} / ${fmt(st.max)}</td><td><strong>${fmt(st.cpk)}</strong></td><td>${wc ? `<small>${escape(wc.min_case.label)} → ${escape(wc.max_case.label)}</small>` : '—'}</td></tr>`;
      }).join('');
      html += `<div class="direct-rec-table-wrap"><table class="direct-rec-table"><thead><tr><th>指标</th><th>规格区间</th><th>有效 / 缺失</th><th>均值 μ</th><th>标准差 σ</th><th>Min / P50 / Max</th><th>Cpk</th><th>极值 Corner 范围</th></tr></thead><tbody>${statRows}</tbody></table></div>`;
    }

    if (activeMetric) {
      html += renderBatchHistogramSvg(summary, activeMetric);
    }

    // Visual PVT Grid / Sample Cards (click any card to inspect child run & waveform!)
    html += `<p class="subtle-note" style="margin-top:12px"><strong>全部样本 / Corner 矩阵（点击任意卡片立即在上方「任务控制」与「波形查看器」中打开该样本）：</strong></p><div class="batch-case-grid">${(summary.cases || []).map(cs => {
      const mVal = activeMetric && Number.isFinite(Number(cs.metrics?.[activeMetric]))
        ? `${Number(cs.metrics[activeMetric]).toFixed(3)} ${escape(summary.metric_stats?.[activeMetric]?.unit || '')}`
        : (cs.failure_reason ? escape(cs.failure_reason.slice(0, 36)) : escape(cs.run_state));
      const cls = `batch-cell outcome-${escape(cs.outcome)}`;
      return `<button type="button" class="${cls}" data-action="direct-select" data-id="${escape(cs.run_id)}" title="${escape(cs.label + ' · ' + (cs.failure_reason || cs.outcome))}"><strong>${escape(cs.mc_seed !== null && cs.mc_seed !== undefined ? `#${cs.sample_index + 1} · s${cs.mc_seed}` : `${cs.process_corner} · ${cs.voltage}V · ${cs.temperature}°C`)}</strong><span>${mVal}</span></button>`;
    }).join('')}</div>`;

    if ((summary.missing_or_failed_samples || []).length) {
      html += `<h4>显式保留的失败 / 规格未达标 / 缺失样本（共 ${summary.missing_or_failed_samples.length} 条 · 种子与身份不丢弃）</h4><div class="direct-rec-table-wrap"><table class="direct-rec-table"><thead><tr><th>Case ID</th><th>Corner / V / T</th><th>MC Seed</th><th>归类</th><th>原因</th><th>操作</th></tr></thead><tbody>${summary.missing_or_failed_samples.map(item => `<tr><td><code>${escape(item.case_id)}</code></td><td>${escape(item.process_corner)} / ${item.voltage}V / ${item.temperature}°C</td><td>${item.mc_seed ?? '—'}</td><td>${batchOutcomeChip(item.outcome)}</td><td>${escape(item.reason || '—')}</td><td><button type="button" class="button secondary small" data-action="direct-select" data-id="${escape(item.run_id)}">查看详情</button></td></tr>`).join('')}</tbody></table></div>`;
    }
    html += '</div>';
  }

  return `<section class="panel direct-panel direct-batch-panel"><div class="section-title"><h2>PVT / Corner / Monte Carlo 批量实验管理</h2><span class="section-note">共 ${rows.length} 个批次</span></div>${html}</section>`;
}

const WF_COLORS = ['#1f6f54', '#2563eb', '#c2410c', '#7c3aed', '#0891b2', '#be123c', '#a16207', '#4f46e5'];

function formatEng(val, unit = '') {
  if (val === null || val === undefined || !Number.isFinite(Number(val))) return '—';
  const n = Number(val);
  if (n === 0) return `0${unit ? ' ' + unit : ''}`;
  const abs = Math.abs(n);
  const prefixes = [
    [1e9, 'G'], [1e6, 'M'], [1e3, 'k'], [1, ''],
    [1e-3, 'm'], [1e-6, 'µ'], [1e-9, 'n'], [1e-12, 'p'], [1e-15, 'f'],
  ];
  for (const [scale, p] of prefixes) {
    if (abs >= scale) {
      const scaled = n / scale;
      const digits = Math.abs(scaled) >= 100 ? 1 : Math.abs(scaled) >= 10 ? 2 : 3;
      return `${scaled.toFixed(digits)} ${p}${unit}`.trim();
    }
  }
  return `${n.toExponential(2)}${unit ? ' ' + unit : ''}`;
}

function currentWfDatasetMeta() {
  const list = directState.wfIndex?.datasets || [];
  return list.find(d => d.dataset === directState.wfDataset) || list[0] || null;
}

function syncWaveformSelection(resetData = false) {
  if (resetData) {
    directState.wfQuery = null;
    directState.wfCycles = null;
    directState.wfMeasure = null;
    directState.wfCursorA = null;
    directState.wfCursorB = null;
    directState.wfCursorNext = 'A';
    directState.wfWindow = {xMin: '', xMax: ''};
    directState.wfSelectedCycle = null;
    directState.wfJumpCycle = '';
    directState.wfError = null;
    directState.wfClock = '';
    directState.wfPeriod = '';
    directState.wfWorstSignal = '';
  }
  const datasets = directState.wfIndex?.datasets || [];
  if (!datasets.length) {
    directState.wfDataset = '';
    directState.wfSignals = [];
    return;
  }
  if (!datasets.some(d => d.dataset === directState.wfDataset)) {
    directState.wfDataset = datasets[0].dataset;
  }
  const ds = currentWfDatasetMeta();
  const avail = (ds?.signals || []).map(s => s.name);
  directState.wfSignals = directState.wfSignals.filter(s => avail.includes(s));
  const clk = avail.find(name => /clk|clock/i.test(name)) || '';
  if (!directState.wfSignals.length && avail.length) {
    const primary = avail.find(name => name !== clk);
    directState.wfSignals = primary ? [primary] : avail.slice(0, 1);
  }
  if (!directState.wfClock || !avail.includes(directState.wfClock)) {
    directState.wfClock = clk;
  }
  if (!directState.wfWorstSignal || !directState.wfSignals.includes(directState.wfWorstSignal)) {
    const nonClk = directState.wfSignals.find(name => name !== directState.wfClock);
    directState.wfWorstSignal = nonClk || directState.wfSignals[0] || '';
  }
}

async function runWaveformQuery(overrideWindow = null) {
  const runId = directState.selectedId;
  const ds = currentWfDatasetMeta();
  if (!runId || !ds || !directState.wfSignals.length || directState.wfBusy) return;
  const win = overrideWindow || directState.wfWindow;
  const body = {
    dataset: ds.dataset,
    signals: directState.wfSignals,
    max_points: Number(directState.wfMaxPoints) || 1200,
    mode: 'auto',
  };
  if (win.xMin !== '' && win.xMin !== null && win.xMin !== undefined && Number.isFinite(Number(win.xMin))) {
    body.x_min = Number(win.xMin);
  }
  if (win.xMax !== '' && win.xMax !== null && win.xMax !== undefined && Number.isFinite(Number(win.xMax))) {
    body.x_max = Number(win.xMax);
  }
  directState.wfBusy = true;
  directState.wfError = null;
  render();
  try {
    directState.wfQuery = await api(`/api/direct/runs/${encodeURIComponent(runId)}/waveforms/query`, {method: 'POST', body});
    directState.wfWindow = {
      xMin: body.x_min !== undefined ? String(body.x_min) : '',
      xMax: body.x_max !== undefined ? String(body.x_max) : '',
    };
  } catch (error) {
    directState.wfError = error.message;
    toast(error.message);
  } finally {
    directState.wfBusy = false;
    render();
  }
}

async function runWaveformCycles(explicitCycleIndex = null) {
  const runId = directState.selectedId;
  const ds = currentWfDatasetMeta();
  if (!runId || !ds || !directState.wfSignals.length || directState.wfBusy) return;
  const body = {
    dataset: ds.dataset,
    signals: directState.wfSignals,
    worst_metric: directState.wfWorstMetric || 'peak_to_peak',
    worst_signal: directState.wfWorstSignal || directState.wfSignals[0],
    overlay_cycles: 10,
  };
  if (directState.wfClock) {
    body.clock_signal = directState.wfClock;
    body.edge = 'rising';
  } else if (directState.wfPeriod !== '' && Number.isFinite(Number(directState.wfPeriod)) && Number(directState.wfPeriod) > 0) {
    body.period = Number(directState.wfPeriod);
  } else {
    directState.wfError = '请选择一个时钟信号，或输入正数固定周期（例如 1e-8）。';
    render();
    return;
  }
  const idx = explicitCycleIndex !== null ? explicitCycleIndex : directState.wfSelectedCycle;
  if (Number.isInteger(idx) && idx >= 0) {
    body.cycle_index = idx;
  }
  directState.wfBusy = true;
  directState.wfError = null;
  render();
  try {
    directState.wfCycles = await api(`/api/direct/runs/${encodeURIComponent(runId)}/waveforms/cycles`, {method: 'POST', body});
    if (explicitCycleIndex !== null) {
      directState.wfSelectedCycle = explicitCycleIndex;
      directState.wfJumpCycle = String(explicitCycleIndex);
      directState.wfCycleTab = 'selected';
    } else if (!directState.wfCycles.key_cycles?.[directState.wfCycleTab]) {
      directState.wfCycleTab = 'worst_case';
    }
  } catch (error) {
    directState.wfError = error.message;
    toast(error.message);
  } finally {
    directState.wfBusy = false;
    render();
  }
}

function sampleSignalAtX(q, sigName, targetX) {
  if (!q || !q.signals?.[sigName]) return null;
  const s = q.signals[sigName];
  const xs = q.x || s.x || [], ys = s.values || [];
  if (!xs.length || xs.length !== ys.length) return null;
  const toNum = v => (v && typeof v === 'object' ? Number(v.mag ?? v.real ?? 0) : Number(v));
  let lo = 0, hi = xs.length - 1;
  if (targetX <= xs[0]) return {x: xs[0], y: toNum(ys[0])};
  if (targetX >= xs[hi]) return {x: xs[hi], y: toNum(ys[hi])};
  while (lo + 1 < hi) {
    const mid = (lo + hi) >> 1;
    if (xs[mid] <= targetX) lo = mid; else hi = mid;
  }
  const dx = xs[hi] - xs[lo];
  const frac = dx > 0 ? (targetX - xs[lo]) / dx : 0;
  const y = toNum(ys[lo]) + frac * (toNum(ys[hi]) - toNum(ys[lo]));
  return {x: targetX, y};
}

function renderCursorReadoutBar(q) {
  const ca = directState.wfCursorA, cb = directState.wfCursorB;
  if (ca === null && cb === null) {
    if (directState.wfToolMode !== 'cursor') return '';
    return `<div class="wf-cursor-bar"><span><strong>📏 双游标卡尺模式已开启：</strong>点击下方波形图放置 <strong>游标 A（琥珀色）</strong>，再次点击放置 <strong>游标 B（紫色）</strong>，实时计算 Δt、等效频率、ΔV 与压摆率。</span></div>`;
  }
  const sig = Object.keys(q?.signals || {})[0] || 'v(out)';
  const unit = q?.sweep?.unit || 's';
  const pa = ca !== null ? sampleSignalAtX(q, sig, ca) : null;
  const pb = cb !== null ? sampleSignalAtX(q, sig, cb) : null;
  const items = [];
  if (pa) items.push(`<span class="wf-cur-pill cur-a"><strong>A</strong> t=${escape(formatEng(pa.x, unit))} · ${escape(sig)}=${escape(formatEng(pa.y, 'V'))}</span>`);
  if (pb) items.push(`<span class="wf-cur-pill cur-b"><strong>B</strong> t=${escape(formatEng(pb.x, unit))} · ${escape(sig)}=${escape(formatEng(pb.y, 'V'))}</span>`);
  if (pa && pb) {
    const dt = pb.x - pa.x, dv = pb.y - pa.y;
    const freq = Math.abs(dt) > 1e-18 ? (1 / Math.abs(dt)) : null;
    const sr = Math.abs(dt) > 1e-18 ? Math.abs(dv / dt) : null;
    items.push(`<span class="wf-cur-pill cur-delta"><strong>Δt</strong> ${escape(formatEng(dt, unit))}${freq ? ` (f=${escape(formatEng(freq, 'Hz'))})` : ''}</span>`);
    items.push(`<span class="wf-cur-pill cur-delta"><strong>ΔV</strong> ${escape(formatEng(dv, 'V'))}${sr ? ` · |ΔV/Δt|=${escape(formatEng(sr, 'V/s'))}` : ''}</span>`);
  }
  return `<div class="wf-cursor-bar">${items.join('')}<button type="button" class="button secondary small" data-action="direct-wf-clear-cursors">清除游标</button></div>`;
}

function renderAnalogMetricsCard(mRes) {
  if (!mRes || !mRes.metrics) return '';
  const m = mRes.metrics, u = mRes.unit || 'V', xu = mRes.x_unit || 's';
  const fmt = (val, unitStr = '') => (val === null || val === undefined || !Number.isFinite(Number(val))) ? '—' : formatEng(val, unitStr);
  const cards = [
    ['最小值 / 最大值', `${fmt(m.min, u)} / ${fmt(m.max, u)}`],
    ['峰峰值 (Vpp)', fmt(m.peak_to_peak, u)],
    ['均值 / RMS', `${fmt(m.mean, u)} / ${fmt(m.rms, u)}`],
    ['上升时间 (10–90%)', fmt(m.rise_time, xu)],
    ['下降时间 (90–10%)', fmt(m.fall_time, xu)],
    [`建立时间 (±${m.settling_tol_pct ?? 2}%)`, fmt(m.settling_time, xu)],
    ['过冲 / 下冲', `${m.overshoot_pct !== null && m.overshoot_pct !== undefined ? m.overshoot_pct.toFixed(2) + '%' : '—'} / ${m.undershoot_pct !== null && m.undershoot_pct !== undefined ? m.undershoot_pct.toFixed(2) + '%' : '—'}`],
    ['正压摆率 (SR+)', fmt(m.slew_rate_pos, `${u}/${xu}`)],
    ['周期 / 频率', m.period_mean ? `${fmt(m.period_mean, xu)} / ${fmt(m.frequency_hz, 'Hz')}` : '—'],
    ['占空比 / RMS 抖动', m.duty_cycle_pct !== null && m.duty_cycle_pct !== undefined ? `${m.duty_cycle_pct.toFixed(1)}% / ${fmt(m.period_jitter_rms, xu)}` : '—'],
  ];
  return `<div class="wf-metrics-card"><div class="section-title"><h3>无损模拟量测结果 · <code>${escape(mRes.signal)}</code></h3>${chip(`100% 无损 (${mRes.sample_count} 点 · measurement_safe)`, 'green')}</div><div class="direct-report-grid">${cards.map(([k, v]) => `<div class="direct-dim-card"><span>${escape(k)}</span><strong>${escape(v)}</strong></div>`).join('')}</div></div>`;
}

function renderCompareOverlaySvg(cmp) {
  const traces = cmp?.traces || [];
  if (!traces.length) return '';
  const W = 760, H = 240, padL = 66, padR = 18, padT = 18, padB = 34;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  let xMin = Infinity, xMax = -Infinity, yMin = Infinity, yMax = -Infinity;
  for (const tr of traces) {
    for (const x of tr.x) { if (x < xMin) xMin = x; if (x > xMax) xMax = x; }
    for (const y of tr.y) { if (Number.isFinite(y)) { if (y < yMin) yMin = y; if (y > yMax) yMax = y; } }
  }
  if (!Number.isFinite(xMin) || xMax <= xMin) { xMin = 0; xMax = 1; }
  if (!Number.isFinite(yMin) || !Number.isFinite(yMax)) { yMin = 0; yMax = 1; }
  const yPad = (yMax - yMin) > 0 ? (yMax - yMin) * 0.08 : 0.1;
  yMin -= yPad; yMax += yPad;
  const sx = x => padL + ((x - xMin) / (xMax - xMin)) * plotW;
  const sy = y => padT + (1 - (y - yMin) / (yMax - yMin)) * plotH;
  const yTicks = [yMin, 0.5 * (yMin + yMax), yMax];
  const xTicks = [xMin, xMin + 0.25 * (xMax - xMin), xMin + 0.5 * (xMax - xMin), xMin + 0.75 * (xMax - xMin), xMax];
  const grid = yTicks.map(yv => `<line x1="${padL}" y1="${sy(yv).toFixed(1)}" x2="${W - padR}" y2="${sy(yv).toFixed(1)}" stroke="#e3e9e5" stroke-dasharray="3 3"></line><text x="${padL - 8}" y="${(sy(yv) + 4).toFixed(1)}" text-anchor="end" font-size="10" fill="#5e7369">${escape(formatEng(yv))}</text>`).join('')
    + xTicks.map(xv => `<text x="${sx(xv).toFixed(1)}" y="${H - 12}" text-anchor="middle" font-size="10" fill="#5e7369">${escape(formatEng(xv, 's'))}</text>`).join('');
  const lines = traces.map((tr, idx) => {
    const color = WF_COLORS[idx % WF_COLORS.length];
    const step = Math.max(1, Math.floor(tr.x.length / 500));
    const pts = [];
    for (let i = 0; i < tr.x.length; i += step) pts.push(`${sx(tr.x[i]).toFixed(1)},${sy(tr.y[i]).toFixed(1)}`);
    return `<polyline points="${pts.join(' ')}" fill="none" stroke="${color}" stroke-width="1.8" stroke-linejoin="round"></polyline>`;
  }).join('');
  const legend = traces.map((tr, idx) => `<span class="wf-legend-item"><i style="background:${WF_COLORS[idx % WF_COLORS.length]}"></i>${escape(tr.label)}</span>`).join('');
  const rows = traces.map((tr, idx) => `<tr><td><i class="wf-dot-inline" style="background:${WF_COLORS[idx % WF_COLORS.length]}"></i><strong>${escape(tr.label)}</strong></td><td>${escape(formatEng(tr.metrics?.min, 'V'))}</td><td>${escape(formatEng(tr.metrics?.max, 'V'))}</td><td>${escape(formatEng(tr.metrics?.final_value, 'V'))}</td><td>${escape(formatEng(tr.metrics?.rise_time, 's'))}</td><td>${escape(formatEng(tr.metrics?.settling_time, 's'))}</td><td>${tr.metrics?.overshoot_pct !== null && tr.metrics?.overshoot_pct !== undefined ? tr.metrics.overshoot_pct.toFixed(2) + '%' : '—'}</td></tr>`).join('');
  return `<div class="wf-plot-card"><div class="wf-legend">${legend}</div><svg class="wf-svg" viewBox="0 0 ${W} ${H}"><rect x="${padL}" y="${padT}" width="${plotW}" height="${plotH}" fill="#ffffff" stroke="#d8e2dc"></rect>${grid}${lines}</svg><div class="direct-rec-table-wrap"><table class="direct-rec-table"><thead><tr><th>Corner / Run</th><th>Vmin</th><th>Vmax</th><th>稳态终值</th><th>上升时间</th><th>建立时间 (2%)</th><th>过冲</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
}

function renderWaveformSvg(q, {interactive = false, height = 250} = {}) {
  if (!q || !q.signals) return '';
  const sigNames = Object.keys(q.signals);
  if (!sigNames.length) return '';
  const W = 760, H = height, padL = 66, padR = 18, padT = 18, padB = 34;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const xMin = Number(q.sweep?.min ?? 0);
  const xMax = Number(q.sweep?.max ?? 1);
  const xSpan = xMax > xMin ? (xMax - xMin) : 1;
  let yMin = Infinity, yMax = -Infinity;
  const toNum = v => (v && typeof v === 'object' ? Number(v.mag ?? v.real ?? 0) : Number(v));
  for (const name of sigNames) {
    const s = q.signals[name];
    if (Number.isFinite(s.min)) yMin = Math.min(yMin, s.min);
    if (Number.isFinite(s.max)) yMax = Math.max(yMax, s.max);
    for (const v of (s.values || [])) {
      const nv = toNum(v);
      if (Number.isFinite(nv)) { yMin = Math.min(yMin, nv); yMax = Math.max(yMax, nv); }
    }
  }
  if (!Number.isFinite(yMin) || !Number.isFinite(yMax)) { yMin = 0; yMax = 1; }
  const yPad = (yMax - yMin) > 0 ? (yMax - yMin) * 0.08 : 0.1;
  yMin -= yPad; yMax += yPad;
  const ySpan = yMax - yMin;
  const sx = x => padL + ((x - xMin) / xSpan) * plotW;
  const sy = y => padT + (1 - (y - yMin) / ySpan) * plotH;

  let shapes = '';
  sigNames.forEach((name, idx) => {
    const color = WF_COLORS[idx % WF_COLORS.length];
    const s = q.signals[name];
    if (!q.exact && q.envelopes?.[name]?.length) {
      const envs = q.envelopes[name];
      const upper = envs.map(b => `${sx(0.5 * (b.t0 + b.t1)).toFixed(1)},${sy(b.max).toFixed(1)}`);
      const lower = [...envs].reverse().map(b => `${sx(0.5 * (b.t0 + b.t1)).toFixed(1)},${sy(b.min).toFixed(1)}`);
      shapes += `<polygon points="${upper.concat(lower).join(' ')}" fill="${color}" fill-opacity="0.20" stroke="none"></polygon>`;
      const xs = s.x || [], ys = s.values || [];
      if (xs.length) {
        const pts = xs.map((x, i) => `${sx(x).toFixed(1)},${sy(toNum(ys[i])).toFixed(1)}`).join(' ');
        shapes += `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.35" stroke-linejoin="round"></polyline>`;
      }
    } else {
      const xs = q.x || s.x || [], ys = s.values || [];
      if (xs.length) {
        const pts = xs.map((x, i) => `${sx(x).toFixed(1)},${sy(toNum(ys[i])).toFixed(1)}`).join(' ');
        shapes += `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.7" stroke-linejoin="round"></polyline>`;
        if (xs.length <= 160) {
          shapes += xs.map((x, i) => `<circle cx="${sx(x).toFixed(1)}" cy="${sy(toNum(ys[i])).toFixed(1)}" r="2.2" fill="${color}"></circle>`).join('');
        }
      }
    }
  });

  const unit = q.sweep?.unit || '';
  const yTicks = [yMin, 0.5 * (yMin + yMax), yMax];
  const xTicks = [xMin, xMin + 0.25 * xSpan, xMin + 0.5 * xSpan, xMin + 0.75 * xSpan, xMax];
  const grid = yTicks.map(yv => `<line x1="${padL}" y1="${sy(yv).toFixed(1)}" x2="${W - padR}" y2="${sy(yv).toFixed(1)}" stroke="#e3e9e5" stroke-dasharray="3 3"></line><text x="${padL - 8}" y="${(sy(yv) + 4).toFixed(1)}" text-anchor="end" font-size="10" fill="#5e7369">${escape(formatEng(yv))}</text>`).join('')
    + xTicks.map(xv => `<line x1="${sx(xv).toFixed(1)}" y1="${padT}" x2="${sx(xv).toFixed(1)}" y2="${H - padB}" stroke="#edf2ee"></line><text x="${sx(xv).toFixed(1)}" y="${H - 12}" text-anchor="middle" font-size="10" fill="#5e7369">${escape(formatEng(xv, unit))}</text>`).join('');

  let cursorOverlay = '';
  if (interactive) {
    const drawCur = (cx, color, lbl) => {
      if (cx === null || cx === undefined || !Number.isFinite(cx) || cx < xMin || cx > xMax) return '';
      const px = sx(cx).toFixed(1);
      return `<line x1="${px}" y1="${padT}" x2="${px}" y2="${H - padB}" stroke="${color}" stroke-width="1.6" stroke-dasharray="4 3"></line><rect x="${(Number(px) - 9).toFixed(1)}" y="${padT + 2}" width="18" height="15" rx="3" fill="${color}"></rect><text x="${px}" y="${padT + 13}" text-anchor="middle" font-size="10" font-weight="700" fill="#ffffff">${lbl}</text>`;
    };
    cursorOverlay = drawCur(directState.wfCursorA, '#d97706', 'A') + drawCur(directState.wfCursorB, '#7c3aed', 'B');
  }
  const legend = sigNames.map((name, idx) => `<span class="wf-legend-item"><i style="background:${WF_COLORS[idx % WF_COLORS.length]}"></i>${escape(name)}</span>`).join('');
  return `<div class="wf-legend">${legend}</div><svg class="wf-svg ${interactive ? (directState.wfToolMode === 'cursor' ? 'wf-clickable' : 'wf-zoomable') : ''}" viewBox="0 0 ${W} ${H}" ${interactive ? `data-wf-zoom-svg="1" data-xmin="${xMin}" data-xmax="${xMax}" data-padl="${padL}" data-padr="${padR}" data-w="${W}"` : ''}><rect x="${padL}" y="${padT}" width="${plotW}" height="${plotH}" fill="#ffffff" stroke="#d8e2dc"></rect>${grid}${shapes}${cursorOverlay}${interactive ? '<rect class="wf-zoom-brush" x="0" y="' + padT + '" width="0" height="' + plotH + '" hidden></rect>' : ''}</svg>`;
}

function renderCycleTrendSvg(cData) {
  const rows = cData?.cycle_metrics || [];
  if (!rows.length) return '';
  const W = 760, H = 165, padL = 66, padR = 18, padT = 18, padB = 30;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const sig = cData.worst_signal;
  const metric = cData.worst_metric;
  const vals = rows.map(r => {
    if (metric === 'period_deviation') return r.duration;
    const m = r.signals?.[sig] || {};
    if (metric === 'max_value' || metric === 'overshoot') return m.max;
    if (metric === 'min_value') return m.min;
    return m.peak_to_peak;
  });
  const finite = vals.filter(v => Number.isFinite(v));
  if (!finite.length) return '';
  let yMin = Math.min(...finite), yMax = Math.max(...finite);
  const yPad = (yMax - yMin) > 0 ? (yMax - yMin) * 0.12 : 0.05;
  yMin -= yPad; yMax += yPad;
  const n = rows.length;
  const sx = i => padL + (n > 1 ? (i / (n - 1)) * plotW : plotW * 0.5);
  const sy = y => padT + (1 - (y - yMin) / (yMax - yMin)) * plotH;

  // For >600 cycles, decimate polyline points cleanly while preserving exact worst & selected markers
  const step = Math.max(1, Math.floor(n / 400));
  const polyPts = [];
  for (let i = 0; i < n; i += step) {
    if (Number.isFinite(vals[i])) polyPts.push(`${sx(i).toFixed(1)},${sy(vals[i]).toFixed(1)}`);
  }
  const wi = cData.worst_cycle_index;
  if (Number.isFinite(vals[wi])) {
    // Ensure worst point is visibly spiked on the curve too
    const insertIdx = Math.min(polyPts.length, Math.floor(wi / step) + 1);
    polyPts.splice(insertIdx, 0, `${sx(wi).toFixed(1)},${sy(vals[wi]).toFixed(1)}`);
  }
  const si = directState.wfSelectedCycle;
  const metricLabel = {
    peak_to_peak: `${sig} 峰峰值 Vpp`,
    max_value: `${sig} 周期最大值`,
    min_value: `${sig} 周期最小值`,
    overshoot: `${sig} 过冲峰值`,
    period_deviation: '周期时长 ΔT',
  }[metric] || metric;
  const yTicks = [yMin, 0.5 * (yMin + yMax), yMax];
  const grid = yTicks.map(yv => `<line x1="${padL}" y1="${sy(yv).toFixed(1)}" x2="${W - padR}" y2="${sy(yv).toFixed(1)}" stroke="#e3e9e5" stroke-dasharray="3 3"></line><text x="${padL - 8}" y="${(sy(yv) + 4).toFixed(1)}" text-anchor="end" font-size="10" fill="#5e7369">${escape(formatEng(yv))}</text>`).join('');
  const xMarks = [0, Math.floor((n - 1) * 0.25), Math.floor((n - 1) * 0.5), Math.floor((n - 1) * 0.75), n - 1]
    .map(i => `<text x="${sx(i).toFixed(1)}" y="${H - 10}" text-anchor="middle" font-size="10" fill="#5e7369">#${i}</text>`).join('');
  const worstDot = Number.isFinite(vals[wi])
    ? `<circle cx="${sx(wi).toFixed(1)}" cy="${sy(vals[wi]).toFixed(1)}" r="6" fill="#dc2626" fill-opacity="0.22" stroke="#dc2626" stroke-width="2"></circle><text x="${Math.min(W - padR - 46, Math.max(padL + 46, sx(wi))).toFixed(1)}" y="${Math.max(padT + 12, sy(vals[wi]) - 9).toFixed(1)}" text-anchor="middle" font-size="10" font-weight="700" fill="#b91c1c">Worst #${wi}</text>`
    : '';
  const selDot = (Number.isInteger(si) && si >= 0 && si < n && Number.isFinite(vals[si]))
    ? `<circle cx="${sx(si).toFixed(1)}" cy="${sy(vals[si]).toFixed(1)}" r="5" fill="#2563eb" fill-opacity="0.25" stroke="#2563eb" stroke-width="2"></circle>`
    : '';

  return `<p class="subtle-note"><strong>逐周期趋势图（${escape(metricLabel)} · 共 ${n} 个周期）</strong>：点击图中任意周期位置，立即无损钻取该周期内部波形。</p><svg class="wf-svg wf-clickable" viewBox="0 0 ${W} ${H}" data-wf-trend-svg="1" data-count="${n}" data-padl="${padL}" data-padr="${padR}" data-w="${W}"><rect x="${padL}" y="${padT}" width="${plotW}" height="${plotH}" fill="#ffffff" stroke="#d8e2dc"></rect>${grid}${xMarks}<polyline points="${polyPts.join(' ')}" fill="none" stroke="#1f6f54" stroke-width="1.5"></polyline>${worstDot}${selDot}</svg>`;
}

function renderOverlaySvg(cData) {
  const traces = cData?.overlay?.cycles || [];
  if (!traces.length) return '<p class="subtle-note">无多周期叠画数据。</p>';
  const W = 760, H = 230, padL = 66, padR = 18, padT = 18, padB = 34;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  let xMax = 0, yMin = Infinity, yMax = -Infinity;
  for (const tr of traces) {
    for (const t of tr.tau) if (t > xMax) xMax = t;
    for (const v of tr.values) if (Number.isFinite(v)) { yMin = Math.min(yMin, v); yMax = Math.max(yMax, v); }
  }
  if (xMax <= 0) xMax = 1;
  const yPad = (yMax - yMin) > 0 ? (yMax - yMin) * 0.08 : 0.1;
  yMin -= yPad; yMax += yPad;
  const sx = x => padL + (x / xMax) * plotW;
  const sy = y => padT + (1 - (y - yMin) / (yMax - yMin)) * plotH;
  const normal = traces.filter(t => !t.is_worst).map(tr => `<polyline points="${tr.tau.map((x, i) => `${sx(x).toFixed(1)},${sy(tr.values[i]).toFixed(1)}`).join(' ')}" fill="none" stroke="#1f6f54" stroke-opacity="0.35" stroke-width="1.4"></polyline>`).join('');
  const worst = traces.filter(t => t.is_worst).map(tr => `<polyline points="${tr.tau.map((x, i) => `${sx(x).toFixed(1)},${sy(tr.values[i]).toFixed(1)}`).join(' ')}" fill="none" stroke="#dc2626" stroke-width="2.2"></polyline>`).join('');
  const unit = cData.sweep_unit || 's';
  const yTicks = [yMin, 0.5 * (yMin + yMax), yMax];
  const xTicks = [0, 0.25 * xMax, 0.5 * xMax, 0.75 * xMax, xMax];
  const grid = yTicks.map(yv => `<line x1="${padL}" y1="${sy(yv).toFixed(1)}" x2="${W - padR}" y2="${sy(yv).toFixed(1)}" stroke="#e3e9e5" stroke-dasharray="3 3"></line><text x="${padL - 8}" y="${(sy(yv) + 4).toFixed(1)}" text-anchor="end" font-size="10" fill="#5e7369">${escape(formatEng(yv))}</text>`).join('')
    + xTicks.map(xv => `<text x="${sx(xv).toFixed(1)}" y="${H - 12}" text-anchor="middle" font-size="10" fill="#5e7369">+${escape(formatEng(xv, unit))}</text>`).join('');
  return `<div class="wf-legend"><span class="wf-legend-item"><i style="background:#1f6f54"></i>稳态周期叠画 (${traces.filter(t => !t.is_worst).length} 周期)</span><span class="wf-legend-item"><i style="background:#dc2626"></i>最差异常周期 (#${cData.worst_cycle_index})</span></div><svg class="wf-svg" viewBox="0 0 ${W} ${H}"><rect x="${padL}" y="${padT}" width="${plotW}" height="${plotH}" fill="#ffffff" stroke="#d8e2dc"></rect>${grid}${normal}${worst}</svg>`;
}

function directWaveformPanel(run) {
  const pub = isPublicDemo();
  const datasets = directState.wfIndex?.datasets || [];
  const ds = currentWfDatasetMeta();
  const hasSkippedRaw = (run?.collection?.skipped_files || []).some(f => String(f.name || '').startsWith('results.raw/'));
  const q = directState.wfQuery;
  const c = directState.wfCycles;

  let body = `<div class="tut-tip-strip"><span><strong>3 步上手：</strong></span><span>① 选信号或点「加载 500 周期演示」</span><span>→</span><span>② <strong>在图上横向拖拽框选</strong>自动切 100% 无损原始点</span><span>→</span><span>③ 点下方趋势图任意点钻取单周期（自动标出 #317 毛刺）</span></div>`;
  body += `<div class="wf-toolbar-top"><p class="subtle-note">大文件原生保留在远端服务器，仅按需读取选定信号与时间窗口；全局预览采用 <strong>Min-Max 包络</strong>（绝不丢毛刺），放大或单周期切片自动切为 <strong>100% 无损原始点</strong>。</p><div class="direct-actions"><button type="button" class="button primary small" data-action="direct-demo-wf" ${directState.busy || directState.wfBusy ? 'disabled' : ''}>加载 500 周期 PSF Binary 演示波形（含 #317 毛刺）</button>${run && !pub ? `<button type="button" class="button secondary small" data-action="direct-wf-refresh-remote" data-id="${escape(run.run_id)}" ${directState.wfBusy ? 'disabled' : ''}>${hasSkippedRaw ? '扫描远端未下载的大波形' : '刷新波形索引'}</button>` : ''}</div></div>`;

  if (directState.wfError) {
    body += `<p class="error-text" role="alert">${escape(directState.wfError)}</p>`;
  }

  if (!run || !datasets.length) {
    body += `<p class="subtle-note">${run ? '当前选中的任务暂无波形数据集。点击上方绿色按钮可一键生成并载入 500 周期 PSF Binary 演示波形。' : '点击上方绿色按钮一键体验 500 周期（20,000 点）PSF Binary 演示波形。'}</p>`;
    return `<section class="panel direct-panel direct-wf-panel"><div class="section-title"><h2>二进制大波形查看器与周期切片（PSF Binary / PSFXL / ASCII）</h2><span class="chip">COMPUTE-NEAR-DATA</span></div>${body}</section>`;
  }

  const formatChip = chip(String(ds.format || 'unknown').toUpperCase(), ds.format === 'psfbin' || ds.format === 'psfxl' ? 'blue' : '');
  const srcChip = chip(ds.source === 'remote_in_place' ? '远端原位查询（零全量下载）' : pub ? '浏览器内合成二进制波形' : '本地已收集', ds.source === 'remote_in_place' ? 'amber' : 'green');
  const sizeKb = (Number(ds.bytes || 0) / 1024).toFixed(1);
  const availSignals = ds.signals || [];

  body += `<div class="wf-meta-bar">${formatChip}${srcChip}<span class="subtle-note">数据集：<code>${escape(ds.dataset)}</code> · ${sizeKb} KiB · 总采样点 <strong>${escape(ds.sweep?.count ?? 0)}</strong> · 范围 ${escape(formatEng(ds.sweep?.min, ds.sweep?.unit || ''))} ~ ${escape(formatEng(ds.sweep?.max, ds.sweep?.unit || ''))}</span></div>`;

  body += `<div class="wf-signal-box"><strong>选择信号：</strong>${availSignals.map(s => `<label class="wf-sig-pill"><input type="checkbox" data-wf-signal="${escape(s.name)}" ${directState.wfSignals.includes(s.name) ? 'checked' : ''}> ${escape(s.name)}${s.unit ? ` (${escape(s.unit)})` : ''}</label>`).join('')}</div>`;

  body += `<div class="direct-actions"><button type="button" class="button primary small" data-action="direct-wf-query" ${directState.wfBusy || !directState.wfSignals.length ? 'disabled' : ''}>${directState.wfBusy ? '查询中…' : '查询 / 刷新波形'}</button><div class="wf-mode-switch"><button type="button" class="wf-tab ${directState.wfToolMode !== 'cursor' ? 'active' : ''}" data-action="direct-wf-mode" data-mode="zoom">🔍 拖拽缩放</button><button type="button" class="wf-tab ${directState.wfToolMode === 'cursor' ? 'active' : ''}" data-action="direct-wf-mode" data-mode="cursor">📏 双游标卡尺 A/B</button></div><button type="button" class="button secondary small" data-action="direct-wf-zoom-in" ${directState.wfBusy || !q ? 'disabled' : ''}>放大 2×</button><button type="button" class="button secondary small" data-action="direct-wf-zoom-out" ${directState.wfBusy || !q ? 'disabled' : ''}>缩小 2×</button><button type="button" class="button secondary small" data-action="direct-wf-pan-left" ${directState.wfBusy || !q ? 'disabled' : ''}>← 左移</button><button type="button" class="button secondary small" data-action="direct-wf-pan-right" ${directState.wfBusy || !q ? 'disabled' : ''}>右移 →</button><button type="button" class="button secondary small" data-action="direct-wf-reset" ${directState.wfBusy || !q ? 'disabled' : ''}>重置全范围</button><button type="button" class="button primary small" data-action="direct-wf-measure" ${directState.wfBusy || !directState.wfSignals.length ? 'disabled' : ''}>📐 一键无损量测</button><button type="button" class="button secondary small" data-action="direct-wf-export-csv" ${!q ? 'disabled' : ''}>↓ 导出 CSV</button><button type="button" class="button secondary small" data-action="direct-wf-export-svg" ${!q ? 'disabled' : ''}>↓ 导出 SVG</button></div>`;

  body += `<details class="host-form-details"><summary>精确窗口与数据集参数（支持直接拖拽图面缩放，无需手输）</summary><div class="direct-grid wf-controls"><label>波形数据集<select id="wf-dataset">${datasets.map(d => `<option value="${escape(d.dataset)}" ${d.dataset === ds.dataset ? 'selected' : ''}>${escape(d.dataset)} (${escape(d.format)} · ${d.sweep?.count ?? 0} pts)</option>`).join('')}</select></label><label>窗口起点 (${escape(ds.sweep?.unit || 's')})<input id="wf-xmin" value="${escape(directState.wfWindow.xMin)}" placeholder="${escape(String(ds.sweep?.min ?? ''))}"></label><label>窗口终点 (${escape(ds.sweep?.unit || 's')})<input id="wf-xmax" value="${escape(directState.wfWindow.xMax)}" placeholder="${escape(String(ds.sweep?.max ?? ''))}"></label><label>最大预览点数<input id="wf-maxpts" type="number" min="64" max="20000" value="${escape(directState.wfMaxPoints)}"></label></div></details>`;

  if (q) {
    const modeBanner = q.exact
      ? `<div class="wf-mode-banner exact">${chip('100% 原始无损点 (exact_window)', 'green')}<span>当前窗口共 <strong>${q.sweep.window_count}</strong> 个原始采样点，已无损返回（<code>measurement_safe: true</code>，可直接用于精确量测）。${directState.wfToolMode === 'cursor' ? '当前处于游标模式：点击图面放置 A/B 卡尺。' : '继续横向拖拽可进一步放大。'}</span></div>`
      : `<div class="wf-mode-banner preview">${chip('Min-Max 包络预览 (downsampled_preview)', 'amber')}<span>当前窗口共 <strong>${q.sweep.window_count}</strong> 个原始点，已使用 <strong>${q.downsampling?.buckets || 0}</strong> 桶流式 Min-Max 包络（完整保留所有毛刺尖峰，<code>measurement_safe: false</code>）。<strong>在下方图上横向拖拽框选</strong>或点「放大 2×」将自动切换为 100% 无损原始点；点击「📐 一键无损量测」将直接请求无损原始采样进行精确计算。</span></div>`;
    body += `<div class="wf-plot-card">${modeBanner}${renderCursorReadoutBar(q)}${renderWaveformSvg(q, {interactive: true})}</div>`;
  }
  if (directState.wfMeasure) {
    body += renderAnalogMetricsCard(directState.wfMeasure);
  }

  // Multi-Corner / Multi-Run Overlay Comparison Sub-Panel
  body += `<div class="wf-cycle-card"><div class="section-title"><h3>多 Corner / 多任务同图叠画对比（Multi-Corner Overlay）</h3><span class="chip">PVT OVERLAY</span></div><p class="subtle-note">将多个工艺角（例如慢角 <code>ss</code>、典型角 <code>tt</code>、快角 <code>ff</code>）的 <code>v(out)</code> 叠在同一张图上，直观对比上升时间、建立时间与过冲差异。</p><div class="direct-actions"><button type="button" class="button secondary small" data-action="direct-wf-compare-corners" ${directState.wfBusy ? 'disabled' : ''}>${directState.wfBusy ? '加载对比中…' : '📊 一键叠画对比 tt / ss / ff 工艺角'}</button>${directState.wfCompare ? '<button type="button" class="button secondary small" data-action="direct-wf-clear-compare">收起对比图</button>' : ''}</div>${directState.wfCompare ? renderCompareOverlaySvg(directState.wfCompare) : ''}</div>`;

  // Cycle Slicer & Drill-Down Sub-Panel
  body += `<div class="wf-cycle-card"><div class="section-title"><h3>多周期切片分析（Cycle Slicer · 逐周期趋势 + 自动抓最差毛刺 + 单周期无损钻取）</h3><span class="chip">CYCLE SLICER</span></div><p class="subtle-note">面对数百至上千个周期的长瞬态仿真，自动逐周期统计极值/峰峰值/过冲，标出最差异常周期并支持点击任意周期看其 100% 无损内部波形。</p><div class="direct-grid"><label>时钟边沿信号（推荐）<select id="wf-clock"><option value="">（不使用时钟，改用固定周期 T）</option>${availSignals.map(s => `<option value="${escape(s.name)}" ${s.name === directState.wfClock ? 'selected' : ''}>${escape(s.name)}</option>`).join('')}</select></label><label>固定周期 T（无时钟时填写）<input id="wf-period" value="${escape(directState.wfPeriod)}" placeholder="例如 1e-8" ${directState.wfClock ? 'disabled' : ''}></label><label>最差周期判定指标<select id="wf-worst-metric"><option value="peak_to_peak" ${directState.wfWorstMetric === 'peak_to_peak' ? 'selected' : ''}>峰峰值最大 (peak_to_peak)</option><option value="max_value" ${directState.wfWorstMetric === 'max_value' ? 'selected' : ''}>周期峰值最高 (max_value)</option><option value="min_value" ${directState.wfWorstMetric === 'min_value' ? 'selected' : ''}>周期谷值最低 (min_value)</option><option value="overshoot" ${directState.wfWorstMetric === 'overshoot' ? 'selected' : ''}>相对稳态过冲最大 (overshoot)</option><option value="period_deviation" ${directState.wfWorstMetric === 'period_deviation' ? 'selected' : ''}>周期时长偏差最大 (period_deviation)</option></select></label><label>指标评估信号<select id="wf-worst-signal">${(directState.wfSignals.length ? directState.wfSignals : availSignals.map(s => s.name)).map(name => `<option value="${escape(name)}" ${name === directState.wfWorstSignal ? 'selected' : ''}>${escape(name)}</option>`).join('')}</select></label></div><div class="direct-actions"><button type="button" class="button primary small" data-action="direct-wf-cycles" ${directState.wfBusy || !directState.wfSignals.length ? 'disabled' : ''}>${directState.wfBusy ? '分析中…' : '执行周期切片分析'}</button>${c ? `<div class="wf-jump-box"><input id="wf-jump-cycle" type="number" min="0" max="${Math.max(0, c.cycle_count - 1)}" value="${escape(directState.wfJumpCycle)}" placeholder="输入周期号 0~${Math.max(0, c.cycle_count - 1)}"><button type="button" class="button secondary small" data-action="direct-wf-jump-cycle" ${directState.wfBusy ? 'disabled' : ''}>钻取该周期</button></div>` : ''}</div>`;

  if (c) {
    const det = c.detection?.basis === 'clock_signal'
      ? `时钟 <code>${escape(c.detection.clock_signal)}</code> ${escape(c.detection.edge)} 边沿 (阈值 ${escape(formatEng(c.detection.threshold, 'V'))})`
      : `固定周期 ${escape(formatEng(c.detection?.period, c.sweep_unit || 's'))}`;
    body += `<div class="wf-cycle-summary">${chip(`共 ${c.cycle_count} 个周期`, 'blue')}${chip(`最差异常周期：#${c.worst_cycle_index}`, 'amber')}<span class="subtle-note">切片依据：${det} · 评估信号：<code>${escape(c.worst_signal)}</code></span></div>`;
    body += renderCycleTrendSvg(c);

    const tabs = [
      ['worst_case', `最差异常周期 (#${c.worst_cycle_index})`],
      ['first', '起始周期 (#0)'],
      ['steady_last', `稳态末周期 (#${Math.max(0, c.cycle_count - 1)})`],
      ['overlay', '多周期叠画 (Eye Overlay)'],
    ];
    if (c.key_cycles?.selected) {
      tabs.push(['selected', `已钻取周期 (#${c.key_cycles.selected.cycle_index})`]);
    }
    body += `<div class="wf-tabs">${tabs.map(([k, label]) => `<button type="button" class="wf-tab ${directState.wfCycleTab === k ? 'active' : ''}" data-action="direct-wf-tab" data-tab="${k}">${escape(label)}</button>`).join('')}</div>`;

    if (directState.wfCycleTab === 'overlay') {
      body += `<div class="wf-plot-card">${renderOverlaySvg(c)}</div>`;
    } else {
      const kc = c.key_cycles?.[directState.wfCycleTab] || c.key_cycles?.worst_case;
      if (kc?.waveform) {
        const m = kc.metrics?.signals?.[c.worst_signal] || {};
        body += `<div class="wf-plot-card"><div class="wf-mode-banner exact">${chip(kc.exact ? '单周期 100% 无损波形 (exact_cycle)' : '周期预览', kc.exact ? 'green' : 'amber')}<span>周期 <strong>#${kc.cycle_index}</strong> · 时间 [${escape(formatEng(kc.t_start, c.sweep_unit || 's'))}, ${escape(formatEng(kc.t_end, c.sweep_unit || 's'))}] · 原始点数 ${kc.waveform.sweep?.window_count ?? 0} · <code>${escape(c.worst_signal)}</code> Vmin=${escape(formatEng(m.min, 'V'))} / Vmax=${escape(formatEng(m.max, 'V'))} / Vpp=${escape(formatEng(m.peak_to_peak, 'V'))}</span></div>${renderWaveformSvg(kc.waveform, {interactive: false, height: 225})}</div>`;
      }
    }
  }
  body += '</div>';

  return `<section class="panel direct-panel direct-wf-panel"><div class="section-title"><h2>二进制大波形查看器与周期切片（PSF Binary / PSFXL / ASCII）</h2><span class="chip">COMPUTE-NEAR-DATA</span></div>${body}</section>`;
}

async function handleDirectClick(event) {
  const button = event.target.closest('button[data-action^="direct-"]');
  if (!button) return;
  const action = button.dataset.action.slice(7);
  const id = button.dataset.id;
  if (action === 'tab') {
    const nextTab = button.dataset.tab || 'waveforms';
    directState.activeTab = nextTab;
    if (nextTab === 'waveforms' && !directState.wfIndex?.datasets?.length) {
      const wfRun = directState.runs.find(r => r.run_id === 'demo-psfbin-500c');
      if (wfRun) {
        directState.selectedId = wfRun.run_id;
        await refreshDirect();
        return;
      }
    }
    render();
    if (nextTab === 'waveforms' && directState.wfIndex?.datasets?.length && !directState.wfQuery) {
      await runWaveformQuery();
      if (directState.wfClock || directState.wfPeriod) await runWaveformCycles();
    }
    return;
  }
  if (action === 'use-placement') {
    directDraft.target_mode = 'host';
    directDraft.host_id = id;
    directDraft.cpus = requestedCpus;
    directDraft.memory_gib = requestedMemory;
    directDraft.simulator = requestedSimulator;
    directState.activeTab = isPublicDemo() ? 'runs' : 'setup';
    location.hash = '#direct';
    return;
  }
  if (action === 'pick-host') {
    if (id) {
      directDraft.target_mode = 'host';
      directDraft.host_id = id;
      render();
    }
    return;
  }
  if (action === 'clear-support') {
    simSourceState.supportFiles = {};
    render();
    toast('已清除附带的相对 include 依赖文件。');
    return;
  }
  if (action === 'quick-launch') {
    await quickLaunchDirectSimulation();
    return;
  }
  if (action === 'apply-agent-code') {
    const thread = codexPanelState.threads.find(item => item.id === codexPanelState.selectedThread);
    const msgItem = (thread?.items || []).find(item => item.id === id);
    const code = extractNetlistCodeBlock(msgItem?.text || '');
    if (code) {
      directDraft.netlist_text = code;
      if (/\.tran|\.ac|^\*/m.test(code) && !/simulator\s+lang=spectre/i.test(code)) {
        directDraft.simulator = 'ngspice';
        directDraft.output_format = 'psfascii';
      } else if (/simulator\s+lang=spectre/i.test(code)) {
        directDraft.simulator = 'spectre';
        directDraft.output_format = 'psfbin';
      }
      render();
      toast('✨ 已将 Agent 生成的网表填入右侧配置！');
    } else {
      toast('未在该回复中找到可导入的网表代码块。');
    }
    return;
  }
  if (action === 'refresh') { await refreshDirect(); return; }
  if (action === 'select') {
    directState.selectedId = id;
    if (isPublicDemo()) {
      if (directState.activeTab !== 'runs') directState.activeTab = 'runs';
      await refreshDirect();
    } else {
      overviewState.selectedRunId = id;
      location.hash = '#overview';
      await refreshDirect();
    }
    return;
  }
  if (action === 'rec-select') {
    directState.selectedRecoveryId = id;
    await refreshDirect();
    return;
  }
  if (action === 'rec-search') {
    await refreshDirect();
    return;
  }
  if (action === 'watch-policy-save') {
    try {
      const payload = {
        enabled: Boolean($('#wp-enabled')?.checked),
        auto_push_to_agent: Boolean($('#wp-autopush')?.checked),
        allow_host_migration: Boolean($('#wp-migrate')?.checked),
        allow_memory_scale: Boolean($('#wp-mem')?.checked),
        allow_sim_option_tuning: Boolean($('#wp-opt')?.checked),
        queue_timeout_seconds: Number($('#wp-qtimeout')?.value || 1800),
        max_memory_gib: Number($('#wp-maxmem')?.value || 64),
        max_agent_resubmissions: Number($('#wp-maxresub')?.value || 3),
      };
      const res = await api('/api/direct/watch-policy', {method: 'POST', body: payload});
      directState.watchPolicy = res.policy;
      render();
      toast('已保存睡前 Agent 自治授权边界（无任何外部软件推送，全程仅在网页留痕）。');
    } catch (error) { toast(error.message); }
    return;
  }
  if (action === 'watch-simulate') {
    try {
      const res = await api('/api/direct/watch-events/simulate', {
        method: 'POST',
        body: {scenario: directState.watchSimScenario || 'queue_timeout'},
      });
      directState.watchEvents = Array.isArray(res.events) ? res.events : directState.watchEvents;
      render();
      toast('已演练夜间异常 → 自动交接给用户 Agent → 网页记录决策全流程！');
    } catch (error) { toast(error.message); }
    return;
  }
  if (action === 'watch-push') {
    if (!id) return;
    try {
      await api(`/api/direct/watch-events/${encodeURIComponent(id)}/push-agent`, {method: 'POST', body: {}});
      await refreshDirect();
      toast('已尝试推送事件给当前连接的用户 Agent。');
    } catch (error) { toast(error.message); }
    return;
  }
  if (action === 'demo-wf') {
    if (directState.busy || directState.wfBusy) return;
    directState.activeTab = 'waveforms';
    directState.wfBusy = true; directState.wfError = null; render();
    try {
      const res = await api('/api/direct/demo-waveform', {method: 'POST', body: {}});
      directState.selectedId = res.run.run_id;
      directState.wfIndex = res.waveforms;
      directState.wfBusy = false;
      await refreshDirect();
      syncWaveformSelection(true);
      await runWaveformQuery();
      await runWaveformCycles();
      toast('已生成 500 周期 PSF Binary 演示波形并自动定位周期 #317 的隐藏毛刺！');
    } catch (error) {
      directState.wfBusy = false;
      directState.wfError = error.message;
      toast(error.message);
      render();
    }
    return;
  }
  if (action === 'wf-refresh-remote') {
    if (!id || directState.wfBusy) return;
    directState.wfBusy = true; directState.wfError = null; render();
    try {
      directState.wfIndex = await api(`/api/direct/runs/${encodeURIComponent(id)}/waveforms?refresh_remote=1`);
      syncWaveformSelection(false);
      toast(`已刷新波形索引：发现 ${(directState.wfIndex.datasets || []).length} 个波形数据集。`);
    } catch (error) {
      directState.wfError = error.message;
      toast(error.message);
    } finally {
      directState.wfBusy = false; render();
    }
    return;
  }
  if (action === 'wf-query') { await runWaveformQuery(); return; }
  if (action === 'wf-mode') {
    directState.wfToolMode = button.dataset.mode === 'cursor' ? 'cursor' : 'zoom';
    render();
    return;
  }
  if (action === 'wf-clear-cursors') {
    directState.wfCursorA = null;
    directState.wfCursorB = null;
    directState.wfCursorNext = 'A';
    render();
    return;
  }
  if (action === 'wf-measure') {
    const runId = directState.selectedId;
    const ds = currentWfDatasetMeta();
    const sig = directState.wfSignals[0];
    if (!runId || !ds || !sig || directState.wfBusy) return;
    const body = {dataset: ds.dataset, signal: sig, tol_pct: 2.0};
    if (directState.wfWindow.xMin !== '' && Number.isFinite(Number(directState.wfWindow.xMin))) body.x_min = Number(directState.wfWindow.xMin);
    if (directState.wfWindow.xMax !== '' && Number.isFinite(Number(directState.wfWindow.xMax))) body.x_max = Number(directState.wfWindow.xMax);
    directState.wfBusy = true; directState.wfError = null; render();
    try {
      directState.wfMeasure = await api(`/api/direct/runs/${encodeURIComponent(runId)}/waveforms/measure`, {method: 'POST', body});
      toast(`已完成 ${sig} 无损模拟量测（共 ${directState.wfMeasure.sample_count} 点）。`);
    } catch (error) { directState.wfError = error.message; toast(error.message); }
    finally { directState.wfBusy = false; render(); }
    return;
  }
  if (action === 'wf-compare-corners') {
    if (directState.wfBusy) return;
    directState.wfBusy = true; directState.wfError = null; render();
    try {
      const targets = [
        {runId: 'demo-pvt-27-tt-1p2-27', label: 'TT · 1.20V · 27°C (典型角)'},
        {runId: 'demo-pvt-27-ss-1p08-125', label: 'SS · 1.08V · 125°C (慢角)'},
        {runId: 'demo-pvt-27-ff-1p32-m40', label: 'FF · 1.32V · -40°C (快角)'},
      ];
      // On local non-demo workspaces, compare up to 3 collected runs if available
      const traces = [];
      if (!isPublicDemo()) {
        const localRuns = directState.runs.filter(r => r.collection_phase === 'complete').slice(0, 3);
        for (const r of localRuns) {
          try {
            const idx = await api(`/api/direct/runs/${encodeURIComponent(r.run_id)}/waveforms?allow_remote=0`);
            const ds = idx.datasets?.[0];
            if (!ds) continue;
            const sig = ds.signals?.find(s => !/clk|clock/i.test(s.name))?.name || ds.signals?.[0]?.name;
            if (!sig) continue;
            const qRes = await api(`/api/direct/runs/${encodeURIComponent(r.run_id)}/waveforms/query`, {method: 'POST', body: {dataset: ds.dataset, signals: [sig], max_points: 2000, mode: 'exact'}});
            const mRes = await api(`/api/direct/runs/${encodeURIComponent(r.run_id)}/waveforms/measure`, {method: 'POST', body: {dataset: ds.dataset, signal: sig, tol_pct: 2.0}});
            traces.push({label: `${r.run_id} (${sig})`, x: qRes.x || [], y: qRes.signals[sig].values || [], metrics: mRes.metrics});
          } catch { /* skip unreadable run */ }
        }
      } else {
        for (const t of targets) {
          const qRes = await api(`/api/direct/runs/${encodeURIComponent(t.runId)}/waveforms/query`, {method: 'POST', body: {dataset: 'tran.tran.tran', signals: ['v(out)'], max_points: 2000, mode: 'exact'}});
          const mRes = await api(`/api/direct/runs/${encodeURIComponent(t.runId)}/waveforms/measure`, {method: 'POST', body: {dataset: 'tran.tran.tran', signal: 'v(out)', tol_pct: 2.0}});
          traces.push({label: t.label, x: qRes.x || [], y: qRes.signals['v(out)'].values || [], metrics: mRes.metrics});
        }
      }
      if (!traces.length) throw new Error('暂无可对比的已收集波形；可先加载演示波形或收集多个仿真任务。');
      directState.wfCompare = {traces};
      toast(`已同图叠画 ${traces.length} 条波形对比。`);
    } catch (error) { directState.wfError = error.message; toast(error.message); }
    finally { directState.wfBusy = false; render(); }
    return;
  }
  if (action === 'wf-clear-compare') {
    directState.wfCompare = null;
    render();
    return;
  }
  if (action === 'wf-export-csv' || action === 'wf-export-svg') {
    const q = directState.wfQuery;
    if (!q) return;
    if (action === 'wf-export-svg') {
      const svgEl = document.querySelector('svg[data-wf-zoom-svg="1"]');
      if (!svgEl) return;
      const blob = new Blob([`<?xml version="1.0" encoding="UTF-8"?>\n` + svgEl.outerHTML], {type: 'image/svg+xml;charset=utf-8'});
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a'); a.href = url; a.download = `${directState.selectedId || 'waveform'}.svg`;
      document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
      toast('已导出当前波形 SVG 矢量图。');
      return;
    }
    const sigNames = Object.keys(q.signals || {});
    const primaryXs = q.x || q.signals[sigNames[0]]?.x || [];
    const lines = [['sweep_' + (q.sweep?.name || 'time'), ...sigNames].join(',')];
    for (let i = 0; i < primaryXs.length; i++) {
      lines.push([primaryXs[i], ...sigNames.map(s => {
        const v = q.signals[s]?.values?.[i];
        return (v && typeof v === 'object') ? (v.mag ?? v.real ?? '') : (v ?? '');
      })].join(','));
    }
    const blob = new Blob([lines.join('\n') + '\n'], {type: 'text/csv;charset=utf-8'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = `${directState.selectedId || 'waveform'}.csv`;
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    toast(`已导出波形 CSV（${primaryXs.length} 行）。`);
    return;
  }
  if (action === 'wf-reset') { await runWaveformQuery({xMin: '', xMax: ''}); return; }
  if (action === 'wf-zoom-in' || action === 'wf-zoom-out' || action === 'wf-pan-left' || action === 'wf-pan-right') {
    const sw = directState.wfQuery?.sweep;
    if (!sw || sw.min === null || sw.max === null) return;
    const fMin = Number(sw.full_min ?? sw.min), fMax = Number(sw.full_max ?? sw.max);
    let cMin = Number(sw.min), cMax = Number(sw.max);
    const span = Math.max(1e-18, cMax - cMin);
    if (action === 'wf-zoom-in') {
      const mid = 0.5 * (cMin + cMax);
      cMin = mid - span * 0.25; cMax = mid + span * 0.25;
    } else if (action === 'wf-zoom-out') {
      const mid = 0.5 * (cMin + cMax);
      cMin = Math.max(fMin, mid - span); cMax = Math.min(fMax, mid + span);
    } else if (action === 'wf-pan-left') {
      const shift = Math.min(span * 0.35, cMin - fMin);
      cMin -= shift; cMax -= shift;
    } else {
      const shift = Math.min(span * 0.35, fMax - cMax);
      cMin += shift; cMax += shift;
    }
    await runWaveformQuery({xMin: String(cMin), xMax: String(cMax)});
    return;
  }
  if (action === 'wf-cycles') { await runWaveformCycles(); return; }
  if (action === 'wf-jump-cycle') {
    const n = Number(directState.wfJumpCycle);
    if (!Number.isInteger(n) || n < 0) { toast('请输入有效的非负整数周期编号。'); return; }
    await runWaveformCycles(n);
    return;
  }
  if (action === 'wf-tab') {
    directState.wfCycleTab = button.dataset.tab || 'worst_case';
    render();
    return;
  }
  if (action === 'batch-demo') {
    if (directState.batchBusy) return;
    const kind = button.dataset.kind || 'pvt';
    directState.batchBusy = true; directState.batchError = null; render();
    try {
      const res = await api('/api/direct/batches/demo', {method: 'POST', body: {kind}});
      directState.selectedBatchId = res.batch.batch_id;
      toast(kind === 'pvt' ? '已加载 27-Corner PVT 演示矩阵（含 1 个慢角规格未达标与 1 个确认 OOM 样本）。' : '已加载 40 样本 Monte Carlo 演示（含 2 个尾部失配超标与 1 个显式保留的执行失败种子）。');
    } catch (error) {
      directState.batchError = error.message;
      toast(error.message);
    } finally {
      directState.batchBusy = false;
      await refreshDirect();
    }
    return;
  }
  if (action === 'batch-select') {
    directState.selectedBatchId = id;
    await refreshDirect();
    return;
  }
  if (action === 'batch-dispatch' || action === 'batch-refresh' || action === 'batch-cancel') {
    if (!id || directState.batchBusy) return;
    const op = action.slice(6);
    if (op === 'cancel' && !window.confirm(`确认取消批次 ${id} 中所有运行中及待派发的任务？`)) return;
    directState.batchBusy = true; directState.batchError = null; render();
    try {
      const body = op === 'refresh' ? {auto_collect: true, dispatch_more: true} : {};
      await api(`/api/direct/batches/${encodeURIComponent(id)}/${op}`, {method: 'POST', body});
      toast(`批量操作 ${op} 已完成。`);
    } catch (error) {
      directState.batchError = error.message;
      toast(error.message);
    } finally {
      directState.batchBusy = false;
      await refreshDirect();
    }
    return;
  }
  if (action === 'batch-download-md' || action === 'batch-download-csv') {
    const isCsv = action === 'batch-download-csv';
    const content = isCsv ? directState.batchDetail?.csv : directState.batchDetail?.markdown;
    if (!content) { toast('没有可导出的批量报告。'); return; }
    const blob = new Blob([content], {type: isCsv ? 'text/csv;charset=utf-8' : 'text/markdown;charset=utf-8'});
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url; link.download = `${id}-batch-summary.${isCsv ? 'csv' : 'md'}`;
    document.body.appendChild(link); link.click(); link.remove();
    URL.revokeObjectURL(url);
    return;
  }
  if (action === 'rec-download') {
    const md = directState.recoveryDetail?.markdown;
    if (!md) { toast('没有可下载的 Markdown 报告。'); return; }
    const blob = new Blob([md], {type: 'text/markdown;charset=utf-8'});
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url; link.download = `${id}-recovery-report.md`;
    document.body.appendChild(link); link.click(); link.remove();
    URL.revokeObjectURL(url);
    return;
  }
  if (action === 'toggle-policy') {
    const targetRun = directState.runs.find(r => r.run_id === id) || directState.detail;
    if (!targetRun || directState.busy) return;
    directState.busy = true; directState.error = null; render();
    try {
      const nextPolicy = {...(targetRun.recovery_policy || {}), enabled: !(targetRun.recovery_policy?.enabled)};
      await api(`/api/direct/runs/${encodeURIComponent(id)}/policy`, {method: 'POST', body: nextPolicy});
      toast(nextPolicy.enabled ? '已开启该任务的有界恢复策略。' : '已关闭该任务的有界恢复策略。');
    } catch (error) { directState.error = error.message; toast(error.message); }
    finally { directState.busy = false; await refreshDirect(); }
    return;
  }
  if (action === 'recover') {
    if (directState.busy || !id) return;
    directState.busy = true; directState.error = null; render();
    try {
      const res = await api(`/api/direct/runs/${encodeURIComponent(id)}/recover`, {method: 'POST', body: {execute: true}});
      if (res.child_run?.run_id) directState.selectedId = res.child_run.run_id;
      if (res.recovery_report?.root_run_id) directState.selectedRecoveryId = res.recovery_report.root_run_id;
      toast(res.decision?.reason || '已完成有界恢复评估并更新恢复报告。');
    } catch (error) { directState.error = error.message; toast(error.message); }
    finally { directState.busy = false; await refreshDirect(); }
    return;
  }
  if (action === 'lookup') {
    const key = directDraft.lookup_key.trim();
    if (!key) { toast('请先输入要对账的 Run Key。'); return; }
    directState.busy = true; directState.error = null; render();
    try {
      directState.lookup = await api('/api/direct/submission?key=' + encodeURIComponent(key));
      if (directState.lookup.found && directState.lookup.run_id) directState.selectedId = directState.lookup.run_id;
    } catch (error) { directState.error = error.message; }
    finally { directState.busy = false; await refreshDirect(); }
    return;
  }
  if (directState.busy || !id) return;
  if (action === 'cancel' && !window.confirm(`确认取消直连任务 ${id}？只向已验证归属的进程组或集群作业发送取消信号。`)) return;
  directState.busy = true; directState.error = null; render();
  try {
    const targetRun = directState.runs.find(r => r.run_id === id) || directState.detail;
    let body = {};
    if (action === 'launch') body = {recheck_resources: targetRun?.backend === 'ssh_direct' ? !!directDraft.recheck_resources : false};
    if (action === 'watch') body = {allow_collect: !!directDraft.allow_collect};
    const res = await api(`/api/direct/runs/${encodeURIComponent(id)}/${action}`, {method: 'POST', body});
    if (res.report) directState.report = res.report;
    toast(`直连操作 ${action} 已完成。`);
  } catch (error) {
    directState.error = error.message;
    toast(error.message);
  } finally {
    directState.busy = false;
    await refreshDirect();
  }
}

function renderAgentEvalHtml(ev) {
  if (!ev) {
    return '<p class="subtle-note">尚未运行评测。点击上方按钮立即执行 10 项冻结闭环安全测试（覆盖缺项拒猜、断线对账、防假通过、降采样拦截、中断回合恢复及 MC 保守良率），对比确定性编排器与弱模型基线。</p>';
  }
  const det = ev.deterministic, weak = ev.weak_naive;
  return `<div class="direct-report-grid"><div class="direct-dim-card"><span>确定性编排器通过率</span><strong>${det.overall_pass_rate_pct}% (${det.passed_trials}/${det.total_trials})</strong></div><div class="direct-dim-card"><span>确定性安全违规（越权/重复/假通过）</span><strong>${det.safety_counters.unauthorized_dispatches} / ${det.safety_counters.duplicate_dispatches} / ${det.safety_counters.false_scientific_passes}</strong></div><div class="direct-dim-card"><span>弱模型对照基线通过率</span><strong>${weak.overall_pass_rate_pct}% (${weak.passed_trials}/${weak.total_trials})</strong></div><div class="direct-dim-card"><span>弱模型安全违规（越权/重复/假通过）</span><strong>${weak.safety_counters.unauthorized_dispatches} / ${weak.safety_counters.duplicate_dispatches} / ${weak.safety_counters.false_scientific_passes}</strong></div></div><div class="direct-rec-table-wrap"><table class="direct-rec-table"><thead><tr><th>用例</th><th>类别</th><th>确定性编排器</th><th>弱模型基线（暴露缺陷）</th></tr></thead><tbody>${det.results.map((r, i) => `<tr><td><code>${escape(r.case_id)}</code></td><td>${escape(r.category)}</td><td>${chip(r.passed ? 'PASS' : 'FAIL', r.passed ? 'green' : 'red')}</td><td>${chip(weak.results[i]?.passed ? 'PASS' : 'FAIL', weak.results[i]?.passed ? 'green' : 'amber')} <small>${escape((weak.results[i]?.violations || []).join(', ') || '—')}</small></td></tr>`).join('')}</tbody></table></div>`;
}

function publicLocalPage() {
  return heading('本机连接器 · LOCAL CONNECTOR', '安装一次无界面连接器，从此所有操作全在当前网页完成', '您不需要另开一套本地网页或每次敲命令行。在自己的电脑上安装一次 SpiceOwl Connector（注册 spiceowl:// 协议，免管理员权限），以后只需打开当前网页点「连接本机连接器」即可直连 SSH 服务器与本机 Codex Agent。') + `<section class="panel public-info" data-public-demo-info="local"><div class="panel-body"><span class="chip green">macOS · Linux · 原生 Windows（无需 WSL · 免管理员）</span><h2>一生只需安装一次本机连接器</h2><div class="direct-actions" style="margin-bottom:14px"><button type="button" class="button primary" data-action="connector-open">🔌 立即检测 / 唤起本机连接器</button><a class="button secondary" href="${demoRepository}/archive/refs/heads/${escape(demoSource().branch)}.zip" target="_blank" rel="noopener noreferrer">下载安装压缩包 (.zip)</a></div><div class="public-feature-grid"><article><h3>Windows 一键安装</h3><p>解压后双击 <code>Install SpiceOwl Connector.bat</code>，自动写入当前用户注册表 <code>HKCU\\Software\\Classes\\spiceowl</code> 并静默启动后台连接器（无黑框）。</p></article><article><h3>macOS 一键安装</h3><p>解压后双击 <code>Install SpiceOwl Connector.command</code>，自动在 <code>~/Applications</code> 生成轻量 <code>SpiceOwl Connector.app</code> 并后台常驻。</p></article></div><details class="host-form-details"><summary>开发者命令行一键安装（需要 Python 3.11+）</summary><pre class="public-command">git clone --branch ${escape(demoSource().branch)} https://github.com/spiceowl-dev/spiceowl.git
cd spiceowl
python3 -m pip install -e .
spiceowl connector install</pre></details><p class="subtle-note">安装完成后直接留在本网页点击「🔌 连接本机连接器」，系统会自动通过 <code>spiceowl://start</code> 唤起连接器并在本页解锁「✦ 新手步步教程 → 第 5 步」真实 SSH 服务器向导。</p><div class="public-info-links"><a class="button primary" href="#quickstart">回到 5 步网页教程</a><a class="button secondary" href="${demoDocument('docs/host-onboarding.md')}" target="_blank" rel="noopener noreferrer">SSH 服务器接入指南</a><a class="button secondary" href="#overview">返回仿真总览</a></div></div></section>`;
}

function publicHostPage() {
  return heading('服务器池资源 · SERVER POOL & SIMULATIONS', '连接本机连接器后，实时查看所有已连接服务器的资源与运行中仿真', '当前为浏览器预览模式，不会读取本机 SSH；连上本机连接器并在首页接好服务器后，本页会自动展示每台服务器的 CPU、内存、磁盘占用与实时在跑的 Spectre / ngspice / Virtuoso 任务。') + `<section class="panel public-info" data-public-demo-info="hosts"><div class="panel-body"><span class="chip">需本机连接器 · SSH CONNECTOR</span><h2>第二页总览：资源占用 + 运行中仿真（示例示意）</h2><div class="direct-rec-table-wrap"><table class="direct-rec-table"><thead><tr><th>示例计算节点（只读示意）</th><th>CPU 占用 / 核数</th><th>内存已用 / 总计</th><th>当前运行中仿真</th><th>状态</th></tr></thead><tbody><tr><td><code>sim-node-a</code></td><td>18.4% · 64 核</td><td>32 / 128 GiB（可用 96 GiB）</td><td>0 个仿真 · 空闲可提交</td><td>${chip('空闲推荐', 'green')}</td></tr><tr><td><code>sim-node-b</code></td><td>76.2% · 96 核</td><td>48 / 128 GiB（可用 80 GiB）</td><td>3 个 spectre（共占 42.5 核）· 4 个 virtuoso</td><td>${chip('运行中', 'blue')}</td></tr><tr><td><code>sim-node-c</code></td><td>94.1% · 64 核</td><td>116 / 128 GiB（可用 12 GiB）</td><td>5 个 spectre（共占 58.0 核）</td><td>${chip('高负载', 'red')}</td></tr></tbody></table></div><p class="subtle-note">所有 SSH 密钥与服务器配置始终只留在本机，不读取网表路径或命令参数。点击下方按钮连接本机连接器后即可查看你的真实服务器池。</p><div class="public-info-links"><button type="button" class="button primary" data-action="connector-open">🔌 连接本机连接器查看真实服务器</button><a class="button secondary" href="#quickstart">返回首页与连接</a></div></div></section>`;
}

function publicAgentPage() {
  const firstRun = state.runs[0];
  return heading('AI 助手与安全护栏评测 · AGENT & EVAL', '既能直接跑 10 项 AI 安全评测，也能一键连接本机 Codex Agent', '下方 10 项安全闭环评测可直接在浏览器内运行；点击「连接本机连接器」后即可在本网页直接接入您的本机 Codex Agent 并接收夜间值守异常。') + `<section class="panel public-info" data-public-demo-info="agents"><div class="panel-body"><span class="chip green">可直接在浏览器运行 · INTERACTIVE BENCHMARK</span><h2>1. 一键在线运行 10 项 Agent 安全闭环评测</h2><p>验证 SpiceOwl 的确定性 Next-Action 编排器如何在 10 类高风险场景（缺项拒猜、断线防重发、有效测量但指标未达标、降采样拦截量测、MC 失败种子防丢弃等）下实现 <strong>100% 通过且 0 安全违规</strong>。</p><div class="direct-actions"><button type="button" class="button primary" data-action="public-run-eval" ${codexPanelState.evalBusy ? 'disabled' : ''}>${codexPanelState.evalBusy ? '正在运行 10 项评测…' : codexPanelState.evalReport ? '重新运行 10 项安全评测' : '🚀 立即在线运行 10 项 Agent 安全评测'}</button></div><div id="public-eval-output" style="margin-top:10px">${renderAgentEvalHtml(codexPanelState.evalReport)}</div><hr class="tut-divider"><h2>2. 接入您自己的 Agent 进行夜间值守</h2><div class="public-feature-grid"><article><h3>网页直连本机 Codex</h3><p>连上本机连接器后直接在本网页接入 Codex，排队超时或 OOM 发生时自动推送给您的 Agent 排查资源并按睡前策略重提。</p></article><article><h3>嵌入 CircuitGit（Embedded）</h3><p>复用 CircuitGit 当前的 Codex 会话，不额外创建第二个连接，保持统一上下文。</p></article><article><h3>只读诊断体验</h3><p>无需配置任何 API 密钥，现在就可以在「仿真总览」任意场景里点击「✧ Diagnose snapshot」体验只读证据诊断。</p></article></div><div class="public-info-links"><button type="button" class="button primary" data-action="connector-open">🔌 连接本机连接器接入 Codex</button><a class="button secondary" href="${circuitGitEntry}" target="_blank" rel="noopener noreferrer">打开 CircuitGit</a>${firstRun ? `<a class="button secondary" href="#run/${escape(firstRun.run_id)}">体验场景诊断</a>` : '<a class="button secondary" href="#overview">前往仿真总览</a>'}</div></div></section>`;
}

function toast(message) {
  clearTimeout(toastTimer);
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 6000);
}

async function api(path, options = {}) {
  if (isPublicDemo()) {
    if (typeof window.SpiceOwlDemo?.request !== 'function') throw new Error('The web preview adapter could not load. Reload this page to try again.');
    // This explicit adapter owns preview requests; errors never fall back silently.
    return window.SpiceOwlDemo.request(path, options);
  }
  const method = options.method || 'GET';
  const targetUrl = isConnectorLive() ? `${connectorState.baseUrl}${path}` : path;
  const authToken = isConnectorLive() ? connectorState.token : csrf;
  const headers = {'Content-Type': 'application/json'};
  if (isConnectorLive() || method !== 'GET') {
    headers['X-SpiceOwl-Token'] = authToken;
  }
  const response = await fetch(targetUrl, {
    method,
    ...(isConnectorLive() ? {mode: 'cors', credentials: 'omit'} : {}),
    headers,
    ...(method !== 'GET' ? {body: JSON.stringify(options.body || {})} : {}),
  });
  const data = await response.json();
  if (!response.ok) {
    if (isConnectorLive() && response.status === 403 && /Pair this web tab/i.test(data.error?.message || '')) {
      disconnectConnector();
      render();
      const staleErr = new Error('本机连接器刚刚重启，请点右上角「连接本机」重新授权 1 秒续连。');
      staleErr.code = 'connector_session_expired';
      staleErr.status = response.status;
      staleErr.definitive = true;
      throw staleErr;
    }
    const error = new Error(data.error?.message || 'The local request failed.');
    error.code = data.error?.code || 'request_failed';
    error.status = response.status;
    error.definitive = response.status < 500;
    throw error;
  }
  return data;
}

let connectorSessionChecked = false;
function renderSidebarRecentRuns() {
  const el = $('#recent-runs');
  if (!el) return;
  if (!isPublicDemo()) {
    const recent = (directState.runs || []).slice(0, 6);
    if (!recent.length) {
      el.innerHTML = '';
      return;
    }
    el.innerHTML = recent.map(run => {
      const org = resolveRunOrganization(run);
      const st = classifyDirectRunState(run);
      const label = org.testbench && org.testbench !== run.run_id ? `${org.testbench} · ${run.run_id}` : run.run_id;
      return `<a href="#overview" data-open-direct-run="${escape(run.run_id)}" title="${escape(org.project + ' / ' + label)}"><i class="dot dot-${escape(st.dot)}"></i><span class="run-title">${escape(label)}</span></a>`;
    }).join('');
    return;
  }
  el.innerHTML = (state.runs || []).slice(0, 5).map(run => `<a href="#run/${escape(run.run_id)}"><i class="dot"></i><span class="run-title">${escape(run.label)}</span></a>`).join('');
}

async function refresh() {
  readCircuitGitUrlHandoff();
  if (!connectorSessionChecked) {
    connectorSessionChecked = true;
    await restoreConnectorSession();
    try {
      const params = new URLSearchParams(location.search || '');
      if (isHostedPages() && !isConnectorLive() && params.get('connect') === '1') {
        params.delete('connect');
        const nextUrl = location.pathname + (params.toString() ? `?${params.toString()}` : '') + (location.hash || '#quickstart');
        history.replaceState(null, '', nextUrl);
        setTimeout(() => { void startConnectorFlow({openModal: true}); }, 50);
      }
    } catch { /* ignore */ }
  }
  publicDemoChrome();
  state = await api('/api/state');
  publicDemoChrome();
  csrf = state.csrf_token;
  $('#version').textContent = state.version;
  $('#connection-count').textContent = state.connections.length;
  $('#host-count').textContent = state.hosts.length;
  if ($('#direct-count')) $('#direct-count').textContent = directState.loaded ? directState.runs.length : (isPublicDemo() ? '2' : '0');
  $('#native-count').textContent = isStandaloneAgent() ? codexPanelState.threads.length : state.questions.length;
  if (!isPublicDemo()) $('.prototype-badge').textContent = isStandaloneAgent() ? 'Standalone · Codex Agent' : 'CircuitGit channel · Engine';
  renderSidebarRecentRuns();
}

function currentRun() {
  const hash = location.hash.replace(/^#/, '');
  return hash.startsWith('run/') ? state.runs.find(run => run.run_id === hash.slice(4)) : null;
}

function chip(text, color = '') {
  return `<span class="chip ${color}"><i class="dot"></i>${escape(title(text))}</span>`;
}

function runStatus(run) {
  if (run.observation !== 'current') return chip('observation ' + run.observation, 'amber');
  if (run.execution === 'failed') return chip('execution failed', 'red');
  if (run.specification_result === 'fail') return chip('valid · target missed', 'amber');
  if (run.execution === 'completed') return chip('completed', 'green');
  return chip(run.execution, 'blue');
}

function heading(eyebrow, name, subtitle, actions = '') {
  return `<div class="page-heading"><div><div class="eyebrow">${eyebrow}</div><h1>${name}</h1><p class="subtitle">${subtitle}</p></div>${actions}</div>`;
}

function livePending(operation = null) {
  const raw = operation ? pendingGet(livePendingKey(operation)) : pendingGet(LIVE_PENDING) || pendingGet(LIVE_COLLECT_PENDING);
  if (!raw) return null;
  const request = JSON.parse(raw);
  if (!['submit', 'collect'].includes(request.operation) || operation && request.operation !== operation || !request.args || typeof request.idempotency_key !== 'string') throw Error('原请求记录不可读，请在本地客户端核对后继续。');
  return request;
}

async function refreshLive() {
  if (isPublicDemo() || live.loading) return;
  live.loading = true;
  try {
    const value = await api('/api/live/runtime');
    if (value.enabled !== false && (value.enabled !== true || value.runtime?.schema !== 'ic.simulation.runtime.v1' || value.project_id !== value.runtime.project_id)) throw Error('真实仿真快照的项目身份不符。');
    live.enabled = value.enabled; live.projectId = value.project_id || ''; live.runtime = value.runtime; live.error = null;
    const pending = livePending();
    if (pending && !liveDraft.configuration && pending.args.project_id === live.projectId) {
      for (const key of Object.keys(liveDraft)) liveDraft[key] = pending.args[key] || '';
    }
  } catch (error) { live.error = error.message; }
  finally { live.loaded = true; live.loading = false; if (location.hash === '#live') render(); }
}

function liveCollectReady() {
  const candidates=live.runtime?.runs || [];
  return candidates.some(run=>run.run_id===liveDraft.run_id.trim()&&run.configuration===liveDraft.configuration.trim()
    &&run.source_commit===liveDraft.source_commit.trim()&&(!run.project_id||run.project_id===live.projectId)
    &&run.collectable===true);
}

function livePage() {
  let html = heading('STANDALONE SPECTRE', '真实仿真 · CircuitGit', '查看保存的资源与运行记录，明确发起状态检查、仿真提交和结果收集。');
  if (isPublicDemo()) return html + '<section class="panel live-panel" data-public-demo-info="live"><h2>公共演示未启用真实仿真</h2><p>在本地启动服务并配置操作员连接后使用。此页不会访问仿真服务器。</p></section>';
  if (!live.loaded) return html + '<section class="panel live-panel"><p>正在读取连接状态…</p></section>';
  if (!live.enabled) return html + `<section class="panel live-panel" data-live-disabled><h2>真实仿真未启用</h2><p>由本地操作员使用 <code>serve --live-client-config</code> 启动参数指定已配置连接。浏览器不接收配置路径或凭据。</p>${live.error ? `<p class="error-text">${escape(live.error)}</p>` : ''}<button class="button secondary" data-action="live-refresh">刷新连接状态</button></section>`;
  const snapshot = live.runtime || {}, capabilities = snapshot.capabilities || {};
  const hosts = Array.isArray(snapshot.pool?.hosts) ? snapshot.pool.hosts.slice(0, 32) : [];
  const runs = Array.isArray(snapshot.runs) ? snapshot.runs.slice(0, 25) : [];
  const metric = (value, suffix = '') => Number.isFinite(value) ? escape(Number(value.toFixed(1))) + suffix : '未记录';
  const disabled = allowed => live.busy || !allowed ? 'disabled' : '';
  let pending = null, submitPending = null, collectPending = null;
  try { submitPending = livePending('submit'); collectPending = livePending('collect'); pending = submitPending || collectPending; } catch (error) { live.error = error.message; }
  html += `<div class="live-workspace"><section class="panel live-panel"><div class="section-title"><h2>执行身份</h2>${chip('CircuitGit 已配置', 'green')}</div><p class="subtle-note">项目由操作员连接固定。配置、完整提交和运行 ID 必须与目标记录一致。</p><div class="live-input-grid"><label>项目<input id="live-project" value="${escape(live.projectId)}" disabled></label><label>配置 ID<input id="live-configuration" ${live.busy ? 'disabled' : ''} value="${escape(liveDraft.configuration)}" maxlength="180" autocomplete="off" placeholder="tran"></label><label class="live-wide">源提交 · 40 位 SHA<input id="live-source-commit" ${live.busy ? 'disabled' : ''} value="${escape(liveDraft.source_commit)}" maxlength="40" autocomplete="off" spellcheck="false"></label><label class="live-wide">已有运行 ID<input id="live-run-id" ${live.busy ? 'disabled' : ''} value="${escape(liveDraft.run_id)}" maxlength="180" autocomplete="off" spellcheck="false" placeholder="从下方运行记录选取，或输入准确 ID"></label></div><div class="live-actions"><button class="button primary" data-action="live-submit" ${disabled(capabilities.direct_submit && !submitPending)}>提交新仿真</button><button class="button secondary" data-action="live-status" ${disabled(true)}>读取运行状态</button><button class="button secondary" data-action="live-collect" ${disabled(capabilities.direct_collect && !collectPending && liveCollectReady())}>收集结果</button><button class="button secondary" data-action="live-watch" ${disabled(capabilities.monitor)}>开启后台监控</button><button class="button secondary" data-action="live-unwatch" ${disabled(capabilities.monitor)}>停止监控</button></div><p class="subtle-note">停止监控仅停止观察；执行、数据有效性与指标结果分别报告。</p>${pending ? '<p class="live-pending" role="status">原请求键已保存。结果未确认前保留同一请求，不会自动再次提交。</p>' : ''}${submitPending ? '<button class="button secondary small" data-action="live-lookup">查询原提交映射</button>' : ''}${live.busy ? '<p role="status">正在处理明确请求…</p>' : ''}${live.error ? `<p class="error-text" role="alert">${escape(live.error)}</p>` : ''}${live.receipt ? `<div class="live-receipt" role="status"><strong>操作回执</strong><p>状态：${escape(live.receipt.state || live.receipt.status || live.receipt.watch_state || '已返回')}</p>${live.receipt.run_id ? `<code>${escape(live.receipt.run_id)}</code>` : ''}${live.receipt.error_code ? `<p>${escape(live.receipt.error_code)}</p>` : ''}</div>` : ''}</section>`;
  html += `<section class="panel live-panel"><div class="section-title"><h2>资源服务器</h2><div class="live-actions"><button class="button secondary small" data-action="live-refresh" ${disabled(true)}>刷新保存记录</button><button class="button primary small" data-action="live-scan" ${disabled(capabilities.scan && !snapshot.pool?.scan_in_progress)}>检查配置的服务器</button></div></div><p class="subtle-note">资源检查时间：${escape(snapshot.pool?.checked_at || '未检查')}。资源数据是观察值；刷新保存记录不会执行 SSH。</p>${snapshot.pool?.scan_in_progress ? '<p role="status">资源检查进行中…</p>' : ''}<div class="live-table-wrap"><table class="live-table"><thead><tr><th>服务器</th><th>连接</th><th>CPU 使用率 / 核数</th><th>可用 / 总内存</th><th>观察时间</th></tr></thead><tbody>${hosts.map(host => `<tr><td>${escape(host.label || host.host_id)}<br><code>${escape(host.host_id)}</code></td><td>${host.reachable === true ? '已验证' : '未确认'}${host.error_code ? `<br>${escape(host.error_code)}` : ''}</td><td>${metric(host.cpu_busy_percent, '%')} / ${metric(host.cpu_count)}</td><td>${metric(Number.isFinite(host.memory_available_bytes) ? host.memory_available_bytes / 2**30 : null, ' GiB')} / ${metric(Number.isFinite(host.memory_total_bytes) ? host.memory_total_bytes / 2**30 : null, ' GiB')}</td><td>${escape(host.observed_at || '未记录')}</td></tr>`).join('') || '<tr><td colspan="5">尚无资源记录。点击明确检查后查看结果。</td></tr>'}</tbody></table></div></section>`;
  html += `<section class="panel live-panel"><div class="section-title"><h2>真实运行记录</h2><span class="section-note">快照读取：${escape(snapshot.read_at || '未记录')}</span></div><div class="live-run-list">${runs.map(run => {
    const diagnosis = Array.isArray(run.diagnostics?.diagnostics) ? run.diagnostics.diagnostics.slice(0, 8) : [];
    return `<article class="live-run"><div class="section-title"><h3>${escape(run.run_id)}</h3><button class="button secondary small" data-action="live-select" data-id="${escape(run.run_id)}">使用此运行</button></div><div class="live-dimensions"><span>执行：<strong>${escape(title(run.execution?.state))}</strong></span><span>观察：<strong>${escape(title(run.monitor?.state))}</strong></span><span>数据：<strong>${escape(title(run.acceptance?.data_validity))}</strong></span><span>指标：<strong>${escape(title(run.acceptance?.specification))}</strong></span></div><p class="live-identity">配置 ${escape(run.configuration)} · 服务器 ${escape(run.execution_target?.host_id || '未记录')}<br><code>${escape(run.source_commit)}</code></p><p class="subtle-note">最近成功观察：${escape(run.monitor?.last_success_at || '未记录')} · 下一次检查：${escape(run.monitor?.next_check_at || '未安排')}</p>${diagnosis.map(item => `<div class="live-diagnostic"><strong>${escape(item.code || item.category)}</strong>${item.redacted_excerpt ? `<pre>${escape(item.redacted_excerpt)}</pre>` : ''}<ul>${(Array.isArray(item.suggested_actions) ? item.suggested_actions : []).slice(0, 4).map(action => `<li>${escape(action)}</li>`).join('')}</ul></div>`).join('')}</article>`;
  }).join('') || '<p class="muted">当前项目尚无真实运行记录。</p>'}</div></section></div>`;
  return html;
}

function liveArguments(operation) {
  const result = {project_id: live.projectId};
  if (operation === 'scan') return result;
  for (const key of ['configuration', 'source_commit']) result[key] = liveDraft[key].trim();
  if (!/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,179}$/.test(result.configuration) || !/^[a-f0-9]{40}$/.test(result.source_commit)) throw Error('请填写配置 ID 和 40 位小写源提交 SHA。');
  if (operation !== 'submit') {
    result.run_id = liveDraft.run_id.trim();
    if (!/^[A-Za-z0-9_-]{1,180}$/.test(result.run_id)) throw Error('请填写准确的已有运行 ID。');
  }
  return result;
}

async function handleLiveClick(event) {
  const button = event.target.closest('button[data-action^="live-"]');
  if (!button) return;
  if (isPublicDemo()) { location.hash = '#local'; return; }
  const operation = button.dataset.action.slice(5);
  if (operation === 'refresh') { await refreshLive(); return; }
  if (operation === 'lookup') {
    if (live.busy) return;
    try {
      const pending = livePending('submit');
      if (!pending || pending.operation !== 'submit') throw Error('没有待核对的提交键。');
      live.busy = true; live.error = null; render();
      const receipt = await api('/api/live/submission?key=' + encodeURIComponent(pending.idempotency_key));
      live.receipt = receipt;
      if (receipt.run_id) liveDraft = {configuration: pending.args.configuration, source_commit: pending.args.source_commit, run_id: receipt.run_id};
    } catch (error) { live.error = error.message; }
    finally { live.busy = false; if (location.hash === '#live') render(); }
    return;
  }
  if (operation === 'select') {
    const run = live.runtime?.runs.find(item => item.run_id === button.dataset.id);
    if (run) { for (const key of Object.keys(liveDraft)) liveDraft[key] = run[key] || ''; render(); }
    return;
  }
  if (live.busy || !live.enabled || !['scan', 'submit', 'status', 'collect', 'watch', 'unwatch'].includes(operation)) return;
  let request;
  try {
    request = {operation, args: liveArguments(operation)};
    if(operation==='collect'&&!liveCollectReady())throw Error('请先读取此运行状态，确认仿真已结束后再收集结果。');
    if (['submit', 'collect'].includes(operation)) {
      const pending = livePending(operation);
      if (pending) throw Error('请先核对原请求；未知结果不会通过再次提交来恢复。');
      request = {...request, idempotency_key: `live-${crypto.randomUUID()}`};
      pendingSet(livePendingKey(operation), JSON.stringify(request));
    }
    live.busy = true; live.error = null; render();
    const receipt = await api('/api/live/control', {method: 'POST', body: request});
    live.receipt = receipt;
    if (receipt.run_id && (operation === 'submit' || !liveDraft.run_id)) liveDraft.run_id = receipt.run_id;
    const uncertain = ['uncertain', 'outcome_unknown', 'launch_outcome_unknown', 'dispatching'].includes(receipt.state || receipt.status);
    if (request.idempotency_key && !uncertain) pendingRemove(livePendingKey(operation));
    await refreshLive();
  } catch (error) {
    if (request?.idempotency_key && error.definitive && !/uncertain|unresolved|unknown/.test(error.code || '')) pendingRemove(livePendingKey(operation));
    live.error = error.message;
  } finally { live.busy = false; if (location.hash === '#live') render(); }
}

function classifyDirectRunState(run) {
  const s = String(run?.state || 'unknown');
  if (s === 'simulator_complete_pending_measurements') {
    const collected = run?.collection_phase === 'complete';
    return {bucket: 'completed', label: collected ? '已完成 · 已收集' : '已完成', color: 'green', dot: 'green'};
  }
  if (s === 'running_observed') return {bucket: 'active', label: '运行中', color: 'blue', dot: 'blue'};
  if (s === 'submitted' || s === 'staged') return {bucket: 'active', label: s === 'submitted' ? '排队中' : '已上传', color: 'blue', dot: 'blue'};
  if (s === 'prepared') return {bucket: 'prepared', label: '待启动 (Prepared)', color: '', dot: 'muted'};
  if (s === 'simulator_failed' || s === 'job_setup_failed' || s === 'input_hash_failure') {
    const oom = Boolean(run?.oom_evidence?.oom_kill);
    return {bucket: 'failed', label: oom ? 'OOM 失败' : '执行失败', color: 'red', dot: 'red'};
  }
  if (s === 'launch_outcome_unknown' || s === 'reconcile_required') {
    return {bucket: 'failed', label: '待对账核查', color: 'amber', dot: 'amber'};
  }
  return {bucket: 'prepared', label: title(s), color: '', dot: 'muted'};
}

function resolveRunOrganization(run) {
  const org = run?.organization && typeof run.organization === 'object' ? run.organization : {};
  const runId = String(run?.run_id || '');
  const rootId = String(run?.root_run_id || runId);
  const hasChain = Boolean(run?.parent_run_id || (Array.isArray(run?.child_run_ids) && run.child_run_ids.length) || Number(run?.attempt_number || 1) > 1);
  const library = org.library || run?.library_name || '';
  const project = org.project || run?.project_name || library || '未归类项目';
  const inferredStem = rootId.replace(/(?:-(?:20\d{10,12}|\d{6}|r\d+))+$/, '').replace(/^[-_]+|[-_]+$/g, '');
  const fallbackTb = (inferredStem.length >= 2 && !['sim', 'direct', 'run', 'test'].includes(inferredStem.toLowerCase())) ? inferredStem : (runId || '未命名 TB');
  const testbench = org.testbench || run?.cell_name || fallbackTb;
  const view = org.view || run?.view_name || '';
  const batchId = org.batch_id || run?.batch_id || '';
  const submissionGroupId = org.submission_group_id || run?.submission_group_id || (batchId ? `batch:${batchId}` : (hasChain ? `chain:${rootId}` : runId));
  const submissionKind = org.submission_kind || (batchId ? 'batch' : (hasChain ? 'recovery_chain' : (submissionGroupId !== runId ? 'multi_run' : 'single')));
  const submissionLabel = org.submission_label || run?.submission_label || (batchId || (hasChain ? `${testbench} · 重试链` : runId));
  return {
    project,
    library,
    testbench,
    view,
    batchId,
    caseLabel: org.case_label || '',
    submissionGroupId,
    submissionKind,
    submissionLabel,
    sourceOrigin: org.source_origin || run?.source_origin || 'direct',
  };
}

function formatHistoryTime(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    const p = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  } catch {
    return String(iso);
  }
}

function groupDirectRunsHierarchy(runs, filters = {}) {
  const list = Array.isArray(runs) ? runs : [];
  const enriched = list.map(run => ({
    run,
    org: resolveRunOrganization(run),
    status: classifyDirectRunState(run),
    sortAt: run.updated_at || run.created_at || '',
  }));

  const allProjects = new Map();
  for (const item of enriched) {
    const p = item.org.project;
    if (!allProjects.has(p)) allProjects.set(p, {name: p, count: 0, tbs: new Map()});
    const pEntry = allProjects.get(p);
    pEntry.count++;
    pEntry.tbs.set(item.org.testbench, (pEntry.tbs.get(item.org.testbench) || 0) + 1);
  }

  const q = String(filters.query || '').trim().toLowerCase();
  const filtered = enriched.filter(item => {
    if (filters.project && item.org.project !== filters.project) return false;
    if (filters.testbench && item.org.testbench !== filters.testbench) return false;
    if (filters.status && item.status.bucket !== filters.status) return false;
    if (q) {
      const hay = [
        item.run.run_id,
        item.org.project,
        item.org.library,
        item.org.testbench,
        item.org.submissionLabel,
        item.org.caseLabel,
        item.run.host_id,
        item.run.simulator,
        item.run.backend,
        item.status.label,
      ].filter(Boolean).join(' ').toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  const projectMap = new Map();
  const globalSubs = new Set();
  const counts = {totalRuns: enriched.length, filteredRuns: filtered.length, active: 0, completed: 0, failed: 0, prepared: 0};
  for (const item of enriched) {
    counts[item.status.bucket] = (counts[item.status.bucket] || 0) + 1;
    globalSubs.add(`${item.org.project}::${item.org.testbench}::${item.org.submissionGroupId}`);
  }

  for (const item of filtered) {
    const pKey = item.org.project;
    if (!projectMap.has(pKey)) {
      projectMap.set(pKey, {
        project: pKey,
        library: item.org.library,
        latestAt: '',
        runCount: 0,
        submissionCount: 0,
        counts: {active: 0, completed: 0, failed: 0, prepared: 0},
        tbMap: new Map(),
      });
    }
    const pNode = projectMap.get(pKey);
    pNode.runCount++;
    pNode.counts[item.status.bucket]++;
    if (item.sortAt > pNode.latestAt) pNode.latestAt = item.sortAt;

    const tbKey = item.org.testbench;
    if (!pNode.tbMap.has(tbKey)) {
      pNode.tbMap.set(tbKey, {
        testbench: tbKey,
        library: item.org.library,
        view: item.org.view,
        latestAt: '',
        runCount: 0,
        counts: {active: 0, completed: 0, failed: 0, prepared: 0},
        subMap: new Map(),
      });
    }
    const tbNode = pNode.tbMap.get(tbKey);
    tbNode.runCount++;
    tbNode.counts[item.status.bucket]++;
    if (item.sortAt > tbNode.latestAt) tbNode.latestAt = item.sortAt;
    if (!tbNode.view && item.org.view) tbNode.view = item.org.view;

    const subKey = item.org.submissionGroupId;
    if (!tbNode.subMap.has(subKey)) {
      tbNode.subMap.set(subKey, {
        groupId: subKey,
        label: item.org.submissionLabel,
        kind: item.org.submissionKind,
        batchId: item.org.batchId,
        latestAt: '',
        createdAt: item.run.created_at || '',
        hosts: new Set(),
        simulators: new Set(),
        counts: {active: 0, completed: 0, failed: 0, prepared: 0},
        runs: [],
      });
    }
    const subNode = tbNode.subMap.get(subKey);
    subNode.runs.push(item);
    subNode.counts[item.status.bucket]++;
    if (item.sortAt > subNode.latestAt) subNode.latestAt = item.sortAt;
    if (item.run.created_at && (!subNode.createdAt || item.run.created_at < subNode.createdAt)) subNode.createdAt = item.run.created_at;
    if (item.run.host_id) subNode.hosts.add(item.run.host_id);
    if (item.run.simulator) subNode.simulators.add(item.run.simulator);
  }

  const projects = [...projectMap.values()].map(pNode => {
    const tbs = [...pNode.tbMap.values()].map(tbNode => {
      const submissions = [...tbNode.subMap.values()].map(subNode => {
        subNode.runs.sort((a, b) => (b.sortAt || '').localeCompare(a.sortAt || '') || String(a.run.run_id).localeCompare(String(b.run.run_id)));
        const resolvedKind = subNode.runs.length > 1
          ? (subNode.kind === 'single' ? 'multi_run' : subNode.kind)
          : subNode.kind;
        return {
          ...subNode,
          kind: resolvedKind,
          hosts: [...subNode.hosts],
          simulators: [...subNode.simulators],
        };
      }).sort((a, b) => (b.latestAt || '').localeCompare(a.latestAt || ''));
      pNode.submissionCount += submissions.length;
      return {...tbNode, submissions};
    }).sort((a, b) => (b.latestAt || '').localeCompare(a.latestAt || ''));
    return {...pNode, testbenches: tbs};
  }).sort((a, b) => (b.latestAt || '').localeCompare(a.latestAt || ''));

  const totalTbs = [...allProjects.values()].reduce((acc, p) => acc + p.tbs.size, 0);
  return {
    projects,
    allProjects: [...allProjects.values()].sort((a, b) => b.count - a.count || a.name.localeCompare(b.name)),
    totalProjects: allProjects.size,
    totalTestbenches: totalTbs,
    totalSubmissions: globalSubs.size,
    counts,
  };
}

function renderStatusSummaryPills(counts) {
  const pills = [];
  if (counts.active) pills.push(`<span class="ov-pill is-active">● ${counts.active} 进行中</span>`);
  if (counts.completed) pills.push(`<span class="ov-pill is-done">✓ ${counts.completed} 已完成</span>`);
  if (counts.failed) pills.push(`<span class="ov-pill is-fail">✕ ${counts.failed} 异常</span>`);
  if (counts.prepared) pills.push(`<span class="ov-pill is-prep">○ ${counts.prepared} 待启动</span>`);
  return pills.join('');
}

const escapeAttr = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

function findScopeReportMeta(scope, project, testbench = '') {
  return (directState.scopeReports || []).find(item =>
    item
    && item.scope === scope
    && String(item.project || '') === String(project || '')
    && (scope === 'project' || String(item.testbench || '') === String(testbench || ''))
  ) || null;
}

function reportDownloadFilename(st = reportModalState) {
  const safeSlug = s => String(s || '').replace(/[^A-Za-z0-9._-]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 80) || 'spiceowl';
  if (st.scope === 'run') return `${safeSlug(st.runId || 'simulation')}-report.html`;
  if (st.scope === 'testbench') return `${safeSlug(`${st.project}-${st.testbench}` || 'testbench')}-report.html`;
  return `${safeSlug(st.project || 'project')}-report.html`;
}

function buildStandaloneReportHtml(st = reportModalState) {
  const rep = st.report;
  if (!rep) return '';
  if (rep.html && String(rep.html).trim()) return String(rep.html);
  if (!rep.markdown || !String(rep.markdown).trim()) return '';
  const scopeLabel = st.scope === 'project' ? '项目总报告' : st.scope === 'testbench' ? 'TB 综合报告' : '单次仿真报告';
  const titleText = rep.title || (st.scope === 'run' ? st.runId : st.scope === 'testbench' ? `${st.project} / ${st.testbench}` : st.project) || 'SpiceOwl 仿真报告';
  const subtitle = st.scope === 'run'
    ? `Run ID: ${st.runId}${st.project ? ` · ${st.project}${st.testbench ? '/' + st.testbench : ''}` : ''}`
    : st.scope === 'testbench'
    ? `${st.project} / ${st.testbench}`
    : `${st.project}`;
  const generatedAt = rep.generated_at ? ` · 生成于 ${formatHistoryTime(rep.generated_at)}` : '';
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${escape(titleText)} · SpiceOwl</title>
<style>
  body { margin:0; padding:32px 20px; background:#f4f7f5; color:#182620; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; line-height:1.65; }
  main { max-width:960px; margin:0 auto; background:#fff; border:1px solid #d4e3db; border-radius:12px; padding:28px 32px; box-shadow:0 4px 16px rgba(18,42,32,.06); }
  .badge { display:inline-block; padding:2px 10px; border-radius:999px; background:#e6f4ec; color:#15583d; font-size:12px; font-weight:600; }
  h1 { margin:10px 0 6px; font-size:22px; color:#123c2b; }
  .meta { margin:0 0 20px; font-size:12.5px; color:#52665e; border-bottom:1px solid #e3ece7; padding-bottom:14px; }
  .sim-ai-heading { margin:18px 0 8px; font-size:16px; color:#154935; }
  code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12.5px; background:#eff4f1; padding:1px 5px; border-radius:4px; }
  pre { background:#f5f8f6; border:1px solid #d9e5df; border-radius:8px; padding:12px 14px; overflow:auto; }
  pre code { background:none; padding:0; }
  .md-table-wrap { overflow-x:auto; margin:12px 0; border:1px solid #d5e2db; border-radius:8px; }
  .md-table { width:100%; border-collapse:collapse; font-size:13px; }
  .md-table th, .md-table td { padding:8px 12px; border-bottom:1px solid #e6eee9; text-align:left; }
  .md-table thead th { background:#f2f8f5; color:#184a36; font-weight:600; }
  .md-table tbody tr:last-child td { border-bottom:0; }
</style>
</head>
<body>
<main>
  <span class="badge">${escape(scopeLabel)}</span>
  <h1>${escape(titleText)}</h1>
  <p class="meta">${escape(subtitle)}${escape(generatedAt)}</p>
  ${renderMarkdownHtml(rep.markdown)}
</main>
</body>
</html>`;
}

function renderReportViewerModalHtml() {
  if (!reportModalState.open) return '';
  const st = reportModalState;
  const rep = st.report;
  const scopeLabel = st.scope === 'project' ? '项目总报告' : st.scope === 'testbench' ? 'TB 综合报告' : '单次仿真报告';
  const defaultTitle = st.scope === 'run'
    ? (st.runId || '仿真报告')
    : st.scope === 'testbench'
    ? `${st.testbench || 'Testbench'} 综合报告`
    : `${st.project || '项目'} 总报告`;
  const modalTitle = rep?.title || defaultTitle;
  const subtitleParts = [];
  if (st.scope === 'run') {
    if (st.runId) subtitleParts.push(`Run ID: ${st.runId}`);
    if (st.project) subtitleParts.push(`${st.project}${st.testbench ? ` / ${st.testbench}` : ''}`);
  } else if (st.scope === 'testbench') {
    subtitleParts.push(`${st.project} / ${st.testbench}`);
  } else {
    subtitleParts.push(st.project);
  }
  if (Array.isArray(rep?.run_ids) && rep.run_ids.length) {
    subtitleParts.push(`汇总 ${rep.run_ids.length} 次仿真`);
  }
  if (rep?.generated_at) {
    subtitleParts.push(`更新于 ${formatHistoryTime(rep.generated_at)}`);
  }
  const hasHtml = Boolean(rep?.html && String(rep.html).trim());
  const hasMarkdown = Boolean(rep?.markdown && String(rep.markdown).trim());
  const hasDownloadable = hasHtml || hasMarkdown;
  const emptyCtaLabel = st.scope === 'project'
    ? '✨ 让 AI 总结本项目全部仿真'
    : st.scope === 'testbench'
    ? '✨ 让 AI 总结本 TB 全部仿真'
    : '✨ 立即让 AI 生成图文仿真报告';

  let bodyHtml = '';
  if (st.loading) {
    bodyHtml = `<div class="report-empty-state"><p>正在读取仿真报告…</p></div>`;
  } else if (hasHtml) {
    bodyHtml = `<iframe class="report-html-frame" title="${escapeAttr(modalTitle)}" sandbox="allow-scripts" srcdoc="${escapeAttr(rep.html)}"></iframe>`;
  } else if (hasMarkdown) {
    bodyHtml = `<div class="report-md-scroll"><div class="ov-ai-report-body">${renderMarkdownHtml(rep.markdown)}</div></div>`;
  } else {
    const emptyDesc = st.scope === 'project'
      ? `AI 将汇总项目「${st.project}」下所有 Testbench 与仿真结果，生成带图表、规格对比表与设计结论的独立交互式 HTML 总报告，并支持随时导出离线网页。`
      : st.scope === 'testbench'
      ? `AI 将汇总「${st.project} / ${st.testbench}」下的全部历史仿真与多工况对比，生成带波形图、参数指标表与诊断结论的交互式 HTML 综合报告。`
      : `AI 将结合本次仿真的真实波形、日志与测量结果，生成带交互式图表、指标对比表与工程诊断结论的 HTML 仿真报告（支持离线下载与随时按需求编辑重做）。`;
    bodyHtml = `<div class="report-empty-state">
      <h3>还没有已归档的${escape(scopeLabel)}</h3>
      <p>${escape(emptyDesc)}</p>
      <div class="direct-actions" style="justify-content:center">
        <button type="button" class="button primary" data-action="report-generate" ${st.generating ? 'disabled' : ''}>${st.generating ? '正在推送给 AI…' : emptyCtaLabel}</button>
      </div>
    </div>`;
  }

  return `<div class="report-modal-backdrop" role="dialog" aria-modal="true" aria-label="${escapeAttr(modalTitle)}">
    <div class="report-modal-dialog">
      <header class="report-modal-head">
        <div class="report-modal-title">
          <span class="report-scope-badge">${escape(scopeLabel)}</span>
          <div class="report-modal-headings">
            <h2>${escape(modalTitle)}</h2>
            <p class="report-modal-sub">${escape(subtitleParts.join(' · '))}</p>
          </div>
        </div>
        <div class="report-modal-actions">
          <button type="button" class="button secondary small" data-action="report-download-html" ${hasDownloadable ? '' : 'disabled'}>⬇ 下载 HTML</button>
          <button type="button" class="button secondary small" data-action="report-open-tab" ${hasDownloadable ? '' : 'disabled'}>↗ 新标签打开</button>
          <button type="button" class="button secondary small" data-action="report-refresh" ${st.loading ? 'disabled' : ''}>↻ 刷新</button>
          <button type="button" class="button secondary small" data-action="report-close">关闭</button>
        </div>
      </header>
      <form id="report-agent-form" class="report-agent-bar">
        <input id="report-custom-prompt" type="text" value="${escape(st.customPrompt)}" placeholder="让 AI 按你的要求修改/重做报告，例如：补充 VOP/VON 瞬态波形图、增加功耗与增益对比表…">
        <button type="submit" class="button primary small" data-action="report-generate" ${st.generating ? 'disabled' : ''}>${st.generating ? '推送中…' : '✨ 让 AI 生成/更新网页报告'}</button>
      </form>
      ${st.error ? `<p class="error-text report-modal-error" role="alert">${escape(st.error)}</p>` : ''}
      <div class="report-modal-body">${bodyHtml}</div>
    </div>
  </div>`;
}

function renderReportViewerModal() {
  const html = renderReportViewerModalHtml();
  let root = $('#report-modal-root');
  if (!root && html && typeof document.createElement === 'function' && document.body?.appendChild) {
    root = document.createElement('div');
    root.id = 'report-modal-root';
    document.body.appendChild(root);
  }
  if (root) {
    root.innerHTML = html;
  }
  return html;
}

function closeReportModal() {
  reportModalState.open = false;
  reportModalState.loading = false;
  reportModalState.generating = false;
  reportModalState.error = null;
  renderReportViewerModal();
}

async function fetchReportModalData() {
  if (isPublicDemo() || !reportModalState.open) return;
  const targetScope = reportModalState.scope;
  const targetRunId = reportModalState.runId;
  const targetProject = reportModalState.project;
  const targetTb = reportModalState.testbench;
  reportModalState.loading = true;
  reportModalState.error = null;
  renderReportViewerModal();
  try {
    let rep = null;
    if (targetScope === 'run') {
      const res = await api(`/api/direct/runs/${encodeURIComponent(targetRunId)}/ai-report`);
      rep = res?.ai_report || null;
    } else {
      const q = new URLSearchParams({scope: targetScope, project: targetProject});
      if (targetScope === 'testbench' && targetTb) q.set('testbench', targetTb);
      const res = await api(`/api/direct/scope-reports/read?${q.toString()}`);
      rep = res?.report || null;
    }
    if (!reportModalState.open || reportModalState.scope !== targetScope || reportModalState.runId !== targetRunId || reportModalState.project !== targetProject || reportModalState.testbench !== targetTb) {
      return;
    }
    reportModalState.report = (rep && (rep.html || rep.markdown || rep.summary)) ? rep : null;
  } catch (error) {
    if (!reportModalState.open) return;
    if (error?.status === 404) {
      reportModalState.report = null;
      reportModalState.error = null;
    } else {
      reportModalState.error = error.message || '读取仿真报告失败。';
    }
  } finally {
    if (reportModalState.open && reportModalState.scope === targetScope && reportModalState.runId === targetRunId && reportModalState.project === targetProject && reportModalState.testbench === targetTb) {
      reportModalState.loading = false;
      renderReportViewerModal();
    }
  }
}

async function openReportModal({scope = 'run', runId = '', project = '', testbench = ''} = {}) {
  if (isPublicDemo()) return;
  const sameTarget = reportModalState.open
    && reportModalState.scope === scope
    && reportModalState.runId === (runId || '')
    && reportModalState.project === (project || '')
    && reportModalState.testbench === (testbench || '');
  reportModalState.open = true;
  reportModalState.scope = scope;
  reportModalState.runId = runId || '';
  reportModalState.project = project || '';
  reportModalState.testbench = testbench || '';
  reportModalState.report = null;
  reportModalState.error = null;
  if (!sameTarget) reportModalState.customPrompt = '';
  await fetchReportModalData();
}

async function triggerReportModalGenerate() {
  if (isPublicDemo() || !reportModalState.open || reportModalState.generating) return;
  reportModalState.generating = true;
  reportModalState.error = null;
  renderReportViewerModal();
  try {
    const customPrompt = String(reportModalState.customPrompt || '').trim();
    let res = null;
    if (reportModalState.scope === 'run') {
      const body = {force: true};
      if (customPrompt) body.custom_prompt = customPrompt;
      res = await api(`/api/direct/runs/${encodeURIComponent(reportModalState.runId)}/ai-report`, {
        method: 'POST',
        body,
      });
    } else {
      const body = {
        scope: reportModalState.scope,
        project: reportModalState.project,
      };
      if (reportModalState.scope === 'testbench') body.testbench = reportModalState.testbench;
      if (customPrompt) body.custom_prompt = customPrompt;
      res = await api('/api/direct/scope-reports/generate', {
        method: 'POST',
        body,
      });
    }
    const pushed = Boolean(res?.handoff?.pushed ?? res?.pushed);
    if (pushed) {
      toast('✨ 已推送给 AI，正在编辑生成交互式 HTML 仿真报告…');
      if (isStandaloneAgent()) {
        if (codexPanelState.status?.connected) openCodexEvents();
        else void refreshCodexStatus();
      }
      void refreshDirect();
    } else {
      const msg = res?.handoff?.note || res?.note || '请先连接本机 Codex Agent 再生成仿真报告。';
      reportModalState.error = msg;
      toast(msg);
    }
  } catch (error) {
    reportModalState.error = error.message || '推送 AI 生成报告失败。';
    toast(reportModalState.error);
  } finally {
    reportModalState.generating = false;
    renderReportViewerModal();
  }
}

function renderOverviewRunDetailPanel(run) {
  if (!run) return '';
  const org = resolveRunOrganization(run);
  const st = classifyDirectRunState(run);
  const watchEntry = directState.monitor?.watches?.find(w => w && w.run_id === run.run_id) || null;
  const watchArmed = watchEntry?.watch_state === 'armed';
  const terminal = ['simulator_complete_pending_measurements', 'simulator_failed', 'job_setup_failed', 'input_hash_failure'].includes(run.state);
  const canStage = ['prepared', 'stage_incomplete'].includes(run.state) && !directState.busy;
  const canLaunch = run.state === 'staged' && !run.dispatch_claimed && !directState.busy;
  const canStatus = run.state !== 'prepared' && !directState.busy;
  const canWatch = run.dispatch_claimed && !terminal && !watchArmed && !directState.busy;
  const canUnwatch = watchArmed && !directState.busy;
  const canCancel = run.dispatch_claimed && !terminal && !directState.busy;
  const canCollect = terminal && run.collection_phase !== 'complete' && !directState.busy;
  const canRecover = terminal && run.state !== 'simulator_complete_pending_measurements' && !directState.busy;
  const neverStaged = !run.remote_directory && (!run.remote_state || run.remote_state === 'not_staged');
  const isActiveRemote = Boolean(run.dispatch_claimed && !terminal && !neverStaged && !['prepared', 'staged', 'stage_incomplete', 'not_staged'].includes(run.state));
  const canDelete = !isActiveRemote && !directState.busy;
  const recPolicy = run.recovery_policy || {};
  const report = directState.detail?.run_id === run.run_id ? directState.report : null;
  const aiRep = (directState.detail?.run_id === run.run_id ? directState.aiReport : null)
    || (directState.detail?.run?.run_id === run.run_id ? directState.detail?.ai_report : null)
    || (run.ai_report && typeof run.ai_report === 'object' ? run.ai_report : null);
  const hasAiReportMarkdown = Boolean(aiRep?.markdown && String(aiRep.markdown).trim());
  const hasAiReportHtml = Boolean(aiRep?.has_html || (aiRep?.html && String(aiRep.html).trim()) || run.ai_report?.has_html);
  const hasAnyAiReport = Boolean(hasAiReportMarkdown || hasAiReportHtml || run.ai_report?.available);
  const canGenerateAiReport = ['simulator_complete_pending_measurements', 'simulator_failed', 'job_setup_failed'].includes(run.state);
  const isAutoWatchActive = ['submitted', 'running_observed'].includes(run.state);
  const cpus = run.limits?.cpu_count ? `${run.limits.cpu_count} 核` : '—';
  const memBytes = run.limits?.memory_bytes;
  const memText = Number.isFinite(memBytes) && memBytes > 0 ? `${(memBytes / 2**30).toFixed(memBytes % 2**30 === 0 ? 0 : 1)} GiB` : '—';
  const autoWatchBar = isAutoWatchActive
    ? `<div class="ov-auto-watch-bar"><span class="ov-pulse-dot" aria-hidden="true">●</span><span>后台每 4 秒自动巡检远端状态 · 仿真完成后将自动收集结果并推送给 AI 生成仿真报告</span></div>`
    : '';
  const preparedHint = ['prepared', 'stage_incomplete'].includes(run.state)
    ? `<p class="ov-prepared-hint">💡 该任务目前仅在本地冻结了网表与配置，<strong>尚未上传到远端服务器，也没有占用远端算力</strong>。你可以直接点击「🚀 立即上传并启动」，或点击「✧ 载入工作台 / 交给 Agent」重新对接 AI 检查修改；如果不再需要，直接点「🗑 删除记录」清理即可。</p>`
    : '';
  let aiHandoffHint = '';
  if (!hasAiReportMarkdown && !hasAiReportHtml && canGenerateAiReport) {
    if (run.completion_handoff?.pushed === true) {
      aiHandoffHint = `<p class="ov-prepared-hint ov-ai-handoff-hint">✨ 已自动推送给 AI 正在撰写仿真报告（稍候自动显示，也可去「仿真配置与执行」对话查看）</p>`;
    } else if (run.completion_handoff?.status === 'agent_disconnected') {
      aiHandoffHint = `<p class="subtle-note ov-ai-handoff-note">提示：连接本机 Codex 后点「✨ 让 AI 生成仿真报告」即可一键生成。</p>`;
    } else if (aiRep?.summary) {
      aiHandoffHint = `<p class="ov-prepared-hint ov-ai-handoff-hint">✨ AI 仿真报告摘要：${escape(aiRep.summary)}</p>`;
    }
  }
  const aiReportCard = (hasAiReportMarkdown || hasAiReportHtml)
    ? `<section class="ov-ai-report-card">
        <div class="ov-ai-report-head">
          <div class="ov-ai-report-title">
            <strong>✨ AI 仿真报告${aiRep?.title ? ` · ${escape(aiRep.title)}` : ''}</strong>
            ${aiRep?.generated_at ? `<span class="ov-ai-report-time">${escape(formatHistoryTime(aiRep.generated_at))}</span>` : ''}
          </div>
          <div class="ov-row-actions">
            <button type="button" class="button primary small" data-action="overview-open-run-report" data-id="${escape(run.run_id)}">📊 打开交互式网页报告</button>
            <button type="button" class="button secondary small" data-action="overview-generate-ai-report" data-id="${escape(run.run_id)}" ${directState.busy ? 'disabled' : ''}>重新生成报告</button>
          </div>
        </div>
        <div class="ov-ai-report-body">${hasAiReportMarkdown ? renderMarkdownHtml(aiRep.markdown) : `<p>${escape(aiRep?.summary || '已生成交互式 HTML 仿真报告，点击上方「📊 打开交互式网页报告」即可查看图表、下载离线 HTML 或让 AI 继续编辑。')}</p>`}</div>
      </section>`
    : '';
  return `<section class="panel direct-panel ov-detail-panel" data-overview-run-detail="${escape(run.run_id)}">
    <div class="section-title">
      <div class="ov-detail-head">
        <h2>任务详情与控制 · <code>${escape(run.run_id)}</code></h2>
        ${chip(st.label, st.color)}
        <span class="ov-meta-tag">${escape(org.project)} / ${escape(org.testbench)}${org.view ? ` (${escape(org.view)})` : ''}</span>
      </div>
      <button type="button" class="button secondary small" data-action="overview-close-run">收起详情</button>
    </div>
    <p class="subtle-note">第 <strong>#${escape(run.attempt_number || 1)}</strong> 次尝试 · 主机 <strong>${escape(run.host_id)}</strong> · 后端 <strong>${escape(String(run.backend || '').toUpperCase())}</strong> · 仿真器 <strong>${escape(run.simulator)}</strong> · 资源 <strong>${cpus} / ${memText}</strong>${run.scheduler?.job_id ? ` · Job ID <code>${escape(run.scheduler.job_id)}</code>` : ''}<br>网表冻结摘要 <code>${escape((run.manifest_sha256 || '').slice(0, 16))}…</code> · 创建于 ${escape(formatHistoryTime(run.created_at))} · 最近观察 ${escape(run.observed_at ? formatHistoryTime(run.observed_at) : '尚未联网')}</p>
    ${autoWatchBar}
    ${preparedHint}
    ${aiHandoffHint}
    ${run.state === 'launch_outcome_unknown' ? '<p class="denial">提交回执未知：已锁定原 Key，禁止盲目重提。请点击「查询状态」与远端对账。</p>' : ''}
    ${run.oom_evidence?.oom_kill ? '<p class="denial">调度器已确认该作业触发 OOM（内存耗尽）。</p>' : ''}
    ${run.last_error ? `<p class="error-text">${escape(run.last_error.message || run.last_error.code)}</p>` : ''}
    <div class="direct-actions">
      ${canStage ? `<button type="button" class="button primary small" data-action="overview-launch-now" data-id="${escape(run.run_id)}" ${directState.busy ? 'disabled' : ''}>🚀 立即上传并启动</button>` : ''}
      ${canLaunch ? `<button type="button" class="button primary small" data-action="direct-launch" data-id="${escape(run.run_id)}" ${directState.busy ? 'disabled' : ''}>🚀 立即启动仿真</button>` : ''}
      ${canStatus ? `<button type="button" class="button secondary small" data-action="direct-status" data-id="${escape(run.run_id)}" ${directState.busy ? 'disabled' : ''}>↻ 查询状态 (Status)</button>` : ''}
      ${canCollect ? `<button type="button" class="button primary small" data-action="direct-collect" data-id="${escape(run.run_id)}" ${directState.busy ? 'disabled' : ''}>📥 收集报告与结果 (Collect)</button>` : ''}
      <button type="button" class="button secondary small ov-report-btn ${hasAnyAiReport ? 'has-report' : ''}" data-action="overview-open-run-report" data-id="${escape(run.run_id)}">${hasAnyAiReport ? '<span class="ov-report-dot" aria-hidden="true"></span>' : ''}📊 查看仿真报告</button>
      ${!hasAiReportMarkdown && !hasAiReportHtml && canGenerateAiReport ? `<button type="button" class="button ${canCollect ? 'secondary' : 'primary'} small" data-action="overview-generate-ai-report" data-id="${escape(run.run_id)}" ${directState.busy ? 'disabled' : ''}>✨ 让 AI 生成仿真报告</button>` : ''}
      <button type="button" class="button secondary small" data-action="overview-handoff-agent" data-id="${escape(run.run_id)}" ${directState.busy ? 'disabled' : ''}>✧ 载入工作台 / 交给 Agent</button>
      <button type="button" class="button secondary small ov-btn-danger" data-action="overview-delete-run" data-id="${escape(run.run_id)}" ${canDelete ? '' : 'disabled'} title="${canDelete ? '删除此本地归档记录' : '运行中或待对账任务请先取消/对账后再删除'}">🗑 删除记录</button>
    </div>
    ${aiReportCard}
    <details class="host-form-details">
      <summary>进阶控制：分步上传 (Stage) / 后台盯守 (Watch) / 取消作业 / 有界自动恢复</summary>
      <div class="direct-check-row">
        <label><input id="direct-recheck" type="checkbox" ${directDraft.recheck_resources ? 'checked' : ''} ${run.backend !== 'ssh_direct' ? 'disabled' : ''}> 启动前复查主机余量（仅 ssh_direct）</label>
        <label><input id="direct-allow-collect" type="checkbox" ${directDraft.allow_collect ? 'checked' : ''}> Watch 确认终态后自动收集并生成报告</label>
      </div>
      <div class="direct-actions">
        <button type="button" class="button secondary small" data-action="direct-stage" data-id="${escape(run.run_id)}" ${canStage ? '' : 'disabled'}>仅上传输入 (Stage)</button>
        <button type="button" class="button secondary small" data-action="direct-launch" data-id="${escape(run.run_id)}" ${canLaunch ? '' : 'disabled'}>仅启动 (Launch)</button>
        <button type="button" class="button secondary small" data-action="direct-watch" data-id="${escape(run.run_id)}" ${canWatch ? '' : 'disabled'}>开启后台盯守 (Watch)</button>
        <button type="button" class="button secondary small" data-action="direct-unwatch" data-id="${escape(run.run_id)}" ${canUnwatch ? '' : 'disabled'}>停止盯守</button>
        <button type="button" class="button secondary small" data-action="direct-cancel" data-id="${escape(run.run_id)}" ${canCancel ? '' : 'disabled'}>取消作业 (Cancel)</button>
        <button type="button" class="button secondary small" data-action="direct-toggle-policy" data-id="${escape(run.run_id)}" ${directState.busy ? 'disabled' : ''}>${recPolicy.enabled ? '关闭有界恢复' : '开启有界恢复'}</button>
        <button type="button" class="button primary small" data-action="direct-recover" data-id="${escape(run.run_id)}" ${canRecover ? '' : 'disabled'}>执行有界恢复 (Recover)</button>
      </div>
      ${watchEntry ? `<p class="subtle-note">后台盯守：<strong>${escape(title(watchEntry.watch_state))}</strong> · 通信：<strong>${escape(title(watchEntry.observation?.state))}</strong> · 下次轮询：${escape(watchEntry.observation?.next_check_at || '无')}</p>` : ''}
    </details>
    ${run.collection ? `<p class="subtle-note">已收集 ${run.collection.file_count} 个文件（${Math.round((run.collection.total_bytes || 0) / 1024)} KiB）${run.collection.collection_complete ? ' · 完整校验通过' : ` · ${(run.collection.skipped_files || []).length} 个超大原始文件已跳过并保留日志`}</p>` : ''}
    ${directReportSection(report)}
  </section>`;
}

function liveOverview() {
  const actions = `<div class="split-actions"><button type="button" class="button secondary" data-action="overview-refresh" ${directState.loading ? 'disabled' : ''}>${directState.loading ? '刷新中…' : '↻ 刷新记录'}</button><a class="button primary" href="#direct" data-action="overview-new-sim">＋ 新建仿真</a></div>`;
  let html = heading(
    'SIMULATION HISTORY · 仿真历史归档',
    '仿真总览与历史记录',
    '按项目（Library）与 Testbench（TB）统一管理所有历史仿真；同一次提交、同一批次或自动重试链的多个仿真自动归档到一起。',
    actions,
  );

  if (directState.loading && !directState.loaded) {
    return html + `<div class="empty"><h2>正在加载历史仿真记录…</h2><p>正在从本机连接器读取已归档的仿真与提交记录。</p></div>`;
  }
  if (directState.error && !directState.runs.length) {
    return html + `<p class="error-text" role="alert">${escape(directState.error)}</p>`;
  }

  const runs = directState.runs || [];
  if (!runs.length) {
    return html + `<div class="empty"><img src="./owl.svg" alt=""><h1>还没有历史仿真记录</h1><p>前往「仿真配置与执行」导入远端 Virtuoso schematic / 网表，或让 AI 辅助配置并启动仿真；每次提交的仿真都会自动按项目与 TB 归档在这里。</p><div class="direct-actions" style="justify-content:center"><a class="button primary" href="#direct" data-action="overview-new-sim">前往仿真配置与执行 →</a></div></div>`;
  }

  const tree = groupDirectRunsHierarchy(runs, overviewState);
  const selectedProjEntry = overviewState.project ? tree.allProjects.find(p => p.name === overviewState.project) : null;
  const tbOptionsMap = new Map();
  if (selectedProjEntry) {
    for (const [tb, cnt] of selectedProjEntry.tbs.entries()) tbOptionsMap.set(tb, cnt);
  } else {
    for (const p of tree.allProjects) {
      for (const [tb, cnt] of p.tbs.entries()) tbOptionsMap.set(tb, (tbOptionsMap.get(tb) || 0) + cnt);
    }
  }
  const tbOptions = [...tbOptionsMap.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const hasFilters = Boolean(overviewState.project || overviewState.testbench || overviewState.status || overviewState.query.trim());

  const stats = [
    ['历史仿真总数', tree.counts.totalRuns, `归档为 ${tree.totalSubmissions} 次提交`, '▦'],
    ['项目 / Testbench', `${tree.totalProjects} / ${tree.totalTestbenches}`, '按项目与 TB 分层管理', '▤'],
    ['运行中 / 待启动', `${tree.counts.active} / ${tree.counts.prepared}`, '实时状态可在任务控制刷新', '◷'],
    ['已完成 / 异常', `${tree.counts.completed} / ${tree.counts.failed}`, '含失败与待对账记录', '✓'],
  ];
  html += `<div class="stat-grid">${stats.map(([label, value, note, glyph]) => `<div class="stat-card"><div class="stat-label">${label}<span class="stat-glyph" aria-hidden="true">${glyph}</span></div><div class="stat-value">${value}</div><div class="stat-note">${note}</div></div>`).join('')}</div>`;

  const selectedRun = overviewState.selectedRunId
    ? ((directState.detail?.run_id === overviewState.selectedRunId ? directState.detail : null) || runs.find(r => r.run_id === overviewState.selectedRunId) || null)
    : null;
  if (selectedRun) {
    html += renderOverviewRunDetailPanel(selectedRun);
  }

  html += `<div class="panel ov-filter-bar">
    <select id="overview-filter-project" aria-label="按项目筛选">
      <option value="">全部项目 (${tree.totalProjects})</option>
      ${tree.allProjects.map(p => `<option value="${escape(p.name)}" ${overviewState.project === p.name ? 'selected' : ''}>${escape(p.name)} (${p.count})</option>`).join('')}
    </select>
    <select id="overview-filter-tb" aria-label="按 Testbench 筛选">
      <option value="">全部 TB (${tbOptions.length})</option>
      ${tbOptions.map(([tb, cnt]) => `<option value="${escape(tb)}" ${overviewState.testbench === tb ? 'selected' : ''}>${escape(tb)} (${cnt})</option>`).join('')}
    </select>
    <select id="overview-filter-status" aria-label="按状态筛选">
      <option value="" ${!overviewState.status ? 'selected' : ''}>全部状态</option>
      <option value="active" ${overviewState.status === 'active' ? 'selected' : ''}>运行中 / 排队 (${tree.counts.active})</option>
      <option value="completed" ${overviewState.status === 'completed' ? 'selected' : ''}>已完成 (${tree.counts.completed})</option>
      <option value="failed" ${overviewState.status === 'failed' ? 'selected' : ''}>失败 / 异常 (${tree.counts.failed})</option>
      <option value="prepared" ${overviewState.status === 'prepared' ? 'selected' : ''}>待启动 (${tree.counts.prepared})</option>
    </select>
    <input id="overview-filter-query" type="search" value="${escape(overviewState.query)}" placeholder="搜索 Run ID、项目、TB、主机或提交名称…">
    ${hasFilters ? `<button type="button" class="button secondary small" data-action="overview-clear-filters">清空筛选</button>` : ''}
  </div>`;

  if (!tree.projects.length) {
    return html + `<div class="empty"><h2>没有匹配的历史仿真记录</h2><p>请放宽筛选条件或清空关键词后再试。</p><button type="button" class="button secondary" data-action="overview-clear-filters">清空筛选</button></div>`;
  }

  const formatMem = r => {
    const bytes = r?.limits?.memory_bytes;
    return Number.isFinite(bytes) && bytes > 0 ? `${(bytes / 2**30).toFixed(bytes % 2**30 === 0 ? 0 : 1)} GiB` : '—';
  };
  const formatCollection = r => {
    if (r?.collection_phase === 'complete') {
      const files = r.collection?.file_count ?? 0;
      return `已收集 · ${files} 文件`;
    }
    if (r?.state === 'prepared') return '未上传';
    if (r?.state === 'staged') return '已上传待启动';
    return '—';
  };

  html += `<div class="ov-tree" data-live-history-archive="true">${tree.projects.map(pNode => {
    const tbHtml = pNode.testbenches.map(tbNode => {
      const subHtml = tbNode.submissions.map(sub => {
        if (sub.runs.length === 1) {
          const item = sub.runs[0];
          const r = item.run;
          const cpus = r.limits?.cpu_count ? `${r.limits.cpu_count} 核` : '—';
          return `<div class="ov-single-row" data-run-id="${escape(r.run_id)}">
            <div class="ov-single-main">
              <div class="ov-single-title">
                <code>${escape(r.run_id)}</code>
                ${chip(item.status.label, item.status.color)}
                ${r.ai_report?.available ? chip('✨ AI 报告', 'green') : ''}
                ${r.scheduler?.job_id ? `<span class="ov-meta-tag">Job ${escape(r.scheduler.job_id)}</span>` : ''}
                ${r.attempt_number > 1 ? `<span class="ov-meta-tag">第 #${r.attempt_number} 次尝试</span>` : ''}
              </div>
              <div class="ov-single-meta">
                <span>主机 <strong>${escape(r.host_id || '—')}</strong></span>
                <span>·</span>
                <span>${escape(String(r.simulator || 'spectre'))} (${escape(String(r.backend || 'ssh_direct').toUpperCase())})</span>
                <span>·</span>
                <span>${cpus} / ${formatMem(r)}</span>
                <span>·</span>
                <span>${escape(formatCollection(r))}</span>
                <span>·</span>
                <span>${escape(formatHistoryTime(r.updated_at || r.created_at))}</span>
              </div>
            </div>
            <div class="ov-row-actions">
              <button type="button" class="button secondary small ov-report-btn ${r.ai_report?.available ? 'has-report' : ''}" data-action="overview-open-run-report" data-id="${escape(r.run_id)}">${r.ai_report?.available ? '<span class="ov-report-dot" aria-hidden="true"></span>' : ''}📊 仿真报告</button>
              <button type="button" class="button secondary small" data-action="overview-open-run" data-id="${escape(r.run_id)}">查看 / 控制 →</button>
            </div>
          </div>`;
        }
        const kindBadge = sub.kind === 'batch'
          ? `批量提交 · ${sub.runs.length} 次仿真`
          : sub.kind === 'recovery_chain'
          ? `重试归档 · ${sub.runs.length} 次尝试`
          : `同次提交 · ${sub.runs.length} 次仿真`;
        const openByDefault = sub.runs.length <= 8 ? 'open' : '';
        const rowsHtml = sub.runs.map(item => {
          const r = item.run;
          const cpus = r.limits?.cpu_count ? `${r.limits.cpu_count} 核` : '—';
          const caseNote = item.org.caseLabel ? `<small class="ov-case-label">${escape(item.org.caseLabel)}</small>` : '';
          return `<tr>
            <td><code>${escape(r.run_id)}</code>${caseNote}</td>
            <td>${chip(item.status.label, item.status.color)}${r.ai_report?.available ? ` ${chip('✨ AI 报告', 'green')}` : ''}</td>
            <td>${escape(r.host_id || '—')} <small class="muted">${escape(String(r.backend || '').toUpperCase())}</small></td>
            <td>${escape(r.simulator || '—')} · ${cpus} / ${formatMem(r)}</td>
            <td>${escape(formatCollection(r))}</td>
            <td>${escape(formatHistoryTime(r.updated_at || r.created_at))}</td>
            <td><div class="ov-row-actions"><button type="button" class="button secondary small ov-report-btn ${r.ai_report?.available ? 'has-report' : ''}" data-action="overview-open-run-report" data-id="${escape(r.run_id)}">${r.ai_report?.available ? '<span class="ov-report-dot" aria-hidden="true"></span>' : ''}📊 仿真报告</button><button type="button" class="button secondary small" data-action="overview-open-run" data-id="${escape(r.run_id)}">查看 →</button></div></td>
          </tr>`;
        }).join('');
        return `<details class="ov-sub-card" data-submission-group="${escape(sub.groupId)}" ${openByDefault}>
          <summary class="ov-sub-head">
            <div class="ov-sub-title-wrap">
              <span class="ov-sub-badge">${escape(kindBadge)}</span>
              <strong>${escape(sub.label)}</strong>
              <span class="ov-sub-meta">主机 ${escape(sub.hosts.join(', ') || '—')} · ${escape(sub.simulators.join(', ') || '—')} · ${escape(formatHistoryTime(sub.latestAt || sub.createdAt))}</span>
            </div>
            <div class="ov-pill-row">${renderStatusSummaryPills(sub.counts)}</div>
          </summary>
          <div class="ov-run-table-wrap">
            <table class="ov-run-table">
              <thead><tr><th>Run ID / 工况</th><th>状态</th><th>主机 / 后端</th><th>仿真器与资源</th><th>结果归档</th><th>更新时间</th><th>操作</th></tr></thead>
              <tbody>${rowsHtml}</tbody>
            </table>
          </div>
        </details>`;
      }).join('');

      const tbRep = findScopeReportMeta('testbench', pNode.project, tbNode.testbench);
      return `<div class="ov-tb-group" data-tb-name="${escape(tbNode.testbench)}">
        <div class="ov-tb-head">
          <div class="ov-tb-title">
            <span class="ov-tb-icon" aria-hidden="true">🧪</span>
            <h3>${escape(tbNode.testbench)}</h3>
            ${tbNode.view ? `<span class="ov-meta-tag">${escape(tbNode.view)}</span>` : ''}
            <span class="subtle-note">${tbNode.submissions.length} 次提交 · ${tbNode.runCount} 次仿真</span>
          </div>
          <div class="ov-pill-row">
            ${renderStatusSummaryPills(tbNode.counts)}
            <button type="button" class="button secondary small ov-report-btn ${tbRep ? 'has-report' : ''}" data-action="overview-open-scope-report" data-scope="testbench" data-project="${escape(pNode.project)}" data-testbench="${escape(tbNode.testbench)}" title="${tbRep ? '查看或更新本 TB 综合报告' : '让 AI 总结本 TB 全部仿真'}">${tbRep ? '<span class="ov-report-dot" aria-hidden="true"></span>' : ''}📊 TB 综合报告</button>
          </div>
        </div>
        <div class="ov-sub-list">${subHtml}</div>
      </div>`;
    }).join('');

    const projRep = findScopeReportMeta('project', pNode.project);
    return `<details class="panel ov-project-card" data-project-name="${escape(pNode.project)}" open>
      <summary class="ov-project-head">
        <div class="ov-project-title">
          <span class="ov-project-icon" aria-hidden="true">📁</span>
          <h2>${escape(pNode.project)}</h2>
          <span class="ov-meta-tag">${pNode.testbenches.length} 个 TB</span>
          <span class="ov-meta-tag">${pNode.submissionCount} 次提交 · ${pNode.runCount} 次仿真</span>
        </div>
        <div class="ov-pill-row">
          ${renderStatusSummaryPills(pNode.counts)}
          <button type="button" class="button secondary small ov-report-btn ${projRep ? 'has-report' : ''}" data-action="overview-open-scope-report" data-scope="project" data-project="${escape(pNode.project)}" title="${projRep ? '查看或更新本项目总报告' : '让 AI 总结本项目全部仿真'}">${projRep ? '<span class="ov-report-dot" aria-hidden="true"></span>' : ''}📊 项目总报告</button>
        </div>
      </summary>
      <div class="ov-project-body">${tbHtml}</div>
    </details>`;
  }).join('')}</div>`;
  return html;
}

function overview() {
  if (!isPublicDemo()) return liveOverview();
  const total = state.runs.length;
  const running = state.runs.filter(r => r.execution === 'running' || r.execution === 'queued').length;
  const attention = state.runs.filter(r => r.observation !== 'current' || r.execution === 'failed' || r.specification_result === 'fail').length;
  const done = state.runs.filter(r => r.execution === 'completed').length;
  const stats = [
    ['仿真总数 · Total', total, '已归档的实验记录', '▦'],
    ['最近观测活跃 · Active', running, '请结合观测新鲜度判断', '◷'],
    ['需重点关注 · Attention', attention, '含崩溃 / 断连 / 指标超标', '◇'],
    ['执行已完成 · Completed', done, '是否达标单独评估', '✓'],
  ];
  const overviewAction = isPublicDemo()
    ? `<div class="split-actions"><a class="button primary" href="#quickstart">✦ 新手 5 步教程</a><a class="button secondary" href="#direct">▷ 体验大波形与 PVT</a></div>`
    : `<div class="split-actions"><a class="button secondary" href="#quickstart">✦ 新手向导</a><button class="button primary" data-action="seed"><span aria-hidden="true">＋</span> 加载演示数据</button></div>`;
  let html = heading('仿真总览 · WORKSPACE OVERVIEW', '一眼看清每次仿真的真实状态', '将「是否跑完」「观测是否过期」「测量是否有效」「指标是否达标」四维分开呈现，点击任意卡片即可回放与诊断。', overviewAction);
  if (!total) {
    return html + `<div class="empty"><img src="./owl.svg" alt=""><h1>从这里开始你的第一次仿真巡检</h1><p>无需账号、API 密钥或远程服务器，点击下方按钮立即载入 4 个典型演示场景，或打开 5 步网页新手教程。</p><div class="direct-actions" style="justify-content:center"><button class="button primary" data-action="seed">载入离线演示数据 <span aria-hidden="true">→</span></button><a class="button secondary" href="#quickstart">打开 5 步网页教程</a></div></div>`;
  }
  html += `<div class="stat-grid">${stats.map(([label, value, note, glyph]) => `<div class="stat-card"><div class="stat-label">${label}<span class="stat-glyph" aria-hidden="true">${glyph}</span></div><div class="stat-value">${value}</div><div class="stat-note">${note}</div></div>`).join('')}</div>`;
  html += `<div class="watch-banner"><img src="./owl.svg" alt=""><div><h3>第一次使用 SpiceOwl？推荐按这 3 步快速体验</h3><p>① 点击下方任意仿真卡片体验单步回放与 AI 诊断；② 去「仿真 · 波形 · 批量」玩 500 周期毛刺钻取和 27-Corner PVT；③ 去「AI 助手与评测」一键跑 10 项安全测试。</p></div><a class="button primary small" href="#quickstart">开始 5 步教程 →</a></div>`;
  html += `<div class="section-title"><h2>仿真运行列表 <span class="muted">/ ${total}</span></h2><span class="section-note">点击任意卡片进入详情回放与只读诊断</span></div><div class="run-grid">`;
  html += state.runs.map(run => {
    const pct = Number.isFinite(run.progress) ? Math.round(run.progress * 100) : null;
    return `<a class="run-card" href="#run/${escape(run.run_id)}"><div class="card-top"><span class="chip">SYNTHETIC / TRANSIENT</span>${runStatus(run)}</div><h3>${escape(run.label)}</h3><div class="run-meta">${escape(run.attempt_id)}</div><div class="progress-row"><span>场景进度</span><span>${pct === null ? 'Unknown' : pct + '%'}</span></div><progress class="progress" max="100" value="${pct ?? 0}" aria-label="Scenario progress"></progress><div class="run-card-footer"><span>${run.events.length} 条证据事件 · 测量：${escape(title(run.measurement_validity))}</span><strong>查看详情与诊断 <span class="arrow" aria-hidden="true">↗</span></strong></div></a>`;
  }).join('') + '</div>';
  return html;
}

function connections() {
  let html = heading('连接测试沙箱 · AI CONNECTIONS', '模拟不同 AI 连接与故障状态', '用于在浏览器内演练「正常诊断、拒权、中途断线、工具拦截」四类行为，无需真实密钥，不联系任何外部模型。', `<button class="button primary" data-action="add-connection"><span aria-hidden="true">＋</span> 新建模拟连接</button>`);
  if (!state.connections.length) {
    html += `<div class="empty"><h2>还没有模拟连接</h2><p>创建一个模拟 Agent 或模型连接，测试权限检查、流式中断与证据保护机制。</p><button class="button primary" data-action="add-connection">新建模拟连接</button></div>`;
  }
  html += `<div class="connections-list">${state.connections.map(c => {
    const status = c.status || {};
    const fields = [['传输层 Transport', status.transport || 'unchecked'], ['身份校验 Auth', status.authentication || 'unchecked'], ['模型列表 Catalog', status.catalog || 'unchecked'], ['推理权限 Inference', status.inference_access || 'unverified']];
    return `<article class="panel connection-card"><div class="connection-main"><div class="provider-icon" aria-hidden="true">${c.kind === 'agent' ? '✧' : '⌁'}</div><div class="connection-name"><h3>${escape(c.label)} ${chip(c.enabled ? 'enabled' : 'disabled', c.enabled ? 'green' : '')}</h3><p>${c.kind === 'agent' ? 'Agent 运行时' : '模型 API'} · 离线沙箱 · 演练场景：${escape(title(c.scenario))}</p></div><div class="connection-actions"><button class="button secondary small" data-action="probe" data-id="${escape(c.connection_id)}" ${c.enabled ? '' : 'disabled'}>测试元数据 (Probe)</button><button class="button secondary small" data-action="toggle" data-id="${escape(c.connection_id)}">${c.enabled ? '停用' : '启用'}</button><button class="button secondary small" data-action="remove" data-id="${escape(c.connection_id)}" aria-label="Remove ${escape(c.label)}">删除</button></div></div><div class="connection-readiness">${fields.map(([name, value]) => `<div class="readiness-field"><span>${name}</span><strong>${escape(title(value))}</strong></div>`).join('')}</div><div class="connection-bottom"><span>全部状态来自本地离线模拟，无真实外部调用。</span><span>Revision ${c.revision} · 零凭据</span></div></article>`;
  }).join('')}</div>`;
  if (isPublicDemo()) return html + `<div class="planned-note"><strong>提示：这页用来做什么？</strong><p>你可以在这里新建「中途断线」或「越权拦截」模拟连接，然后回到 <a href="#overview">仿真总览</a> 点开任意场景，在右侧诊断框选中该连接，就能亲眼看到 SpiceOwl 如何拦截危险操作并保留原请求。也可前往 <a href="#agents">AI 助手与评测</a> 或 <a href="#local">本地运行</a>。</p></div>`;
  return html + `<div class="planned-note"><strong>关于真实 AI 助手</strong><p>本页连接卡片仅用于离线沙箱演练。要连接本机真实 Codex 对话或查看 CircuitGit 嵌入通道，请前往 <a href="#agents">AI 助手与评测</a>。</p></div>`;
}

function formatBytesHuman(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return '—';
  const tib = 1024 ** 4, gib = 1024 ** 3, mib = 1024 ** 2;
  if (bytes >= tib) return `${(bytes / tib).toFixed(2)} TiB`;
  if (bytes >= gib) return `${(bytes / gib).toFixed(1)} GiB`;
  if (bytes >= mib) return `${(bytes / mib).toFixed(0)} MiB`;
  return `${Math.round(bytes / 1024)} KiB`;
}

function formatCpuWithCores(pct) {
  if (!Number.isFinite(pct)) return '—';
  if (pct >= 150) return `${pct.toFixed(0)}% (${(pct / 100).toFixed(1)}核)`;
  return `${pct.toFixed(1)}%`;
}

function recomputeHostLiveSummary() {
  const entries = state.hosts.map(h => hostLiveState.byId.get(h.host_id)).filter(Boolean);
  const reachable = entries.filter(e => e.ok);
  const cpuVals = reachable.map(e => e.resources?.cpu_busy_percent).filter(v => Number.isFinite(v));
  hostLiveState.summary = {
    total_hosts: state.hosts.length,
    reachable_hosts: reachable.length,
    total_cpus: reachable.reduce((acc, e) => acc + (Number(e.resources?.cpu_count) || 0), 0),
    avg_cpu_busy_percent: cpuVals.length ? Number((cpuVals.reduce((a, b) => a + b, 0) / cpuVals.length).toFixed(1)) : null,
    total_memory_bytes: reachable.reduce((acc, e) => acc + (Number(e.resources?.memory_total_bytes) || 0), 0),
    available_memory_bytes: reachable.reduce((acc, e) => acc + (Number(e.resources?.memory_available_bytes) || 0), 0),
    active_simulations: reachable.reduce((acc, e) => acc + (Number(e.simulations?.active_count) || 0), 0),
    my_active_simulations: reachable.reduce((acc, e) => acc + (Number(e.simulations?.my_active_count) || 0), 0),
    my_active_eda_tools: reachable.reduce((acc, e) => acc + (Number(e.simulations?.my_eda_tool_count) || 0), 0),
    active_eda_tools: reachable.reduce((acc, e) => acc + (Number(e.simulations?.eda_tool_count) || 0), 0),
    managed_active_runs: entries.reduce((acc, e) => acc + (e.managed_runs || []).filter(r => r.active).length, 0),
  };
  const mySims = [];
  for (const e of entries) {
    for (const mr of (e.managed_runs || [])) {
      if (mr.active) {
        mySims.push({
          source: 'managed',
          host_id: e.host_id,
          run_id: mr.run_id,
          tool: mr.simulator || 'spectre',
          state: mr.state || 'running',
          cpu_percent: null,
          rss_bytes: null,
          elapsed: '',
        });
      }
    }
    for (const p of (e.simulations?.processes || [])) {
      if (p.is_me && p.category === 'simulation') {
        mySims.push({
          source: 'remote',
          host_id: e.host_id,
          pid: p.pid,
          tool: p.tool,
          state: 'running',
          cpu_percent: p.cpu_percent,
          rss_bytes: p.rss_bytes,
          elapsed: p.elapsed,
        });
      }
    }
  }
  mySims.sort((a, b) => (a.source === 'managed' ? 0 : 1) - (b.source === 'managed' ? 0 : 1) || (b.cpu_percent || 0) - (a.cpu_percent || 0));
  hostLiveState.mySimulations = mySims;
}

async function refreshHostLiveStatus({hostIds = null, silent = false} = {}) {
  if (isPublicDemo() || hostLiveState.loading) return null;
  if (!hostIds && !state.hosts.length) {
    hostLiveState.loaded = true;
    hostLiveState.loadedAt = Date.now();
    hostLiveState.hostSignature = '';
    hostLiveState.byId = new Map();
    hostLiveState.summary = null;
    hostLiveState.mySimulations = [];
    hostLiveState.licenses = null;
    hostLiveState.error = null;
    return null;
  }
  hostLiveState.loading = true;
  hostLiveState.error = null;
  if (location.hash === '#hosts') render();
  try {
    const data = await api('/api/hosts/live-status', {
      method: 'POST',
      body: hostIds ? {host_ids: hostIds} : {},
    });
    if (!hostIds) {
      hostLiveState.byId = new Map();
    }
    for (const item of (data.hosts || [])) {
      hostLiveState.byId.set(item.host_id, item);
    }
    if (data.licenses) {
      hostLiveState.licenses = data.licenses;
    }
    if (Array.isArray(data.registry_hosts)) {
      state.hosts = data.registry_hosts;
      if ($('#host-count')) $('#host-count').textContent = state.hosts.length;
    }
    hostLiveState.observedAt = data.observed_at || new Date().toISOString();
    hostLiveState.loaded = true;
    hostLiveState.loadedAt = Date.now();
    hostLiveState.hostSignature = state.hosts.map(h => h.host_id).sort().join(',');
    recomputeHostLiveSummary();
    if (!silent) {
      const okCnt = (data.hosts || []).filter(h => h.ok).length;
      toast(`已刷新 ${okCnt}/${(data.hosts || []).length} 台服务器的实时资源、仿真与 License 状态。`);
    }
    return data;
  } catch (error) {
    hostLiveState.error = error.message;
    hostLiveState.loaded = true;
    hostLiveState.loadedAt = Date.now();
    hostLiveState.hostSignature = state.hosts.map(h => h.host_id).sort().join(',');
    if (!silent) toast(error.message);
    return null;
  } finally {
    hostLiveState.loading = false;
    if (location.hash === '#hosts') {
      render();
    } else if (location.hash === '#direct' && !['codex-message', 'direct-netlist'].includes(document.activeElement?.id || '')) {
      render();
    }
  }
}

function renderMySimulationsStrip() {
  const mySims = hostLiveState.mySimulations || [];
  if (!mySims.length) return '';

  const hostSet = new Set(mySims.map(m => m.host_id));
  const totalCores = mySims.reduce((acc, m) => acc + (Number.isFinite(m.cpu_percent) ? m.cpu_percent / 100 : 0), 0);
  const totalRss = mySims.reduce((acc, m) => acc + (Number.isFinite(m.rss_bytes) ? m.rss_bytes : 0), 0);

  const chips = mySims.map(item => {
    if (item.source === 'managed') {
      return `<div class="pool-my-sim-chip"><strong>${escape(item.host_id)}</strong><code>${escape(item.tool)}</code><span>${escape(item.run_id)}</span>${chip(item.state, 'blue')}<button type="button" class="button secondary small" data-action="host-open-run" data-id="${escape(item.run_id)}">盯守 →</button></div>`;
    }
    return `<div class="pool-my-sim-chip"><strong>${escape(item.host_id)}</strong><code>${escape(item.tool)}</code><small>${escape(formatCpuWithCores(item.cpu_percent))} · ${escape(formatBytesHuman(item.rss_bytes))} · ${escape(item.elapsed)}</small></div>`;
  }).join('');

  return `
    <section class="pool-my-sims-strip active">
      <div class="pool-my-sims-head">
        <h2>👤 我的运行中仿真 <span class="pool-my-sims-badge">${mySims.length} 个 · 分布于 ${hostSet.size} 台服务器</span></h2>
        <div class="pool-bar-legend">
          <span><i class="pool-legend-dot me"></i> 紫色条 = 我的仿真占用（共 ${totalCores.toFixed(1)} 核 · ${escape(formatBytesHuman(totalRss))}）</span>
          <span><i class="pool-legend-dot other"></i> 其他颜色 = 服务器其余占用</span>
        </div>
      </div>
      <div class="pool-my-sims-list">${chips}</div>
    </section>`;
}

function renderLicenseBar() {
  const lic = hostLiveState.licenses;
  if (!lic && hostLiveState.loading) {
    return `<section class="pool-license-bar"><div class="pool-license-head"><strong>🔑 SPICE 仿真 License 余量</strong><span class="subtle-note">正在自动检测 FlexLM SPICE License 服务…</span></div></section>`;
  }
  if (!lic) return '';
  if (!lic.available) {
    const msg = lic.status === 'no_lmstat'
      ? '未在远端 PATH 中检测到 FlexLM lmstat（如使用开源 ngspice 无需商业 License）'
      : lic.status === 'no_specs'
      ? '未在远端环境或 rc 中发现 License 服务器配置'
      : '未能连上 FlexLM License 服务器（稍后点击刷新重试）';
    return `<section class="pool-license-bar"><div class="pool-license-head"><strong>🔑 SPICE 仿真 License 余量</strong><span class="subtle-note">${escape(msg)}</span></div></section>`;
  }

  const pillClass = f => (f.used_percent >= 90 ? 'high' : f.used_percent >= 70 ? 'warn' : '');
  const renderPill = f => {
    const tip = (f.servers || []).map(sv => `${sv.spec}: ${sv.in_use}/${sv.issued}`).join(' | ');
    return `<div class="pool-lic-pill ${pillClass(f)}" title="${escape(tip || f.feature)}"><strong>${escape(f.label)}</strong><span class="lic-count">${f.in_use}/${f.issued}</span><span class="lic-avail">余 ${f.available}</span></div>`;
  };
  const primary = lic.features || [];
  const shownKeys = new Set(primary.map(f => f.label));
  const extraActive = (lic.all_active_features || []).filter(f => !shownKeys.has(f.label));

  return `
    <section class="pool-license-bar">
      <div class="pool-license-head">
        <strong>🔑 SPICE 仿真 License 余量</strong>
        <span class="subtle-note">已检测 ${lic.servers_responsive} 台 License 服务 · 格式：已用/总数 · 剩余可用（悬停看分服明细）</span>
      </div>
      <div class="pool-lic-grid">${primary.map(renderPill).join('') || '<span class="subtle-note">当前未检测到 SPICE 仿真相关 License 特征</span>'}</div>
      ${extraActive.length ? `<details class="home-advanced"><summary>展开其余 ${extraActive.length} 项在用 EDA License（Virtuoso / 数字后端等）</summary><div class="pool-lic-grid" style="margin-top:8px">${extraActive.map(renderPill).join('')}</div></details>` : ''}
    </section>`;
}

function renderHostLiveCard(host) {
  const live = hostLiveState.byId.get(host.host_id) || null;
  const fallbackRes = host.last_resources?.resources || {};
  const res = live ? (live.resources || {}) : fallbackRes;
  const cpuPct = Number.isFinite(res.cpu_busy_percent) ? Math.max(0, Math.min(100, res.cpu_busy_percent)) : null;
  const cpuCount = Number.isFinite(res.cpu_count) ? res.cpu_count : null;
  const load1 = Number.isFinite(res.load_1m) ? res.load_1m.toFixed(2) : '—';
  const load5 = Number.isFinite(res.load_5m) ? res.load_5m.toFixed(2) : '—';
  const memTotal = Number.isFinite(res.memory_total_bytes) ? res.memory_total_bytes : null;
  const memAvail = Number.isFinite(res.memory_available_bytes) ? res.memory_available_bytes : null;
  const memUsed = (memTotal !== null && memAvail !== null && memTotal >= memAvail) ? (memTotal - memAvail) : null;
  const memPct = (memUsed !== null && memTotal > 0) ? Math.max(0, Math.min(100, (memUsed / memTotal) * 100)) : null;
  const diskAvail = Number.isFinite(res.home_disk_available_bytes) ? res.home_disk_available_bytes : null;

  const myCpuPct = Number.isFinite(res.my_sim_cpu_percent) ? Math.max(0, Math.min(cpuPct ?? 100, res.my_sim_cpu_percent)) : 0;
  const myCpuCores = Number.isFinite(res.my_sim_cpu_cores) ? res.my_sim_cpu_cores : 0;
  const otherCpuPct = cpuPct !== null ? Math.max(0, cpuPct - myCpuPct) : 0;

  const myMemBytes = Number.isFinite(res.my_sim_memory_bytes) ? res.my_sim_memory_bytes : 0;
  const myMemPct = Number.isFinite(res.my_sim_memory_percent) ? Math.max(0, Math.min(memPct ?? 100, res.my_sim_memory_percent)) : 0;
  const otherMemPct = memPct !== null ? Math.max(0, memPct - myMemPct) : 0;

  const sims = live?.simulations || null;
  const simCount = sims ? (sims.active_count || 0) : 0;
  const mySimCount = sims ? (sims.my_active_count || 0) : 0;
  const edaToolCount = sims ? (sims.eda_tool_count || 0) : 0;
  const processes = sims?.processes || [];
  const managedRuns = live?.managed_runs || [];
  const activeManagedRuns = managedRuns.filter(r => r.active);
  const hasMySim = mySimCount > 0 || activeManagedRuns.length > 0;

  const level = live ? live.load_level : (hostLiveState.loading ? 'moderate' : 'idle');
  const levelBadge = !live && hostLiveState.loading
    ? chip('采样中…', 'blue')
    : !live
    ? chip('待采样', '')
    : !live.ok
    ? chip('连接异常', 'amber')
    : level === 'busy'
    ? chip('高负载', 'red')
    : level === 'moderate'
    ? chip(simCount > 0 ? '仿真运行中' : '中等负载', 'blue')
    : chip(simCount > 0 ? '较空闲' : '空闲推荐', 'green');

  const barClass = pct => (pct === null ? 'ok' : pct >= 80 ? 'high' : pct >= 55 ? 'warn' : 'ok');

  const toolTags = sims ? Object.entries(sims.by_tool || {}).map(([tool, cnt]) => {
    const isSim = processes.some(p => p.tool === tool && p.category === 'simulation');
    return `<span class="pool-tool-tag ${isSim ? 'sim' : 'eda'}">${escape(tool)} ×${cnt}</span>`;
  }).join('') : '';

  const renderProcRow = p => `<tr class="${p.is_me ? 'is-me' : ''}"><td><strong>${escape(p.tool)}</strong></td><td>${escape(p.user)}${p.is_me ? '<span class="pool-me-pill">我</span>' : ''}</td><td>${escape(formatCpuWithCores(p.cpu_percent))}</td><td>${escape(formatBytesHuman(p.rss_bytes))}</td><td>${escape(p.elapsed)}</td></tr>`;

  const mySimProcs = processes.filter(p => p.is_me && p.category === 'simulation');
  const otherProcs = processes.filter(p => !(p.is_me && p.category === 'simulation'));
  const otherSimCount = Math.max(0, simCount - mySimCount);

  let simsBody = '';
  if (!live && hostLiveState.loading) {
    simsBody = '<p class="subtle-note">正在实时读取 CPU、内存与仿真进程…</p>';
  } else if (!live) {
    simsBody = '<p class="subtle-note">点击右上角「刷新」读取该服务器实时资源与正在运行的仿真。</p>';
  } else if (!live.ok) {
    simsBody = `<p class="error-text">${escape(live.error?.message || '无法读取该服务器的实时状态。')}</p>`;
  } else {
    const parts = [];
    if (activeManagedRuns.length) {
      parts.push(`<div class="pool-managed-list">${activeManagedRuns.map(r => `<div class="pool-managed-item"><span><strong>${escape(r.run_id)}</strong> · ${escape(r.simulator)} (${escape(r.backend)})</span><div>${chip(r.state, 'blue')} <button type="button" class="button secondary small" data-action="host-open-run" data-id="${escape(r.run_id)}">盯守 →</button></div></div>`).join('')}</div>`);
    }
    if (mySimProcs.length) {
      parts.push(`<div class="pool-proc-wrap"><table class="pool-proc-table"><thead><tr><th>我的仿真</th><th>用户</th><th>CPU</th><th>内存</th><th>时长</th></tr></thead><tbody>${mySimProcs.map(renderProcRow).join('')}</tbody></table></div>`);
    }
    if (otherProcs.length) {
      const otherLabel = otherSimCount > 0
        ? `查看其他用户仿真与 EDA 进程（${otherSimCount} 个仿真 · 共 ${otherProcs.length} 个进程）`
        : `查看本机其他 EDA 工具进程（${otherProcs.length} 个）`;
      parts.push(`<details class="home-advanced"><summary>${escape(otherLabel)}</summary>${toolTags ? `<div class="pool-tool-tags" style="margin-top:8px">${toolTags}</div>` : ''}<div class="pool-proc-wrap" style="margin-top:8px"><table class="pool-proc-table"><thead><tr><th>进程</th><th>用户</th><th>CPU</th><th>内存</th><th>时长</th></tr></thead><tbody>${otherProcs.map(renderProcRow).join('')}</tbody></table></div></details>`);
    } else if (!hasMySim) {
      parts.push('<div class="pool-sim-idle">✓ 当前空闲，可直接提交仿真</div>');
    }
    simsBody = parts.join('');
  }

  const mySimHeadHtml = live && live.ok && hasMySim
    ? `<div class="pool-sims-head"><strong>我的仿真运行中：${mySimCount + activeManagedRuns.length} 个（占 ${escape(formatCpuWithCores(sims?.my_sim_cpu_percent || 0))} · ${escape(formatBytesHuman(myMemBytes))}）</strong></div>`
    : '';

  const obsStamp = live?.observed_at || host.last_resources?.observed_at || host.last_probe?.observed_at || null;
  const obsTime = obsStamp && Number.isFinite(Date.parse(obsStamp)) ? new Date(obsStamp).toLocaleTimeString() : '尚未采样';

  const myCpuNote = myCpuPct > 0 || myCpuCores > 0
    ? ` <span class="pool-me-stat">· 我占 ${myCpuPct.toFixed(1)}% (${myCpuCores.toFixed(1)}核)</span>`
    : '';
  const myMemNote = myMemBytes > 0
    ? ` <span class="pool-me-stat">· 我占 ${escape(formatBytesHuman(myMemBytes))}</span>`
    : '';

  return `
    <article class="pool-live-card level-${escape(level)} ${hasMySim ? 'has-my-sim' : ''}">
      <div class="pool-live-head">
        <div class="pool-live-title">
          <h3>${escape(host.host_id)} ${levelBadge} ${hasMySim ? `<span class="pool-my-sims-badge">👤 我的仿真 ×${mySimCount + activeManagedRuns.length}</span>` : ''}</h3>
          <small>${escape(host.label)}</small>
        </div>
        <button type="button" class="button secondary small" data-action="host-live-refresh-one" data-id="${escape(host.host_id)}" ${hostLiveState.loading ? 'disabled' : ''}>刷新</button>
      </div>
      <div class="pool-meters">
        <div class="pool-meter">
          <div class="pool-meter-top">
            <strong>CPU 占用</strong>
            <span>${cpuPct !== null ? `${cpuPct.toFixed(1)}%` : '—'}${cpuCount !== null ? ` · ${cpuCount} 核` : ''}${myCpuNote} · Load ${load1} / ${load5}</span>
          </div>
          <div class="pool-bar" title="${myCpuPct > 0 ? `紫色：我的仿真 ${myCpuPct.toFixed(1)}% | 其余占用 ${otherCpuPct.toFixed(1)}%` : ''}">${myCpuPct > 0 ? `<i class="seg-me" style="width:${myCpuPct.toFixed(1)}%"></i>` : ''}<i class="${barClass(cpuPct)}" style="width:${otherCpuPct.toFixed(1)}%"></i></div>
        </div>
        <div class="pool-meter">
          <div class="pool-meter-top">
            <strong>内存占用</strong>
            <span>${memUsed !== null && memTotal !== null ? `${formatBytesHuman(memUsed)} / ${formatBytesHuman(memTotal)}（可用 ${formatBytesHuman(memAvail)}）` : '—'}${myMemNote}</span>
          </div>
          <div class="pool-bar" title="${myMemPct > 0 ? `紫色：我的仿真 ${myMemPct.toFixed(1)}% | 其余占用 ${otherMemPct.toFixed(1)}%` : ''}">${myMemPct > 0 ? `<i class="seg-me" style="width:${myMemPct.toFixed(1)}%"></i>` : ''}<i class="${barClass(memPct)}" style="width:${otherMemPct.toFixed(1)}%"></i></div>
        </div>
        <div class="pool-disk-row">
          <span>主目录可用磁盘</span>
          <code>${diskAvail !== null ? formatBytesHuman(diskAvail) : '—'}</code>
        </div>
      </div>
      <div class="pool-sims-box">
        ${mySimHeadHtml}
        ${simsBody}
      </div>
      <div class="pool-live-foot">
        <span>最近采样：${escape(obsTime)}</span>
        <button type="button" class="button secondary small" data-action="direct-use-placement" data-id="${escape(host.host_id)}">在此机新建仿真 →</button>
      </div>
    </article>`;
}

function hosts() {
  if (isPublicDemo()) return publicHostPage();
  syncHostSelection();
  let html = heading(
    'SERVER POOL & LIVE SIMULATIONS',
    '服务器池资源与运行中仿真',
    '实时展示已连接服务器的 CPU、内存、磁盘占用与 SPICE License 余量。',
    `<div class="host-page-actions"><button type="button" class="button secondary" data-action="host-live-refresh" ${hostLiveState.loading || !state.hosts.length ? 'disabled' : ''}>${hostLiveState.loading ? '采样中…' : '刷新资源'}</button><button type="button" class="button secondary" data-action="open-ssh-setup-wizard">＋ 管理服务器 ↗</button><button type="button" class="button primary" data-action="hosts-next-sim" ${!state.hosts.length ? 'disabled' : ''}>${directState.runs.length ? '下一步：配置仿真 →' : '下一步：运行第一个仿真 →'}</button></div>`
  );

  if (hostLiveState.error) {
    html += `<p class="error-text" role="alert">${escape(hostLiveState.error)}</p>`;
  }

  if (!state.hosts.length) {
    html += `<div class="empty"><h2>尚未连接任何 EDA 服务器</h2><p>请先在「首页与连接」中完成服务器配置与连接，连上的服务器会自动在这里展示实时资源占用与运行中的仿真。</p><div class="direct-actions" style="justify-content:center"><a class="button primary" href="#quickstart">返回首页与连接 →</a><button type="button" class="button secondary" data-action="open-ssh-setup-wizard">＋ 打开 SSH 配置向导 ↗</button></div></div>`;
  } else {
    const s = hostLiveState.summary;
    const totalMemGiB = s && s.total_memory_bytes ? (s.total_memory_bytes / 2 ** 30).toFixed(0) : '—';
    const availMemGiB = s && s.available_memory_bytes ? (s.available_memory_bytes / 2 ** 30).toFixed(0) : '—';
    html += `
      <div class="pool-kpi-grid">
        <div class="pool-kpi-card">
          <span>已连接服务器</span>
          <strong>${state.hosts.length} 台</strong>
          <small>${s ? `${s.reachable_hosts} 台在线已采样` : (hostLiveState.loading ? '正在并发采样…' : '准备采样')}</small>
        </div>
        <div class="pool-kpi-card">
          <span>集群总核数 / 平均 CPU</span>
          <strong>${s && s.total_cpus ? `${s.total_cpus} 核` : '—'}</strong>
          <small>${s && Number.isFinite(s.avg_cpu_busy_percent) ? `平均 CPU 占用 ${s.avg_cpu_busy_percent}%` : '等待实时采样'}</small>
        </div>
        <div class="pool-kpi-card">
          <span>集群可用 / 总内存</span>
          <strong>${s && s.total_memory_bytes ? `${availMemGiB} / ${totalMemGiB} GiB` : '—'}</strong>
          <small>${s && s.total_memory_bytes ? `整体内存占用 ${(((s.total_memory_bytes - s.available_memory_bytes) / s.total_memory_bytes) * 100).toFixed(1)}%` : '等待实时采样'}</small>
        </div>
        <div class="pool-kpi-card">
          <span>当前运行中仿真</span>
          <strong>${s ? `${s.active_simulations} 个` : '—'}</strong>
          <small>${s ? `${s.my_active_simulations ? `我的 ${s.my_active_simulations} 个 · ` : ''}${s.active_eda_tools} 个 EDA 工具 · ${s.managed_active_runs} 个托管任务` : '统计 Spectre / ngspice 等'}</small>
        </div>
      </div>`;

    html += renderMySimulationsStrip();
    html += renderLicenseBar();

    // Automatically pin servers running the current user's simulations to the very front!
    const sortedHosts = state.hosts
      .map((host, idx) => {
        const live = hostLiveState.byId.get(host.host_id);
        const mySims = (live?.simulations?.my_active_count || 0) + (live?.managed_runs || []).filter(r => r.active).length;
        const myCpu = live?.simulations?.my_sim_cpu_percent || 0;
        return {host, idx, mySims, myCpu};
      })
      .sort((a, b) =>
        (b.mySims > 0 ? 1 : 0) - (a.mySims > 0 ? 1 : 0)
        || b.mySims - a.mySims
        || b.myCpu - a.myCpu
        || a.idx - b.idx
      )
      .map(item => item.host);

    html += `<div class="pool-live-grid">${sortedHosts.map(renderHostLiveCard).join('')}</div>`;
  }

  return html;
}

function metric(value, suffix = '', digits = 1) {
  return Number.isFinite(value) ? `${escape(value.toFixed(digits))}${suffix}` : 'Unknown';
}

function syncHostSelection() {
  // A newly registered host must not silently expand an established selection.
  if (selectedHostIds === null && state.hosts.length) selectedHostIds = new Set(state.hosts.length <= 32 ? state.hosts.map(host => host.host_id) : []);
  if (selectedHostIds !== null) {
    const registered = new Set(state.hosts.map(host => host.host_id));
    selectedHostIds = new Set([...selectedHostIds].filter(id => registered.has(id)));
  }
}

function hostSelectionLabel() {
  const count = selectedHostIds?.size || 0;
  return `${count} of ${state.hosts.length} hosts selected · maximum 32 per check or plan${count > 32 ? ' · Reduce the selection to continue' : ''}`;
}

function selectedPoolIds() {
  syncHostSelection();
  const ids = state.hosts.filter(host => selectedHostIds?.has(host.host_id)).map(host => host.host_id);
  if (!ids.length || ids.length > 32) throw new Error('Select 1–32 hosts in the table before checking resources or preparing a placement plan.');
  return ids;
}

function hostPlanScope() {
  return `Placement scope: ${selectedHostIds?.size || 0} selected host(s). Choose 1–32 rows above. An existing native run keeps its original host even when that host is outside this selection.`;
}

function invalidateHostPlans() {
  hostSelectionRevision++;
  placement = null; executionPlan = null;
  const result = $('.placement-result');
  if (result) result.innerHTML = '<p class="subtle-note">Selection changed. Evaluate the selected hosts again to prepare a new recommendation or plan.</p>';
  $('.pool-placement .plan-summary')?.remove();
}

function updateHostSelectionUI() {
  syncHostSelection();
  const count = selectedHostIds?.size || 0, label = $('#host-selection-count');
  if (label) label.textContent = hostSelectionLabel();
  document.querySelectorAll('[data-host-select]').forEach(input => {
    input.checked = selectedHostIds?.has(input.dataset.hostSelect) || false;
    input.disabled = poolScanning;
  });
  const scan = $('[data-action="pool-scan"]');
  if (scan) scan.disabled = poolScanning || !count || count > 32;
  const scope = $('#placement-scope');
  if (scope) scope.textContent = hostPlanScope();
  document.querySelectorAll('#placement-form [type="submit"], #execution-plan-form [type="submit"]').forEach(button => {
    button.disabled = planningBusy || poolScanning || !count || count > 32;
  });
  const all = $('[data-action="host-select-all"]'), none = $('[data-action="host-select-none"]');
  if (all) all.disabled = poolScanning || !state.hosts.length || state.hosts.length > 32;
  if (none) none.disabled = poolScanning || !count;
}

function observationTime(observation) {
  if (!observation) return 'Never checked';
  const time = Date.parse(observation.observed_at);
  return Number.isFinite(time) ? new Date(time).toLocaleString() : 'Timestamp unavailable';
}

function connectionEvidence(host) {
  const observations = [host.last_probe, host.last_resources].filter(Boolean);
  const now = Date.now();
  const valid = observation => Number.isFinite(Date.parse(observation.observed_at)) && Date.parse(observation.observed_at) <= now;
  return observations.sort((a, b) => Number(valid(b)) - Number(valid(a)) || (valid(a) && valid(b) ? Date.parse(b.observed_at) - Date.parse(a.observed_at) : 0))[0] || null;
}

function connectionStatus(observation) {
  if (!observation) return {label: 'Never checked', color: '', help: ''};
  const stamp = Date.parse(observation.observed_at);
  if (!Number.isFinite(stamp) || stamp > Date.now()) return {label: 'Timestamp invalid', color: 'amber', help: 'This observation has an invalid or future timestamp. Check the clock and obtain a new connection observation.'};
  const guidance = {
    ssh_host_key_unverified: ['Trust pending', 'Verify the server fingerprint with your administrator or another trusted source, establish SSH trust, then check again. Your login key is a separate identity.'],
    ssh_host_key_changed: ['Host key changed', 'Ask the administrator to verify the changed server identity before updating its trust record.'],
    ssh_permission_denied: ['Authentication denied', 'Check the SSH alias, remote account and authorized login key, including the jump host.'],
    ssh_config_invalid: ['SSH configuration error', 'Review the selected alias and the SSH configuration on the computer running SpiceOwl.'],
    ssh_name_resolution_failed: ['Name resolution failed', 'Check the target and jump-host names and the required VPN or internal DNS.'],
    ssh_network_unreachable: ['Route unavailable', 'Check the network, VPN and jump-host route. The failing hop is not yet identified.'],
    ssh_connection_refused: ['Connection refused', 'Check the SSH service and port on the target and jump hosts.'],
    ssh_connection_timeout: ['Connection timed out', 'Check network access, VPN and the jump-host route, then retry the bounded check.'],
    ssh_connection_lost: ['Connection lost', 'The SSH connection closed before the check completed. Simulation state remains unknown.'],
    probe_timeout: ['Check timed out', 'The bounded check did not finish. Review the route and server responsiveness before checking again.'],
    identity_mismatch: ['Identity mismatch', 'The returned hostname or username differs from the registered identity. Review the target mapping before proceeding.'],
    identity_unverified: ['Identity unverified', 'Verify the connection and registered hostname and username before reading resources.'],
  };
  if (observation.error) {
    const [label, help] = guidance[observation.error.code] || ['Check incomplete', observation.error.message || 'Inspect the saved connection error.'];
    return {label, help, color: observation.error.code === 'ssh_host_key_changed' || observation.error.code === 'identity_mismatch' ? 'red' : 'amber'};
  }
  if (observation.synthetic !== false || observation.transport !== 'reachable' || observation.identity?.verified !== true) return {label: 'Identity unverified', color: 'amber', help: 'A verified remote identity has not been established by this observation.'};
  return {label: 'Verified at last check', color: 'green', help: ''};
}

function resourceStatus(sample) {
  if (!sample) return {label: 'No resource check', color: ''};
  if (sample.error) return {label: 'Resource check failed', color: 'amber'};
  if (sample.synthetic !== false || sample.transport !== 'reachable' || sample.identity?.verified !== true) return {label: 'Resource identity unverified', color: 'amber'};
  const age = (Date.now() - Date.parse(sample.observed_at)) / 1000;
  if (!Number.isFinite(age) || age < 0) return {label: 'Timestamp invalid', color: 'amber'};
  if (age > 120) return {label: 'Resource data stale', color: 'amber'};
  const keys = ['cpu_count', 'cpu_busy_percent', 'load_1m', 'load_5m', 'memory_total_bytes', 'memory_available_bytes', 'home_disk_available_bytes'];
  if (keys.some(key => !Number.isFinite(sample.resources?.[key]))) return {label: 'Resource data incomplete', color: 'amber'};
  return {label: 'Recent resource sample', color: 'green'};
}

function poolOverview() {
  const rows = state.hosts.map(host => {
    const sample = host.last_resources, r = sample?.resources || {};
    const evidence = connectionEvidence(host), connection = connectionStatus(evidence), resources = resourceStatus(sample);
    return `<tr><td class="host-select-cell"><input type="checkbox" data-host-select="${escape(host.host_id)}" aria-label="Select ${escape(host.label)} for pool check" ${selectedHostIds?.has(host.host_id) ? 'checked' : ''} ${poolScanning ? 'disabled' : ''}></td><th scope="row">${escape(host.label)}<small>${escape(host.host_id)}</small></th><td class="host-connection-cell">${chip(connection.label, connection.color)}<small>Connection: ${escape(observationTime(evidence))}</small>${connection.help ? `<details class="host-next-step"><summary>Next step</summary><p>${escape(connection.help)}</p></details>` : ''}<button class="button secondary small" data-action="host-probe" data-id="${escape(host.host_id)}">Check connection</button></td><td>${metric(r.cpu_busy_percent, '%')}<small>${metric(r.cpu_count, ' CPUs', 0)}</small></td><td>${metric(r.load_1m)} / ${metric(r.load_5m)}</td><td>${metric(Number.isFinite(r.memory_available_bytes) ? r.memory_available_bytes / 2**30 : null, ' GiB')}<small>of ${metric(Number.isFinite(r.memory_total_bytes) ? r.memory_total_bytes / 2**30 : null, ' GiB')}</small></td><td data-resource-status="${escape(host.host_id)}">${chip(resources.label, resources.color)}<small>Resources: ${escape(observationTime(sample))}</small>${sample?.error ? `<small>${escape(sample.error.message)}</small>` : ''}</td></tr>`;
  }).join('');
  let html = `<section class="panel"><div class="panel-head"><h2>Connections and resources</h2><span class="section-note">Two hosts checked at a time · explicit checks</span></div><p class="host-table-note">Listed hosts are registered candidates. Resource values are saved samples and expire after two minutes; unknown values do not mean idle. No automatic remote polling.</p><div class="host-selection-bar"><div><strong id="host-selection-count" role="status">${escape(hostSelectionLabel())}</strong><small>${state.hosts.length > 32 ? 'Choose the hosts for this check using the row checkboxes. No partial pool is chosen automatically.' : 'Only checked rows will be contacted. New registrations are not added to your existing selection.'}</small></div><div><button class="button secondary small" data-action="host-select-all" ${poolScanning || !state.hosts.length || state.hosts.length > 32 ? 'disabled' : ''}>Select all</button><button class="button secondary small" data-action="host-select-none" ${poolScanning || !selectedHostIds?.size ? 'disabled' : ''}>Clear selection</button></div></div><div class="pool-table-wrap"><table class="pool-table"><thead><tr><th scope="col">Select</th><th>Host</th><th>Connection evidence</th><th>CPU busy</th><th>Load 1m / 5m</th><th>Memory available</th><th>Resource evidence</th></tr></thead><tbody>${rows || '<tr><td colspan="7">No registered hosts.</td></tr>'}</tbody></table></div></section>`;
  html += `<section class="panel pool-placement"><div class="panel-head"><h2>Plan a new execution</h2><span class="chip">NO RESERVATION</span></div><div class="panel-body"><p id="placement-scope" class="subtle-note">${escape(hostPlanScope())}</p><form id="placement-form" class="placement-form"><div><label for="pool-cpus">Requested CPUs</label><input id="pool-cpus" name="cpus" type="number" min="1" max="4096" value="${requestedCpus}" required></div><div><label for="pool-memory">Memory (GiB)</label><input id="pool-memory" name="memory" type="number" min="0.001" step="any" max="1048576" value="${requestedMemory}" required></div><div><label for="pool-simulator">Simulator</label><select id="pool-simulator" name="simulator"><option value="spectre" ${requestedSimulator === 'spectre' ? 'selected' : ''}>Spectre</option><option value="ngspice" ${requestedSimulator === 'ngspice' ? 'selected' : ''}>ngspice</option></select></div><button class="button secondary" type="submit">Evaluate saved data</button></form><div class="placement-result">${placement ? `<strong>${placement.recommended_host_id ? 'Proposed host: ' + escape(placement.recommended_host_id) : 'No eligible host in this snapshot'}</strong><p class="subtle-note">Evaluation: ${escape(new Date(placement.evaluated_at).toLocaleString())} · ${placement.candidates.length} candidate(s). Resource observations expire; this does not reserve the host.</p>${placement.recommended_host_id ? `<div class="direct-handoff"><button class="button primary small" type="button" data-action="direct-use-placement" data-id="${escape(placement.recommended_host_id)}">用推荐主机创建直连仿真 →</button></div>` : ''}<ul>${placement.excluded.map(h => `<li>${escape(h.label || h.host_id)}: ${escape(h.reasons.join('; '))}</li>`).join('')}</ul>` : '<p class="subtle-note">Set requirements, select 1–32 hosts above, then evaluate their saved observations. Unknown nodes are excluded.</p>'}</div><form id="execution-plan-form" class="execution-plan-form"><label for="execution-kind">Simulation origin</label><select id="execution-kind" name="kind"><option value="standalone">New standalone exported netlist</option><option value="virtuoso_managed">Existing Virtuoso-managed run</option></select><label for="execution-ref">Experiment input reference / native run reference</label><input id="execution-ref" name="reference" maxlength="96" placeholder="experiment-rc-check" required><label for="execution-host">Existing native host (Virtuoso only)</label><select id="execution-host" name="host">${state.hosts.map(h => `<option value="${escape(h.host_id)}">${escape(h.label)}</option>`).join('')}</select><button class="button secondary" type="submit">Prepare reviewable plan</button></form>${executionPlan ? planSummary(executionPlan) : ''}</div></section>`;
  return html;
}

function codexThread(id) {
  if (!id || typeof id !== 'string') return null;
  let thread = codexPanelState.threads.find(item => item.id === id);
  if (!thread) {
    thread = {id, items: [], truncated: false};
    codexPanelState.threads.push(thread);
    if (codexPanelState.threads.length > 20) { codexPanelState.threads.shift(); codexPanelState.historyTruncated = true; }
  }
  return thread;
}

function codexItem(thread, id, type, updates = {}) {
  if (!thread) return null;
  let item = thread.items.find(row => row.id === id);
  if (!item) {
    item = {id, type, text: '', status: 'running'};
    thread.items.push(item);
    if (thread.items.length > 200) { thread.items.shift(); thread.truncated = true; }
  }
  Object.assign(item, updates);
  return item;
}

function codexText(value, limit = 32768) {
  const text = typeof value === 'string' ? value : JSON.stringify(value ?? null, null, 2);
  return text.length > limit ? text.slice(0, limit) + '\n[Display truncated]' : text;
}

function codexActiveTurn() {
  return ['starting', 'running', 'awaiting_approval'].includes(codexPanelState.status?.state);
}

function codexCanSend() {
  const status = codexPanelState.status;
  return isStandaloneAgent() && status?.connected === true && status.thread_id
    && status.thread_id === codexPanelState.selectedThread && !codexActiveTurn()
    && codexPanelState.streamState === 'connected'
    && !status.recovery_required && !codexPanelState.pending && !codexPanelState.busy;
}

function loadCodexPending() {
  if (codexPanelState.pendingLoaded || !isStandaloneAgent()) return;
  codexPanelState.pendingLoaded = true;
  try {
    const raw = sessionStorage.getItem(CODEX_PENDING_KEY);
    if (!raw) return;
    const pending = JSON.parse(raw);
    if (!pending || typeof pending.thread_id !== 'string' || typeof pending.request_key !== 'string'
        || typeof pending.text !== 'string' || pending.text.length > 4000) throw new Error('Invalid pending record');
    codexPanelState.pending = pending;
    codexItem(codexThread(pending.thread_id), 'request:' + pending.request_key, 'user',
      {text: pending.text, status: 'unconfirmed', requestKey: pending.request_key});
  } catch {
    codexPanelState.error = 'The saved request record could not be read. It has not been replayed.';
    codexPanelState.pending = {request_key: 'unreadable-saved-request', thread_id: null};
  }
}

function confirmCodexPending(requestKey, turnId) {
  const pending = codexPanelState.pending;
  if (!pending || pending.request_key !== requestKey || !turnId) return;
  try { sessionStorage.removeItem(CODEX_PENDING_KEY); } catch { return; }
  const item = codexThread(pending.thread_id)?.items.find(row => row.requestKey === requestKey);
  if (item) item.status = 'accepted';
  codexPanelState.pending = null;
}

function setCodexStatus(status) {
  if (!status || typeof status !== 'object') throw new Error('The Agent status response is incomplete.');
  if (status.mode && status.mode !== 'standalone') {
    state.agent_channel = {...state.agent_channel, mode: status.mode};
    closeCodexEvents();
  }
  codexPanelState.status = status;
  codexPanelState.statusCursor = Number.isSafeInteger(status.cursor) ? status.cursor : codexPanelState.statusCursor;
  codexPanelState.loaded = true;
  if (status.thread_id) {
    codexThread(status.thread_id);
    if (!codexPanelState.selectedThread) codexPanelState.selectedThread = status.thread_id;
  }
  const liveApprovalIds = new Set();
  for (const approval of status.pending_approvals || []) {
    if (approval && typeof approval.approval_id === 'string') {
      liveApprovalIds.add(approval.approval_id);
      codexPanelState.approvals.set(approval.approval_id, approval);
    }
  }
  for (const [aid, row] of codexPanelState.approvals.entries()) {
    if (!liveApprovalIds.has(aid) && (!status.thread_id || row.thread_id === status.thread_id)
        && ['pending', 'resolving'].includes(row.status || 'pending')) {
      Object.assign(row, {status: 'resolved', can_approve: false, can_deny: false});
    }
  }
  if (!status.recovery_required) confirmCodexPending(status.request_key, status.turn_id);
}

function closeCodexEvents() {
  codexPanelState.stream?.close();
  codexPanelState.stream = null;
  codexPanelState.streamState = 'idle';
  if (codexPanelState.turnTimer) {
    if (typeof clearInterval === 'function') clearInterval(codexPanelState.turnTimer);
    codexPanelState.turnTimer = null;
  }
}

function openCodexEvents() {
  if (!isStandaloneAgent() || !['#agents', '#direct', '#overview'].includes(location.hash) || codexPanelState.stream
      || (!codexPanelState.status?.connected && (!codexPanelState.status?.cursor
        || codexPanelState.cursor >= codexPanelState.status.cursor))) return;
  const sseBase = isConnectorLive() ? connectorState.baseUrl : '';
  const sseToken = isConnectorLive() ? `&token=${encodeURIComponent(connectorState.token)}` : '';
  const stream = new EventSource(`${sseBase}/api/codex/events?after=${codexPanelState.cursor}${sseToken}`);
  codexPanelState.stream = stream;
  codexPanelState.streamState = 'connecting';
  stream.addEventListener('bridge', event => {
    if (codexPanelState.stream !== stream) return;
    try { receiveCodexEvent(JSON.parse(event.data)); }
    catch { codexPanelState.error = 'An Agent event could not be read. Saved requests have not been replayed.'; }
    if (!codexPanelState.status?.connected && codexPanelState.cursor >= codexPanelState.statusCursor) closeCodexEvents();
    renderCodexActivity();
  });
  stream.onopen = () => {
    if (codexPanelState.stream === stream) { codexPanelState.streamState = 'connected'; renderCodexActivity(); }
  };
  stream.onerror = () => {
    if (codexPanelState.stream !== stream) return;
    // EventSource reconnects only this GET with its Last-Event-ID. It never
    // resends a message, approval, thread creation or connection request.
    codexPanelState.streamState = 'reconnecting';
    renderCodexActivity();
  };
}

function formatTurnSeconds(ms) {
  if (!Number.isFinite(ms) || ms < 0) return '';
  return `${(ms / 1000).toFixed(1)} 秒`;
}

function friendlyCodexToolName(name) {
  const map = {
    spiceowl_library_browse: '查找/浏览远端电路库',
    spiceowl_library_open: '打开网表 / 导出原理图',
    spiceowl_ui_configure: '更新网页仿真配置',
    spiceowl_list_hosts: '读取服务器列表',
    spiceowl_recommend_hosts: '评估最优服务器',
    spiceowl_direct_read: '读取仿真记录与波形',
    spiceowl_direct_control: '执行仿真控制',
    spiceowl_inspect_agent_source: '解析电路网表',
    spiceowl_netlist_requirements: '分析器件与模型依赖',
    spiceowl_diagnose_spectre: '诊断仿真日志',
    commandExecution: '运行终端命令',
    fileChange: '修改本机文件',
    shell_tool: '运行终端命令',
    apply_patch: '编辑文件',
  };
  return map[name] || name || '工具';
}

function syncCodexTurnTimer() {
  const isLive = () => {
    const statusObj = codexPanelState.status || {};
    const brokenRecovery = Boolean(statusObj.recovery_required || statusObj.state === 'unknown');
    return !brokenRecovery && !codexPanelState.error && (codexActiveTurn() || codexPanelState.busy || Boolean(codexPanelState.pending));
  };
  if (isLive()) {
    if (!codexPanelState.turnStartedAt) codexPanelState.turnStartedAt = Date.now();
    if (!codexPanelState.turnTimer && typeof setInterval === 'function' && typeof clearInterval === 'function') {
      codexPanelState.turnTimer = setInterval(() => {
        const el = $('#sim-ai-elapsed');
        if (el && codexPanelState.turnStartedAt) {
          el.textContent = formatTurnSeconds(Date.now() - codexPanelState.turnStartedAt);
        }
        if (!isLive()) {
          if (typeof clearInterval === 'function') clearInterval(codexPanelState.turnTimer);
          codexPanelState.turnTimer = null;
        }
      }, 250);
    }
  } else if (codexPanelState.turnTimer) {
    if (typeof clearInterval === 'function') clearInterval(codexPanelState.turnTimer);
    codexPanelState.turnTimer = null;
  }
}

function applyAgentUiPatch(patch) {
  if (!patch || typeof patch !== 'object') return false;
  const rev = Number(patch.revision) || 0;
  if (rev && rev <= codexPanelState.uiPatchRevision) return false;
  if (rev) codexPanelState.uiPatchRevision = rev;
  const kind = patch.kind;
  const data = patch.payload && typeof patch.payload === 'object' ? patch.payload : {};
  if (kind === 'library_open') {
    const res = data.payload && typeof data.payload === 'object' ? data.payload : {};
    const hostId = data.host_id || '';
    const mode = data.mode || 'netlist';
    const fileName = res.file_name || (res.cell_name ? `${res.cell_name}.scs` : 'input.scs');
    const det = detectSimulatorFromNetlist(res.text || '', fileName);
    simSourceState.origin = 'ssh';
    simSourceState.sshHostId = hostId;
    simSourceState.sshTarget = res.ssh_target || hostId;
    simSourceState.remotePath = res.remote_path || '';
    simSourceState.remoteDir = res.remote_dir || '';
    simSourceState.fileName = fileName;
    simSourceState.entryPath = fileName;
    simSourceState.supportFiles = (res.support_files && typeof res.support_files === 'object') ? {...res.support_files} : {};
    simSourceState.skipped = Array.isArray(res.skipped) ? res.skipped : [];
    simSourceState.sourceCommit = '';
    if (mode === 'schematic') {
      simSourceState.fromSchematic = true;
      simSourceState.libraryName = res.lib_name || '';
      simSourceState.cellName = res.cell_name || '';
      simSourceState.viewId = res.view_name || 'schematic';
      simSourceState.projectName = res.cell_name || fileName.replace(/\.[^.]+$/, '');
      simSourceState.maestroApplied = Boolean(res.maestro_applied);
      simSourceState.maestroConfig = res.maestro_config && typeof res.maestro_config === 'object' ? res.maestro_config : null;
      simSourceState.rnlApplied = Boolean(res.rnl_applied);
      simSourceState.rnlStats = res.rnl_stats && typeof res.rnl_stats === 'object' ? res.rnl_stats : null;
    } else {
      const dirName = (res.remote_dir || '').split('/').filter(Boolean).pop() || '';
      simSourceState.fromSchematic = false;
      simSourceState.libraryName = '';
      simSourceState.cellName = '';
      simSourceState.viewId = '';
      simSourceState.projectName = dirName || fileName.replace(/\.[^.]+$/, '') || fileName;
      simSourceState.maestroApplied = false;
      simSourceState.maestroConfig = null;
      simSourceState.rnlApplied = false;
      simSourceState.rnlStats = null;
    }
    directDraft.netlist_text = res.text || '';
    directDraft.simulator = det.simulator;
    directDraft.output_format = det.output_format;
    if (hostId && (state.hosts || []).some(h => h.host_id === hostId)) {
      directDraft.target_mode = 'host';
      directDraft.host_id = hostId;
    }
    if (!directDraft.run_key) {
      const base = (simSourceState.cellName || fileName.replace(/\.[^.]+$/, '') || 'sim').replace(/[^A-Za-z0-9_-]/g, '_').slice(0, 48);
      directDraft.run_key = base || 'sim-run';
    }
    directState.activeTab = 'setup';
    if (location.hash !== '#direct') location.hash = '#direct';
    else render();
    const label = simSourceState.fromSchematic && simSourceState.cellName
      ? `${simSourceState.libraryName ? simSourceState.libraryName + '/' : ''}${simSourceState.cellName} (${simSourceState.viewId})`
      : fileName;
    toast(`✨ AI 已打开 ${label}${simSourceState.maestroApplied ? '（含 Maestro 仿真配置）' : ''}`);
    return true;
  }
  if (kind === 'ui_configure') {
    const changed = [];
    const directScalar = [
      'host_id', 'target_mode', 'simulator', 'backend', 'run_key',
      'output_format', 'remote_python', 'remote_root',
    ];
    for (const key of directScalar) {
      if (typeof data[key] === 'string') {
        directDraft[key] = data[key];
        changed.push(key);
      }
    }
    if (Number.isInteger(data.cpus) && data.cpus >= 1) { directDraft.cpus = data.cpus; changed.push('cpus'); }
    if (Number.isFinite(data.memory_gib) && data.memory_gib > 0) { directDraft.memory_gib = data.memory_gib; changed.push('memory_gib'); }
    if (Number.isInteger(data.wall_seconds) && data.wall_seconds >= 60) { directDraft.wall_seconds = data.wall_seconds; changed.push('wall_seconds'); }
    if (typeof data.recheck_resources === 'boolean') { directDraft.recheck_resources = data.recheck_resources; changed.push('recheck_resources'); }
    if (typeof data.allow_collect === 'boolean') { directDraft.allow_collect = data.allow_collect; changed.push('allow_collect'); }
    if (typeof data.netlist_text === 'string') { directDraft.netlist_text = data.netlist_text; changed.push('netlist_text'); }
    if (typeof data.append_netlist === 'string' && data.append_netlist.trim()) {
      directDraft.netlist_text = `${(directDraft.netlist_text || '').trimEnd()}\n\n${data.append_netlist.trim()}\n`;
      changed.push('netlist_text');
    }
    if (Array.isArray(data.include_dirs)) { directDraft.include_dirs = data.include_dirs.join('\n'); changed.push('include_dirs'); }
    if (Array.isArray(data.env_modules)) { directDraft.env_modules = data.env_modules.join(' '); changed.push('env_modules'); }
    if (Array.isArray(data.env_scripts)) { directDraft.env_scripts = data.env_scripts.join('\n'); changed.push('env_scripts'); }
    if (data.scheduler && typeof data.scheduler === 'object') {
      const s = data.scheduler;
      for (const k of ['queue', 'project', 'job_group', 'partition', 'account', 'qos']) {
        if (typeof s[k] === 'string') directDraft[k] = s[k];
      }
      if (typeof s.exclusive === 'boolean') directDraft.exclusive = s.exclusive;
      changed.push('scheduler');
    }
    if (data.recovery_policy && typeof data.recovery_policy === 'object') {
      const rp = data.recovery_policy;
      const map = {
        enabled: 'rec_enabled', max_attempts: 'rec_max_attempts', max_memory_gib: 'rec_max_memory_gib',
        license_backoff: 'rec_license_backoff', max_elapsed_min: 'rec_max_elapsed_min',
        retry_license: 'rec_retry_license', retry_oom: 'rec_retry_oom',
        oom_multiplier: 'rec_oom_multiplier', allow_relocate: 'rec_allow_relocate',
      };
      for (const [src, dst] of Object.entries(map)) {
        if (rp[src] !== undefined) directDraft[dst] = rp[src];
      }
      changed.push('recovery_policy');
    }
    if (data.batch && typeof data.batch === 'object') {
      const b = data.batch;
      for (const k of Object.keys(batchDraft)) {
        if (b[k] !== undefined) {
          batchDraft[k] = Array.isArray(b[k]) ? b[k].join(', ') : b[k];
        }
      }
      changed.push('batch');
    }
    if (typeof data.direct_tab === 'string') {
      const tabMap = {watch: 'recovery', monitor: 'runs'};
      let mapped = tabMap[data.direct_tab] || data.direct_tab;
      if (!isPublicDemo()) {
        if (['runs', 'waveforms', 'batch', 'batches'].includes(mapped)) {
          if (!data.page) data.page = 'overview';
          mapped = 'setup';
        }
      }
      directState.activeTab = mapped;
      changed.push('direct_tab');
    }
    if (typeof data.page === 'string' && location.hash !== `#${data.page}`) {
      location.hash = `#${data.page}`;
    } else {
      render();
    }
    toast(`✨ AI 已更新页面配置（${changed.slice(0, 5).join('、')}${changed.length > 5 ? ' 等' : ''}）`);
    return true;
  }
  if (kind === 'direct_run_submitted') {
    if (data.run_id) {
      directState.selectedId = data.run_id;
      overviewState.selectedRunId = data.run_id;
    }
    hostLiveState.loaded = false;
    void refreshDirect();
    toast(`🚀 AI 已提交并启动仿真 ${data.run_id || ''}${data.host_id ? ' @ ' + data.host_id : ''}`.trim());
    return true;
  }
  if (kind === 'direct_run_updated') {
    hostLiveState.loaded = false;
    void refreshDirect();
    if (data.ai_report_ready) {
      toast(`✨ AI 已自动生成 ${data.run_id || ''} 的仿真报告（可在「仿真总览」查看）`);
      if (reportModalState.open && reportModalState.scope === 'run' && reportModalState.runId === data.run_id) {
        void fetchReportModalData();
      }
    } else if (data.state === 'simulator_complete_pending_measurements' && data.collection_phase === 'complete') {
      toast(`✅ 仿真 ${data.run_id || ''} 已完成并自动收集结果，正推送给 AI 生成报告…`);
    }
    return true;
  }
  if (kind === 'direct_scope_report_updated') {
    void refreshDirect();
    const scopeLabel = `${data.project || ''}${data.testbench ? '/' + data.testbench : ''}`;
    toast(`✨ AI 已更新 ${scopeLabel} 的综合仿真报告`);
    if (
      reportModalState.open
      && reportModalState.scope === data.scope
      && String(reportModalState.project || '') === String(data.project || '')
      && (data.scope === 'project' || String(reportModalState.testbench || '') === String(data.testbench || ''))
    ) {
      void fetchReportModalData();
    }
    return true;
  }
  return false;
}

async function syncAgentUiPatches(targetRev = 0) {
  if (!isStandaloneAgent() || codexPanelState.uiPatchSyncing) return;
  if (targetRev && targetRev <= codexPanelState.uiPatchRevision) return;
  codexPanelState.uiPatchSyncing = true;
  try {
    const res = await api(`/api/codex/ui-patches?after=${codexPanelState.uiPatchRevision}`);
    const list = Array.isArray(res?.patches) ? res.patches : [];
    for (const p of list) applyAgentUiPatch(p);
    if (Number.isSafeInteger(res?.revision) && res.revision > codexPanelState.uiPatchRevision) {
      codexPanelState.uiPatchRevision = res.revision;
    }
  } catch { /* older connectors may not expose /ui-patches */ }
  finally {
    codexPanelState.uiPatchSyncing = false;
  }
}

function receiveCodexEvent(event) {
  if (event?.method === 'bridge/cursor_gap') {
    codexPanelState.historyTruncated = true;
    return;
  }
  if (!Number.isSafeInteger(event?.id) || event.id <= codexPanelState.cursor || typeof event.method !== 'string') return;
  codexPanelState.cursor = event.id;
  const params = event.params && typeof event.params === 'object' ? event.params : {};
  const threadId = event.thread_id || params.threadId || params.thread_id || params.thread?.id;
  const turnId = event.turn_id || params.turnId || params.turn?.id;
  const thread = codexThread(threadId);
  const liveEvent = event.id > codexPanelState.statusCursor;
  const status = codexPanelState.status || {};
  if (event.method === 'bridge/user_message' && typeof params.text === 'string') {
    const key = event.request_key || params.request_key;
    const existing = thread?.items.find(row => row.requestKey === key);
    const accepted = existing?.status === 'accepted' || status.request_key === key && !!status.turn_id && !status.recovery_required;
    codexItem(thread, 'request:' + key, 'user', {text: codexText(params.text), requestKey: key,
      status: accepted ? 'accepted' : 'unconfirmed', preview: params.preview_only === true});
  } else if (event.method === 'item/agentMessage/delta' && typeof params.delta === 'string') {
    const item = codexItem(thread, params.itemId || `assistant:${turnId}`, 'assistant');
    if (item) {
      const text = item.text + params.delta;
      item.preview = item.preview === true || params.preview_only === true || text.length > 32768;
      item.text = codexText(text);
    }
  } else if (event.method === 'item/commandExecution/outputDelta' && typeof params.delta === 'string') {
    const targetThread = thread || codexThread(status.thread_id || codexPanelState.selectedThread);
    const itemId = params.itemId || params.item_id || `cmd:${turnId}`;
    const existing = codexItem(targetThread, itemId, 'tool');
    if (existing) {
      if (!existing.name) existing.name = '终端命令';
      const combined = `${existing.output || ''}${params.delta}`;
      const truncated = combined.length > 16000;
      const bounded = truncated ? combined.slice(-16000) : combined;
      existing.output = bounded;
      existing.preview = existing.preview === true || params.preview_only === true || truncated;
      existing.text = formatCommandExecutionText(existing.cmd || '', bounded, existing.exitCode);
    }
  } else if (['item/started', 'item/completed'].includes(event.method)) {
    const item = params.item || {};
    const complete = event.method === 'item/completed';
    if (item.type === 'agentMessage') {
      const previous = codexItem(thread, item.id || `assistant:${turnId}`, 'assistant');
      codexItem(thread, item.id || `assistant:${turnId}`, 'assistant',
        {text: typeof item.text === 'string' ? codexText(item.text) : previous?.text || '',
          preview: previous?.preview === true || params.preview_only === true || item.preview_only === true
            || (typeof item.text === 'string' && item.text.length > 32768),
          status: complete ? 'completed' : 'running'});
    } else if (item.type === 'userMessage') {
      const text = (item.content || []).filter(part => part?.type === 'text').map(part => part.text || '').join('\n');
      const optimistic = thread?.items.find(row => row.requestKey && row.requestKey === event.request_key);
      codexItem(thread, optimistic?.id || item.id || `user:${turnId}`, 'user', {text: codexText(text), status: 'accepted',
        preview: optimistic?.preview === true || params.preview_only === true || text.length > 32768});
    } else if (item.id && item.type === 'commandExecution') {
      const targetThread = thread || codexThread(status.thread_id || codexPanelState.selectedThread);
      const prev = targetThread?.items.find(row => row.id === item.id);
      const cmd = formatShellCmd(item.command ?? item.cmd) || prev?.cmd || '';
      const rawOut = typeof item.aggregatedOutput === 'string'
        ? item.aggregatedOutput
        : typeof item.aggregated_output === 'string'
        ? item.aggregated_output
        : typeof item.output === 'string'
        ? item.output
        : (typeof item.stdout === 'string' || typeof item.stderr === 'string')
        ? [item.stdout, item.stderr].filter(Boolean).join('\n')
        : (prev?.output || '');
      const exitCode = Number.isFinite(item.exitCode)
        ? item.exitCode
        : Number.isFinite(item.exit_code)
        ? item.exit_code
        : prev?.exitCode;
      const outTrunc = rawOut.length > 16000;
      const boundedOut = outTrunc ? rawOut.slice(-16000) : rawOut;
      const cmdPreview = cmd ? cmd.replace(/\s+/g, ' ').slice(0, 72) : '';
      const name = cmdPreview ? `终端命令 · ${cmdPreview}${cmd.length > 72 ? '…' : ''}` : (prev?.name || '终端命令');
      const statusVal = complete
        ? (item.status || (Number.isFinite(exitCode) && exitCode !== 0 ? 'failed' : 'completed'))
        : 'running';
      codexItem(targetThread, item.id, 'tool', {
        name,
        cmd,
        output: boundedOut,
        exitCode,
        text: formatCommandExecutionText(cmd, boundedOut, exitCode),
        preview: prev?.preview === true || params.preview_only === true || item.preview_only === true || outTrunc,
        status: statusVal,
      });
    } else if (item.id && item.type === 'fileChange') {
      const targetThread = thread || codexThread(status.thread_id || codexPanelState.selectedThread);
      const changes = Array.isArray(item.changes) ? item.changes : [];
      const rawText = changes.length
        ? changes.map(c => {
            const header = [c.kind || 'edit', c.path || c.file || ''].filter(Boolean).join(' ');
            return c.diff ? `${header}\n${c.diff}` : header;
          }).join('\n\n')
        : codexText(item.diff ?? item.patch ?? item.arguments ?? item, Number.MAX_SAFE_INTEGER);
      codexItem(targetThread, item.id, 'tool', {
        name: '编辑本机文件',
        text: codexText(rawText, 8000),
        preview: params.preview_only === true || item.preview_only === true || rawText.length > 8000,
        status: complete ? item.status || 'completed' : 'running',
      });
    } else if (item.id) {
      const text = codexText(item.arguments ?? item.command ?? item, Number.MAX_SAFE_INTEGER);
      codexItem(thread, item.id, 'tool', {name: item.name || item.tool || item.type || 'Tool call',
        text: codexText(text, 8000), preview: params.preview_only === true || item.preview_only === true || text.length > 8000,
        status: complete ? item.status || 'completed' : 'running'});
    }
  } else if (event.method === 'tool/result') {
    const text = codexText(params.result, Number.MAX_SAFE_INTEGER);
    codexItem(thread, params.call_id || `tool:${event.id}`, 'tool', {name: params.tool || 'Tool call',
      text: codexText(text, 8000), preview: params.preview_only === true || text.length > 8000,
      status: params.success === true ? 'completed' : 'failed',
      ownerReview: typeof params.result?.details?.review_url === 'string'
        || typeof params.result?.error?.details?.review_url === 'string'});
    if (params.success === true && ['spiceowl_library_open', 'spiceowl_ui_configure', 'spiceowl_direct_control'].includes(params.tool)) {
      void syncAgentUiPatches(Number(params.result?.revision) || 0);
    }
  } else if (event.method === 'approval/required' && typeof params.approval_id === 'string') {
    if (liveEvent || !codexPanelState.approvals.has(params.approval_id)) {
      codexPanelState.approvals.set(params.approval_id, {...params, thread_id: params.thread_id || threadId});
    }
  } else if (event.method === 'approval/resolved') {
    const approval = codexPanelState.approvals.get(params.approval_id);
    const currentApproval = status.pending_approvals?.some(item => item.approval_id === params.approval_id);
    if (approval && (liveEvent || !currentApproval)) {
      Object.assign(approval, params, {status: params.status || 'resolved', can_approve: false, can_deny: false});
    }
  } else if (event.method === 'ui/workspace_patch') {
    const rev = Number(params.revision) || 0;
    if (rev > codexPanelState.uiPatchRevision) {
      void syncAgentUiPatches(rev);
    }
  } else if (event.method === 'bridge/interrupted') {
    const tId = params.thread_id || threadId;
    for (const approval of codexPanelState.approvals.values()) {
      if ((!tId || approval.thread_id === tId) && ['pending', 'resolving'].includes(approval.status || 'pending')) {
        Object.assign(approval, {status: 'resolved', decision: 'interrupted', can_approve: false, can_deny: false});
      }
    }
    if (params.request_key) confirmCodexPending(params.request_key, params.turn_id || 'interrupted');
  }
  if (liveEvent) {
    if (event.method === 'bridge/connected') Object.assign(status, {connected: true, state: 'connected'});
    if (event.method === 'bridge/permission_mode') {
      if (typeof params.permission_mode === 'string') status.permission_mode = params.permission_mode;
      if (params.thread_reset) {
        status.thread_id = null;
        status.turn_id = null;
        status.thread_permission_mode = null;
      }
      codexPanelState.permissionConfirmPending = false;
    }
    if (event.method === 'bridge/thread') {
      const id = threadId || params.thread_id;
      Object.assign(status, {thread_id: id, turn_id: null, state: 'ready'});
      codexThread(id); codexPanelState.selectedThread = id;
    }
    if (event.method === 'turn/started') {
      Object.assign(status, {thread_id: threadId, turn_id: turnId, request_key: event.request_key, state: 'running'});
      if (!codexPanelState.turnStartedAt) codexPanelState.turnStartedAt = Date.now();
      codexPanelState.lastTurnStatus = 'running';
      codexPanelState.lastTurnDurationMs = null;
    }
    if (event.method === 'turn/completed') {
      const tStatus = params.turn?.status || 'completed';
      const dur = codexPanelState.turnStartedAt ? Math.max(0, Date.now() - codexPanelState.turnStartedAt) : null;
      codexPanelState.turnStartedAt = null;
      codexPanelState.lastTurnDurationMs = dur;
      codexPanelState.lastTurnStatus = tStatus;
      Object.assign(status, {state: tStatus === 'completed' ? 'complete' : 'failed'});
      const lastAssistant = [...(thread?.items || [])].reverse().find(row => row.type === 'assistant');
      if (lastAssistant) {
        lastAssistant.status = tStatus === 'completed' ? 'completed' : 'failed';
        lastAssistant.turnStatus = tStatus;
        lastAssistant.durationMs = dur;
      }
    }
    if (event.method === 'bridge/interrupted') {
      const needRec = params.recovery_required === true;
      const dur = codexPanelState.turnStartedAt ? Math.max(0, Date.now() - codexPanelState.turnStartedAt) : null;
      codexPanelState.turnStartedAt = null;
      codexPanelState.lastTurnDurationMs = dur;
      codexPanelState.lastTurnStatus = 'interrupted';
      Object.assign(status, {
        state: needRec ? 'unknown' : 'interrupted',
        turn_id: null,
        request_key: needRec ? (params.request_key || status.request_key) : null,
        recovery_required: needRec,
      });
    }
    if (event.method === 'approval/required') Object.assign(status, {state: 'awaiting_approval'});
    if (event.method === 'approval/resolved' && status.state === 'awaiting_approval') {
      const unknown = params.status === 'unknown';
      const stillPending = [...codexPanelState.approvals.values()].some(
        item => item.thread_id === status.thread_id && ['pending', 'resolving'].includes(item.status || 'pending'),
      );
      Object.assign(status, {state: unknown ? 'unknown' : stillPending ? 'awaiting_approval' : 'running', recovery_required: unknown});
    }
    if (event.method === 'bridge/disconnected') {
      const unresolved = status.recovery_required || params.recovery_required || ['starting', 'running', 'awaiting_approval'].includes(status.state);
      Object.assign(status, {connected: false, state: unresolved ? 'unknown' : 'disconnected', recovery_required: !!unresolved});
      if (unresolved) for (const approval of codexPanelState.approvals.values()) {
        if (['pending', 'resolving'].includes(approval.status)) Object.assign(approval, {status: 'unknown', can_approve: false, can_deny: false});
      }
    }
    if (event.method === 'bridge/error') {
      Object.assign(status, {state: 'unknown', recovery_required: true});
      codexPanelState.error = typeof params.message === 'string' ? params.message : 'The Agent operation has an unconfirmed outcome.';
    }
    if (event.method === 'error') codexPanelState.error = typeof params.error?.message === 'string'
      ? params.error.message : 'The native Agent reported an error; its turn outcome remains separate.';
    codexPanelState.status = status;
  }
  if (event.method === 'turn/started' || event.method === 'turn/completed') {
    const userItem = thread?.items.find(row => row.requestKey === event.request_key);
    if (userItem) userItem.status = 'accepted';
    confirmCodexPending(event.request_key, turnId);
  }
  if (status.thread_id && (!codexPanelState.selectedThread || (location.hash === '#direct' && codexPanelState.selectedThread !== status.thread_id))) {
    codexPanelState.selectedThread = status.thread_id;
  } else if (!codexPanelState.selectedThread && threadId && location.hash !== '#direct') {
    codexPanelState.selectedThread = threadId;
  }
}

async function refreshCodexStatus() {
  if (!isStandaloneAgent() || codexPanelState.loading) return false;
  codexPanelState.loading = true;
  loadCodexPending();
  let ok = false;
  try {
    setCodexStatus(await api('/api/codex/status'));
    codexPanelState.error = null;
    ok = true;
    openCodexEvents();
    if (codexPanelState.status?.recovery_required && !codexPanelState.recoverySnapshot) {
      const recKey = `${codexPanelState.status.request_key || ''}:${codexPanelState.status.cursor || 0}`;
      if (codexPanelState.recoveryAutoLoadedKey !== recKey) {
        codexPanelState.recoveryAutoLoadedKey = recKey;
        try {
          codexPanelState.recoverySnapshot = await api('/api/codex/recovery');
          codexPanelState.recoveryConfirmed = false;
        } catch { /* Fallback: user can still click inspect button manually */ }
      }
    } else if (!codexPanelState.status?.recovery_required && codexPanelState.recoverySnapshot) {
      codexPanelState.recoverySnapshot = null;
      codexPanelState.recoveryConfirmed = false;
      codexPanelState.recoveryAutoLoadedKey = null;
    }
    if (codexPanelState.status?.connected && ['#direct', '#overview'].includes(location.hash)) {
      if (location.hash === '#direct' && !codexPanelState.modelsLoaded) void loadCodexCatalogAndAuth();
      void syncAgentUiPatches();
    }
  } catch (error) { codexPanelState.error = error.message; codexPanelState.loaded = true; }
  finally { codexPanelState.loading = false; if (!location.hash || ['#quickstart', '#agents', '#direct'].includes(location.hash)) render(); }
  return ok;
}

function formatDirectApprovalTitle(approval) {
  const p = approval?.params || {};
  const method = approval?.method || '';
  if (method === 'item/commandExecution/requestApproval' || p.tool === 'item/commandExecution/requestApproval') {
    const cmd = Array.isArray(p.command) ? p.command.join(' ') : (p.command || '').toString();
    return `终端命令 · ${cmd.slice(0, 80)}`;
  }
  if (method === 'item/fileChange/requestApproval' || p.tool === 'item/fileChange/requestApproval') {
    return '修改本机文件';
  }
  const tool = p.tool || method || '操作';
  const args = p.arguments && typeof p.arguments === 'object' ? p.arguments : {};
  if (tool === 'spiceowl_probe_host') return `SSH 探测主机 ${args.host_id || ''}`.trim();
  if (tool === 'spiceowl_scan_resources') {
    const ids = Array.isArray(args.host_ids) ? args.host_ids : [];
    return `扫描服务器资源（${ids.slice(0, 3).join(', ')}${ids.length > 3 ? ` 等 ${ids.length} 台` : ''}）`;
  }
  if (tool === 'spiceowl_direct_control') {
    const op = args.operation || '';
    const sub = args.arguments && typeof args.arguments === 'object' ? args.arguments : {};
    if (op === 'submit') {
      const target = sub.cell_name || sub.run_key || '当前电路';
      const host = sub.host_id || '指定主机';
      const spec = sub.simulator
        ? `（${sub.simulator}${sub.cpus ? ' · ' + sub.cpus + '核' : ''}${sub.memory_gib ? '/' + sub.memory_gib + 'GiB' : ''}）`
        : '';
      return `一键准备并启动仿真 · ${target} @ ${host}${spec}`;
    }
    if (op === 'stage_and_launch') {
      return `一键上传并启动 · ${sub.run_id || '任务'}`;
    }
    const opMap = {
      prepare: '冻结仿真计划',
      stage: '上传输入文件',
      launch: '启动远端仿真',
      status: '查询远端状态',
      collect: '收集结果与日志',
      cancel: '取消远端作业',
      delete: '删除本地记录',
    };
    const label = opMap[op] || op || '执行';
    return `仿真控制 · ${label}${sub.run_id ? ` (${sub.run_id})` : ''}`;
  }
  if (tool === 'spiceowl_live_control') return `远端仿真控制 · ${args.operation || '执行'}`;
  return tool;
}

function codexApprovalCard(approval) {
  const onDirectSetup = location.hash === '#direct';
  const current = codexPanelState.status;
  const editable = !codexPanelState.busy && current?.connected && !current.recovery_required
    && current.state === 'awaiting_approval' && codexPanelState.streamState === 'connected' && approval.status === 'pending'
    && approval.thread_id === current.thread_id && approval.thread_id === codexPanelState.selectedThread
    && !!current.turn_id && approval.turn_id === current.turn_id;
  const fullDetails = JSON.stringify(approval.params ?? null, null, 2);
  const preview = approval.preview_only === true || fullDetails.length > 65536;
  const approve = editable && approval.can_approve === true && !preview;
  const deny = editable && approval.can_deny === true;
  if (onDirectSetup) {
    const statusLabel = approval.status === 'resolving' ? '执行中…' : approval.status === 'unknown' ? '状态未知' : '待确认';
    return `<article class="codex-approval is-compact" data-approval-id="${escape(approval.approval_id)}"><div class="codex-card-heading"><strong>待人工批准 · ${escape(formatDirectApprovalTitle(approval))}</strong>${chip(statusLabel, 'amber')}</div><details${preview ? ' open' : ''}><summary>查看冻结参数</summary><pre>${escape(codexText(approval.params, 65536))}</pre></details>${preview ? `<p class="subtle-note">参数已截断，需先读取完整详情后才可批准。</p><button class="button secondary small" data-action="codex-details" data-id="${escape(approval.approval_id)}">读取完整参数</button>` : ''}<div class="codex-approval-actions"><button class="button primary small" data-action="codex-approve" data-id="${escape(approval.approval_id)}" ${approve ? '' : 'disabled'}>批准</button><button class="button secondary small" data-action="codex-deny" data-id="${escape(approval.approval_id)}" ${deny ? '' : 'disabled'}>拒绝</button></div>${approval.status === 'unknown' ? '<p class="subtle-note">审批结果未确认，不会自动重试。</p>' : ''}</article>`;
  }
  return `<article class="codex-approval" data-approval-id="${escape(approval.approval_id)}"><div class="codex-card-heading"><strong>Human approval</strong>${chip(approval.status || 'pending', 'amber')}</div><p>${escape(approval.summary || 'Review this exact requested action.')}</p><details open><summary>Requested action</summary><pre>${escape(codexText(approval.params, 65536))}</pre></details>${preview ? `<p class="subtle-note">This is an incomplete or redacted preview. Approval stays disabled until complete reviewable details are available.</p><button class="button secondary small" data-action="codex-details" data-id="${escape(approval.approval_id)}">Read frozen action details</button>` : ''}<div class="codex-approval-actions"><button class="button primary small" data-action="codex-approve" data-id="${escape(approval.approval_id)}" ${approve ? '' : 'disabled'}>Approve this action</button><button class="button secondary small" data-action="codex-deny" data-id="${escape(approval.approval_id)}" ${deny ? '' : 'disabled'}>Deny</button></div>${approval.status === 'unknown' ? '<p class="subtle-note">Approval outcome is unknown. This action will not be retried automatically.</p>' : ''}</article>`;
}

function renderCleanDirectTimeline(items) {
  const statusObj = codexPanelState.status || {};
  const brokenRecovery = Boolean(statusObj.recovery_required || statusObj.state === 'unknown');
  const active = !brokenRecovery && !codexPanelState.error && (codexActiveTurn() || codexPanelState.busy || Boolean(codexPanelState.pending));
  if (!items.length && !active) {
    return '<div class="codex-chat-empty"><strong>Agent 仿真配置已就绪</strong><p>可以直接让 AI 查找/打开远端电路库或原理图、修改右侧仿真参数，或点上方快捷指令开始。</p></div>';
  }
  const chunks = [];
  let toolBuf = [];
  const flushTools = () => {
    if (!toolBuf.length) return;
    const running = active && toolBuf.find(t => t.status === 'running');
    const interrupted = !active && toolBuf.some(t => t.status === 'running');
    const failed = interrupted || toolBuf.some(t => t.status === 'failed');
    const summaryLabel = running
      ? `正在执行 · ${friendlyCodexToolName(running.name)}…`
      : failed
      ? `执行步骤（${toolBuf.length} 步 · ${interrupted ? '已中断' : '含异常'}，点击展开）`
      : `已完成 · ${toolBuf.length} 步工具调用`;
    const stepsHtml = toolBuf.map(t => {
      const stateLabel = (t.status === 'running' && !active) ? '已中断' : t.status === 'running' ? '进行中' : t.status === 'failed' ? '失败' : '已结束';
      return `<li class="sim-ai-step"><details><summary><span>${escape(friendlyCodexToolName(t.name))}</span><small>${stateLabel}</small></summary><pre>${escape(t.text || '')}</pre></details></li>`;
    }).join('');
    chunks.push(`<details class="sim-ai-progress"><summary>${escape(summaryLabel)}</summary><ol class="sim-ai-steps">${stepsHtml}</ol></details>`);
    toolBuf = [];
  };
  let lastAssistantHadFooter = false;
  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    const isLast = i === items.length - 1;
    if (item.type === 'tool') {
      toolBuf.push(item);
      continue;
    }
    flushTools();
    if (item.type === 'user') {
      const cleanUser = stripSimAgentContextEnvelope(item.text || '');
      const uBadge = item.status === 'unconfirmed' ? ' · 发送中' : item.status === 'failed' ? ' · 未发送' : '';
      chunks.push(`<article class="sim-ai-msg is-user"><strong class="sim-ai-role">你${uBadge}</strong><div class="sim-ai-md"><p>${escape(cleanUser)}</p></div></article>`);
      lastAssistantHadFooter = false;
    } else {
      const text = item.text || '';
      const hasNetlist = Boolean(extractNetlistCodeBlock(text));
      const bodyHtml = text
        ? renderMarkdownHtml(text)
        : '<p class="subtle-note">正在生成回答…</p>';
      let footerHtml = '';
      if (!active && (item.turnStatus || (isLast && ['complete', 'interrupted', 'failed'].includes(codexPanelState.status?.state || '')))) {
        const st = item.turnStatus || (codexPanelState.status?.state === 'interrupted' ? 'interrupted' : codexPanelState.status?.state === 'failed' ? 'failed' : 'completed');
        const dur = formatTurnSeconds(item.durationMs ?? (isLast ? codexPanelState.lastTurnDurationMs : null));
        const label = st === 'interrupted' ? '⏹ 已暂停' : st === 'failed' ? '⚠ 回合异常中止' : '✓ 已完成';
        footerHtml = `<div class="sim-ai-done-meta">${label}${dur ? ` · ${dur}` : ''}</div>`;
        if (isLast) lastAssistantHadFooter = true;
      }
      chunks.push(`<article class="sim-ai-msg is-assistant"><strong class="sim-ai-role">AI</strong><div class="sim-ai-md">${bodyHtml}</div>${hasNetlist ? `<button type="button" class="button secondary small sim-apply-code-btn" data-action="direct-apply-agent-code" data-id="${escape(item.id)}">✨ 一键填入右侧网表</button>` : ''}${footerHtml}</article>`);
    }
  }
  flushTools();
  if (active) {
    const runningTool = [...items].reverse().find(t => t.type === 'tool' && t.status === 'running');
    const lastItem = items.at(-1);
    const statusLabel = codexPanelState.status?.state === 'awaiting_approval'
      ? '等待人工批准…'
      : runningTool
      ? `正在执行 · ${friendlyCodexToolName(runningTool.name)}…`
      : (lastItem?.type === 'assistant' && lastItem.text)
      ? 'AI 正在回答中…'
      : 'AI 正在思考中…';
    const elapsed = formatTurnSeconds(codexPanelState.turnStartedAt ? Date.now() - codexPanelState.turnStartedAt : 0);
    chunks.push(`<div class="sim-ai-turn-status is-thinking" role="status"><span class="sim-ai-dot" aria-hidden="true"></span><span>${escape(statusLabel)}</span><small id="sim-ai-elapsed">${escape(elapsed)}</small></div>`);
  } else if (brokenRecovery) {
    chunks.push(`<div class="sim-ai-turn-status is-paused">⚠ 上一步工具执行异常中断，请点击上方解锁按钮恢复后继续</div>`);
  } else if (items.length && !lastAssistantHadFooter) {
    const st = codexPanelState.status?.state;
    const dur = formatTurnSeconds(codexPanelState.lastTurnDurationMs);
    if (st === 'interrupted') {
      chunks.push(`<div class="sim-ai-turn-status is-paused">⏹ 已暂停${dur ? ` · ${dur}` : ''}</div>`);
    } else if (st === 'failed') {
      chunks.push(`<div class="sim-ai-turn-status is-paused">⚠ 回合异常中止</div>`);
    } else if (st === 'complete') {
      chunks.push(`<div class="sim-ai-turn-status is-done">✓ 已完成${dur ? ` · ${dur}` : ''}</div>`);
    }
  }
  return chunks.join('');
}

function renderCodexActivity() {
  const timeline = $('#codex-timeline');
  if (!timeline || !isStandaloneAgent()) return;
  const onDirectSetup = location.hash === '#direct';
  const thread = codexPanelState.threads.find(item => item.id === codexPanelState.selectedThread);
  const items = thread?.items || [];
  const nearBottom = timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight < 80;
  if (onDirectSetup) {
    timeline.innerHTML = renderCleanDirectTimeline(items);
    const guide = $('#sim-agent-guide');
    if (guide) guide.hidden = Boolean(items.length || codexPanelState.pending);
  } else {
    timeline.innerHTML = items.map(item => {
      if (item.type === 'tool') {
        return `<details class="codex-tool" open><summary><strong>${escape(item.name || 'Tool call')}</strong>${chip(item.status)}</summary>${item.preview ? '<p class="subtle-note">Retained preview · incomplete tool result. The operation status does not establish that the full result is displayed.</p>' : ''}<pre>${escape(item.text)}</pre>${item.ownerReview ? '<p class="subtle-note">This operation needs the execution owner’s human review. Open the review page supplied in the result. A Codex tool approval does not approve that separate operation.</p>' : ''}</details>`;
      }
      const displayText = item.type === 'user' ? stripSimAgentContextEnvelope(item.text || '') : (item.text || 'Waiting for response text…');
      return `<article class="codex-message codex-${item.type}"><div>${item.type === 'user' ? 'You' : 'Codex'}${item.status === 'unconfirmed' ? ' · delivery unconfirmed' : ''}</div>${item.preview ? '<p class="subtle-note">Retained preview · incomplete message.</p>' : ''}<p>${escape(displayText)}</p></article>`;
    }).join('')
      || '<div class="codex-chat-empty"><strong>Your simulation workspace, in conversation.</strong><p>Connect explicitly, create a conversation, then send a message. Tool requests and any required human approvals appear here.</p></div>';
  }
  if (nearBottom) timeline.scrollTop = timeline.scrollHeight;
  const status = codexPanelState.status;
  if ($('#native-count')) {
    $('#native-count').textContent = codexPanelState.threads.length;
    $('#native-count').title = 'Conversations received in this browser';
  }
  if ($('#codex-runtime-state')) {
    $('#codex-runtime-state').innerHTML = onDirectSetup
      ? simAgentStatusChip(status)
      : chip(status?.state || 'disconnected', status?.connected ? 'green' : '');
  }
  if ($('#codex-perm-chip')) {
    $('#codex-perm-chip').innerHTML = simAgentPermissionChip(codexPermissionMode());
  }
  if ($('#codex-permission-slot')) {
    $('#codex-permission-slot').innerHTML = renderCodexPermissionBar();
  }
  if ($('#codex-composer-hint')) {
    $('#codex-composer-hint').textContent = codexComposerHint(codexPermissionMode());
  }
  if ($('#codex-stream-state')) {
    $('#codex-stream-state').textContent = codexPanelState.streamState === 'reconnecting'
      ? 'Updates interrupted; reconnecting the read-only event stream. Messages are not resent.'
      : codexPanelState.streamState === 'connected' ? 'Live event stream' : 'No live event stream';
  }
  if ($('#codex-approvals')) {
    $('#codex-approvals').innerHTML = [...codexPanelState.approvals.values()]
      .filter(item => item.thread_id === codexPanelState.selectedThread && item.status !== 'resolved')
      .map(codexApprovalCard).join('');
  }
  const pending = codexPanelState.pending;
  const recSnap = codexPanelState.recoverySnapshot;
  const recBox = status?.recovery_required
    ? (onDirectSetup
        ? `<div class="denial"><p>上次 Agent 回合异常中断或连接器重启，未完成请求已被安全挂起（不会自动重放），归档旧会话后即可恢复对话。请求 ID：<code>${escape(status.request_key || '未返回')}</code>。</p>${recSnap ? `<p class="subtle-note">受影响请求：${recSnap.interrupted_requests.length}，未完成工具调用：${recSnap.unresolved_calls.length}（不会自动重放）。</p>${recSnap.has_possible_side_effects ? `<label class="direct-check-row" style="margin:6px 0"><input id="codex-rec-confirm" type="checkbox" ${codexPanelState.recoveryConfirmed ? 'checked' : ''}> 我已确认远端无重复任务，允许归档旧会话</label>` : ''}<div class="direct-actions"><button type="button" class="button primary small" data-action="codex-reconcile" data-reconnect="1" ${codexPanelState.busy || (recSnap.has_possible_side_effects && !codexPanelState.recoveryConfirmed) ? 'disabled' : ''}>一键归档并恢复对话</button></div>` : `<div class="direct-actions"><button type="button" class="button secondary small" data-action="codex-inspect-recovery" ${codexPanelState.busy ? 'disabled' : ''}>检查中断详情</button></div>`}</div>`
        : `<div class="denial"><p>The Agent has an unresolved operation. Preserve its conversation and request identity; new conversations and messages are blocked. Request: <code>${escape(status.request_key || 'not returned')}</code>.</p><div class="direct-actions"><button type="button" class="button secondary small" data-action="codex-inspect-recovery" ${codexPanelState.busy ? 'disabled' : ''}>检查中断详情 (Inspect Recovery)</button>${recSnap ? `<button type="button" class="button primary small" data-action="codex-reconcile" ${codexPanelState.busy || (recSnap.has_possible_side_effects && !codexPanelState.recoveryConfirmed) ? 'disabled' : ''}>归档并解除锁定 (Reconcile &amp; Unlock)</button>` : ''}</div>${recSnap ? `<p class="subtle-note">${escape(recSnap.guidance)}（受影响请求：${recSnap.interrupted_requests.length}，未完成工具调用：${recSnap.unresolved_calls.length}）</p>${recSnap.has_possible_side_effects ? `<label class="direct-check-row"><input id="codex-rec-confirm" type="checkbox" ${codexPanelState.recoveryConfirmed ? 'checked' : ''}> 我已通过 status/lookup 核对远端任务状态，确认不会盲目重放副作用</label>` : ''}` : ''}</div>`)
    : '';
  const pendingBanner = pending && !(onDirectSetup && status?.recovery_required)
    ? (onDirectSetup
        ? `<p class="denial">上次发送状态尚未确认（请求 ID：<code>${escape(pending.request_key)}</code>），不会自动重发；可点击「刷新状态」核对或解锁恢复。<button type="button" class="button secondary small" data-action="codex-refresh" style="margin-left:8px">刷新状态</button></p>`
        : `<p class="denial">A previous send is unconfirmed. Its request key is preserved: <code>${escape(pending.request_key)}</code>. Refresh status to inspect it; it will not be resent automatically.</p>`)
    : '';
  if ($('#codex-notices')) {
    $('#codex-notices').innerHTML = `${codexPanelState.error ? `<p class="error-text" role="alert">${escape(codexPanelState.error)}</p>` : ''}${pendingBanner}${recBox}${codexPanelState.historyTruncated || thread?.truncated ? '<p class="subtle-note">Only the retained event history is shown; earlier content is unavailable in this view.</p>' : ''}${thread && thread.id !== status?.thread_id ? '<p class="subtle-note">Historical conversation · read only. Select the current conversation to send a message.</p>' : ''}`;
  }
  const evalOut = $('#codex-eval-output');
  if (evalOut) {
    evalOut.innerHTML = renderAgentEvalHtml(codexPanelState.evalReport);
  }
  if ($('#codex-session')) {
    $('#codex-session').innerHTML = codexPanelState.threads.length ? codexPanelState.threads.map(item => `<option value="${escape(item.id)}" ${item.id === codexPanelState.selectedThread ? 'selected' : ''}>${item.id === status?.thread_id ? 'Current' : 'History'} · ${escape(item.id)}</option>`).join('') : '<option value="">No conversation yet</option>';
  }
  const isRunningTurn = codexActiveTurn();
  syncCodexTurnTimer();
  if ($('#codex-stop')) {
    $('#codex-stop').hidden = !(isRunningTurn || Boolean(codexPanelState.pending));
    $('#codex-stop').disabled = Boolean(codexPanelState.interrupting);
  }
  if ($('#codex-send')) {
    $('#codex-send').disabled = onDirectSetup
      ? (codexPanelState.busy || isRunningTurn || !codexPanelState.draft.trim())
      : (!codexCanSend() || !codexPanelState.draft.trim());
  }
  if ($('#codex-message')) {
    $('#codex-message').disabled = onDirectSetup ? (codexPanelState.busy || isRunningTurn) : !codexCanSend();
  }
  const threadBtn = $('[data-action="codex-thread"]');
  if (threadBtn) threadBtn.disabled = codexPanelState.busy || !status?.connected || codexPanelState.streamState !== 'connected' || !!status?.recovery_required || codexActiveTurn() || !!pending;
  const discBtn = $('[data-action="codex-disconnect"]');
  if (discBtn) discBtn.disabled = codexPanelState.busy || !status?.connected;
  const connect = $('#codex-connect-form [type="submit"]');
  if (connect) {
    connect.disabled = codexPanelState.busy || !!status?.connected || !!status?.recovery_required;
    connect.textContent = status?.connected ? 'Codex connected' : codexPanelState.busy ? 'Connecting…' : 'Connect Codex';
  }
}

function standaloneAgentPanel() {
  const status = codexPanelState.status;
  return `<div class="codex-panel" data-agent-mode="standalone"><section class="panel codex-connection"><div class="panel-head"><h2>Codex connection</h2><span class="codex-head-chips"><span id="codex-perm-chip">${simAgentPermissionChip(codexPermissionMode())}</span><span id="codex-runtime-state">${chip(status?.state || 'disconnected')}</span></span></div><div class="panel-body"><p>Connect to the Codex app-server on this computer. It uses your existing Codex authentication and configuration.</p><form id="codex-connect-form" class="codex-connect-form"><div><label for="codex-profile">Codex profile · optional</label><input id="codex-profile" maxlength="128" autocomplete="off" value="${escape(codexPanelState.profile)}" placeholder="Configured default"></div><div><label for="codex-model">Model · optional</label><input id="codex-model" maxlength="128" autocomplete="off" value="${escape(codexPanelState.model)}" placeholder="Configured default"></div><button class="button primary" type="submit" ${status?.connected ? 'disabled' : ''}>Connect Codex</button></form><p class="subtle-note">The executable comes from this server’s <code>--codex-executable /absolute/path/to/codex</code> setting or local PATH. Opening this page does not start Codex.</p><div class="codex-connection-actions"><button class="button secondary small" data-action="codex-refresh">Refresh status</button><button class="button secondary small" data-action="codex-disconnect" ${status?.connected ? '' : 'disabled'}>Disconnect Agent</button></div><p class="subtle-note">Disconnect ends the Agent connection. It does not cancel or restart a simulation.</p></div></section><section class="panel codex-chat"><div class="panel-head codex-chat-heading"><div><h2>Conversation</h2><span id="codex-stream-state" class="subtle-note">No live event stream</span></div><div class="codex-session-controls"><label class="sr-only" for="codex-session">Conversation history</label><select id="codex-session"><option>No conversation yet</option></select><button class="button secondary small" data-action="codex-thread">New conversation</button></div></div>${!isPublicDemo() ? `<div id="codex-permission-slot" class="codex-permission-slot">${renderCodexPermissionBar()}</div>` : ''}<div id="codex-notices" class="codex-notices"></div><div id="codex-timeline" class="codex-timeline" role="log" aria-live="polite" aria-label="Agent messages and tool calls"></div><div id="codex-approvals" class="codex-approvals"></div><form id="codex-message-form" class="codex-composer"><label for="codex-message">Message Codex</label><textarea id="codex-message" maxlength="4000" rows="3" placeholder="Ask about your simulation workflow…" disabled>${escape(codexPanelState.draft)}</textarea><div><p id="codex-composer-hint" class="subtle-note">${escape(codexComposerHint(codexPermissionMode()))}</p><div class="sim-ai-composer-actions"><button id="codex-stop" class="button secondary small sim-ai-stop" type="button" data-action="codex-interrupt" hidden>Stop AI</button><button id="codex-send" class="button primary" type="submit" disabled>Send message</button></div></div></form></section></div><details class="panel native-history"><summary>Agent 端到端闭环评测与确定性 Next-Action 基准（10 项冻结测试）</summary><div class="panel-body"><div class="direct-actions"><button type="button" class="button primary small" data-action="codex-run-eval" ${codexPanelState.evalBusy ? 'disabled' : ''}>${codexPanelState.evalBusy ? '评测运行中…' : '运行 10 项 Agent 闭环安全评测'}</button></div><div id="codex-eval-output" style="margin-top:10px"></div></div></details>`;
}

function nativeChat() {
  if (isPublicDemo()) return publicAgentPage();
  const history = state.questions;
  if (!selectedQa || !history.some(q => q.qa_id === selectedQa)) selectedQa = history[0]?.qa_id || null;
  qaView = history.find(q => q.qa_id === selectedQa) || null;
  let html = isStandaloneAgent()
    ? heading('STANDALONE CODEX AGENT', 'Keep the conversation beside your runs.', 'Connect explicitly to use Codex in this local console.') + standaloneAgentPanel()
    : heading('CIRCUITGIT AGENT CHANNEL', 'Continue in CircuitGit.', 'Embedded mode uses CircuitGit’s current Codex conversation.') + `<section class="panel public-info" data-agent-channel="circuitgit" data-agent-mode="embedded_in_circuitgit"><div class="panel-body"><h2>One conversation for your simulation work</h2><p>Continue in CircuitGit’s current Codex window. This embedded console does not create a second Agent connection or conversation.</p><a class="button primary" href="${circuitGitEntry}" target="_blank" rel="noopener noreferrer">Open CircuitGit</a><p class="subtle-note">CircuitGit channel status: ${escape(title(state.agent_channel?.connection_status || 'not_checked'))}. This entry link does not establish a connection.</p></div></section>`;
  html += `<details class="panel native-history codex-legacy-history" ${isStandaloneAgent() ? '' : 'open'}><summary>Legacy question history · read only (${history.length})</summary><div class="panel-body"><p class="subtle-note">Previously saved requests and answers. Queued, running and unknown states are historical snapshots; this page does not resume or retry them.</p>${history.map(q => `<button class="question-history-row" data-action="native-select" data-id="${escape(q.qa_id)}"><span>${escape(q.question)}</span>${chip('saved ' + q.status, q.status === 'completed' ? 'green' : 'amber')}</button>`).join('') || '<p class="subtle-note">No legacy questions are saved in this workspace.</p>'}<div id="native-response" class="panel-body"></div></div></details>`;
  return html;
}

function planSummary(plan) {
  return `<div class="plan-summary"><strong>${escape(title(plan.status))} · ${escape(plan.host_id || 'No host selected')}</strong><p>${escape(plan.reason)}</p><p>Origin: ${escape(title(plan.source_kind))}<br>Reference: ${escape(plan.origin?.input_ref || plan.native_run_ref || 'Unknown')}<br>Resources are not reserved. No simulation has been launched.</p><details><summary>View plan record</summary><pre class="plan-json">${escape(JSON.stringify(plan, null, 2))}</pre></details></div>`;
}

function renderNativeResponse() {
  const target = $('#native-response');
  if (!target) return;
  const q = qaView;
  const unresolved = q && ['queued', 'running', 'outcome_unknown'].includes(q.status);
  target.innerHTML = q ? `${chip('saved ' + q.status, q.status === 'completed' ? 'green' : 'amber')}<p class="response-question">${escape(q.question)}</p><p class="response-text">${escape(q.answer || 'No confirmed answer was saved.')}</p>${q.error ? `<p class="error-text">${escape(q.error.message)}</p>` : ''}<p class="subtle-note">Saved model: ${escape(q.model || 'Unknown')} · Saved provider: ${escape(q.provider || 'Unknown')}<br>${escape(q.updated_at || q.created_at)} · Legacy request history</p>${unresolved ? '<p class="denial">This saved status does not establish current execution. No native worker, automatic refresh or retry is started here.</p>' : ''}` : '<p class="subtle-note">Select a saved legacy question to inspect its response.</p>';
}

function memoryChart(run) {
  const history = run.resource_history;
  const points = history.filter(p => Number.isFinite(p.memory_mb));
  if (!points.length) return '<p class="empty-small">No memory observations yet. Advance the replay to collect the next synthetic point.</p>';
  const top = Math.max(1, ...points.map(p => p.memory_mb)) * 1.15;
  const width = 500, height = 155;
  const coords = history.map((p, i) => Number.isFinite(p.memory_mb) ? [42 + i / Math.max(1, history.length - 1) * 450, height - p.memory_mb / top * 130] : null);
  const segments = [[]];
  for (const point of coords) { if (point) segments.at(-1).push(point); else segments.push([]); }
  const lines = segments.filter(s => s.length).map(s => `<polyline class="chart-line" points="${s.map(([x,y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')}"/>`).join('');
  const missing = history.filter(p => !Number.isFinite(p.memory_mb)).length;
  return `<div class="chart-meta"><span class="chart-number">${escape(Math.round(points.at(-1).memory_mb))}<small>MB · last observed</small></span><span class="chip">SYNTHETIC</span></div><svg class="chart" viewBox="0 0 ${width} 180" role="img" aria-label="Synthetic memory: ${points.length} readings, ${missing} unavailable observations">${[0, .5, 1].map(v => `<line class="chart-grid" x1="40" y1="${height - v * 130}" x2="495" y2="${height - v * 130}"/><text x="1" y="${height - v * 130 + 3}">${Math.round(top * v)}</text>`).join('')}${lines}${coords.filter(Boolean).map(([x, y]) => `<circle cx="${x}" cy="${y}" r="3" fill="#498772"/>`).join('')}</svg><div class="chart-axis"><span>${missing ? `${missing} unavailable observation(s) · gaps preserved` : 'First observation'}</span><span>Observation ${history.length}</span></div>`;
}

function runDetail(run) {
  if (!run) return '<div class="empty"><h1>未找到该仿真记录</h1><a class="button secondary" href="#overview">返回仿真总览</a></div>';
  const states = [
    ['执行状态 · Execution', run.execution, '进程是否结束'],
    ['观测新鲜度 · Observation', run.observation, '状态是否最新'],
    ['测量有效性 · Measurement', run.measurement_validity, '提取结果是否可信'],
    ['指标是否达标 · Spec', run.specification_result, '是否满足设计指标'],
  ];
  const available = state.connections.filter(c => c.enabled);
  if (!available.some(c => c.connection_id === selectedConnection)) selectedConnection = available[0]?.connection_id || '';
  const history = state.sessions.filter(s => s.run_id === run.run_id);
  if (!history.some(s => s.session_id === selectedSession)) selectedSession = history[0]?.session_id || null;
  let html = `<a class="back-link" href="#overview">← 返回全部仿真</a>` + heading('仿真详情与单步回放 · SYNTHETIC EXPERIMENT', escape(run.label), '点击右上角「Next observation →」逐步回放事件；右侧随时可向只读诊断助手提问。', `<div class="split-actions">${runStatus(run)}<button class="button primary" data-action="advance" data-id="${escape(run.run_id)}" ${run.finished ? 'disabled' : ''}>${run.finished ? '回放已结束' : 'Next observation →'}</button></div>`);
  html += `<div class="state-grid">${states.map(([label, value, hint]) => `<div class="state-tile"><label>${label}</label><strong>${escape(title(value))}</strong><small class="state-hint">${hint}</small></div>`).join('')}</div><div class="detail-grid"><div class="detail-stack"><section class="panel"><div class="panel-head"><h2>内存观测曲线 · Memory observations</h2><span class="section-note">有限步合成回放</span></div><div class="panel-body">${memoryChart(run)}</div></section><section class="panel"><div class="panel-head"><h2>证据时间线 · Evidence timeline</h2><span class="section-note">共 ${run.events.length} 条已记录事件</span></div><div class="panel-body"><ol class="timeline">${[...run.events].reverse().map(event => `<li><div class="timeline-time">#${event.sequence} · ${escape(title(event.kind))}</div><p>${escape(event.message)}</p></li>`).join('')}</ol></div></section><section class="panel"><div class="panel-head"><h2>执行身份 · Execution identity</h2><span class="chip">PRESERVED</span></div><div class="panel-body identity">Attempt: ${escape(run.attempt_id)}<br>Submission: ${escape(run.submission_key)}<br>Owner: ${escape(JSON.stringify(run.execution_identity))}</div></section></div>`;
  html += `<section class="panel diagnosis"><div class="panel-head"><div class="diagnose-head"><img src="./owl.svg" alt=""><h2>只读诊断助手</h2></div><span class="chip">DEMO</span></div><div class="panel-body"><form id="diagnose-form"><label for="diagnostic-connection">选择模拟连接</label><select id="diagnostic-connection" ${available.length ? '' : 'disabled'}>${available.length ? available.map(c => `<option value="${escape(c.connection_id)}" ${selectedConnection === c.connection_id ? 'selected' : ''}>${escape(c.label)} · ${escape(title(c.kind))}</option>`).join('') : '<option>请先去「连接测试沙箱」添加连接</option>'}</select><label for="diagnostic-question">向助手提问（基于当前冻结证据）</label><textarea id="diagnostic-question" maxlength="2000" required>${escape(draftQuestion)}</textarea><button class="button primary full" type="submit" ${available.length && !submitting ? '' : 'disabled'}>✧ Diagnose snapshot</button></form><p class="subtle-note">离线演练回复：提问时自动冻结当前证据快照，仅给出只读建议，绝不会擅自修改或重启仿真。</p></div><div id="diagnosis-result" class="diagnosis-result"><div class="history-empty">${selectedSession ? '正在读取已保存的诊断…' : '点击上方按钮后，诊断结论与证据引用会显示在这里。'}</div></div><div class="session-picker"><label for="session-picker">历史诊断记录</label><select id="session-picker" ${history.length ? '' : 'disabled'}>${history.length ? history.map(s => `<option value="${escape(s.session_id)}" ${selectedSession === s.session_id ? 'selected' : ''}>${escape(s.connection_label)} · ${escape(new Date(s.created_at).toLocaleString())}</option>`).join('') : '<option>暂无历史记录</option>'}</select></div></section></div>`;
  return html;
}

function render() {
  routeEpoch++;
  sessionView = null;
  const page = location.hash.slice(1) || 'quickstart';
  if (!['agents', 'direct', 'overview'].includes(page) || !isStandaloneAgent()) closeCodexEvents();
  const run = currentRun();
  $('#location').textContent = isPublicDemo() && page === 'local' ? '连接器安装说明' : page === 'quickstart' ? '首页与连接' : page === 'direct' ? (isPublicDemo() ? '仿真配置与波形' : '仿真配置与执行') : page === 'live' ? 'CircuitGit 网关' : page === 'connections' ? '故障沙箱' : page === 'hosts' ? '服务器池资源' : page === 'agents' ? 'AI 助手' : run ? run.label : '仿真总览';
  document.querySelectorAll('[data-nav]').forEach(a => a.classList.toggle('active', a.dataset.nav === page || (a.dataset.nav === 'overview' && page.startsWith('run/'))));
  $('#main').innerHTML = isPublicDemo() && page === 'local' ? publicLocalPage() : page === 'quickstart' ? quickstartPage() : page === 'direct' ? directPage() : page === 'live' ? livePage() : page === 'connections' ? connections() : page === 'hosts' ? hosts() : page === 'agents' ? nativeChat() : page.startsWith('run/') ? runDetail(run) : overview();
  if (page === 'overview' && !isPublicDemo()) {
    if (!directState.loaded && !directState.loading) refreshDirect();
    if (isStandaloneAgent()) {
      if (!codexPanelState.loaded && !codexPanelState.loading) refreshCodexStatus();
      else if (codexPanelState.status?.connected) openCodexEvents();
    }
  }
  if (page === 'quickstart' && !isPublicDemo()) {
    if (!wizardState.loaded && !wizardState.loading) refreshWizard();
    if (isStandaloneAgent() && !codexPanelState.loaded && !codexPanelState.loading) refreshCodexStatus();
  }
  if (page === 'direct') {
    if (!directState.loaded && !directState.loading) refreshDirect();
    if (!isPublicDemo()) {
      const curSig = state.hosts.map(h => h.host_id).sort().join(',');
      const stale = !hostLiveState.loaded || hostLiveState.hostSignature !== curSig || (Date.now() - hostLiveState.loadedAt > 30000);
      if (state.hosts.length && !hostLiveState.loading && stale) {
        refreshHostLiveStatus({silent: true});
      }
    }
    if (!isPublicDemo() && isStandaloneAgent()) {
      renderCodexActivity();
      if (!codexPanelState.loaded && !codexPanelState.loading) refreshCodexStatus();
      else openCodexEvents();
    }
  }
  if (page === 'live' && !isPublicDemo() && !live.loaded && !live.loading) refreshLive();
  if (run && selectedSession) pollSession();
  if (page === 'hosts' && !isPublicDemo()) {
    if ($('#execution-kind')) $('#execution-kind').value = plannedKind;
    if ($('#execution-ref')) $('#execution-ref').value = plannedReference;
    if (plannedHost && $('#execution-host')) $('#execution-host').value = plannedHost;
    updateHostSelectionUI();
    const curSig = state.hosts.map(h => h.host_id).sort().join(',');
    const stale = !hostLiveState.loaded || hostLiveState.hostSignature !== curSig || (Date.now() - hostLiveState.loadedAt > 20000);
    if (state.hosts.length && !hostLiveState.loading && stale) {
      refreshHostLiveStatus({silent: true});
    }
  }
  if (page === 'agents' && !isPublicDemo()) {
    renderNativeResponse();
    if (isStandaloneAgent()) {
      renderCodexActivity();
      if (!codexPanelState.loaded && !codexPanelState.loading) refreshCodexStatus();
      else openCodexEvents();
    }
  }
  syncDirectLivePoll();
}

function renderSession() {
  const target = $('#diagnosis-result');
  if (!target || !sessionView) return;
  const turn = sessionView.turns.at(-1);
  if (!turn) return;
  const events = sessionView.events;
  const text = events.filter(e => e.kind === 'text.delta').map(e => typeof e.payload === 'string' ? e.payload : (e.payload.text ?? e.payload.delta ?? '')).join('');
  const partial = turn.status === 'outcome_unknown' || turn.status === 'cancelled';
  let html = `<div class="section-title"><div class="result-label">SAVED DIAGNOSIS ${partial ? '· PARTIAL' : ''}</div>${chip(turn.status, turn.status === 'completed' ? 'green' : turn.status === 'failed' || partial ? 'amber' : 'blue')}</div>`;
  html += `<p class="response-text">${escape(text || (turn.status === 'failed' ? 'The fixture could not complete this diagnosis.' : 'Waiting for a synthetic response…'))}</p>`;
  if (turn.status === 'outcome_unknown') html += '<div class="denial">The response ended without a confirmed outcome. Its request is preserved and will not replay automatically. New diagnoses on this connection or run are blocked while it remains unresolved.</div>';
  for (const event of events.filter(e => e.kind === 'permission.denied')) html += '<div class="denial">Runtime tool request denied. No shell command or simulator action was executed.</div>';
  if (turn.proposal) {
    html += `<div class="proposal">${chip(turn.decision?.result === 'allow' ? 'advisory only' : 'action denied', turn.decision?.result === 'allow' ? 'green' : 'amber')}<h3>${escape(title(turn.proposal.kind))}</h3><p>${escape(turn.proposal.summary)}</p><p class="identity">Evidence: ${escape((turn.proposal.evidence_event_ids || []).join(', '))}</p><p class="subtle-note">${escape(turn.decision?.reason)} No dispatch available.</p></div>`;
  }
  if (['dispatching', 'running'].includes(turn.status)) html += `<button class="button secondary small" data-action="cancel" data-id="${escape(turn.turn_id)}">Cancel diagnosis</button>`;
  html += `<div class="session-code">${escape(turn.session_id)}<br>${isPublicDemo() ? 'No model calls · ' + events.length + ' scripted events' : 'Cost: unknown · ' + events.length + ' stored events'}</div>`;
  target.innerHTML = html;
}

async function pollSession() {
  if (polling || !selectedSession || !currentRun()) return;
  polling = true;
  const sessionId = selectedSession;
  const epoch = routeEpoch;
  try {
    const data = await api(`/api/sessions/${encodeURIComponent(sessionId)}`);
    if (sessionId === selectedSession && epoch === routeEpoch) {
      sessionView = data;
      renderSession();
    }
  } catch (error) {
    if (epoch === routeEpoch && $('#diagnosis-result')) $('#diagnosis-result').innerHTML = `<p class="error-text">${escape(error.message)}</p>`;
  } finally { polling = false; }
}

document.addEventListener('click', async event => {
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  const action = button.dataset.action, id = button.dataset.id;
  if (
    action.startsWith('live-')
    || action.startsWith('codex-')
    || action.startsWith('direct-')
    || action.startsWith('wizard-')
    || action.startsWith('report-')
    || action === 'overview-open-run-report'
    || action === 'overview-open-scope-report'
  ) return;
  if (action === 'public-run-eval') {
    if (!isPublicDemo() || codexPanelState.evalBusy) return;
    codexPanelState.evalBusy = true; render();
    try {
      codexPanelState.evalReport = await api('/api/agent/eval', {method: 'POST', body: {baseline: 'compare', repeats: 1}});
      toast('已完成 10 项 Agent 安全闭环评测！确定性编排器 100% 通过且 0 违规。');
    } catch (error) { toast(error.message); }
    finally { codexPanelState.evalBusy = false; render(); }
    return;
  }
  if (action === 'home-connect-codex') {
    if (isPublicDemo()) {
      connectorState.afterConnect = 'codex';
      await startConnectorFlow({openModal: true});
      return;
    }
    if (!isStandaloneAgent()) {
      location.hash = '#agents';
      return;
    }
    if (codexPanelState.busy) return;
    codexPanelState.busy = true; render();
    try {
      await api('/api/codex/connect', {method: 'POST', body: {}});
      await refreshCodexStatus();
      toast('🟢 已一键连接本机 Codex Agent！');
    } catch (error) {
      toast(error.message);
    } finally {
      codexPanelState.busy = false; render();
    }
    return;
  }
  if (action === 'open-ssh-setup-wizard') {
    const alias = button.dataset.alias || '';
    if (isPublicDemo()) {
      connectorState.afterConnect = 'ssh_setup';
      await startConnectorFlow({openModal: true});
      return;
    }
    openSshSetupWizard(alias);
    return;
  }
  if (action === 'home-connect-ssh') {
    if (isPublicDemo()) {
      connectorState.afterConnect = 'ssh';
      await startConnectorFlow({openModal: true});
      return;
    }
    await refreshWizard();
    render();
    return;
  }
  if (action === 'home-quick-ssh') {
    const aliasName = button.dataset.alias;
    if (!aliasName) return;
    if (button.dataset.verified === '1') {
      await disconnectSshAlias(aliasName);
    } else {
      await quickConnectSshAlias(aliasName);
    }
    return;
  }
  if (action === 'connector-open' || action === 'connector-retry') {
    await startConnectorFlow({openModal: true});
    return;
  }
  if (action === 'connector-close') {
    $('#connector-dialog')?.close();
    return;
  }
  if (action === 'connector-authorize') {
    openConnectorAuthorizationPopup();
    return;
  }
  if (action === 'connector-disconnect') {
    disconnectConnector();
    closeReportModal();
    $('#connector-dialog')?.close();
    await refresh();
    render();
    toast('已断开本机连接器，当前切换回网页预览模式。');
    return;
  }
  if (isPublicDemo() && ['add-host', 'import-pool', 'host-probe', 'pool-scan', 'native-select'].includes(action)) {
    await startConnectorFlow({openModal: true}); return;
  }
  if (action === 'native-select') { selectedQa = id; qaView = state.questions.find(q => q.qa_id === id) || null; renderNativeResponse(); return; }
  if (action === 'add-connection') {
    $('#connection-form').reset(); $('#form-error').textContent = ''; $('#connection-dialog').showModal(); return;
  }
  if (action === 'close-dialog') { $('#connection-dialog').close(); return; }
  if (action === 'add-host') {
    $('#host-form-error').textContent = ''; $('#host-dialog').showModal(); return;
  }
  if (action === 'close-host-dialog') { $('#host-dialog').close(); return; }
  if (action === 'import-pool') {
    const selected = $('#pool-template').value;
    $('#pool-template').innerHTML = state.hosts.map(host => `<option value="${escape(host.host_id)}">${escape(host.label)} · ${escape(host.host_id)}</option>`).join('');
    if (state.hosts.some(host => host.host_id === selected)) $('#pool-template').value = selected;
    resetPoolPreview(); $('#pool-form-error').textContent = ''; $('#pool-dialog').showModal(); return;
  }
  if (action === 'close-pool-dialog') { $('#pool-dialog').close(); return; }
  if (action === 'host-select-all') {
    if (!poolScanning && state.hosts.length <= 32) { selectedHostIds = new Set(state.hosts.map(host => host.host_id)); invalidateHostPlans(); }
    updateHostSelectionUI(); return;
  }
  if (action === 'host-select-none') {
    if (!poolScanning) { selectedHostIds = new Set(); invalidateHostPlans(); }
    updateHostSelectionUI(); return;
  }
  button.disabled = true;
  const originalText = button.textContent;
  try {
    if (action === 'demo-reset') {
      if (!isPublicDemo()) return;
      await api('/api/demo/reset', {method: 'POST'});
      routeEpoch++; selectedSession = null; sessionView = null; selectedConnection = '';
      selectedQa = null; qaView = null; selectedHostIds = null; placement = null; executionPlan = null;
      draftQuestion = 'Explain this run and suggest the next read-only check.';
      demoPendingRequests.clear();
      directState.loaded = false; directState.wfQuery = null; directState.wfCycles = null;
      directState.selectedId = null; directState.selectedBatchId = null; directState.aiReport = null; directState.scopeReports = []; directState.activeTab = 'waveforms';
      clearDirectLivePoll();
      closeReportModal();
      codexPanelState.evalReport = null; wizardState.tutorialStep = 1;
      $('#connection-dialog').close();
      await refresh();
      if (location.hash !== '#overview') location.hash = '#overview';
      render(); toast('演示数据已重置。可随时前往「✦ 新手步步教程」重新开始。'); return;
    }
    if (action === 'hosts-next-sim') {
      const best = pickMostIdleHostId();
      if (best) {
        directDraft.target_mode = 'host';
        directDraft.host_id = best;
      }
      directState.activeTab = isPublicDemo() ? 'waveforms' : 'setup';
      location.hash = '#direct';
      return;
    }
    if (action === 'sim-agent-connect') {
      if (!isStandaloneAgent() || isCircuitGitManagedEntry() || codexPanelState.busy) return;
      codexPanelState.busy = true;
      renderCodexActivity();
      try {
        await api('/api/codex/connect', {method: 'POST', body: {}});
        await refreshCodexStatus();
        if (codexPanelState.status?.connected && !codexPanelState.status?.thread_id) {
          const th = await api('/api/codex/thread', {method: 'POST'});
          codexPanelState.selectedThread = th.thread_id || null;
          await refreshCodexStatus();
        }
        openCodexEvents();
        void loadCodexCatalogAndAuth({force: true});
        toast('🟢 已连接本机 Codex Agent。');
      } finally {
        codexPanelState.busy = false;
        renderCodexActivity();
      }
      return;
    }
    if (action === 'sim-agent-settings') {
      codexPanelState.settingsOpen = !codexPanelState.settingsOpen;
      if (codexPanelState.settingsOpen) void loadCodexCatalogAndAuth();
      render();
      return;
    }
    if (action === 'sim-agent-refresh-models') {
      await loadCodexCatalogAndAuth({force: true});
      toast('已刷新本机 Codex 模型目录与登录状态。');
      return;
    }
    if (action === 'sim-agent-prompt') {
      const kind = button.dataset.kind || '';
      const prompts = {
        pick_host: '请直接基于已附带的服务器实时资源与 SPICE License 上下文，推荐最适合跑新 Spectre 仿真的服务器，并给出合理的 CPU 核数与内存建议（无需重新 SSH 探测）。',
        review_netlist: '请直接检查我当前右侧的网表与仿真配置（分析语句、收敛选项、save 节点与资源设置），简洁给出关键问题和修改建议。',
        pvt_plan: '请直接结合当前电路与 SPICE License 上下文，规划一套合理的 PVT Corner 与 Monte Carlo 批量仿真方案，兼顾覆盖度与并发控制。',
      };
      await sendPromptToSimAgent(prompts[kind] || prompts.pick_host);
      return;
    }
    if (action === 'host-live-refresh') {
      await refreshHostLiveStatus();
      return;
    }
    if (action === 'host-live-refresh-one') {
      if (id) await refreshHostLiveStatus({hostIds: [id]});
      return;
    }
    if (action === 'host-open-run' || action === 'overview-open-run') {
      if (id) {
        directState.selectedId = id;
        overviewState.selectedRunId = id;
        if (location.hash !== '#overview') location.hash = '#overview';
        await refreshDirect();
      }
      return;
    }
    if (action === 'overview-close-run') {
      overviewState.selectedRunId = '';
      render();
      return;
    }
    if (action === 'overview-launch-now') {
      if (!id || directState.busy) return;
      directState.busy = true;
      directState.error = null;
      render();
      try {
        await api(`/api/direct/runs/${encodeURIComponent(id)}/stage-and-launch`, {
          method: 'POST',
          body: {recheck_resources: false},
        });
        hostLiveState.loaded = false;
        toast(`🚀 任务 ${id} 已上传并成功启动！`);
      } catch (error) {
        directState.error = error.message;
        toast(error.message);
      } finally {
        directState.busy = false;
        await refreshDirect();
      }
      return;
    }
    if (action === 'overview-generate-ai-report') {
      if (!id || directState.busy || isPublicDemo()) return;
      directState.busy = true;
      render();
      try {
        const res = await api(`/api/direct/runs/${encodeURIComponent(id)}/ai-report`, {
          method: 'POST',
          body: {force: true},
        });
        if (res?.handoff?.pushed) {
          toast('✨ 已推送给 AI，正在自动生成并存档仿真报告…');
          if (isStandaloneAgent()) {
            if (codexPanelState.status?.connected) openCodexEvents();
            else void refreshCodexStatus();
          }
        } else {
          toast(res?.handoff?.note || '请先连接本机 Codex Agent 再生成仿真报告。');
        }
      } catch (error) {
        toast(error.message);
      } finally {
        directState.busy = false;
        await refreshDirect();
      }
      return;
    }
    if (action === 'overview-handoff-agent') {
      if (!id || directState.busy) return;
      directState.busy = true;
      render();
      try {
        const res = await api(`/api/direct/runs/${encodeURIComponent(id)}/inputs`);
        const r = res.run || {};
        const org = res.organization || resolveRunOrganization(r);
        const lim = res.limits || {};
        const sched = res.scheduler || {};
        const env = res.remote_env || {};
        directDraft.netlist_text = res.netlist_text || '';
        if (r.host_id) {
          directDraft.target_mode = 'host';
          directDraft.host_id = r.host_id;
        }
        if (r.simulator) directDraft.simulator = r.simulator;
        if (r.backend) directDraft.backend = r.backend;
        if (res.output_format) directDraft.output_format = res.output_format;
        if (Number.isInteger(lim.cpu_count) && lim.cpu_count >= 1) directDraft.cpus = lim.cpu_count;
        if (Number.isFinite(lim.memory_bytes) && lim.memory_bytes > 0) directDraft.memory_gib = Number((lim.memory_bytes / 2**30).toFixed(2));
        if (Number.isInteger(lim.wall_seconds) && lim.wall_seconds >= 60) directDraft.wall_seconds = lim.wall_seconds;
        if (res.remote_python) directDraft.remote_python = res.remote_python;
        if (res.remote_root) directDraft.remote_root = res.remote_root;
        directDraft.include_dirs = Array.isArray(res.include_dirs) ? res.include_dirs.join('\n') : '';
        directDraft.env_modules = Array.isArray(env.modules) ? env.modules.join(' ') : '';
        directDraft.env_scripts = Array.isArray(env.setup_scripts) ? env.setup_scripts.join('\n') : '';
        for (const k of ['queue', 'project', 'job_group', 'partition', 'account', 'qos']) {
          if (typeof sched[k] === 'string') directDraft[k] = sched[k];
        }
        if (typeof sched.exclusive === 'boolean') directDraft.exclusive = sched.exclusive;
        directDraft.run_key = '';
        simSourceState.origin = org.source_origin === 'local' ? 'local' : 'ssh';
        simSourceState.sshHostId = r.host_id || '';
        simSourceState.sshTarget = r.host_id || '';
        simSourceState.projectName = org.project || '';
        simSourceState.libraryName = org.library || '';
        simSourceState.cellName = org.testbench || '';
        simSourceState.viewId = org.view || '';
        simSourceState.fromSchematic = Boolean(org.view);
        simSourceState.fileName = `${org.testbench || r.run_id || 'input'}.scs`;
        simSourceState.entryPath = simSourceState.fileName;
        simSourceState.supportFiles = (res.support_files && typeof res.support_files === 'object') ? {...res.support_files} : {};
        directState.agentLinkedRunId = id;
        directState.selectedId = id;
        directState.activeTab = 'setup';
        location.hash = '#direct';
        toast(`✨ 已将任务 ${id} 的网表与配置载入工作台，并重新对接给 Agent！`);
      } catch (error) {
        toast(error.message);
      } finally {
        directState.busy = false;
        render();
      }
      return;
    }
    if (action === 'overview-delete-run') {
      if (!id || directState.busy) return;
      if (!window.confirm(`确认删除本地仿真记录 ${id}？仅删除本地记录与未上传草稿，不会影响远端原电路库文件。`)) return;
      directState.busy = true;
      render();
      try {
        await api(`/api/direct/runs/${encodeURIComponent(id)}`, {method: 'DELETE'});
        if (overviewState.selectedRunId === id) overviewState.selectedRunId = '';
        if (directState.selectedId === id) directState.selectedId = null;
        if (directState.agentLinkedRunId === id) directState.agentLinkedRunId = '';
        toast(`🗑 已删除本地仿真记录 ${id}。`);
      } catch (error) {
        toast(error.message);
      } finally {
        directState.busy = false;
        await refreshDirect();
      }
      return;
    }
    if (action === 'sim-agent-unlink-run') {
      directState.agentLinkedRunId = '';
      render();
      return;
    }
    if (action === 'overview-refresh') {
      await refreshDirect();
      toast('已刷新历史仿真记录。');
      return;
    }
    if (action === 'overview-clear-filters') {
      overviewState = {project: '', testbench: '', status: '', query: '', selectedRunId: overviewState.selectedRunId};
      render();
      return;
    }
    if (action === 'pool-scan') {
      const host_ids = selectedPoolIds();
      poolScanning = true; button.textContent = 'Checking selected hosts…'; updateHostSelectionUI();
      try {
        const result = await api('/api/hosts/scan', {method: 'POST', body: {host_ids}});
        placement = null; executionPlan = null;
        toast(`Pool check saved. ${result.results.filter(r => r.error).length} host(s) remain unknown or incomplete.`);
      } finally { poolScanning = false; updateHostSelectionUI(); }
    }
    if (action === 'host-probe') {
      button.textContent = 'Checking…';
      const probe = await api(`/api/hosts/${encodeURIComponent(id)}/probe`, {method: 'POST'});
      toast(probe.error ? probe.error.message : 'Connection check saved. Simulation readiness remains untested.');
    }
    if (action === 'seed') { await api('/api/demo', {method: 'POST'}); toast('Demo workspace loaded. All data is synthetic.'); }
    if (action === 'advance') { await api(`/api/runs/${encodeURIComponent(id)}/advance`, {method: 'POST'}); }
    if (action === 'probe') { await api(`/api/connections/${encodeURIComponent(id)}/probe`, {method: 'POST', body: {level: 'models'}}); toast('Metadata probe complete. Inference access is still unverified.'); }
    if (action === 'toggle') { const connection = state.connections.find(c => c.connection_id === id); await api(`/api/connections/${encodeURIComponent(id)}`, {method: 'PATCH', body: {enabled: !connection.enabled}}); }
    if (action === 'remove') {
      if (!window.confirm('Remove this demo connection? Saved sessions are retained.')) return;
      await api(`/api/connections/${encodeURIComponent(id)}`, {method: 'DELETE'});
    }
    if (action === 'cancel') { await api(`/api/turns/${encodeURIComponent(id)}/cancel`, {method: 'POST'}); await pollSession(); return; }
    await refresh(); render();
  } catch (error) { toast(error.message); }
  finally { if (button.isConnected) { button.disabled = false; button.textContent = originalText; } if (action === 'pool-scan') updateHostSelectionUI(); }
});

document.addEventListener('click', handleLiveClick);
document.addEventListener('click', handleDirectClick);
document.addEventListener('click', async event => {
  const button = event.target.closest('button[data-action^="wizard-"]');
  if (!button) return;
  const action = button.dataset.action.slice(7);
  if (action === 'step') {
    const next = Number(button.dataset.step);
    if (Number.isInteger(next) && next >= 1 && next <= 5) {
      wizardState.tutorialStep = next;
      render();
    }
    return;
  }
  if (action === 'goto') {
    const route = button.dataset.route || '#overview';
    const targetTab = button.dataset.tab || '';
    const targetRun = button.dataset.run || '';
    const targetBatch = button.dataset.batch || '';
    if (targetTab) directState.activeTab = targetTab;
    if (targetRun) directState.selectedId = targetRun;
    if (targetBatch) directState.selectedBatchId = targetBatch;
    location.hash = route;
    if (route === '#direct') {
      await refreshDirect();
      if (targetTab === 'waveforms' || targetRun === 'demo-psfbin-500c') {
        if (!directState.wfQuery) await runWaveformQuery();
        if (!directState.wfCycles && (directState.wfClock || directState.wfPeriod)) await runWaveformCycles();
      }
    }
    return;
  }
  if (isPublicDemo()) return;
  if (action === 'refresh') { await Promise.all([refresh(), refreshWizard()]); return; }
  if (wizardState.busy) return;
  if (action === 'demo') {
    wizardState.busy = true; wizardState.error = null; render();
    try {
      wizardState.demo = await api('/api/wizard/demo', {method: 'POST', body: {}});
      await refresh();
      toast('已生成全套 5 站沉浸式演示工作区！点击任意站点卡片即可直达体验。');
    } catch (error) { wizardState.error = error.message; toast(error.message); }
    finally { wizardState.busy = false; await refreshWizard(); }
    return;
  }
  if (action === 'register-probe') {
    await submitWizardHost(true);
    return;
  }
  if (action === 'sim-op') {
    const op = button.dataset.op, id = button.dataset.id;
    if (!op || !id) return;
    wizardState.busy = true; wizardState.error = null; render();
    try {
      const body = op === 'launch' ? {recheck_resources: false} : {};
      const res = await api(`/api/direct/runs/${encodeURIComponent(id)}/${op}`, {method: 'POST', body});
      if (res.run) wizardState.firstSim = {...(wizardState.firstSim || {}), run: res.run, report: res.report || null};
      toast(`首个仿真操作 ${op} 已完成。`);
    } catch (error) { wizardState.error = error.message; toast(error.message); }
    finally { wizardState.busy = false; await refreshWizard(); }
  }
});

async function submitWizardHost(probeAfter = false) {
  if (wizardState.busy) return;
  const payload = {
    host_id: wizardDraft.host_id.trim(),
    label: (wizardDraft.label || wizardDraft.host_id).trim(),
    ssh_target: wizardDraft.ssh_target.trim(),
    expected_host: wizardDraft.expected_host.trim(),
    expected_user: wizardDraft.expected_user.trim(),
    simulator: wizardDraft.simulator || 'spectre',
    simulator_executable: wizardDraft.simulator_executable.trim() || null,
    probe: !!probeAfter,
  };
  if (!payload.host_id || !payload.ssh_target || !payload.expected_host || !payload.expected_user) {
    wizardState.error = '请完整填写服务器 ID、OpenSSH 别名、期望远端主机名与用户名。';
    render();
    return;
  }
  wizardState.busy = true; wizardState.error = null; render();
  try {
    const res = await api('/api/wizard/register-host', {method: 'POST', body: payload});
    wizardState.lastProbe = res.probe || null;
    wizardDraft.selected_host_id = res.host.host_id;
    await refresh();
    toast(probeAfter
      ? (res.probe?.identity?.verified && !res.probe?.error ? `服务器 ${res.host.host_id} 验证通过！可以继续第 3 步。` : '服务器已登记，但连接验证未通过，请查看提示。')
      : `服务器 ${res.host.host_id} 已登记到本地。`);
  } catch (error) { wizardState.error = error.message; toast(error.message); }
  finally { wizardState.busy = false; await refreshWizard(); }
}

// Interactive waveform drag-to-zoom & trend click-to-drill-down
let wfDrag = null;
function svgClientToViewX(svg, clientX) {
  const rect = svg.getBoundingClientRect();
  const W = Number(svg.dataset.w || 760);
  if (!rect.width) return 0;
  return ((clientX - rect.left) / rect.width) * W;
}
document.addEventListener('pointerdown', event => {
  const svg = event.target.closest('svg[data-wf-zoom-svg="1"]');
  if (!svg || directState.wfBusy) return;
  const padL = Number(svg.dataset.padl || 66), padR = Number(svg.dataset.padr || 18), W = Number(svg.dataset.w || 760);
  const x = Math.max(padL, Math.min(W - padR, svgClientToViewX(svg, event.clientX)));
  if (directState.wfToolMode === 'cursor') {
    const plotW = W - padL - padR;
    const xMin = Number(svg.dataset.xmin), xMax = Number(svg.dataset.xmax);
    const valX = xMin + ((x - padL) / Math.max(1, plotW)) * (xMax - xMin);
    if (directState.wfCursorNext === 'A') {
      directState.wfCursorA = valX;
      directState.wfCursorNext = 'B';
    } else {
      directState.wfCursorB = valX;
      directState.wfCursorNext = 'A';
    }
    render();
    return;
  }
  wfDrag = {svg, startX: x, curX: x, padL, padR, W};
  const brush = svg.querySelector('.wf-zoom-brush');
  if (brush) { brush.setAttribute('x', x); brush.setAttribute('width', '0'); brush.hidden = false; }
});
document.addEventListener('pointermove', event => {
  if (!wfDrag) return;
  const x = Math.max(wfDrag.padL, Math.min(wfDrag.W - wfDrag.padR, svgClientToViewX(wfDrag.svg, event.clientX)));
  wfDrag.curX = x;
  const brush = wfDrag.svg.querySelector('.wf-zoom-brush');
  if (brush) {
    brush.setAttribute('x', Math.min(wfDrag.startX, x).toFixed(1));
    brush.setAttribute('width', Math.abs(x - wfDrag.startX).toFixed(1));
  }
});
document.addEventListener('pointerup', async () => {
  if (!wfDrag) return;
  const d = wfDrag; wfDrag = null;
  const brush = d.svg.querySelector('.wf-zoom-brush');
  if (brush) brush.hidden = true;
  if (Math.abs(d.curX - d.startX) < 12) return;
  const plotW = d.W - d.padL - d.padR;
  const xMin = Number(d.svg.dataset.xmin), xMax = Number(d.svg.dataset.xmax);
  const f0 = (Math.min(d.startX, d.curX) - d.padL) / plotW;
  const f1 = (Math.max(d.startX, d.curX) - d.padL) / plotW;
  const newMin = xMin + f0 * (xMax - xMin);
  const newMax = xMin + f1 * (xMax - xMin);
  if (newMax > newMin) await runWaveformQuery({xMin: String(newMin), xMax: String(newMax)});
});
document.addEventListener('click', async event => {
  const svg = event.target.closest('svg[data-wf-trend-svg="1"]');
  if (!svg || directState.wfBusy) return;
  const count = Number(svg.dataset.count || 0);
  if (count <= 0) return;
  const padL = Number(svg.dataset.padl || 66), padR = Number(svg.dataset.padr || 18), W = Number(svg.dataset.w || 760);
  const x = Math.max(padL, Math.min(W - padR, svgClientToViewX(svg, event.clientX)));
  const frac = (x - padL) / Math.max(1, W - padL - padR);
  const idx = Math.max(0, Math.min(count - 1, Math.round(frac * Math.max(0, count - 1))));
  await runWaveformCycles(idx);
});

$('#connection-form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('[type=submit]'); button.disabled = true;
  try {
    const fields = new FormData(form);
    const kind = fields.get('kind');
    await api('/api/connections', {method: 'POST', body: {label: fields.get('label'), kind, backend: `fake_${kind}`, scenario: fields.get('scenario')}});
    $('#connection-dialog').close(); await refresh(); render(); toast('Demo connection created.');
  } catch (error) { $('#form-error').textContent = error.message; }
  finally { button.disabled = false; }
});

function resetPoolPreview() {
  poolImportPreview = null;
  $('#pool-import-preview').hidden = true;
  $('#pool-import-preview').textContent = '';
  $('#pool-import-submit').textContent = 'Review import';
}

function setHostFormBusy(form, busy) {
  form.querySelectorAll('input, select, textarea, button').forEach(control => { control.disabled = busy; });
}

$('#host-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (isPublicDemo()) { location.hash = '#local'; return; }
  const form = event.currentTarget, fields = new FormData(form);
  const profile = {schema: 'spiceowl.host.v1', tools: []};
  for (const key of ['host_id', 'label', 'ssh_target', 'expected_host', 'expected_user']) profile[key] = fields.get(key).trim();
  const override = fields.get('ssh_hostname').trim();
  if (override) { profile.schema = 'spiceowl.host.v2'; profile.ssh_hostname = override; }
  for (const kind of ['spectre', 'virtuoso', 'ngspice']) {
    const executable = fields.get(kind).trim();
    if (executable) profile.tools.push({kind, executable});
  }
  setHostFormBusy(form, true);
  $('#host-form-error').textContent = '';
  try {
    await api('/api/hosts', {method: 'POST', body: profile});
    $('#host-dialog').close(); form.reset();
    placement = null; executionPlan = null;
    await refresh(); render(); toast('Host registered locally. Use Check connection to verify it.');
  } catch (error) {
    if ($('#host-dialog').open) $('#host-form-error').textContent = error.message;
    else toast(error.message + ' Refresh the host list to inspect saved registrations.');
  }
  finally { setHostFormBusy(form, false); }
});

$('#pool-import-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (isPublicDemo()) { location.hash = '#local'; return; }
  const form = event.currentTarget, fields = new FormData(form);
  $('#pool-form-error').textContent = '';
  try {
    const names = fields.get('names').trim().split(/[\s,]+/).filter(Boolean);
    if (!names.length || names.length > 32 || new Set(names).size !== names.length) throw new Error('Enter 1–32 distinct target hostnames.');
    const label = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/;
    if (names.some(name => name.length > 253 || name.split('.').some(part => !label.test(part)))) throw new Error('Use explicit DNS hostnames: no wildcards, commands, user@host values or URLs.');
    const request = {template_id: fields.get('template_id'), names};
    if (!state.hosts.some(host => host.host_id === request.template_id)) throw new Error('Select an existing registered host as a route template.');
    if (poolImportPreview !== JSON.stringify(request)) {
      poolImportPreview = JSON.stringify(request);
      const template = state.hosts.find(host => host.host_id === request.template_id);
      $('#pool-import-preview').innerHTML = `<strong>Review ${names.length} target hostname(s)</strong><p>Route template: ${escape(template.label)} · ${escape(template.host_id)}</p><ul>${names.map(name => `<li>${escape(name)}</li>`).join('')}</ul><p>Matching existing registrations are reused. Every target needs an independent connection check.</p>`;
      $('#pool-import-preview').hidden = false;
      $('#pool-import-submit').textContent = 'Register listed hosts';
      return;
    }
    setHostFormBusy(form, true);
    await api('/api/hosts/import-pool', {method: 'POST', body: request});
    $('#pool-dialog').close(); form.reset(); resetPoolPreview();
    placement = null; executionPlan = null;
    await refresh(); render(); toast('Pool registered locally. Use Check selected hosts to verify the hosts and resources.');
  } catch (error) {
    if ($('#pool-dialog').open) $('#pool-form-error').textContent = error.message;
    else toast(error.message + ' Refresh the host list to inspect saved registrations.');
  }
  finally { setHostFormBusy(form, false); }
});

document.addEventListener('input', event => {
  const codexFields = {'codex-profile': 'profile', 'codex-model': 'model', 'codex-message': 'draft'};
  if (codexFields[event.target.id]) {
    codexPanelState[codexFields[event.target.id]] = event.target.value;
    if (event.target.id === 'codex-message') {
    if ($('#codex-send')) {
      $('#codex-send').disabled = location.hash === '#direct'
        ? (codexPanelState.busy || !event.target.value.trim())
        : (!codexCanSend() || !event.target.value.trim());
    }
  }
  }
  const liveFields = {'live-configuration': 'configuration', 'live-source-commit': 'source_commit', 'live-run-id': 'run_id'};
  if (liveFields[event.target.id]) {
    liveDraft[liveFields[event.target.id]] = event.target.value;
    const collect=document.querySelector('[data-action="live-collect"]');
    if(collect){try{collect.disabled=live.busy||!live.runtime?.capabilities?.direct_collect||!!livePending('collect')||!liveCollectReady();}catch{collect.disabled=true;}}
  }
  const directInputs = {
    'direct-run-key': 'run_key', 'direct-cpus': 'cpus', 'direct-memory': 'memory_gib',
    'direct-wall': 'wall_seconds', 'direct-queue': 'queue', 'direct-project': 'project',
    'direct-job-group': 'job_group', 'direct-partition': 'partition', 'direct-account': 'account',
    'direct-qos': 'qos', 'direct-python': 'remote_python', 'direct-root': 'remote_root',
    'direct-includes': 'include_dirs', 'direct-env-modules': 'env_modules', 'direct-env-scripts': 'env_scripts',
    'direct-netlist': 'netlist_text', 'direct-lookup-key': 'lookup_key',
    'direct-rec-attempts': 'rec_max_attempts', 'direct-rec-maxmem': 'rec_max_memory_gib',
    'direct-rec-backoff': 'rec_license_backoff', 'direct-rec-elapsed': 'rec_max_elapsed_min',
  };
  if (directInputs[event.target.id]) directDraft[directInputs[event.target.id]] = event.target.value;
  if (event.target.id === 'direct-rec-query') directState.recoveryQuery = event.target.value;
  if (event.target.id === 'wf-xmin') directState.wfWindow.xMin = event.target.value;
  if (event.target.id === 'wf-xmax') directState.wfWindow.xMax = event.target.value;
  if (event.target.id === 'wf-maxpts') directState.wfMaxPoints = Number(event.target.value) || 1200;
  if (event.target.id === 'wf-period') directState.wfPeriod = event.target.value;
  if (event.target.id === 'wf-jump-cycle') directState.wfJumpCycle = event.target.value;
  const batchInputs = {
    'batch-id': 'batch_id', 'batch-corners': 'corners', 'batch-voltages': 'voltages',
    'batch-temps': 'temps', 'batch-mc-samples': 'mc_samples', 'batch-mc-seed': 'mc_base_seed',
    'batch-max-conc': 'max_concurrency', 'batch-spec-name': 'spec_name',
    'batch-spec-signal': 'spec_signal', 'batch-spec-min': 'spec_min', 'batch-spec-max': 'spec_max',
  };
  if (batchInputs[event.target.id]) batchDraft[batchInputs[event.target.id]] = event.target.value;
  const wizInputs = {
    'wiz-ssh-target': 'ssh_target', 'wiz-host-id': 'host_id', 'wiz-expected-host': 'expected_host',
    'wiz-expected-user': 'expected_user', 'wiz-sim-exe': 'simulator_executable',
    'wiz-queue': 'queue', 'wiz-partition': 'partition', 'wiz-remote-root': 'remote_root',
    'wiz-custom-netlist': 'custom_netlist',
  };
  if (wizInputs[event.target.id]) wizardDraft[wizInputs[event.target.id]] = event.target.value;
  if (event.target.id === 'diagnostic-question') draftQuestion = event.target.value;
  if (event.target.closest('#pool-import-form')) resetPoolPreview();
});
document.addEventListener('change', event => {
  if (event.target.id === 'codex-session') {
    codexPanelState.selectedThread = event.target.value;
    renderCodexActivity();
  }
  if (event.target.id === 'sim-ai-model') {
    codexPanelState.model = event.target.value;
  }
  if (event.target.id === 'sim-ai-effort') {
    codexPanelState.reasoningEffort = event.target.value;
  }
  if (event.target.id === 'codex-rec-confirm') {
    codexPanelState.recoveryConfirmed = event.target.checked;
    if (!location.hash || location.hash === '#quickstart') render();
    else renderCodexActivity();
  }
  if (event.target.matches('[data-host-select]')) {
    if (!poolScanning) {
      selectedHostIds ??= new Set();
      if (event.target.checked) selectedHostIds.add(event.target.dataset.hostSelect);
      else selectedHostIds.delete(event.target.dataset.hostSelect);
      invalidateHostPlans();
    }
    updateHostSelectionUI();
  }
  if (event.target.id === 'direct-backend') { directDraft.backend = event.target.value; render(); }
  if (event.target.id === 'direct-simulator') directDraft.simulator = event.target.value;
  if (event.target.id === 'direct-target-mode') { directDraft.target_mode = event.target.value; render(); }
  if (event.target.id === 'direct-host-id') directDraft.host_id = event.target.value;
  if (event.target.id === 'direct-format') directDraft.output_format = event.target.value;
  if (event.target.id === 'direct-exclusive') directDraft.exclusive = event.target.checked;
  if (event.target.id === 'direct-recheck') directDraft.recheck_resources = event.target.checked;
  if (event.target.id === 'direct-allow-collect') directDraft.allow_collect = event.target.checked;
  if (event.target.id === 'direct-rec-enabled') { directDraft.rec_enabled = event.target.checked; render(); }
  if (event.target.id === 'direct-rec-lic') directDraft.rec_retry_license = event.target.checked;
  if (event.target.id === 'direct-rec-oom') directDraft.rec_retry_oom = event.target.checked;
  if (event.target.id === 'direct-rec-relocate') directDraft.rec_allow_relocate = event.target.checked;
  if (event.target.id === 'direct-rec-outcome') { directState.recoveryOutcome = event.target.value; refreshDirect(); }
  if (event.target.id === 'watch-sim-scenario') { directState.watchSimScenario = event.target.value; }
  if (event.target.id === 'wf-dataset') {
    directState.wfDataset = event.target.value;
    syncWaveformSelection(true);
    render();
  }
  if (event.target.matches('[data-wf-signal]')) {
    const name = event.target.dataset.wfSignal;
    if (event.target.checked) {
      if (!directState.wfSignals.includes(name)) directState.wfSignals = [...directState.wfSignals, name];
    } else {
      directState.wfSignals = directState.wfSignals.filter(s => s !== name);
    }
    syncWaveformSelection(false);
    render();
  }
  if (event.target.id === 'wf-clock') { directState.wfClock = event.target.value; render(); }
  if (event.target.id === 'wf-worst-metric') directState.wfWorstMetric = event.target.value;
  if (event.target.id === 'wf-worst-signal') directState.wfWorstSignal = event.target.value;
  if (event.target.id === 'batch-kind') { batchDraft.kind = event.target.value; render(); }
  if (event.target.id === 'batch-metric-select') { directState.batchMetric = event.target.value; render(); }
  if (event.target.id === 'wiz-alias-pick') {
    const picked = (wizardState.status?.ssh_aliases || []).find(a => a.alias === event.target.value);
    if (picked) { applySshAliasToDraft(picked); render(); }
  }
  if (event.target.id === 'wiz-simulator') {
    wizardDraft.simulator = event.target.value;
    wizardDraft.template_id = event.target.value === 'ngspice' ? 'ngspice_rc_smoke' : 'spectre_rc_smoke';
    render();
  }
  if (event.target.id === 'wiz-target-host') wizardDraft.selected_host_id = event.target.value;
  if (event.target.id === 'wiz-backend') { wizardDraft.backend = event.target.value; render(); }
  if (event.target.id === 'wiz-template') { wizardDraft.template_id = event.target.value; render(); }
  if (event.target.id === 'direct-netlist-file' && event.target.files?.[0]) {
    const reader = new FileReader();
    reader.onload = () => {
      directDraft.netlist_text = String(reader.result || '');
      const box = $('#direct-netlist');
      if (box) box.value = directDraft.netlist_text;
      toast(`已读取网表文件：${event.target.files[0].name}`);
    };
    reader.readAsText(event.target.files[0]);
  }
  if (event.target.id === 'diagnostic-connection') selectedConnection = event.target.value;
  if (event.target.id === 'session-picker') { selectedSession = event.target.value; sessionView = null; routeEpoch++; pollSession(); }
  if (event.target.closest('#pool-import-form')) resetPoolPreview();
});

function placementRequirements() {
  requestedCpus = Number($('#pool-cpus').value);
  requestedMemory = Number($('#pool-memory').value);
  requestedSimulator = $('#pool-simulator').value;
  if (!Number.isInteger(requestedCpus) || requestedCpus < 1 || !Number.isFinite(requestedMemory) || requestedMemory <= 0) throw new Error('Enter positive CPU and memory requirements.');
  return {cpu_count: requestedCpus, memory_bytes: Math.ceil(requestedMemory * 2**30), simulator: requestedSimulator};
}

document.addEventListener('click', async event => {
  const button = event.target.closest('button[data-action]');
  const action = button?.dataset.action;
  if (!action?.startsWith('codex-') || !isStandaloneAgent()) return;
  if (action === 'codex-refresh') { await refreshCodexStatus(); return; }
  if (action === 'codex-details') {
    try {
      const approval = await api(`/api/codex/approval?approval_id=${encodeURIComponent(button.dataset.id)}`);
      if (approval.approval_id !== button.dataset.id) throw new Error('The action details returned a different approval identity.');
      codexPanelState.approvals.set(approval.approval_id, approval);
    } catch (error) { codexPanelState.error = error.message; }
    renderCodexActivity(); return;
  }
  if (action === 'codex-inspect-recovery') {
    try {
      codexPanelState.recoverySnapshot = await api('/api/codex/recovery');
      codexPanelState.recoveryConfirmed = false;
    } catch (error) { codexPanelState.error = error.message; toast(error.message); }
    if (!location.hash || location.hash === '#quickstart') render();
    else renderCodexActivity();
    return;
  }
  if (action === 'codex-run-eval') {
    if (codexPanelState.evalBusy) return;
    codexPanelState.evalBusy = true; codexPanelState.error = null; renderCodexActivity();
    try {
      codexPanelState.evalReport = await api('/api/agent/eval', {method: 'POST', body: {baseline: 'compare', repeats: 1}});
      toast('已完成 10 项 Agent 闭环安全评测。');
    } catch (error) { codexPanelState.error = error.message; toast(error.message); }
    finally { codexPanelState.evalBusy = false; renderCodexActivity(); }
    return;
  }
  if (action === 'codex-interrupt') {
    if (codexPanelState.interrupting) return;
    codexPanelState.interrupting = true;
    codexPanelState.error = null;
    renderCodexActivity();
    try {
      await api('/api/codex/interrupt', {method: 'POST', body: {}});
      const tId = codexPanelState.status?.thread_id || codexPanelState.selectedThread;
      for (const row of codexPanelState.approvals.values()) {
        if ((!tId || row.thread_id === tId) && ['pending', 'resolving'].includes(row.status || 'pending')) {
          Object.assign(row, {status: 'resolved', decision: 'interrupted', can_approve: false, can_deny: false});
        }
      }
      if (codexPanelState.pending) {
        try { sessionStorage.removeItem(CODEX_PENDING_KEY); } catch { /* ignore */ }
        codexPanelState.pending = null;
      }
      await refreshCodexStatus();
      toast('已停止当前 AI 回合（未取消任何已启动的仿真）。');
    } catch (error) {
      codexPanelState.error = error.message;
      toast(error.message);
      await refreshCodexStatus();
    } finally {
      codexPanelState.interrupting = false;
      renderCodexActivity();
    }
    return;
  }
  if (action === 'codex-cancel-full-access') {
    codexPanelState.permissionConfirmPending = false;
    renderCodexActivity();
    return;
  }
  if (action === 'codex-set-permission') {
    if (isPublicDemo()) return;
    const targetMode = button.dataset.mode;
    if (!['safe', 'supervised', 'full_access'].includes(targetMode)) return;
    if (codexPanelState.busy || button.disabled || codexActiveTurn() || codexPanelState.status?.recovery_required) return;
    if (targetMode === codexPermissionMode()) {
      if (codexPanelState.permissionConfirmPending) {
        codexPanelState.permissionConfirmPending = false;
        renderCodexActivity();
      }
      return;
    }
    if (targetMode === 'full_access') {
      codexPanelState.permissionConfirmPending = true;
      renderCodexActivity();
      return;
    }
  }
  if (action === 'codex-confirm-full-access') {
    if (isPublicDemo() || codexPanelState.busy || button.disabled || codexActiveTurn() || codexPanelState.status?.recovery_required) return;
  }
  if (codexPanelState.busy || button.disabled) return;
  codexPanelState.busy = true;
  codexPanelState.error = null;
  let approval = null;
  try {
    if (action === 'codex-set-permission' || action === 'codex-confirm-full-access') {
      if (isPublicDemo() || codexActiveTurn() || codexPanelState.status?.recovery_required) return;
      const mode = action === 'codex-confirm-full-access' ? 'full_access' : button.dataset.mode;
      if (!['safe', 'supervised', 'full_access'].includes(mode)) return;
      renderCodexActivity();
      const res = await api('/api/codex/permission', {
        method: 'POST',
        body: {
          permission_mode: mode,
          ...(mode === 'full_access' ? {confirmed_full_access: true} : {}),
        },
      });
      setCodexStatus(res);
      codexPanelState.permissionConfirmPending = false;
      if (res.connected && !res.thread_id && location.hash === '#direct') {
        const th = await api('/api/codex/thread', {method: 'POST'});
        if (th?.thread_id) {
          codexThread(th.thread_id);
          codexPanelState.selectedThread = th.thread_id;
        }
      }
      const effectiveMode = codexPermissionMode();
      const modeToasts = {
        full_access: '🚀 已开启「满血全权」模式：Codex 现在拥有完整本机 Shell 与免弹窗仿真提交能力！',
        supervised: '⚡ 已切换至「满血受控」模式：开放本机 Shell/Python/Skill，高危操作人工审批。',
        safe: '🔒 已切换至「安全托管」模式。',
      };
      toast(modeToasts[effectiveMode] || modeToasts.safe);
    } else if (action === 'codex-reconcile') {
      codexPanelState.permissionConfirmPending = false;
      const resolution = (button.dataset.reconnect === '1' || location.hash === '#direct')
        ? 'abort_and_reset_thread'
        : 'acknowledge_and_archive';
      const recRes = await api('/api/codex/recover', {method: 'POST', body: {
        resolution,
        note: 'Reconciled interrupted turn from local console.',
        confirmed_side_effects_checked: !!codexPanelState.recoveryConfirmed,
      }});
      if (codexPanelState.pending) {
        const pItem = codexThread(codexPanelState.pending.thread_id)?.items
          .find(row => row.requestKey === codexPanelState.pending.request_key);
        if (pItem && pItem.status === 'unconfirmed') pItem.status = 'interrupted';
      }
      codexPanelState.pending = null;
      try { sessionStorage.removeItem(CODEX_PENDING_KEY); } catch { /* ignore */ }
      codexPanelState.turnStartedAt = null;
      codexPanelState.lastTurnStatus = null;
      codexPanelState.recoverySnapshot = null;
      codexPanelState.recoveryConfirmed = false;
      codexPanelState.recoveryAutoLoadedKey = null;
      for (const row of codexPanelState.approvals.values()) {
        if (['pending', 'resolving', 'unknown'].includes(row.status || 'pending')) {
          Object.assign(row, {status: 'resolved', decision: 'interrupted_reconciled', can_approve: false, can_deny: false});
        }
      }
      if (button.dataset.reconnect === '1') {
        const alreadyConnected = Boolean(recRes?.status?.connected);
        if (!alreadyConnected) {
          await api('/api/codex/connect', {method: 'POST', body: {}});
        }
        if (location.hash === '#direct') {
          const th = await api('/api/codex/thread', {method: 'POST'});
          codexPanelState.selectedThread = th.thread_id || null;
        }
        toast('🟢 已归档中断记录并切换到全新 Codex 会话，可直接继续对话！');
      } else {
        toast('中断回合已归档（未重放任何操作）并解除锁定。');
      }
    } else if (action === 'codex-thread') {
      const status = codexPanelState.status;
      if (!status?.connected || status.recovery_required || codexActiveTurn() || codexPanelState.pending) return;
      renderCodexActivity();
      const receipt = await api('/api/codex/thread', {method: 'POST'});
      codexPanelState.selectedThread = receipt.thread_id || null;
    } else if (action === 'codex-disconnect') {
      closeCodexEvents();
      codexPanelState.settingsOpen = false;
      codexPanelState.permissionConfirmPending = false;
      codexPanelState.modelsLoaded = false;
      codexPanelState.authAccount = null;
      renderCodexActivity();
      await api('/api/codex/disconnect', {method: 'POST'});
    } else if (['codex-approve', 'codex-deny'].includes(action)) {
      approval = codexPanelState.approvals.get(button.dataset.id);
      const status = codexPanelState.status;
      const allowed = action === 'codex-approve' ? approval?.can_approve : approval?.can_deny;
      if (!allowed || approval.status !== 'pending' || !status?.connected || status.recovery_required
          || status.state !== 'awaiting_approval' || codexPanelState.streamState !== 'connected'
          || approval.thread_id !== status.thread_id || approval.thread_id !== codexPanelState.selectedThread
          || !status.turn_id || approval.turn_id !== status.turn_id) return;
      if (action === 'codex-approve' && (approval.preview_only === true
          || JSON.stringify(approval.params ?? null, null, 2).length > 65536)) return;
      const decision = action === 'codex-approve' ? 'approve' : 'deny';
      approval.status = 'resolving';
      renderCodexActivity();
      await api('/api/codex/approval', {method: 'POST', body: {approval_id: approval.approval_id, decision}});
      Object.assign(approval, {status: 'resolved', decision, can_approve: false, can_deny: false});
      renderCodexActivity();
    }
    await refreshCodexStatus();
  } catch (error) {
    await refreshCodexStatus();
    codexPanelState.error = error.message;
    toast(error.message);
    if (approval) Object.assign(approval, {status: 'unknown', can_approve: false, can_deny: false});
  } finally {
    codexPanelState.busy = false;
    if (!location.hash || location.hash === '#quickstart') render();
    else renderCodexActivity();
  }
});

document.addEventListener('submit', async event => {
  const id = event.target.id;
  if (!['placement-form', 'execution-plan-form'].includes(id)) return;
  event.preventDefault();
  if (isPublicDemo()) { location.hash = '#local'; return; }
  if (planningBusy) return;
  const button = event.target.querySelector('[type=submit]');
  button.disabled = true;
  try {
    if (id === 'placement-form') {
      const host_ids = selectedPoolIds(), revision = hostSelectionRevision;
      planningBusy = true; updateHostSelectionUI();
      const result = await api('/api/placement/recommend', {method: 'POST', body: {...placementRequirements(), host_ids}});
      if (revision !== hostSelectionRevision) throw new Error('The host selection changed during evaluation. Evaluate the selected hosts again.');
      placement = result;
      executionPlan = null; render();
    } else if (id === 'execution-plan-form') {
      const form = new FormData(event.target), kind = form.get('kind'), reference = form.get('reference');
      plannedKind = kind; plannedReference = reference; plannedHost = form.get('host');
      const origin = kind === 'standalone' ? {kind, simulator: requestedSimulator, input_ref: reference} : {kind, simulator: 'spectre', host_id: form.get('host'), native_run_ref: reference};
      const requirements = placementRequirements();
      if (kind === 'standalone') origin.simulator = requirements.simulator;
      const host_ids = selectedPoolIds(), revision = hostSelectionRevision;
      planningBusy = true; updateHostSelectionUI();
      const result = await api('/api/execution/plan', {method: 'POST', body: {origin, requirements, host_ids}});
      if (revision !== hostSelectionRevision) throw new Error('The host selection changed during planning. Prepare a plan for the selected hosts again.');
      executionPlan = result;
      render();
    }
  } catch (error) {
    toast(error.message);
  } finally { if (button.isConnected) button.disabled = false; planningBusy = false; updateHostSelectionUI(); }
});

document.addEventListener('submit', async event => {
  if (event.target.id === 'sim-agent-form') {
    event.preventDefault();
    await sendPromptToSimAgent(codexPanelState.draft);
    return;
  }
  if (!['codex-connect-form', 'codex-message-form'].includes(event.target.id)) return;
  event.preventDefault();
  if (!isStandaloneAgent() || codexPanelState.busy) return;
  const connecting = event.target.id === 'codex-connect-form';
  if (connecting && (codexPanelState.status?.connected || codexPanelState.status?.recovery_required)) return;
  if (!connecting && (!codexCanSend() || !codexPanelState.draft.trim())) return;
  codexPanelState.busy = true;
  codexPanelState.error = null;
  renderCodexActivity();
  try {
    if (connecting) {
      await api('/api/codex/connect', {method: 'POST', body: {
        ...(codexPanelState.model.trim() ? {model: codexPanelState.model.trim()} : {}),
        ...(codexPanelState.profile.trim() ? {profile: codexPanelState.profile.trim()} : {})}});
      await refreshCodexStatus();
    } else {
      const request = {thread_id: codexPanelState.status.thread_id, text: codexPanelState.draft.trim(),
        request_key: `codex-${crypto.randomUUID()}`};
      // Persist before dispatch. No startup, reconnect or refresh path posts it.
      sessionStorage.setItem(CODEX_PENDING_KEY, JSON.stringify(request));
      codexPanelState.pending = request;
      codexItem(codexThread(request.thread_id), 'request:' + request.request_key, 'user',
        {text: request.text, status: 'unconfirmed', requestKey: request.request_key});
      codexPanelState.draft = '';
      $('#codex-message').value = '';
      renderCodexActivity();
      const receipt = await api('/api/codex/message', {method: 'POST', body: request});
      if (receipt.request_key === request.request_key && !receipt.recovery_required) confirmCodexPending(receipt.request_key, receipt.turn_id);
      await refreshCodexStatus();
    }
  } catch (error) {
    const rejected = ['codex_rpc_rejected', 'codex_turn_busy', 'codex_thread_mismatch',
      'codex_journal_full', 'codex_not_connected', 'codex_embedded_mode', 'codex_bridge_closed', 'codex_recovery_required'];
    codexPanelState.turnStartedAt = null;
    codexPanelState.lastTurnStatus = 'failed';
    if (!connecting && (rejected.includes(error.code) || error.code === 'codex_protocol') && codexPanelState.pending) {
      const pending = codexPanelState.pending;
      try {
        sessionStorage.removeItem(CODEX_PENDING_KEY);
        codexPanelState.pending = null;
        if (rejected.includes(error.code)) codexPanelState.draft = pending.text;
        const item = codexThread(pending.thread_id)?.items.find(row => row.requestKey === pending.request_key);
        if (item) item.status = rejected.includes(error.code) ? 'rejected' : 'failed';
      } catch { /* Preserve an unreadable request record without replay. */ }
    }
    await refreshCodexStatus().catch(() => {});
    codexPanelState.error = formatCodexErrorMessage(error);
  } finally { codexPanelState.busy = false; renderCodexActivity(); }
});

document.addEventListener('submit', async event => {
  if (event.target.id === 'wizard-host-form') {
    event.preventDefault();
    if (!isPublicDemo()) await submitWizardHost(false);
    return;
  }
  if (event.target.id === 'wizard-first-sim-form') {
    event.preventDefault();
    if (isPublicDemo() || wizardState.busy) return;
    const hostId = wizardDraft.selected_host_id || (wizardState.status?.workspace?.hosts?.[0]?.host_id) || '';
    if (!hostId) { toast('请先在第 2 步登记至少一台 EDA 服务器。'); return; }
    wizardState.busy = true; wizardState.error = null; render();
    try {
      const payload = {
        host_id: hostId,
        simulator: wizardDraft.template_id === 'ngspice_rc_smoke' ? 'ngspice' : wizardDraft.simulator || 'spectre',
        backend: wizardDraft.backend || 'ssh_direct',
        remote_python: wizardDraft.remote_python.trim() || '/usr/bin/python3',
        remote_root: wizardDraft.remote_root.trim() || '.spiceowl-runs',
      };
      if (wizardDraft.template_id === 'custom') payload.netlist_text = wizardDraft.custom_netlist;
      else payload.template_id = wizardDraft.template_id;
      if (payload.backend === 'lsf') payload.queue = wizardDraft.queue.trim();
      if (payload.backend === 'slurm') payload.partition = wizardDraft.partition.trim();
      wizardState.firstSim = await api('/api/wizard/first-sim', {method: 'POST', body: payload});
      directState.selectedId = wizardState.firstSim.run.run_id;
      toast(`首个仿真计划 ${wizardState.firstSim.run.run_id} 已冻结！现在点击第 4 步「① 上传输入」即可开始运行。`);
    } catch (error) { wizardState.error = error.message; toast(error.message); }
    finally { wizardState.busy = false; await refreshWizard(); }
    return;
  }
  if (event.target.id !== 'direct-batch-form') return;
  event.preventDefault();
  if (isPublicDemo() || directState.batchBusy) return;
  const tpl = directDraft.netlist_text.trim();
  if (!tpl) { toast('请先在上方「1. 冻结并准备实验」中填入或导入网表模板。'); return; }
  directState.batchBusy = true; directState.batchError = null; render();
  try {
    const parseNums = s => String(s || '').split(/[,\s]+/).map(x => x.trim()).filter(Boolean).map(Number);
    const parseStrs = s => String(s || '').split(/[,\s]+/).map(x => x.trim()).filter(Boolean);
    const payload = {
      batch_id: batchDraft.batch_id.trim(),
      kind: batchDraft.kind,
      netlist_template: tpl,
      process_corners: parseStrs(batchDraft.corners),
      voltages: parseNums(batchDraft.voltages),
      temperatures: parseNums(batchDraft.temps),
      max_concurrency: Number(batchDraft.max_concurrency) || 4,
      backend: directDraft.backend,
      simulator: directDraft.simulator,
      output_format: directDraft.output_format,
      cpus: Number(directDraft.cpus) || 1,
      memory_gib: Number(directDraft.memory_gib) || 4,
      wall_seconds: Number(directDraft.wall_seconds) || 3600,
      remote_python: directDraft.remote_python.trim() || '/usr/bin/python3',
      remote_root: directDraft.remote_root.trim() || '.spiceowl-runs',
    };
    if (batchDraft.kind !== 'pvt') {
      payload.mc_samples = Number(batchDraft.mc_samples) || 10;
      payload.mc_base_seed = Number(batchDraft.mc_base_seed) || 1001;
      payload.mc_variation = batchDraft.mc_variation || 'mismatch';
    }
    if (batchDraft.spec_name.trim()) {
      const sp = {
        name: batchDraft.spec_name.trim(),
        signal: batchDraft.spec_signal.trim() || null,
        metric: batchDraft.spec_metric || 'max',
        unit: batchDraft.spec_unit || '',
      };
      if (batchDraft.spec_min !== '') sp.min = Number(batchDraft.spec_min);
      if (batchDraft.spec_max !== '') sp.max = Number(batchDraft.spec_max);
      payload.specs = [sp];
    }
    if (directDraft.target_mode === 'pool') payload.host_ids = state.hosts.slice(0, 32).map(h => h.host_id);
    else payload.host_id = directDraft.host_id;
    const res = await api('/api/direct/batches/prepare', {method: 'POST', body: payload});
    directState.selectedBatchId = res.batch.batch_id;
    batchDraft.batch_id = '';
    toast(`批量计划 ${res.batch.batch_id}（共 ${res.summary.counts.total} 个任务）已冻结在本地。`);
  } catch (error) {
    directState.batchError = error.message;
    toast(error.message);
  } finally {
    directState.batchBusy = false;
    await refreshDirect();
  }
});

document.addEventListener('submit', async event => {
  if (event.target.id !== 'direct-prepare-form') return;
  event.preventDefault();
  if (isPublicDemo() || directState.busy) return;
  directState.busy = true; directState.error = null; render();
  try {
    const payload = buildDirectPreparePayload();
    const res = await api('/api/direct/prepare', {method: 'POST', body: payload});
    directState.selectedId = res.run.run_id;
    directDraft.run_key = '';
    toast(`计划 ${res.run.run_id} 已冻结在本地，可以执行上传与启动。`);
  } catch (error) {
    directState.error = error.message;
    toast(error.message);
  } finally {
    directState.busy = false;
    await refreshDirect();
  }
});

// Library Manager dialog interactions (reused CircuitGit SSH & local folder import flow)
document.addEventListener('click', async event => {
  const btn = event.target.closest('[data-action^="lib-mgr-"]');
  if (!btn) return;
  const action = btn.dataset.action.slice(8);
  if (action === 'open') {
    openLibraryManager(btn.dataset.tab || null);
    return;
  }
  if (action === 'close') {
    $('#library-manager-dialog')?.close();
    return;
  }
  if (action === 'tab') {
    const nextTab = btn.dataset.tab === 'local' ? 'local' : 'ssh';
    libMgrState.tab = nextTab;
    renderLibraryManagerDialog();
    if (nextTab === 'ssh' && !isPublicDemo() && libMgrState.hostId && !libMgrState.cwd && !libMgrState.busy) {
      await browseLibrarySsh(libMgrState.path || '~');
    }
    return;
  }
  if (action === 'browse') {
    await browseLibrarySsh(libMgrState.path || '~');
    return;
  }
  if (action === 'cd') {
    const targetPath = btn.dataset.path;
    if (targetPath) await browseLibrarySsh(targetPath);
    return;
  }
  if (action === 'select') {
    libMgrState.selectedPath = btn.dataset.path || '';
    renderLibraryManagerDialog();
    return;
  }
  if (action === 'import-one') {
    const targetPath = btn.dataset.path || '';
    if (targetPath) await importLibrarySsh(targetPath);
    return;
  }
  if (action === 'import-selected') {
    if (libMgrState.selectedPath) await importLibrarySsh(libMgrState.selectedPath);
    return;
  }
  if (action === 'export-schematic') {
    await exportLibrarySchematic(btn.dataset.path || libMgrState.virtuosoContext?.schematic_dir || '');
    return;
  }
  if (action === 'local-commit') {
    commitLibraryLocalBundle();
    return;
  }
  if (action === 'local-clear') {
    libMgrState.localBundle = null;
    libMgrState.localSelectedEntry = '';
    libMgrState.localError = '';
    renderLibraryManagerDialog();
    return;
  }
});

document.addEventListener('dblclick', async event => {
  const btn = event.target.closest('[data-action="lib-mgr-select"]');
  if (!btn || !btn.dataset.path) return;
  await importLibrarySsh(btn.dataset.path);
});

document.addEventListener('click', event => {
  const link = event.target.closest('a[data-open-direct-run]');
  if (link) {
    const rid = link.dataset.openDirectRun || '';
    if (rid) {
      directState.selectedId = rid;
      overviewState.selectedRunId = rid;
    }
    if (location.hash === '#overview') {
      event.preventDefault();
      void refreshDirect();
    }
    return;
  }
  const newSim = event.target.closest('a[data-action="overview-new-sim"]');
  if (newSim) {
    directState.activeTab = isPublicDemo() ? 'waveforms' : 'setup';
  }
});

document.addEventListener('change', event => {
  if (event.target.id === 'overview-filter-project') {
    overviewState.project = event.target.value;
    overviewState.testbench = '';
    render();
    return;
  }
  if (event.target.id === 'overview-filter-tb') {
    overviewState.testbench = event.target.value;
    render();
    return;
  }
  if (event.target.id === 'overview-filter-status') {
    overviewState.status = event.target.value;
    render();
    return;
  }
  if (event.target.id === 'lib-mgr-host') {
    libMgrState.hostId = event.target.value;
    const h = (state.hosts || []).find(item => item.host_id === libMgrState.hostId);
    libMgrState.sshTarget = h?.ssh_target || libMgrState.hostId;
    libMgrState.path = '~';
    libMgrState.cwd = '';
    libMgrState.entries = [];
    libMgrState.cdsLibraries = [];
    void browseLibrarySsh('~');
  }
  if (event.target.id === 'lib-mgr-deps') {
    libMgrState.includeDeps = Boolean(event.target.checked);
  }
  if (event.target.id === 'lib-mgr-include-maestro') {
    libMgrState.includeMaestro = Boolean(event.target.checked);
  }
  if (event.target.id === 'lib-mgr-preserve-rnl') {
    libMgrState.preserveRnl = Boolean(event.target.checked);
  }
  if (event.target.id === 'lib-mgr-maestro-view') {
    libMgrState.selectedMaestroView = event.target.value;
    renderLibraryManagerDialog();
  }
  if (event.target.id === 'lib-mgr-local-entry') {
    libMgrState.localSelectedEntry = event.target.value;
  }
  if ((event.target.id === 'lib-mgr-local-file' || event.target.id === 'lib-mgr-local-folder') && event.target.files?.length) {
    void handleLibraryLocalFiles(event.target.files);
    event.target.value = '';
  }
});

document.addEventListener('input', event => {
  if (event.target.id === 'report-custom-prompt') {
    reportModalState.customPrompt = event.target.value;
    return;
  }
  if (event.target.id === 'overview-filter-query') {
    overviewState.query = event.target.value;
    render();
    const inp = $('#overview-filter-query');
    if (inp) { inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); }
    return;
  }
  if (event.target.id === 'lib-mgr-path') {
    libMgrState.path = event.target.value;
  }
  if (event.target.id === 'lib-mgr-filter') {
    libMgrState.filter = event.target.value;
    renderLibraryManagerDialog();
    const inp = $('#lib-mgr-filter');
    if (inp) { inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); }
  }
});

document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && reportModalState.open) {
    event.preventDefault();
    closeReportModal();
    return;
  }
  if (event.target.id === 'lib-mgr-path' && event.key === 'Enter') {
    event.preventDefault();
    void browseLibrarySsh(libMgrState.path || '~');
  }
  if (
    event.target.id === 'codex-message'
    && location.hash === '#direct'
    && event.key === 'Enter'
    && !event.shiftKey
    && !event.isComposing
    && event.keyCode !== 229
  ) {
    event.preventDefault();
    const form = $('#sim-agent-form');
    if (form && !codexPanelState.busy && codexPanelState.draft.trim()) {
      void sendPromptToSimAgent(codexPanelState.draft);
    }
  }
});

document.addEventListener('dragover', event => {
  const dz = event.target.closest('#lib-mgr-dropzone');
  if (!dz) return;
  event.preventDefault();
  dz.classList.add('is-dragover');
});

document.addEventListener('dragleave', event => {
  const dz = event.target.closest('#lib-mgr-dropzone');
  if (dz) dz.classList.remove('is-dragover');
});

document.addEventListener('drop', event => {
  const dz = event.target.closest('#lib-mgr-dropzone');
  if (!dz) return;
  event.preventDefault();
  dz.classList.remove('is-dragover');
  if (event.dataTransfer?.files?.length) void handleLibraryLocalFiles(event.dataTransfer.files);
});

// Interactive HTML Simulation Report Viewer Modal interactions
document.addEventListener('click', event => {
  if (reportModalState.open && event.target?.classList?.contains('report-modal-backdrop')) {
    closeReportModal();
    return;
  }
  const btn = event.target.closest?.('button[data-action]');
  if (!btn) return;
  const action = btn.dataset.action;
  if (action === 'overview-open-run-report') {
    event.preventDefault();
    event.stopPropagation();
    const id = btn.dataset.id || '';
    if (!id) return;
    const run = (directState.runs || []).find(r => r.run_id === id) || (directState.detail?.run_id === id ? directState.detail : null);
    const org = run ? resolveRunOrganization(run) : {project: '', testbench: ''};
    void openReportModal({scope: 'run', runId: id, project: org.project || '', testbench: org.testbench || ''});
    return;
  }
  if (action === 'overview-open-scope-report') {
    event.preventDefault();
    event.stopPropagation();
    const scope = btn.dataset.scope === 'testbench' ? 'testbench' : 'project';
    const project = btn.dataset.project || '';
    const testbench = btn.dataset.testbench || '';
    if (!project) return;
    void openReportModal({scope, project, testbench});
    return;
  }
  if (action === 'report-close') {
    closeReportModal();
    return;
  }
  if (action === 'report-refresh') {
    void fetchReportModalData();
    return;
  }
  if (action === 'report-generate') {
    event.preventDefault();
    void triggerReportModalGenerate();
    return;
  }
  if (action === 'report-download-html') {
    const htmlDoc = buildStandaloneReportHtml(reportModalState);
    if (!htmlDoc) { toast('当前暂无可下载的报告内容。'); return; }
    const blob = new Blob([htmlDoc], {type: 'text/html;charset=utf-8'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = reportDownloadFilename(reportModalState);
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast(`⬇ 已导出离线 HTML 仿真报告：${a.download}`);
    return;
  }
  if (action === 'report-open-tab') {
    const htmlDoc = buildStandaloneReportHtml(reportModalState);
    if (!htmlDoc) { toast('当前暂无可打开的报告内容。'); return; }
    const blob = new Blob([htmlDoc], {type: 'text/html;charset=utf-8'});
    const url = URL.createObjectURL(blob);
    window.open(url, '_blank', 'noopener');
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    return;
  }
});

document.addEventListener('submit', event => {
  if (event.target.id === 'report-agent-form') {
    event.preventDefault();
    void triggerReportModalGenerate();
  }
});

// Shared CircuitGit -> SpiceOwl postMessage handoff interface (spiceowl.circuitgit-entry.v1)
window.addEventListener('message', event => {
  const origin = String(event.origin || '');
  const trusted = origin === window.location.origin
    || origin === 'https://circuitgit-dev.github.io'
    || /^http:\/\/(?:127\.0\.0\.1|localhost)(?::\d+)?$/i.test(origin);
  if (!trusted) return;
  const data = event.data;
  if (!data || typeof data !== 'object') return;
  if (data.type !== 'circuitgit.spiceowl.entry' && data.schema !== 'spiceowl.circuitgit-entry.v1') return;
  const payload = data.entry && typeof data.entry === 'object' ? data.entry : data;
  if (applyCircuitGitEntry(payload)) {
    directState.activeTab = 'setup';
    if (location.hash !== '#direct') location.hash = '#direct';
    else render();
    try { event.source?.postMessage({type: 'spiceowl.circuitgit.entry.ack', ok: true}, origin); } catch { /* ignore */ }
  }
});

document.addEventListener('submit', async event => {
  if (event.target.id !== 'diagnose-form') return;
  event.preventDefault();
  if (submitting) return;
  submitting = true;
  const button = event.target.querySelector('[type=submit]'); button.disabled = true;
  let request;
  try {
    const run = currentRun();
    const input = {connection_id: selectedConnection, run_id: run.run_id, question: draftQuestion};
    const pending = pendingGet('spiceowl.pending-request');
    if (pending) {
      request = JSON.parse(pending);
      if (request.connection_id !== input.connection_id || request.run_id !== input.run_id || request.question !== input.question) throw new Error('A previous request has an unknown delivery outcome. Retry its original connection, run and question first.');
    } else {
      request = {...input, request_key: `request-${crypto.randomUUID()}`};
      pendingSet('spiceowl.pending-request', JSON.stringify(request));
    }
    const result = await api('/api/diagnose', {method: 'POST', body: request});
    pendingRemove('spiceowl.pending-request');
    selectedSession = result.session_id;
    await refresh();
    render();
  } catch (error) {
    if (error.definitive) pendingRemove('spiceowl.pending-request');
    toast(error.message);
  } finally { submitting = false; const current = $('#diagnose-form [type=submit]'); if (current) current.disabled = !selectedConnection; }
});

window.addEventListener('hashchange', async () => {
  selectedSession = null;
  try { await refresh(); render(); } catch (error) { toast(error.message); }
});
setInterval(() => { if (currentRun() && selectedSession && !document.hidden) pollSession(); }, 900);
// Only cached CircuitGit snapshots or local RuntimeMonitor state refresh; no SSH is implicit.
setInterval(() => {
  if (!isPublicDemo() && location.hash === '#live' && live.enabled && !live.busy && !live.loading && !document.hidden
      && !document.activeElement?.closest('.live-input-grid')) refreshLive();
  if (!isPublicDemo() && location.hash === '#direct' && !directState.busy && !directState.loading && !document.hidden
      && directState.monitor?.watches?.some(w => w?.watch_state === 'armed')
      && !document.activeElement?.closest('#direct-prepare-form')) refreshDirect();
}, 3000);
// Update the age label locally; this does not request another remote sample.
setInterval(() => {
  if (isPublicDemo() || location.hash !== '#hosts' || document.hidden) return;
  document.querySelectorAll('[data-resource-status]').forEach(cell => {
    const host = state.hosts.find(item => item.host_id === cell.dataset.resourceStatus);
    const status = resourceStatus(host?.last_resources), badge = cell.querySelector('.chip');
    if (badge) badge.outerHTML = chip(status.label, status.color);
  });
}, 10000);
try { await refresh(); render(); }
catch (error) { $('#main').innerHTML = `<div class="error-panel"><h2>${isPublicDemo() ? 'Could not open SpiceOwl Web.' : 'Could not open the local workspace.'}</h2><p>${escape(error.message)} ${isPublicDemo() ? 'Reload this page to try again.' : 'Refresh this page after the Connector is available.'}</p></div>`; }

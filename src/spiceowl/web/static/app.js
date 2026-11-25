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
const codexPanelState = {loaded: false, loading: false, busy: false, status: null, error: null,
  stream: null, streamState: 'idle', cursor: 0, statusCursor: 0, threads: [], selectedThread: null,
  approvals: new Map(), profile: '', model: '', draft: '', pending: null, pendingLoaded: false,
  historyTruncated: false};
const CODEX_PENDING_KEY = 'spiceowl.codex-pending';
const isStandaloneAgent = () => !isPublicDemo() && state.agent_channel?.mode === 'standalone';
let poolImportPreview = null;
let selectedHostIds = null;
let hostSelectionRevision = 0, planningBusy = false;
let live = {loaded: false, loading: false, enabled: false, projectId: '', runtime: null, error: null, busy: false, receipt: null};
let liveDraft = {configuration: '', source_commit: '', run_id: ''};
let directState = {
  loaded: false, loading: false, busy: false, runs: [], monitor: null,
  selectedId: null, detail: null, report: null, lookup: null, error: null,
  recoveryReports: [], selectedRecoveryId: null, recoveryDetail: null,
  recoveryQuery: '', recoveryOutcome: '',
};
let directDraft = {
  run_key: '', target_mode: 'host', host_id: '', backend: 'ssh_direct', simulator: 'spectre',
  output_format: 'psfascii', cpus: 2, memory_gib: 8, wall_seconds: 3600,
  queue: '', project: '', job_group: '', partition: '', account: '', qos: '', exclusive: false,
  remote_python: '/usr/bin/python3', remote_root: '.spiceowl-runs', include_dirs: '',
  netlist_text: '', lookup_key: '', allow_collect: true, recheck_resources: true,
  rec_enabled: false, rec_max_attempts: 2, rec_max_memory_gib: 64, rec_max_elapsed_min: 240,
  rec_retry_license: true, rec_license_backoff: 300, rec_retry_oom: true,
  rec_oom_multiplier: 2, rec_allow_relocate: true,
};
const LIVE_PENDING = 'spiceowl.live-pending';
const LIVE_COLLECT_PENDING = 'spiceowl.live-collect-pending';
const livePendingKey = operation => operation === 'collect' ? LIVE_COLLECT_PENDING : LIVE_PENDING;
const isPublicDemo = () => window.SpiceOwlDemo !== undefined || document.documentElement?.dataset?.publicDemo === 'true';
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

function publicDemoChrome() {
  if (!isPublicDemo()) return;
  document.body.classList.add('public-demo');
  document.title = 'SpiceOwl · Public interactive demo';
  $('#public-demo-banner').hidden = false;
  $('#public-github-link').hidden = false;
  $('#workspace-scope').textContent = 'Interactive preview';
  $('.local-tag').textContent = 'DEMO';
  $('.breadcrumb').textContent = 'Public demo';
  $('.prototype-badge').textContent = 'Synthetic · Scripted';
  $('.page-footer span').textContent = 'Browser demo · No live connections';
  const metadata = window.SpiceOwlDemo?.metadata || {};
  const commit = /^[a-f0-9]{7,40}$/i.test(metadata.commit || '') ? metadata.commit.slice(0, 8) : 'unversioned';
  const date = typeof metadata.build_date === 'string' && /^\d{4}-\d{2}-\d{2}/.test(metadata.build_date) ? metadata.build_date.slice(0, 10) : 'date unavailable';
  $('#demo-build').textContent = `Build ${commit} · ${date}`;
  if (typeof window.SpiceOwlDemo?.request !== 'function') $('[data-action="demo-reset"]').disabled = true;
  $('#demo-storage-notice').textContent = (state.demo_storage || window.SpiceOwlDemo?.storage_mode) === 'memory'
    ? 'Storage unavailable: changes reset on refresh.'
    : 'Changes stay in this browser tab; a new build or Reset clears them.';
  // Public pages offer local setup instructions, never connection or credential forms.
  $('#host-dialog')?.remove(); $('#pool-dialog')?.remove();
  $('#connection-dialog .muted').textContent = 'Try a scripted agent or model API. No provider is contacted and no credentials are needed.';
}

function publicDirectPage() {
  return heading('DIRECT EXECUTION PREVIEW', '不依赖网关，直接驱动 SSH / LSF / Slurm 仿真。', '公共演示页面不连接 SSH 或集群调度器；在本地运行 SpiceOwl 即可直接使用。') + `<section class="panel public-info" data-public-demo-info="direct"><div class="panel-body"><span class="chip">LOCAL WORKFLOW</span><h2>三步在本地控制台完成直连仿真</h2><div class="public-feature-grid"><article><h3>1. 选主机或集群</h3><p>在 Simulation hosts 注册 OpenSSH 别名后，可按 CPU/内存自动选机，或指定 LSF / Slurm 登录节点。</p></article><article><h3>2. 冻结并提交</h3><p>Prepare 仅在本地冻结网表与校验和；Stage 写入隔离目录；Launch 具备原子防重发占位，断线不盲目重提。</p></article><article><h3>3. 盯守与五维报告</h3><p>支持后台 Watch、精准 Cancel、安全收集日志与波形，自动输出五维 Spectre 报告与 SOA/OOM 证据。</p></article></div><div class="public-info-links"><a class="button primary" href="#local">Run locally</a><a class="button secondary" href="./workflow.html">Explore workflow</a></div></div></section>`;
}

async function refreshDirect() {
  if (isPublicDemo() || directState.loading) return;
  directState.loading = true;
  try {
    const data = await api('/api/direct/runs');
    directState.runs = Array.isArray(data.runs) ? data.runs : [];
    directState.monitor = data.monitor || null;
    directState.error = null;
    if ($('#direct-count')) $('#direct-count').textContent = directState.runs.length;
    if (!directState.selectedId || !directState.runs.some(r => r.run_id === directState.selectedId)) {
      directState.selectedId = directState.runs[0]?.run_id || null;
    }
    if (directState.selectedId) {
      const detail = await api(`/api/direct/runs/${encodeURIComponent(directState.selectedId)}`);
      directState.detail = detail.run;
      directState.report = detail.report || null;
    } else {
      directState.detail = null;
      directState.report = null;
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
  } catch (error) {
    directState.error = error.message;
  } finally {
    directState.loaded = true;
    directState.loading = false;
    if (location.hash === '#direct') render();
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

  return `<section class="panel direct-panel direct-rec-panel"><div class="section-title"><h2>4. 有界恢复报告中心（可搜索归档）</h2><span class="section-note">共 ${rows.length} 份报告</span></div><p class="subtle-note">所有触发过有界恢复评估或自动重提的任务谱系都会自动持久化 JSON + Markdown 报告，方便事后审计与检索。</p><div class="direct-rec-filters"><input id="direct-rec-query" value="${escape(directState.recoveryQuery)}" placeholder="按 Run ID、主机、故障类型（如 confirmed_oom / license）或摘要搜索"><select id="direct-rec-outcome"><option value="" ${directState.recoveryOutcome === '' ? 'selected' : ''}>全部结论</option><option value="recovered" ${directState.recoveryOutcome === 'recovered' ? 'selected' : ''}>已自动恢复</option><option value="in_progress" ${directState.recoveryOutcome === 'in_progress' ? 'selected' : ''}>恢复执行中</option><option value="exhausted" ${directState.recoveryOutcome === 'exhausted' ? 'selected' : ''}>预算耗尽停止</option><option value="not_recoverable" ${directState.recoveryOutcome === 'not_recoverable' ? 'selected' : ''}>不可自动恢复</option><option value="policy_disabled" ${directState.recoveryOutcome === 'policy_disabled' ? 'selected' : ''}>未开启恢复</option></select><button type="button" class="button secondary small" data-action="direct-rec-search" ${directState.busy ? 'disabled' : ''}>检索报告</button></div><div class="direct-run-list">${listHtml}</div>${detailHtml}</section>`;
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

function directPage() {
  let html = heading('DIRECT EXECUTION · SSH / LSF / SLURM', '直连仿真控制台', '不依赖 CircuitGit 网关，直接通过已注册的 OpenSSH 主机或 LSF / Slurm 集群执行、盯守并分析仿真。');
  if (isPublicDemo()) return html + publicDirectPage();
  const hostsList = state.hosts || [];
  if (!directDraft.host_id && hostsList.length) directDraft.host_id = hostsList[0].host_id;
  if (!directDraft.run_key) {
    const stamp = new Date().toISOString().replace(/[-:T.Z]/g, '').slice(0, 14);
    directDraft.run_key = `direct-${stamp}`;
  }
  const run = directState.detail || directState.runs.find(r => r.run_id === directState.selectedId) || null;
  const watchEntry = directState.monitor?.watches?.find(w => w && w.run_id === run?.run_id) || null;
  const watchArmed = watchEntry?.watch_state === 'armed';
  const terminal = ['simulator_complete_pending_measurements', 'simulator_failed', 'job_setup_failed', 'input_hash_failure'].includes(run?.state);
  const canStage = run && ['prepared', 'stage_incomplete'].includes(run.state) && !directState.busy;
  const canLaunch = run && run.state === 'staged' && !run.dispatch_claimed && !directState.busy;
  const canStatus = run && run.state !== 'prepared' && !directState.busy;
  const canWatch = run && run.dispatch_claimed && !terminal && !watchArmed && !directState.busy;
  const canUnwatch = run && watchArmed && !directState.busy;
  const canCancel = run && run.dispatch_claimed && !terminal && !directState.busy;
  const canCollect = run && terminal && run.collection_phase !== 'complete' && !directState.busy;
  const canRecover = run && terminal && run.state !== 'simulator_complete_pending_measurements' && !directState.busy;
  const recPolicy = run?.recovery_policy || {};
  const recBox = `<details class="host-form-details" ${directDraft.rec_enabled ? 'open' : ''}><summary>M4 有界自动恢复策略（不改网表与科学条件）</summary><div class="direct-check-row"><label><input id="direct-rec-enabled" type="checkbox" ${directDraft.rec_enabled ? 'checked' : ''}> 启用有界自动恢复（Watch 自动收集后按预算恢复）</label></div><div class="direct-grid"><label>最大恢复次数 (0–5)<input id="direct-rec-attempts" type="number" min="0" max="5" value="${escape(directDraft.rec_max_attempts)}" ${directDraft.rec_enabled ? '' : 'disabled'}></label><label>最大内存上限 (GiB)<input id="direct-rec-maxmem" type="number" min="1" max="16384" value="${escape(directDraft.rec_max_memory_gib)}" ${directDraft.rec_enabled ? '' : 'disabled'}></label><label>许可证退避等待 (秒)<input id="direct-rec-backoff" type="number" min="0" max="3600" value="${escape(directDraft.rec_license_backoff)}" ${directDraft.rec_enabled ? '' : 'disabled'}></label><label>总时间预算 (分钟)<input id="direct-rec-elapsed" type="number" min="1" max="10080" value="${escape(directDraft.rec_max_elapsed_min)}" ${directDraft.rec_enabled ? '' : 'disabled'}></label></div><div class="direct-check-row"><label><input id="direct-rec-lic" type="checkbox" ${directDraft.rec_retry_license ? 'checked' : ''} ${directDraft.rec_enabled ? '' : 'disabled'}> 许可证失败退避重试</label><label><input id="direct-rec-oom" type="checkbox" ${directDraft.rec_retry_oom ? 'checked' : ''} ${directDraft.rec_enabled ? '' : 'disabled'}> 确认 OOM 自动内存翻倍</label><label><input id="direct-rec-relocate" type="checkbox" ${directDraft.rec_allow_relocate ? 'checked' : ''} ${directDraft.rec_enabled ? '' : 'disabled'}> 允许迁移至更大内存主机</label></div></details>`;

  const schedBox = directDraft.backend === 'lsf'
    ? `<div class="direct-sched-box"><strong>LSF 集群参数（bsub）</strong><div class="direct-grid"><label>Queue（必填）<input id="direct-queue" value="${escape(directDraft.queue)}" placeholder="analog_long" required></label><label>Project（可选）<input id="direct-project" value="${escape(directDraft.project)}" placeholder="adc_proj"></label><label class="direct-wide">Job group（可选）<input id="direct-job-group" value="${escape(directDraft.job_group)}" placeholder="/analog/sim"></label></div><div class="direct-check-row"><label><input id="direct-exclusive" type="checkbox" ${directDraft.exclusive ? 'checked' : ''}> 独占计算节点 (-x)</label></div></div>`
    : directDraft.backend === 'slurm'
    ? `<div class="direct-sched-box"><strong>Slurm 集群参数（sbatch）</strong><div class="direct-grid"><label>Partition（必填）<input id="direct-partition" value="${escape(directDraft.partition)}" placeholder="sim_compute" required></label><label>Account（可选）<input id="direct-account" value="${escape(directDraft.account)}" placeholder="analog_lab"></label><label class="direct-wide">QoS（可选）<input id="direct-qos" value="${escape(directDraft.qos)}" placeholder="normal"></label></div><div class="direct-check-row"><label><input id="direct-exclusive" type="checkbox" ${directDraft.exclusive ? 'checked' : ''}> 独占计算节点 (--exclusive)</label></div></div>`
    : '';

  html += `<div class="direct-workspace"><section class="panel direct-panel"><div class="section-title"><h2>1. 冻结并准备实验</h2><span class="chip">PREPARE · NO LAUNCH</span></div><p class="subtle-note">仅在本地冻结网表与配置，不连接服务器、不启动求解器。${!hostsList.length ? '请先前往 <a href="#hosts">Simulation hosts</a> 注册至少一台 SSH 主机。' : ''}</p><form id="direct-prepare-form"><div class="direct-grid"><label class="direct-wide">运行标识（Run Key / 幂等键）<input id="direct-run-key" value="${escape(directDraft.run_key)}" required maxlength="128" pattern="[A-Za-z0-9][A-Za-z0-9_\\-]*" placeholder="direct-inv-001"></label><label>执行后端<select id="direct-backend"><option value="ssh_direct" ${directDraft.backend === 'ssh_direct' ? 'selected' : ''}>SSH 直连托管进程</option><option value="lsf" ${directDraft.backend === 'lsf' ? 'selected' : ''}>LSF 集群 (bsub)</option><option value="slurm" ${directDraft.backend === 'slurm' ? 'selected' : ''}>Slurm 集群 (sbatch)</option></select></label><label>仿真器<select id="direct-simulator"><option value="spectre" ${directDraft.simulator === 'spectre' ? 'selected' : ''}>Spectre</option><option value="ngspice" ${directDraft.simulator === 'ngspice' ? 'selected' : ''}>ngspice</option></select></label><label>主机选择方式<select id="direct-target-mode"><option value="host" ${directDraft.target_mode === 'host' ? 'selected' : ''}>指定单台主机</option><option value="pool" ${directDraft.target_mode === 'pool' ? 'selected' : ''}>按资源自动推荐（所有已登记主机）</option></select></label><label>目标主机<select id="direct-host-id" ${directDraft.target_mode === 'pool' || !hostsList.length ? 'disabled' : ''}>${hostsList.length ? hostsList.map(h => `<option value="${escape(h.host_id)}" ${h.host_id === directDraft.host_id ? 'selected' : ''}>${escape(h.label)} (${escape(h.host_id)})</option>`).join('') : '<option value="">尚未注册主机</option>'}</select></label><label>CPU 核数<input id="direct-cpus" type="number" min="1" max="4096" value="${escape(directDraft.cpus)}" required></label><label>内存 (GiB)<input id="direct-memory" type="number" min="0.1" step="any" max="16384" value="${escape(directDraft.memory_gib)}" required></label><label>最大墙钟时长 (秒)<input id="direct-wall" type="number" min="10" max="604800" value="${escape(directDraft.wall_seconds)}" required></label><label>波形格式<select id="direct-format"><option value="psfascii" ${directDraft.output_format === 'psfascii' ? 'selected' : ''}>PSF ASCII</option><option value="psfbin" ${directDraft.output_format === 'psfbin' ? 'selected' : ''}>PSF Binary</option><option value="psfxl" ${directDraft.output_format === 'psfxl' ? 'selected' : ''}>PSFXL</option></select></label></div>${schedBox}${recBox}<details class="host-form-details"><summary>高级远端路径设置（可选）</summary><div class="direct-grid"><label>远端 Python3 路径<input id="direct-python" value="${escape(directDraft.remote_python)}"></label><label>远端工作根目录<input id="direct-root" value="${escape(directDraft.remote_root)}" placeholder=".spiceowl-runs 或 /scratch/..."></label><label class="direct-wide">Spectre Include 目录（每行一个绝对路径）<textarea id="direct-includes" rows="2" placeholder="/opt/pdk/models">${escape(directDraft.include_dirs)}</textarea></label></div></details><div class="direct-file-row"><label for="direct-netlist">网表内容（input.scs）</label><label class="direct-file-label">从本地文件读取 (.scs/.sp)<input id="direct-netlist-file" type="file" accept=".scs,.sp,.cir,.net,.txt"></label></div><textarea id="direct-netlist" class="direct-netlist" required placeholder="simulator lang=spectre&#10;...">${escape(directDraft.netlist_text)}</textarea><div class="direct-actions"><button class="button primary" type="submit" ${directState.busy || !hostsList.length ? 'disabled' : ''}>冻结并准备计划（Prepare）</button></div></form></section>`;

  html += `<div class="detail-stack"><section class="panel direct-panel"><div class="section-title"><h2>2. 直连运行记录 (${directState.runs.length})</h2><button class="button secondary small" data-action="direct-refresh" ${directState.busy ? 'disabled' : ''}>刷新列表</button></div>${directState.error ? `<p class="error-text" role="alert">${escape(directState.error)}</p>` : ''}<div class="direct-run-list">${directState.runs.map(item => `<button type="button" class="direct-run-row ${item.run_id === run?.run_id ? 'selected' : ''}" data-action="direct-select" data-id="${escape(item.run_id)}"><div><strong>${escape(item.run_id)}</strong><small>${escape(item.backend.toUpperCase())} · ${escape(item.simulator)} · ${escape(item.host_id)}${item.scheduler?.job_id ? ' · Job ' + escape(item.scheduler.job_id) : ''}</small></div>${directRunChip(item)}</button>`).join('') || '<p class="subtle-note">还没有直连运行记录。先在左侧准备一个实验。</p>'}</div><div class="direct-lookup"><input id="direct-lookup-key" value="${escape(directDraft.lookup_key)}" placeholder="按原 Run Key / Submission Key 对账查询"><button type="button" class="button secondary small" data-action="direct-lookup" ${directState.busy ? 'disabled' : ''}>查询原 Key</button></div>${directState.lookup ? `<p class="subtle-note" role="status">${directState.lookup.found ? `已找到原记录：<code>${escape(directState.lookup.run_id)}</code>（状态：${escape(title(directState.lookup.run?.state))}）` : escape(directState.lookup.note)}</p>` : ''}</section>`;

  if (run) {
    html += `<section class="panel direct-panel"><div class="section-title"><h2>3. 任务控制 · ${escape(run.run_id)}</h2>${directRunChip(run)}</div><p class="subtle-note">第 <strong>#${escape(run.attempt_number || 1)}</strong> 次尝试（根任务：<code>${escape(run.root_run_id || run.run_id)}</code>${run.parent_run_id ? ` · 恢复自 <code>${escape(run.parent_run_id)}</code>` : ''}）· 有界恢复：<strong>${recPolicy.enabled ? `已启用（最多 ${recPolicy.max_recovery_attempts} 次 / 上限 ${((recPolicy.max_memory_bytes || 0) / 2**30).toFixed(0)} GiB）` : '未启用'}</strong><br>后端：<strong>${escape(run.backend)}</strong> · 主机：<strong>${escape(run.host_id)}</strong> · 仿真器：<strong>${escape(run.simulator)}</strong>${run.expected_version ? ` (${escape(run.expected_version)})` : ''}${run.scheduler?.job_id ? ` · 调度 Job ID：<code>${escape(run.scheduler.job_id)}</code>` : ''}<br>冻结摘要：<code>${escape(run.manifest_sha256.slice(0, 16))}…</code> · 最近观察：${escape(run.observed_at ? new Date(run.observed_at).toLocaleString() : '尚未联网')}</p>${run.state === 'launch_outcome_unknown' ? '<p class="denial">提交回执未知：已锁定原 Key，禁止盲目重提。请点击「查询状态」与远端对账。</p>' : ''}${run.oom_evidence?.oom_kill ? '<p class="denial">调度器已确认该作业触发 OOM（内存耗尽）。</p>' : ''}${run.last_error ? `<p class="error-text">${escape(run.last_error.message || run.last_error.code)}</p>` : ''}<div class="direct-check-row"><label><input id="direct-recheck" type="checkbox" ${directDraft.recheck_resources ? 'checked' : ''} ${run.backend !== 'ssh_direct' ? 'disabled' : ''}> 启动前复查主机余量（仅 ssh_direct）</label><label><input id="direct-allow-collect" type="checkbox" ${directDraft.allow_collect ? 'checked' : ''}> Watch 确认终态后自动收集并生成报告</label></div><div class="direct-actions"><button type="button" class="button secondary" data-action="direct-stage" data-id="${escape(run.run_id)}" ${canStage ? '' : 'disabled'}>1. 上传输入 (Stage)</button><button type="button" class="button primary" data-action="direct-launch" data-id="${escape(run.run_id)}" ${canLaunch ? '' : 'disabled'}>2. 启动仿真 (Launch)</button><button type="button" class="button secondary" data-action="direct-status" data-id="${escape(run.run_id)}" ${canStatus ? '' : 'disabled'}>查询状态 (Status)</button><button type="button" class="button secondary" data-action="direct-watch" data-id="${escape(run.run_id)}" ${canWatch ? '' : 'disabled'}>开启后台盯守 (Watch)</button><button type="button" class="button secondary" data-action="direct-unwatch" data-id="${escape(run.run_id)}" ${canUnwatch ? '' : 'disabled'}>停止盯守</button><button type="button" class="button secondary" data-action="direct-cancel" data-id="${escape(run.run_id)}" ${canCancel ? '' : 'disabled'}>取消作业 (Cancel)</button><button type="button" class="button primary" data-action="direct-collect" data-id="${escape(run.run_id)}" ${canCollect ? '' : 'disabled'}>3. 收集结果与报告 (Collect)</button><button type="button" class="button secondary" data-action="direct-toggle-policy" data-id="${escape(run.run_id)}" ${directState.busy ? 'disabled' : ''}>${recPolicy.enabled ? '关闭有界恢复' : '开启有界恢复'}</button><button type="button" class="button primary" data-action="direct-recover" data-id="${escape(run.run_id)}" ${canRecover ? '' : 'disabled'}>执行有界恢复 (Recover)</button></div>${watchEntry ? `<p class="subtle-note">后台盯守：<strong>${escape(title(watchEntry.watch_state))}</strong> · 通信：<strong>${escape(title(watchEntry.observation?.state))}</strong> · 下次轮询：${escape(watchEntry.observation?.next_check_at || '无')}</p>` : ''}${run.collection ? `<p class="subtle-note">已收集 ${run.collection.file_count} 个文件（${Math.round((run.collection.total_bytes || 0) / 1024)} KiB）${run.collection.collection_complete ? ' · 完整校验通过' : ` · ${run.collection.skipped_files.length} 个超大原始文件已跳过并保留日志`}</p>` : ''}${directReportSection(directState.report)}</section>`;
  }
  html += '</div></div>';
  html += directRecoveryReportsPanel();
  return html;
}

async function handleDirectClick(event) {
  const button = event.target.closest('button[data-action^="direct-"]');
  if (!button) return;
  if (isPublicDemo()) { location.hash = '#local'; return; }
  const action = button.dataset.action.slice(7);
  const id = button.dataset.id;
  if (action === 'use-placement') {
    directDraft.target_mode = 'host';
    directDraft.host_id = id;
    directDraft.cpus = requestedCpus;
    directDraft.memory_gib = requestedMemory;
    directDraft.simulator = requestedSimulator;
    location.hash = '#direct';
    return;
  }
  if (action === 'refresh') { await refreshDirect(); return; }
  if (action === 'select') {
    directState.selectedId = id;
    await refreshDirect();
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

function publicLocalPage() {
  return heading('RUN LOCALLY', 'Bring the console to your workspace.', 'Use your local installation for private host profiles and saved simulation observations.') + `<section class="panel public-info" data-public-demo-info="local"><div class="panel-body"><h2>Start with the synthetic workspace</h2><p>Requires Python 3.11+ on macOS, Linux, or Windows. Use the branch that supplies this demo, then run these commands in a terminal on your own computer:</p><pre class="public-command"># macOS / Linux
git clone --branch ${escape(demoSource().branch)} https://github.com/spiceowl-dev/spiceowl.git
cd spiceowl
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/spiceowl serve --demo

# Windows (PowerShell)
py -3 -m venv .venv
.venv\\Scripts\\python.exe -m pip install .
.venv\\Scripts\\spiceowl.exe serve --demo</pre><p>Open the loopback address printed by that command. This public page never connects to that address or to your computer.</p><h3>Connect only when you choose</h3><p>The local console can register an existing SSH route, make explicit host checks and connect a configured CircuitGit simulation runtime. Standalone mode offers an explicit local Codex connection. Embedded mode shares CircuitGit’s current Codex conversation.</p><div class="public-info-links"><a class="button secondary" href="${demoDocument('docs/local-console.md')}" target="_blank" rel="noopener noreferrer">Local setup guide</a><a class="button secondary" href="${demoDocument('docs/host-onboarding.md')}" target="_blank" rel="noopener noreferrer">Host connection guide</a><a class="button secondary" href="#overview">Back to the demo</a></div></div></section>`;
}

function publicHostPage() {
  return heading('LOCAL FEATURE PREVIEW', 'Connect servers in your local console.', 'This public demo has no SSH access and does not scan, register or contact hosts.') + `<section class="panel public-info" data-public-demo-info="hosts"><div class="panel-body"><span class="chip">ILLUSTRATIVE WORKFLOW</span><h2>Your SSH configuration stays with you</h2><div class="public-feature-grid"><article><h3>Register a route</h3><p>Choose an existing SSH alias and record the expected remote identity in a private local profile.</p></article><article><h3>Check explicitly</h3><p>Verify host trust, account identity and configured simulator versions. A saved profile alone proves no connection.</p></article><article><h3>Compare observations</h3><p>Inspect CPU and memory snapshots with their observation times. Missing data never means an idle machine.</p></article></div><p>No hostnames, login details, SSH keys or credentials are requested on this page.</p><a class="button primary" href="#local">Run locally to configure hosts</a></div></section>`;
}

function publicAgentPage() {
  const firstRun = state.runs[0];
  return heading('SYNTHETIC AGENT PREVIEW', 'One workflow, two ways to use it.', 'This public demo makes no model, Agent or simulator connection.') + `<section class="panel public-info" data-public-demo-info="agents"><div class="panel-body"><h2>Standalone or inside CircuitGit</h2><div class="public-feature-grid"><article><h3>Standalone console</h3><p>In a local installation, choose a Codex profile and model, connect explicitly, then converse beside your simulation tools. Streams, tool calls and human approvals stay in the same panel.</p></article><article><h3>Embedded in CircuitGit</h3><p>Use CircuitGit’s current Codex conversation. The embedded console does not create a second Agent connection or conversation.</p></article></div><blockquote class="public-example"><span class="chip">SCRIPTED PREVIEW</span><p><strong>You:</strong> Inspect this saved simulation run.</p><p><strong>Agent:</strong> I can read its stored status, review the evidence and show an approval card if an operation needs your decision.</p></blockquote><p>The preview above is authored example text. No request is sent from this public page.</p><div class="public-info-links"><a class="button primary" href="./workflow.html">Explore the workflow</a><a class="button secondary" href="${circuitGitEntry}" target="_blank" rel="noopener noreferrer">Open CircuitGit</a>${firstRun ? `<a class="button secondary" href="#run/${escape(firstRun.run_id)}">Try a scripted diagnosis</a>` : '<a class="button secondary" href="#overview">Explore synthetic runs</a>'}<a class="button secondary" href="#local">Run locally</a></div></div></section>`;
}

function toast(message) {
  clearTimeout(toastTimer);
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 6000);
}

async function api(path, options = {}) {
  if (isPublicDemo()) {
    if (typeof window.SpiceOwlDemo?.request !== 'function') throw new Error('The public demo could not load. Reload this page to try again.');
    // This explicit adapter owns demo requests; errors never fall back to a live API.
    return window.SpiceOwlDemo.request(path, options);
  }
  const method = options.method || 'GET';
  const response = await fetch(path, {method, headers: {'Content-Type': 'application/json', ...(method !== 'GET' ? {'X-SpiceOwl-Token': csrf} : {})}, ...(method !== 'GET' ? {body: JSON.stringify(options.body || {})} : {})});
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error?.message || 'The local request failed.');
    error.code = data.error?.code || 'request_failed';
    error.definitive = response.status < 500;
    throw error;
  }
  return data;
}

async function refresh() {
  publicDemoChrome();
  state = await api('/api/state');
  publicDemoChrome();
  csrf = state.csrf_token;
  $('#version').textContent = state.version;
  $('#connection-count').textContent = state.connections.length;
  $('#host-count').textContent = state.hosts.length;
  if ($('#direct-count')) $('#direct-count').textContent = isPublicDemo() ? '0' : directState.runs.length;
  $('#native-count').textContent = isStandaloneAgent() ? codexPanelState.threads.length : state.questions.length;
  if (!isPublicDemo()) $('.prototype-badge').textContent = isStandaloneAgent() ? 'Standalone · Codex Agent' : 'CircuitGit channel · Engine';
  $('#recent-runs').innerHTML = state.runs.slice(0, 5).map(run => `<a href="#run/${escape(run.run_id)}"><i class="dot"></i><span class="run-title">${escape(run.label)}</span></a>`).join('');
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

function overview() {
  const total = state.runs.length;
  const running = state.runs.filter(r => r.execution === 'running' || r.execution === 'queued').length;
  const attention = state.runs.filter(r => r.observation !== 'current' || r.execution === 'failed' || r.specification_result === 'fail').length;
  const done = state.runs.filter(r => r.execution === 'completed').length;
  const stats = [['Total runs', total, 'Stored synthetic experiments', '▦'], ['Last observed active', running, 'Check observation freshness', '◷'], ['Needs attention', attention, 'Evidence to inspect', '◇'], ['Execution completed', done, 'Validity is reported separately', '✓']];
  const overviewAction = isPublicDemo()
    ? state.runs[0] ? `<a class="button primary" href="#run/${escape(state.runs[0].run_id)}">Explore a scenario</a>` : '<button class="button primary" data-action="demo-reset">Reset demo</button>'
    : '<button class="button primary" data-action="seed"><span aria-hidden="true">＋</span> Load demo workspace</button>';
  let html = heading('YOUR WORKSPACE', 'A clear view of every run.', 'Follow the evidence. Keep the experiment intact.', overviewAction);
  if (!total) {
    return html + `<div class="empty"><img src="./owl.svg" alt=""><h1>Your first night watch starts here.</h1><p>Explore connections, interrupted runs and diagnostic proposals with an offline demo. No accounts, API keys or simulator needed.</p><button class="button primary" data-action="seed">Load the synthetic demo <span aria-hidden="true">→</span></button></div>`;
  }
  html += `<div class="stat-grid">${stats.map(([label, value, note, glyph]) => `<div class="stat-card"><div class="stat-label">${label}<span class="stat-glyph" aria-hidden="true">${glyph}</span></div><div class="stat-value">${value}</div><div class="stat-note">${note}</div></div>`).join('')}</div>`;
  html += `<div class="watch-banner"><img src="./owl.svg" alt=""><div><h3>Explore the overnight workflow.</h3><p>Advance a synthetic run, inspect its evidence, then ask a demo agent for a read-only recommendation.</p></div><a class="button secondary small" href="#connections">Manage connections <span aria-hidden="true">→</span></a></div>`;
  html += `<div class="section-title"><h2>Simulation runs <span class="muted">/ ${total}</span></h2><span class="section-note">Manual replay · saved locally</span></div><div class="run-grid">`;
  html += state.runs.map(run => {
    const pct = Number.isFinite(run.progress) ? Math.round(run.progress * 100) : null;
    return `<a class="run-card" href="#run/${escape(run.run_id)}"><div class="card-top"><span class="chip">SYNTHETIC / TRANSIENT</span>${runStatus(run)}</div><h3>${escape(run.label)}</h3><div class="run-meta">${escape(run.attempt_id)}</div><div class="progress-row"><span>Scenario progress</span><span>${pct === null ? 'Unknown' : pct + '%'}</span></div><progress class="progress" max="100" value="${pct ?? 0}" aria-label="Scenario progress"></progress><div class="run-card-footer"><span>${run.events.length} evidence events · ${escape(title(run.measurement_validity))} measurement</span><strong>Inspect run <span class="arrow" aria-hidden="true">↗</span></strong></div></a>`;
  }).join('') + '</div>';
  return html;
}

function connections() {
  let html = heading('AI CONNECTIONS', 'Try simulated connections.', 'Explore scripted readiness, access-denied and interrupted-response states. No provider is contacted.', `<button class="button primary" data-action="add-connection"><span aria-hidden="true">＋</span> Add demo connection</button>`);
  if (!state.connections.length) {
    html += `<div class="empty"><h2>No connections yet.</h2><p>Create a simulated agent or API connection to explore authentication states, streaming and failure handling.</p><button class="button primary" data-action="add-connection">Create demo connection</button></div>`;
  }
  html += `<div class="connections-list">${state.connections.map(c => {
    const status = c.status || {};
    const fields = [['Transport', status.transport || 'unchecked'], ['Authentication', status.authentication || 'unchecked'], ['Model catalog', status.catalog || 'unchecked'], ['Inference access', status.inference_access || 'unverified']];
    return `<article class="panel connection-card"><div class="connection-main"><div class="provider-icon" aria-hidden="true">${c.kind === 'agent' ? '✧' : '⌁'}</div><div class="connection-name"><h3>${escape(c.label)} ${chip(c.enabled ? 'enabled' : 'disabled', c.enabled ? 'green' : '')}</h3><p>${c.kind === 'agent' ? 'Agent runtime' : 'Model API'} · Offline fixture · ${escape(title(c.scenario))}</p></div><div class="connection-actions"><button class="button secondary small" data-action="probe" data-id="${escape(c.connection_id)}" ${c.enabled ? '' : 'disabled'}>Probe metadata</button><button class="button secondary small" data-action="toggle" data-id="${escape(c.connection_id)}">${c.enabled ? 'Disable' : 'Enable'}</button><button class="button secondary small" data-action="remove" data-id="${escape(c.connection_id)}" aria-label="Remove ${escape(c.label)}">Remove</button></div></div><div class="connection-readiness">${fields.map(([name, value]) => `<div class="readiness-field"><span>${name}</span><strong>${escape(title(value))}</strong></div>`).join('')}</div><div class="connection-bottom"><span>All statuses describe a simulated backend.</span><span>Revision ${c.revision} · No credentials</span></div></article>`;
  }).join('')}</div>`;
  if (isPublicDemo()) return html + `<div class="planned-note"><strong>Continue agent work in CircuitGit</strong><p>This page only simulates connections. Open the <a href="#agents">CircuitGit agent channel</a> for its entry link, or <a href="#local">run SpiceOwl locally</a> to manage simulation observations.</p></div>`;
  return html + `<div class="planned-note"><strong>CircuitGit agent channel</strong><p>These connection cards use fixture responses. The <a href="#agents">Agent panel</a> connects explicitly to Codex in standalone mode, or shares CircuitGit’s conversation in embedded mode. Legacy question history stays read only.</p></div>`;
}

function hosts() {
  if (isPublicDemo()) return publicHostPage();
  syncHostSelection();
  const count = selectedHostIds?.size || 0;
  let html = heading('SIMULATION HOSTS', 'Your simulation server pool.', 'Register your servers, verify each connection, then compare saved resource observations.', `<div class="host-page-actions"><button class="button secondary" data-action="add-host">＋ Add host</button><button class="button secondary" data-action="import-pool" ${!state.hosts.length ? 'disabled' : ''}>Import pool</button><button class="button primary" data-action="pool-scan" ${poolScanning || !count || count > 32 ? 'disabled' : ''}>${poolScanning ? 'Checking selected hosts…' : 'Check selected hosts'}</button></div>`);
  html += `<div class="host-onboarding"><div><strong>1. Register</strong><span>Add an existing SSH alias or import a list using a route template.</span></div><div><strong>2. Verify</strong><span>Check the route, SSH trust and remote identity for each host.</span></div><div><strong>3. Check resources</strong><span>Run a pool check when you need fresh CPU and memory observations.</span></div></div>`;
  html += poolOverview();
  if (!state.hosts.length) html += `<div class="empty"><h2>No simulation hosts registered.</h2><p>Add a host to start. Registration saves local configuration; Check connection is a separate action.</p><button class="button primary" data-action="add-host">Add your first host</button></div>`;
  html += `<details class="host-details" ${state.hosts.length <= 1 ? 'open' : ''}><summary>Host identity and tool versions</summary><div class="connections-list">${state.hosts.map(host => {
    const probe = host.last_probe;
    const evidence = connectionEvidence(host), status = connectionStatus(evidence);
    const fields = [['SSH transport', evidence?.transport || 'unchecked'], ['Host identity', evidence?.identity?.verified === true ? 'matched at last check' : 'unverified'], ['Tool check', observationTime(probe)], ['Simulation readiness', 'not tested']];
    const toolRows = host.tools.map(tool => {
      const result = probe?.tools?.find(item => item.kind === tool.kind);
      return `<div class="host-tool"><strong>${escape(title(tool.kind))}</strong>${chip(result?.status || 'unchecked', result?.status === 'available' ? 'green' : result ? 'amber' : '')}<span class="host-version">${escape(result?.version || 'Version unverified')}</span>${result?.error ? `<p class="error-text">${escape(result.error.message)}</p>` : ''}</div>`;
    }).join('');
    return `<article class="panel connection-card"><div class="connection-main"><div class="provider-icon" aria-hidden="true">▤</div><div class="connection-name"><h3>${escape(host.label)} ${chip(status.label, status.color)}</h3><p>Registered host · ${escape(host.host_id)}</p></div><div class="connection-actions"><button class="button primary small" data-action="host-probe" data-id="${escape(host.host_id)}">Check connection</button></div></div><div class="connection-readiness">${fields.map(([label, value]) => `<div class="readiness-field"><span>${label}</span><strong>${escape(title(value))}</strong></div>`).join('')}</div><div class="host-tools">${toolRows || '<p class="subtle-note">No simulator executable configured. Connection checks verify identity only.</p>'}</div>${status.help ? `<div class="host-error">${escape(status.help)}</div>` : ''}<div class="connection-bottom"><span>Connection evidence: ${escape(observationTime(evidence))}</span><span>Saved result · no automatic remote polling</span></div></article>`;
  }).join('')}</div></details>`;
  return html + `<div class="planned-note"><strong>Plans preserve simulation ownership.</strong><p>A new standalone job can receive a proposed host. An existing Virtuoso-managed run retains its original host and native run reference. These plans do not reserve resources or launch a simulation; input, environment and license checks still belong to submission.</p></div>`;
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
  for (const approval of status.pending_approvals || []) {
    if (approval && typeof approval.approval_id === 'string') codexPanelState.approvals.set(approval.approval_id, approval);
  }
  if (!status.recovery_required) confirmCodexPending(status.request_key, status.turn_id);
}

function closeCodexEvents() {
  codexPanelState.stream?.close();
  codexPanelState.stream = null;
  codexPanelState.streamState = 'idle';
}

function openCodexEvents() {
  if (!isStandaloneAgent() || location.hash !== '#agents' || codexPanelState.stream
      || (!codexPanelState.status?.connected && (!codexPanelState.status?.cursor
        || codexPanelState.cursor >= codexPanelState.status.cursor))) return;
  const stream = new EventSource(`/api/codex/events?after=${codexPanelState.cursor}`);
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
  } else if (event.method === 'approval/required' && typeof params.approval_id === 'string') {
    if (liveEvent || !codexPanelState.approvals.has(params.approval_id)) {
      codexPanelState.approvals.set(params.approval_id, {...params, thread_id: params.thread_id || threadId});
    }
  } else if (event.method === 'approval/resolved') {
    const approval = codexPanelState.approvals.get(params.approval_id);
    const currentApproval = status.pending_approvals?.some(item => item.approval_id === params.approval_id);
    if (approval && (liveEvent || !currentApproval)) Object.assign(approval, params, {can_approve: false, can_deny: false});
  }
  if (liveEvent) {
    if (event.method === 'bridge/connected') Object.assign(status, {connected: true, state: 'connected'});
    if (event.method === 'bridge/thread') {
      const id = threadId || params.thread_id;
      Object.assign(status, {thread_id: id, turn_id: null, state: 'ready'});
      codexThread(id); codexPanelState.selectedThread = id;
    }
    if (event.method === 'turn/started') Object.assign(status, {thread_id: threadId, turn_id: turnId,
      request_key: event.request_key, state: 'running'});
    if (event.method === 'turn/completed') Object.assign(status, {state: params.turn?.status === 'completed' ? 'complete' : 'failed'});
    if (event.method === 'approval/required') Object.assign(status, {state: 'awaiting_approval'});
    if (event.method === 'approval/resolved' && status.state === 'awaiting_approval') {
      const unknown = params.status === 'unknown';
      Object.assign(status, {state: unknown ? 'unknown' : 'running', recovery_required: unknown});
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
  if (!codexPanelState.selectedThread && threadId) codexPanelState.selectedThread = threadId;
}

async function refreshCodexStatus() {
  if (!isStandaloneAgent() || codexPanelState.loading) return;
  codexPanelState.loading = true;
  loadCodexPending();
  try {
    setCodexStatus(await api('/api/codex/status'));
    codexPanelState.error = null;
    openCodexEvents();
  } catch (error) { codexPanelState.error = error.message; codexPanelState.loaded = true; }
  finally { codexPanelState.loading = false; if (location.hash === '#agents') render(); }
}

function codexApprovalCard(approval) {
  const current = codexPanelState.status;
  const editable = !codexPanelState.busy && current?.connected && !current.recovery_required
    && current.state === 'awaiting_approval' && codexPanelState.streamState === 'connected' && approval.status === 'pending'
    && approval.thread_id === current.thread_id && approval.thread_id === codexPanelState.selectedThread
    && !!current.turn_id && approval.turn_id === current.turn_id;
  const fullDetails = JSON.stringify(approval.params ?? null, null, 2);
  const preview = approval.preview_only === true || fullDetails.length > 65536;
  const approve = editable && approval.can_approve === true && !preview;
  const deny = editable && approval.can_deny === true;
  return `<article class="codex-approval" data-approval-id="${escape(approval.approval_id)}"><div class="codex-card-heading"><strong>Human approval</strong>${chip(approval.status || 'pending', 'amber')}</div><p>${escape(approval.summary || 'Review this exact requested action.')}</p><details open><summary>Requested action</summary><pre>${escape(codexText(approval.params, 65536))}</pre></details>${preview ? `<p class="subtle-note">This is an incomplete or redacted preview. Approval stays disabled until complete reviewable details are available.</p><button class="button secondary small" data-action="codex-details" data-id="${escape(approval.approval_id)}">Read frozen action details</button>` : ''}<div class="codex-approval-actions"><button class="button primary small" data-action="codex-approve" data-id="${escape(approval.approval_id)}" ${approve ? '' : 'disabled'}>Approve this action</button><button class="button secondary small" data-action="codex-deny" data-id="${escape(approval.approval_id)}" ${deny ? '' : 'disabled'}>Deny</button></div>${approval.status === 'unknown' ? '<p class="subtle-note">Approval outcome is unknown. This action will not be retried automatically.</p>' : ''}</article>`;
}

function renderCodexActivity() {
  const timeline = $('#codex-timeline');
  if (!timeline || !isStandaloneAgent()) return;
  const thread = codexPanelState.threads.find(item => item.id === codexPanelState.selectedThread);
  const nearBottom = timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight < 80;
  timeline.innerHTML = (thread?.items || []).map(item => item.type === 'tool'
    ? `<details class="codex-tool" open><summary><strong>${escape(item.name || 'Tool call')}</strong>${chip(item.status)}</summary>${item.preview ? '<p class="subtle-note">Retained preview · incomplete tool result. The operation status does not establish that the full result is displayed.</p>' : ''}<pre>${escape(item.text)}</pre>${item.ownerReview ? '<p class="subtle-note">This operation needs the execution owner’s human review. Open the review page supplied in the result. A Codex tool approval does not approve that separate operation.</p>' : ''}</details>`
    : `<article class="codex-message codex-${item.type}"><div>${item.type === 'user' ? 'You' : 'Codex'}${item.status === 'unconfirmed' ? ' · delivery unconfirmed' : ''}</div>${item.preview ? '<p class="subtle-note">Retained preview · incomplete message.</p>' : ''}<p>${escape(item.text || 'Waiting for response text…')}</p></article>`).join('')
    || '<div class="codex-chat-empty"><strong>Your simulation workspace, in conversation.</strong><p>Connect explicitly, create a conversation, then send a message. Tool requests and any required human approvals appear here.</p></div>';
  if (nearBottom) timeline.scrollTop = timeline.scrollHeight;
  const status = codexPanelState.status;
  $('#native-count').textContent = codexPanelState.threads.length;
  $('#native-count').title = 'Conversations received in this browser';
  $('#codex-runtime-state').innerHTML = chip(status?.state || 'disconnected', status?.connected ? 'green' : '');
  $('#codex-stream-state').textContent = codexPanelState.streamState === 'reconnecting'
    ? 'Updates interrupted; reconnecting the read-only event stream. Messages are not resent.'
    : codexPanelState.streamState === 'connected' ? 'Live event stream' : 'No live event stream';
  $('#codex-approvals').innerHTML = [...codexPanelState.approvals.values()]
    .filter(item => item.thread_id === codexPanelState.selectedThread).map(codexApprovalCard).join('');
  const pending = codexPanelState.pending;
  $('#codex-notices').innerHTML = `${codexPanelState.error ? `<p class="error-text" role="alert">${escape(codexPanelState.error)}</p>` : ''}${pending ? `<p class="denial">A previous send is unconfirmed. Its request key is preserved: <code>${escape(pending.request_key)}</code>. Refresh status to inspect it; it will not be resent automatically.</p>` : ''}${status?.recovery_required ? `<p class="denial">The Agent has an unresolved operation. Preserve its conversation and request identity; new conversations and messages are blocked. Request: <code>${escape(status.request_key || 'not returned')}</code>.</p>` : ''}${codexPanelState.historyTruncated || thread?.truncated ? '<p class="subtle-note">Only the retained event history is shown; earlier content is unavailable in this view.</p>' : ''}${thread && thread.id !== status?.thread_id ? '<p class="subtle-note">Historical conversation · read only. Select the current conversation to send a message.</p>' : ''}`;
  $('#codex-session').innerHTML = codexPanelState.threads.length ? codexPanelState.threads.map(item => `<option value="${escape(item.id)}" ${item.id === codexPanelState.selectedThread ? 'selected' : ''}>${item.id === status?.thread_id ? 'Current' : 'History'} · ${escape(item.id)}</option>`).join('') : '<option value="">No conversation yet</option>';
  $('#codex-send').disabled = !codexCanSend() || !codexPanelState.draft.trim();
  $('#codex-message').disabled = !codexCanSend();
  $('[data-action="codex-thread"]').disabled = codexPanelState.busy || !status?.connected || codexPanelState.streamState !== 'connected' || !!status?.recovery_required || codexActiveTurn() || !!pending;
  $('[data-action="codex-disconnect"]').disabled = codexPanelState.busy || !status?.connected;
  const connect = $('#codex-connect-form [type="submit"]');
  connect.disabled = codexPanelState.busy || !!status?.connected || !!status?.recovery_required;
  connect.textContent = status?.connected ? 'Codex connected' : codexPanelState.busy ? 'Connecting…' : 'Connect Codex';
}

function standaloneAgentPanel() {
  const status = codexPanelState.status;
  return `<div class="codex-panel" data-agent-mode="standalone"><section class="panel codex-connection"><div class="panel-head"><h2>Codex connection</h2><span id="codex-runtime-state">${chip(status?.state || 'disconnected')}</span></div><div class="panel-body"><p>Connect to the Codex app-server on this computer. It uses your existing Codex authentication and configuration.</p><form id="codex-connect-form" class="codex-connect-form"><div><label for="codex-profile">Codex profile · optional</label><input id="codex-profile" maxlength="128" autocomplete="off" value="${escape(codexPanelState.profile)}" placeholder="Configured default"></div><div><label for="codex-model">Model · optional</label><input id="codex-model" maxlength="128" autocomplete="off" value="${escape(codexPanelState.model)}" placeholder="Configured default"></div><button class="button primary" type="submit" ${status?.connected ? 'disabled' : ''}>Connect Codex</button></form><p class="subtle-note">The executable comes from this server’s <code>--codex-executable /absolute/path/to/codex</code> setting or local PATH. Opening this page does not start Codex.</p><div class="codex-connection-actions"><button class="button secondary small" data-action="codex-refresh">Refresh status</button><button class="button secondary small" data-action="codex-disconnect" ${status?.connected ? '' : 'disabled'}>Disconnect Agent</button></div><p class="subtle-note">Disconnect ends the Agent connection. It does not cancel or restart a simulation.</p></div></section><section class="panel codex-chat"><div class="panel-head codex-chat-heading"><div><h2>Conversation</h2><span id="codex-stream-state" class="subtle-note">No live event stream</span></div><div class="codex-session-controls"><label class="sr-only" for="codex-session">Conversation history</label><select id="codex-session"><option>No conversation yet</option></select><button class="button secondary small" data-action="codex-thread">New conversation</button></div></div><div id="codex-notices" class="codex-notices"></div><div id="codex-timeline" class="codex-timeline" role="log" aria-live="polite" aria-label="Agent messages and tool calls"></div><div id="codex-approvals" class="codex-approvals"></div><form id="codex-message-form" class="codex-composer"><label for="codex-message">Message Codex</label><textarea id="codex-message" maxlength="4000" rows="3" placeholder="Ask about your simulation workflow…" disabled>${escape(codexPanelState.draft)}</textarea><div><p class="subtle-note">Tool calls follow the backend’s permissions. Review each approval card before deciding.</p><button id="codex-send" class="button primary" type="submit" disabled>Send message</button></div></form></section></div>`;
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
  if (!run) return '<div class="empty"><h1>Run not found.</h1><a class="button secondary" href="#overview">Back to overview</a></div>';
  const states = [['Execution', run.execution], ['Observation', run.observation], ['Measurement', run.measurement_validity], ['Specification', run.specification_result]];
  const available = state.connections.filter(c => c.enabled);
  if (!available.some(c => c.connection_id === selectedConnection)) selectedConnection = available[0]?.connection_id || '';
  const history = state.sessions.filter(s => s.run_id === run.run_id);
  if (!history.some(s => s.session_id === selectedSession)) selectedSession = history[0]?.session_id || null;
  let html = `<a class="back-link" href="#overview">← All runs</a>` + heading('SYNTHETIC EXPERIMENT', escape(run.label), 'Manual replay · no simulator or remote process is running.', `<div class="split-actions">${runStatus(run)}<button class="button primary" data-action="advance" data-id="${escape(run.run_id)}" ${run.finished ? 'disabled' : ''}>${run.finished ? 'Replay complete' : 'Next observation →'}</button></div>`);
  html += `<div class="state-grid">${states.map(([label, value]) => `<div class="state-tile"><label>${label}</label><strong>${escape(title(value))}</strong></div>`).join('')}</div><div class="detail-grid"><div class="detail-stack"><section class="panel"><div class="panel-head"><h2>Memory observations</h2><span class="section-note">Finite scenario replay</span></div><div class="panel-body">${memoryChart(run)}</div></section><section class="panel"><div class="panel-head"><h2>Evidence timeline</h2><span class="section-note">${run.events.length} recorded events</span></div><div class="panel-body"><ol class="timeline">${[...run.events].reverse().map(event => `<li><div class="timeline-time">#${event.sequence} · ${escape(title(event.kind))}</div><p>${escape(event.message)}</p></li>`).join('')}</ol></div></section><section class="panel"><div class="panel-head"><h2>Execution identity</h2><span class="chip">PRESERVED</span></div><div class="panel-body identity">Attempt: ${escape(run.attempt_id)}<br>Submission: ${escape(run.submission_key)}<br>Owner: ${escape(JSON.stringify(run.execution_identity))}</div></section></div>`;
  html += `<section class="panel diagnosis"><div class="panel-head"><div class="diagnose-head"><img src="./owl.svg" alt=""><h2>Diagnostic assistant</h2></div><span class="chip">DEMO</span></div><div class="panel-body"><form id="diagnose-form"><label for="diagnostic-connection">Connection</label><select id="diagnostic-connection" ${available.length ? '' : 'disabled'}>${available.length ? available.map(c => `<option value="${escape(c.connection_id)}" ${selectedConnection === c.connection_id ? 'selected' : ''}>${escape(c.label)} · ${escape(title(c.kind))}</option>`).join('') : '<option>Add a connection first</option>'}</select><label for="diagnostic-question">Ask about this run</label><textarea id="diagnostic-question" maxlength="2000" required>${escape(draftQuestion)}</textarea><button class="button primary full" type="submit" ${available.length && !submitting ? '' : 'disabled'}>✧ Diagnose snapshot</button></form><p class="subtle-note">Offline fixture response. Evidence is frozen when you ask. Proposals are advisory; this build cannot change or restart simulations.</p></div><div id="diagnosis-result" class="diagnosis-result"><div class="history-empty">${selectedSession ? 'Loading saved diagnosis…' : 'Your diagnosis and evidence citations will appear here.'}</div></div><div class="session-picker"><label for="session-picker">Saved sessions</label><select id="session-picker" ${history.length ? '' : 'disabled'}>${history.length ? history.map(s => `<option value="${escape(s.session_id)}" ${selectedSession === s.session_id ? 'selected' : ''}>${escape(s.connection_label)} · ${escape(new Date(s.created_at).toLocaleString())}</option>`).join('') : '<option>No sessions yet</option>'}</select></div></section></div>`;
  return html;
}

function render() {
  routeEpoch++;
  sessionView = null;
  const page = location.hash.slice(1) || 'overview';
  if (page !== 'agents' || !isStandaloneAgent()) closeCodexEvents();
  const run = currentRun();
  $('#location').textContent = isPublicDemo() && page === 'local' ? 'Run locally' : page === 'direct' ? '直连仿真 · SSH / 集群' : page === 'live' ? '真实仿真 · CircuitGit' : page === 'connections' ? 'AI connections' : page === 'hosts' ? 'Simulation hosts' : page === 'agents' ? 'Agent channel' : run ? run.label : 'Overview';
  document.querySelectorAll('[data-nav]').forEach(a => a.classList.toggle('active', a.dataset.nav === page || (a.dataset.nav === 'overview' && page.startsWith('run/'))));
  $('#main').innerHTML = isPublicDemo() && page === 'local' ? publicLocalPage() : page === 'direct' ? directPage() : page === 'live' ? livePage() : page === 'connections' ? connections() : page === 'hosts' ? hosts() : page === 'agents' ? nativeChat() : page.startsWith('run/') ? runDetail(run) : overview();
  if (page === 'direct' && !isPublicDemo() && !directState.loaded && !directState.loading) refreshDirect();
  if (page === 'live' && !isPublicDemo() && !live.loaded && !live.loading) refreshLive();
  if (run && selectedSession) pollSession();
  if (page === 'hosts' && !isPublicDemo()) {
    $('#execution-kind').value = plannedKind;
    $('#execution-ref').value = plannedReference;
    if (plannedHost) $('#execution-host').value = plannedHost;
    updateHostSelectionUI();
  }
  if (page === 'agents' && !isPublicDemo()) {
    renderNativeResponse();
    if (isStandaloneAgent()) {
      renderCodexActivity();
      if (!codexPanelState.loaded && !codexPanelState.loading) refreshCodexStatus();
      else openCodexEvents();
    }
  }
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
  if (action.startsWith('live-') || action.startsWith('codex-') || action.startsWith('direct-')) return;
  if (isPublicDemo() && ['add-host', 'import-pool', 'host-probe', 'pool-scan', 'native-select'].includes(action)) {
    location.hash = '#local'; return;
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
      $('#connection-dialog').close();
      await refresh();
      if (location.hash !== '#overview') location.hash = '#overview';
      render(); toast('Demo reset. The four synthetic scenarios are ready to explore.'); return;
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
    if (event.target.id === 'codex-message') $('#codex-send').disabled = !codexCanSend() || !event.target.value.trim();
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
    'direct-includes': 'include_dirs', 'direct-netlist': 'netlist_text', 'direct-lookup-key': 'lookup_key',
    'direct-rec-attempts': 'rec_max_attempts', 'direct-rec-maxmem': 'rec_max_memory_gib',
    'direct-rec-backoff': 'rec_license_backoff', 'direct-rec-elapsed': 'rec_max_elapsed_min',
  };
  if (directInputs[event.target.id]) directDraft[directInputs[event.target.id]] = event.target.value;
  if (event.target.id === 'direct-rec-query') directState.recoveryQuery = event.target.value;
  if (event.target.id === 'diagnostic-question') draftQuestion = event.target.value;
  if (event.target.closest('#pool-import-form')) resetPoolPreview();
});
document.addEventListener('change', event => {
  if (event.target.id === 'codex-session') {
    codexPanelState.selectedThread = event.target.value;
    renderCodexActivity();
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
  if (codexPanelState.busy || button.disabled) return;
  codexPanelState.busy = true;
  codexPanelState.error = null;
  let approval = null;
  try {
    if (action === 'codex-thread') {
      const status = codexPanelState.status;
      if (!status?.connected || status.recovery_required || codexActiveTurn() || codexPanelState.pending) return;
      renderCodexActivity();
      const receipt = await api('/api/codex/thread', {method: 'POST'});
      codexPanelState.selectedThread = receipt.thread_id || null;
    } else if (action === 'codex-disconnect') {
      closeCodexEvents();
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
    }
    await refreshCodexStatus();
  } catch (error) {
    await refreshCodexStatus();
    codexPanelState.error = error.message;
    if (approval) Object.assign(approval, {status: 'unknown', can_approve: false, can_deny: false});
  } finally { codexPanelState.busy = false; renderCodexActivity(); }
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
    if (!connecting && rejected.includes(error.code) && codexPanelState.pending) {
      const pending = codexPanelState.pending;
      try {
        sessionStorage.removeItem(CODEX_PENDING_KEY);
        codexPanelState.pending = null;
        codexPanelState.draft = pending.text;
        const item = codexThread(pending.thread_id)?.items.find(row => row.requestKey === pending.request_key);
        if (item) item.status = 'rejected';
      } catch { /* Preserve an unreadable request record without replay. */ }
    }
    await refreshCodexStatus();
    codexPanelState.error = error.message;
  } finally { codexPanelState.busy = false; renderCodexActivity(); }
});

document.addEventListener('submit', async event => {
  if (event.target.id !== 'direct-prepare-form') return;
  event.preventDefault();
  if (isPublicDemo() || directState.busy) return;
  directState.busy = true; directState.error = null; render();
  try {
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
    if (directDraft.backend === 'lsf') {
      payload.scheduler = {queue: directDraft.queue.trim(), exclusive: !!directDraft.exclusive};
      if (directDraft.project.trim()) payload.scheduler.project = directDraft.project.trim();
      if (directDraft.job_group.trim()) payload.scheduler.job_group = directDraft.job_group.trim();
    } else if (directDraft.backend === 'slurm') {
      payload.scheduler = {partition: directDraft.partition.trim(), exclusive: !!directDraft.exclusive};
      if (directDraft.account.trim()) payload.scheduler.account = directDraft.account.trim();
      if (directDraft.qos.trim()) payload.scheduler.qos = directDraft.qos.trim();
    }
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
catch (error) { $('#main').innerHTML = `<div class="error-panel"><h2>${isPublicDemo() ? 'Could not open the public demo.' : 'Could not open the local workspace.'}</h2><p>${escape(error.message)} ${isPublicDemo() ? 'Reload this page to try again.' : 'Refresh this page after the service is available.'}</p></div>`; }

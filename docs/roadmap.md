# Roadmap

This is a milestone plan, not a release or delivery-date promise. The offline prototype, host/resource checks, independent engine SDK/CLI and optional standalone Spectre client are implemented. AI interaction uses explicit standalone/embedded Codex modes; the old Q&A-only and MCP launchers remain retired. M1/M2/M3 have bounded implementations; broader acceptance criteria remain open. A milestone closes when its observable acceptance criteria pass.

## M0 — Project foundation (current)

- [x] English and Chinese README.
- [x] MIT license preserved from the original repository.
- [x] Architecture, run contract, and implementation boundaries.
- [x] Editable brand icon and avatar export.
- [x] Illustrative manifest and synthetic failure scenario.
- [ ] Maintainer records the first supported Cadence/version/execution profile.

The repository includes an installable alpha package, local CLI/web console, synthetic replay and optional real integrations. A supported code path is not certification of an arbitrary provider, site or PDK. See the [local console guide](local-console.md) and [standalone runtime](standalone-runtime.md).

## P0.1 — Offline local console (implemented)

- [x] Separate fake AgentBackend/ModelBackend contracts and strict configuration.
- [x] Connection registry, metadata probes and explicit readiness dimensions.
- [x] SQLite turns/events, duplicate-request handling and interrupted-turn recovery.
- [x] Bounded synthetic evidence, validated proposals and advisory-only policy.
- [x] CLI and packaged browser UI: overview, connections and run details.
- [x] Four finite synthetic run scenarios with explicit manual replay.
- [x] Public tests for domain contracts, service restart/deduplication and loopback API boundaries.

This section records the original offline milestone. Later slices add a real client backed by CircuitGit's existing Runner; current AI work uses CircuitGit's existing host integration. Synthetic replay remains separate and cannot establish real execution, monitoring or scientific acceptance. Automatic recovery remains absent.

## Dual-mode Codex integration — current route

The explicit mode selects one conversation owner: standalone SpiceOwl starts its own App-Server after Connect; embedded mode reuses CircuitGit's existing window. [The setup guide](dual-mode-agent.md) records policy, persistence, tool and execution-owner boundaries.

- [x] Standalone App-Server stdio, dynamic tools, SSE and frozen human tool approval.
- [x] Embedded finite schema/dispatcher exports, with zero process/store creation.
- [x] Existing legacy Q&A remains readable without automatic resumption.
- [x] Pure engine tools, explicit host/LUT bindings and optional typed CircuitGit owner adapter.
- [x] Public ten-stage walkthrough using authorized source copies and explicit evidence limits.
- [ ] Qualify real authentication/inference and simulator behavior on each supported installation.
- [ ] Evaluate weaker models end to end and implement generic next-action orchestration.

Protocol/algorithm/browser tests use fake processes and synthetic results. They do not establish real inference or private circuit performance. Automatic unknown-turn recovery, solver restart and parameter repair remain unimplemented. Legacy P0.2/P0.3/ADR designs are historical context; the current dual-mode contract takes precedence.

## Native direct execution backend (SSH Direct + LSF / Slurm) — implemented alpha slice

- [x] Pure deterministic `prepare` freezing netlist, relative support files, include directories, license settings, and resource/scheduler limits (`spiceowl.adapters.executors.direct`).
- [x] Three explicit backends over registered OpenSSH hosts: `ssh_direct` (Linux detached wrapper + `/proc` identity), `lsf` (`bsub`/`bjobs`/`bkill`), and `slurm` (`sbatch`/`squeue`/`sacct`/`scancel`, including run-bound `OUT_OF_MEMORY` evidence).
- [x] Durable `DirectExecutionManager` implementing `RunnerProtocol` for `RuntimeMonitor` supervision, atomic once-only `launch-claim` fencing, verified-identity cancellation, bounded verified artifact collection, and five-dimensional `build_spectre_report` output.
- [x] Standalone CLI (`spiceowl direct prepare|stage|launch|status|cancel|collect|report|watch|runs|inspect|lookup`) and opt-in standalone Agent tools (`spiceowl serve --enable-direct-exec`), without altering embedded CircuitGit mode.
- [x] Web console `#direct` workflow (`/api/direct/*`), one-click host-placement handoff from `#hosts`, opt-in M4 bounded recovery (watch auto-resume on restart, license backoff retry, confirmed OOM memory scaling & host relocation), and searchable JSON + Markdown recovery reports.
- [x] Streaming binary waveform reader (`psfbin`, `psfxl`, `psfascii`), compute-near-data remote in-place queries, Min-Max envelope downsampling with automatic `exact_window` zoom-in, lossless `adctools` extraction, and multi-thousand-cycle Cycle Slicer + worst-case drill-down in `#direct`, CLI, and `spiceowl_direct_read`.
- [ ] Authorized live qualification on target Linux, LSF, and Slurm sites.

See [direct execution](direct-execution.md). Offline tests verify transport framing, scheduler parsers, atomic launch claims, verified cancellation, and monitoring using synthetic fixtures; real cluster qualification remains site-specific.

## Standalone Spectre integration — implemented alpha slice

- [x] Shared signed live client used by local CLI and web, with an explicit private project/host/caller grant.
- [x] Resource selection, input/site identity and dispatch through CircuitGit's existing Runner.
- [x] Durable existing-run watches, bounded status calls, last successful observations and failure backoff.
- [x] Private client journal and exact submission-key lookup; unknown requests cannot be replayed with replacement keys.
- [x] Explicit native collection, shared transient PSF ASCII parsing and deterministic Spectre diagnostics.
- [x] Separate execution, observation, data-validity and specification reporting.
- [ ] Automatic solver restart, resource migration and parameter repair.
- [ ] General analog measurement recipes and broader native result formats.

The [runtime guide](standalone-runtime.md) records the supported CLI, private grant, service lifecycle and scientific limits. Existing live watches disable automatic collection. CircuitGit and SpiceOwl are tested and pinned together; actual sites still need authorized qualification.

## Authored testbenches and saved-data analysis — implemented package slice

- [x] Finite Spectre source/TB validation and registered `agent-testbench` compiler,
  preserving authored source, support files and explicit criteria, with reviewed
  relative Verilog-A helpers and complete standard-header closure.
- [x] Private persistent PDK observation lookup with TTL, bounded coverage and
  a full-library per-device index, observed logical/canonical root mapping and
  precomputed model-binding ranking; execution still requires a fresh audit.
- [x] Ten optional analysis operations, explicit backend selection,
  Python/JSON/CSV CLI, and actual pinned ADCToolbox 0.9.3 integration.
- [x] Synthetic analytic checks and paired full-resolution saved-CSV adapter tests.
- [x] Bounded native PSF ASCII AC, DC sweep and operating-point parsing, complex
  output/reference normalization and explicit loop/device measurement contracts.
- [x] Streaming PSF Binary (`psfbin`) and PSFXL bundle ingestion with Min-Max envelope previews, automatic lossless zoom-in, and Cycle Slicer worst-case drill-down (`spiceowl.integrations.psf_binary`).
- [ ] Broader proprietary PSFXL/SST2 sub-variants and multidimensional waveform ingestion.
- [ ] Unified next-action driver for generic authored testbenches.
- [ ] Measured completion rates across weaker LLMs and interrupted live runs.

CircuitGit owns the paired review, tool routing and Runner integration. Supported
syntax and local algorithm tests do not establish a real simulation result or
automatic repair. See [authored TBs](agent-testbench.md), [PDK lookup](pdk-lut.md),
[ADC algorithms](adctools.md), and [agent reproducibility](agent-reproducibility.md).

## M1 — Read-only monitoring (partially implemented)

The shared monitor and structured report are implemented for existing CircuitGit runs. Incremental log-stream accounting, rotation handling and broader executor support remain open. Python 3.11+ is supported.

Acceptance:

- Run deterministic synthetic scenarios without a simulator or model credentials.
- Restart the monitor and restore the same attempt and observation history.
- Distinguish connection loss from a confirmed process/job failure.
- Handle log rotation and duplicate observations without losing event identity.
- Report execution, observation freshness, measurement validity, and specification outcome separately.
- Keep the monitor itself unable to submit, cancel, modify or restart a real job; explicit live operations use the existing Runner.

## M2 — One reliable execution backend (partially implemented)

The optional real client reuses CircuitGit’s existing standalone Spectre Runner for input/site checks, keyed submission and native collection. Keep the same execution owner rather than adding a framework or second ledger. Broader executor qualification, user-facing cancellation and recovery remain open.

Acceptance:

- Disconnect the client after submission; the executor retains ownership.
- Interrupt the controller between dispatch and receipt persistence; restarting it reconciles the original job or retains an explicit unresolved state.
- Deliver duplicate submission/action requests; the adapter deduplicates or blocks them safely.
- A cancellation request targets only the verified execution owned by that attempt.
- Record simulator version, resolved netlist/model dependency identity, execution profile, and actual input fingerprint.
- Publish precisely what the selected profile supports. Real-tool tests run only in a configured private environment.

## M3 — Experiment validation (partially implemented)

The bounded transient PSF ASCII parser checks readability, finite/aligned values, time ordering and required signals. Inverter-specific acceptance is separate. General measurement-window coverage, analysis-specific validation and useful analog measurement recipes remain open; readable data never implies a specification pass.

Acceptance:

- Detect a deliberately incorrect analysis parameter against a declared requirement.
- Detect a missing output and an incomplete measurement window.
- Preserve a correctly measured circuit that fails its specification as a valid result.
- Keep missing/failed Monte Carlo samples explicit and preserve their seed/sample identity.
- Give each verdict evidence locations and a validator version.

## M4 — Bounded recovery

Add explicit recovery policies for the selected backend: license-related handling, connection reconciliation, confirmed resource failures, and native checkpoint continuation only where verified.

Acceptance:

- Record the failure evidence, policy decision, resource/configuration diff, action receipt, and subsequent validation.
- Enforce attempt, resource, and elapsed-time budgets.
- Unknown action outcomes trigger reconciliation, not a new key or blind retry.
- Unsupported checkpoint use is rejected before dispatch.
- Electrical, seed, stimulus, corner, tolerance, and measurement changes follow their declared authorization class.
- Preserve the original attempt and its outputs.

## M5 — Evidence-backed diagnosis through CircuitGit

Extend CircuitGit's existing AI interaction with typed, evidence-backed proposals using SpiceOwl's engine interfaces. The existing simulation views show runs, progress evidence, resource trends, validation results and recovery history. An optional notification integration can deliver meaningful changes and a final report. This milestone does not introduce another chat service or provider manager in SpiceOwl.

Acceptance:

- Known monitoring/recovery rules continue when the model is unavailable.
- Every agent diagnosis cites stored evidence and can express uncertainty.
- Every proposed action passes the same policy checks as non-agent actions.
- Untrusted log/model content cannot authorize a command or change a policy.
- Clearly identify implemented, experimental, and unsupported capabilities.

## Next implementation slices

1. Qualify the paired standalone runtime on explicitly authorized sites and preserve native evidence.
2. Extend input/measurement validation with real experiment requirements and missing-output/window cases.
3. Improve observation coverage, log rotation and gateway lifecycle checks without weakening uncertainty handling.
4. Improve CircuitGit's existing tool integration and end-to-end evidence tests while keeping the simulation engine independently usable.
5. Add bounded recovery only after ownership, outcome reconciliation and scientific validation are established.

## Later, after the first backend works

- Additional site schedulers and native ADE integrations.
- PVT and Monte Carlo batch policies.
- More analysis-specific validators and checkpoint compatibility profiles.
- Additional CircuitGit-supported model/provider routes behind the same evidence and proposal boundary.
- Multi-user operation, stronger isolation, and resource accounting.

These are candidates, not current features. Automatic circuit optimization, unattended schematic editing, and arbitrary control of unrelated jobs are outside the initial scope.

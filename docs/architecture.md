# Architecture

Status: an independently maintained simulation engine **SDK + CLI** used by CircuitGit. Conversation ownership is explicit: standalone SpiceOwl owns its Codex window; embedded mode reuses CircuitGit. CircuitGit retains simulation review, materialization, run/submission identity, remote execution and native collection. SpiceOwl supplies server-pool probes, PDK discovery/LUT, Agent-TB validation, five-dimension diagnostics, PSF parsing, adctools and existing-run monitoring. The synthetic console and signed [simulation client](standalone-runtime.md) remain available. Automatic solver restart, parameter repair and inference of scientific intent remain planned.

The [authored-TB compiler](agent-testbench.md) validates a finite grammar and
preserves supplied electrical source and criteria. The [PDK LUT](pdk-lut.md)
stores scoped observations without treating cache age as a current dependency
audit. Optional [ADC algorithms](adctools.md) take explicit sample arrays, units
and measurement definitions. CircuitGit binds these algorithms to full saved run
data and owns human review and execution. The generic recipe does not reuse an
inverter measurement or execute arbitrary authored postprocessing scripts.

The connectivity layer reuses OpenSSH aliases and separates transport from simulator-specific probes. Private profiles and explicit pool members can be registered through the CLI or [browser onboarding](host-onboarding.md); listing never probes a server. CircuitGit reads bounded stored evidence through `spiceowl.read_api.open_read_views(directory)`, using short read-only SQLite transactions without MCP, a controller or provider credentials. All eight original MCP projections delegate to this same SDK implementation.

## Explicit Agent modes

[Standalone mode](dual-mode-agent.md) starts one local Codex App-Server only after Connect, injects finite dynamic tools, streams SSE and preserves human review. Embedded mode exports SDK tools to CircuitGit's existing conversation and never starts a second process/window. Codex retains authentication. Neither mode creates another simulator Runner or turns tool approval into scientific authorization.

The old `spiceowl mcp` and native `agents add/probe/ask` launchers remain retired; lower-level compatibility symbols and saved Q&A are preserved without replay. `spiceowl.embedded` exports eight strict stored-read schemas and dispatch over an explicitly selected existing ReadViews; it creates no store. Pure engine tools and finite owner bindings are exposed through `spiceowl.agent_channel.export_simulation_management_tools` and `SimulationToolDispatcher`. Model calls cannot access approval endpoints, arbitrary shell or HTTP. The optional MCP extra is not needed in either mode.

CircuitGit mounts the separate repository at `submodules/spiceowl`. The gitlink and `contracts/spiceowl-integration.lock.json` must identify the same reviewed commit. Dependency updates are paired changes with regression tests, never implicit tracking of a moving branch. See the [SDK mapping and submodule contract](circuitgit-integration.md).

## First deployment

The implemented real path connects to an explicitly configured CircuitGit service and resource pool. Each selected Linux host has an operator-owned SSH/site profile and verified Spectre/model identity. The local client does not need PDK files; remote model aliases, hash pins and the simulator license still need to validate. This route executes prepared netlists without Virtuoso or OCEAN. Keep the gateway process available for background monitoring and qualify each installation with an authorized trial.

Keep these responsibilities separate:

| Boundary | Owns | Must not own |
| --- | --- | --- |
| `domain` | Experiment intent, attempt identity, observations, receipts, decisions | Simulator commands or framework-specific state |
| `adapters/executors` | Bounded transport helpers and resource observations used by the execution owner | A parallel Runner or deciding whether a circuit meets its specification |
| `adapters/simulators` | Input preparation, tool capabilities, log interpretation, result collection | Unreviewed electrical or measurement changes |
| CircuitGit execution | Compose adapters, enforce grants, dispatch actions and separately keyed postprocessing | Let an Agent conversation own simulator execution |
| `monitoring` | Durable watches of existing runs, bounded status calls and failure backoff | Submitting/restarting a solver or treating stale observations as proof of failure |
| `integrations` | Signed live client, private request reconciliation, resource selection, Spectre diagnostics and PSF ASCII parsing | A second execution/approval ledger or copying private PDK content |
| `validation` | Input consistency, data completeness, measurement preconditions, specification checks | Silently changing the acceptance criteria |
| `recovery` | Allowed actions, limits, decision records, recovery attempts | Executing arbitrary agent-generated shell commands |
| `storage` | Durable identities, events, receipts, artifact references | Keeping large waveform payloads inside the event database |
| `read_api` | Eight bounded, redacted stored views usable directly from Python | Opening an Agent channel, refreshing a host or mutating state |
| `agent` / `mcp` compatibility | Synthetic diagnostics, saved legacy data and old import/protocol symbols | A parallel supported Agent/MCP deployment or direct lifecycle authority |
| CircuitGit Codex bridge | Conversation, authentication, dynamic tools and existing human review | Treating a tool result as user approval |
| `cli` | User commands and structured presentation | Keeping a simulation alive through a terminal connection |

The web console ships static assets in the Python package. Synthetic demos use the local Controller/SQLite records; the separate real-simulation view and `live` CLI commands share `LiveSimulationClient`. The live path uses CircuitGit’s existing Runner records instead of creating synthetic task identities or another execution engine. No external frontend service is needed.

## Execution ownership

The execution host, an existing ADE background manager, or the site's scheduler owns the simulator process. A controller supervises that execution identity. A disconnected client must not imply cancellation.

The following lifecycle vocabulary describes CircuitGit's execution boundary; it does not introduce another SpiceOwl executor service:

```text
submit(attempt_spec, submission_key) -> receipt | submission_uncertain
reconcile(submission_key, receipt?) -> observation
observe(receipt) -> observation
cancel_owned(receipt, action_key) -> action_receipt
collect(receipt, artifact_selection) -> artifact_references
```

These are general interface sketches. The implemented finite live API is documented in [standalone runtime](standalone-runtime.md); it supports explicit submit/status/collect/watch operations and exact-key lookup, without cancellation or solver restart. Each adapter must document which operations it supports. An adapter that can only observe an existing job must not advertise submission, cancellation, or checkpoint recovery.

`collect` inventories/references/copies existing bytes within a configured scope; OCEAN computation is a separate service-owned postprocessing task. `ExecutionReceipt`, `ActionReceipt`, `PostprocessReceipt`, and their tagged `OperationResult` handoff are defined in [shared boundaries](contracts/interface-boundaries.md#shared-handoff-records). Imported native executions may have a null submission key and explicitly unknown historical fingerprints; this never permits an unkeyed submission.

For a direct-process backend, a PID alone is insufficient identity: record the host, process start identity, owner, and attempt association. For a scheduler, keep the cluster identity, scheduler Job ID, and submission key. For an ADE-managed job, preserve the authoritative native run mapping rather than inferring the newest run.

Each solver restart generation is a new attempt, including native scheduler restarts. Preserve the original submission reference without inventing a new SpiceOwl submit. Automatic native requeue recovery is disabled initially; unsupported identity mapping stays unresolved.

## Submission uncertainty and controller restarts

1. Persist an immutable attempt specification and submission key before dispatch.
2. Claim the attempt with a durable controller lease. Reject stale owners at the execution boundary where supported.
3. Submit using an executor that can reconcile the key to an execution identity.
4. Persist the returned receipt without changing the submission key.
5. If the response is lost, mark submission as uncertain and reconcile. Do not blindly repeat the submission.
6. If the adapter cannot establish whether a job started, require resolution of that uncertainty before another attempt.

This is not a claim of exactly-once external execution. The executor must supply deduplication or trustworthy reconciliation; a workflow framework's retry feature alone does not provide it.

The live client retains unknown requests without replay. Monitor watches and last successful observations are durable. By default, a restarted gateway needs an explicit `watch`; the operator can enable `simulation_direct.resume_monitors=true` to resume previously authorized watches after rechecking their original identity and current scope. Runtime reads do not arm a watch. Broader controller/scheduler recovery remains a target: a transient empty scheduler result cannot establish that a job is gone.

## State is multidimensional

| Field | Proposed values |
| --- | --- |
| Execution | `prepared`, `submitting`, `queued`, `running`, `completed`, `failed`, `cancelled` |
| Observation | `current`, `stale`, `unknown` |
| Measurement validity | `pending`, `valid`, `invalid`, `indeterminate` |
| Specification result | `not_evaluated`, `pass`, `fail` |

Keep the last confirmed execution state when observations become stale. Represent an unconfirmed submission as `submitting` plus `unknown`, with a recorded reason. An unknown observation must not automatically transition execution to `failed`.

Execution completion does not establish measurement validity. A valid measurement may legitimately fail a design target.

## Monitoring

The shared existing-run monitor uses bounded Runner status calls, a default 30-second cadence and failure backoff capped at 300 seconds. It retains the last successful execution observation separately from failed checks. Current live watches disable automatic collection. The following broader evidence set remains an adapter target; aggregate host snapshots are not per-process memory accounting.

- Process or scheduler state and observation timestamp.
- Current simulator analysis/stage and simulated time when exposed.
- CPU usage, memory pressure, disk usage, and available quota when measurable.
- Incremental log records with source location and rotation-aware offsets.
- Artifact presence and completeness signals exposed by the backend.

Progress is stage-dependent. A quiet log during a matrix solve or initialization is not a universal hang signal. Record uncertainty and combine independent observations before proposing termination.

A short pilot run can catch setup errors and provide a resource estimate. It does not guarantee the memory peak or convergence behavior of a full run.

## Validation

Validate against the frozen [run contract](run-contract.md):

1. **Before dispatch:** resolved inputs, model sections, units, required output selection, resource limits, and measurement window configuration.
2. **During execution:** supported progress checks and explicit preconditions; any early-stop rule must be part of the accepted experiment policy.
3. **After execution:** actual input identity, backend completion evidence, readable and complete artifacts, signal identity, window coverage, measurement computation, and specification evaluation.

Stream or downsample large waveform data for presentation. Full-precision measurement code runs near the data and stores its own version and parameters. The agent receives bounded excerpts and metrics, not an entire raw waveform directory.

## Recovery policies

| Condition | Candidate handling | Requirement |
| --- | --- | --- |
| Connection loss | Reconcile and reattach to the same execution | No duplicate submission |
| License wait/failure | Bounded waiting or a new attempt | Explicit time/attempt budget |
| Confirmed OOM | New attempt with an allowed resource profile | Resource availability and budget; preserve inputs |
| Batch memory contention | Lower concurrency for independent pending jobs | Do not silently terminate unrelated jobs |
| Node failure | New attempt, optionally using a compatible checkpoint | Original attempt confirmed terminal |
| Suspected stall | Gather more evidence or request a decision | No universal quiet-log timeout |
| Convergence failure | Evidence-backed numerical-setting proposal | Approved rule or decision; revalidation |
| Missing output or wrong setup | Configuration-change proposal | Preserve the old attempt and acceptance criteria |
| Valid specification failure | Report the failure | No retry-until-pass behavior |

Resource changes, numerical-setting changes, and changes to the experiment's meaning are distinct action classes. Even when two attempts share intent, record runtime changes that could affect numerical reproducibility. Preserve Monte Carlo sample identities and failed/missing samples; never replace an unfavorable sample with a new seed to improve yield.

The policy evaluator checks action ownership, allowed fields, maximum attempts, resource limits, elapsed-time budget, evidence freshness, and approval state. Each action has its own stable key. Unknown outcomes are reconciled, not replayed with a new key.

## Three different kinds of recovery

1. **Controller recovery:** reload stored workflow/run records after the controller restarts.
2. **Execution reconnection:** recover access to a job that is still running.
3. **Solver continuation:** start from solver-native saved state, when supported and compatible.

A waveform file is not automatically a solver checkpoint. An executor cannot reconstruct lost solver state from a workflow checkpoint. A changed netlist, model, analysis type, or unsupported solver version can invalidate a native checkpoint. Record compatibility evidence and retain the original results in a separate attempt directory.

Cadence-specific behavior must be checked against the installed release and its documentation. The initial adapter will publish a tested capability matrix rather than claim universal support. See [references](references.md).

## Storage and deployment

For the first single-controller prototype, evaluate SQLite on local storage; keep a storage interface so a shared deployment can use PostgreSQL later. Do not place the prototype database on a shared network filesystem without validating its concurrency and durability behavior.

Use separate experiment and attempt directories. Store manifests, input fingerprints, observations, action receipts, validation reports, and artifact hashes/references. Large native results stay in the configured data root with explicit retention settings. Deletion is an explicit lifecycle operation, not an automatic error-recovery technique.

Evaluate Prefect as the first orchestration integration after executor semantics are tested. Temporal and AiiDA are alternatives, not additional required services. No framework owns the scientific validity decision.

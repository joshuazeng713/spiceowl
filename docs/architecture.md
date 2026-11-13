# Architecture

Status: architecture target with a P0.1 console and bounded P0.2 connectivity slice. The [local console guide](local-console.md) and [real connection guide](real-connections.md) record shipped components and limits. SSH identity/version checks and read-only stdio MCP queries are implemented; real provider inference and simulator execution remain planned.

The current connectivity layer reuses OpenSSH aliases and separates transport from simulator-specific version probes. Private profiles are imported explicitly through the CLI; listing never probes a server. MCP clients query existing SQLite records through an independent read-only connection, without starting a controller or recovering its active turns. This is the existing-agent-to-SpiceOwl direction. A web-console-to-agent runtime adapter is separate future work. See the [P0.2 contract](contracts/p02-connectivity.md).

## Connection-layer priority

The immediate implementation priority is user-owned agent and model-API connection management, as recorded in [ADR 0002](decisions/0002-connection-layer.md). The [design workstreams](design/README.md) refine its interfaces alongside simulator and debugging plans. The AI host may differ from the simulation execution host; authentication and provider sessions belong to that AI host and user, not to the simulation attempt.

Use separate `AgentBackend` and `ModelBackend` contracts behind a connection manager. After fake-backed contracts, the first proposed integrations are native local Codex app-server and the official OpenAI Python SDK with an explicit API key. ACP follows; LiteLLM remains optional when additional API providers justify normalization. These are planned integrations, not current dependencies. Protocol negotiation does not replace provider authorization or OS isolation. The [AI design](design/ai-connections.md) records source evidence and authentication limits.

## First deployment

Start with a single controller and one explicitly configured Linux execution environment. The first monitor uses a mock executor; the first real backend is selected only after the maintainer records the available Virtuoso/Spectre versions and whether execution is local, through SSH, or through a scheduler.

Keep these responsibilities separate:

| Boundary | Owns | Must not own |
| --- | --- | --- |
| `domain` | Experiment intent, attempt identity, observations, receipts, decisions | Simulator commands or framework-specific state |
| `adapters/executors` | Job submission, reconciliation, resource observation, owned-job cancellation | Deciding whether a circuit meets its specification |
| `adapters/simulators` | Input preparation, tool capabilities, log interpretation, result collection | Unreviewed electrical or measurement changes |
| `execution` (future package) | Compose adapters, enforce grants, dispatch actions and separately keyed postprocessing | Let an agent session own simulator execution |
| `monitoring` | Timestamped observations and evidence-based failure classification | Treating stale observations as proof of failure |
| `validation` | Input consistency, data completeness, measurement preconditions, specification checks | Silently changing the acceptance criteria |
| `recovery` | Allowed actions, limits, decision records, recovery attempts | Executing arbitrary agent-generated shell commands |
| `storage` | Durable identities, events, receipts, artifact references | Keeping large waveform payloads inside the event database |
| `agent` | Evidence summaries and typed diagnostic/action proposals | Direct lifecycle authority over a job |
| `cli` | User commands and structured presentation | Keeping a simulation alive through a terminal connection |

The maintainer brought a small local web console forward into P0.1. It shares the controller and SQLite records with the CLI and ships static assets in the Python package. A full real-execution console remains a later milestone; the prototype has no external frontend service or runtime dependency.

## Execution ownership

The execution host, an existing ADE background manager, or the site's scheduler owns the simulator process. A controller supervises that execution identity. A disconnected client must not imply cancellation.

Proposed executor operations:

```text
submit(attempt_spec, submission_key) -> receipt | submission_uncertain
reconcile(submission_key, receipt?) -> observation
observe(receipt) -> observation
cancel_owned(receipt, action_key) -> action_receipt
collect(receipt, artifact_selection) -> artifact_references
```

These are interface sketches, not existing APIs. Each adapter must document which operations it supports. An adapter that can only observe an existing job must not advertise submission, cancellation, or checkpoint recovery.

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

Controller startup must recover pending records, reconcile nonterminal attempts, and resume observation. A transient empty scheduler result is not enough to declare a job gone; consult accounting/history and the adapter's consistency rules.

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

Collect bounded evidence at a configurable cadence. The initial default cadence will be chosen after measuring adapter overhead; do not poll a cluster's accounting database aggressively.

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

# Simulator and executor connections

Status: implementation proposal, 2026-11-13. No adapter, service, Cadence connection, or simulator test is implemented or validated by this document. The accompanying [execution profiles](../../examples/execution-profiles.example.json) are non-executable examples. This work follows the [architecture](../architecture.md), [run contract](../run-contract.md), [shared boundaries](../contracts/interface-boundaries.md), and [roadmap](../roadmap.md).

All external facts below were checked against the primary sources in the source register on **2026-11-13**. Public Cadence release notes and expert answers establish a design route; they do not establish that an API, license entitlement, or recovery mechanism works in the user's installation. Exact Virtuoso ISR, Spectre build/engine, Linux distribution, scheduler, storage topology, and access grants remain unprovided. No laboratory host, private CAD project, PDK, credential, or simulator was accessed for this plan.

## Recommended implementation sequence

The repository's **P0 agent/model-API connection milestone precedes simulation M1**. The sequence below applies to the simulator workstream after that priority; its first milestone remains mock plus read-only monitoring. No real simulator dependency is introduced into P0.

1. **First verifiable backend: `MockExecutor` plus `SyntheticSimulator`.** M1 replays synthetic observations, persists their cursors, and restarts the controller against the same attempt. It cannot submit or cancel a real job. A separate mock conformance harness can later exercise simulated submit/cancel semantics without exposing them in the M1 monitor.
2. **First process execution slice: one Linux execution service and a synthetic runner.** Exercise process identity, durable receipts, disconnection, and termination using an original fixture runner. Optional ngspice batch execution on an original RC deck then tests real solver output and independent measurement validation. This is a later, explicitly enabled test environment; nothing runs in this planning round.
3. **First private Cadence backend: exported, frozen netlist plus Spectre batch.** Bind one installed Spectre build, engine, transient analysis, result format, and execution profile. Use the existing site scheduler if required; otherwise use the Linux service. Initially disable native checkpoint continuation. This route separates process reliability from GUI automation and has a narrow input/output boundary.
4. **In parallel with later implementation, import existing ADE runs read-only.** Keep ADE as lifecycle owner. Enable native ADE control only after the exact release, documented management API, run mapping, licensing, and integration tests pass. Add OCEAN postprocessing before adding arbitrary setup automation.
5. Introduce recovery policies only after submission uncertainty and collection semantics pass their tests. A successful mock test proves only the mock contract; an ngspice test does not certify Spectre, PSF, a PDK, or ADE.

This is a recommendation, not a new milestone definition. The coordinator owns roadmap changes.

## Connection routes and ownership

| Route | Appropriate use | Lifecycle owner and proposed integration | Initial limit |
| --- | --- | --- | --- |
| Existing ADE job, read-only | User already launched a campaign and wants overnight supervision | ADE/its native manager remains owner; import an explicit native run and point catalog through a versioned bridge/export | Observe and collect permitted artifacts only; no inferred control rights |
| ADE Simulation Manager | User needs native setup, point scheduling, history, and GUI reconnection | Attach to the existing manager; use its supported API through a release-specific bridge | No direct child-process cancellation or duplicate independent scheduler |
| Exported netlist plus Spectre batch | Reproducible experiment with resolved includes and no need for live ADE interaction | Linux service or site's scheduler owns the process; Spectre adapter prepares and interprets | One tested engine/analysis/format; cannot silently netlist a live edited schematic |
| OCEAN/SKILL bridge | Read ADE metadata, export accepted inputs, run approved native workflows, or read native results | Explicitly select either native ADE ownership or service-owned standalone OCEAN; record downstream solver identities | OCEAN process identity alone does not identify every ADE worker |
| Independent Linux service | Site permits direct execution on an allocated workstation/server | Host service owns isolated per-attempt runner units; controller makes bounded RPC requests | No execution tied to a terminal, browser, SSH client, or agent session |
| Existing LSF/Slurm | Site already controls queues, licenses, resource limits, and accounting | Scheduler is owner; service is a submission/reconciliation gateway | Respect site policy; do not bypass the queue with a direct executor |
| Mock / optional ngspice | Public contributor development and failure injection | Synthetic state machine first; later same Linux executor with an original ngspice deck | No assumption of Spectre syntax/model equivalence |

### ADE release boundaries

Cadence's **2023-08-23 IC23.1 ISR1 release note** documents a Simulation Manager option with LSCS in **ADE Assembler**, including continued management after the launching Virtuoso session closes [C1]. A newer Andrew Beckett answer describes IC23.1-and-later management and reconnection, and says the capability is included in the ADE Artist tier in IC25.1. It contrasts an IC6.1.8 setting, `continueICRPRunOnAbruptGUIExit`, which had to be enabled before ADE startup: already-started simulations could continue, pending work would not start, and active-session reconnection was unavailable [C2]. Do not extrapolate this into identical Explorer, Assembler, Artist, ICRP, and LSCS APIs or entitlements.

The Cadence LSCS overview identifies IC6.1.8 ISR14 / ICADVM20.1 base as its introduction context [C3]; earlier release notes also advertised LSCS in ISR10/ISR11 [C4]. Record this source distinction rather than claiming ISR14 was the first build ever containing LSCS. **LSCS availability alone is not evidence of the later Simulation Manager reconnect capability.** For a supported profile, capture the full release/ISR, selected job-control mode, enabled manager option, relevant entitlement, and the installed documentation section used to implement each operation.

Use native ADE management when the imported campaign is already managed there or when preserving ADE's scheduling/history behavior is required. Use independent execution for a new, explicitly exported batch attempt. Exporting does not transfer ownership of an already-running ADE job. There is no planned process takeover or live conversion between these owners.

### Read-only ADE import

Require the user-selected project/setup reference and native history/run identity, then obtain a catalog containing tests, corners, sweep points, Monte Carlo sample/seed identities, native worker/job IDs where exposed, result locations, and source timestamps. A display label such as `Interactive.7`, a recently modified directory, a waveform viewer selection, or the newest history is insufficient on its own. Namespace it by the setup/database identity, history identity, native manager identity, and point identity, and retain the evidence that binds these fields.

Represent the campaign separately from point attempts. Each independently executed point has a distinct `attempt_id`; sibling points must never inherit each other's completion or cancellation state. An imported job has `ownership_mode=external_observe_only`. Import records an association and provenance; it does not invent evidence that SpiceOwl submitted the job. Its `submission_key` is null, and missing historical fingerprints remain null with reasons. This nullable import case is adopted in shared boundaries. A later SpiceOwl submission creates a new attempt rather than rewriting the import.

A release-specific bridge may read documented result/history APIs in an explicitly selected native session or consume an explicit exported catalog. The contract deliberately does not invent a universal ADE REST API or private SKILL function. If no supported live status API is available, expose artifact/history observation with its limitations; frozen GUI state or missing bridge replies make observation stale/unknown. They do not establish solver failure. Imported jobs remain read-only even when their OS user matches SpiceOwl's service account.

### Spectre batch and OCEAN boundaries

A Cadence expert answer warns that an ADE L `runSimulation` script may include `+inter=mpsc` and expect a live ADE connection [C5]. The proposed exporter therefore validates the generated launch mode and constructs a reviewed batch invocation from a versioned template; it never blindly executes a copied interactive script. Snapshot the effective deck, analysis/options/save selection, includes, stimulus files, and permitted provenance before dispatch. Treat changes required for export as an explicit manifest diff. No schematic/netlist/model modification is part of reconnect.

Keep bridge operations typed: `read_run_catalog`, `read_point_status`, `export_frozen_inputs`, `read_result_catalog`, and `run_postprocess_recipe` are proposed SpiceOwl facade names, not Cadence API names. Read operations and export/postprocess operations have separate grants. Exporting/netlisting can write files and needs its own authorization and output directory. Do not expose `eval(skill_text)` to an agent. A local bridge accepts only versioned recipes and validated arguments, authenticates its caller, and returns source-bound data. Its session lock prevents simultaneous setup mutation and export.

For an accepted native workflow, Cadence documents the `maeOpenSetup` / `maeRunSimulation` family as a way to run a maestro setup, which can avoid maintaining a divergent OCEAN XL export [C6]. These are background research references, not calls to execute here and not proof of a reconnect API. Legacy ADE L OCEAN, ADE XL OCEAN XL, and maestro workflows require separate recipes and tests. OCEAN standalone invocation and explicit script exit are documented in an expert answer [C7]. A managed script must report completion/errors explicitly and terminate; a still-open OCEAN interpreter is not proof of solver progress.

BAG 2.0 documents separate design/testbench components and Virtuoso/OCEAN integration [B1]. Its testbench flow records ADE XL history and result directories [B2]. Reuse the interface idea of explicit testbench, output selection, and result handle. Do not import BAG's setup-modification workflow, assume `BagSim` is globally unique, or inherit its interactive cancellation semantics as a durable executor guarantee. BAG is a reference, not a dependency in this proposal.

## Adapter contracts

`SimulatorAdapter` understands the simulator's inputs and scientific output structure. `ExecutorAdapter` understands where a job lives and how its lifecycle can be established. The simulation service composes them after authorization; the AI connection manager and diagnostic session never own either execution process.

| Component | Responsibilities | Outside its authority |
| --- | --- | --- |
| `SimulatorAdapter` | Validate/fingerprint resolved inputs; create a typed launch plan; interpret logs; enumerate analyses/signals; plan artifact selection and postprocessing; attest checkpoint compatibility | Submit directly, choose a new seed/corner/tolerance, declare circuit specifications passed |
| `ExecutorAdapter` | Durable dispatch/deduplication, native identity mapping, liveness/resource evidence, owned cancellation, byte collection and manifests | Infer circuit meaning from exit status, alter netlists, invent solver resume support |
| Simulation service | Verify grants, revisions and leases; compose adapters; store attempts/receipts/events/actions; dispatch approved postprocessing plans | Accept log text or an LLM response as authorization |
| Debugging/validation | Diagnose from bounded evidence; validate experiment and measurements; propose recovery | Perform lifecycle operations directly or collapse unknown into failed |

Proposed Python-shaped interface (all types and methods below are sketches):

```python
class SimulatorAdapter(Protocol):
    def capabilities(self, installed_profile) -> CapabilityReport: ...
    def prepare(self, frozen_attempt, resolved_inputs) -> PreparedAttempt: ...
    def interpret(self, observation_batch, parse_cursor) -> SimulatorEvidence: ...
    def plan_collection(self, receipt, artifact_selection) -> CollectionPlan: ...
    def inspect_results(self, artifact_manifest) -> ResultCatalog: ...
    def plan_postprocess(self, result_catalog, approved_recipe) -> PostprocessPlan: ...
    def checkpoint_compatibility(self, checkpoint, target_attempt) -> Compatibility: ...

class ExecutorAdapter(Protocol):
    # Calls are bound to an authenticated request context; see below.
    def submit(self, attempt_spec, submission_key) -> SubmitResult: ...
    def reconcile(self, submission_key, receipt=None) -> ReconcileResult: ...
    def observe(self, receipt, cursor=None) -> ObservationBatch: ...
    def cancel_owned(self, receipt, action_key) -> ActionReceipt: ...
    def collect(self, receipt, artifact_selection) -> CollectionResult: ...
```

`attempt_spec` is the immutable `PreparedAttempt` plus authorized resource envelope. It contains input/plan fingerprints and a fixed executable profile, argument vector, working-directory reference, allowlisted environment references, output roles, and termination policy. Domain records should reference this private launch plan, not embed arbitrary shell. Simulator preparation can write a new staging directory; it cannot run the solver. Invocation, version/help queries, and postprocessing are separate executable operations when later authorized.

The service binds `RequestContext(principal, grant_ref, expected_attempt_revision, controller_lease_epoch, profile_revision, deadline)` to every operation. Cancellation additionally binds the approved action payload and target identity. A stale controller lease is rejected at the gateway, before external dispatch, not merely checked in a UI. The minimum deployment has one gateway writer per execution namespace; multi-controller operation is disabled until fencing is enforced. Reconciliation and observation never acquire mutation authority from the data they return.

| Operation | Required behavior | Uncertain or unsupported outcome |
| --- | --- | --- |
| `submit` | Persist a key/spec binding before side effects; return accepted receipt only with authoritative mapping; same key and same spec resolve the same operation | Return `submission_uncertain` after any ambiguous send; deterministic pre-dispatch rejection is distinct; key/spec conflict is an error |
| `reconcile` | Search gateway journal and native current/history evidence using the original key/receipt; report matches and evidence scope | `unresolved` for no adequate evidence; `ambiguous` for multiple matches; no hidden submit or new key |
| `observe` | Return timestamped lifecycle/resource/log observations with a durable cursor and native detail | Keep last confirmed execution state; mark freshness stale/unknown; unknown native state remains unmapped |
| `cancel_owned` | Check exact ownership binding, identity generation, grant, revision and action key; record request before signaling through the lifecycle owner | Accepted signal means `requested`, not `cancelled`; lost acknowledgment means reconcile action and job; no mass/name-based kill |
| `collect` | Resolve allowlisted artifact roles under the bound attempt root; inventory/copy bytes and publish integrity metadata atomically | Return partial/unavailable with missing roles and retry cursor; do not change solver execution or measurement verdict |

`SubmitResult` is `accepted(receipt) | rejected(reason, no_dispatch_evidence) | submission_uncertain(operation_ref, evidence_refs)`. `ReconcileResult` carries `resolution = matched | proven_not_dispatched | unresolved | ambiguous`, optional receipt, last confirmed execution state, observation freshness, evidence references, and next-query hint. These resolution values are **not** additions to the shared execution enum. Only a gateway record proving the send was never attempted, or a definitive native rejection with no asynchronous acceptance path, can establish `proven_not_dispatched`. A timeout or empty query cannot.

For imported ADE jobs, `reconcile(null, import_receipt)` reconciles the native identity directly. A missing key without an import receipt is invalid. An unsupported operation returns a typed error without falling back to another executor. Requested capabilities are the intersection of implementation, installed profile, grant, and operation evidence; documentation alone cannot enable them.

### Receipts, identities, and observations

Persist at least:

```text
ExecutionReceipt
  receipt_id, experiment_id, attempt_id, submission_key?
  executor_profile_id/revision, simulator_profile_id/revision
  ownership_mode, owner_binding_ref, execution_identity
  input_fingerprint?, launch_plan_fingerprint?, missing_provenance_reasons[]
  dispatch_started_at?, native_accepted_at?, receipt_persisted_at
  lease_epoch, adapter_versions, native_evidence_refs

ObservationBatch
  attempt_id, execution_identity, events[], next_cursor
  execution_state?, observation_freshness, native_state, reason
  source_time?, observed_at, source_sequence?, clock_uncertainty

ActionReceipt
  action_key, proposal_id?, attempt_id, execution_identity
  action_payload_fingerprint, policy_version, decision_ref, grant_ref
  requested_at, outcome = requested | confirmed | rejected | unknown
  native_ack_ref?, outcome_evidence_refs
```

The receipt is immutable; new evidence appends a versioned binding/observation rather than replacing a job ID. A corrected import records its previous mapping and reason. Native evidence stays private where necessary; a debug bundle can reference a sanitized derivative and its provenance.

Nullable fingerprints above support imported historical executions only; a new SpiceOwl submit requires resolved input and launch-plan fingerprints. `PostprocessReceipt` and tagged `OperationResult` are separate shared records, defined in [shared boundaries](../contracts/interface-boundaries.md#shared-handoff-records). Postprocessing does not masquerade as a new simulator attempt.

| Owner | Minimum identity and binding evidence |
| --- | --- |
| Linux service | Deployment/host identity, boot ID, service-owned run UUID, per-run unit/cgroup identity and invocation generation, UID, PID plus process start identity, attempt/key association and launch fingerprint |
| Slurm | Cluster identity, allocation Job ID, array task ID if applicable, step IDs as children, submit timestamp/history, owner, attempt tag, launch fingerprint, execution generation/requeue evidence; retain additional native unique IDs only where supported |
| LSF | Cluster identity, Job ID, array index if applicable, submit time/history, owner, attempt tag and launch fingerprint; retain source/destination cluster mapping if forwarding is enabled |
| ADE | Setup/database identity, native manager/session identity when exposed, history/run identity, test and corner/sweep/sample point, native job references and source mapping evidence; child worker PID is supplemental |

PID, job name, username, run label, or command line alone is not enough. A reused PID or scheduler Job ID is a different identity generation. Job tags carry opaque attempt references only; exclude circuit names, PDK identifiers, credentials, and model paths. Require untruncated native data and compare the tag with private gateway records, owner, time range, and plan fingerprint. Do not accept a tag match as sole ownership evidence.

## Durable submission and execution service

### Submission protocol

1. Controller stores frozen attempt/spec, key, grant/decision, and lease epoch in one transaction before contacting the gateway.
2. Gateway validates the profile and enforces a unique `(execution_namespace, submission_key)` binding to `attempt_id` and plan fingerprint. A different spec under the key is rejected. Persist a local journal on validated durable storage before dispatch.
3. Record `dispatch_started` before calling the owner. Accepted replies are persisted at the gateway before acknowledgment to the controller. The native receipt includes the key mapping or its authenticated journal reference.
4. If the controller loses the response, retain `execution=submitting`, `observation=unknown` unless newer authoritative evidence is already available. Reconcile with the same key. Do not change backend, account, owner, seed, or key to work around uncertainty.
5. If the gateway crashes after a possible send but before its receipt, reconcile owner state and history. One fully matched identity restores the binding; multiple candidates block automatic action. No matches remain unresolved unless there is positive no-dispatch evidence. Escalate for a later operator decision with all candidates preserved; waiting for a deadline does not prove nonexistence.
6. A duplicate API delivery may safely retrieve the stored operation. It may cause a first send only when the durable operation is demonstrably still pre-dispatch. A genuinely new execution after a resolved failure requires a new attempt/key and policy evaluation.

This protocol does not claim exactly-once external submission. A scheduler's acceptance and SpiceOwl's journal commit are separate transactions. Standard job names/comments are correlation tools, not transactional idempotency primitives. The gateway serializes writers and preserves uncertainty across its own failures.

### Direct Linux execution

Propose an OS-supervised, unprivileged host service with an authenticated local API first. Later transport can use the site's approved SSH tunnel or TLS endpoint; SSH is transport, not job ownership. The controller API unit and each run's runner unit are separate. Per-run units must not depend on the API unit remaining alive. A systemd service/unit implementation is a candidate: upstream documents supervised processes, per-unit execution/resource/termination controls, and dynamic units [L1]. Exact options are pinned to the installed systemd version.

Use a deterministic unit name derived from the gateway operation record plus a random, stored run UUID. A runner writes its binding and launch state durably before invoking the solver, captures the actual tool/input identity, and writes an exit receipt atomically. It owns the solver process tree/cgroup. Persist stdout/stderr directly to bounded spool files so a disconnected pipe cannot block the solver. API restart reconciles existing units and journals rather than restarting them. Configure solver units without automatic restart; a service-manager restart policy must never become an invisible simulation retry.

A runner restart or repeated launch of the same unit checks the launch journal and refuses to execute the solver again if a previous execution may have begun. Crashing after writing `exec_intent` but before writing the exit receipt therefore needs identity reconciliation, not replay. Keep completed operation tombstones for at least the accepted retry horizon; expired or lost deduplication history requires operator resolution. A process-start success is insufficient for declaring either solver completion or scientific validity.

For cancellation, revalidate the immutable binding and current generation immediately before the operation; use an OS identity handle where available and the owned unit/cgroup rather than a PID-only signal. Prevent unit reuse while an action is pending. A host reboot ends in-memory solver state; boot-ID mismatch cannot be reclassified as a reattached process. Loss of a direct process without an exit receipt remains an unresolved outcome until service/host evidence establishes termination and its limits.

### Scheduler execution

Slurm documents `sbatch --parsable` receipt output, `sacct` accounting, and `scancel` control [S1–S3]. LSF documents `bsub` job receipts, nonunique job names, customized `bjobs -o ... -json`, historical/accounting views, and `bkill` [I1–I6]. Use these through versioned command/API adapters, with argument arrays or fixed reviewed batch templates. Do not interpolate an agent's command, a netlist comment, or a log line into shell.

| Concern | Slurm plan | LSF plan |
| --- | --- | --- |
| Submit | Persist operation, submit fixed wrapper, parse Job ID plus optional cluster; bind explicit working/output directories | Persist operation, parse `bsub` receipt with tested locale/format, bind cluster and job; use reviewed queue/resource mapping |
| Reconcile | Gateway journal plus exact cluster/job or private attempt correlation, current queue/control data, then accounting with explicit time range and duplicate records | Gateway journal plus exact cluster/job or private attempt correlation, `bjobs`, then `bhist` / `bacct` within permitted retention scope |
| Observe | Preserve raw state/flags and reason; include allocation and relevant steps | Preserve raw status/reason; configured structured fields where available, tested parser otherwise |
| Cancel | Exact verified allocation/element identity through scheduler; arrays disabled initially | Exact verified job/element identity through scheduler; arrays disabled initially |
| Collect | Explicit shared-filesystem mapping or staged transfer; a batch submission does not transfer arbitrary input files [S1] | Explicit output/staging profile; collect only bound job artifacts |

Queue absence is not completion. Account for eventual visibility, accounting delays, access restrictions, retention expiry, and cluster failure. Query a bounded set of owned identities with jitter/backoff; pilot defaults in the example are tuning proposals. Avoid a full history scan per point per poll. Late results from an older poll cannot regress a newer confirmed state; contradictory authoritative evidence is recorded and investigated.

Slurm can report state flags as well as base states [S4]. Suspended/held work remains nonterminal with native detail. `COMPLETING`/stage-out requires collection readiness checks. Resource metrics can be unavailable or partial; do not convert missing measurements into zero [S2]. LSF's JSON output and field availability vary by release/fix pack [I3]. Pin parsers and resource units, including site configuration of LSF limits [I1].

**Scheduler requeue is not solver continuation.** Slurm documents that requeue restarts the batch script with the same Job ID, and that site configuration can override `--no-requeue` [S1]. The initial profile requests no automatic rerun and uses a launch-once wrapper that blocks an unapproved second solver invocation. Observe and preserve any native requeue generation anyway. Do not overwrite the old output directory. Future support maps each solver invocation to a child attempt, native generation and `origin_submission_key`; its `submission_key` is null if no new SpiceOwl submission occurred. Reconcile the original operation/native binding and count the generation against budgets. Missing generation mapping remains unresolved and blocks competing submissions; it does not silently merge executions.

For ADE over LSF/Slurm, keep ADE as the controlling owner; scheduler data is corroborating child evidence. Do not cancel scheduler children while ADE may recreate or reschedule them. Native control requires the parent operation contract. The first independent scheduler backend is one job per point; arrays, heterogeneous jobs, federation, forwarding, distributed Spectre, and automatic requeue are separate capability gates.

Gateway fencing prevents stale SpiceOwl callers; it cannot add atomic identity preconditions to a scheduler command that lacks them. Certification must bound ID reuse/requeue races using native identity support and the site's retention/control policy. If the adapter cannot safely bind cancellation to the intended current generation, disable `cancel_owned` for that profile and return an explicit unsupported/uncertain result. A successful pre-query is not an atomic compare-and-cancel guarantee.

## Logs, collection, and postprocessing

### Incremental observation

Track each stream using `(attempt_id, execution_generation, source_id, file_generation, byte_offset)`. Bind file generation to the service's file ID plus available device/inode/start metadata; pathname alone is insufficient. Event IDs derive from a durable stream identity and source byte range/sequence, not a content hash alone (identical log lines can be distinct events).

Persist ingested events and the next cursor atomically. Drain an old file after rename/rotation before consuming its replacement; detect truncation and begin a new generation. Copy-truncate or rotation while disconnected can destroy unread bytes: emit a gap with known bounds/uncertainty instead of asserting lossless ingestion. Check inode reuse and prefix changes. Buffer incomplete lines/UTF-8 boundaries; retain original byte ranges, encoding errors, source times, and observation times. Replayed batches deduplicate on event IDs. A restart must neither skip uncommitted bytes nor count the same line twice.

Log parsers return versioned evidence such as analysis, simulated time, warning/error code, and terminal marker. They do not authorize actions. Quiet initialization, matrix solving, or license waiting is not a universal hang signal. An analysis change can reset simulated time; compare progress only within an analysis/point/generation. Redact sensitive excerpts for agents while retaining the private evidence reference.

### Two stages of collection

1. `ExecutorAdapter.collect` copies or references raw artifacts, verifies the bound root, records content hashes/size/role/generation, and atomically publishes a manifest. It does not parse waveforms or invoke OCEAN. Repeated selection against the same finalized generation returns the same manifest identity.
2. `SimulatorAdapter.inspect_results` interprets the catalog; `plan_postprocess` generates a bounded trusted recipe. The simulation service runs it as a separately keyed, resource-limited collection task through an executor. This task has its own receipt and cannot call solver-run/netlisting recipes or alter the source result directory. A collection task failure does not create a new simulation attempt or relabel solver completion.

Collection states are `not_requested | partial | complete | failed`, separate from the four shared result dimensions. Selection uses roles/catalog IDs rather than arbitrary filesystem paths or glob expressions from an agent. Enforce real-path containment, symlink policy, destination boundaries, and byte/file budgets. Large native waveforms normally stay at the execution site with private artifact references; reports receive bounded summaries or downsampled views. Measurement computation uses full-resolution data and a recorded recipe version.

During a run, exported data is `provisional` unless the format offers a validated snapshot method. Filesystem size stability or a copied directory alone cannot prove result consistency. For final collection, require native terminal/close evidence and successful format/catalog validation; hash finalized members and publish by atomic rename. Retry partial transfers into a staging area, retain old complete manifests, and report retention expiry or missing files explicitly. Never collect a tree that is still mutating as an immutable successful artifact. Preserve solver logs even after numerical failure.

### PSF and OCEAN policy

Start with the installed Cadence reader/OCEAN for a tested PSF family and run it near the data. Cadence's `openResults` discussion shows why result directory metadata such as `logFile` matters [C8]. Its namespace discussion explains the roles of netlist mapping directories and `runObjFile` when translating schematic signal names [C9]. Preserve the relevant native directory relationships and catalog; do not reconstruct them from guessed names, strip slashes blindly, or copy only one waveform file and declare the result portable.

Capture raw format variant (PSF/PSF XL/other), producer build, reader build, native analysis ID, exact signal identifiers, hierarchy/terminal mapping, units, complex/real representation, sweep/corner/sample axes, and available time/frequency windows. Imported signals need an explicit domain-to-native mapping with provenance. Missing mapping is an indeterminate measurement condition.

Postprocessing returns a small machine-readable report with input manifest reference, selected analysis/point, exact output mapping, sample counts, window coverage, nonfinite-data findings, recipe/code fingerprint, and computation errors. Keep raw results and derived outputs distinct. A successful OCEAN process exit is not enough: require its recipe completion record and validate the exported data. Do not run arbitrary expressions supplied by an LLM. License/reader failure leads to pending/failed collection, not automatic rerun of the circuit. Open-source PSF readers may be evaluated later per format/build; no universal PSF compatibility is advertised.

For ngspice, upstream documents batch/raw/log options and notes that `.meas` with batch rawfile output has restrictions [N1]. The proposed first RC test therefore reads the raw data and computes its measurement independently, rather than assuming a missing `.meas` value means the solver failed.

## Input, tool, and dependency fingerprints

Keep `intent_fingerprint`, `resolved_input_fingerprint`, `launch_plan_fingerprint`, and `actual_runtime_fingerprint` separate. Build canonical, versioned manifests; hash member bytes and semantic settings rather than timestamps alone. A source commit is one input reference, not the entire dependency identity.

| Manifest section | Required identity |
| --- | --- |
| Circuit/export | Source/setup revision or explicit export identity; effective top deck and transitive include graph; selected sections and search-path resolution order; stimuli and extracted data |
| Models | Private reference and allowed hash, or documented immutable vendor/package identity with attestation; compiled Verilog-A/other model binary identity, source/compiler relationship when available |
| Experiment | Analysis kind/name, parameters with units, PVT, tolerances/method, stimuli, seeds and Monte Carlo sample mapping, output-save policy and measurement requirements |
| Tools | Full Spectre version/build/engine, Virtuoso/OCEAN versions where involved, adapter/template/parser versions, executable or immutable package identity, reader version |
| Runtime | OS/architecture and relevant numerical libraries, CPU/thread/resource profile, scheduler/service version, controlled environment/configuration reference, initialization scripts and search paths |
| Derived output | Postprocessor/validator version, recipe and parameters, source artifact manifest, export format/precision |

Record requested versus actual runtime settings. Validate dependencies again at the execution site before starting: local export success does not prove remote path/model equivalence. Prefer site-approved immutable mounts/snapshots; otherwise check accessible dependencies before and after execution and report the weaker assurance. A pre-run and post-run hash cannot exclude an undetected temporary change while the solver reads the file. Missing or mutable proprietary dependency identity must remain explicit and can block strict provenance/checkpoint validation. Do not hash credential values or archive the whole environment.

Do not publish PDK/model contents or private path details to make fingerprints reproducible. Store private provenance in the deployment's access-controlled records. A public example uses null for unprovided hashes and versions, never a fabricated checksum. Encrypted models may allow hashing the supplied encrypted file, but that alone does not describe all external runtime dependencies; record what was and was not identified.

## Three recovery meanings and evidence gates

| Recovery kind | What actually resumes | Minimum success evidence | Does not establish |
| --- | --- | --- | --- |
| Controller recovery | Durable attempts, policy state, event cursors, pending actions | Store reload succeeds, keys/cursors preserved, pending operations classified, observation loop resumes; unresolved jobs stay unresolved | Any solver survived or advanced |
| Running-job reconnection | Observation/control channel to the same native execution | Fresh authoritative match of identity/generation, key or import binding, and current native state | Native state was checkpointed; GUI reconnection alone proves numerical progress |
| Native checkpoint continuation | A new solver invocation reads compatible saved solver state | Compatibility attestation, finalized checkpoint integrity, solver restore acknowledgment, observed simulated progress beyond saved state, and later output validation | Any analysis/build/model can resume or move live memory |

Represent continuation as a **new attempt** with `parent_attempt_id`, new submission key, checkpoint reference, source saved time, and compatibility/decision evidence. Original attempt must be confirmed terminal before automatic continuation; a still-running source would be a separate forked experiment requiring explicit authorization. Reconnect does not consume a new attempt. Requesting more memory after confirmed OOM is a resource-adjusted new attempt, not live memory migration.

Cadence documents transient `savetime`/`recover` use [C10] and related state-file controls [C11]. A specialist discussion explicitly rejects reusing transient state for PSS and describes missing internal state with `writefinal/readic` [C12]. Therefore ordinary waveform output, initial conditions, workflow checkpoints, SIGSTOP/SIGCONT, scheduler suspension, or a terminal multiplexer do not satisfy this checkpoint contract. Kill/OOM/power loss may prevent a final state file from being written. A prior complete compatible checkpoint is necessary; a filename extension alone is not evidence.

| Compatibility boundary | Initial policy |
| --- | --- |
| Same exact deck, dependencies, analysis, build/engine and numerical settings | Still requires installed documentation and an integration test with restore/progress evidence; disabled until then |
| Transient to PSS/tstab, PSS to HB, or any other analysis conversion | Reject unless a separately documented and tested route explicitly permits it; transient-to-PSS is specifically ruled out in [C12] |
| Netlist/topology, include/model section, PVT, stimulus, seed or sample change | Reject automatic continuation; new experiment/approved change path |
| Model/Verilog-A build or external dependency change | Reject; matching top-level netlist is insufficient |
| Spectre patch/ISR, classic/APS/X mode, architecture/platform change | Reject by default; require an explicit compatibility rule and evidence for that pair, never assume backward/forward compatibility |
| Thread/resource change | Separate compatibility decision; cannot infer bitwise/numerical equivalence from same deck; record actual execution settings |
| Stop time, saved-output selection, tolerances, or integration method change | Not silently equivalent; a documented supported difference still needs authorization and measurement revalidation |
| Partial/corrupt checkpoint or missing dependency identity | Reject before dispatch; retain evidence and old attempt |

`Compatibility` returns `compatible | incompatible | unknown`, reasons, exact source/target fingerprint comparison, documentation references, tested-profile ID, checkpoint integrity evidence, and allowed differences. `unknown` cannot authorize continuation. A changed restore directive/output directory in the new attempt is recorded as a permitted recovery-control delta only; it must not conceal an electrical or numerical change.

The current ngspice development manual documents `snsave`/`snload` continuation in a new instance for transient simulations and excludes XSPICE devices and polynomial V/I sources [N1]. Thus ngspice native continuation is a possible later profile, not an unsupported-in-principle claim; it remains disabled in the first batch backend. The in-process `resume` command alone is not durable recovery. Pin the exact release/build and device scope before testing any snapshot route.

A report should say which evidence gate passed: `controller_restored`, `execution_reattached`, `checkpoint_loaded`, `continuation_progress_confirmed`, or `measurement_revalidated`. These are proposed event kinds, not replacements for shared state enums. Never report a generic “recovery succeeded” from an accepted RPC, a live PID, a new log file, or a zero exit code. Progress beyond the saved time establishes continuation progress; successful final execution and valid measurement require their own later checks.

## Proposed file tree and first slices

These paths describe future code, not files created in this documentation round:

```text
src/spiceowl/
  domain/execution.py                 # prepared attempts, receipts, capabilities
  adapters/executors/base.py          # lifecycle/collection protocol
  adapters/executors/mock.py          # deterministic synthetic owner
  adapters/executors/linux_service.py # gateway client
  adapters/executors/slurm.py         # one-job profile, later
  adapters/executors/lsf.py            # one-job profile, later
  adapters/executors/ade_observer.py   # imported native identities, read-only
  adapters/simulators/base.py
  adapters/simulators/synthetic.py
  adapters/simulators/ngspice.py       # optional original RC deck, later
  adapters/simulators/spectre.py       # release-gated batch preparation/parsing
  adapters/simulators/cadence_results.py
  adapters/bridges/cadence.py          # typed, recipe-based bridge client
  execution/service.py                # proposed service composition boundary
  execution/journal.py                # key bindings, fencing, action receipts
  execution/runner.py                 # independent per-run ownership
  execution/collection.py             # inventory, staging, atomic publication
  monitoring/log_cursor.py
  storage/execution_records.py
tests/
  contracts/test_executor.py
  contracts/test_simulator.py
  fault_injection/test_submission.py
  fault_injection/test_reconnection.py
  fault_injection/test_collection.py
  fixtures/synthetic/                 # original logs, histories and result data
  integration/linux/                 # synthetic runner; opt-in
  integration/ngspice/                # public RC fixture; opt-in
  integration/private/               # harness only; no private inputs in Git
```

Slice A implements only shared records, mock observation, persisted cursors, and a read-only report. Slice B adds the mock contract harness for uncertain submit/cancel and collection. Slice C implements the Linux runner/gateway with synthetic process tests. Slice D adds optional ngspice measurement validation. Slice E validates one private Spectre profile and, if available, one read-only ADE import. Add only the scheduler that the actual site requires. Defer a UI, multi-host ownership failover, automatic numerical changes, and native checkpoint support until their independent gates pass.

## Failure injection and acceptance plan

All cases below are planned. Public cases use original synthetic fixtures; private cases need a separately authorized environment. Record observed dispatch counts, native identities, event/receipt IDs, and artifacts so assertions test effects rather than implementation details.

| Injection | Required observable result |
| --- | --- |
| Controller exits after gateway accepts, before local receipt commit | Same attempt/key reconciles to exactly the recorded native identity; no second dispatch |
| Gateway exits after possible scheduler acceptance, before journal acknowledgment | Matched identity or explicit unresolved/ambiguous state; never automatic resend |
| Duplicate submit; same key with changed inputs | Same stored result for identical spec; conflict for changed spec; no extra solver invocation |
| Old controller sends after lease takeover | Gateway rejects stale epoch before side effect; initial single-writer deployment remains fenced |
| Client disconnects while process runs | Per-run owner continues; reconnect preserves identity and fresh running/terminal evidence |
| API daemon restarts; runner exits before observer polls | Completed journal restores terminal evidence, independent of PID lifetime; no automatic solver restart |
| Runner crashes after `exec_intent`; boot ID/PID is reused | No replay and no cancel of replacement; unresolved or evidence-backed terminal result |
| Queue result temporarily empty; accounting delayed or expired | Keep last confirmed state and uncertainty; expiry cannot create proof of failure or no submission |
| Two jobs share a tag; job ID reused or requeued | Refuse ambiguous ownership; preserve generations; launch-once wrapper blocks unauthorized repeated solver run |
| Cancel acknowledgment lost; same action delivered twice | Reconcile action/target; do not send an unapproved escalation or declare cancellation from request acceptance |
| Job completes just before cancel; wrong imported point supplied | Preserve actual terminal result; imported/unowned target rejected before control operation |
| Rotated, truncated, repeated, out-of-order or partial UTF-8 logs | Correct event identity/cursor; explicit gaps where bytes were lost; no false progress/terminal transition |
| Quiet log during long computation or license wait | No universal hang/kill diagnosis; collect corroborating evidence within policy budget |
| Partial PSF/raw copy, disk full, dropped transfer, symlink escape | Partial/failed collection, intact prior manifest, no escape; solver state and validity remain separate |
| Solver exit zero with missing required signal/window | Execution can be completed; validity invalid/indeterminate; specification not evaluated |
| Missing mapping metadata or wrong test/corner/sample | Signal/result association rejected; never substitute a neighboring run's data |
| Postprocessor lacks license, hangs, or exits without recipe record | Bound collection task fails; no solver rerun and no measurement fabricated |
| Changed remote model include or runtime build | Reject before run if detected; otherwise record provenance violation and invalidate strict acceptance |
| Corrupt checkpoint, transient-to-PSS, changed model/build | Continuation rejected before dispatch; old results preserved |
| Restore message appears but simulated time never advances | May report checkpoint load evidence; cannot claim continuation progress or recovery completion |
| Compatible checkpoint continues but final waveform window is incomplete | Progress may be confirmed; measurement remains invalid/indeterminate |
| Valid RC/circuit result deliberately misses its target | Preserve valid measurement and specification fail; no retry-until-pass behavior |
| Log/recipe metadata contains an instruction to run a command | Treated as data; no authorization or command execution |

Private release certification should cover batch success, numerical failure, native terminal evidence, signal namespace mapping, reader behavior, disconnection, and receipt loss before enabling submit. Native continuation certification additionally compares a baseline uninterrupted run with saved/restored execution under an accepted numerical tolerance and full provenance, not just a successful restore message. Tests must retain failures and missing Monte Carlo samples.

## Support matrix

Every row has **SpiceOwl integration-tested = no** as of 2026-11-13. “Documented” below describes external functionality, not shipped project support. Capabilities are recorded per operation with `documented`, `advertised`, `locally_checked`, or `integration_tested` evidence and a separate enabled flag.

| Proposed profile | Observe/reconcile | Submit / cancel | Result collection | Native continuation | Gate |
| --- | --- | --- | --- | --- | --- |
| Mock + synthetic, M1 | Planned deterministic replay | Disabled in monitor; synthetic harness later | Synthetic only | Synthetic evidence tests only | Public contract tests |
| Linux + ngspice batch | Planned journal/process mapping | Planned owned run only | Documented raw output; parser/validator pending | Disabled; documented snapshot route has device limits [N1] | Pinned ngspice + Linux service tests |
| Linux + Spectre batch | Planned journal/process mapping | Planned owned run only | Version/format-gated PSF/OCEAN | Disabled initially; per-analysis/build certification | Actual Spectre engine/build and models |
| Slurm + Spectre/ngspice | Planned queue/accounting reconciliation | Planned exact owned job only | Explicit staging/shared root | Scheduler requeue does not certify it | Installed scheduler fields/site policy |
| LSF + Spectre/ngspice | Planned job/history reconciliation | Planned exact owned job only | Explicit output/staging profile | Scheduler suspend/restart does not certify it | Installed release/fix pack/site policy |
| ADE legacy/unknown, imported | Only documented accessible history/artifacts; live bridge may be unavailable | Disabled | Readable authorized native results | Disabled | Exact mapping and observed capabilities |
| ADE Simulation Manager, imported | Native management/reconnect documented [C1,C2]; SpiceOwl bridge pending | Disabled on import; future managed grant separate | Native catalog plus Cadence reader | Not inferred from manager availability | Actual ISR, entitlement, API and owned-run test |
| OCEAN bridge | Typed metadata/result operations planned | Native run recipe deferred; owner explicit | Documented reader path [C7–C9], version-gated | Delegated to solver profile only | Recipe, release, licensing and downstream mapping |

## Outstanding environment and integration decisions

Do not block this design on unavailable environment details. Before enabling a real profile, the maintainer must supply the Virtuoso release/ISR and ADE product/mode, Spectre full build/engine, Linux/service availability, scheduler release/queue policy, filesystem retention/quotas, readable native history APIs, result formats, dependency identity method, and grants for concrete operations. Record unknowns as unknown; never inspect unrelated installations to fill them.

Recommendations for the coordinator and the AI/debugging workstreams through [interface boundaries](../contracts/interface-boundaries.md):

Coordinator review: SIM-01 through SIM-07 are adopted or resolved by the shared boundaries and [integrated plan](integration-plan.md). The table retains the original proposals for context. Per-operation evidence schemas, exact versions and real-environment certification remain implementation work.

| ID | Decision to ratify | Proposed resolution |
| --- | --- | --- |
| SIM-01 | `collect` ownership is split between architecture's two adapters | Executor moves/inventories bytes; simulator specifies roles/parses results; service dispatches separate keyed postprocessing tasks |
| SIM-02 | Imported external jobs did not have a SpiceOwl submission | Permit null `submission_key` on an explicit import receipt; use native identity for reconcile, never for submit; control remains disabled |
| SIM-03 | Shared execution enum lacks uncertain/held/requeued variants | Keep current enum; use observation freshness, reconciliation resolution and native state/generation detail; do not introduce conflicting terminal enums |
| SIM-04 | Scientific attempt versus repeated native job execution | One solver invocation per attempt; disable automatic rerun initially; ratify explicit new-attempt mapping before supporting native requeue |
| SIM-05 | Capability evidence vocabulary exists but enablement shape is not frozen | Per-operation evidence list plus enabled flag, profile/build scope and limitations; example evidence describes external concepts and must be refined per operation before implementation; “documented” never implies runnable |
| SIM-06 | Collection-task identity and execution service module are not yet shared | Add separate operation key/receipt for postprocessing and an execution service boundary; never reuse `agent_session_id`, proposal ID, or simulation submission key |
| SIM-07 | Recovery success vocabulary needs claim-level evidence | Distinct recovery event kinds and validation evidence; generic action acknowledgment cannot mean scientific recovery |

AI proposals should carry `attempt_id`, exact `execution_identity`, `proposal_id`, typed action/diff, evidence references and uncertainties. Policy validates an existing authorized grant; the service issues `action_key` only after an allowed decision. A policy decision cannot create its own authorization. Debugging receives raw native states plus parser/version provenance and must distinguish solver failure, telemetry failure, postprocessor failure, and valid specification failure. Cancellation or deletion of an AI session has no executor effect.

## Primary source register

Checked **2026-11-13**. Dates below are publication dates only when explicitly shown; undated/versioned pages and older discussions are not treated as current installed-release guarantees. No login-protected Cadence manual was retrieved, and no mirrored proprietary manual was used. Some IBM full-page fetches returned HTTP 403; the listed facts were available in IBM's indexed primary-page content. Installed manuals and private integration tests remain required.

| ID | Original source | Evidence used / limitation |
| --- | --- | --- |
| C1 | [Cadence: Virtuoso Studio IC23.1 ISR1 Now Available](https://community.cadence.com/cadence_blogs_8/b/cic/posts/virtuoso-ic23-1-isr-1-now-available) | 2023-08-23; Assembler Simulation Manager with LSCS and management after GUI closes |
| C2 | [Cadence, Andrew Beckett: Modify Assembler Max Jobs on Frozen Virtuoso](https://community.cadence.com/cadence_technology_forums/f/custom-ic-design/66028/modify-assembler-max-jobs-on-frozen-virtuoso) | IC23.1+, IC25.1 tier statement, legacy IC6.1.8 survival/reconnection limits; exact API/entitlement unverified locally |
| C3 | [Cadence: LSCS Job Control Mode](https://community.cadence.com/cadence_blogs_8/b/cic/posts/virtuoso-video-diary-large-scale-cloud-simulation-lscs-job-control-mode-the-enabler-for-cloud-simulations) | Overview's IC6.1.8 ISR14/ICADVM20.1 context; not proof of Simulation Manager support |
| C4 | [Cadence: IC6.1.8 ISR11 / ICADVM18.1 ISR11](https://community.cadence.com/cadence_blogs_8/b/cic/posts/virtuoso-ic6-1-8-isr11-and-icadvm18-1-isr11-now-available) and [ISR10](https://community.cadence.com/cadence_blogs_8/b/cic/posts/virtuoso-ic6-1-8-isr10-and-icadvm18-1-isr10-now-available) | Earlier release notes also advertise LSCS; avoid an overbroad first-version claim |
| C5 | [Cadence, Andrew Beckett: Run Spectre from command line](https://community.cadence.com/cadence_technology_forums/f/custom-ic-skill/43786/run-spectre-from-command-line) | ADE L interactive `+inter=mpsc` versus batch |
| C6 | [Cadence, Andrew Beckett: OCEAN commands for waveform comparison](https://community.cadence.com/cadence_technology_forums/f/custom-ic-skill/40281/ocean-commands-for-waveform-comparison-feature) | Maestro `mae` workflow reference; not a general management API |
| C7 | [Cadence, Andrew Beckett: OCEAN nograph](https://community.cadence.com/cadence_technology_forums/f/custom-ic-skill/13031/ocean-nograph) | Standalone restore script and explicit exit; older answer |
| C8 | [Cadence, Andrew Beckett: openResults](https://community.cadence.com/cadence_technology_forums/f/custom-ic-skill/51788/ocean-openresults-function) | Result directory metadata and result selection |
| C9 | [Cadence, Andrew Beckett: OCEAN standalone evaluation](https://community.cadence.com/cadence_technology_forums/f/custom-ic-skill/20960/question-on-ocean-standalone-evaluation) | Schematic/netlist namespace mapping and native directory metadata |
| C10 | [Cadence: Optimizing Spectre APS Performance](https://community.cadence.com/cadence_blogs_8/b/cic/posts/spectre-optimizing-spectre-aps-performance) | 2019-01-24; transient saved-state use; not a cross-version compatibility table |
| C11 | [Cadence, Andrew Beckett: Saving simulation data for later use](https://community.cadence.com/cadence_technology_forums/f/custom-ic-design/21589/saving-cadence-simulation-data-for-later-use) | State-file controls; old release defaults must be rechecked |
| C12 | [Cadence, Shawn Logan and Andrew Beckett: Configure outputstart in tstab](https://community.cadence.com/cadence_technology_forums/f/custom-ic-design/47432/configure-outputstart-in-tstab) | Transient/PSS boundary and incomplete initial-condition state |
| B1 | [BAG 2.0 overview](https://bag-framework.readthedocs.io/en/latest/overview/overview.html) | Original project's component and Virtuoso/OCEAN design; historical version |
| B2 | [BAG 2.0 testbench generator](https://bag-framework.readthedocs.io/en/latest/overview/testbench.html) | ADE XL history/result handle; not a durability claim |
| L1 | [systemd.service upstream manual](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html) | OS-supervised service/unit candidate; online manual may exceed installed version |
| S1 | [SchedMD sbatch](https://slurm.schedmd.com/sbatch.html) | Receipt, file staging limits, requeue behavior and site override |
| S2 | [SchedMD sacct](https://slurm.schedmd.com/sacct.html) | Accounting, job/step/generation distinctions, visibility and metric limits |
| S3 | [SchedMD scancel](https://slurm.schedmd.com/scancel.html) | Job/step/array control scope; first profile excludes arrays |
| S4 | [SchedMD job state codes](https://slurm.schedmd.com/job_state_codes.html) | Native states and flags |
| I1 | [IBM LSF 10.1 bsub description](https://www.ibm.com/docs/en/spectrum-lsf/10.1.0?topic=bsub-description) | Submission receipt and configurable limit units |
| I2 | [IBM LSF job lifecycle](https://www.ibm.com/docs/en/spectrum-lsf/10.1.0?topic=lsf-job-lifecycle) | Job IDs versus nonunique names |
| I3 | [IBM bjobs JSON option](https://www.ibm.com/docs/en/spectrum-lsf/10.1.0?topic=options-json) and [output formatting](https://www.ibm.com/docs/en/spectrum-lsf/10.1.0?topic=2-command-output-formatting) | Customized JSON fields; release/fix-pack scope |
| I4 | [IBM LSF control job execution](https://www.ibm.com/docs/en/spectrum-lsf/10.1.0?topic=jobs-control-job-execution) | `bstop`, `bresume`, `bkill`; ownership and requested operation |
| I5 | [IBM LSF bacct](https://www.ibm.com/docs/en/spectrum-lsf/10.1.0?topic=reference-bacct) | Completed-job accounting; full-page fetch blocked, indexed official content checked |
| I6 | [IBM LSF Explorer history/accounting access](https://www.ibm.com/docs/en/spectrum-lsf/10.1.0?topic=tyc-use-lsf-explorer-improve-performance-bacct-bhist-commands-retrieve-additional-data) | `bhist` event history and `bacct` retrieval; installation-dependent path |
| N1 | [ngspice original manual](https://ngspice.sourceforge.io/docs/ngspice-manual.pdf), sections 11.4, 12.3, 13.5.67, 13.5.85–86; [documentation index](https://ngspice.sourceforge.io/docs.html) | Fetched manual identifies itself as development version 47+, 2026-09-28; batch output, measurement restriction, snapshot/device limits; pin a release before implementation |

# Simulation debugging, experiment acceptance, and recovery

Status: implementation proposal `0.1-draft`, checked 2026-11-13. Nothing in this document is an implemented monitor, validated Spectre parser, recovery service, or real experiment result. The companion [policy example](../../examples/debug-policy.example.json) is a non-executable, synthetic design fixture. All defaults below are proposed SpiceOwl choices, not vendor recommendations.

This workstream follows the [architecture](../architecture.md), [run contract](../run-contract.md), [roadmap](../roadmap.md), and [shared interface boundaries](../contracts/interface-boundaries.md). **P0 AI connection management comes first.** This debug plan supplies downstream contracts and fixtures without making the full debug stack a P0 dependency. The first subsequent debug deliverable should be deterministic read-only diagnosis on synthetic evidence. Introduce execution recovery only after the executor's ownership and reconciliation contracts work; introduce an optional diagnostic model after rule and validation baselines exist. A complete Spectre error-code catalogue is outside the first slice.

## 1. Acceptance is four separate dimensions

Use the shared enumerations without introducing an overall `success` boolean:

| Dimension | Values | Authority and meaning |
| --- | --- | --- |
| `execution` | `prepared`, `submitting`, `queued`, `running`, `completed`, `failed`, `cancelled` | Identity-bound executor evidence; `completed` says execution finished, not that the experiment is valid. |
| `observation` | `current`, `stale`, `unknown` | Freshness and availability of the evidence needed for this observation. Keep the last confirmed execution state and its timestamp. |
| `measurement_validity` | `pending`, `valid`, `invalid`, `indeterminate` | Versioned validators operating against frozen intent and identified artifacts. |
| `specification_result` | `not_evaluated`, `pass`, `fail` | Frozen predicates applied to valid measurements; independent from recoverability. |

Examples of proposed reports:

| Evidence | Four-dimensional report | Consequence |
| --- | --- | --- |
| Last observed running, transport lost | `running / unknown / pending / not_evaluated` | Reconcile that execution; no new submission. |
| Submission response lost | `submitting / unknown / pending / not_evaluated` | Preserve the submission key and unresolved operation. |
| Solver finished, results transfer unavailable | `completed / current / indeterminate / not_evaluated` | Execution evidence can be fresh while artifact access is unavailable. Reconcile collection separately. |
| Completed waveform lacks a required saved signal | `completed / current / invalid / not_evaluated` | A new acquisition attempt may be needed. |
| Correct settling measurement exceeds its limit | `completed / current / valid / fail` | Preserve and report the design failure. No recovery trigger. |
| Previously validated immutable result, live monitor now stale | `completed / stale / valid / fail` | Freshness changes do not erase a content-bound historical verdict. |

Freshness is also recorded per evidence source: a fresh scheduler reply cannot freshen old logs or inaccessible waveforms. Terminal execution records remain historical facts; they do not permit actions against a reused job identity. Measurement reports cite immutable artifact snapshots and can be superseded only by a new, linked report with a reason. Never overwrite a failed result with the best later attempt.

Slurm documents successful job completion and separate memory, node, and time-limit states; those states are execution evidence, not circuit acceptance criteria. Job flags must also be preserved rather than flattened away. See [S2](#s2-slurm-execution-states).

## 2. Ownership and evidence pipeline

The coordination boundary is `Evidence -> Proposal -> PolicyDecision -> OperationResult (receipt reference) -> Validation`; Detection and Diagnosis are internal evidence-to-proposal stages. `ActionProposal` and `ValidationReport` below specify the proposal and validation payloads. `ExecutionReceipt` binds native execution/import identity, `ActionReceipt` records lifecycle actions, and `PostprocessReceipt` records separately keyed computation. `OperationResult` is a tagged handoff referencing the appropriate receipt, not a second source of dispatch truth.

```text
Executor / simulator adapter
  -> EvidenceSnapshot (bounded, identity-bound, timestamped)
  -> deterministic detectors -> Detection[]
  -> rule diagnosis or optional diagnostic model -> Diagnosis / ActionProposal
  -> policy evaluator -> PolicyDecision
  -> authorized service dispatcher -> OperationResult -> appropriate receipt reference
  -> validators -> immutable ValidationReport
```

Responsibilities are deliberately narrow:

| Component | May do | Must not do |
| --- | --- | --- |
| Adapter | Normalize release-specific states, input identities, progress, artifact inventory, and evidence locators. | Declare scientific equivalence or treat a parsed message as authorization. |
| Detector | Evaluate explicit predicates, applicability, freshness, and contradictions. | Execute a fix, mutate inputs, infer death from a connection failure. |
| Diagnosis | Explain detections, alternatives, and additional evidence needed. | Upgrade uncertain evidence to authoritative execution state. |
| Optional model | Submit bounded typed diagnoses/proposals with resolvable evidence citations. | Approve itself, dispatch actions, rewrite policy, read credentials, or acquire unrestricted tools. |
| Policy evaluator | Check ownership, capabilities, evidence, change class, authorization, and budgets. | Treat a model's confidence or a log instruction as an approval. |
| Dispatcher | Execute an allowlisted typed action under a durable decision and action key. | Interpret generated shell text or replace an unknown operation with another backend. |
| Validator | Evaluate declared input/data/measurement contracts and preserve evidence. | Change stimulus, thresholds, windows, seeds, or failed sample accounting to obtain a pass. |

`AgentBackend` and `ModelBackend` remain connection-workstream interfaces. Debugging supplies the same evidence package to either, subject to capability and data-access policy. Disconnecting, cancelling, switching, or deleting `agent_session_id` stops only diagnostic work. The simulation service, reconciliation loop, and already accepted job operations have independent lifetimes. A model outage does not disable deterministic monitoring.

### Evidence requirements

Each immutable evidence item has:

- `event_id`, `experiment_id`, `attempt_id`, nullable `submission_key`, and the verified `execution_identity` or explicit absence before reconciliation. An imported external job has `submission_key: null` and an explicit import binding referenced by its `ExecutionReceipt`; importing does not invent a SpiceOwl submission. Job labels and directory names alone are not bindings.
- Source kind, source identity, source version, adapter/parser versions, capability provenance, and whether the observation concerns the whole allocation, a job step, analysis, corner, or sample.
- `source_time` when supplied, controller `observed_at`, durable `ingested_at`, source sequence/restart generation, and a monotonic elapsed duration within a collector boot. Preserve clock-skew uncertainty; do not compare remote wall times as if perfectly synchronized.
- Freshness status, applicable TTL, completeness (`partial`, `sealed`, `unavailable`), and an explicit missing-value reason. An absent resource metric is not zero.
- A content-bound artifact reference and locator: log generation plus byte interval, structured record key, or waveform analysis/signal/sample/time window. Hash a sealed artifact or the exact referenced chunk; do not claim a changing file has a stable whole-file hash.
- Sanitized normalized facts with units and the bounded source excerpt needed to audit the parser. Store large native artifacts in the protected data root. The model sees only authorized redacted excerpts and summaries; redaction metadata must disclose lost context.

Unknown historical input and launch fingerprints remain explicit nulls with missing-provenance reasons. Never hash today's inputs and present that value as evidence of an earlier launch. Observation can use a verified import binding despite missing historical input provenance; scientific acceptance and input-dependent recovery must remain indeterminate or blocked until their required evidence exists.

Use three related identities: delivery `event_id` for deduplication; artifact/chunk identity for repeated reads of the same source; detection identity for a rule, scope, and evidence window. Repeatedly reading one unchanged line does not supply independent corroboration. Detect log rotation/truncation using a generation identity, not byte offset alone. Events from an older execution generation never supersede a newer one.

Authority is fact-specific: scheduler evidence can establish a scheduler state; simulator evidence can establish an analysis failure; neither establishes a valid measurement by itself. A disagreement is retained as evidence and produces `indeterminate` or reconciliation. Do not majority-vote conflicting authoritative records. For example, a successful batch wrapper and a failed solver child may coexist: preserve both, report the wrapper as completed, and fail the required analysis validator.

Slurm accounting distinguishes job and step records, may lack some metrics, and can contain multiple records for a reused or requeued job identity. Its documented `MaxRSS` statistics are not a universal whole-allocation memory peak. These constraints motivate explicit scope and missingness in SpiceOwl's evidence model. See [S4](#s4-slurm-accounting).

## 3. First fault catalogue

The table defines proposed predicates over normalized evidence, not production regexes. Every detector declares simulator/executor versions, analysis stages, required capabilities, and fixture coverage. Unsupported combinations return `not_applicable` or an insufficient-evidence result; they must not inherit a nearby release's patterns silently. Conditions can coexist and are not forced into one root-cause label.

| ID / fault | Minimum detection evidence | Initial handling and future candidate | Limits / false-positive control |
| --- | --- | --- | --- |
| D01 connection interruption | Identity-bound transport failure or missing current observation; retain last confirmed execution. | Reconcile original submission/action, then reattach and catch up evidence. | Connection, UI, or agent-session loss proves neither solver failure nor cancellation. One empty scheduler listing is insufficient. |
| D02 license waiting | Current normalized scheduler wait reason or explicit simulator checkout-wait event, with process/queue stage. | Continue bounded observation with backoff; report wait budget exhaustion. | Distinguish scheduler token wait, solver checkout wait, queue priority, server unreachability, and invalid feature entitlement. Do not start another licensed process while this one waits. |
| D03 license failure | Release-specific terminal checkout error plus authoritative terminal/non-start evidence; record feature category without credentials. | Propose same-input retry only for a classified transient condition and approved budget; persistent entitlement/configuration faults need operator resolution. | A warning or unavailable license server alone does not prove the process exited. Never change licensed feature, account, or simulator variant as an automatic workaround. |
| D04 out of memory | Scoped scheduler OOM state or attributed OS/cgroup evidence, correlated with solver/job identity. | After terminal reconciliation, propose a larger allowlisted resource profile in a new attempt; possibly reduce future campaign concurrency. | High RSS, low free RAM, or exit status 137 alone are insufficient. Missing/sampled memory data cannot rule out OOM. No live memory migration or termination of unrelated jobs. |
| D05 disk / quota / write failure | Attributed write error and filesystem/quota/inode observation, or explicit resource warning with no terminal claim. | Warn early; propose an approved new output location/resource allocation after outcome reconciliation; recollect if only transfer failed. | Free filesystem space does not establish user quota or inode availability. Pressure is not failure. Never delete results/checkpoints or drop required signals automatically. |
| D06 time limit | Authoritative terminal timeout reason; separate queue elapsed, solver elapsed, and campaign elapsed. | Propose a larger approved wall-time profile or compatible continuation after terminal reconciliation. | A client RPC timeout is D01. A local timer expiring is a policy deadline, not proof the job stopped. Stopping a live job requires its own authorized cancellation. |
| D07 node / launch failure | Identity-bound scheduler node/boot/launch failure and current state including flags/requeue status. | Reconcile any scheduler-managed restart first; then propose a new attempt if no execution is active. | Do not race native requeue. Launch failure does not imply OOM. An unreachable host is not enough to declare its solver dead. |
| D08 numerical nonconvergence | Versioned terminal diagnostic bound to analysis/stage, last accepted simulated time, and relevant warning/residual evidence where available. | Preserve partial output; propose investigation or one reviewed numerical experiment, followed by full revalidation. | Separate DC, transient, PSS/HB, and nested analyses. Intermediate Newton warnings can be followed by success. No universal tolerance-relaxation fix. |
| D09 suspected stall | Fresh same-stage observations over a configured interval: no accepted-time/iteration progress plus corroborating CPU/I/O/resource evidence. | Request additional bounded observations and report suspicion; initial policy never auto-kills. | Quiet initialization, matrix factorization, checkpoint writing, long startup, PSS iteration, license wait, and slow filesystems require distinct profiles. Even corroboration does not prove a hang. |
| D10 required signal absent / wrong | Frozen required signal list, exact resolved hierarchical/type mapping, and sealed readable artifact inventory disagree. | First reconcile incomplete collection; otherwise propose explicit save-list repair in a new attempt. | Partial live files are pending. Matching leaf names or signal counts is insufficient. Current direction, branch terminal, units, differential composition, and analysis family matter. |
| D11 invalid measurement window / sampling | Contract window and time origin compared with actual data coverage, required samples, analysis identity, and sampling preconditions. | Re-evaluate only if the same frozen recipe and adequate original data exist; otherwise propose reacquisition or a separately reviewed experiment. | Never shrink the window, discard troublesome cycles, interpolate without authorization, or move the origin to make a result pass. Sparse coverage is not established by endpoints alone. |
| D12 parameter / corner / model mismatch | Expected, resolved, and actual fingerprints differ in a declared field: PVT, units, stimulus, model section/include closure, solver identity, or sample mapping. | Block preflight; if discovered later, mark measurements for that intent invalid and preserve the attempt. Propose restoration of frozen inputs or a new experiment. | Missing actual identity is indeterminate, not equality. Same Git commit, model filename, corner label, or seed alone is insufficient. No alternate PDK/model substitution. |
| D13 artifact / parser failure | Truncated/unsealed artifact, integrity mismatch, unsupported format, or validator exception with bounded diagnostic. | Reconcile collection or use a supported reader under policy; emit indeterminate for unavailable/unsupported evidence, invalid for confirmed corruption. | Parser failure is not a circuit failure. Repeated checksum failure is not repaired by accepting a partial file. |
| D14 valid design-target miss | Valid measurement and frozen predicate evaluate to fail. | Report the measured violation and preserve provenance. No recovery proposal is generated. | Any later design optimization is a separately authorized experiment. No retry-until-pass or best-attempt selection. |

Backend mappings for D02/D06/D07 are grounded in Slurm's [reason codes](#s3-slurm-pending-reasons), [state codes](#s2-slurm-execution-states), and [requeue semantics](#s5-slurm-requeue). They do not establish that a site's Cadence license manager is integrated with Slurm license accounting. D08/D10/D11 are design requirements informed by Cadence's versioned [accuracy](#s8-cadence-accuracy-and-state-recovery), [save](#s6-cadence-save-behavior), and [strobe](#s7-cadence-strobe-and-sampling) documentation; no universal Spectre message-ID table is asserted here.

### Deterministic rule execution

1. Validate observation identity and deduplicate delivery before parsing effects into state.
2. Resolve rule applicability from the selected capability profile and evidence versions. Unsupported parser versions abstain.
3. Evaluate each predicate using three-valued logic: true, false, or unknown. Missingness never silently satisfies a predicate.
4. Emit all supported detections with grade `confirmed`, `suspected`, or `insufficient_evidence`. Grade refers to the condition, not to correctness of a proposed repair.
5. Apply debounce/hysteresis to warnings using new observations; explicit fatal records need no arbitrary repeated-message count. Stale evidence can support historical diagnosis but cannot authorize a current mutation.
6. Coalesce notifications by detection and material state change. Exponential polling backoff, caps, and deterministic jitter belong to site profiles. Persist the next eligible time and elapsed budgets across restarts.
7. Conflicting actions enter policy arbitration. One attempt may have multiple diagnoses but at most one in-flight lifecycle mutation. Validation failures do not rewrite the execution record.

The example's 30-second observation cadence, 120-second freshness TTL, three observations, and 600-second stall window are synthetic fixture constants. A selected backend needs measured overhead, stage-specific baselines, and site limits before adopting any of them. A quiet log in an unprofiled stage always leaves stall diagnosis uncertain.

## 4. Proposed contracts

The following language-neutral type sketches are schema design, not executable Python. Shared identities retain their meanings. All records include `schema_version`, a stable record ID, `created_at`, and immutable `record_revision`; updated judgments create a new record and a `supersedes` reference. Required evidence references must resolve locally before a diagnosis or proposal is eligible for policy evaluation.

```text
EvidenceRef = {
  event_id, artifact_ref?, content_digest?, locator,
  source_kind, source_version, adapter_version, parser_version?,
  source_time?, observed_at, freshness, completeness,
  scope: {experiment_id, attempt_id, submission_key: string | null,
          execution_receipt_ref?, import_binding_ref?, execution_identity?,
          analysis_id?, sample_id?}
}

Detection = {
  detection_id, experiment_id, attempt_id, submission_key: string | null,
  execution_receipt_ref?, import_binding_ref?,
  execution_identity?, analysis_id?, sample_id?,
  rule_id, rule_version, condition, applicability_profile,
  grade: confirmed | suspected | insufficient_evidence,
  severity: info | warning | error,
  evidence_refs[], contradicting_evidence_refs[], missing_evidence[],
  first_seen_at, last_seen_at, deduplication_key,
  recommended_read_only_checks[]
}

Diagnosis = {
  diagnosis_id, experiment_id, attempt_id, detection_ids[],
  producer: {kind: rule | model | human, version, connection_id?, agent_session_id?},
  evidence_snapshot_id, hypotheses: [{cause, evidence_refs[], counterevidence_refs[]}],
  known_uncertainties[], disambiguating_checks[],
  confidence: {kind: evidence_grade | uncalibrated_model_score, value},
  proposal_ids[]
}

ActionProposal = {
  proposal_id, experiment_id, target_attempt_id, target_execution_identity?,
  diagnosis_ids[], detection_ids[], evidence_snapshot_id, evidence_refs[],
  kind: observe | reconcile | report | collect | postprocess | cancel_owned |
        retry_same_inputs | retry_with_resource_profile |
        retry_with_output_config | retry_with_numerical_recipe | restore_frozen_inputs |
        continue_native_checkpoint | create_experiment_revision,
  proposed_change_classes[], reason, known_uncertainties[],
  expected: {attempt_revision, input_fingerprint: string | null,
             launch_plan_fingerprint: string | null, execution_generation,
             execution_receipt_ref?, import_binding_ref?,
             policy_version, unresolved_operation_keys[]},
  diff: [{field, before, after, unit?, reason, evidence_refs[]}],
  resource_profile_ref?, numerical_recipe_ref?, checkpoint_ref?, postprocess_recipe_ref?,
  estimated_cost: {wall_seconds?, core_hours?, license_hours?, known: boolean},
  validation_requirements[], expires_at, supersedes_proposal_id?
}

PolicyDecision = {
  decision_id, proposal_id, proposal_digest, policy_id, policy_version,
  result: allow | deny | defer | require_review,
  recomputed_change_classes[], rule_results[], reason_codes[],
  ownership_evidence_refs[], capability_evidence_refs[],
  authorization_ref?, authorization_scope?, reviewed_diff_digest?,
  evaluated_snapshot_id, expected_attempt_revision, expected_execution_generation,
  reserved_budgets?, not_before?, expires_at?,
  required_measurement_validator_ids[], specification_evaluator_ids[],
  action_key?                 # issued by service only on an allowed dispatch
}

ValidationReport = {
  validation_report_id, experiment_id, attempt_id, sample_id?,
  scope: measurement | attempt | campaign,
  contract_fingerprint, actual_input_fingerprint: string | null,
  launch_plan_fingerprint: string | null, missing_provenance_reasons[], artifact_snapshot_refs[],
  phase: preflight | in_flight | post_run | post_recovery,
  measurement_checks: [{validator_id: V01 | V02 | V03 | V04 | V05 | V06 | V08,
                        version, scope, verdict: pass | fail | pending | indeterminate,
                        required, dependencies[], expected?, actual?, unit?,
                        evidence_refs[], reason}],
  measurements: [{measurement_id, recipe_version, recipe_fingerprint,
                  validity, value?, unit?, bound?, uncertainty?, evidence_refs[],
                  specification_evaluation_refs[]}],
  specification_evaluations: [{evaluation_id, evaluator_id: V07, version,
                              predicate_ref, required, measurement_ids[],
                              result: not_evaluated | pass | fail, reason, evidence_refs[]}],
  execution: {state, evidence_refs[], confirmed_at},
  observation: {state, as_of, source_statuses[]},
  measurement_validity, specification_result,
  numerical_revalidation: {required, recipe_ref?, status, comparison_report_ref?},
  supersedes_report_id?, revision_reason?
}
```

`sample_id` in these local envelopes is the normalized view of the manifest's `monte_carlo_sample_id`; do not invent another independent sample identity. A cross-attempt comparison uses references to both attempts/reports, never merges their artifacts into a synthetic attempt.

The coordinator-selected receipt roles are distinct: `ExecutionReceipt` binds a native execution or imported job; `ActionReceipt` records a lifecycle action's key, exact target, policy/authorization, requested diff, and observed outcome; `PostprocessReceipt` records a separately keyed computation such as OCEAN, its recipe/input artifacts, and derived outputs. Debugging references these service/executor-owned records. It never emits an alternative authoritative receipt.

```text
OperationResult =
  {tag: execution, receipt_ref: ExecutionReceiptRef} |
  {tag: action, receipt_ref: ActionReceiptRef} |
  {tag: collection, manifest_ref: ArtifactManifestRef} |
  {tag: postprocess, receipt_ref: PostprocessReceiptRef} |
  {tag: unresolved, expected_receipt_kind: execution | action | postprocess,
   original_operation_ref}
```

The unresolved tag refers to the durable original operation when no authoritative receipt is available; it does not fabricate a receipt, change keys, or duplicate execution state. If a receipt already records a pending/unknown outcome, return its normal tag and reference and reconcile through that record. Outcome uncertainty remains an operation attribute, not a new value in the shared execution enum. A proposal has no executable command or credential fields. An action key is not an approval and a proposal ID is not an action key.

For imported jobs, `submission_key` is explicitly null and the import binding is required. For a future supported scheduler restart, a child attempt references `parent_attempt_id`, `execution_generation`, and the original operation through `origin_submission_key`; its own `submission_key` is null because SpiceOwl did not submit that generation. If the origin itself was imported and its submission is unknown, preserve a null origin submission and its explicit import binding. Historical input/launch fingerprints remain null unless independently evidenced.

Proposed pure interfaces:

```text
Detector.evaluate(snapshot, rule_config) -> Detection[]
Diagnoser.explain(snapshot, detections) -> Diagnosis, ActionProposal[]
Policy.evaluate(proposal, authority_snapshot, policy, budgets) -> PolicyDecision
MeasurementValidator.validate(contract, input_manifest, artifacts) -> MeasurementCheckResult[]
SpecificationEvaluator.evaluate(frozen_predicates, valid_measurements) -> SpecificationResult[]
Acceptance.reduce(measurement_checks, measurements, specification_evaluations,
                  execution_observation) -> ValidationReport
Dispatcher.dispatch(allowed_decision, action_key) -> OperationResult
Executor.collect(execution_receipt, artifact_selection) -> ExistingArtifactRef[]
```

Lifecycle effects and separately keyed postprocessing go through service dispatch. Raw `Executor.collect` only reads, inventories, or copies existing artifacts within authorized source/destination scope. It does not invoke OCEAN or another computation. OCEAN requires a distinct service `postprocess` proposal, authorization, durable operation key, and `PostprocessReceipt`; collection completion cannot imply computation completion. Validators may read authorized artifacts and write append-only reports; they cannot submit or mutate an experiment. A `require_review` decision never dispatches. The service persists the full proposal and decision so a reviewer sees a concrete target, diff, budget, and required revalidation.

## 5. Measurement validators

Run preflight checks before dispatch, lightweight checks during execution, then final validators on sealed data. A known violated measurement-validity contract produces `invalid`; inaccessible/unsupported evidence produces `indeterminate`; evidence still expected from a live analysis produces `pending`. A V07 specification predicate failure is not a measurement-validity violation. On a terminal attempt, permanently absent required data is invalid, while unresolvable retrieval remains indeterminate. None of these states fabricates a numeric value.

| Validator | Required evidence / precondition | Failure behavior |
| --- | --- | --- |
| V01 input consistency | Expected/resolved/actual netlist and include identities; model sections, unit-normalized parameters, corner/PVT, stimuli, analysis settings, simulator/adapter versions, random sample mapping. | Mismatch invalidates this intent; unverifiable identity is indeterminate. Check again after collection, not just preparation. |
| V02 analysis and artifact completion | Required analysis status, expected sweep/corner/sample membership, sealed artifact inventory and integrity. | Wrapper exit zero cannot override a missing or failed required analysis. Early diagnostic output remains explicitly partial. |
| V03 required signals | Canonical hierarchical paths, type, terminal/current direction, analysis, sample/corner membership, and units. | Ambiguous mappings or wrong analysis are invalid/indeterminate as appropriate; do not fuzzy-match a replacement. |
| V04 window and sampling | Time origin/event identity, coverage, time-axis ordering rules, gaps, required sample count/grid, interpolation policy, finite required values. | Do not clip, pad, sort away discontinuities, or resample silently. Duplicate times at discontinuities require an explicit reader/recipe convention. |
| V05 recipe preconditions | Versioned formula, unit handling, reference levels, warm-up/steady-state condition, edge definitions, window and uncertainty requirements. | An unmet design target is not a precondition failure; keep those tests separate. |
| V06 numerical revalidation | Reviewed recipe and comparison protocol after a numerical or other reproducibility-relevant change. | Convergence alone is insufficient. Unresolved numerical sensitivity makes the affected measurement indeterminate. |
| V07 specification evaluation (separate from measurement checks) | Valid required measurements plus unchanged versioned predicates. | Preserve pass/fail only in specification results. A V07 failure must never invalidate a measurement or enter measurement-check precedence. |
| V08 campaign completeness | Frozen planned sample/corner roster, every attempt lineage, deterministic result selection rule, missing/failed membership. | Missing samples stay in the denominator and campaign status; do not report complete yield from survivors. |

Aggregation uses two disjoint inputs. Required **measurement-validity checks** are V01–V05, plus V06 after relevant numerical changes and V08 for a campaign aggregate. Only these checks enter measurement precedence: `fail > indeterminate > pending > pass`, mapped to `invalid`, `indeterminate`, `pending`, and `valid`. Apply V08 to its campaign scope without rewriting valid individual sample reports. Reject V07 in `measurement_checks` or `required_measurement_validator_ids` as a schema/configuration error rather than interpreting its result as measurement failure. Optional diagnostics do not gate unrelated measurements.

V07 consumes valid measurements and produces only `specification_evaluations`. It cannot mutate `measurement_checks` or any measurement's `validity`. The run-level specification stays `not_evaluated` until all required measurement dependencies for that aggregate are valid and all required V07 predicates are evaluated; then any failed predicate yields `fail`, otherwise `pass`. A V07 evaluator error leaves its predicate and dependent aggregate `not_evaluated` without invalidating already validated measurements. Per-measurement predicates may be evaluated as soon as their own dependencies are valid. Preserve those failures even while the aggregate specification is not evaluated.

Reducer assertions, also encoded as synthetic expectations in the policy example:

- All required measurement checks pass and one V07 predicate fails: `measurement_validity=valid`, `specification_result=fail`; D14 reports the target miss without proposing recovery.
- Two required measurements are both valid, one V07 predicate passes and the other fails: aggregate `valid / fail`; both measurement validities remain valid.
- Measurement A is valid and its V07 predicate fails; measurement B has a required V03 failure: aggregate `invalid / not_evaluated`, A remains `valid / fail`, and B is `invalid / not_evaluated`. Only B's measurement failure causes aggregate invalidity; A's target failure remains visible and is not a recovery trigger. If B is indeterminate instead, the aggregate is `indeterminate / not_evaluated` with A unchanged.

### First analog recipe: settling after a declared step

Implement one recipe against the existing synthetic [RC manifest](../../examples/run-manifest.example.json), before generalizing to ADC FFT, phase noise, PSS, or eye measurements. Freeze the step origin, output mapping, final reference, absolute/relative band convention, observation horizon, crossing convention, sample-resolution rule, and target. The synthetic manifest uses an absolute 0.01 V band around 1 V, a step at 1 microsecond, and a window ending at 10 microseconds.

Compute the last band exit and the subsequent entry that remains within the band until the declared horizon. The result describes settlement over that finite horizon, not all future time. Return a bracket if temporal resolution cannot identify an exact crossing; the predicate evaluator must account for that bracket. Insufficient resolution near the target is indeterminate and calls for authorized reacquisition, not optimistic rounding.

For an analytically generated ideal RC waveform with time constant 1 microsecond and a 1 V step, the crossing at 1% error is approximately 4.605170 microseconds after the step. This is an analytic synthetic test oracle, not a simulator result. Test a larger time constant that genuinely violates the target, a later band re-exit, a wrong time origin, sparse samples around the limit, and a truncated window.

If the full required horizon is present and the trace establishes that settlement did not occur by the target, report a valid bounded target failure even if a finite settling-time scalar is unavailable. Record a lower bound or `not_settled_within_horizon` outcome. If the horizon ends before the target or data cannot resolve the relevant crossings, report indeterminate. A recipe whose precondition says "must settle" must not erase a real failure as invalid data.

For later FFT/SNDR recipes, freeze sample grid, clock/stimulus phase, warm-up selection, coherent/noncoherent mode, window function, bins, scaling, and interpolation assumptions. Treat missing prerequisites as measurement errors rather than deleting bad cycles. Cadence's strobe article distinguishes enforced sample times from `maxstep`; that supports checking actual time samples, not assuming an option guarantees adequate sampling in every tool/profile. See [S7](#s7-cadence-strobe-and-sampling).

### Numerical repair requires a new acceptance record

Every precision/preset/tolerance/integration/step-size/initialization change is recorded, including changes implied by a simulator mode. For the initial release, no numerical changes run automatically. A human-reviewed recipe must identify a supported release and analysis, exact diff, scientific rationale, cost limit, and verification plan. Future preauthorization can cover a finite tested recipe under an explicit environment grant; a generic "fix convergence" grant is insufficient.

The verification plan should compare a defined measurement set with an independently specified tighter/reference configuration, use predeclared absolute/relative discrepancy limits, and retain both waveforms or permitted summaries. If the original run never converged, comparison against that failed run is not a reference: a separate credible reference or sensitivity study is required. These are proposed validation requirements; this planning round establishes no numerically safe parameter ranges.

Add physical and trajectory checks appropriate to the research question: DC operating-point branch, startup state, limit cycle, charge/current balance, or jitter sensitivity. Classify initial conditions, artificial conductance/capacitance aids, topology changes, and model substitutions conservatively if they can change the intended experiment. Do not relax the accuracy criterion or move the acceptance window as part of repair. A result may converge and still remain numerically indeterminate or validly fail the design target.

## 6. Change classes and authorization

The policy engine recomputes classes from the full effective diff, including expanded presets and changed dependencies; it never trusts the class supplied by the model. Unknown fields or undocumented coupled effects require review and adapter resolution. A multi-class proposal must satisfy every class, not just the least restrictive one.

| Class | Examples | Required treatment |
| --- | --- | --- |
| `observation` | Observe, reconcile, report; raw collection reads/inventories/copies identified existing artifacts. | Allowed only within configured read/copy scope. Reconnection creates no new attempt. Raw collection cannot invoke remote computation or modify source artifacts. |
| `postprocessing` | Service-owned OCEAN or other computation over identified existing artifacts. | Separate typed proposal, authorization, operation key, recipe/input fingerprints, output namespace, and `PostprocessReceipt`; never hidden inside `Executor.collect`. A changed measurement definition also requires `scientific_intent` authorization. |
| `lifecycle` | Cancel an owned job; repeat an identical execution after confirmed terminal failure. | Concrete environment/action grant, fresh ownership, unresolved-operation gate, and budgets. Cancellation alone does not create a new attempt. New execution always does. |
| `resource` | Memory/time profile, authorized host placement, threads, pending-job concurrency. | Explicit allowlist and budget; new attempt for reruns. Capture runtime/hardware differences and revalidate. No claim of bitwise equivalence. |
| `output_configuration` | Restore missing required save signals, increase acquisition coverage to the already frozen requirement. | Reviewed exact diff or narrowly preauthorized recipe; new attempt and V01–V05. Never remove required outputs. If sampling or solver behavior changes, also classify `numerical`; changed measurement meaning is `scientific_intent`. |
| `input_restoration` | Restore a confirmed wrong resolved parameter/corner/model to the original frozen intent. | Concrete human-reviewed diff and proof the target equals frozen intent; new attempt, V01–V05, and any additional numeric checks. Never edit the old attempt or its contract. |
| `numerical` | Tolerances, accuracy preset, step controls, integration method, solver mode, supported initialization aids. | Concrete human review initially; later only finite validated recipes under an explicit grant. New attempt, numerical provenance, and V06 plus all original validators. Solver/version changes require compatibility review too. |
| `scientific_intent` | Stimulus, electrical parameters/design, corner/PVT, model/section, random seed/sample, measurement definition/threshold/window, physically meaningful initial state. | Human-authorized linked experiment revision with a new `experiment_id`. Never a repair of the original result, and never automatically approved. Preserve original reports and sample roster. |

The `restore_frozen_inputs` service plan carries lifecycle and `input_restoration` plus any other applicable classes. Classify `scientific_intent` by changes to the accepted contract, and record the actual-to-proposed diff independently so restoration cannot hide its effects. A target different from accepted intent requires the new experiment route. A model cannot label that difference "restoration" to bypass review.

An authorization records the human or authenticated policy-owner principal, exact environment/execution scope, permitted action and field/value bounds, proposal/diff digest, expiry, policy version, and cost limits. An LLM response is never an authorization principal. Standing authorizations may enable bounded infrastructure recovery later, but this public example grants none. Review expiry or a changed target/diff invalidates that approval.

## 7. Durable recovery and unknown outcomes

Policy evaluation order is deterministic:

1. Reject executable text, unsupported schema/fields, non-resolving evidence, example-only runtime input, or disallowed capabilities.
2. Verify target ownership/import binding, execution generation, evidence applicability, and any input fingerprints or current observations required for that action. A read-only imported job remains read-only; unknown historical input/launch fingerprints stay null, not invented for schema completeness.
3. If submission, cancellation, separately keyed postprocessing, or recovery outcome is unknown, defer dependent mutations and reconcile the original key. Neither another diagnostic model nor another executor/account may repeat the effect. Raw collection can inspect existing outputs but cannot rerun an uncertain computation.
4. Deny recovery triggered solely by a valid specification failure. Recompute all change classes and compare the full diff with the frozen contract and authorization.
5. Enforce aggregate campaign wall time, per-attempt memory/time, retry counts, per-condition cooldowns, concurrency, and resource/diagnostic-cost caps. Reserve budgets atomically with a compare-and-swap on the attempt revision. A model fallback does not reset any counter.
6. Return `allow`, `deny`, `defer`, or `require_review` with machine-readable reasons. In M1 and the example, every lifecycle/input/resource mutation is denied regardless of other grants.
7. For an allowed mutation, persist decision, durable action key, intended new attempt if any, and an outbox record before external dispatch. Recheck revision, expiry, ownership, and lease/fencing support immediately before dispatch.
8. Persist the appropriate receipt or original-operation uncertainty and return its tagged `OperationResult`. A new submission dispatched by SpiceOwl gets its own submission key; importing a job or recording a scheduler restart does not. A retry of transport for the same uncertain operation retains its key. Do not claim exactly-once effects unless the adapter actually provides them.
9. After confirmed execution, collect identified artifacts and rerun all applicable validators. Keep failure evidence, parent attempt, parameter/resource diff, decision, receipts, and comparison reports intact.

On restart, rebuild budgets from durable reservations and receipts, reconcile nonterminal/unknown operations, and then resume observation. A timeout awaiting acknowledgement does not free a reservation. A retry of a known rejected, never-started request must still follow the adapter's documented contract and retain its original operation identity. One proposal may be superseded for explanation; that does not cancel or replay its already accepted action.

Cancellation is a separate explicit lifecycle operation. Cancellation of a chat, a diagnostic provider request, or the controller's polling task never calls `cancel_owned`. A campaign deadline stops authorization of new attempts; it only stops an already running solver if a concrete, previously accepted stop policy and verified backend capability authorize that cancellation. Otherwise report the budget overrun and preserve the execution.

### Checkpoint and scheduler restart limits

A continuation proposal needs a native checkpoint artifact digest, producer attempt, exact sample/seed identity, analysis and simulated time, netlist/model identities, solver release/mode, and an adapter compatibility attestation. Checkpoint presence alone proves nothing. New attempts use separate output directories; no waveform stitching without an explicit compatible reader and boundary checks. Validate time-origin continuity, duplication/gaps, state-dependent outputs, and the original measurement horizon after continuation.

Cadence's public APS article describes transient saved-state reuse; it does not establish cross-release, cross-model, cross-corner, Monte Carlo, or PSS/HB compatibility. A newer support-page lookup was blocked and provides no additional verified capability here. See [S8](#s8-cadence-accuracy-and-state-recovery) and [S10](#s10-discovered-but-unverified-support-pages).

Slurm's documented requeue restarts the batch script from its beginning. It is not native solver continuation. See [S5](#s5-slurm-requeue). The coordinated representation is one child attempt per scheduler restart generation, referencing the origin submission and preserving per-generation inputs/artifacts; no new SpiceOwl submission/key is invented. Native requeue integration is initially disabled. If an unexpected restart is observed before mapping is supported, retain the evidence and unresolved identity, block dependent recovery, and report the unsupported generation rather than merging outputs or creating a competing submission. When supported, externally restarted executions count toward the recovery budget even though SpiceOwl did not dispatch them; unknown restart count means budget uncertainty, not zero retries.

## 8. Monte Carlo and multi-day campaigns

Freeze the planned roster before dispatch: experiment/corner identity, sample ID/index, base seed, any per-sample or transient-noise seed, variation mode, sampler/version identity, and expected count. Capture backend sample mapping and, when authorized and supported, generated-parameter fingerprints. Never derive a sample's identity from completion order. Split batches and parallel workers must reproduce the declared roster rather than reinterpret the seed as a new sample number.

Each roster entry retains all attempts with execution outcome, measurement validity, specification result, failure/missing reason, and any numeric-repair provenance. A rerun targets the same sample and seed; if the backend cannot attest that identity, its result cannot replace that sample. Same seed alone is not a general cross-netlist/version reproducibility guarantee; an older Cadence discussion illustrates seed/run-index and topology sensitivity, not a compatibility promise for current releases. See [S9](#s9-cadence-monte-carlo-reproduction).

Choose the primary scientific result using a frozen rule such as the first valid, protocol-compliant attempt in attempt-sequence order. Preserve subsequent contradictory results and flag reproducibility uncertainty; do not select whichever passes. Numerically modified attempts count as eligible only if the campaign authorized that repair protocol before observing outcomes and the required comparison passed. Otherwise report them as separate diagnostic results. A valid failing sample cannot be resampled for yield; a separately authorized replicate campaign is a new experiment.

Report `planned`, `not_started`, `in_flight`, `valid_pass`, `valid_fail`, `invalid`, and `indeterminate` with an explicit partition of roster entries and separate counts of execution failures and retries. Nominal runs are separate from random samples. For a fixed roster of N with P valid passes, F valid failures, and U unresolved entries, report P + F + U = N. P/(P+F) is conditional on evaluable samples, not complete-campaign yield. The interval [P/N, (P+U)/N] is only a deterministic missing-result bound, not a statistical confidence interval; weighting or adaptive sampling requires a separate declared estimator.

For synthetic N=100, P=87, F=8, U=5, retain the five unresolved samples and show [87%, 92%] missing-result bounds, plus the reason breakdown. Report 87/95 as conditional only if useful. Failed simulations are not assumed missing at random. PVT grids similarly retain every planned corner and never replace a failing corner with an easier one.

Across nights, persist observation cursors, reservations, remaining retries, wait deadlines, roster progress, and queued reviews. Daily reporting summarizes changed evidence and unresolved decisions; it does not reset campaign budgets. Retention expiry and deletion are separate lifecycle policies, not debug recovery tactics.

## 9. Custodian reuse decision

Custodian's API separates `Job.setup/run/postprocess`, `ErrorHandler.check/correct`, and end-of-job `Validator.check`. Monitor handlers can terminate/correct/rerun; `is_terminating` defaults true. Validation checks return whether an error was found. Its job checkpoint archives are separate from simulator-native saved state. See [S1](#s1-custodian-api).

Reuse the separation of lifecycle, detection, and validation, plus bounded correction counters and structured error/action history. Do not directly adopt mutation inside `correct()`, a single boolean error/success model, or process-coupled supervision. SpiceOwl needs evidence references, unknown-state handling, ownership, policy/review, and immutable attempts between check and effect. If a later compatibility adapter is useful, translate `check()` into a Detection and `correct()` into a pure ActionProposal; only SpiceOwl's service can dispatch. Explicitly test the boolean polarity when adapting Validator. No Custodian dependency is required for M1, and no adapter is implemented here.

## 10. Planned file tree and slices

Only this document and its example are delivered now. P0 connection records, fake backends, and management interfaces remain the first implementation priority; the sequence below starts after P0 and does not replace the shared roadmap. The following paths are proposed future implementation, aligned with the existing scaffold:

```text
src/spiceowl/
  domain/debug.py                  # evidence, detection, proposal, decision records
  domain/validation.py             # check/measurement/report contracts
  monitoring/evidence.py           # normalization, redaction, immutable references
  monitoring/freshness.py          # source clocks, missingness, rotation generations
  monitoring/rules.py              # pure registry, applicability, predicate results
  validation/inputs.py             # V01
  validation/artifacts.py          # V02, V03
  validation/windows.py            # V04
  validation/settling.py           # first V05 recipe
  validation/numerics.py           # V06 comparison protocol
  validation/specification.py      # V07 reducer
  validation/campaign.py           # V08 roster accounting
  recovery/policy.py               # deterministic decisions and diff classification
  recovery/actions.py              # typed service dispatch; executor stays separate
  storage/debug_records.py         # decisions, outbox, budgets, lineage
  agent/diagnosis.py               # optional bounded evidence-to-proposal adapter
tests/
  fixtures/synthetic/debug/        # normalized events, analytic waveforms, expected reports
  unit/test_debug_rules.py
  unit/test_acceptance.py
  unit/test_policy.py
  fault_injection/test_recovery.py # mock service and clock; no commercial tools
```

1. **M1 slice:** typed read-only snapshots/detections and four-dimensional reports; implement D01/D09/D13, replay the existing disconnected-running fixture, persist cursors, and reject all mutations. D09 remains suspicion-only.
2. **M3 slice after executor foundations:** V01–V05/V07/V08 with analytic RC data; add all normalized fault predicates as synthetic rule coverage. A synthetic pass certifies logic, not a Spectre integration.
3. **M4 slice:** policy evaluation, durable action outbox, budget reservations, mock unknown-outcome injection, and authorization tests. Only then qualify one private execution/simulator profile for bounded infrastructure recovery under a separate authorization.
4. **M5 slice:** evidence-only model diagnosis, schema/citation validation, and comparison with the frozen deterministic baseline. Do not make model availability a monitoring dependency.

## 11. Unit and fault-injection acceptance cases

All listed tests are planned. Use a virtual clock, seeded fault scheduling, original normalized fixtures, and analytic waveforms. Do not copy real proprietary logs into public fixtures. Keep test oracle events separate from observations so detectors cannot read the injected ground truth.

| Test | Injection / input | Required assertion |
| --- | --- | --- |
| T01 | Disconnect while mock job continues; delete diagnostic session. | Original execution identity persists; zero submit/cancel actions; only observation changes. |
| T02 | Lose response after dispatch, then restart controller. | Same action/submission key is reconciled; no duplicate effect or new-key retry; reservation persists. |
| T03 | Lose cancellation acknowledgement; request another model/backend. | Dependent recovery deferred; reconcile cancellation; no provider/account/backend escape. |
| T04 | Duplicate/out-of-order events, rotated and truncated log, clock skew. | Idempotent projection, preserved generations, missingness explicit; old running event cannot undo confirmed completion. |
| T05 | License wait then success; separately terminal checkout failure and entitlement failure. | One execution for wait; cooldown/budget persists; persistent configuration failure does not retry. |
| T06 | Exit 137 only, high RSS only, then scoped OOM evidence. | First two remain uncertain; only confirmed terminal OOM permits a resource proposal, within authorization. |
| T07 | Plenty of disk bytes but quota/inodes exhausted; partial collection. | Correct evidence scope; no deletion or signal removal; solver completion remains separate from retrieval. |
| T08 | RPC timeout versus scheduler TIMEOUT versus campaign deadline. | Different detections; deadline alone never implies cancellation; no new attempt beyond budget. |
| T09 | NODE_FAIL followed by native requeue and reused job ID. | Integration is disabled initially; unsupported mapping stays unresolved and blocks recovery. A future enabled synthetic mapping creates a child attempt referencing origin submission with null own submission key, counts the restart, and produces no SpiceOwl submit. |
| T10 | Quiet factorization/PSS stage, active CPU; later corroborated no-progress window. | No universal hang claim and zero automatic cancellations; suspicion carries evidence and limits. |
| T11 | Intermediate convergence warning followed by success; distinct terminal DC/transient/PSS failure. | Warning is not terminal; stage-specific detector applicability; no automatic tolerance changes. |
| T12 | Matching signal count but wrong hierarchy/current direction; incomplete vs sealed output. | No leaf-name substitution; pending live inventory becomes invalid only when absence is established. |
| T13 | Full endpoints but interior gaps, wrong time origin, NaN, duplicate discontinuity times, sparse threshold crossing. | Recipe-specific invalid/indeterminate outcomes; no hidden padding/sorting/interpolation. |
| T14 | Same source commit with changed include/model section, units, PVT or actual seed. | V01 detects mismatch; missing hash/immutable identity is indeterminate; original attempt retained. |
| T15 | Analytic RC pass, target miss, not-settled bound, re-exit, insufficient horizon. | Numeric/bracket oracle respected; all required measurement checks passing plus V07 fail yields `valid / fail`; D14 never triggers recovery and V07 never enters measurement-check precedence. |
| T16 | Numerical recipe converges but comparison exceeds its frozen discrepancy bound. | V06 prevents valid acceptance; thresholds remain unchanged; all versions/attempts retained. |
| T17 | Partial Monte Carlo campaign; out-of-order samples; retries include a valid fail then a pass. | Roster/seed identities and missing entries preserved; no double counting or best-pass substitution. |
| T18 | Two proposals race for last retry; crash after reservation or receipt persistence. | Atomic claim; aggregate cap never exceeded; restart neither loses reservation nor authorizes duplicate work. |
| T19 | LLM fabricates evidence/approval, embeds shell in diff, or log contains prompt injection. | Schema/reference/policy rejection; zero job-control effects; known rules continue. |
| T20 | Approval expires or target/diff changes before dispatch; imported observe-only job. | Recheck blocks dispatch; review of one proposal does not authorize another job or change. |
| T21 | Waveform offered as checkpoint; checkpoint from another seed/corner/release. | Reject before dispatch; no inferred native continuation capability. |
| T22 | Validator crashes/format unsupported; sealed historical report becomes stale to live observer. | Indeterminate new check, no fabricated pass; historical content-bound report retained. |
| T23 | Denied example fixture loaded as runtime config; arbitrary unknown action or field. | Fail closed before any executor call even if a mutation grant is supplied elsewhere. |
| T24 | Model timeout or cancellation during diagnosis or after a service action was accepted. | Deterministic loop and accepted action lifecycle continue independently; no implicit solver stop. |
| T25 | Two valid measurements have pass/fail V07 results; then make only measurement B fail V03 or become indeterminate. | First aggregate is `valid / fail`; then `invalid / not_evaluated` or `indeterminate / not_evaluated`. Measurement A stays `valid / fail`; only B's measurement status changes aggregate validity. Reject V07 in the measurement-check list. |
| T26 | Import a running external job with explicit import binding but no known historical submission/input/launch fingerprint. | Nulls remain null, observation uses the verified binding, no submit is invented, and missing provenance prevents valid acceptance or input-dependent recovery. |
| T27 | Collect existing artifacts, then lose acknowledgement for separately keyed OCEAN postprocessing. | Raw collect executes no computation. Postprocessing uses its own key and `PostprocessReceipt`; `OperationResult` references the receipt or original unresolved operation, and reconciliation never repeats computation with a fresh key. |

Add property tests for permutation-invariant event replay within source-order constraints, retry budget monotonicity, immutable frozen criteria, sample-roster conservation, and no mutation under unresolved outcome. A separate private integration suite must verify release-specific message parsing, identity/requeue mapping, signal inventory, license behavior, and checkpoint compatibility before claiming backend support. This round runs none of those private tests.

## 12. Metrics for later research comparisons

Compare a frozen rule baseline with rules plus model advice, and optionally human adjudication, on the same hidden-ground-truth scenarios and fixed action budgets. Split by fault family, simulator release, analysis stage, and testbench family to limit near-duplicate leakage. Keep prompt/model versions and inference settings with results; model diagnosis is nondeterministic even when event replay is deterministic. All metrics below are proposed, with no measured results yet.

| Measure | Definition / reporting rule |
| --- | --- |
| Detection quality | Per-condition precision/recall, confusion matrix, abstention rate and coverage; distinguish confirmed from suspected labels. Count alerts per incident rather than repeated log line. |
| Detection latency | Event onset to supported detection using virtual ground-truth time; median/p95 and observation cadence; report undetected/censored incidents separately. |
| Stall false alarms | Suspected stalls per healthy job-hour, stratified by analysis stage; separately count unsupported termination proposals. |
| Unsafe effect count | Duplicate executions, mutations under unknown outcome, unauthorized changes/cancellations, false scientific passes. Mandatory target zero; report raw counts and exposure, not just averages. |
| Scientific validity | Correctly preserved valid design failures, false-valid and false-invalid measurements, numerical discrepancies against predeclared reference limits. |
| Recovery effectiveness | Eligible confirmed infrastructure failures restored to valid measurement / all eligible failures; exclude design misses from repair eligibility. Include unrecovered cases and timeouts. |
| Time and resource cost | Time to interpretable result, reserved/consumed core-hours, memory peak with scope, storage, license wait/runtime when observable, model tokens/cost. Unknown costs remain unknown. |
| Evidence quality | Fraction of diagnoses/proposals whose cited evidence resolves and supports the claim; unsupported causal claims, contradictory evidence omitted, schema rejection rate. |
| Human burden | Reviews per fault, approval/rejection distribution, operator time, unnecessary alerts; never optimize burden by removing required approval. |
| Recovery reproducibility | Same-sample replay eligibility, measurement discrepancy, checkpoint-boundary violations, and fraction needing numerical revalidation. |
| Campaign accounting | Planned roster conservation, unresolved fraction, repeated samples, lost seeds, and bounds with missing samples; never compare yield only among recovered survivors. |
| Durability | State restored after restart, unreconciled-operation duration, duplicate-effect count, and journal/receipt completeness under injected crashes. |

For stochastic model comparisons, predeclare replicate counts and report variability and failed runs. Public synthetic results demonstrate logic and failure handling only. Performance or scientific-equivalence claims about real circuits require an authorized private benchmark with fixed intent and independently verified reference measurements.

## 13. Primary sources and verification limits

All sources below were checked on **2026-11-13**. This is a public-document review date, not a local integration-test date. No login, paid inference, SSH, simulator command, dependency installation, private PDK/model access, or private simulation-log inspection was performed. Links identify documentation rather than vendored dependencies.

### S1 Custodian API

[Materials Project: custodian.custodian API](https://materialsproject.github.io/custodian/custodian.custodian.html). Public API documentation read, including Job, ErrorHandler, Validator, monitor/termination defaults, correction limits, and job-level checkpoint description. Unpinned documentation; pin a package release and recheck implementation before any future adapter. This source informs boundary reuse, not remote execution guarantees.

### S2 Slurm execution states

[SchedMD: Job State Codes](https://slurm.schedmd.com/job_state_codes.html). Public state/flag table read. The adapter must preserve base/flag distinctions and qualify behavior against the chosen installed release. No local cluster was inspected.

### S3 Slurm pending reasons

[SchedMD: Job Reason Codes](https://slurm.schedmd.com/job_reason_codes.html). Public reasons table read, including `Licenses` and resource/policy reasons. A displayed reason need not enumerate every blocker. Site integration with a commercial license system remains unknown.

### S4 Slurm accounting

[SchedMD: sacct](https://slurm.schedmd.com/sacct.html). Public documentation read for job/step records, duplicates, metrics and missingness. Available identity fields and accounting access differ by selected profile. No specific new field such as SLUID is made a mandatory cross-version assumption.

### S5 Slurm requeue

[SchedMD: sbatch](https://slurm.schedmd.com/sbatch.html). Public requeue semantics checked: the batch script begins again. This motivates restart-generation lineage and prevents treating scheduler requeue as solver continuation. Configuration and permissions still require profile qualification.

### S6 Cadence save behavior

[Cadence: Spectre APS Save Overview, Part 1](https://community.cadence.com/cadence_blogs_8/b/cic/posts/spectre-tech-tips-spectre-aps-save-overview---part-1), published 2019-05-29. Public vendor article read. It describes selected signals, hierarchy/type-aware save controls, and output/performance effects; it explicitly identifies historical release changes. It supports inventory validation, not universal option semantics.

### S7 Cadence strobe and sampling

[Cadence: Using the Spectre Strobe Feature](https://community.cadence.com/cadence_blogs_8/b/cic/posts/spectre-tech-tips-using-the-spectre-strobe-feature), published 2022-04-28. Public vendor article read. Strobe sample times, interpolation, and simulator-specific option caveats inform the proposed sampling checks. No option from this article is automatically applied.

### S8 Cadence accuracy and state recovery

[Cadence: Optimizing Spectre APS Performance](https://community.cadence.com/cadence_blogs_8/b/cic/posts/spectre-optimizing-spectre-aps-performance), published 2019-01-24. Public vendor article read. Accuracy/performance options and transient save/recover support the need for numeric change classification and native compatibility checks. The article is not evidence that any setting preserves every analog measurement or works across tool releases.

### S9 Cadence Monte Carlo reproduction

[Cadence forum: monte-carlo simulation running one seed](https://community.cadence.com/cadence_technology_forums/f/custom-ic-design/22456/monte-carlo-simulation-running-one-seed). Public historical discussion read, especially Andrew Beckett's explanations of seed versus run number and topology-sensitive mismatch reproduction. Treat it as a scoped vendor-hosted explanation of older behavior; current statistical-corner/mismatch identity support needs separate verification.

### S10 Discovered but unverified support pages

[Cadence: Using Spectre Save-Recover](https://support1.cadence.com/public/docs/content/20470407.html) and [Cadence: Virtuoso ADE Assembler — Monte Carlo Mismatch ID](https://support1.cadence.com/public/docs/content/20508386.html) were discovered in search, but direct access returned HTTP 403 on the check date. Their complete contents and reported release qualifications were not verified. They are follow-up leads only; no automatic recovery or sample-equivalence claim relies on them.

## 14. Coordinator decisions and unresolved interface differences

No shared files are changed by this workstream. P0 remains AI connection management. The next debug implementation after P0 is D01 plus the four-dimensional report and read-only policy gate using the existing synthetic disconnect fixture. Acceptance: monitor/session interruption changes freshness, preserves the same attempt, and produces zero simulator lifecycle calls.

The following coordinator decisions are incorporated; the remaining schema/profile details are explicitly identified:

The [shared boundaries](../contracts/interface-boundaries.md#shared-handoff-records) and [integrated plan](integration-plan.md) settle the proposal, change-class, sample-identity and freshness recommendations below. Concrete schemas, recipes and capability profiles remain implementation work.

| Item | Proposed resolution / unresolved boundary |
| --- | --- |
| Shared proposal contract | Adopt the five envelopes above or merge equivalent fields; keep `proposal_id`, `action_key`, and authorization distinct. `ActionProposal` implements the coordinated Proposal stage. Capability evidence retains documented/advertised/locally checked/integration-tested provenance. |
| Receipt naming — resolved | `ExecutionReceipt` binds native execution/import identity, `ActionReceipt` records lifecycle actions, and `PostprocessReceipt` records separate computation. Dispatcher returns tagged `OperationResult` references, not duplicate dispatch truth. The concrete tag/reference schema still needs shared implementation. |
| Runtime recovery actions — resolved | Keep existing executor verbs authoritative. `retry_*` and `restore_frozen_inputs` are service plans, not raw executor commands. `Executor.collect` only reads/inventories/copies existing artifacts; OCEAN is separately keyed and authorized service postprocessing. |
| Input restoration | This proposal adds a narrowly reviewed `input_restoration` class. It requires the proposed inputs to equal frozen intent. Decide the shared class vocabulary; restoration must not become an exemption for changing scientific intent. |
| Import identity — resolved | Imported external jobs have null `submission_key` and explicit import binding. Unknown historical input/launch fingerprints remain null. No fictitious submission or present-day fingerprint substitutes for historical evidence. |
| Requeue identity — resolved, initially disabled | Each scheduler restart generation is a child attempt referencing origin submission; its own submission key is null and no SpiceOwl submit is invented. An unknown imported origin keeps its null submission and import binding. Unsupported mapping blocks dependent recovery until enabled and tested. |
| Roster naming | Reuse manifest `monte_carlo_sample_id`; local envelope `sample_id` is only its normalized view. Agree a campaign roster contract and actual sample-parameter identity support with the simulator workstream. No seed-only reproducibility promise. |
| Aggregate acceptance — resolved | Required measurement checks are V01–V05 plus applicable V06/V08; V07 evaluates separate specification predicates and never changes validity. T15/T25 cover target failures and mixed aggregates. Recipe resolution/uncertainty fields still need shared implementation. |
| Freshness envelope | Keep per-source freshness plus the shared observation summary and historical as-of timestamp. Define which sources gate each action; a fresh scheduler reply must not freshen unavailable artifacts. |
| First real profile | Simulator version/mode, scheduler/accounting setup, parser messages, stage thresholds, private artifact access, and checkpoint compatibility remain unverified. No real-environment grant exists in this design. |
| Numerical protocol | A researcher must select the reference configuration, discrepancy limits, branch/trajectory checks, and allowed repair recipes for the first private use case. No universal tolerance ladder is proposed. |
| Recovery authorization | M1 is read-only; future grants bind concrete environments/actions. Review is hosted by the service/UI and cannot be manufactured by a diagnostic agent. Cancelling the diagnostic session has no simulator effect. |

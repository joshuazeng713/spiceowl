# Shared interface boundaries

Status: draft `0.1`, 2026-11-13. Use this vocabulary across the three design workstreams. Names are proposed contracts, not implemented Python APIs.

## Independent identities

- `connection_id`: a selected agent runtime or model-API configuration owned by a user on an AI host, which may differ from the simulation host.
- `agent_session_id`: a diagnostic/conversation session on that connection, mapped to a provider session ID when supported.
- `experiment_id`: the scientific intent and acceptance requirements.
- `attempt_id`: one execution attempt with frozen inputs and resource settings.
- `submission_key`: one SpiceOwl submission operation's durable identity; required before dispatch, nullable for an explicitly imported external execution.
- `execution_identity`: the authoritative native job/process/run mapping, including its host or scheduler scope.
- `event_id`: a stable observation/evidence event identity with source and timestamps.
- `proposal_id` and `action_key`: a proposed action and an authorized dispatch identity; they are not interchangeable.

An agent session does not own the simulator process. Disconnecting, cancelling, switching, or deleting a conversation does not cancel a simulation. A model change does not imply transferable provider-native session state.

AI-only `turn_id`, `request_key`, `connection_revision`, and `auth_epoch` identify inference and configuration history. They cannot substitute for simulator submission/action keys. `execution_generation` identifies a native invocation; `parent_attempt_id` and `origin_submission_key` preserve restart lineage. `postprocess_id` identifies a separate derived-data computation. Use the manifest's `monte_carlo_sample_id` as the canonical sample identity; adapter `sample_id` fields are mapped views of it.

## AI boundary

Distinguish `AgentBackend` (an existing runtime with its own session/tool lifecycle) from `ModelBackend` (inference endpoints used by a SpiceOwl-owned diagnostic loop). Expose them through a connection manager but do not pretend their capabilities are identical.

Use provider-owned login flows or explicit user-provided API credential references. Do not turn subscription credentials into a general API pool. A provider's advertised models do not prove that the current account may call them. Unknown quota/cost is unknown, not zero.

The first diagnostic mode receives a bounded evidence package and emits a structured proposal. It has no direct execution authority. A proposal contains target identities, action kind, evidence references, intended parameter/resource diff, known uncertainty, and validation requirements.

The selected implementation order is fake backends and registry, then native local Codex app-server plus the official OpenAI Python SDK API-key route, then ACP's official Python SDK. LiteLLM is a later optional normalization adapter once a second API provider requires it. These are design selections, not installed dependencies or verified integrations. See the [AI design](../design/ai-connections.md) for dated upstream evidence and authentication limits.

## Simulator boundary

Separate executor operations from simulator-specific preparation, parsing, and native saved-state support. Preserve submission uncertainty until an authoritative identity is reconciled. Distinguish workflow persistence, reconnection to an active job, and native solver checkpoint continuation.

The simulation service evaluates authorization and policy before dispatch. Neither an LLM response nor a log message can approve a job operation. Existing external jobs may begin in observe-only mode until ownership is established.

An import receipt binds an explicitly selected native execution and `ownership_mode=external_observe_only`. Its `submission_key` is null, and missing historical input/launch fingerprints remain null with reasons. `reconcile(null, import_receipt)` is valid; `submit` without a key is not. Import cannot manufacture a dispatch history or establish control rights.

Every solver invocation has its own attempt. Future native scheduler restarts create a child attempt with the native generation and original submission reference; a restart with no new SpiceOwl submission has no new `submission_key`. Reconciliation follows the original submission/native binding, never a fabricated send. Preserve generation-specific artifacts and count restarts in budgets. The initial backend does not enable automatic requeue recovery; if generation mapping cannot be established, retain uncertainty and block competing submissions.

`ExecutorAdapter.collect` only inventories/references/copies identified existing artifacts under a configured read and destination scope. Running OCEAN or another derivation is a separately authorized postprocessing task with `postprocess_id`, durable `action_key`, resource limits and its own receipt. It does not create a new simulation attempt. A failed postprocessor cannot relabel solver completion or trigger an automatic simulation rerun.

## Debugging boundary

Produce evidence-backed diagnoses and candidate actions. The policy layer decides whether an action is permitted; the executor reports the outcome; validators check the resulting experiment.

Keep execution outcome, observation freshness, measurement validity, and specification outcome separate, as defined in [architecture](../architecture.md). Valid design failures must be preserved. Keep Monte Carlo sample/seed identity and unresolved/missing samples explicit.

An unknown result after a side-effecting operation triggers reconciliation, not an automatic retry through another model, backend, account, or submission key.

Required measurement checks determine validity; specification predicates run only on valid measurements. A failed specification predicate (V07 in the debug design) never enters the measurement-invalid reducer. Preserve per-measurement outcomes while aggregate specification remains `not_evaluated` if another required measurement is unresolved. Freshness is tracked per source; a fresh scheduler reply cannot freshen inaccessible waveform evidence.

Recovery plans such as `retry_with_resource_profile` and `restore_frozen_inputs` are service-level plans, not new executor verbs. Input restoration must target the original frozen intent and create a new attempt after review. A changed accepted intent creates a linked experiment. The change classes are `observation`, `postprocessing`, `lifecycle`, `resource`, `output_configuration`, `input_restoration`, `numerical`, and `scientific_intent`; effective diffs can require several classes together.

## Shared handoff records

The logical flow is `Evidence -> ActionProposal -> PolicyDecision -> OperationResult -> ValidationReport`. Read-only reports can stop before dispatch; a proposal is never required merely to display existing evidence. The detailed payloads remain drafts in the owned designs.

| Record | Coordinator decision |
| --- | --- |
| `EvidenceSnapshot` / bounded AI package | Bind experiment/attempt, event IDs, immutable content digest and locators, source and observed times, per-source freshness, missingness, redaction and allowed export scope. The AI package is an authorized projection with its own digest and source snapshot reference. It contains no authority or arbitrary path access. |
| `ActionProposal` | Include proposal ID, producing connection/session/turn when applicable, target identity, cited snapshot/events, typed action, full intended diff, uncertainties and required validators. Recovery recomputes classification and resolves references. |
| `PolicyDecision` | Bind proposal/diff digests, policy revision, target revision/generation, capability/ownership evidence, grant reference and budgets. Only the service issues a dispatch action key after an allowed decision. A runtime permission answer is a different record. |
| `ExecutionReceipt` | Immutable binding of an accepted simulator execution or explicit external import, as defined by the simulator workstream. It is not a generic action result or a scientific verdict. |
| `ActionReceipt` | Tracks a requested lifecycle action, decision/grant, exact target, payload digest and requested/confirmed/rejected/unknown outcome. A retry plan links its new attempt and execution receipt. |
| `PostprocessReceipt` | Tracks a distinct derived-data task, source artifact manifest, recipe/version, action key, grant, execution identity, result manifest and outcome. It refers to the source attempt without overwriting it. |
| `OperationResult` | Tagged service result: execution, action, collection, or postprocess; reference the authoritative receipt/manifest, or an unresolved operation record. It is not a second receipt store. Read-only collection can return a manifest directly. |
| `ValidationReport` | Immutable, versioned check results and measurement/specification outcomes tied to a frozen contract and artifact snapshots. Superseding a report preserves its history and reason. |

Shared contracts live under `domain`; provider code under `agent/connections`; simulator/executor implementations under `adapters`; service composition, dispatch and postprocessing under a future `execution` package. The coordinator owns common schema migrations through `storage`. All workstreams use `tests/contracts/` and `tests/fault_injection/` when those tests are implemented. No new runtime directories are implied by this design document.

## Event and capability rules

- Record source time and observation time separately when available. Handle duplicate and out-of-order delivery.
- Capabilities include provenance: documented, advertised, locally checked, or integration-tested. Do not collapse these levels into one `supported=true` flag.
- Optional operations such as session resume, logout, quota reporting, and native checkpoint recovery must be negotiated or verified; absent capability means unavailable.
- Permission callbacks and protocol capabilities are not an OS sandbox. A diagnostic runtime must not inherit unrestricted access to simulation credentials or job-control commands.
- Store secret references and sanitized metadata in ordinary records. Provider credentials, API keys, and tokens do not belong in examples, transcripts, Git, or debug exports.

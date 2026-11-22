# P0.1 implementation contract

This bounded implementation follows the design drafts. Python 3.11+; standard-library runtime; local single-user prototype. Frontend resources ship with the Python package. There is no provider authentication, inference, remote execution, or real simulator control in this slice. The user accepted bringing a small web console forward into this milestone.

## Workstream boundaries

The coordinator owns domain helpers, SQLite, application service, CLI, HTTP, frontend and integration tests. AI owns `agent/connections/` and its tests. Simulator owns `adapters/executors/mock.py` plus package initializers and its tests. Debug owns `validation/acceptance.py`, `recovery/policy.py`, `agent/diagnosis.py` and its tests. Each works in an isolated checkout and returns a commit for integration. No real environments or credentials are accessed.

All functions below accept/return JSON-compatible dictionaries. Raise `spiceowl.domain.common.DomainError(message, code)` for invalid inputs. Mutations of input arguments are forbidden. Shared helpers provide `utcnow`, `new_id`, `digest`, `require_id`.

## AI functions

`agent.connections.config.validate_connection(data) -> dict` validates a single configuration. Allowed fields: `connection_id`, `label`, `kind`, `backend`, `scenario`, `enabled`. Required: label, kind (`agent` or `model`), backend (`fake_agent` or `fake_model`, matched to kind). Optional ID generated if omitted, enabled defaults true, scenario defaults success. Scenarios: `success`, `inference_denied`, `disconnect`, `permission_denied`. Reject unknown keys, example-only drafts before side effects, real backends, credentials/URLs, duplicate IDs at storage layer. Return normalized fields plus `synthetic: true`. Label <=80 printable characters. All public interfaces reject unknown/invalid scenario values.

`agent.connections.backends.fake.probe_connection(connection, level) -> dict` returns a readiness vector: configuration, transport, authentication, catalog, inference_access, isolation, synthetic. Levels are transport/auth/models; a metadata probe must leave inference_access unverified. Auth is explicitly simulated; no real-account verification.

`agent.connections.backends.fake.generate_events(connection, evidence, question) -> Iterator[dict]`: yield bounded original synthetic events `{kind, payload}`. Kinds: `text.delta`, `permission.denied`, `usage.observed`, `proposal.ready`, `turn.terminal`. Terminal payload status is completed/failed/outcome_unknown. Stream disconnect emits partial text and outcome_unknown, with no complete proposal. No sleeps, IO, credentials, providers or subprocesses. Terminal usage cost is null, not zero real billing. `permission_denied` must record a denied shell request; never execute it. Scenarios are controlled by trusted connection config, not question/evidence text.

`proposal.ready` payload is a proposal matching the debug contract below; use current evidence IDs. Question length <=2000; treat it as data. Diagnostics may distinguish the four run dimensions but never imply a simulator was run. AgentBackend and ModelBackend protocols/classes remain distinct even though fake generation is shared.

## Synthetic runs

`adapters.executors.mock.list_scenarios() -> list[dict]` returns scenarios with `scenario`, `label`, `description`. IDs: `disconnect`, `oom`, `completed`, `target_miss`.

`create_run(scenario, run_id) -> dict`: run_id is coordinator-generated. Return run_id, experiment_id, attempt_id, submission_key, execution_identity (dict), label, scenario, synthetic=true, created_at, updated_at, cursor=0, execution, observation, measurement_validity, specification_result, progress (0..1 or null), resource_history (bounded list), events (bounded list), finished (bool). `execution_identity` remains stable through reconnect. A sample resource entry is `{sequence, simulated_time_us, memory_mb}`; null means unavailable. An event is `{event_id, sequence, kind, observed_at, message, facts}`. Initial event sequence >=1; use deterministic IDs scoped to run, so replay is idempotent.

`advance_run(run) -> dict`: return a copy with the next finite synthetic observation, updated cursor/time and appended event/resource points. Terminal replay is a no-op. Disconnect keeps last confirmed execution state and changes observation to unknown, then reconciles same identity. OOM is explicitly synthetic confirmed execution failure with invalid/indeterminate measurement. Completed final is valid/pass; target_miss is valid/fail. No submit/cancel APIs or real job control. Keep charts/fixtures finite and original. Controller advances these only on explicit demo replay action; no wall-clock simulator activity is claimed.

## Debug functions

`validation.acceptance.aggregate_acceptance(measurements) -> dict`: list of `{measurement_id, checks: [pass|fail|pending|indeterminate], specification: pass|fail|not_evaluated}`; reject malformed or empty checks, duplicate IDs, invalid verdicts. Return measurement_validity, specification_result, measurements with independent validity/specification. Only checks determine validity; invalid/unknown dependencies force not_evaluated. Aggregate any fail > indeterminate > pending > valid; aggregate specification waits for all valid and evaluated.

`agent.diagnosis.build_evidence(run) -> dict`: bounded authorized projection with experiment_id, attempt_id, execution_identity, synthetic=true, four dimensions, events (event_id/message/facts), evidence_digest. No arbitrary paths or raw data import. Use immutable copies and strict bounded inputs.

`validate_proposal(proposal, evidence) -> dict`: strict fields `proposal_id`, `experiment_id`, `attempt_id`, `kind`, `summary`, `evidence_event_ids`, `uncertainties`, `diff`, `synthetic`; optional producer session/turn fields are added by service later. All references must resolve to this snapshot; target IDs must match; reject unknown keys, executable text/commands fields, unsupported kinds, forged authority. Kind report/observe/reconcile/cancel_owned/retry_with_resource_profile/change_inputs; diff is a bounded list of `{field,before,after}` over typed allowlisted fields, no shell. Preserve valid design misses as report-only in policy. Model-supplied fields never authorize an operation.

`recovery.policy.evaluate_policy(proposal, run) -> dict`: decision_id, proposal_id, result allow/deny, reason, dispatch_enabled=false, synthetic=true. This prototype permits only report/observe/reconcile suggestions; denies every simulator mutation, and any retry for a valid design failure. No action keys/grants issued and no execution methods called. All proposals are advisory in UI.

## Service and UI

Coordinator API: GET /api/state; POST /api/demo (idempotently seed two connections and four run scenarios); POST /api/connections; POST /api/connections/<id>/probe; PATCH/DELETE /api/connections/<id>; GET /api/runs/<id>; POST /api/runs/<id>/advance; POST /api/diagnose `{connection_id,run_id,question,request_key}`; GET /api/sessions/<id>?after=N; POST /api/turns/<id>/cancel. Responses are JSON; errors `{error:{code,message}}`. IDs/keys validated. GET state includes connections/runs/sessions, capabilities and application version. Session view includes session, turns, durable events. Durable event envelope: event_id, sequence, session_id, turn_id, kind, payload, observed_at. Request-key deduplication is global in this prototype; same key with different payload conflicts.

Serve only on loopback; same-origin mutating requests need an application CSRF token/header; reject foreign Host/Origin and oversized/invalid JSON. No arbitrary files or commands via HTTP. Browser assets use no external fonts/scripts/CDNs. UI offers Overview, Connections, Run details, context diagnosis with event history and explicit synthetic labels. Polling replays persisted events; it does not redispatch inference. A service crash preserves a started turn as outcome_unknown on restart, never automatically replays it. Unknown turns block dependent new diagnosis until reviewed; this slice can inspect them, not claim a recovered provider operation.

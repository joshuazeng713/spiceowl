# Roadmap

This is a milestone plan, not a release or delivery-date promise. M0 and the bounded P0.1 offline prototype are implemented. A milestone closes when its observable acceptance criteria pass.

## M0 — Project foundation (current)

- [x] English and Chinese README.
- [x] MIT license preserved from the original repository.
- [x] Architecture, run contract, and implementation boundaries.
- [x] Editable brand icon and avatar export.
- [x] Illustrative manifest and synthetic failure scenario.
- [ ] Maintainer records the first supported Cadence/version/execution profile.

The repository now includes an installable alpha package, local CLI/web console and synthetic replay. No real provider or simulator integration is certified. See the [local console guide](local-console.md).

## P0.1 — Offline local console (implemented)

- [x] Separate fake AgentBackend/ModelBackend contracts and strict configuration.
- [x] Connection registry, metadata probes and explicit readiness dimensions.
- [x] SQLite turns/events, duplicate-request handling and interrupted-turn recovery.
- [x] Bounded synthetic evidence, validated proposals and advisory-only policy.
- [x] CLI and packaged browser UI: overview, connections and run details.
- [x] Four finite synthetic run scenarios with explicit manual replay.
- [x] Public tests for domain contracts, service restart/deduplication and loopback API boundaries.

The small web console was brought forward at the maintainer's request. Synthetic replay does not close M1's continuous-monitor requirements. Provider credentials, real inference, durable external execution and automatic recovery remain absent.

## P0 — Agent and model-API connections (partially implemented)

Added at the maintainer's request on 2026-11-13. See [workstreams](workstreams.md) and the [connection-layer decision](decisions/0002-connection-layer.md). This milestone precedes M1; the simulation reliability and scientific acceptance boundaries remain unchanged.

Deliver connection configuration/registry, separate agent-runtime and model-API contracts, capability-aware auth/session state, secret references, usage/budget records, and an initial management interface. Begin with deterministic fake backends, then verify one official local Codex path and an API-key route through the official OpenAI Python SDK before broadening ACP support. The [integrated implementation plan](design/integration-plan.md) defines P0.1–P0.3 and their observable demos. LiteLLM remains a later optional adapter.

P0.2 now has a bounded connectivity slice: private SSH profiles, explicit identity/tool-version probes, a browser host view, and optional read-only stdio MCP tools. Public tests use a fake SSH peer and an actual local MCP handshake. These checks do not close M1 or M2. Native agent-runtime adapters, real API inference, persistent credential entry and account-scoped usage limits remain planned. See the [connectivity contract](contracts/p02-connectivity.md).

Acceptance:

- Add, list, inspect, disable, and remove connection metadata without invoking a model or logging credentials.
- Distinguish configuration validity, transport reachability, login status, and a successfully tested inference request.
- Negotiate optional operations and expose unsupported/unknown capabilities honestly.
- Preserve the connection owner/host and provider session mapping; do not imply cross-provider native-session portability.
- Deny paid fallback unless it is explicitly configured within a budget; record model switches and their new session/context boundaries.
- Keep provider-session cancellation separate from simulation cancellation.
- Keep all public tests independent of subscriptions, API keys, Cadence, and private servers.
- Real login and small paid smoke tests are separately authorized, opt-in integration checks, not automatic discovery.

## M1 — Read-only monitoring

Deliver a Python CLI, mock executor, durable attempt records, incremental log observations, and a plain structured report. Reuse the packaging introduced by P0; Python 3.11+ is the current proposal, subject to selected dependency support.

Acceptance:

- Run deterministic synthetic scenarios without a simulator or model credentials.
- Restart the monitor and restore the same attempt and observation history.
- Distinguish connection loss from a confirmed process/job failure.
- Handle log rotation and duplicate observations without losing event identity.
- Report execution, observation freshness, measurement validity, and specification outcome separately.
- Document that M1 cannot submit, cancel, modify, or recover real jobs.

## M2 — One reliable execution backend

Select one Linux execution profile and one simulator version. Implement immutable input capture, preparation checks, submission receipts, reconciliation, owned-job cancellation, artifact collection, and input provenance. Evaluate Prefect integration against these semantics.

Acceptance:

- Disconnect the client after submission; the executor retains ownership.
- Interrupt the controller between dispatch and receipt persistence; restarting it reconciles the original job or retains an explicit unresolved state.
- Deliver duplicate submission/action requests; the adapter deduplicates or blocks them safely.
- A cancellation request targets only the verified execution owned by that attempt.
- Record simulator version, resolved netlist/model dependency identity, execution profile, and actual input fingerprint.
- Publish precisely what the selected profile supports. Real-tool tests run only in a configured private environment.

## M3 — Experiment validation

Implement validators for input consistency, required signals, analysis completion, measurement-window coverage, and one useful analog measurement recipe.

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

## M5 — Diagnostic agent and console

Add typed agent proposals and a web console over the stable run API. The console shows a run list, progress evidence, resource trends, validation results, and recovery history. An optional notification integration delivers meaningful changes and a final report.

Acceptance:

- Known monitoring/recovery rules continue when the model is unavailable.
- Every agent diagnosis cites stored evidence and can express uncertainty.
- Every proposed action passes the same policy checks as non-agent actions.
- Untrusted log/model content cannot authorize a command or change a policy.
- Clearly identify implemented, experimental, and unsupported capabilities.

## First implementation slices

1. Define connection records, capability/auth/session contracts, and fake agent/API backends.
2. Add metadata persistence and a local management interface with secret-reference handling.
3. Add one documented Codex route and one API-key route with isolated, opt-in smoke tests; expand ACP after the common contracts pass.
4. Return to M1: implement the mock simulation monitor and durable observation/report flow.
5. Inject duplicate events, rotated logs, lost responses, and missing results before real submission/recovery.

## Later, after the first backend works

- Additional site schedulers and native ADE integrations.
- PVT and Monte Carlo batch policies.
- More analysis-specific validators and checkpoint compatibility profiles.
- Local or hosted model providers behind the same proposal interface.
- Multi-user operation, stronger isolation, and resource accounting.

These are candidates, not current features. Automatic circuit optimization, unattended schematic editing, and arbitrary control of unrelated jobs are outside the initial scope.

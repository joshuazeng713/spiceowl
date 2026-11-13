# Roadmap

This is a milestone plan, not a release or delivery-date promise. Only M0 exists today. A milestone closes when its observable acceptance criteria pass.

## M0 — Project foundation (current)

- [x] English and Chinese README.
- [x] MIT license preserved from the original repository.
- [x] Architecture, run contract, and implementation boundaries.
- [x] Editable brand icon and avatar export.
- [x] Illustrative manifest and synthetic failure scenario.
- [ ] Maintainer records the first supported Cadence/version/execution profile.

The repository is documentation and scaffold only. There is no executable CLI, running service, tested simulator integration, or released package.

## M1 — Read-only monitoring

Deliver a Python CLI, mock executor, durable attempt records, incremental log observations, and a plain structured report. Packaging and its minimum Python version are introduced here; Python 3.11+ is the current proposal.

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

## First three implementation slices

1. Add the domain models and a mock observation source for the disconnected-but-running fixture.
2. Persist observations locally and produce a read-only report that survives restart.
3. Add failure injection for duplicate events, rotated logs, and missing results before implementing real submission.

## Later, after the first backend works

- Additional site schedulers and native ADE integrations.
- PVT and Monte Carlo batch policies.
- More analysis-specific validators and checkpoint compatibility profiles.
- Local or hosted model providers behind the same proposal interface.
- Multi-user operation, stronger isolation, and resource accounting.

These are candidates, not current features. Automatic circuit optimization, unattended schematic editing, and arbitrary control of unrelated jobs are outside the initial scope.

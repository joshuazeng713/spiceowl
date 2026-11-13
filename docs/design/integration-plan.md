# Integrated implementation plan

Status: coordinated design, 2026-11-13. No runtime implementation or real integration is delivered by this plan. The three specialist designs are [AI connections](ai-connections.md), [simulator connections](simulator-connections.md), and [simulation debugging](simulation-debug.md); [shared boundaries](../contracts/interface-boundaries.md) resolve their interface recommendations.

## Selected route

Build the first usable capability around connection management. The initial user is a single researcher running a local controller, with a simulation host that may be separate. Connect an existing agent runtime through `AgentBackend`; connect inference endpoints through `ModelBackend`. The distinction preserves authentication, sessions, tools, billing and cancellation semantics.

| Stage | Concrete deliverable | Completion evidence |
| --- | --- | --- |
| P0.1, first code change | Python packaging, tagged connection configuration, registry, fake agent/API backends, SQLite session/event records and a local CLI | Offline synthetic diagnostic turn; restart restores the same session/events; duplicate request does not redispatch; permission escalation denied |
| P0.2 | Official local Codex app-server adapter and official OpenAI Python SDK API-key adapter | Fake protocol/HTTP peer tests first; separately authorized, bounded real smoke tests record exact versions, selected route/model and completed inference |
| P0.3 | Official ACP Python SDK client adapter | Echo fixture and capability/cancellation/replay tests; qualify one selected runtime before expanding the matrix |
| M1 | Mock simulation monitor, durable cursors and four-dimensional report | Disconnect while job continues: preserve attempt identity, mark observation unknown and issue zero submit/cancel calls |
| M2 | Independent Linux runner with synthetic process tests; optional ngspice RC test; then one private Spectre profile | Lost receipt reconciles without a duplicate run; execution identity and actual inputs verified for the selected environment |
| M3–M4 | Measurement validators, then bounded recovery | Preserve valid target failures; test unknown outcomes and budgets; real actions require the selected environment's grants |
| M5 | Evidence-backed agent diagnosis integrated with a web console | The rule monitor survives provider outage; every proposal passes service policy and retains evidence |

P0's synthetic diagnostic turn demonstrates the connection pipeline only. It does not claim scientific diagnosis, replace M3 validation, or implement M5's production console. LiteLLM is a later optional API adapter; T3 Code and AionUi remain design references. The AI design contains the upstream evidence and license/authentication qualifications for these choices.

For the simulator path, begin with exported, frozen netlists and batch Spectre in one qualified environment. Import existing ADE jobs read-only with explicit native identities. If the site already uses a scheduler, retain its ownership. Native ADE control, additional analysis families and checkpoint continuation have separate qualification gates.

## First runnable offline demonstration

P0.1 should let a contributor:

1. Create a synthetic connection through the future CLI; listing or inspecting it resolves no secrets and starts no network request.
2. Probe a fake runtime and see transport, authentication, catalog and inference verification as distinct states.
3. Start one diagnostic turn with an original bounded evidence fixture, then see streamed events and a schema-valid proposal citing those fixture events.
4. Reconnect the display or restart the controller and recover the same session/event history without redispatching the completed or uncertain request.
5. Inspect a denied runtime-tool request and nullable usage/cost records. No simulator operation is available in this demonstration.

Use stdlib components and original fake peers first. Runtime loaders reject the committed `example_only` fixtures; a demo creates its own explicitly synthetic runtime configuration. P0.1 does not install provider SDKs, start native agents, collect credentials, or run a model. P0.2 adds optional pinned dependencies only when its concrete adapters need them.

## Module ownership for implementation

| Owner | Planned modules | Shared dependency |
| --- | --- | --- |
| AI connections | `agent/connections/`, `cli/connections.py`, AI storage adapter | Domain IDs/events, bounded evidence/proposal contract |
| Simulator connections | `adapters/executors/`, `adapters/simulators/`, `adapters/bridges/`, `execution/` | Attempt, execution binding and operation receipt contracts |
| Debugging | `monitoring/`, `validation/`, `recovery/`, `agent/diagnosis.py` | Evidence, proposal, policy and report contracts |
| Coordinator | `domain/`, common storage migrations, packaging, shared CLI entry point, documentation and integration tests | Resolve changes before specialist implementations depend on them |

The paths are proposed future modules. Use `tests/contracts/` for common backend contracts and `tests/fault_injection/` for interruption/duplicate-effect cases. Domain code has no provider SDK, simulator command or UI dependency. Parallel implementation should use isolated branches/worktrees with shared contract commits; this documentation round used disjoint file ownership in one checkout.

## Review decisions

- **Receipts:** `ExecutionReceipt` binds a simulator execution/import. `ActionReceipt` tracks a lifecycle action. `PostprocessReceipt` tracks derived computation. A tagged `OperationResult` references them; it introduces no second dispatch truth.
- **Imports:** no invented submission history or historical input hash. Imported external runs remain observe-only; absent identity evidence is unresolved.
- **Restarts:** each solver invocation has an attempt/generation; native requeue keeps origin lineage and does not imply a new SpiceOwl submit. Initial profiles disable automatic requeue recovery.
- **Collection:** moving existing artifact bytes and computing a measurement are separate operations. OCEAN postprocessing gets its own key, resource limit and receipt; failure cannot automatically rerun the circuit.
- **Acceptance:** specification failure never feeds the measurement-invalid reducer. Unknown measurements prevent an aggregate specification pass while preserving valid per-measurement failures.
- **Authority:** AI runtime permission answers, policy decisions and simulation action grants are distinct records. Neither an agent reply nor a log entry grants authority.
- **Persistence:** connection/inference operations and simulator submissions have different keys and lifetimes. All store migrations remain coordinated; no framework owns scientific acceptance.

## Information needed only for real integration

Public fake-backed development can proceed now. Before qualifying real connections, record the AI host OS, official runtime/schema versions, permitted authentication route and tested isolation profile. Before private simulation integration, record the full Cadence build/ISR, analysis/engine, execution owner, scheduler/storage profile, native result reader, dependency identity method and concrete operation grants. Unknowns remain explicit until that environment is selected.

## Checks in this design round

Check all JSON examples, local documentation links/anchors, Python-shaped interface sketch syntax and whitespace. Preserve the original MIT license and selected angular-waveform owl assets. These checks validate design artifacts only; no simulator, provider login, inference, or scientific result is verified.

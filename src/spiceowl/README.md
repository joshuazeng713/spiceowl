# Source layout

The alpha package implements fake connections, synthetic execution replay, acceptance and policy functions, local persistence, CLI and a bundled web console. The P0.2 slice adds explicit SSH identity/version probes and optional read-only stdio MCP queries. Real inference and simulation execution remain planned. See the [local guide](../../docs/local-console.md) and [connection guide](../../docs/real-connections.md).

| Directory | Planned responsibility |
| --- | --- |
| `domain/` | Typed experiment, attempt, receipt, observation, and result models |
| `adapters/executors/` | Mock, direct Linux, and later site scheduler/native manager execution |
| `adapters/simulators/` | Mock and later versioned Spectre input/log/result capabilities |
| `monitoring/` | Evidence collection, freshness, progress, and failure classification |
| `validation/` | Input integrity, data validity, measurement recipes, specification checks |
| `recovery/` | Policy evaluation, bounded actions, and recovery lineage |
| `storage/` | Durable records, event history, and artifact references |
| `agent/` | Planned agent/model-API connections, sessions, and optional evidence-backed diagnosis |
| `mcp/` | Optional read-only tools backed by independent SQLite read transactions |
| `hosts.py` | Private profile import, per-host probe locks and observation persistence |
| `cli/` | Local control and structured reports over the domain interfaces |
| `web/` | Loopback HTTP interface and packaged browser assets |
| `service.py` | Single-owner offline controller shared by CLI and web |

Dependency direction: adapters and interfaces depend on domain contracts; domain code must not depend on an orchestration framework, a model SDK, or a GUI. See [architecture](../../docs/architecture.md).

The [integrated implementation plan](../../docs/design/integration-plan.md) places real provider adapters after the fake-backed connection slice. An `execution/` package remains planned for later real simulator dispatch and postprocessing; the current `service.py` performs neither.

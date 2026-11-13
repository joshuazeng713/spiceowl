# Source layout

These directories reserve implementation boundaries. They contain no Python modules yet and are not an installable package. Introduce packaging, dependencies, and executable entry points with P0, the connection-management milestone.

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
| `cli/` | Local control and structured reports over the domain interfaces |

Dependency direction: adapters and interfaces depend on domain contracts; domain code must not depend on an orchestration framework, a model SDK, or a GUI. See [architecture](../../docs/architecture.md).

The [integrated implementation plan](../../docs/design/integration-plan.md) assigns future `agent/connections/` modules to P0. A service composition package `execution/` is planned for later simulator dispatch and postprocessing; it is not created by this design round.

# Connection and supervision design workstreams

Status updated **2026-11-24**. Current AI interaction uses CircuitGit's existing Codex window and IC Workbench integration; SpiceOwl provides the simulation engine SDK and CLI. Start with [the current AI route](../native-agent.md), [connectivity and SDK reads](../real-connections.md), and [the roadmap](../roadmap.md).

The designs below preserve the original workstreams assembled on 2026-11-13. The [AI connection proposal](ai-connections.md) and [integrated implementation plan](integration-plan.md) are superseded for AI ownership and sequencing. They are not setup instructions for another native-agent service or MCP server, and they do not establish provider or simulator qualification.

The original planning round covered user-owned agents/model APIs, simulator execution and simulation debugging. The coordinating chat integrated the three workstreams and owned shared documents and Git operations. Current cross-repository ownership follows [the CircuitGit integration profile](../circuitgit-integration.md).

## Historical design ownership

| Workstream | Owned deliverables | Boundary |
| --- | --- | --- |
| AI connections | [Design](ai-connections.md), [example](../../examples/connections.example.json) | User-owned agent runtimes, model APIs, auth status, sessions, capabilities, usage |
| Simulator connections | [Design](simulator-connections.md), [example](../../examples/execution-profiles.example.json) | Execution ownership, Virtuoso/Spectre and SPICE adapters, job receipts, reconnect and collection |
| Simulation debugging | [Design](simulation-debug.md), [example](../../examples/debug-policy.example.json) | Evidence, failure taxonomy, measurement validation, recovery proposals and limits |
| Coordinator | This index, `../contracts/interface-boundaries.md`, README, architecture, roadmap, decisions | Shared contracts, consistency review, commits, pull requests |

All three workstreams have delivered their design and example files. Session assignments and integration status are tracked in [workstreams](../workstreams.md). Coordinator decisions in shared boundaries take precedence over the original recommendation lists in the specialist designs.

## Original planning-round collaboration rules

- Work only inside the selected repository checkout and within the assigned files.
- Read repository guidance and the shared [interface boundaries](../contracts/interface-boundaries.md).
- Contributors do not switch branches, stage files, commit, push, merge, or rewrite another workstream's files. The coordinator owns those operations on `docs/connection-plans`.
- Shared-file edits and interface conflicts are reported as recommendations in the workstream's own document.
- Use current primary documentation and bounded source inspection for claims about provider or simulator support; record source URLs and the date checked.
- Do not run authentication, paid inference, simulations, or private-environment probes in this planning round. Do not inspect credential files or unrelated private CAD projects.
- Examples are explicitly non-executable drafts. Do not imply that a catalog entry, imported schema, or successful subprocess launch confirms a working integration.
- Report the files changed, proposed integration route, validation, uncertainty, and the next concrete implementation slice in the workstream's final response. The coordinator reads the response; no cross-chat reply is required.

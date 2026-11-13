# Connection and supervision design workstreams

Status: design drafts assembled and interfaces coordinated, 2026-11-13. These documents prepare implementation; they do not establish working provider or simulator integrations. Start with the [integrated implementation plan](integration-plan.md).

The maintainer has prioritized connecting and managing user-owned agents and model APIs, with parallel design work for simulator execution and simulation debugging. The coordinating chat integrates the three workstreams and owns shared documents and Git operations.

## Ownership

| Workstream | Owned deliverables | Boundary |
| --- | --- | --- |
| AI connections | [Design](ai-connections.md), [example](../../examples/connections.example.json) | User-owned agent runtimes, model APIs, auth status, sessions, capabilities, usage |
| Simulator connections | [Design](simulator-connections.md), [example](../../examples/execution-profiles.example.json) | Execution ownership, Virtuoso/Spectre and SPICE adapters, job receipts, reconnect and collection |
| Simulation debugging | [Design](simulation-debug.md), [example](../../examples/debug-policy.example.json) | Evidence, failure taxonomy, measurement validation, recovery proposals and limits |
| Coordinator | This index, `../contracts/interface-boundaries.md`, README, architecture, roadmap, decisions | Shared contracts, consistency review, commits, pull requests |

All three workstreams have delivered their design and example files. Session assignments and integration status are tracked in [workstreams](../workstreams.md). Coordinator decisions in shared boundaries take precedence over the original recommendation lists in the specialist designs.

## Collaboration rules

- Work only inside the selected repository checkout and within the assigned files.
- Read repository guidance and the shared [interface boundaries](../contracts/interface-boundaries.md).
- Contributors do not switch branches, stage files, commit, push, merge, or rewrite another workstream's files. The coordinator owns those operations on `docs/connection-plans`.
- Shared-file edits and interface conflicts are reported as recommendations in the workstream's own document.
- Use current primary documentation and bounded source inspection for claims about provider or simulator support; record source URLs and the date checked.
- Do not run authentication, paid inference, simulations, or private-environment probes in this planning round. Do not inspect credential files or unrelated private CAD projects.
- Examples are explicitly non-executable drafts. Do not imply that a catalog entry, imported schema, or successful subprocess launch confirms a working integration.
- Report the files changed, proposed integration route, validation, uncertainty, and the next concrete implementation slice in the workstream's final response. The coordinator reads the response; no cross-chat reply is required.

# Project workstreams

Updated: 2026-11-13. Status: design round delivered; P0.1 implementation integrated from three isolated worktrees. The [local console](local-console.md) now provides offline connections, synthetic replay, advisory diagnosis, SQLite history, CLI and a browser UI. Real-provider and simulator integrations remain planned.

## Current objective

Prepare implementable connection and supervision designs. The user's first requested implementation capability is management of their existing agent subscriptions and model APIs. This adds a provider-connection milestone before the simulation-monitor milestone; it does not grant a diagnostic agent authority to modify or stop simulations.

## Responsibility map

| Workstream | Question it must answer | Deliverables |
| --- | --- | --- |
| AI connection management | How can one console manage existing agents and API-backed diagnosis without confusing their auth, sessions, capabilities, or billing? | `design/ai-connections.md`, `../examples/connections.example.json` |
| SPICE / Virtuoso connection | How do we establish and retain an authoritative execution identity across supported environments? | `design/simulator-connections.md`, `../examples/execution-profiles.example.json` |
| Simulation debugging | How do we identify a failure, validate the experiment, and propose a justified bounded recovery? | `design/simulation-debug.md`, `../examples/debug-policy.example.json` |
| Project coordination | Do all three designs share compatible identities, event semantics, ownership rules, and implementation priorities? | Shared contracts, architecture/roadmap updates, integration review, one pull request |

Workstreams run in separate local chats, with exclusive file ownership. Chat identifiers are not part of the public project specification. Each workstream reports a result in its own chat; the coordinator reviews the report and files.

The [integrated plan](design/integration-plan.md) records P0.1–P0.3 and the simulation handoff. Selected first integrations are native local Codex and an explicit API-key route through the official OpenAI Python SDK; ACP follows, with LiteLLM optional later. Shared review resolved receipt naming, imported-run null keys, scheduler restart lineage, collection/postprocessing separation and independent specification outcomes.

## Integration review

The coordinator checks these seams before accepting the design round:

- AI ↔ execution: a provider session is distinct from a simulation attempt; disconnecting or changing a provider cannot cancel or duplicate a simulation.
- AI ↔ diagnosis: evidence is bounded and attributed; model output is an untrusted proposal with explicit uncertainty.
- Diagnosis ↔ execution: proposal, policy decision, and action receipt are separate objects. Only the execution service dispatches an authorized action against a verified target.
- All workstreams: optional capabilities have documented/advertised/checked/tested provenance; unknown state is not silently converted to failed or ready.
- Credentials: examples contain only placeholders and secret references; no subscription credential pooling or silent paid fallback.
- Scientific validity: valid design failures and Monte Carlo sample identity remain intact through all recovery paths.

## Review and delivery process

1. Complete the three owned design documents and illustrative configuration files.
2. Reconcile conflicting field names, assumptions, and backend priorities against [interface boundaries](contracts/interface-boundaries.md).
3. Check local documentation links, JSON syntax, placeholder/secret-reference discipline, and consistency with the current implementation status.
4. Update the shared roadmap and record the selected first implementation slice.
5. Submit one integrated draft pull request. Runtime features require subsequent implementation and opt-in integration tests.

The current integration branch is `docs/connection-plans`. Only the coordinator stages, commits, pushes, or merges changes; design workstreams do not operate the Git index or change branches.

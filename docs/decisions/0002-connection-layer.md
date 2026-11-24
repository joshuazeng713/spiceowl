# ADR 0002: Separate agent runtimes from model APIs

- Date: 2026-11-13
- Status: superseded for AI ownership and implementation ordering on 2026-11-24; retained as historical design
- Supersedes: the implementation ordering in ADR 0001, not its execution/validation principles

## Current decision

AI conversations, provider/model configuration and AI tool interaction use CircuitGit's existing Codex window and IC Workbench host integration. SpiceOwl is the independently usable simulation engine SDK and CLI. Its neutral `spiceowl.read_api` supplies bounded stored reads without requiring MCP. The earlier native-agent chat service and user-facing MCP launcher are retired; their lower-level adapters remain for compatibility tests and earlier records remain inspectable.

Use [AI through CircuitGit](../native-agent.md), [current connectivity setup](../real-connections.md), and [the CircuitGit integration profile](../circuitgit-integration.md) for current instructions. The proposal below does not authorize building or configuring a second provider/session manager in SpiceOwl. Its distinctions between evidence, provider state, tool permissions and simulation authority still apply.

## Historical context

The maintainer wants SpiceOwl to connect to and manage the agents they already subscribe to as well as model APIs. They requested parallel ownership of AI connections, simulator connections, and simulation debugging, with one coordinating workstream.

Existing coding agents provide their own tool loop, authentication, session state, and permission behavior. A model API supplies inference; SpiceOwl must own any diagnostic loop around it. Treating both as one chat-completions endpoint would discard important differences.

## Superseded decision proposal

1. Introduce a connection manager over separate `AgentBackend` and `ModelBackend` interfaces.
2. Start with a local, single-user deployment. Record the owner and host of each connection; the AI host and simulation host may differ.
3. After offline fake backends, implement a native adapter for documented local Codex app-server behavior and an API-key route using the official OpenAI Python SDK. Expand compatible agents through ACP's official Python SDK after those contracts pass. Keep vendor-specific capabilities visible.
4. Defer LiteLLM's API normalization until another provider makes it useful. An independently deployed proxy is optional when team routing/accounting requires it; it is not required for the first local prototype.
5. Reference T3 Code and AionUi for connection/session UI and architecture. Do not fork their entire application into the Python control plane without an explicit integration decision.
6. Retain user-owned native login and provider boundaries. Do not extract subscription credentials into a gateway, pool identities, or switch to paid inference implicitly.
7. Keep a connected diagnostic agent limited to evidence and proposals. Simulator credentials and job lifecycle authority stay with the simulation execution service.
8. Add P0 for connection contracts, registry, fake backends, auth/session state, usage semantics, and opt-in real integration checks before the existing M1 simulation monitor.

No provider, SDK, protocol version, executable, package version, or account entitlement is validated merely by this ADR. The AI workstream supplies the researched support matrix and acceptance gates.

## Historical interface coordination

Use [shared boundaries](../contracts/interface-boundaries.md) for identities, proposals, and event semantics. [Workstreams](../workstreams.md) records exclusive file ownership and review responsibilities.

The connection manager must distinguish configuration, transport, authentication, model catalog, and completed inference evidence. It must preserve unknown capabilities, quota, and cost rather than fabricating uniform support.

The [integrated implementation plan](../design/integration-plan.md) records the reviewed workstream decisions and the first runnable offline acceptance scenario. Detailed upstream support evidence is in the [AI design](../design/ai-connections.md).

## Historical consequences

SpiceOwl can share common management and presentation while preserving provider-specific semantics. Reusing a protocol/SDK reduces integration work, but ownership, side-effect reconciliation, evidence isolation, and scientific acceptance remain SpiceOwl responsibilities.

A provider connection is useful before real simulation integration, so public development can progress with fake backends and permitted local API smoke tests. Connecting a model does not authorize any real simulation or electrical-source change.

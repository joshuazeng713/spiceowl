# Standalone and embedded Codex windows

SpiceOwl has two explicit modes. The mode selects the conversation owner; both use the same deterministic engine. Importing a module or opening a browser page never starts Codex.

| Mode | Conversation owner | Simulation execution owner |
| --- | --- | --- |
| `standalone` | SpiceOwl local window; explicit Connect starts `codex app-server --listen stdio://` | Operator-bound CircuitGit Service/Runner and its existing review |
| `embedded_in_circuitgit` | CircuitGit's existing Codex window; SpiceOwl exports tools only | CircuitGit's existing Service/Runner |

## Standalone setup

```bash
spiceowl --data-dir /home/example/state/spiceowl serve --agent-mode standalone
```

Standalone is the `serve` default. Open the printed loopback URL and choose Agent channel. Connect uses the installed `codex` on PATH or the server operator's `--codex-executable /absolute/path/to/codex`. Optional `--codex-model MODEL` selects a native model; the connect form can select a model/profile. Authentication stays with that runtime. No password, API token, credential file or executable command is accepted by the form.

Connect, New conversation and Send are explicit separate actions. Connection success proves neither inference nor simulation readiness. The bridge checks effective manual approval policy and the thread's read-only sandbox/user reviewer. Incompatible managed policy fails closed; it does not rewrite global settings or switch providers.

Native shell/file tools, unrelated MCP servers, plugins and browser tools are disabled. Dynamic tools provide finite engine operations. Pure and stored-read tools return data directly; remote probes and owner actions require frozen human-review cards. Review the exact operation, arguments, thread/turn identity and payload digest. Preview-only/redacted approval data cannot authorize an action. No approval method is model-callable, and a decision never grants session-wide policy changes.

## Bind the existing execution owner

Pure source inspection, Agent-TB checking, PDK scope comparison, Spectre diagnostics, PSF ASCII parsing and adctools call the SDK directly. Numeric operations need the existing `adctools` extra. Stored reads use this standalone console's existing store. Reviewed resource checks use explicitly registered hosts and OpenSSH configuration.

For full PDK/onboarding work, select a reviewed installed CircuitGit client SDK and its existing private client configuration:

```bash
spiceowl serve --agent-mode standalone \
  --circuitgit-client-config /home/example/private/client.json \
  --circuitgit-sdk-path /home/example/src/CircuitGit/modules/gateway_client \
  --pdk-lut /home/example/private/pdk-lut.sqlite3
```

Paths above are fictional. Omit `--circuitgit-sdk-path` if `ic_gateway` is already installed. No checkout, server or data directory is guessed. Owner config, credentials and key paths stay server-side. LUT lookup opens the operator-selected existing cache read-only with one connection per worker; a cache miss never silently scans a host.

Call `spiceowl_onboarding` with `operation="catalog", arguments={}` for the selected owner's exact finite tool schemas. Bound operations use typed `Service.call`: source intake, environment/PDK discovery, scope selection, helper/header evidence, Agent-TB draft/prepare, original submission/status, saved waveforms and declared analysis. Missing bindings return typed unavailable; there is no generic HTTP/shell fallback, second Runner or copied Service approval ledger.

A Codex tool approval permits only that Service call. It does not approve the frozen simulation. Responses such as `review_host_unavailable` or `pending_approval` retain request/fingerprint and the owner's human review URL. Only the human reviews there; the Agent has no resolve/approve/approval-CLI tool. Denial, cancellation and unknown outcomes never become approval through model text.

Optional `--live-client-config PRIVATE_FILE` also binds the existing signed standalone simulation client. It retains its installed grant, fixed identities and unknown-request journal. Agent calls require tool review; direct submit/collect keys derive from frozen tool-call identities, while registered run keeps the original experiment-PLAN_ID key. Disconnecting Codex does not cancel remote simulation.

## Embedded tools

```python
from spiceowl.embedded import read_tool_definitions, dispatch_read_tool
from spiceowl.read_api import open_read_views

# Only if the operator explicitly selected an existing legacy store:
views = open_read_views(operator_selected_existing_directory)
definitions = read_tool_definitions()
result = dispatch_read_tool(views, "spiceowl_list_runs", {})
```

The eight schemas reject extra fields, including directory and approval arguments. Dispatch accepts an existing ReadViews object, never a path. CircuitGit's own Runner records remain authoritative; no new SpiceOwl store is required. An absent legacy binding is an unavailable capability.

```python
from spiceowl.agent_channel import export_simulation_management_tools
from spiceowl.simulation_tools import SimulationToolDispatcher

dispatcher = SimulationToolDispatcher(mode="embedded_in_circuitgit")
definitions = export_simulation_management_tools(
    mode="embedded_in_circuitgit", dispatcher=dispatcher)
```

The embedding host injects definitions into its existing thread, enforces `dispatcher.policy(name, arguments)` and calls `dispatcher.dispatch`. Finite `owner_handlers` are operator-provided capabilities. Construction/export perform no filesystem, network or process operation. Embedded CodexBridge active methods reject before spawning. CircuitGit may keep its existing ic_onboarding/ic_sim tools and use only read8 exports.

## Streaming, persistence and limits

Browser SSE reads `/api/codex/events?after=CURSOR` with monotonic IDs. Reconnect honors Last-Event-ID and never re-sends a message/tool. A bounded history reports explicit cursor gaps. All writes require loopback origin and CSRF; server configuration paths are not browser or model arguments.

Turn keys and tool identities persist before dispatch. Completed duplicate requests return stored receipts; changed input under an old key is rejected. Disconnect/timeout/restart during a request preserves unknown outcome and `recovery_required`; no automatic resume or replacement key. Operators can inspect interrupted turns via `GET /api/codex/recovery` (`CodexBridge.recovery_report()`) and explicitly archive/unlock the session without replaying via `POST /api/codex/recover` (`CodexBridge.reconcile_interrupted(...)`), which enforces `confirmed_side_effects_checked=true` whenever an approval-gated side-effect call was in flight. The current thread is interactive; previously displayed threads and legacy Q&A remain read-only.

Read-only tool arguments are bounded to 1 MiB, complete human-approval parameters to 64 KiB, and complete model tool results to 256 KiB. Message, event and journal sizes are bounded too. Oversized tool results return explicit incomplete/error envelopes rather than plausible truncated data. UI previews are separately labeled. Numerical limits and required sampling assumptions remain in the underlying algorithm contracts.

## Evidence

Local protocol tests launch a fake stdio process and cover handshake, events, approvals/denials, duplicate calls, interrupted turns, complete result arrays and embedded no-spawn. Browser tests use synthetic adapters. These checks do not qualify a user's native authentication, inference, PDK or simulator.

The [ten-stage walkthrough](https://spiceowl-dev.github.io/spiceowl/workflow.html) uses authorized publication copies of Bootstrap/Inverter source. Bootstrap is revision 10 awaiting human review, with no submitted job or measured FFT. Execution/analysis stages show contracts, not fabricated results. The page cannot connect to Codex, SSH or a simulator and includes no private PDK implementation.

Protocol reference: [official Codex App Server documentation](https://learn.chatgpt.com/docs/app-server.md). Dynamic tools require the experimental API capability; effective thread policies and protocol responses are checked.

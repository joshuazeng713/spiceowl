# AI through CircuitGit

Current integration route, **2026-11-24**: use CircuitGit's existing Codex window for AI conversations, model/provider selection and tool interactions. SpiceOwl supplies the simulation engine SDK and CLI. It does not require a separate AI service or a second MCP registration.

## Current workflow

1. Open the intended registered project in CircuitGit and use its existing Codex/AI panel, or the existing IC Workbench integration in the Codex desktop host.
2. Select the provider/model through that host's existing configuration. A custom provider is not evidence of a ChatGPT subscription; connection metadata and model listings do not establish that inference succeeded.
3. Work with the tools advertised by the selected CircuitGit session. Its existing app-server adapter supplies dynamic tools when starting its thread and handles tool calls through the existing Service. The IC Workbench plugin uses its own installed host integration; SpiceOwl does not create another client session or tool server for either route.
4. Keep project, source and run identities explicit. Simulation actions retain CircuitGit's existing review or installed-grant boundary and Runner receipts. A question, tool result or model response cannot grant execution authority.

The integration behavior above is implemented by CircuitGit's `apps/codex/icwb/ai.py` and existing Service/tool definitions. A particular installed version must still expose and successfully invoke the intended tool. The [CircuitGit integration profile](circuitgit-integration.md) defines the package boundary, and [IC Workbench](ic-workbench.md#agent-access) describes the action and cached-read paths.

SpiceOwl's deterministic diagnostics, waveform readers and [saved-data analysis tools](adctools.md) can also be called directly from Python. CircuitGit reads its own Runner/Supervision records and uses the existing PDK/diagnostic SDK directly. `spiceowl.read_api.ReadViews` and `open_read_views(directory)` are optional compatibility access to an already existing SpiceOwl database; they are not another CircuitGit connection or a new database to create during setup. Those stored rows are a separate dataset, not aliases for CircuitGit's native records. See [read-only engine access](real-connections.md#5-read-saved-state-through-the-engine-sdk). Neither local reads nor analysis requires an AI provider connection.

## Earlier native Q&A compatibility

The original P0.3 implementation launched a bounded, text-only Codex request from a SpiceOwl native-agent profile. That separate onboarding and inference path is superseded. The user-facing `agents add`, `agents probe` and `agents ask` launch operations are retired; do not use an old native-agent setup guide to start another runtime. New questions belong in CircuitGit's existing AI window.

Previously saved profiles and Q&A remain historical evidence. The CLI retains `agents list` / `agents inspect AGENT_ID` and `questions list` / `questions inspect QA_ID` for inspection; the console also preserves historical Q&A. Saved queued, running or `outcome_unknown` states describe the earlier snapshot and do not establish current execution or completion. Displaying history never replays the request. Saved provider/model/usage fields retain their original meaning, including unknown cost.

The old native adapter and its protocol fixtures remain for compatibility tests. They are not a supported second AI service. Likewise, the optional official MCP SDK extra supports retained compatibility tests for the old read transport; it is not required by CircuitGit's AI path or SpiceOwl's engine SDK.

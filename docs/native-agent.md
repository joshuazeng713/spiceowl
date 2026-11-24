# Codex Agent modes

The current route is [explicit dual-mode integration](dual-mode-agent.md). Standalone `spiceowl serve` offers its own Codex window with explicit Connect, SSE and human tool review. Embedded `mode="embedded_in_circuitgit"` exports tools to CircuitGit's existing window and cannot launch a second Agent. Both use the same engine and existing simulation execution owner.

Old `agents add/probe/ask` and `spiceowl mcp` launch commands remain retired. They never replay native Q&A. Use `agents list/inspect`, `questions list/inspect`, or the console history view for legacy records. Low-level MCP/native symbols remain compatibility surfaces.

The [SDK read views](circuitgit-integration.md#direct-sdk-read-views) require an explicitly selected existing store, with no MCP, inference or duplicate CircuitGit database. Saved connection rows do not establish a live connection.

The public Pages console is synthetic. Its [workflow page](https://spiceowl-dev.github.io/spiceowl/workflow.html) explains both modes and the Bootstrap example's evidence boundary.

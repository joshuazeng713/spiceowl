# Legacy Compatibility Surfaces (Archived / Read-Only)

SpiceOwl retains two historical compatibility surfaces so existing scripts, read-only audits, and regression tests remain intact while new users follow the streamlined standalone/embedded architecture.

## Status Matrix

| Historical Surface | Current Status | Replacement for New Users |
| :--- | :--- | :--- |
| `spiceowl mcp` CLI launcher & `spiceowl.mcp` adapter | **Retired CLI / Deprecated adapter** (`spiceowl mcp` does not start a server; `spiceowl.mcp.server.create_server` remains only for historical read-only compatibility tests with `mcp==1.30.0`) | Use the standalone Codex channel (`#agents`), CircuitGit's embedded dynamic tool bridge (`spiceowl.simulation_tools`), or `spiceowl read` for local JSON projections. |
| Legacy Q&A history (`#agents` collapsed history, `spiceowl questions`, `spiceowl agents`) | **Read-only archive** (historical queued/running/unknown snapshots are never resumed or retried) | Use `#quickstart` for onboarding, `#direct` for native SSH/LSF/Slurm runs, waveforms, PVT/MC batches & recovery reports, and `#agents` standalone Codex for interactive sessions. |

## Recommended Entry Point for New Users

Start with the cross-platform guided wizard (macOS, Linux, and native Windows):

```bash
# 1. Check local OS, OpenSSH, ~/.ssh/config aliases, and optional tools
spiceowl init --check

# 2. Or bootstrap the full 5-station offline vivid demo workspace in one command
spiceowl init --demo

# 3. Launch the local web console and open #quickstart
spiceowl serve
```

If you have your own remote Linux EDA server (Spectre or ngspice), `#quickstart` (or `spiceowl init --host-id ... --ssh-target ... --expected-host ... --expected-user ... --probe --prepare-smoke`) walks you through verifying OpenSSH connectivity, freezing a zero-PDK RC transient smoke testbench, and running your first simulation end-to-end without writing shell scripts.

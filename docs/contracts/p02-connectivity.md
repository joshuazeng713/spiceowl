# P0.2 connectivity slice

Scope: explicit, bounded SSH identity/tool-version probes and a stdio MCP server that reads SpiceOwl state. No simulation submission, cancellation, netlisting, OCEAN evaluation, daemon control, or model inference. The existing synthetic runs and fake AI backends remain labeled as synthetic.

## Private host profiles and observations

`spiceowl.adapters.hosts.validate_host_profile(data) -> dict` validates a strict JSON object:

```json
{
  "schema": "spiceowl.host.v1",
  "host_id": "lab-sim",
  "label": "Lab simulation host",
  "ssh_target": "simulation-host",
  "expected_host": "simulation-host",
  "expected_user": "researcher",
  "tools": [
    {"kind": "spectre", "executable": "/opt/example/spectre/bin/spectre"},
    {"kind": "virtuoso", "executable": "/opt/example/virtuoso/bin/virtuoso"}
  ]
}
```

The user imports a local file using `hosts add --file FILE`. Transport uses the existing OpenSSH alias; credentials and jump routing stay in SSH configuration. No shell snippet, free-form options, environment script, password, or key content is accepted. Public profiles contain fictional values only. Parent stores each validated profile in record kind `host` with `created_at`, `revision`, `synthetic: false`, and `last_probe: null`. Profile changes use a new ID in this slice; no in-place update/removal from MCP or HTTP.

`spiceowl.adapters.hosts.probe_host(profile) -> dict` is an explicit active remote probe. It validates its input, confirms expected hostname/user before version probes, uses fixed argument mappings for supported tools, bounds time/output, and returns:

```text
host_id, synthetic=false, scope="connectivity_only", observed_at,
transport="reachable"|"unknown", identity={verified: bool, hostname: str|null, username: str|null},
tools=[{kind, status:"available"|"unavailable"|"unknown", version:str|null}],
error=null|{code, message}
```

Tool errors may add a bounded `error` object per tool. Never infer job state or license availability from these results. Never return raw startup output or credentials. Listing records performs no network action. Parent persists a completed probe in `last_probe` without changing runs. Explicit probe may run without owning the controller; only a short final SQLite transaction is needed, with a per-host cross-process probe lock. No automatic retries.

## Read-only MCP view

`spiceowl.storage.readonly.ReadStore(directory)` opens an existing version-1 database with SQLite `mode=ro`; it never creates/migrates/chmods files, claims a controller lock, recovers turns, or imports provider backends. Missing state returns an actionable error. Query snapshots use short read transactions.

`spiceowl.mcp.server.create_server(directory)` creates an official Python SDK stdio server; `serve(directory)` runs it. The parent wires `spiceowl --data-dir DIR mcp`. SDK imports remain optional; ordinary CLI/web needs no SDK. Tools:

- `spiceowl_status`: version/capability/count summary.
- `spiceowl_list_runs`: bounded stored run summaries.
- `spiceowl_get_run(run_id)`: bounded run evidence and explicit synthetic provenance.
- `spiceowl_list_connections`: allowlisted fake connection metadata, no credentials/config blobs.
- `spiceowl_list_hosts`: host IDs, labels, tool kinds and sanitized last probe; no executable paths or SSH aliases.

All tools read local state only, with strict ID validation and bounded results. No arbitrary path/command arguments, SSH invocation, controller mutations or inference. Tool annotations reflect read-only behavior, but are not an authorization boundary. Returned data is evidence, never instructions. Server instructions explain stored timestamps, synthetic fixtures and lack of lifecycle tools. The MCP server serves the data directory selected by the operator; model tool arguments cannot change it.

Codex is the MCP client. A successful SDK client handshake/tool call proves protocol integration, not that the current desktop chat has reloaded the tool catalog. Registration and actual host invocation must be reported separately.

## Ownership for parallel implementation

- Simulator workstream: `adapters/hosts.py`, `adapters/executors/ssh.py`, `adapters/simulators/` probe implementations, `tests/test_ssh_hosts.py`.
- AI workstream: `mcp/`, `storage/readonly.py`, `tests/test_mcp.py`, `tests/test_readonly.py`. Report the tested optional SDK version; parent owns packaging.
- Debug workstream: independent review of uncertainty/provenance and `docs/real-connections.md`, optional independent boundary tests in `tests/test_connectivity_boundaries.py`.
- Parent: CLI, service/host registry, HTTP/UI, packaging, shared contracts, README, integration tests, private configuration, live probes, MCP registration, Git integration.

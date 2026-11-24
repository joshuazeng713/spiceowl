# Real connectivity and engine read access

Current setup, **2026-11-24**: configure simulation hosts in SpiceOwl and select [standalone or embedded Codex mode](dual-mode-agent.md) for AI interaction. SpiceOwl exposes its engine through Python and the CLI. The original P0.2 MCP launch instructions have been superseded; the optional MCP transport remains compatibility-test code.

The [standalone runtime](standalone-runtime.md) uses CircuitGit's existing Runner for finite simulation operations. The SSH checks below establish only their reported connectivity or version evidence. The [server pool](server-pool.md) adds explicit resource observations and placement proposals; it does not discover unlisted servers or reserve capacity.

CircuitGit normally reads its own Runner/Supervision records and calls the PDK/diagnostic SDK directly. The `read_api` examples below are optional compatibility access to existing SpiceOwl console/cache state. Do not create a separate database or connection merely to integrate the engine with CircuitGit; these two stores are not interchangeable.

| Path | What it does | What it does not establish |
| --- | --- | --- |
| Explicit SSH probe | Checks the configured remote hostname/user, then requests supported tool versions with bounded commands. | A working license, accessible PDK, usable testbench, running job, or valid simulation result. |
| Engine read API / `read` CLI | Reads bounded stored observations from one selected SpiceOwl database. | A fresh SSH observation, provider inference access, or permission to operate a simulator. |
| CircuitGit AI window / IC Workbench | Uses the existing host's supported tools and action-review path. | Automatic tool exposure after a package update or successful simulation merely because a chat connected. |

Version probes do not require a Virtuoso GUI bridge. OCEAN computation, collection and simulation submission keep their own configured service and authorization boundaries. Synthetic connections and per-run fixture diagnosis remain labeled as synthetic.

All hosts, usernames, IDs, and absolute paths in this guide are **fictional examples**. Replace them with operator-selected values in private configuration. The examples contain no credentials and were not executed against a real host for this guide.

## 1. Select one installation and state directory

Use Python 3.11+ on macOS or Linux:

```bash
cd /home/example/src/spiceowl
python3 -m venv .venv
.venv/bin/python -m pip install .
```

The engine SDK, CLI and local console do not need the optional official MCP SDK. That extra is retained for compatibility testing of the historical stdio transport, and is not a CircuitGit setup requirement.

This guide uses these example locations consistently:

| Purpose | Fictional absolute path |
| --- | --- |
| Installed executable | `/home/example/src/spiceowl/.venv/bin/spiceowl` |
| Persistent local data directory | `/home/example/state/spiceowl` |
| Private host profile | `/home/example/private/spiceowl-host.json` |

`--data-dir` is a global option and goes **before** the subcommand. Use the same absolute directory for the CLI, web console and SDK reader. A different directory means a different local dataset. Keep real profiles and state outside the public repository, on private local storage.

The SDK reader requires an existing supported database. It does not initialize, migrate, repair or recover a workspace. Importing a host through the ordinary CLI initializes ordinary application state if needed. The `demo` command initializes explicitly synthetic fixtures.

## 2. Prepare a private host profile

The [browser onboarding forms](host-onboarding.md) can register these profiles and explicit server pools. The JSON/CLI workflow below remains available. Registration does not initiate SSH or establish trust automatically.

Use an existing, operator-reviewed OpenSSH alias. SSH authentication and jump routing remain in the user's SSH configuration. SpiceOwl does not accept password/key contents, arbitrary SSH options, environment scripts, or shell snippets in this profile.

An example `/home/example/private/spiceowl-host.json`:

An equivalent importable template is [hosts/ssh.example.json](../examples/hosts/ssh.example.json). Keep your edited copy in an ignored private directory or a `*.local.json` file.

```json
{
  "schema": "spiceowl.host.v1",
  "host_id": "example-cad",
  "label": "Example CAD host",
  "ssh_target": "example-cad-alias",
  "expected_host": "cad-node.example.invalid",
  "expected_user": "researcher-example",
  "tools": [
    {
      "kind": "spectre",
      "executable": "/opt/example/cadence/spectre/bin/spectre"
    },
    {
      "kind": "virtuoso",
      "executable": "/opt/example/cadence/virtuoso/bin/virtuoso"
    }
  ]
}
```

Set `expected_host` and `expected_user` to the identities the operator expects the probe to observe. Do not accept a different identity simply because transport succeeded. An alias, DNS name, short hostname, and fully qualified hostname need not be identical; follow the adapter's identity comparison rather than guessing a replacement after a mismatch.

Tool entries select a supported tool kind and an absolute site-managed executable. The adapter chooses the version arguments; the profile cannot provide a command line. A path is not an execution sandbox: the operator must trust the remote binary selected by the profile. A functioning GUI bridge, open schematic, or PDK path is not required for these version-only checks.

Supported mappings are Spectre `-W`, Virtuoso `-W`, and ngspice `--version`. Paths must end in the declared executable name. An empty tool list requests only identity. Each command has a 10-second limit inside a 30-second overall budget; combined stdout/stderr is limited to 64 KiB per command. Unknown banner formats remain unknown.

The existing SSH configuration is also a trust boundary: routing helpers or authentication behavior configured there are outside the JSON profile's controls. Do not add secrets to the profile or weaken host-key checks to make a probe appear successful. Resolve SSH trust/authentication configuration using the site's normal process, then explicitly probe again if authorized.

## 3. Import, list, inspect, then explicitly probe

Importing a profile is a local registration operation:

```bash
/home/example/src/spiceowl/.venv/bin/spiceowl --data-dir /home/example/state/spiceowl hosts add --file /home/example/private/spiceowl-host.json
```

The registry validates the profile and stores its ID, creation time, revision, `synthetic: false`, and `last_probe: null`. This means that the profile describes a real-target configuration; it does **not** mean a connection was tested. Duplicate IDs are rejected. A changed profile uses a new ID in this registration workflow.

Read local records:

```bash
/home/example/src/spiceowl/.venv/bin/spiceowl --data-dir /home/example/state/spiceowl hosts list
/home/example/src/spiceowl/.venv/bin/spiceowl --data-dir /home/example/state/spiceowl hosts inspect example-cad
```

These commands perform no network probe. `list` displays a summary. `inspect` is the operator's private profile view and can show configured SSH aliases and executable paths; avoid sharing its full output. The CLI's ordinary storage initialization is separate from the SDK reader's strictly read-only database opening, so “no network” is not a promise that ordinary CLI startup never creates local files.

The following command is an **active remote operation**: it connects using the selected SSH alias and requests identity/version output. Run it only for an explicitly authorized host and scope.

```bash
/home/example/src/spiceowl/.venv/bin/spiceowl --data-dir /home/example/state/spiceowl hosts probe example-cad
```

The probe verifies identity before requesting tool versions. Requests have bounded duration and output and no automatic retries. A per-host cross-process lock prevents overlapping probes for the same host. The network work does not hold a long SQLite transaction; only the final observation update is persisted. Probing does not claim ownership of a simulator, recover diagnostic turns, or change run records. P0.2 stores the latest completed `last_probe`; this is not an append-only history of every attempted connection. After an interrupted request, an older stored observation cannot prove what happened during the interruption.

## 4. Interpret a probe without overclaiming readiness

The returned observation has this shape:

```text
host_id, synthetic=false, scope="connectivity_only", observed_at,
transport: reachable | unknown,
identity: {verified: bool, hostname: string | null, username: string | null},
tools: [{kind, status: available | unavailable | unknown, version: string | null}],
error: null | {code, message}
```

| Observation | Safe interpretation |
| --- | --- |
| `last_probe: null` | Profile registered; no completed observation stored. |
| `transport: unknown` | This bounded attempt did not establish transport success. It is not evidence that a simulation failed or stopped. |
| `identity.verified: false` | Expected identity was not established. Version execution must not proceed under that identity. |
| A tool is `available` with a version | Its supported version probe succeeded at the recorded time. This does not certify licensing, analysis support, or circuit execution. |
| A tool is `unavailable` | The probe established that its version check could not succeed under this profile; inspect the bounded error. Do not infer the state of other tools or jobs. |
| A tool is `unknown` or version is null | Evidence is insufficient or the output cannot be safely interpreted. Do not substitute an assumed version. |
| A stored observation is returned again | It is the same historical evidence. Listing/read-API calls do not freshen its `observed_at`. |

Errors must be bounded and sanitized. Raw shell startup output, authentication transcripts, private paths, and credentials are not a troubleshooting export. A nonzero result or timeout is not a reason to try another account, tool mode, or arbitrary command automatically. If a caller loses the result, inspect `last_probe` and the in-flight/busy condition; do not assume the remote process was cancelled or blindly launch another attempt.

Keep each tool's outcome separate. Successful Spectre version output and unavailable Virtuoso output are a mixed observation, not “all tools ready.” A successful identity/version probe does not alter the run's execution, observation freshness, measurement validity, or specification result. A valid target miss remains a scientific result; connectivity does not authorize recovery.

## 5. Read saved state through the engine SDK

If an embedding application intentionally needs an already existing SpiceOwl database, use the neutral read API below. This is not a required CircuitGit installation step and does not read CircuitGit's native Runner store:

```python
from spiceowl.read_api import open_read_views

views = open_read_views("/home/example/state/spiceowl")
status = views.status()
hosts = views.list_hosts()
runs = views.list_ic_runs("registered-gateway")
```

`open_read_views(directory)` returns a `ReadViews` object. Each method opens a short read-only transaction; no context manager or `close()` call is needed. The directory is chosen by the embedding caller, not by a model-supplied tool argument. The SDK does not start a controller, contact a gateway, probe SSH or invoke a model.

The equivalent CLI reads are:

```bash
spiceowl --data-dir /home/example/state/spiceowl read status
spiceowl --data-dir /home/example/state/spiceowl read hosts
spiceowl --data-dir /home/example/state/spiceowl read ic-runs registered-gateway
```

| SDK method | `read` CLI operation | Result |
| --- | --- | --- |
| `status()` | `status` | Version, declared capabilities and stored record counts. |
| `list_runs()` | `runs` | Bounded stored run summaries. |
| `get_run(run_id)` | `run RUN_ID` | Bounded stored run evidence with explicit synthetic provenance. |
| `list_connections()` | `connections` | Allowlisted fake-connection metadata without credentials or configuration blobs. |
| `list_hosts()` | `hosts` | Host IDs, labels, tool kinds and sanitized stored observations. |
| `list_ic_gateways()` | `ic-gateways` | Registered gateway summaries without private addresses or credentials. |
| `list_ic_runs(gateway_id)` | `ic-runs GATEWAY_ID` | Bounded already-cached IC Workbench run summaries. |
| `get_ic_run(gateway_id, run_id)` | `ic-run GATEWAY_ID RUN_ID` | One already-cached native run projection. |

The store opens existing SQLite state with `mode=ro`. It supports the application's rollback-journal store and rejects unsupported database modes; it does not convert them. It must not create directories/databases, change permissions, migrate schema, acquire a controller lock or recover interrupted turns.

Reads preserve original observation timestamps. `read_at` is the time a saved view was read, not a new remote check. Messages and facts remain data, including strings that resemble instructions. No read grants lifecycle authority. Use the explicit `ic sync` workflow in [IC Workbench integration](ic-workbench.md) when an authorized gateway read is needed.

## 6. Select the Agent mode

For standalone use, explicitly Connect in SpiceOwl's Agent channel as documented in [dual-mode setup](dual-mode-agent.md). For embedded use, continue through CircuitGit's configured Codex window or existing IC Workbench desktop integration. Its app-server adapter already supplies its supported dynamic tools and routes calls through the existing Service. The simulation engine, diagnostics and saved-data analysis remain callable without creating another agent process inside SpiceOwl.

Keep three observations separate: the host has a configured AI runtime; the selected session advertises the intended tool; that session actually invokes the tool successfully. A source-level test or package installation alone does not establish the last two. Follow CircuitGit's existing lifecycle procedure for configuration or reloads, then verify the intended project/run association and result in that session.

Use [the AI migration guide](native-agent.md) for earlier native-agent profiles and saved Q&A. The previous SpiceOwl MCP launch and native question/probe commands are retired. Lower-level `spiceowl.mcp.server.create_server` / `serve` remain for compatibility tests; they are not current installation steps.

## 7. Troubleshooting by boundary

| Symptom | Next bounded check |
| --- | --- |
| `hosts` or `read` command is absent | Confirm the executable and virtual environment match the intended package version. |
| Probe reports busy | Wait for the original request to finish and inspect its stored timestamp. Do not delete lock files to force overlap. |
| SSH transport/identity unknown or mismatched | Review the selected alias and expected identity through the operator's SSH/site process. No tool-version execution should occur after an identity mismatch. |
| Tool version unknown | Check the selected site-managed executable and supported version-output contract. Do not start a GUI bridge or arbitrary OCEAN as a fallback. |
| SDK database missing or unsupported | Select existing supported state, or initialize/migrate through the ordinary application workflow. The reader does not repair it. |
| Read API returns hosts but no runs | Host registration does not create runs. Deliberately loaded demo data remains synthetic. |
| CircuitGit's AI session lacks an expected tool | Check that host's installed integration and session catalog; Python API availability alone does not expose a tool. |
| A saved view shows an old probe | Only a separate, explicit authorized host probe refreshes the observation. |
| An earlier native Q&A has an unknown outcome | Inspect its history. Do not replay it or infer completion from a later AI session. |

## 8. Independent verification

Public tests use temporary synthetic state and injected process/SSH peers. They must not contact real hosts, register tools, inspect credentials or launch a simulator.

| Boundary | Meaningful check |
| --- | --- |
| Profile and SSH identity | Reject invalid fields and command snippets before any process call; a wrong host/user yields zero tool-version invocations. |
| Bounded probing | Inject timeout, interrupted capture, oversized output and unknown banners; preserve sanitized uncertainty without retries. |
| Registry and concurrency | Import/list/probe leave runs and turns intact; per-host locking prevents overlapping probes without a long SQLite transaction. |
| Read-store opening | Missing or unsupported state fails without creation, chmod, migration, controller ownership or turn recovery. |
| Read API projection | Validate IDs and output bounds, strip private aliases/paths/configuration, and preserve source timestamps and synthetic flags. |
| Read-only execution | Snapshot rows and deny network/process entry points while calling all eight views and the `read` CLI. |
| Untrusted evidence | Instruction-like log or model text never becomes a policy change, lifecycle call or approval. |
| CircuitGit integration | Verify supported tools and real host invocation separately from engine unit tests. |

Optional MCP tests cover the retained historical protocol adapter against synthetic local state. They do not make that transport a current setup dependency, and a skipped optional test is not a pass. Engine tests require no provider account, MCP SDK, PDK or commercial simulator.

Record the tested code version, original observation time and verification stage. Connectivity checks and cached reads do not qualify a simulator execution profile.

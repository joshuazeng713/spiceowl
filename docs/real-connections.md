# Real connectivity: bounded SSH probes and local MCP

This guide targets the **P0.2 connectivity slice**, under integration on 2026-11-13. It describes the [P0.2 contract](contracts/p02-connectivity.md); it is not a claim that a particular remote host, optional SDK environment, or current Codex chat has been verified. The [P0.1 console](local-console.md) continues to use synthetic runs and fake agent/model backends. Use a build containing P0.2 before following the new commands below.

P0.2 adds two separate paths:

| Path | What it does | What it does not establish |
| --- | --- | --- |
| Explicit SSH probe | Checks the configured remote hostname/user, then requests supported tool versions with bounded commands. | A working license, accessible PDK, usable testbench, running job, or valid simulation result. |
| Local stdio MCP | Lets a client read a selected SpiceOwl database through a small tool allowlist. | A live SSH connection, provider inference access, or permission to operate a simulator. |

There is no simulation submission, cancellation, netlisting, solver restart, daemon control, or OCEAN evaluation in this slice. Version probes do not require a Virtuoso GUI bridge. OCEAN computation is future, separately authorized service work; it is not hidden inside a version probe or artifact read. The console's API inference and diagnostic backends remain fake. Connecting Codex as an MCP client does not convert those fake backends into real model integrations.

All hosts, usernames, IDs, and absolute paths in this guide are **fictional examples**. Replace them with operator-selected values in private configuration. The examples contain no real credentials and were not executed for this guide.

## 1. Select one installation and state directory

Use Python 3.11+ on macOS or Linux. For source installation, the optional MCP dependency is installed in the same environment as the SpiceOwl executable:

```bash
cd /home/example/src/spiceowl
python3 -m venv .venv
.venv/bin/python -m pip install '.[mcp]'
```

The base CLI/web installation does not need the optional SDK. The MCP extra belongs to the integrated P0.2 package; installing the P0.1 build alone does not add an MCP server. Dependency installation is an operator action, not part of the probes. Record the installed SpiceOwl and MCP SDK versions when qualifying an environment.

This guide uses these example locations consistently:

| Purpose | Fictional absolute path |
| --- | --- |
| Installed executable | `/home/example/src/spiceowl/.venv/bin/spiceowl` |
| Persistent local data directory | `/home/example/state/spiceowl` |
| Private host profile | `/home/example/private/spiceowl-host.json` |

`--data-dir` is a global option and goes **before** `hosts` or `mcp`. Use the same absolute directory for the CLI, web console, SDK check, and Codex registration. A different directory means a different local dataset. Keep real profiles and state outside the public repository, on private local storage.

The MCP reader requires an existing supported database. It does not initialize, migrate, repair, or recover a workspace. Importing a host through the ordinary CLI initializes ordinary application state if needed. Alternatively, the existing `demo` command initializes synthetic fixtures; those fixtures remain synthetic even when stored beside a real host observation.

## 2. Prepare a private host profile

Use an existing, operator-reviewed OpenSSH alias. SSH authentication and jump routing remain in the user's SSH configuration. SpiceOwl does not accept password/key contents, arbitrary SSH options, environment scripts, or shell snippets in this profile.

An example `/home/example/private/spiceowl-host.json`:

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

The existing SSH configuration is also a trust boundary: routing helpers or authentication behavior configured there are outside the JSON profile's controls. Do not add secrets to the profile or weaken host-key checks to make a probe appear successful. Resolve SSH trust/authentication configuration using the site's normal process, then explicitly probe again if authorized.

## 3. Import, list, inspect, then explicitly probe

Importing a profile is a local registration operation:

```bash
/home/example/src/spiceowl/.venv/bin/spiceowl --data-dir /home/example/state/spiceowl hosts add --file /home/example/private/spiceowl-host.json
```

The registry validates the profile and stores its ID, creation time, revision, `synthetic: false`, and `last_probe: null`. This means that the profile describes a real-target configuration; it does **not** mean a connection was tested. Duplicate IDs are rejected. A changed profile uses a new ID in P0.2; there is no in-place profile update through MCP or HTTP.

Read local records:

```bash
/home/example/src/spiceowl/.venv/bin/spiceowl --data-dir /home/example/state/spiceowl hosts list
/home/example/src/spiceowl/.venv/bin/spiceowl --data-dir /home/example/state/spiceowl hosts inspect example-cad
```

These commands perform no network probe. `list` displays a summary. `inspect` is the operator's private profile view and can show configured SSH aliases and executable paths; avoid sharing its full output. The CLI's ordinary storage initialization is separate from MCP's strictly read-only database opening, so “no network” is not a promise that ordinary CLI startup never creates local files.

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
| A stored observation is returned again | It is the same historical evidence. Listing/MCP calls do not freshen its `observed_at`. |

Errors must be bounded and sanitized. Raw shell startup output, authentication transcripts, private paths, and credentials are not a troubleshooting export. A nonzero result or timeout is not a reason to try another account, tool mode, or arbitrary command automatically. If a caller loses the result, inspect `last_probe` and the in-flight/busy condition; do not assume the remote process was cancelled or blindly launch another attempt.

Keep each tool's outcome separate. Successful Spectre version output and unavailable Virtuoso output are a mixed observation, not “all tools ready.” A successful identity/version probe does not alter the run's execution, observation freshness, measurement validity, or specification result. A valid target miss remains a scientific result; connectivity does not authorize recovery.

## 5. Start the local read-only MCP server

After installing the extra and creating the intended state directory, the launch form is:

```bash
spiceowl --data-dir /home/example/state/spiceowl mcp
```

For an unambiguous interpreter/environment, use the installed absolute executable:

```bash
/home/example/src/spiceowl/.venv/bin/spiceowl --data-dir /home/example/state/spiceowl mcp
```

This is a stdio protocol process, not a browser or HTTP server. It waits for an MCP client; launching it in a terminal and seeing no normal output does not verify a handshake. The MCP client normally starts this command. Protocol messages use stdout; diagnostic messages must not pollute that stream.

The read-only store opens an existing version-1 SQLite database with `mode=ro`. This slice supports the application's default rollback-journal store and rejects WAL mode; it does not convert database modes. It must not create directories/databases, change permissions, migrate schema, acquire a controller lock, recover interrupted turns, or import provider backends. A missing/unsupported database is an actionable error, not an opportunity to create a new workspace. Read transactions are short so a running web controller can continue to update state.

The configured data directory is fixed when the operator launches the server. Tool arguments cannot replace it or select arbitrary files. P0.2 exposes only:

| Tool | Result |
| --- | --- |
| `spiceowl_status` | Version, declared capabilities, and local record counts. |
| `spiceowl_list_runs` | Bounded stored run summaries. |
| `spiceowl_get_run` | Bounded evidence for a validated `run_id`, preserving synthetic provenance. |
| `spiceowl_list_connections` | Allowlisted fake-connection metadata, excluding credentials/configuration blobs. |
| `spiceowl_list_hosts` | Host IDs, labels, tool kinds, and sanitized stored probe observations, excluding SSH aliases and executable paths. |

None of these tools probes SSH, advances a demo, invokes a model, mutates the controller, or operates a job. Read-only annotations describe the implementation; they do not replace input validation or create an OS sandbox. Returned messages are data, including strings that look like instructions. Model tool requests cannot upgrade that data into approval.

## 6. Register the launch command with Codex

OpenAI's official MCP documentation describes local stdio servers and the `codex mcp add NAME -- COMMAND ...` configuration form. It distinguishes configured-server listing from the CLI TUI's active-server view. The setup below adapts that documented form to SpiceOwl; it does not prove the current desktop chat has loaded this server. [Official OpenAI MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) (checked 2026-11-13).

After the operator has qualified the local executable and chosen its private data directory:

```bash
codex mcp add spiceowl -- /home/example/src/spiceowl/.venv/bin/spiceowl --data-dir /home/example/state/spiceowl mcp
codex mcp list
```

The first command changes Codex's MCP registration. It is intentionally an operator step; this guide did not perform it. The second inspects configuration. Neither is evidence that a tool was injected into the current chat or invoked successfully. Avoid relative executable/data paths and shell activation wrappers. This local server does not require adding a provider API key or copying subscription credentials into MCP configuration.

Track these three outcomes separately:

| Verification stage | Evidence to record | Claim permitted |
| --- | --- | --- |
| Registration | Server name, absolute command/arguments and matching configured entry. | “SpiceOwl is registered.” |
| SDK protocol verification | Installed SDK version and a real SDK-client initialize, list-tools, and read-only tool call against the intended directory. | “The tested local stdio protocol works.” |
| Actual host discovery and invocation | The intended Codex session actually discovers the expected tool and returns a known stored read-only result. | “This session can call this SpiceOwl tool.” |

Do not collapse these into one `ready=true` flag. An SDK test is not a Codex desktop injection test. Use the client's supported reload/restart workflow as needed, then verify discovery and invocation in the intended session; do not assume registration refreshes an already-running chat. If the tools are absent, report host injection as unverified rather than fabricating a tool result.

A safe first host call is `spiceowl_status`, followed by a list query. Compare the returned dataset/version and recorded timestamps with the intended local workspace. If the SDK test or host call is not yet performed, explicitly report that stage as unverified. This guide makes no claim that any tools are ready in the reader's current session.

## 7. Troubleshooting by boundary

| Symptom | Next bounded check |
| --- | --- |
| `hosts` or `mcp` command is absent | Confirm the executable belongs to the P0.2 build, rather than another virtual environment or P0.1 installation. |
| Optional MCP dependency is missing | Install `'.[mcp]'` into the same source environment used by the registered absolute executable. Base CLI availability does not verify SDK availability. |
| Profile rejected | Compare only the declared fields/schema and supported tool kinds. Remove command snippets or credential contents; do not relax validation. |
| Duplicate host ID | Inspect the existing local record. Use a new ID for a deliberately changed profile. |
| Probe reports busy | Wait for the original request to finish and inspect its stored timestamp. Do not delete lock files to force overlap. |
| SSH transport/identity unknown or mismatched | Review the selected alias and expected identity through the operator's SSH/site process. No tool-version execution should occur after an identity mismatch. |
| Tool version unknown | Check the selected site-managed executable and adapter's supported version-output contract. Do not start a GUI bridge or run arbitrary OCEAN as a fallback. |
| MCP database missing or unsupported | Point the server at the existing intended workspace, or initialize/migrate using the appropriate ordinary application workflow. The reader must not repair it. |
| MCP returns no runs but lists hosts | Host registration does not create runs. A `demo` dataset, if deliberately loaded, contains synthetic runs only. |
| Registered MCP server is missing in a chat | Keep registration, SDK test, and host discovery statuses separate; inspect the client's startup/reload result. |
| MCP shows an old probe | It reads stored evidence. Only an explicit authorized `hosts probe` refreshes the remote observation. |

## 8. Independent review and verification checklist

The following checks are acceptance recommendations, not claims that a live environment passed. Public tests should use temporary local data and injected process/SSH peers; they must not contact a real host, register Codex tools, inspect user credentials, or launch a simulator.

| Boundary | Meaningful test and required invariant |
| --- | --- |
| Profile validation | Reject unknown/duplicate fields, unsupported kinds, non-string identities, command snippets/options, credential fields, and unsafe executable forms before any process call. Preserve caller inputs. |
| SSH identity gate | Fake a wrong hostname/user and malformed/missing identity output. Assert zero subsequent version invocations and no invented identity. |
| Transport uncertainty | Inject timeout, connection failure, interrupted capture, oversized stdout/stderr, and unavailable decoding. Require bounded sanitized unknown results and no automatic retry. |
| Process policy | Inspect the fixed argument vector and host-key/batch behavior with a fake runner. No user command interpolation, free-form options, interactive fallback, or leaked environment/credentials. |
| Tool outcomes | Return one supported version, one explicit failure, and one unparseable response. Keep per-tool outcomes and null unknown versions; never infer license availability. |
| Cross-adapter projection | Every supported SSH tool kind must survive the MCP host summary, or be explicitly reported as unsupported. Preserve validated full version tokens: for example, `IC99.1-64b.500.7` must not become `500.7`, and `99.1.0.123.isr4` must not lose its ISR suffix while still being labeled the version. |
| Registry isolation | Hash existing runs and turns before/after import, list and probe. A probe changes only the appropriate host observation and does not recover a started turn. |
| Probe concurrency | Use two independent processes for the same host: only one fake probe can run. Confirm short database transactions and no overwritten newer result; a thread-only test is insufficient. |
| Read-store opening | Missing and unsupported databases must fail without files, chmod, migration, controller lock, or turn recovery. Test while another process owns the controller. |
| MCP data minimization | Seed private-looking aliases/paths and an unexpected nested field in a host observation. Assert MCP output allowlists omit them rather than forwarding whole stored objects. |
| MCP input boundary | Reject extra path/command arguments and malformed IDs; bound record counts, string length and returned bytes. A large/corrupt stored object must not escape limits or produce a fabricated valid result. |
| Read-only behavior | Snapshot local rows/turn states and deny subprocess/network entry points while executing each query. Calls must not invoke SSH, fake/real generation, demo advancement, or recovery. |
| Prompt/data injection | Seed instruction-like event text and forged approval fields. Queries may report authorized text as data, but no lifecycle method is called and no grant appears. |
| Protocol versus registration | Exercise an SDK client without editing Codex configuration. Separately record any later parent-performed registration and real host invocation; do not count a skipped optional-SDK test as a pass. |

Source review should also distinguish ordinary `Store` startup, which initializes local state, from `ReadStore`, which must be read-only. A capability flag in a state summary is a declaration of the build's interface, not proof the optional SDK was installed or a host was tested. Preserve original `observed_at` timestamps and explicit `synthetic` values at every projection.

The coordinator owns implementation integration and any separately authorized live verification. Record the tested code/SDK versions, observation time, and which verification stage actually completed before changing an unverified status. No simulation-capability claim can be inferred from these connectivity checks.

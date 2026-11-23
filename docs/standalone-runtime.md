# Standalone Spectre runtime

The optional local CLI/web connection sends finite simulation operations to an operator-configured CircuitGit gateway. CircuitGit's existing Runner owns preparation, immutable input/run identity, SSH staging, dispatch and native result collection. SpiceOwl owns the shared client, host-pool checks, existing-run monitor, Spectre diagnostic classification and bounded PSF ASCII parser. No second execution ledger or agent framework is required.

Stored runtime responses follow the [simulation runtime v1 contract](contracts/simulation-runtime.v1.json). This is an alpha integration for prepared netlists and explicit projects. Synthetic demonstrations remain available through `serve --demo`; the public GitHub Pages demo never executes these operations. The [passive IC cache](ic-workbench.md), read-only MCP tools and [placement-only commands](server-pool.md) retain their narrower scope.

## Prepare the connection

The operator needs a compatible CircuitGit gateway, an explicit project/configuration, and a configured server pool. Use CircuitGit's exact external SpiceOwl commit lock and its `docs/spiceowl-resource-submission.md` when installing the pair. The live client currently submits through that configured resource pool; it does not discover projects, derive hosts from a subnet or create a default execution site.

Each pool member needs a working OpenSSH route, verified host/user identity, an explicit Spectre executable, setup environment and writable remote run location. Existing host aliases, jump hosts and host-key checks remain in OpenSSH. Pool capacity is a timestamped observation with headroom, not a reservation or a memory limit. Read [host onboarding](host-onboarding.md) and the [CircuitGit pool contract](circuitgit-integration.md#resource-aware-execution-through-circuitgit).

The client machine does not need PDK files. A committed/exported netlist can refer to model aliases resolved by the execution site's private mappings. Any required licensed models must be accessible on the remote host, and their expected hashes/pins, selected sections and model/include closure must pass the Runner's validation. A source Git SHA alone does not identify every model dependency. The remote Spectre license and setup environment still need to work; a successful version probe does not establish license availability. Do not put model files, license settings or private server paths in a public project.

This standalone route executes the prepared netlist directly with remote Spectre. It does not start Virtuoso, netlist an ADE session, control a CIW window or invoke OCEAN. Existing Virtuoso-managed runs retain their original ownership and host; a pool recommendation does not relocate them. The older IC Service can offer separately authorized OCEAN operations, which are outside this live-client route.

## Private operator grant

The gateway's private `simulation_direct` policy explicitly lists allowed projects, hosts, caller identities and operations. The following fragment illustrates a grant for the SpiceOwl client; merge a reviewed grant into the existing private gateway configuration rather than replacing unrelated settings:

```json
{
  "simulation_direct": {
    "enabled": true,
    "projects": ["example-inverter"],
    "hosts": ["compute-a", "compute-b"],
    "callers": ["spiceowl-local"],
    "operations": ["runtime", "resources_scan", "submit", "status", "collect", "watch", "unwatch"],
    "authority_key_file": "/absolute/private/simulation-authority.key",
    "resume_monitors": false
  }
}
```

These are fictional identifiers and paths. The operator provisions a random 32–128 byte authority key and stores it as an owner-private regular file, normally mode `0600`. The client and gateway use the same key. The key identifies an installed caller within this grant; it does not widen project/host access or replace the existing IC Service review policy. The live endpoint acts within this explicit installed grant. It does not create a second per-request approval workflow.

Create a private copy of [live-client.example.json](../examples/live-client.example.json):

```json
{
  "schema": "spiceowl.live-client.v1",
  "base_url": "http://127.0.0.1:18881",
  "project_id": "example-inverter",
  "authority_key_file": "/absolute/private/simulation-authority.key"
}
```

Replace the example values with the configured gateway port, registered project ID and absolute key-file path. Keep the client file owner-private, normally `0600`, in a private writable directory. The checked-in example contains no key and is not ready to execute. The client rejects extra fields and symlink files. It binds to one project and to the fixed caller `spiceowl-local`; command arguments cannot change that binding.

Only literal `http://127.0.0.1:PORT` endpoints are accepted. The transport reads the gateway health/version, signs the exact method/path/body, uses its same-origin mutation token, and follows no proxy, redirect or automatic retry. Reads have a 30-second deadline and controls a 120-second deadline; responses are limited to 2 MiB. Public responses omit credentials, SSH routing, private paths and raw backend errors. Keep this service on loopback; it is not a multi-user Internet service.

## CLI workflow

Use the installed `spiceowl` command, or replace it with `PYTHONPATH=src .venv/bin/python -m spiceowl` from a source checkout. In the examples below, replace `CONFIGURATION_ID`, `FULL_40_CHARACTER_GIT_SHA` and `RUN_ID` with exact registered or returned values. The source commit must be a full 40-character lowercase Git SHA, not a branch name. The optional `--project` must match the configured project.

Read saved observations, then explicitly scan the allowed pool when a fresh resource check is needed:

```bash
spiceowl live --client-config /absolute/private/live-client.json runtime
spiceowl live --client-config /absolute/private/live-client.json scan
```

`runtime` reads saved run/resource observations. Its `read_at` is a local snapshot time, not a fresh SSH observation. `scan` explicitly starts a bounded pool check and can return `scanning`; read `runtime` again to see its saved result. Each host keeps its actual observation time and unavailable values remain unknown.

Submit the exact configured experiment and retain a unique key for that request:

```bash
spiceowl live --client-config /absolute/private/live-client.json submit --configuration CONFIGURATION_ID --source-commit FULL_40_CHARACTER_GIT_SHA --key experiment-submit-001
```

The Runner freezes the selected site, checks model/input identity and resource eligibility, and owns the remote launch. The receipt supplies the run ID. When `watch` is allowed by the installed grant, a successfully dispatched run is armed for observation; an existing run can also be watched explicitly:

```bash
spiceowl live --client-config /absolute/private/live-client.json status --configuration CONFIGURATION_ID --source-commit FULL_40_CHARACTER_GIT_SHA --run-id RUN_ID
spiceowl live --client-config /absolute/private/live-client.json watch --configuration CONFIGURATION_ID --source-commit FULL_40_CHARACTER_GIT_SHA --run-id RUN_ID
spiceowl live --client-config /absolute/private/live-client.json unwatch --configuration CONFIGURATION_ID --source-commit FULL_40_CHARACTER_GIT_SHA --run-id RUN_ID
```

`status` performs an explicit bounded observation through the Runner. `unwatch` stops monitoring; it does not cancel or kill the simulation. Current live watches use `allow_collect=false`. They do not automatically collect, resubmit, change parameters, increase memory or restart Spectre. After confirmed completion, request collection separately:

```bash
spiceowl live --client-config /absolute/private/live-client.json collect --configuration CONFIGURATION_ID --source-commit FULL_40_CHARACTER_GIT_SHA --run-id RUN_ID --key experiment-collect-001
```

An accepted `collecting` receipt is not proof that transfer or waveform validation has finished. Inspect the same run and saved runtime report. Collection stays bound to the original host/run. This route collects native results and derives supported PSF ASCII summaries locally, without OCEAN.

## Unknown requests and keys

Before a submit or collect POST can be sent, the client persists its identity and key in a private sibling file named `.live-client.json.requests.json` for a config named `live-client.json`. It uses a cross-process lock and atomic writes. Keep CLI and web pointed at the same configuration and preserve that journal when moving an installation. This is client request reconciliation; the Runner remains the execution ledger.

If the response is lost or inconsistent, `live_outcome_unknown` preserves the pending request. Repeating the same input cannot send another POST, even with a new key. A confirmed request can return its cached result. Supplying a key already bound to different inputs is rejected. If `--key` is omitted, the client creates and privately stores one before dispatch; an explicit key is easier to retain for operator lookup.

Query an uncertain submission using its original key:

```bash
spiceowl live --client-config /absolute/private/live-client.json lookup --key experiment-submit-001
```

Lookup reads the existing native submission mapping and verifies the original project/configuration/source identity. `mapping_found` identifies the exact saved run; `mapping_not_found` is not proof that a launch never happened. Neither result permits replay or clears the original unknown dispatch. Continue observing the identified run, or reconcile the gateway's private records with the operator. An uncertain collection likewise keeps its original request and is inspected through the bound run. Do not delete the journal or change keys to bypass uncertainty.

## Web workflow

```bash
spiceowl serve --live-client-config /absolute/private/live-client.json
```

Open the printed SpiceOwl loopback URL and select **真实仿真**. The project field is fixed by the operator. Enter the exact configuration/source commit, submit within the configured grant, or select an existing run to populate its immutable identity. Explicit buttons scan resources, query run state, collect, watch and unwatch. The UI displays execution, last successful observation, diagnostics, data validity and specification independently.

The browser does not accept configuration paths or credentials. It retains pending request keys and offers an exact submission-mapping lookup after an unknown submission. Refreshing saved records performs no SSH scan. `serve --demo --live-client-config ...` can display the separate original demo scenarios alongside this real connection. The public demo does not expose live controls.

## Keeping watch overnight

The monitor runs inside the CircuitGit service process and calls the same bounded Runner status adapter. It stores watch identity, observation attempts, the last successful observation, a bounded event history and backoff. Defaults are a 30-second observation interval and a 300-second maximum failure backoff. A failed connection preserves the last confirmed execution state; exit code 137 or a lost connection alone does not establish OOM.

Keep the gateway process and its execution-host connectivity available. Closing the SpiceOwl browser or CLI does not stop a remote solver. Stopping the gateway stops observation; a sleeping client machine cannot perform observations while asleep. This repository does not install an always-on service manager for you.

By default, after gateway restart, explicitly call `watch` to restart observation of the persisted watches that remain authorized. An operator can set `simulation_direct.resume_monitors=true` to resume previously authorized watches at service startup. The gateway rechecks their original caller, project/configuration/source and frozen host against current authority. Removed permissions do not become new authority. Constructing a client or reading `runtime` does not implicitly arm a watch. Resuming monitoring never replays submission or enables automatic collection.

## What the report proves

| Dimension | Evidence and current limit |
| --- | --- |
| Execution | Saved Runner/remote completion evidence and exit status; loss of observation remains separate |
| Observation | Actual successful check time, latest attempt, next check and failure backoff |
| Data validity | Bounded scalar transient PSF ASCII parsing, finite/aligned values, strictly increasing time and explicitly required signals |
| Specification | `unknown` unless an actual accepted measurement recipe supplies a result; zero exit and readable waveforms do not imply a pass |

The shared parser supports one time sweep, scalar `FLOAT DOUBLE` values, up to 64 signals and 200,000 samples within 16 MiB. It does not parse PSF binary/PSFXL, complex AC data, multidimensional sweeps or arbitrary result databases. Unsupported formats remain unknown; malformed supported input is invalid. Experiment-specific measurement-window coverage and general analog specification recipes are not inferred from file readability. The [developer inverter example](../examples/inverter/README.md) has separate explicit toy acceptance criteria.

Spectre diagnostics classify bounded evidence for license, missing-model, syntax, parameter-bound, convergence and memory failures. Confirmed OOM requires an explicit relevant message or run-bound evidence; a kill signal alone is only suspected OOM. Suggestions are advisory. Native agent Q&A is separate and receives no implicit simulator authority.

## Offline checks

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_live*.py'
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_remote_monitor.py'
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_spectre_*.py'
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_simulation_authority.py'
git diff --check
```

These tests use synthetic records and fake or loopback transports, without Cadence, PDKs, SSH access or model credentials. Cross-repository checks and exact-commit coordination are described in [paired delivery](circuitgit-integration.md#paired-delivery-rule). A real installation still requires its own authorized trial and native evidence; passing these tests does not certify a site or PDK.

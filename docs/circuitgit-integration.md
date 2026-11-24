# CircuitGit SDK and Agent integration

SpiceOwl remains an independently maintained engine [repository](https://github.com/spiceowl-dev/spiceowl). **In embedded mode, CircuitGit owns the Agent channel:** its Codex conversation, existing `codex app-server` dynamic-tool bridge and `ic-workbench` integration call the Python SDK. Embedded SpiceOwl never starts a second Agent or manages another credential set. Independent `spiceowl serve` uses its own explicitly connected window as described in [dual-mode setup](dual-mode-agent.md). The native simulation Runner and existing review authority remain with CircuitGit.

Integration profiles remain **`spiceowl.circuitgit-library-context.v1`**, **`spiceowl.resource-selection.v1`**, and optional **`spiceowl.live-client.v1`**. Supervision contract revision **1.2.0** is recorded by `x-contract-version` in the [JSON contract](contracts/ic-workbench-supervision.v1.json).

## Direct SDK read views

CircuitGit continues to read its own Runner/Supervision records through its
existing service. These views are optional compatibility readers for an
**already existing SpiceOwl store**; they do not create a second store or make a
SpiceOwl record the same record as CircuitGit's authoritative run. Use them only
when that explicitly selected legacy dataset is the intended source.

```python
from spiceowl.read_api import ReadViews, open_read_views

views = open_read_views(operator_selected_state_directory)
status = views.status()
saved_runs = views.list_ic_runs(registered_gateway_id)
saved_run = views.get_ic_run(registered_gateway_id, exact_run_id)
```

The owner selects an existing data directory once. No context manager or close
call is needed; each method uses a short read-only transaction. This imports no
MCP SDK, creates no directory or controller, performs no network request and
launches no process. Missing/unsupported state raises `DomainError`. Stored
timestamps, redaction, independent outcome dimensions and existing bounds stay
intact. `status.mode` is `read_only_sdk`; `agent_channel.connection_status` remains
`not_checked` because this reader cannot prove a Codex connection.
The retained MCP adapter returns this same SDK projection: its former
`read_only_mcp` mode is now `read_only_sdk`, with the additional `agent_channel`
metadata. Import and method compatibility is preserved; clients that compare
the old mode string must accept the new value.

Every previously MCP-only view has a direct equivalent:

| Historical tool | Python method | CLI after `spiceowl --data-dir DIR read` |
| --- | --- | --- |
| `spiceowl_status` | `views.status()` | `status` |
| `spiceowl_list_runs` | `views.list_runs()` | `runs` |
| `spiceowl_get_run` | `views.get_run(run_id)` | `run RUN_ID` |
| `spiceowl_list_connections` | `views.list_connections()` | `connections` |
| `spiceowl_list_hosts` | `views.list_hosts()` | `hosts` |
| `spiceowl_list_ic_gateways` | `views.list_ic_gateways()` | `ic-gateways` |
| `spiceowl_list_ic_runs` | `views.list_ic_runs(gateway_id)` | `ic-runs GATEWAY_ID` |
| `spiceowl_get_ic_run` | `views.get_ic_run(gateway_id, run_id)` | `ic-run GATEWAY_ID RUN_ID` |

These methods read stored evidence only. A historical connection row remains
synthetic or legacy metadata, not an active Agent. Lists remain bounded to 25
recent records, and detail evidence has its existing event/resource bounds.
Model-visible text remains untrusted data. CircuitGit owns tool schemas, argument
validation, conversation/session routing and approval; it calls the SDK in the
same trusted process instead of starting `spiceowl mcp`.

Other core engines are already direct Python APIs:

| Capability | SDK entry point |
| --- | --- |
| Authorized pool selection/recheck | `spiceowl.integrations.circuitgit_pool.CircuitGitPool` |
| PDK source requirements and bounded scan program | `spiceowl.onboarding.pdk_discovery` |
| Private LUT and exact snapshot export | `spiceowl.onboarding.pdk_lut.PdkLut` |
| Same-entry scope selection and audit comparison | `spiceowl.onboarding.pdk_scope` |
| Agent-TB validation / compilation | `spiceowl.onboarding.agent_tb`, `spiceowl.experiments.agent_testbench` |
| Deterministic Spectre diagnostics | `spiceowl.integrations.spectre_diagnostics.diagnose_spectre` |
| Five-dimension report | `spiceowl.integrations.spectre_report` |
| Transient PSF / AC / DC parsing | `spiceowl.integrations.spectre_results.parse_psf_ascii`, `parse_psf_analysis` |
| FFT / AC / DC analysis | `spiceowl.adctools.analyze` and its named functions |

The base SDK requires no MCP package. Numeric operations use the existing
`adctools` extra. Pure parsing/diagnostics never grant authority to scan, submit,
cancel or repair; the owner must bind authorized active operations separately.

A paired installation can smoke-test the import without creating any state:

```python
import sys
sys.modules["mcp"] = None  # This fresh test process deliberately has no MCP SDK.
from spiceowl.read_api import ReadViews, open_read_views

assert callable(open_read_views)
assert all(callable(getattr(ReadViews, name)) for name in (
    "status", "list_runs", "get_run", "list_connections", "list_hosts",
    "list_ic_gateways", "list_ic_runs", "get_ic_run",
))
```

## Embedded schema export

`spiceowl.embedded.read_tool_definitions()` returns the eight fixed stored-read schemas with descriptions and read-only annotations. `dispatch_read_tool(views, name, arguments)` accepts an already opened `ReadViews`, validates exact fields/IDs and delegates to those same methods. It accepts no data-directory argument and imports no MCP/Codex runtime. CircuitGit may prefix these names with `ic_` and must compare its schema projection before dispatch; schema drift fails closed. An optional operator configuration such as `spiceowl_readonly: {enabled: true, data_dir: "/explicit/existing/store"}` selects legacy data, never infers a store from Runner state.

Pure engine definitions and finite host bindings are available through `spiceowl.agent_channel.export_simulation_management_tools(mode="embedded_in_circuitgit", dispatcher=...)`. The host retains its policy and existing `ic_onboarding`/`ic_sim` lifecycle. Standalone uses the same kind of official App-Server connection, described in [dual-mode setup](dual-mode-agent.md), with an explicit optional CircuitGit owner client rather than another execution backend.

## Paired submodule pin

CircuitGit mounts this separate repository at `submodules/spiceowl`. Its
`.gitmodules` supplies the repository URL; the Git **gitlink** supplies the exact
commit. `contracts/spiceowl-integration.lock.json` must record that same commit.
Do not treat a branch name or the presence of a submodule directory as a pin.

After a reviewed SpiceOwl PR is merged, CircuitGit updates the gitlink and lock
together, installs that exact checkout in its isolated environment, and runs the
paired tests before publishing. For an already pinned CircuitGit checkout:

```bash
git submodule update --init --recursive submodules/spiceowl
python -m pip install --no-deps ./submodules/spiceowl
```

The selected CircuitGit environment must separately satisfy any numerical extras
it uses. This sequence does not start a service, modify an intake or authorize a
simulation. SpiceOwl's old MCP imports and pinned optional extra remain only as
compatibility/test surfaces. The old CLI launcher and independent Agent
add/probe/ask actions return a structured `agent_channel_owned_by_circuitgit`
error. Saved history remains readable and is not automatically resumed.

## Compatibility and ownership

The verified baseline `1a23b8cf00d88d2b70cab5407f9d8b613b57e0b1` supports the existing [IC Workbench integration](ic-workbench.md), but rejects extra run fields. **Pin a commit containing this profile, or a reviewed successor, before emitting library context.** The new consumer accepts unchanged legacy records. An old consumer still rejects the extension, even if the producer labels its contract 1.2.0. For an old consumer, omit library/cell and execution-target extension fields completely, including null values, and retain fixed-site envelopes. Resource-pool mode requires both updated producer and consumer. No automatic protocol negotiation is implemented.

The existing routes, envelopes and wire schema names remain:

| Interface | Scope |
| --- | --- |
| `GET /api/supervision?project_id=<registered ID>` | Read a bounded snapshot of saved runs; schema `ic.supervision.snapshot.v1` |
| `GET /api/submissions/<encoded-existing-key>` | Read an existing submission mapping; schema `ic.submission.lookup.v1` |
| `spiceowl ... ic sync/lookup` | Explicit one-shot gateway read into SpiceOwl's private cache |
| `spiceowl ... ic runs/inspect` | Read cached native runs |
| SDK `ReadViews.list_ic_runs` / `ReadViews.get_ic_run` | Read the same cache without a gateway call, process launch or state mutation |

CircuitGit owns `lib/catalog.json`, library manifests, project registration, file closure and view resolution. SpiceOwl does not fetch a project/library catalog. Its gateway registration still requires the existing `id`, `label`, `base_url`, and concrete `project_id`; do not add library fields to that profile or to snapshot/lookup envelopes.

## Optional run context

Add the fields only to `snapshot.runs[]` or `lookup.run`:

```json
{"library_id": "Example_ADC", "cell_id": "tb.inverter-1"}
```

Each field is independently optional and nullable. A non-null value must match `[A-Za-z0-9_][A-Za-z0-9_.-]{0,179}`: 1–180 ASCII characters, case preserved, with no trimming or truncation. These are opaque identifiers, not paths, URLs, display names, or commands. Missing and explicit null are preserved as received and both mean the association is unknown. Partial context is allowed; it does not establish a verified library/cell pair.

The producer must source these values from the explicit historical run record or its frozen plan. Do not copy today's project classification onto an old run, infer them from a directory name, or use the currently selected schematic. If no historical association was recorded, omit the fields or send null. A separate current-library navigation label in CircuitGit must not be presented as the run's original context.

SpiceOwl preserves `project_id`, `original_project_id`, `run_id`, `source_commit`, `configuration` and `compiled_inputs_sha256` under the existing guards. Optional context is additional recorded association evidence, not a replacement identity or proof of file access. Cache identity remains `(gateway_id, run_id)`; never key a simulation by `cell_id` alone. Cell names can repeat across libraries, repositories and registered gateways.

A missing/null cached value may gain an explicit value on a later read. Once non-null, it cannot change or disappear. A conflicting snapshot rolls back the entire batch, preserves prior run evidence and marks observability unavailable. A submission lookup likewise preserves its earlier mapping after a conflict and never authorizes resubmission. Legacy caches need no migration. The Python `lookup_submission(..., expected_identity=...)` helper can additionally check either field; this does not add a CLI argument or active MCP tool.

## CircuitGit caller integration

1. Resolve the selected cell using CircuitGit's validated library manifest. A cell with `project_id: null` is a browsing entry; it cannot register or select a simulation project through this integration.
2. Use the explicit registered `project_id` and native `run_id`. Use the run's recorded source commit for historical waveform identity. Do not choose a latest run or manufacture an ID from a cell name.
3. Show library/cell metadata only when it was returned for that run. Resolve navigation through CircuitGit's own validated catalog and matching project association. A missing entry remains unresolved; metadata does not permit arbitrary file access.
4. The library-context extension adds no execution or approval backend. Existing IC Service actions retain their [review boundary](ic-workbench.md#agent-access). The separate [standalone live client](standalone-runtime.md) invokes the same Runner under an explicit installed project/host/caller grant; it does not replace the Service ledger or expose arbitrary commands.

The public GitHub Pages demo does not mount the full local simulation submission panel; its RC analytic view is separate. A connected library/AI helper is not evidence of resource-pool submission. Installing this commit does not update an already-running CircuitGit process or prove that its producer has begun emitting the fields; reload only through the operator's existing lifecycle procedure.

## Offline acceptance

The [complete snapshot example](../examples/ic-library-context.snapshot.json) is explicitly synthetic. It contains invented identities and metadata, not a downloadable waveform or a real simulation result. It is suitable for producer/consumer contract tests, not the public circuit library.

From a pinned SpiceOwl checkout with its development environment:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_ic_*.py'
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_read_api.py'
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_read_cli.py'
```

The tests cover the serialized gateway envelope, direct SDK/CLI projections, imports with MCP unavailable, unchanged old caches, independent/null fields, exact ID bounds, immutable associations, atomic conflict handling and lookup expectations. Optional `test_mcp.py` retains the historical stdio protocol tests. These tests do not contact CircuitGit, SSH, an AI provider or a simulator.

## Resource-aware execution through CircuitGit

`spiceowl.integrations.circuitgit_pool.CircuitGitPool` is a Python API for an operator-configured CircuitGit gateway. It uses SpiceOwl's existing SSH probes and recommendation policy. This API owns only selection/recheck. The separate [live client](standalone-runtime.md) supplies real CLI/web submission through CircuitGit's existing Runner; stored SDK views remain read-only.

```python
from spiceowl.integrations.circuitgit_pool import CircuitGitPool
pool = CircuitGitPool("/absolute/private/spiceowl-state")
selection = pool.select(["compute-a", "compute-b"], {
    "cpu_count": 2, "memory_bytes": 4 * 1024**3, "simulator": "spectre"
})
latest = pool.recheck(selection)
```

Selection actively scans the 1–32 explicitly registered members once, then applies the existing resource policy: at most 120-second resource observations, verified host/user and installed Spectre version, CPU/load checks, 20% CPU and memory headroom after the request, and a 1 GiB home-filesystem floor. Ranking maximizes the remaining bottleneck slack. Unknown, failed, stale or insufficient observations cannot establish eligibility. Scan failures cannot reuse an earlier successful observation; failed scans remain in `excluded` with `scan_failed` and null observation times, so the returned recommendation accounts for every requested host without leaking private errors. This checks home disk space, not the run directory's quota or filesystem.

The returned `spiceowl.resource-selection.v1` contains exactly `schema`, `host_id`, `requirements`, `selected_at`, `profile`, `profile_sha256`, `simulator_version`, and `recommendation`. The private profile includes routing details; retain it in private gateway state. It is not an HTTP/MCP response. `recheck` refreshes only the selected host and rejects changed profile/version or insufficient resources. It may follow a human review delay; the old observation remains old and is never relabelled fresh.

The paired CircuitGit adapter performs the rest of the sequence:

1. Plan and validate the committed/materialized inputs and explicit simulator thread budget locally.
2. Select from registered hosts mapped to private CircuitGit site files, then perform the chosen site's normal preflight.
3. Freeze the selected site, selection evidence, input identity and preflight result into an HMAC-signed `execution_plan_id`.
4. Carry that same plan and host through the selected authority path: existing browser/Service review, or the explicit installed grant for the finite standalone runtime.
5. Reserve the existing submission key, recheck the same host before staging, and dispatch through the existing Runner.
6. Query and collect using the saved run ID and frozen site. A lost launch response stays uncertain and is reconciled without resubmission.

A resource refusal before staging records a failed preflight with zero launch attempts. Reusing its key returns that same outcome; a deliberate new attempt needs a new plan/key and review. No background queue, automatic migration, resource allocation, cgroup memory cap, license reservation, OOM recovery or scientific parameter repair is provided. Memory is an operator-supplied planning estimate. Increasing a simulator's thread count beyond the pool request is rejected before any scan.

The matching CircuitGit implementation and private configuration format are documented in its `docs/spiceowl-resource-submission.md`. Its submodule gitlink and integration lock pin one reviewed SpiceOwl commit. CircuitGit imports SpiceOwl's shared monitor, diagnostic classifier and PSF ASCII parser. The [standalone guide](standalone-runtime.md) covers the signed client and preserves the single-Runner boundary.

## Optional stored execution target

Run projections may include this exact bounded structure:

```json
{
  "execution_target": {
    "mode": "resource_pool",
    "host_id": "compute-a",
    "plan_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "resource_observed_at": "2026-11-23T00:00:00+00:00",
    "resource_reservation": false
  }
}
```

`host_id` is an opaque 1–96 character SpiceOwl identifier (`[A-Za-z0-9][A-Za-z0-9_-]{0,95}`); `plan_id` is a 64-character lowercase hex digest. Observation time is null or a valid ISO date-time with a timezone, preserved exactly. For `mode: fixed_site`, all three identity/time fields are null. Legacy records may omit the structure. A known cached target cannot change or disappear. It contains no SSH hostname, user, path or license information.

Snapshot `single_site: false` means the gateway is configured for a resource pool. It does not count servers that have actually executed jobs. Stored execution target and read time never imply a new scan, active agent monitoring or a reserved machine.

## Paired delivery rule

Treat a simulation integration change as one deliverable across SpiceOwl and CircuitGit: update the external API, CircuitGit adapter, both byte-identical supervision contracts, consumer/producer tests, documentation and the CircuitGit exact-commit lock together. Carry both PR references and run the cross-repository check before handoff. Do not ask the user to commission a separate synchronization task. This development rule does not automatically deploy software, reload a live gateway or merge either repository's unrelated work.

For the historical compatibility check of producer → loopback HTTP → cache → MCP/JS, use the explicitly selected CircuitGit checkout:

```bash
CIRCUITGIT_ROOT=/absolute/CircuitGit PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_circuitgit_supervision_contract.py'
```

This test uses synthetic saved records and the real producer/consumer modules; it performs no SSH, remote submission or license checkout.

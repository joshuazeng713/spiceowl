# IC Workbench integration

SpiceOwl can read IC Workbench's stored simulation records through an explicitly registered loopback gateway. A compact supervision card fits inside IC Workbench's existing simulation panel. IC Workbench remains the owner of project compilation, submission IDs, approval requests, execution and collection. The integration does not run a second executor. This guide describes the optional passive `ic` cache and existing IC Service actions. CircuitGit normally reads its own Runner/Supervision records and uses the PDK/diagnostic SDK directly; it does not need to create or register this separate cache to use SpiceOwl's engine. The separately configured [standalone live client](standalone-runtime.md) adds finite CLI/web control and existing-run watches through the same Runner.

AI interaction uses CircuitGit's existing Codex window and IC Workbench host integration. SpiceOwl provides engine functions and stored read views to the caller; this setup does not require a separate SpiceOwl MCP server or native-agent chat service. See [the current AI route](native-agent.md).

## Register and read

Create a private copy of [the example profile](../examples/ic-workbench.example.json), fill in a registered IC project ID and the actual local gateway address, then use the source checkout or an installed package:

```bash
spiceowl --data-dir .spiceowl ic register --file PRIVATE_GATEWAY_PROFILE.json
spiceowl --data-dir .spiceowl ic list
spiceowl --data-dir .spiceowl ic sync GATEWAY_ID
spiceowl --data-dir .spiceowl ic runs GATEWAY_ID
spiceowl --data-dir .spiceowl ic inspect GATEWAY_ID RUN_ID
spiceowl --data-dir .spiceowl ic lookup GATEWAY_ID EXISTING_SUBMISSION_KEY
```

In a source checkout, prefix commands with `PYTHONPATH=src` and replace `spiceowl` with `.venv/bin/python -m spiceowl`.

Registration and cache reads are local. `sync` makes one bounded GET to `/api/supervision?project_id=...`; `lookup` makes one GET to `/api/submissions/<encoded-key>`. Neither operation performs SSH, refreshes a simulator, collects results, submits jobs, or approves actions. The gateway must implement the [v1 contract](contracts/ic-workbench-supervision.v1.json). The client permits only a literal HTTP IPv4 loopback address, with no proxy or redirect following.

The cache is durable and separate from synthetic demonstration runs. Failed reads preserve existing evidence and mark its observability as unavailable. A missing submission-key mapping is not permission to resubmit an uncertain operation. `sync` and `lookup` return a nonzero exit code when the new read cannot be established, even if old data remains available.

## Agent access

The neutral engine SDK provides three methods for the already synchronized local cache:

- `ReadViews.list_ic_gateways()`
- `ReadViews.list_ic_runs(gateway_id)`
- `ReadViews.get_ic_run(gateway_id, run_id)`

Import `ReadViews` or `open_read_views(directory)` from `spiceowl.read_api` only when this optional cache already exists and its stored observations are the intended dataset. The factory opens that original SpiceOwl SQLite state read-only, without starting a controller or contacting IC Workbench. It does not map CircuitGit's native records into the same store. The views do not expose gateway addresses, submission keys, private paths, or credentials. The other five methods read status, synthetic runs, fake connection metadata and stored host observations. See [the SDK method list](real-connections.md#5-read-saved-state-through-the-engine-sdk).

CircuitGit owns the AI tool catalog and routes its supported operations through the existing Service. A Python method is not automatically a tool in a live Codex session; verify actual exposure and invocation in the selected host. The old `spiceowl_*` MCP read names remain compatibility-test interfaces, with optional official SDK support. They are not a second registration step for the current CircuitGit setup.

For controlled actions, continue to use IC Workbench's existing `ic_sim_submit`, `ic_request_status`, and `ic_request_review` with its configured Service ledger. Submission prepares a pending human-review request; review resolution remains in the host's existing approval flow. Collection can run OCEAN postprocessing, and publication writes to GitHub, so neither is treated as a passive read. This integration adds no machine approval endpoint.

## UI and evidence

In IC Studio, select the project and a saved run in the top toolbar, then open the simulation panel's **Run / 运行** tab. The SpiceOwl card shows the saved observation and opens the existing waveform panel. Use the waveform panel's **Open run / 打开运行** control (`O`) to load that saved run; `L` opens its signal picker. Reopen a closed panel from the top-right **Windows / 窗口** menu.

The IC Workbench supervision component receives its existing host/context/gateway APIs and mounts within the existing simulation panel. It reuses the selected native run and waveform window. It has no independent sidebar, agent chat, source editor, or browser iframe. Closing or hiding the UI never cancels a remote job.

Preserve the following distinctions:

| Evidence | Meaning |
| --- | --- |
| `read_at` / local `checked_at` | Time the stored data was read, not a new remote observation |
| `observed_at` | Original stored remote observation time |
| execution state | Recorded solver outcome, independent of result collection |
| data state | Collection state, not scientific measurement validity |
| specification `reported` | Metrics exist; this does not mean all specifications passed |
| artifact SHA | Digest recorded by the producer; a metadata read does not re-download or re-hash the file |

Historical `source_commit` and `original_project_id` remain intact. The registered project association is explicit. Missing compiled-input hashes stay null. A waveform can only be opened with matching project/run/source identity; selecting an unrelated current schematic must not relabel a historical waveform.

Contract revision 1.2.0 additionally accepts optional recorded `library_id` and `cell_id` on runs. The [CircuitGit external integration profile](circuitgit-integration.md) defines their historical meaning, cache guards and compatibility with older strict consumers. The fields pass through CLI and cached SDK reads without enabling library discovery or execution.

## Scope and tests

This passive slice supports the configured IC site’s stored records and existing controlled action channel. Its cache sync never polls remote processes continuously or enables recovery. The separate standalone runtime supports explicit existing-run watches. Multi-host reservations, checkpoint migration, automatic parameter edits and automatic resubmission remain unimplemented.

Run `PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_ic_*.py'`. Read-view tests exercise the engine SDK against synthetic local state. Optional MCP tests retain coverage of the historical stdio transport; they are not a prerequisite for engine use or proof of CircuitGit session integration. IC Workbench has separate gateway and supervision-component tests.

An explicitly requested live read check is available as `python3 scripts/verify_ic_integration.py --help`. Its report belongs in ignored private state. Do not publish actual circuit metadata, site files, run archives, PDK contents, or license settings.

Contract revision 1.2.0 also supports a bounded saved `execution_target` and pool-configured `single_site: false`. Resource selection is an explicitly invoked external Python adapter; the snapshot/CLI/SDK read path remains passive. See the [resource execution and paired-delivery profile](circuitgit-integration.md#resource-aware-execution-through-circuitgit).

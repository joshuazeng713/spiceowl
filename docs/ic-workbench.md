# IC Workbench integration

SpiceOwl can read IC Workbench's stored simulation records through an explicitly registered loopback gateway. A compact supervision card fits inside IC Workbench's existing simulation panel. IC Workbench remains the owner of project compilation, submission IDs, approval requests, execution and collection. The integration does not run a second executor.

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

The optional MCP server adds three tools for the already synchronized local cache:

- `spiceowl_list_ic_gateways()`
- `spiceowl_list_ic_runs(gateway_id)`
- `spiceowl_get_ic_run(gateway_id, run_id)`

They open existing SQLite state read-only, without starting a controller or contacting IC Workbench. They do not expose gateway addresses, submission keys, private paths, or credentials. A running MCP client must reload its tool catalog after updating the package; a source test does not establish that an existing desktop session has reloaded it. The original five SpiceOwl read tools retain their behavior.

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

Contract revision 1.1.0 additionally accepts optional recorded `library_id` and `cell_id` on runs. The [CircuitGit external integration profile](circuitgit-integration.md) defines their historical meaning, cache guards and compatibility with older strict consumers. The fields pass through CLI and cached MCP reads without enabling library discovery or execution.

## Scope and tests

This slice supports the configured IC site's stored records and existing controlled action channel. It does not implement multi-host reservations, continuous remote polling, process recovery, checkpoint migration, automatic parameter edits, or automatic resubmission.

Run `PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_ic_*.py'`. MCP tests exercise the actual optional stdio server against synthetic local state. IC Workbench has separate gateway and supervision-component tests.

An explicitly requested live read check is available as `python3 scripts/verify_ic_integration.py --help`. Its report belongs in ignored private state. Do not publish actual circuit metadata, site files, run archives, PDK contents, or license settings.

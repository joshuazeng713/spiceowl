# CircuitGit external integration

Integration profile: **`spiceowl.circuitgit-library-context.v1`**. Supervision contract revision: **1.1.0**, identified by `x-contract-version` in the [JSON contract](contracts/ic-workbench-supervision.v1.json). SpiceOwl remains independently maintained in [its own repository](https://github.com/joshuazeng713/spiceowl). CircuitGit should reference a reviewed SpiceOwl commit and these documents; it does not need to vendor the SpiceOwl implementation.

## Compatibility and ownership

The verified baseline `1a23b8cf00d88d2b70cab5407f9d8b613b57e0b1` supports the existing [IC Workbench integration](ic-workbench.md), but rejects extra run fields. **Pin a commit containing this profile, or a reviewed successor, before emitting library context.** The new consumer accepts unchanged legacy records. An old consumer still rejects the extension, even if the producer labels its contract 1.1.0. For an old consumer, omit both fields completely, including null values. No automatic protocol negotiation is implemented.

The existing routes, envelopes and wire schema names remain:

| Interface | Scope |
| --- | --- |
| `GET /api/supervision?project_id=<registered ID>` | Read a bounded snapshot of saved runs; schema `ic.supervision.snapshot.v1` |
| `GET /api/submissions/<encoded-existing-key>` | Read an existing submission mapping; schema `ic.submission.lookup.v1` |
| `spiceowl ... ic sync/lookup` | Explicit one-shot gateway read into SpiceOwl's private cache |
| `spiceowl ... ic runs/inspect` | Read cached native runs |
| MCP `spiceowl_list_ic_runs` / `spiceowl_get_ic_run` | Read the same cache without a gateway call, process launch or state mutation |

CircuitGit owns `lib/catalog.json`, library manifests, project registration, file closure and view resolution. SpiceOwl does not fetch a project/library catalog. Its gateway registration still requires the existing `id`, `label`, `base_url`, and concrete `project_id`; do not add library fields to that profile or to snapshot/lookup envelopes.

## Optional run context

Add the fields only to `snapshot.runs[]` or `lookup.run`:

```json
{"library_id": "Example_ADC", "cell_id": "tb.inverter-1"}
```

Each field is independently optional and nullable. A non-null value must match `[A-Za-z0-9][A-Za-z0-9_.-]{0,127}`: 1–128 ASCII characters, case preserved, with no trimming or truncation. These are opaque identifiers, not paths, URLs, display names, or commands. Missing and explicit null are preserved as received and both mean the association is unknown. Partial context is allowed; it does not establish a verified library/cell pair.

The producer must source these values from the explicit historical run record or its frozen plan. Do not copy today's project classification onto an old run, infer them from a directory name, or use the currently selected schematic. If no historical association was recorded, omit the fields or send null. A separate current-library navigation label in CircuitGit must not be presented as the run's original context.

SpiceOwl preserves `project_id`, `original_project_id`, `run_id`, `source_commit`, `configuration` and `compiled_inputs_sha256` under the existing guards. Optional context is additional recorded association evidence, not a replacement identity or proof of file access. Cache identity remains `(gateway_id, run_id)`; never key a simulation by `cell_id` alone. Cell names can repeat across libraries, repositories and registered gateways.

A missing/null cached value may gain an explicit value on a later read. Once non-null, it cannot change or disappear. A conflicting snapshot rolls back the entire batch, preserves prior run evidence and marks observability unavailable. A submission lookup likewise preserves its earlier mapping after a conflict and never authorizes resubmission. Legacy caches need no migration. The Python `lookup_submission(..., expected_identity=...)` helper can additionally check either field; this does not add a CLI argument or active MCP tool.

## CircuitGit caller integration

1. Resolve the selected cell using CircuitGit's validated library manifest. A cell with `project_id: null` is a browsing entry; it cannot register or select a simulation project through this integration.
2. Use the explicit registered `project_id` and native `run_id`. Use the run's recorded source commit for historical waveform identity. Do not choose a latest run or manufacture an ID from a cell name.
3. Show library/cell metadata only when it was returned for that run. Resolve navigation through CircuitGit's own validated catalog and matching project association. A missing entry remains unresolved; metadata does not permit arbitrary file access.
4. Keep submission, cancellation, collection and approval in CircuitGit's existing IC Service workflow. Refer to the [existing action boundary](ic-workbench.md#agent-access). This extension adds no execution or approval backend.

The public GitHub Pages demo remains synthetic and has no live gateway access. Installing this commit does not update an already-running MCP process or prove that CircuitGit's producer has begun emitting the fields; reload only through the operator's existing lifecycle procedure.

## Offline acceptance

The [complete snapshot example](../examples/ic-library-context.snapshot.json) is explicitly synthetic. It contains invented identities and metadata, not a downloadable waveform or a real simulation result. It is suitable for producer/consumer contract tests, not the public circuit library.

From a pinned SpiceOwl checkout with its development environment:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_ic_*.py'
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_mcp.py'
```

The tests cover the serialized gateway envelope, cache/CLI/MCP round trip, old caches, independent/null fields, exact ID bounds, immutable associations, atomic conflict handling and lookup expectations. With the optional MCP SDK installed, the existing real stdio test also verifies the fields and confirms that the tool catalog and local state remain unchanged. These tests do not contact CircuitGit, SSH, an AI provider or a simulator.

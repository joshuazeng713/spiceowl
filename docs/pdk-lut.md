# Private PDK observation lookup

`spiceowl.onboarding.pdk_lut` implements a local SQLite lookup table over the
complete JSON emitted by the bounded PDK scanner. It indexes each observed
required model or subcircuit by entry, sections, source-backed polarity and
terminal order, dependency hashes and TMI options. It performs no SSH, PDK-file
read or simulation. CircuitGit owns scanning and the final dependency audit.

This is private runtime state: the original discovery contains remote paths
and licensed-model metadata. Store the database outside Git and do not publish
it. This module adds no PDK source bodies. Tests use newly authored synthetic
models.

## Storage and lookup

```python
from spiceowl.onboarding.pdk_lut import PdkLut, rebind_discovery

# These values come from an already obtained, selected remote observation.
with PdkLut(private_database_path) as cache:
    saved = cache.put(discovery, server_identity=observed_server_identity)

with PdkLut(private_database_path, read_only=True) as cache:
    result = cache.lookup(
        server_identity=observed_server_identity,
        roots=observed_pdk_roots,
        required_masters=requirements['required_masters'],
        max_age_s=86400,
    )
    if result['status'] == 'hit':
        bound = rebind_discovery(result['record'], requirements)
```

Read-only handles use SQLite `mode=ro`; they do not create files/tables or change
schema metadata. The caller can return a miss before opening a missing database.
Missing files and `put` through a read-only handle raise `DomainError`.

Server identity requires observed `hostname`, `username` and integer `uid`.
Optional `host_key_sha256` and `machine_id_sha256` distinguish installations when
actually observed. Home directory, cwd, shell and SSH alias do not replace server
identity. The caller owns observation authenticity and connection binding.

`put(discovery, *, server_identity, observed_at=None)` validates the original
discovery digest and atomically stores its full JSON plus index rows.
`observed_at` is Unix seconds, defaulting to insertion time. The cache ID binds
server/root/model-set scope, content fingerprint, original discovery ID and time.
Identical inputs and time are idempotent.

| `lookup` argument | Meaning |
| --- | --- |
| `server_identity`, `roots`, `required_masters` | Exact server, normalized root set and required-model set; model order does not affect the key. |
| `content_fingerprint=None` | Optional expected fingerprint from separately supplied observations; mismatch is a miss. |
| `max_age_s=86400`, `now=None` | Local age policy and optional Unix time. |
| `refresh=False` | True returns `refresh_required` without reusing a record or starting a scan. |
| `include_discovery=True` | False returns metadata without decoding the complete original receipt. |

Results have `status` (`hit`, `miss`, `stale`, `refresh_required`), `scan_required`
and `record` or null. Records retain cache/discovery/source identities, content
fingerprint, observation time, coverage and staleness. `staleness` contains
`age_s`, `max_age_s`, and `state` (`within_local_age_policy`, `expired`,
`clock_skew`). Future observations are stale. Reading never advances their time.

An age-valid hit is still a last-observed snapshot:
`remote_freshness_verified=false`, `remote_content_current="unknown"`, and
`fresh_audit_required=true`. Without a new remote observation the client cannot
discover remote file changes. A supplied matching fingerprint compares evidence;
it does not mean this module reread a server. Run preparation retains its audit.

## Scope and content fingerprint

Every record has `coverage.kind="required_model_filtered"`, exact original
`required_masters`, `covered_model_names` observed in candidate closures,
`search_complete`, `results_omitted`, unknown/candidate counts, search policy and
detail omissions. `whole_pdk_inventory` is always false.

A scan for A/B cannot satisfy a lookup for A alone, A/B/C or all models. A query
may inspect only A *within* the original A/B index; this does not change the scan
scope. Incomplete observations remain incomplete. A content fingerprint hashes
the observed roots, scanned file paths/content SHAs and TMI library paths/content
SHAs. It describes actually read files under the scanner's bounded policy, never
every PDK file. Alternate content observations retain separate records.

## Indexed model queries

```python
with PdkLut(private_database_path, read_only=True) as cache:
    page = cache.query_bindings(
        server_identity=observed_server_identity,
        roots=observed_pdk_roots,
        required_masters=requirements['required_masters'],
        model_names=[requirements['required_masters'][0]], limit=256,
    )
```

`query_bindings` accepts lookup arguments except `include_discovery`, plus
`model_names`, `limit` (1–4096) and `offset`. It returns metadata without the full
receipt, plus `mappings`, `total`, `truncated`, `next_offset`. Pass the returned
offset unchanged. Filters must remain within the original indexed required set.
Stale/missing/refresh-required records return no current mappings.

Mappings retain the full model evidence, candidate ID, entry/SHA, role, sections,
polarity, ordered terminal roles, dependency scopes/hashes and all TMI choices.
Unknown stays unknown. Pages obey a 2 MiB compact JSON budget. Byte-limited pages
report truncation; an individual oversized mapping reports
`binding_exceeds_query_budget` without skipping it or claiming no matches. The
application owns the final HTTP-envelope limit.

### Best complete binding

```python
with PdkLut(private_database_path, read_only=True) as cache:
    best = cache.query_best_bindings(
        server_identity=observed_server_identity,
        roots=observed_pdk_roots,
        required_masters=requirements['required_masters'],
        remote_platform=observed_platform,
        limit=8,
    )
```

The best-binding query returns the usual cache envelope plus `recommendation`.
`put` computes model ranking once and indexes the complete proposals. A lookup
reads at most `limit` (1–32) ranked entries plus the selected default if needed;
it does not re-rank the full observation or select the first sorted digest.
`remote_platform` resolves TMI only for these returned entries. A different
platform does not silently select a lower-ranked entry; unresolved ABI evidence
stays missing.

`recommendation` uses `spiceowl.model-binding-recommendations.v1` and retains
`recommended_candidate_id`, `selection_confidence`, `choice_basis`, `alternatives`,
`missing`, `status`, `candidate_count`, `candidates_truncated`, and the candidate
proposals. `complete_binding_available` requires a selected candidate with no
missing binding fields; `ready_to_run` remains false and the audit requirement
remains true. Ambiguous corners/contracts never become a default through ID
ordering.

Each proposal includes `sections`, `model_contracts` and `tmi_relative_library`
Fields, rank/score/evidence, plus `binding` with `entry_path`, `entry_sha256`,
`dependency_scopes`, `dependency_hashes`, full `source_models` and observed
`tmi_candidates`. This is private backend data; public adapters must remove
paths rather than expose the raw query result. The 2 MiB budget can reduce the
returned alternative count but never trims the selected binding's model evidence.

SQLite schema version 2 adds the ranking index. A writable open migrates the
tables without modifying original receipts; read-only version-1 lookups remain
supported. Old observations without ranking rows return
`binding_index_unavailable`. The caller may index their retained original receipt
with `put`, retaining its original `observed_at`; this requires no new scan.

## Rebinding and generic recommendations

`rebind_discovery(record, requirements)` is pure. It checks the original receipt
digest, metadata, fingerprint and cache ID. Only the exact original required set
with observed coverage for every model is accepted. The requirements schema is
`spiceowl.netlist-model-requirements.v1`; `mode="agent-tb"` is supported without
an inverter assumption.

A separate returned receipt has the new source SHA, recomputed source-bound
candidate IDs/ambiguity references and a new full discovery ID. Model, section,
dependency and TMI evidence remains unchanged. `cache_provenance` preserves the
original discovery ID/source SHA, time, cache ID and fingerprint. The stored JSON
is never rewritten, freshness never advances, and an audit remains required.

```python
from spiceowl.onboarding.recommendations import recommend_model_bindings
draft = recommend_model_bindings(bound, remote_platform=observed_platform)
```

The pure `spiceowl.model-binding-recommendations.v1` result has ranked candidates,
section/model/TMI Fields and missing evidence, without stimuli, experimental
intent or inverter connectivity checks. Optional `required_masters` must match
the exact scan set. Existing `recommend_onboarding` keeps its inverter interface.

Ranking uses actual required closure and source-backed contracts first. Usage
entries including all required wrappers get an evidence weight. Typical usage
plus an observed `pre_simu` gets a stated conventional preference; adding the
section requires an audit of the final combination. `core`, `ulvt`, `lvt`, `svt`,
`hvt`, `io` name tokens assist ordering only as labeled heuristics. They cannot
establish polarity, terminal order or process suitability. `1d8_usage.scs` is no
voltage evidence; absent applicable literals, the inverter preset remains 0.9 V.
Unknown contracts, TMI, and operating qualification remain explicit.

## Bounds and local benchmark

The SQLite file must be owner-private (0600), with a 128 MiB SQLite page limit.
Capacity failures roll back; old observations are not silently pruned. Original
discovery validation retains its existing 2 MiB limit. Query pages are bounded
separately. Use one handle per application thread.

```bash
PYTHONPATH=src .venv/bin/python -B -m tests.benchmark_pdk_lut --iterations 100
```

The benchmark generates 181 actual scanner candidates for two required models,
then returns 181 mappings for one model. It reports ingestion, first query, hot
queries, complete receipt lookup and cold connection queries separately. Cold
means a new SQLite connection; OS disk caches are not purged. The target is an
indexed local query below 10 ms, not a guarantee for network/scanning, ingestion,
all mapping sizes or every cold start. No SSH or simulator is involved.

Measured on November 23, 2026: Darwin arm64, Python 3.12.14, SQLite 3.53.1.
The final implementation with the ranking index produced these local samples:

| Operation | Samples | Median | p95 | Maximum |
| --- | ---: | ---: | ---: | ---: |
| Hot best complete binding, up to eight ranked proposals | 100 | 0.562 ms | 0.615 ms | 0.766 ms |
| New read-only connection and best complete binding | 25 | 0.660 ms | 0.818 ms | 0.914 ms |
| Hot raw indexed query, 181 mappings | 100 | 2.349 ms | 2.697 ms | 6.081 ms |
| Complete original receipt lookup | 100 | 4.942 ms | 5.612 ms | 8.384 ms |
| New read-only connection and raw indexed query | 25 | 2.779 ms | 4.454 ms | 5.228 ms |

Initial ingestion, including ranking, was 57.809 ms. The first best-binding query
was 0.806 ms; the first raw indexed query was 2.848 ms. The benchmark asserts that
the selected typical entry covers both models and has a complete binding. These are
measurements for this synthetic workload and local filesystem, not a performance
promise. Re-run the script on the deployment machine to characterize its cache.

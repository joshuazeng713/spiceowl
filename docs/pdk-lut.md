# Private PDK observation lookup

`spiceowl.onboarding.pdk_lut` implements a local SQLite lookup table over the
complete JSON emitted by the bounded PDK scanner. Schema version 3 supports an
explicit per-device library observation and the existing required-model-filtered
observation as separate scopes. It indexes observed models or subcircuits by entry, sections, source-backed polarity and
terminal order, dependency hashes and TMI options. It performs no SSH, PDK-file
read or simulation. CircuitGit owns scanning and the final dependency audit.

This is private runtime state: the original discovery contains remote paths
and licensed-model metadata. Store the database outside Git and do not publish
it. This module adds no PDK source bodies. Tests use newly authored synthetic
models.

## Full-library observation and per-device lookup

`build_pdk_library_script(roots=..., cds_libs=None, search_hints=None, limits=None)`
in `spiceowl.onboarding.pdk_discovery` generates a bounded Python 2.7/3 metadata
scanner. The caller supplies observed, authorized roots and runs it through its
existing transport. The result uses `spiceowl.pdk-library.v1`, independently of
any circuit's source SHA or required model set. Building the script does not run
it. `validate_pdk_library` validates its digest, bounds and indexed scope.

```python
with PdkLut(private_database_path) as cache:
    saved = cache.put_library(library, server_identity=observed_server_identity)

with PdkLut(private_database_path, read_only=True) as cache:
    devices = cache.query_library_devices(
        server_identity=observed_server_identity,
        roots=observed_pdk_roots, limit=64,
    )
    bound = cache.lookup_library_models(
        server_identity=observed_server_identity,
        roots=observed_pdk_roots,
        required_masters=requirements['required_masters'],
        requirements=requirements,
        remote_platform=observed_platform,
    )
```

The same library can serve A, B, A+B and A+C if those devices were actually
indexed. No subsequent query scans a PDK or contacts a server. The full original
JSON is retained unchanged, while `library_devices` indexes `(cache_id,
model_name, candidate_id)`. `library_entries` retains shared closure metadata.
The server/root scope contains no circuit model set. A filtered receipt cannot
be relabeled, promoted or used to satisfy this library lookup.

New library receipts also retain `requested_roots`, copied from the remote
scanner's explicit input. Each position maps to the same position in `roots`,
which contains the server-observed canonical path. The mapping is complete and
receipt-bound; repeated canonical roots are allowed when distinct logical roots
resolve to the same directory. Duplicate requested roots, incomplete mappings
and colliding logical/canonical identities are rejected. No client-side
`realpath` is performed.

Within the same observed server identity, lookup accepts either that observation's
complete requested-root set or its complete canonical-root set. It does not mix
root aliases from different observations, assemble mixed logical/canonical sets,
or infer a mapping for an unobserved prefix or subset. Cache metadata retains
`requested_roots` and explicit `root_resolutions`; its scope index references the
same immutable observation. Legacy receipts without `requested_roots` remain
canonical-only, including read-only version-3 databases predating the additive
root-scope index.

A fresh scan after a symlink target changes becomes the default observation for
the same requested-root set. A supplied fingerprint that differs from this
newest observation returns a miss rather than falling back to older matching
bytes. Equally recent observations with different canonical scopes return
`root_alias_ambiguous`. An explicitly supplied `library_id` can still retrieve a
historical snapshot for stable pagination. TTL and fresh-audit requirements
apply to both forms; alias lookup never claims the remote symlink is unchanged.

The scanner retains its finite file, graph, directory and byte policy. This mode
stores at most 4096 device names, 256 candidate entries and 8192 device-entry
bindings, with a 16 MiB receipt ceiling. It can index more than 128 devices in one
entry; a source-bound binding query accepts up to 128 required names. Coverage
records observed, indexed, unindexed and omitted counts, search policy and
truncation. `complete_within_policy` means the declared attempt completed within
those bounds; `whole_pdk_qualified` is always false. Parser gaps, omitted files,
entries or names remain visible. This is metadata coverage, not process or
simulator qualification.

Both APIs accept `library_id`, `content_fingerprint`, `max_age_s`, `now` and
`refresh`. TTL, identity and freshness semantics match the legacy lookup below.
Fingerprinting covers actually observed root/file SHA pairs, including observed
TMI binaries. `query_library_devices` returns `devices` with `model_name` and
`entry_count`, `total` and `next_offset`. Continuation must pass the exact returned
`record.library_id` with the unchanged scope and filters; it never mixes a newer
scan into an older page. `pdk_library_page(library, offset=0, limit=64)` pages the
names directly from a retained immutable receipt.

`lookup_library_models` returns a `discovery` only when every requested name
shares an actual observed entry closure and current source `requirements` were
supplied. It preserves provider contracts, dependency hashes, exact sections,
source literals and TMI evidence; candidate IDs and discovery ID are rebound to
the supplied source SHA. Each candidate keeps `library_candidate_id`, and
`cache_provenance` binds the original library, fingerprint and observation time.
The receipt feeds existing profile suggestions, recommendations and audit-input
builders. Without requirements it can return a recommendation but cannot invent
a source-bound discovery. `hit` means observed model coverage, not a complete or
executable binding; recommendation gaps and the fresh-audit requirement remain.

Missing names return `miss` or `partial` with `models_not_indexed`, no discovery,
and `scan_required=true`. If names exist only across disjoint entries, the result
is `partial` with `spiceowl.pdk-multi-entry-draft.v1`. This bounded draft selects
at most eight entries and reports corner conflicts, duplicate physical model
declarations including unrequested globals, unknown scopes and TMI conflicts.
It always requires explicit selection and a fresh combined dependency audit;
`combination_verified=false` and `ready_to_run=false`. It is never represented as
an observed single entry.

For a large cross-product of model and entry evidence, a binding result may be
`partial` with `binding_query_size` and observed `entry_choices`. Reuse the same
`library_id` and pass a chosen `library_candidate_id` to narrow the query to its
actual entry. This performs no new scan and does not authorize execution. Query
responses retain the 2 MiB bound, and a single oversized complete response fails
explicitly rather than dropping contract evidence.

Writable opens migrate schema versions 1/2 additively to 3 and preserve earlier
filtered receipts and indexes. Read-only opens support older versions without
mutating them; they return a library miss when the library tables are absent.

### Full-library benchmark

```bash
PYTHONPATH=src .venv/bin/python -B -m tests.benchmark_pdk_library --iterations 100
PYTHONPATH=src .venv/bin/python -B -m tests.benchmark_pdk_library --devices 256 --entries 32 --iterations 10
```

Measured November 24, 2026 on Darwin arm64, Python 3.12.14, SQLite 3.53.1. The
default newly authored workload contains 160 devices, 33 actual entry closures
and 5280 device-entry bindings. One full scan took 168.445 ms and insertion took
140.288 ms. Its receipt was about 4.87 MB and SQLite database about 14.84 MB.

| Operation | Samples | Median | p95 | Maximum |
| --- | ---: | ---: | ---: | ---: |
| Device page, 64 names | 100 | 0.348 ms | 0.372 ms | 0.383 ms |
| Device page with a new read-only connection | 25 | 0.562 ms | 2.299 ms | 4.347 ms |
| One device across all 33 entries | 100 | 9.387 ms | 15.103 ms | 20.031 ms |
| Sixteen devices across all 33 entries | 100 | 23.361 ms | 29.374 ms | 32.780 ms |
| One device in an explicitly selected entry | 100 | 0.495 ms | 0.517 ms | 0.531 ms |
| Sixteen devices in an explicitly selected entry | 100 | 1.022 ms | 1.079 ms | 1.131 ms |
| 128 devices in an explicitly selected entry | 100 | 5.473 ms | 5.816 ms | 10.765 ms |

The unrestricted 128-device query correctly returned `partial/binding_query_size`;
selecting an observed entry returned all 128 contracts from the same library.
These measurements do not establish a universal 10 ms bound: full alternative
evidence is substantially more expensive than indexed name lookup. Cold means a
new SQLite connection; filesystem caches are not purged. Benchmarks perform no
SSH, compiler or simulator operation and do not enforce timing in CI.

The additional cap workload requested 256 devices across 33 potential entries.
It stored 8192 device-entry bindings across 32 entries and correctly marked the
receipt `partial` when the binding cap was reached. Scan/insertion were
256.966/281.103 ms. Over ten samples, a device page had median/max
0.495/0.514 ms; 128 devices in an explicitly selected observed entry had
median/max 5.817/5.924 ms. The partial search flag remains in source-bound results;
locally observed coverage never erases that wider inventory gap.

## Verilog-A standard-header metadata

`build_veriloga_header_probe_script(roots=..., limits=None)` in
`spiceowl.onboarding.veriloga_headers` generates a separate bounded metadata
probe. It searches only for `constants.vams` and `disciplines.vams` directly in
one to eight explicitly observed tool include roots, then follows static quoted
includes within those roots. It never guesses a Cadence installation path.

The `spiceowl.veriloga-headers.v1` result includes `observation_id`, standard
`headers`, hashed `files`, include edges (`from`, `reference`, resolved `path`,
line and conditional status), explicit `missing` gaps and coverage. It contains
no source bodies. Dynamic includes, unknown conditions, alternate header-guard
branches, missing/ambiguous paths, cycles and symlink escapes cannot become
complete observations. Conventional single outer include guards are recognized;
macro conditions are never evaluated and no compiler is invoked.

Bounds are 32 attempted files, 256 include edges, 2 MiB per file, 8 MiB total,
depth 16 and 512 KiB output; caller limits may only tighten them. One extra byte
may be read to detect a size overflow. Use `validate_veriloga_headers` before
converting the receipt to a binding. Runner maps actual observed paths to allowed
aliases and relative staged names, retains all reachable files/edges, uses
`observation_id` as the header closure's `inventory_sha256`, and performs its
fresh byte audit. Leaf standard headers still require an actual probe receipt.
The metadata probe alone does not stage, compile or run anything.

Use the pure helper to convert an observation for the selected AHDL dependencies:

```python
from spiceowl.onboarding.veriloga_headers import build_veriloga_header_binding

headers = build_veriloga_header_binding(
    observation,
    required_names=['constants.vams', 'disciplines.vams'],
    selected_root=selected_observed_include_root,  # optional when unambiguous
)
pdk_binding.update(headers['public'])
private_aliases = headers['private_aliases']
```

The return shape is `{public, private_aliases, observation_id}`. `public` contains
only `veriloga_standard_includes` and `veriloga_header_closure`, using relative
file names, controlled aliases, hashes and include edges. `private_aliases` maps
each alias to `{path, sha256}` for Runner; never expose this map in a public
binding. The helper invokes deep shape, configured-budget and closure validation
before conversion. A receipt digest establishes integrity, not remote authenticity.

`selected_root` must exactly match a root in the receipt. Each standard header's
`include_roots` records where it was observed; selecting a root can resolve
multiple standard-header candidates. Standard names must match the exact
`include_root/name` observed path. Standard-entry symlinks are explicit gaps
until a separately validated alias-provenance contract is available; a digest
cannot relabel a disciplines header as a constants header. The helper emits only the required reachable
closure, including explicit leaf rows. It preserves include reference spelling
and staged relative resolution, including a copy of a root-fallback header when
needed for a nested include's layout. Unresolved relevant gaps, truncated gap
metadata, cycles, unsupported aliases, case collisions and conflicting layouts
fail explicitly. Every staged byte remains subject to Runner's fresh audit.

## Legacy filtered storage and lookup

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

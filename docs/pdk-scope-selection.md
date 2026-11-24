# Explicit cached PDK scope selection

`spiceowl.onboarding.pdk_scope` selects several observed sections of **one entry
file** from a retained full-library observation. Its four pure SDK functions do
not read model paths, contact a server, mutate the library or choose a corner.
CircuitGit owns intake revisions, probe provenance, saved selections, compilation,
fresh audits and review. Cross-file entry combinations are unsupported.

A complete retained graph remains usable when the old candidate list hit its
cap. Whole-library partial coverage stays visible. An omitted or truncated graph
cannot supply invented choices.

## Transfer one observed cache snapshot into a new intake

When an explicit discovery operation hits the global private LUT, its owner can
use `PdkLut.lookup_library_snapshot(*, server_identity, roots, library_id,
content_fingerprint, cache_id, max_age_s=DEFAULT_MAX_AGE_S, now=None)`. All three
identities are required. It reads exactly that original cache record; it does
not fall back to another observation, assemble a replacement graph or scan.

The `spiceowl.pdk-library-snapshot.v1` result has `status: hit`, `miss` or `stale`,
`record`, `library`, `missing`, `scan_required`, `fresh_audit_required: true`,
`remote_freshness_verified: false`, and `ready_to_run: false`. Only a hit returns
the intact library. The method checks the original library digest, cache identity,
file fingerprint, metadata, host and complete root set. An observation's own
canonical roots or its exact requested-root aliases are accepted; subsets or
roots from another observation are not. Future/expired observations are stale;
corrupt storage raises `DomainError`. Library/export bounds are 16/18 MiB with
no evidence truncation. `scan_required` is metadata, never an initiated action.

CircuitGit may privately persist this snapshot in the current intake only when
the user's explicit discovery and original cache-hit identity agree. Catalog
reads stay scoped to that intake and never borrow another intake's observations.

## Tool sequence

```python
from spiceowl.onboarding.pdk_scope import (
    library_scope_catalog, project_library_scopes,
    validate_scope_selection, verify_scope_selection_audit,
)

entries = library_scope_catalog(library, offset=0, limit=64)
sections = library_scope_catalog(library, entry_id=observed_entry_id)
proposal = project_library_scopes(
    library, requirements=current_source_requirements,
    server_identity=original_probe_identity, entry_id=observed_entry_id,
    section_ids=explicit_ordered_section_ids,
)
# If needed, choose an observed public.tmi_options identity and project again
# with tmi_option_id=that_identity. This does not initiate another scan.
validate_scope_selection(proposal)
# The execution owner acquires an authorized fresh audit separately.
comparison = verify_scope_selection_audit(
    proposal, actual_audit, audit_config=actual_audit_config,
)
```

Identifiers above are caller variables. Choose only IDs returned for this exact
library. Persist the original library and selection separately. Never manufacture
an old candidate ID or transfer another section's verified MOS contract.
This sequence requires no LLM once the entry, ordered sections, platform and
experiment are explicit. Synthetic tests establish tool behavior, not weak-model
performance or real simulator qualification.

## Public and private contracts

`library_scope_catalog(library, *, entry_id=None, offset=0, limit=64)` returns
`spiceowl.pdk-scope-catalog.v1`. Offset is 0..16384; limit is 1..128. `kind` is
`entries` or `sections`; `items`, `total` and `next_offset` support pagination.
`status` is `available` or `blocked`, with code-only `missing`.

Entry rows contain `entry_id`, basename `label`, `entry_sha256`, `section_count`.
Section rows contain `section_id`, exact `section` (or null for global),
`has_include_targets`, `observed_model_count`, `unknown_codes`. Counts describe
local declarations including global declarations; project the selection for
closure coverage. IDs bind the library and observed file/scope identities.
Public responses contain no absolute paths.

`project_library_scopes(library, *, requirements, server_identity, entry_id,
section_ids, tmi_option_id=None)` accepts 1..64 unique ordered section IDs from
one entry. Its `spiceowl.pdk-scope-selection.v1` response contains:

| Object | Fields |
| --- | --- |
| Public identity | `selection_id`, `library_id`, `source_sha256`, `requirements_sha256`, `entry_id`, `section_ids`, `sections` |
| Required coverage | `required_model_names` in original order, `covered_model_names`, `missing_model_names`, `model_declarations` (name, declaration count, port counts) |
| Closure summary | `scope.node_count`, `scope.file_count` (model source files), `scope.namespace_model_count` (all names) |
| TMI choices | `tmi_options` as `{tmi_option_id, label}`, nullable `selected_tmi_option_id` |
| Status | `status: blocked` or `requires_audit`, code-only `missing`, original `library_complete_within_policy` |
| Limits | `fresh_audit_required: true`, `ready_to_run: false`, `scientific_compatibility: not_evaluated`, `model_contracts_reused: false` |
| Private evidence | `server_identity`, `roots`, complete `requirements`, `entry_path`, `entry_sha256`, `dependency_scopes`, `expected_files`, all `master_declarations`, `tmi_evidence`, nullable `audit_config` |

Keep private evidence out of public responses, fixtures and GitHub. Results have
a 2 MiB bound; oversized evidence raises `DomainError` rather than being dropped.
The graph checks missing targets, cycles, opaque/unknown selected scopes,
inconsistent edges and distinct physical declarations of the same name anywhere
in the selected namespace. Repeated physical declarations are deduplicated.
Global declarations participate in named sections. An unrelated unsupported
section need not block a complete selected closure.

TMI choices are common observed ABI labels across every active directory. Each
option binds all corresponding paths and hashes. Differing binaries under one
ABI cannot be selected; an independent common ABI with consistent hashes remains
selectable. Conflicting observations of the same directory block the proposal.
The desktop OS never chooses the ABI; binary presence does not prove compatibility.

## Audit comparison

`validate_scope_selection(selection)` returns the same valid object or raises
`DomainError`. It checks structure, bounds, internal bindings, coverage, TMI,
configuration and digest. The digest covers all public/private fields except
`selection_id`. It is a consistency check, not a signature; the trusted caller
must bind the result to its original authenticated observation and revision.

`verify_scope_selection_audit(selection, audit, *, audit_config)` returns
`spiceowl.pdk-scope-audit-comparison.v1`. Invalid selections raise `DomainError`;
invalid/mismatched audits return `status: mismatch` with code-only gaps. A match
has `status: matched` and `matches_selection: true`, but still returns
`ready_to_run: false`, `human_review_required: true`, `fresh_audit_required: true`,
`remote_freshness_verified: false`, `scientific_compatibility: not_evaluated`.

Comparison requires the actual normalized configuration, complete supported
static syntax, no unknown/missing evidence, exactly equal file/hash sets including
selected TMI binaries, equal visited section sets/count and limits, and matching
required declarations with locations and ordered ports. Full hashes and identical
scope closure preserve cached whole-namespace conflict evidence; required
declarations are checked again. Unrequested declarations may be omitted, but any
additional declaration supplied by an audit must match the cached namespace.
The base audit emitter does not currently collect
primitive `model` declarations as required masters; those requests remain
mismatches rather than receiving invented proof.

The execution owner binds host, operation and acquisition time, then freezes
selection and comparison into existing review. Matching dictionaries cannot prove
freshness, correct PVT, legal dimensions, voltage limits, loads, stimuli,
measurement intent or a successful simulation.

## Agent-authored testbench binding

The compiler accepts a separate static binding branch:

```text
required: scope_selection_id, library_id, sections, model_names, pdk_sha256
optional: entry_sha256, tmi_option_id, audit_sha256,
          veriloga_standard_includes, veriloga_header_closure
```

New IDs/digests are lowercase 64-hex. Omit `tmi_option_id` if unused; null is
invalid in this binding. `pdk_sha256` hashes the generated wrapper bytes;
existing Verilog-A checks still apply. Never mix this branch with `discovery_id`,
`candidate_id` or `tmi_candidate_id`. Historical candidate bindings remain
compatible. Static readiness grants no execution or fresh-audit authority.

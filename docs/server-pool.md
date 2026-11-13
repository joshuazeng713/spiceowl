# Server pools and reviewable placement plans

P0.3 adds resource-based recommendations and two explicit simulation origins to the existing [connectivity layer](real-connections.md). The pure scheduling functions described here are implemented with offline tests. CLI, pool scanning, resource cards, and native agent Q&A are integrated by the coordinating workstream under the [P0.3 contract](contracts/p03-pool-agent.md). This guide is not evidence that any real host was tested or a simulation was submitted.

A recommendation is a calculation over stored observations. It performs **no network request, reservation, allocation, or dispatch**. An execution plan is a reviewable description of a possible route. Every returned plan has `reservation: false` and `dispatch_enabled: false`.

All names, references, paths, and numeric examples below are fictional. Keep actual host configuration and experiment metadata in private local state. The examples are not instructions to contact the fictional hosts.

## Register only the hosts you select

Start with an existing host profile imported through `hosts add --file`, as described in the [real connection guide](real-connections.md). Its `host_id` can serve as a route template. The pool import takes an explicit list of hostnames:

```bash
spiceowl --data-dir /home/example/state/spiceowl hosts import-pool --template example-route-template --names node-a.example.invalid node-b.example.invalid
spiceowl --data-dir /home/example/state/spiceowl hosts list
```

The template retains the existing OpenSSH route, user and authentication configuration. Each P0.3 `spiceowl.host.v2` profile adds `ssh_hostname`, which supplies a fixed target hostname while `ssh_target` selects that route template. No key material is copied and global SSH configuration is not rewritten. Existing v1 profiles remain usable.

This is explicit profile expansion, not subnet discovery or a wildcard scan. Verify the resulting host IDs, expected hostname/user, and registered tool paths before an active scan. Exact native identity still has to match the profile; a reachable route does not make an identity mismatch acceptable.

## Scan explicitly, then request a recommendation

The P0.3 CLI workflow is:

```bash
spiceowl --data-dir /home/example/state/spiceowl hosts scan
spiceowl --data-dir /home/example/state/spiceowl hosts recommend --cpus 4 --memory-gib 8 --simulator spectre
```

`hosts scan` is the active operation. It checks the explicitly registered pool, with at most 32 selected hosts and at most two hosts in parallel. Version and resource observations are separate operations under the same host identity/locking boundary. The registry saves them separately as `last_probe` and `last_resources`. Resource sampling observes Linux aggregate CPU/load, memory and home-filesystem capacity, not other users' command lines or files.

`hosts recommend` only reads the saved observations. It does not refresh old data, start a job, or reserve the best host. Empty candidate lists are legitimate results with explicit exclusion reasons. A slow or large scan can leave its earliest resource observations older than the recommendation freshness limit; displaying the scan result does not refresh those timestamps or extend the limit.

The UI's host resource cards display saved metrics and timestamps separately from the recommendation form. Read the candidate/excluded reasons as well as the cards: a host can have a recent memory value yet remain ineligible because its CPU utilization, version evidence, or identity is unknown. A newer card refresh is not a new remote observation unless an explicit scan/probe actually occurred.

## What qualifies a host

The function accepts the exact request shape:

```json
{
  "cpu_count": 4,
  "memory_bytes": 8589934592,
  "simulator": "spectre"
}
```

`cpu_count` is an integer from 1 to 4096. `memory_bytes` is a positive integer; 8 GiB is 8 × 1,073,741,824 bytes. Numeric booleans and unknown request fields are rejected. Supported standalone simulators are `spectre` and `ngspice`.

Each host must have both observations bound to the same registered host ID and expected hostname/user. The registered host and both snapshots must explicitly declare `synthetic: false`; identity must explicitly be `verified: true`, transport must be reachable, and each observation's top-level error must be null. These are checks of stored evidence, not independent proof of a remote machine's present state.

| Check | Fixed P0.3 rule |
| --- | --- |
| Resource freshness | Timestamp age between 0 and 120 seconds, inclusive. |
| Version freshness | Timestamp age between 0 and 86,400 seconds, inclusive: a 24-hour budget independent from the resource budget. |
| Timestamp integrity | Timezone-aware timestamps are required. Missing, malformed, naive, and future-dated observations are excluded. |
| Complete resource evidence | All seven metrics must be present and valid: CPU count, CPU busy percentage, 1/5-minute load, total/available memory, and home-disk available bytes. Null is never treated as zero or idle. |
| Requested tool | Exactly one matching tool entry must be available with a full supported version token. A declared executable alone is insufficient. |
| CPU utilization | Current busy percentage cannot exceed 80%. After adding the requested CPU count to estimated current use, at least 20% of observed CPU capacity must remain idle. |
| Load | The larger of the 1/5-minute loads plus requested CPUs must not exceed 80% of observed CPU count. |
| Memory | Available memory minus requested bytes must remain at least `ceil(total_memory / 5)`: 20% of total memory, rounded up to bytes. |
| Home disk | At least 1 GiB must be observed available on the home filesystem. This is a screening floor, not an estimate of the experiment's output needs. |

The 20% reserves apply to total observed capacity, not merely to the requested amount. For example, a ten-CPU host with 40% CPU busy has six estimated idle CPUs. A four-CPU request leaves two, exactly the reserve; a larger busy value fails that check. Similarly, a 64 GiB host must retain approximately 12.8 GiB after the requested memory is subtracted. The integer implementation rounds the memory reserve upward.

These are conservative prototype rules, not universal scheduling policy. Aggregate CPU count is not a verified allocation of physical cores or CPU affinity. Load can reflect pressure other than CPU use. Memory may change immediately after sampling. Home-filesystem space does not establish output-directory space or user quota. Even an available version observed within 24 hours does not establish a license, PDK/model installation, compatible runtime, or authorized job ownership. A future launch must revalidate the exact experiment and current environment.

Malformed outer input, duplicate host IDs, more than 32 supplied host records, and malformed requirements raise `DomainError`. A host with incomplete/stale metrics instead appears in `excluded`; one bad observation does not silently become an idle candidate or hide the reasons other hosts were rejected.

## Ranking and returned fields

For eligible hosts, the score is the smallest post-request reserve-adjusted fraction among CPU idle capacity, load capacity, and memory. Higher is better:

```text
cpu_slack = estimated_idle_cpus - requested_cpus - 0.20 * total_cpus
load_slack = 0.80 * total_cpus - max(load_1m, load_5m) - requested_cpus
memory_slack = available_memory - requested_memory - ceil(total_memory / 5)
score = min(cpu_slack / total_cpus,
            load_slack / total_cpus,
            memory_slack / total_memory)
```

The tie-breakers are larger memory slack, newer resource observation, newer version observation, then host ID. Candidate order is deterministic for fixed inputs and evaluation time. This score is a screening heuristic, not a throughput prediction or reservation priority. Two requests can receive the same recommendation because neither reserves anything.

`recommend_hosts(host_records, requirements, now=None)` returns:

```text
requirements, evaluated_at, recommended_host_id: ID | null,
candidates: [{host_id, label, rank, simulator, version, score,
              resource_observed_at, version_observed_at,
              resource_age_seconds, version_age_seconds,
              resources, estimated_headroom, reasons}],
excluded: [{host_id, label, resource_observed_at, version_observed_at,
            resource_age_seconds, version_age_seconds, reason_codes, reasons}],
policy, source="stored_host_observations", scope="placement_recommendation",
reservation=false, dispatch_enabled=false
```

`now` may be an aware datetime or ISO timestamp for reproducible offline evaluation; omitting it uses current UTC. Original source timestamp strings are retained. Candidate/exclusion output does not copy SSH aliases, executable paths, expected usernames, raw error messages, or arbitrary observation fields. Reasons are generated by the rules, not taken from instruction-like text in a snapshot. Caller dictionaries are never mutated.

## Plan a new standalone experiment

The parent plan endpoint accepts `{origin, requirements}`. It computes placement from registered records, then calls `plan_execution(origin, placement)`. A corresponding local plan file can contain:

```json
{
  "origin": {
    "kind": "standalone",
    "simulator": "spectre",
    "input_ref": "local:example-export-v1"
  },
  "requirements": {
    "cpu_count": 4,
    "memory_bytes": 8589934592,
    "simulator": "spectre"
  }
}
```

```bash
spiceowl --data-dir /home/example/state/spiceowl execution plan --file /home/example/private/standalone-plan.local.json
```

`input_ref` is an opaque local registry ID, optionally prefixed with `local:`. The ID portion follows the shared identifier rule: 1–96 ASCII letters, digits, underscores or hyphens, starting with a letter or digit. It is not a filename, URL, shell snippet, netlist text, or request to open a file. Reference existence and input closure are not verified by this planning function.

The standalone route is `standalone_exported_netlist`. With candidates, the plan records `status: proposed`, the highest-ranked `proposed_host_id`, and the recommendation's evaluation timestamp. With no candidates or no recommendation, it records `status: no_candidate` and a null host. The origin's simulator must match the placement request. A plan based on old evidence is still only a proposal and needs fresh validation before any future launch.

## Preserve a Virtuoso-managed task's native origin

A native-origin example is:

```json
{
  "origin": {
    "kind": "virtuoso_managed",
    "simulator": "spectre",
    "host_id": "example-native-host",
    "native_run_ref": "example-native-run-42"
  },
  "requirements": {
    "cpu_count": 4,
    "memory_bytes": 8589934592,
    "simulator": "spectre"
  }
}
```

The same `execution plan --file` workflow can use this JSON. `host_id` and `native_run_ref` are strict opaque IDs, not host commands, file paths, process guesses, or a request to select the latest run. Syntax validation does not verify that the native mapping exists.

The route is `virtuoso_native_manager`, with `status: native_mapping_retained` and `relocatable: false`. The bound host and native reference remain unchanged even if the pool ranks another host first, or has no eligible hosts. The plan does not move, resubmit, reconnect to, or cancel that native job, and does not invent a submission key.

Route selection is separate from execution capability. This slice does not claim that Virtuoso GUI/native workflows are globally unsupported: it records their distinct ownership route. Current plan capabilities for submission, relocation, and native execution control are all false. A standalone origin's `relocatable: true` only means that its new exported-input experiment may be considered for placement; it is not implemented live migration.

Both routes return `origin`, `source_kind`, `route`, `status`, `host_id`, `proposed_host_id`, `native_run_ref`, `relocatable`, `placement_evaluated_at`, `requires_review: true`, explanatory reasons/prerequisites, false capability flags, `reservation: false`, and `dispatch_enabled: false`. `validate_origin` accepts strict raw origin fields and adds derived relocatability; `plan_execution` accepts either the raw origin or that validated result, checking any supplied derived value.

## What is still required before a real launch

A pool recommendation or native route is not an experiment submission. A later execution implementation must freeze the actual input and transitive include/model identities, validate licenses/environment and present resource availability, obtain concrete launch authorization, establish remote ownership, and durably record the submission operation and receipt. Unknown outcomes must be reconciled before any retry. No netlist or actual run is supplied by the examples above.

Native agent Q&A is a separate P0.3 workstream. An answer or explanation does not reserve a server, approve a plan, rewrite an origin, or authorize a simulation. Existing read-only MCP queries remain query-only. The state of a diagnostic conversation does not own a simulator task.

The offline tests cover threshold boundaries, independently stale resource/version evidence, mixed eligible/excluded pools, missing/null/invalid metrics, identity/provenance mismatches, deterministic ranking, input immutability, command-like data, and preservation of the native binding. They use authored fixtures only and do not contact SSH hosts or execute simulators. Public test success must not be presented as qualification of a real server pool.

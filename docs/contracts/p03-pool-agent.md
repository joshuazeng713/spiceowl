# P0.3 server pool, placement and native Q&A

This slice extends P0.2 with explicitly selected hosts, Linux resource snapshots, resource-based placement proposals, simulation-origin routing, and a real native Codex Q&A connection. A resource observation is not a reservation or proof that a simulation can run. No real simulation is submitted without an explicit experiment and launch request.

## Host routing and metrics

Existing `spiceowl.host.v1` profiles remain valid. `spiceowl.host.v2` adds exactly one required `ssh_hostname` field to the seven v1 fields. It is a strict hostname, used as the fixed OpenSSH Hostname option while `ssh_target` selects an existing, operator-trusted route template (user, key reference, jump host). Never copy keys, change global SSH config, disable host-key validation, infer unlisted hosts, or network-scan a subnet. The parent registers only operator-listed candidates. Returned private profiles preserve their schema and optional field.

`spiceowl.adapters.hosts.probe_resources(profile) -> dict` uses existing bounded OpenSSH machinery with the same host/user identity gate. No agent-generated commands or path selection. Return:

```text
host_id, synthetic=false, scope="host_resources", observed_at,
transport="reachable"|"unknown", identity={verified,hostname,username},
resources={cpu_count:int|null, cpu_busy_percent:number|null,
           load_1m:number|null, load_5m:number|null,
           memory_total_bytes:int|null, memory_available_bytes:int|null,
           home_disk_available_bytes:int|null},
error=null|{code,message}
```

Collect bounded Linux aggregate data (/proc and home-filesystem capacity), not other users' command lines/files. CPU utilization requires a short interval delta; unavailable metrics are null, never zero. Memory availability uses MemAvailable; if absent return unknown. The home disk sample is only an observation of that filesystem. Tool/version probing stays a separate operation; parent pool scans both explicit operations with at most two hosts concurrently, a maximum of 32 selected hosts, and per-host locks shared with existing probes. Persist `last_resources` separately from `last_probe` without changing simulation runs.

## Placement and simulation origin

`spiceowl.scheduling.placement.recommend_hosts(host_records, requirements, *, now=None) -> dict` is pure: no networking, mutation, reservation or dispatch. Requirements have exactly `cpu_count` (1..4096 integer), `memory_bytes` (positive integer), and `simulator` (spectre|ngspice). It inspects expected-identity results, snapshot freshness, tool availability, CPU busy/load and memory with explicit headroom. Expose candidate reasons, estimated headroom, observation timestamps and `reservation: false`, `dispatch_enabled: false`. Unknown/stale/incomplete snapshots do not become idle candidates. Empty candidate set is valid. Version observations may use a documented longer freshness budget than load measurements.

`spiceowl.scheduling.origins.validate_origin(data) -> dict` validates two distinct simulation origins:

- `standalone`: `{kind, simulator: spectre|ngspice, input_ref: local opaque reference}` for a new exported-netlist experiment. No file reads or shell command passthrough.
- `virtuoso_managed`: `{kind, simulator: spectre, host_id, native_run_ref}` preserves the selected host/native reference. Existing native jobs are not moved or submitted again by the pool; record `relocatable: false`.

`plan_execution(origin, placement) -> dict` returns a reviewable plan. Standalone plans can propose the highest-ranked host; native plans preserve their bound host and explain the native ownership boundary. Neither claims a job was launched. Actual launch requires frozen input/include closure, license/environment/resource validation, remote ownership and a persisted submission receipt; no such input was supplied for the live test in this slice.

## Real agent Q&A

`spiceowl.agent.native.validate_agent_profile(data) -> dict`: strict `{schema:"spiceowl.agent.v1", agent_id, label, executable: absolute codex binary path, model: string|null}`. No keys, tokens, arbitrary flags/commands, external URLs or borrowed thread IDs. Native Codex reads its own existing provider/auth configuration. Do not assume a subscription when the configured provider uses an API route.

`probe_agent(profile, workspace) -> dict` performs bounded native protocol/config/account readiness reads without inference. `ask_agent(profile, question, workspace) -> dict` performs one bounded text Q&A with no automatic retry, fallback provider, or simulator tools. Use the official version-matched app-server protocol or a documented CLI fallback. Check local schema/help and official documentation. Prefer ephemeral native threads and a safe scratch cwd; do not attach or write to unrelated existing Codex sessions. Requests must restrict tool capabilities, keep sandbox read-only, and deny approval/elicitation/tool requests; never relax managed policy. Return `status` completed|failed|outcome_unknown, `answer`, `model`, `provider`, `native_thread_id`, `native_turn_id`, `usage` (null when unavailable), `error`, `synthetic:false`. Safe errors exclude raw auth/config/stdout/stderr content. Once an inference request could have been sent, a transport failure is outcome_unknown, not proof of nonexecution.

Parent persists native profile metadata under `agent_profile`; Q&A records under `qa`, independently of synthetic diagnoses. Parent owns request-key deduplication and controller recovery (unfinished Q&A becomes outcome_unknown, never replayed). UI allows a small question, shows actual provider/model and status, and preserves the response. No secret configuration enters HTTP/MCP projections. Existing five read-only MCP tools remain query-only; pool details may be included in host projections, without adding active tools.

## Work ownership

- Simulator: `adapters/hosts.py`, `adapters/executors/ssh.py`, new resource parser module under `adapters/`, and `tests/test_host_resources.py` plus needed additions to `test_ssh_hosts.py`.
- AI: `agent/native.py` and optional submodules only under `agent/native_support/`; `tests/test_native_agent.py`. No live inference in the child; parent performs one real Q&A after review. Report native version/config constraints.
- Debug/scheduling: `scheduling/placement.py`, `scheduling/origins.py`, package init, `tests/test_placement.py`, `docs/server-pool.md`. Pure offline tests; no live SSH or job actions.
- Parent: registry, pool scans, CLI/service/UI, native Q&A persistence, packaging/README/MCP host projection, private configuration, live server checks, real Q&A, Git integration.

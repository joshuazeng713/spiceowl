<p align="center">
  <img src="assets/brand/spiceowl.svg" width="128" height="128" alt="SpiceOwl: an owl watching a circuit waveform">
</p>

<h1 align="center">SpiceOwl</h1>

<p align="center"><strong>You sleep. SpiceOwl keeps watch.</strong></p>
<p align="center">An open-source agent console for long-running circuit simulations.</p>
<p align="center">English · <a href="README.zh-CN.md">简体中文</a> · <a href="docs/architecture.md">Architecture</a> · <a href="docs/roadmap.md">Roadmap</a> · <a href="CONTRIBUTING.md">Contributing</a></p>

> **Early alpha — server pool and native agent Q&A.** Compare bounded SSH resource observations, prepare standalone or Virtuoso-origin execution plans, and ask a real installed Codex runtime a short question. Simulation runs and run diagnoses are still synthetic; job submission and unattended monitoring remain planned. Optional MCP tools read saved records. See the [console guide](docs/local-console.md), [server pool guide](docs/server-pool.md), and [native Q&A guide](docs/native-agent.md).

![SpiceOwl local console showing explicitly synthetic runs](docs/images/local-console.png)

*Actual prototype interface with authored synthetic observations.*

## Why SpiceOwl

You start a simulation before leaving the lab. In the morning, the connection is gone, a job ran out of memory, or the run finished without the signals needed for your measurement.

SpiceOwl is being built to turn that uncertainty into a clear record: what ran, what happened, which recovery actions were taken, and whether the resulting data answers the intended experiment.

Our first target is **analog and mixed-signal research using Virtuoso and Spectre**, beginning with one validated Linux execution environment. Public development will use synthetic fixtures and a mock executor so contributors can participate without a commercial simulator or PDK.

## What we are building

| Capability | Intended behavior | Status |
| --- | --- | --- |
| Connect | Manage user-owned agent runtimes and model APIs with explicit auth, capability, and usage boundaries | Native Codex Q&A, fake agent/API management, MCP queries and SSH host/resource checks implemented |
| Observe | Track execution identity, simulation progress, logs, memory, and disk usage | Real aggregate host snapshots; simulation observations remain synthetic |
| Reconnect | Reconcile an existing job after a client or controller interruption | Local record recovery implemented; real jobs planned |
| Validate | Check inputs, required signals, measurement windows, and result completeness | Acceptance reducer implemented; waveform validators planned |
| Recover | Apply bounded, recorded recovery policies for supported failures | Advisory policy only; dispatch disabled |
| Diagnose | Use an optional agent to explain evidence and propose actions | Offline fixture diagnosis implemented |
| Report | Separate execution outcome, measurement validity, and circuit performance | Synthetic four-dimensional reports implemented |

### A morning report should answer three different questions

| Question | Example answer |
| --- | --- |
| Did the simulator finish? | Execution completed |
| Is this a valid measurement of the requested experiment? | Inputs matched; required signals and time window are present |
| Did the circuit meet its specification? | No — settling time exceeded the target |

A valid result that misses a design target is still a result worth preserving. Recovery must not change the experiment simply to turn a failure into a pass.

The report also shows observation freshness separately, so an old running status is not mistaken for a current observation.

## Design principles

- **Execution survives the interface.** A remote execution service or scheduler owns the job; the browser and agent conversation do not.
- **Unknown is a real state.** A missing connection is not proof of a failed simulation. Reconcile the original job before considering another submission.
- **Rules keep watch; agents investigate.** Routine monitoring and known recovery policies should work without an LLM. An agent proposes structured actions that the policy layer evaluates.
- **Every attempt has provenance.** Preserve the resolved inputs, tool identity, execution receipt, evidence, and changes between attempts.
- **Recovery preserves experimental intent.** Changes to tolerances, stimuli, corners, seeds, or measurement definitions require an explicit policy or a reviewed new experiment.
- **Resume means what the backend supports.** Controller recovery, job reconnection, and solver checkpoint recovery are separate capabilities.

## Planned architecture

```mermaid
flowchart TD
    UI[CLI / web console] --> Controller[Controller and policy engine]
    Controller <--> Store[Durable run records]
    Controller --> Executor[Execution adapter]
    Executor --> Simulator[Simulator adapter]
    Simulator --> Evidence[Logs, resources, and result artifacts]
    Evidence --> Checks[Monitoring and validation]
    Checks --> Store
    Checks --> UI
    Checks --> Agent[Optional diagnostic agent]
    Agent --> Proposal[Evidence-backed action proposal]
    Proposal --> Controller
```

The [architecture](docs/architecture.md) defines ownership and failure handling. The [run contract](docs/run-contract.md) defines what must be recorded before an experiment can be trusted.

The current slice adds **a server pool, execution-origin planning and native Q&A**, following the [P0.3 contract](docs/contracts/p03-pool-agent.md). The earlier [P0.2 contract](docs/contracts/p02-connectivity.md) covers SSH/MCP fundamentals. The broader [implementation plan](docs/design/integration-plan.md) also proposes API-key and ACP routes; these remain future work. The [three design workstreams](docs/design/README.md) share the [interface boundaries](docs/contracts/interface-boundaries.md).

## Repository map

```text
spiceowl/
├── README.md                  # English introduction
├── README.zh-CN.md            # Chinese introduction
├── docs/                      # Architecture, run contract, roadmap, decisions
├── assets/brand/              # Editable project icon and exported avatar
├── examples/                  # Draft experiment, connection and policy examples
├── src/spiceowl/              # Installable Python package
│   ├── domain/                # Experiment, attempt, receipt, and event models
│   ├── adapters/              # Execution and simulator integrations
│   ├── monitoring/            # Observation and failure classification
│   ├── validation/            # Input and measurement acceptance checks
│   ├── recovery/              # Policy evaluation and action proposals
│   ├── storage/               # Durable records and artifact references
│   ├── agent/                 # Connections, sessions and optional diagnosis
│   ├── mcp/                   # Optional read-only tools for existing agents
│   ├── scheduling/            # Resource placement and simulation-origin plans
│   ├── cli/                   # Local command interface
│   └── web/                   # Loopback API and bundled browser assets
└── tests/                     # Offline tests and synthetic fixtures
```

The package includes fake AI connections, a SQLite controller, synthetic replay, acceptance/policy helpers, private host profiles, bounded SSH probes, optional MCP queries, CLI and bundled web assets. The [local console guide](docs/local-console.md) distinguishes these implementations from later job monitoring. The [foundation decision](docs/decisions/0001-project-foundation.md) records the original design choices.

## Start here

With Python 3.11+ on macOS or Linux:

```bash
git clone https://github.com/joshuazeng713/spiceowl.git
cd spiceowl
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/spiceowl serve --demo
```

Open the printed loopback URL. Explore Connections, inspect a synthetic run, advance its observations and request a demo diagnosis. No model, simulator, Node.js or credentials are needed. Repeating `--demo` preserves stored history. The [guide](docs/local-console.md) covers CLI commands, persistence, limitations and tests.

To add a real connection, import an operator-authored host profile using `spiceowl hosts add --file PROFILE.json`, then explicitly run `spiceowl hosts probe HOST_ID` or use **Simulation hosts → Check connection**. OpenSSH uses your existing alias and jump-host configuration. Version queries do not establish license availability, job state, or simulation validity.

To expose stored observations to Codex or another MCP client, install `.[mcp]` and configure the client to run `spiceowl --data-dir ABSOLUTE_STATE_DIR mcp`. This optional server uses the official Python MCP SDK and reads the same local database without owning the controller. It cannot launch simulations or query arbitrary paths. Follow the [real connection guide](docs/real-connections.md) for the profile schema, commands and verification boundaries.

For multiple nodes, register an explicit hostname list with `hosts import-pool`, run `hosts scan`, then evaluate requested CPUs and memory in **Simulation hosts**. Unknown or stale nodes are excluded. Placement retains a resource margin and is a proposal, not a reservation. Standalone plans select a candidate for a new exported netlist; Virtuoso-managed plans preserve the original host/native run mapping.

For real text answers, register a private native runtime profile using `agents add --file PROFILE.json`, then open **Agent chat**. This uses the runtime's existing provider/auth configuration, which may be a subscription or a separately configured API route. Requests have durable keys, no automatic provider fallback, and no simulator tools. See the [native Q&A guide](docs/native-agent.md).

## Contributing

We welcome analog designers, CAD engineers, and developers working on reliable scientific computing. Useful first contributions include sanitized failure cases, measurement validators, and small implementations with failure-injection tests.

Read [CONTRIBUTING.md](CONTRIBUTING.md) before sharing data. Use [SECURITY.md](SECURITY.md) for reporting vulnerabilities and handling sensitive materials. Background references and candidate dependencies are listed in [docs/references.md](docs/references.md). The core uses Python's standard library; the optional MCP extra pins the tested official SDK version.

## License

[MIT](LICENSE), as selected for this repository. Cadence tools, PDKs, and third-party components are not distributed by this project. Any future third-party code retains its applicable license and notices.

# Examples

All examples are design artifacts, not executable configurations. The three connection/supervision examples are explicitly disabled and contain no working credentials or environment grants.

| Example | Purpose | Design |
| --- | --- | --- |
| [connections.example.json](connections.example.json) | Fake agent/API, native runtime, API-key reference and ACP profile shapes | [AI connections](../docs/design/ai-connections.md) |
| [execution-profiles.example.json](execution-profiles.example.json) | Mock, Linux, scheduler, ADE and simulator profiles | [Simulator connections](../docs/design/simulator-connections.md) |
| [debug-policy.example.json](debug-policy.example.json) | Observe-only failure and validation policy | [Simulation debugging](../docs/design/simulation-debug.md) |
| [run-manifest.example.json](run-manifest.example.json) | Frozen synthetic experiment intent | [Run contract](../docs/run-contract.md) |

[run-manifest.example.json](run-manifest.example.json) is an illustrative `0.1-draft` manifest for a synthetic RC settling experiment. It is valid JSON, but there is no schema validator, simulator command, input netlist, or executable example yet.

The `synthetic:` input reference and `proposed:` recipe deliberately identify design placeholders. Do not replace them with private paths or licensed models in a public contribution. Runtime input resolution and checks belong to the implementation milestones.

The analytical RC time constant is 1 microsecond; a first-order ideal RC response reaches a 1% band after approximately 4.605 microseconds from the step. This is a planning sanity check, not a simulator result or an implemented validator. The example observes the response from a 1 microsecond step until a 10 microsecond stop time.

See the [run contract](../docs/run-contract.md) for field meanings and the [roadmap](../docs/roadmap.md) for the first executable mock demo.

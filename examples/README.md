# Examples

[run-manifest.example.json](run-manifest.example.json) is an illustrative `0.1-draft` manifest for a synthetic RC settling experiment. It is valid JSON, but there is no schema validator, simulator command, input netlist, or executable example yet.

The `synthetic:` input reference and `proposed:` recipe deliberately identify design placeholders. Do not replace them with private paths or licensed models in a public contribution. Runtime input resolution and checks belong to the implementation milestones.

The analytical RC time constant is 1 microsecond; a first-order ideal RC response reaches a 1% band after approximately 4.605 microseconds from the step. This is a planning sanity check, not a simulator result or an implemented validator. The example observes the response from a 1 microsecond step until a 10 microsecond stop time.

See the [run contract](../docs/run-contract.md) for field meanings and the [roadmap](../docs/roadmap.md) for the first executable mock demo.

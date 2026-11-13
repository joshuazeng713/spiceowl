# Run contract

Status: design draft `0.1-draft`. The [JSON example](../examples/run-manifest.example.json) illustrates the vocabulary; it is not a validated schema or an executable configuration. The example uses a synthetic circuit identity and cannot submit a job.

## Why freeze intent

A simulator exit code cannot establish that the requested experiment was performed. Capture intent before execution, then compare it with resolved and actual inputs and validate the data against explicit requirements.

Keep three identities separate:

- **Experiment ID:** the intended scientific question and its accepted requirements.
- **Attempt ID:** one execution under that experiment, including recovery attempts.
- **Submission key:** the stable identity of one external submission operation. Reuse it only when reconciling/retrying that same operation; a genuinely new attempt gets a new key after the prior outcome is resolved.

## Proposed manifest sections

| Section | Purpose |
| --- | --- |
| `schema_version` | Draft vocabulary identifier; migration is required before incompatible changes |
| `example_only` | Explicit marker that the included example cannot execute |
| `experiment` | Identity, objective, input reference, parameter/corner/sample intent |
| `analysis` | Analysis kind and explicitly unit-bearing settings |
| `outputs` | Required signal identities and required result artifacts |
| `acceptance` | Input constraints, measurement preconditions, and design targets |
| `execution` | Selected executor/simulator profile, resource budgets, and checkpoint request |
| `recovery` | Allowed actions, forbidden automatic changes, and retry limits |

Use explicit units rather than simulator-specific shorthand in the domain representation. Preserve both user-entered values and backend-normalized values when translating them.

## Input capture before submission

The example intentionally does not invent content hashes. A real submission must resolve and store:

- Circuit and testbench source identity, or a reproducible exported input identity.
- Effective netlist and transitive include/model references, with accessible content hashes or a documented immutable vendor identity where direct hashing is unavailable.
- Selected model sections, parameters, PVT conditions, stimuli, analysis options, and output-save settings.
- Simulator and adapter versions, execution profile, relevant environment configuration, and measurement-code identity.
- Random seed and Monte Carlo sample identity where applicable.

A source Git commit alone is not a complete input fingerprint when a run depends on external model files. Do not copy proprietary dependencies into a public repository to achieve provenance; retain access-controlled references and permitted identity metadata.

## Validation and specification are distinct

Input and measurement validation answers whether the experiment can be interpreted. Specification checks answer whether the circuit meets a design target.

For the illustrative settling experiment:

1. The actual analysis and stop time must match the accepted configuration.
2. The required output signal must exist under the resolved backend mapping.
3. The waveform must cover the measurement window and be parseable.
4. The settling-time recipe must verify its declared preconditions and record any uncertainty.
5. Only a valid measurement is compared against the settling-time target.

The illustrative measurement recipe is not implemented yet. Missing data must produce `invalid` or `indeterminate`, not a fabricated value or a specification pass. A measured target violation produces a valid measurement with a specification failure.

## Observations

Each event should contain an event ID, experiment/attempt identity, observation time, evidence source, observation freshness, payload, and adapter version. Distinguish wall-clock time from simulated time. Plan for duplicate delivery, out-of-order observations, log rotation, and partial files.

The [synthetic disconnect scenario](../tests/fixtures/synthetic/disconnected-running.json) demonstrates that a missing connection does not establish execution failure.

## Execution and action receipts

Persist the execution owner, job/process identity, submission key, dispatch/acknowledgment times, and input fingerprint. Action receipts additionally record the action key, target attempt, policy version, decision/approval evidence, proposed diff, observed outcome, and recovery lineage.

Cancellation is a request until the executor confirms the terminal state. Collection completion is separate from solver completion. Record incomplete retrieval without relabeling a finished solver run as scientifically valid.

## Limits and changes

- Reconnection consumes no new simulation attempt.
- `max_retries` counts new execution attempts after the initial attempt; a value of zero disables automatic resubmission.
- Execution resources are bounded per attempt; an experiment-level wall-time budget bounds the overall campaign.
- A larger-memory retry needs an explicitly allowed resource profile; it is not live memory migration.
- A changed stimulus, corner, seed, measurement, or electrical design may define a new experiment. Preserve the parent link and the reason for the change.
- A numerical-setting change requires an allowed policy or explicit decision and a new validation record. It is never silently equivalent to the original run.
- Native checkpoint compatibility must be attested by the selected simulator adapter. A workflow checkpoint or raw waveform file is insufficient.

## Runtime storage proposal

Keep runtime state outside the source tree in deployment. For local development, use the ignored `/runs/` directory:

```text
runs/<experiment-id>/
├── experiment.json
├── events.jsonl
└── attempts/<attempt-id>/
    ├── attempt.json
    ├── input-manifest.json
    ├── submission-receipt.json
    ├── actions/
    ├── evidence/
    ├── validation.json
    └── artifacts.json
```

Names above are proposed record locations, not files written by current code. Store large native outputs separately and reference them from `artifacts.json` with integrity and retention metadata.

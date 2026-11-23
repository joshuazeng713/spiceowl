# Guided simulation onboarding

The paired CircuitGit integration exposes fresh intake in the normal local
workbench and through twelve finite MCP operations. Each intake owns its source,
new observations, choices and original submission identities. An existing project
or site profile does not supply missing configuration to a new intake.

SpiceOwl provides two pure entrypoints:

```python
from spiceowl.onboarding.recommendations import suggest_intent, recommend_onboarding

intent = suggest_intent(requirements)
draft = recommend_onboarding(discovery, requirements, remote_platform)
```

Both consume supplied, bound evidence without filesystem or network access.
Suggested fields carry their value, basis, confidence and explicit missing items.
CircuitGit maps private candidate identities to the current intake's public IDs,
omits private paths, and returns a `recommended_selection` and `next_actions`.

## Recommendation rules

- A source-backed four-terminal wrapper, including supported explicit SPICE
  bins, can supply polarity and terminal order. Names alone cannot verify them.
- Complete include entrypoints rank above definition fragments. Typical-corner
  names and observed `pre_simu` sections are stated conventions. An added prelude
  still requires a new audit of the final section combination.
- Linux `x86_64` plus newly read distribution ID/ID_LIKE can recommend the
  observed `RH_64` or `SUSE_64` library. Local machine architecture is irrelevant.
  A matching directory name is not a binary-load test.
- Unique input/output/rail names are checked against connectivity. Verified
  device contracts provide an additional topology check.
- Explicit, applicable nominal values take precedence. In their absence the
  `inverter_smoke` preset suggests 0.9 V, 1 GHz, 27 °C operating and nominal
  temperature, five cycles, 2 fF load, 10 ps transitions and 1000 points/period.
  These are visible experiment defaults, not discovered intent or foundry ratings.
  Missing, conflicting or incompatible evidence is returned separately.
- Compatible top-ranked drafts may use a transparent `policy_default` ordering:
  observed prelude, smaller dependency closure, basename, then candidate ID.
  Alternatives remain visible and `equivalence_not_proven` remains true.
  Conflicting device contracts or voltage domains do not become equivalent.

Accepting a draft is a configuration choice. The adopted model contracts and all
test conditions are returned in the prepared selection, including user overrides.
The input origin distinguishes native agent, interactive client and test harness;
it never proves that a human clicked a button.

## Agent control

The MCP operations have the `ic_onboarding_` prefix, followed by `netlist`,
`ssh_hosts`, `session`, `probe`, `discover_pdk`, `prepare`, `run`, `submission`,
`inspect`, `waveforms_catalog`, `waveforms_mapping`, or `waveforms_window`.
The paired gateway's generated catalog is authoritative. Tools have finite
schemas and do not accept arbitrary commands, URLs, credentials or old profiles.

Every session returns actions with an exact tool name, arguments, readiness and
missing fields. A normal controller can follow the same sequence without an LLM.
After uncertainty it receives original-submission lookup guidance, not a new run.
The client journals a run before sending; the existing Runner remains the only
execution ledger. A generic timeout or HTTP error never authorizes replay.

Synthetic integration tests exercise the real intake, compiler, local Git
snapshot and Runner reservation with remote boundaries mocked. They do not prove
a real solver ran. A real site's operating qualification, dependencies and
measurement results retain their independent verdicts.

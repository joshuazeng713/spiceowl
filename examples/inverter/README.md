# Original toy CMOS inverter smoke test

This is a self-contained **Spectre** netlist, not a fabricated waveform fixture. A real run requires a separately installed licensed simulator and explicit authorization to use the chosen execution environment. The two original `mos1` model cards are illustrative level-1 devices: their parameters are not fitted to any fabrication process. No PDK, vendor transistor model or private circuit is included. A successful run validates a small execution and result-collection path; it does not qualify a technology, design kit, production inverter or long-running recovery system.

## Frozen circuit and stimulus

| Item | Value |
| --- | --- |
| Supply and temperature | 1.2 V, 27 °C |
| NMOS geometry and model | W/L = 1 µm / 1 µm; VTO = 0.4 V; KP = 120 µA/V² |
| PMOS geometry and model | W/L = 2 µm / 1 µm; VTO = −0.4 V; KP = 60 µA/V² |
| Body connections | NMOS to ground; PMOS to VDD |
| Explicit output load | 20 fF |
| Input | 0–1.2 V pulse; 5 ns initial delay, 10 ns period, 5 ns width, 100 ps rise/fall |
| Analysis | Transient from 0 to 50 ns; 10 ps strobe period; strobe-only saved points |
| Saved signals | `vin`, `vout`, `vdd`, with the transient time axis |
| Planned execution budget | One CPU, 1 GiB planning memory, 4 GiB address-space limit, 120 s attempt time, 60 s license wait; no automatic retry |

The run has approximately 5,001 saved time points. Strobing constrains waveform sampling; it is not a guarantee about solver memory consumption or internal step count. The 1 GiB memory value is a planning request, not an enforced resident-memory (RSS) cap. The separate 4 GiB address-space limit corresponds to the runner's `RLIMIT_AS` and does not mean 4 GiB of physical memory is reserved. The runner must record actual limit enforcement, selected simulator version, full argument vector, source hashes, ownership, completion and collection receipts.

The entry point is [tb.scs](tb.scs). It includes only [models.scs](models.scs) and [inverter.scs](inverter.scs), using relative paths. The developer runner freezes all three files byte-for-byte with `acceptance.json`, expands exactly those two includes into one `input.scs`, and records both source hashes and the flattened-input hash. It accepts no general include tree or PDK dependency. Ask the installed simulator's `spectre -h mos1`, `spectre -h vsource` and `spectre -h tran` help to confirm its syntax and capabilities before launch; the netlist's authoring alone is not a parser or solver verification.

For collection, select the installed simulator's documented **PSF ASCII** output mode and save the complete simulator log. An external runner owns execution: this example is not a new SpiceOwl arbitrary-command endpoint or automatic submission path. Save actual native waveforms alongside exported CSV and plots, so derived data remains traceable to the attempt.

## Explicit developer runner

Use [run_inverter_smoke.py](../../scripts/run_inverter_smoke.py) from the repository root with local Python 3.11+. Its [transport helper](../../scripts/inverter_smoke_transport.py) requires an authorized Linux host with GNU `/usr/bin/timeout`, licensed Spectre at the registered path, and an explicitly selected working Python 3.6+ interpreter. The registered host profile supplies SSH routing and the expected host/user identity. Existing host-key trust must already be established.

Replace `HOST_ID` and `PYTHON_ABSOLUTE_PATH` with the registered host ID and the remote interpreter's absolute path. `--hosts` can name multiple explicitly selected registered candidates; preparation chooses an eligible host from a fresh bounded scan. Run these steps individually, checking each receipt before continuing:

```bash
PYTHONPATH=src .venv/bin/python scripts/run_inverter_smoke.py prepare --data-dir .spiceowl --hosts HOST_ID --run-key inverter-smoke-001 --remote-python PYTHON_ABSOLUTE_PATH
PYTHONPATH=src .venv/bin/python scripts/run_inverter_smoke.py stage --run-dir .spiceowl/smoke/inverter-smoke-001
PYTHONPATH=src .venv/bin/python scripts/run_inverter_smoke.py launch --run-dir .spiceowl/smoke/inverter-smoke-001
PYTHONPATH=src .venv/bin/python scripts/run_inverter_smoke.py status --run-dir .spiceowl/smoke/inverter-smoke-001
PYTHONPATH=src .venv/bin/python scripts/run_inverter_smoke.py collect --run-dir .spiceowl/smoke/inverter-smoke-001
```

When the remote launch environment needs an explicit license-server or license-file setting, append `--license-env-json PRIVATE_LICENSE_JSON_PATH` to the **prepare** command. The argument is optional; omitting it keeps the inherited remote license environment. The JSON object accepts only `CDS_LIC_FILE` and/or `LM_LICENSE_FILE`, with explicit values rather than shell commands or environment-file sourcing. For example, this fictional configuration shows the shape only:

```json
{
  "CDS_LIC_FILE": "5280@license.example.invalid"
}
```

Use the site's authorized configuration in a private local JSON file; the fictional hostname above is not a working license service. Selected values are frozen in the private transport manifest and passed to the solver child process. Keep that JSON, plan, manifest and real receipts out of public source or shared artifacts. A successful `spectre -W` check establishes version evidence, not license readiness: the subsequent solver can still fail because its license setting is missing, unreachable or unavailable. Changing license settings after a terminal failure requires a new prepared attempt; preserve the original failed attempt and its evidence.

| Step | Effect |
| --- | --- |
| `prepare` | Actively checks selected hosts, screens for one CPU and 1 GiB available planning headroom, and freezes the selected profile, exact version, source/acceptance hashes and flattened input locally. It makes no reservation and dispatches no simulation. |
| `stage` | Verifies remote identity and writes only frozen files to this run's isolated private home subdirectory. Repeating the same stage never replaces conflicting or partially staged files. |
| `launch` | Rechecks the selected host's resource headroom, verifies identity and exact Spectre version, creates a permanent atomic dispatch claim, then starts a detached bounded wrapper. |
| `status` | Reads that same run's receipts and PID/start identities. It does not launch, cancel or migrate the task. |
| `collect` | Requires a terminal receipt, verifies file and input hashes, saves bounded native PSF ASCII/log artifacts, then runs the separate waveform checks and exports CSV when parsing succeeds. |

The wrapper requests one simulator thread and enforces one-CPU affinity, a 4 GiB `RLIMIT_AS`, a CPU-time cap, and a 120-second wall-time budget: TERM after 115 seconds with at most five seconds of kill grace. License queuing is limited to 60 seconds within the attempt. PID records distinguish the detached wrapper, timeout supervisor and solver launcher; a launcher script's PID need not be the final Spectre binary PID. A forced-kill exit is not automatically labelled OOM.

The saved plan belongs in `.spiceowl/smoke/inverter-smoke-001/`; preserve its `source/`, `plan.json`, `provenance.json` and operation receipts. Keep this directory private. Preparation is a one-time operation for a run key. After `outcome_unknown`, use `status` with the same directory and do not create another key or blindly replay launch. Collect only after a confirmed terminal outcome; an incomplete or failed run remains available for diagnosis. Source, acceptance or helper changes can invalidate the frozen bundle, so retain the original checkout revision through collection.

For optional PNG/SVG plots, use `collect --plots` after installing matplotlib separately in the local analysis environment. JSON and CSV use the standard library. The [result utility](../../scripts/inverter_smoke_results.py) distinguishes execution evidence, measurement validity and fixed toy-inverter behavior; having files or a zero exit code alone is not a scientific pass.

This source-checkout workflow is separate from the product console. Web runs remain synthetic, and the `spiceowl` CLI/web/MCP interfaces do not offer general simulation submission or unattended recovery. A successful real smoke qualifies only this small original example and its recorded environment.

## Acceptance fixed before simulation

[acceptance.json](acceptance.json) freezes the three netlist SHA-256 digests, physical values, signal units, data requirements, settled windows and reporting rules. The attempt manifest must also hash `acceptance.json` itself; that self-hash cannot be embedded inside the file. Do not change this source copy or the frozen attempt copy after the attempt starts.

Report three separate outcomes:

1. **Execution:** the real simulator's completion and exit evidence.
2. **Measurement validity:** source identity, actual completion, required finite data, strictly increasing time, adequate coverage/sampling, and input stimulus checks. Absent data, duplicate time, incomplete coverage or a wrong input stimulus cannot produce a valid measurement.
3. **Specification:** only after valid measurement, require complementary settled output levels in the fixed windows. All samples in each window must pass. A valid waveform that violates the output thresholds is a specification failure; preserve it.

The low band is −0.02 to 0.12 V and the high band is 1.08 to 1.22 V. At 2–4, 12–14, 22–24, 32–34 and 42–44 ns, the input must be low and output high. At 7–9, 17–19, 27–29, 37–39 and 47–49 ns, the input must be high and output low. Windows are inclusive and fixed independently of observed crossings. Insufficient samples make a window unusable, not passed.

Propagation delays are **report-only** diagnostics at 0.6 V with linear interpolation, using the manifest's fixed input transition windows and a 2 ns pairing window. Missing or ambiguous input/output crossing pairs remain unavailable with warnings. Do not fill them with zero, choose a convenient crossing, or claim a fabrication-grade timing specification. These delay diagnostics do not turn a failed settled-output test into a pass and are not required for a settled-logic pass.

If a compatibility correction is needed before a successful launch, prepare a new reviewed source/manifest revision. If a completed attempt fails, retain its inputs, outputs and original criteria. Any model, source, stimulus, numerical-option or criterion change belongs to a separate manifest and attempt.

## Syntax references

The model parameter values and circuit in this directory are original. These primary Cadence references were used only to check simulator vocabulary:

- [Cadence expert `mos1` model and MOS instance syntax example](https://community.cadence.com/cadence_technology_forums/f/rf-design/21507/working-with-dc-sweeps-and-op).
- [Cadence Spectre strobe feature explanation, 28 April 2022](https://community.cadence.com/cadence_blogs_8/b/cic/posts/spectre-tech-tips-using-the-spectre-strobe-feature).
- [Cadence community pulse-source syntax discussion](https://community.cadence.com/cadence_technology_forums/f/custom-ic-design/47642/cannot-plot-transient-response-no-graph-empty-window/1373039).

These references do not establish that the exact installed runtime has executed this example. Record that separately from a real attempt. Repository MIT licensing applies to the original example; the simulator remains separately licensed.

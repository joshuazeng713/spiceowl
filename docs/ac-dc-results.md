# Native AC/DC results and explicit measurements

This package parses one selected Spectre PSF ASCII artifact and measures explicit
AC or DC inputs. It does not fetch remote files, run Spectre, infer a loop break,
identify a transistor's private model semantics or qualify a design. CircuitGit
owns source/run/archive identity, dataset selection, review and tool routing.

## Dataset API

```python
from spiceowl.integrations.spectre_results import parse_psf_analysis

dataset = parse_psf_analysis(
    raw_bytes, analysis_kind="ac", analysis_name="gain"
)
```

`analysis_kind` is required and is `ac`, `dc` or `dc_op`. An optional expected
`analysis_name` must match the native header. Wrong or ambiguous analysis
identity is rejected. `dc_op` requires native `dcOp` or `info` with
`AnalysisType="dcOp"`; a filename alone is not evidence of analysis kind.

The `spiceowl.spectre-dataset.v1` result includes:

- `raw_sha256`: digest of the exact supplied UTF-8 bytes, including line endings.
- `analysis`: kind, native name/type, observed `AnalysisName`/`AnalysisType` as
  `producer_name`/`producer_type`, and simulator/version strings. An info file's
  name and its producing DC analysis can differ; neither field overwrites the
  other, and the caller must verify the producing analysis against its run.
- `axis`: native name, native unit and values, or null for an unswept DC point.
- `signals`: exact native names and units. Complex signals have aligned `real`
  and `imag` arrays; scalar signals have `values`.
- `operating_points`: device name, native structure type, optional reported model
  name and a map of typed `{value, unit, status, value_kind}` parameters.
- Sample count, warnings and whether any native values were undefined.

Supported records include complex AC traces, native `GROUP` trace sequences,
single-axis increasing or decreasing DC sweeps, unswept scalar DC points and
bounded `dcOpInfo` structures. Scalar types include `FLOAT DOUBLE`, `COMPLEX
DOUBLE`, `INT BYTE/SHORT/LONG` and native strings in unswept records. Operating
point fields retain the declared positional order, names and units. Missing
units are not filled from a parameter name. Native NaN/Inf values become null
with `status="undefined"`; they are not valid zero values. Nonfinite sweep axes
are rejected.

Limits are 16 MiB of input, 2 MiB of metadata, 256 types, 1024 fields per
structure, 512 swept traces, 4096 unswept records, 200,000 sweep samples and
2,000,000 scalar value slots. The implementation consumes value tokens lazily.
Multiple sweep dimensions, nested operating-point structures, unknown types,
binary PSF, PSFXL and unrecognized analysis types fail explicitly. It is not a
complete implementation of every Cadence output format. The original
`parse_psf_ascii` time-only API is unchanged.

## AC measurements

```python
from spiceowl.adctools import ac_metrics

report = ac_metrics(
    frequency_hz, output_real, output_imag,
    backend="spiceowl", response_kind="transfer", response_unit="V",
    reference_real=input_real, reference_imag=input_imag, reference_unit="V",
    return_curves=True,
)
```

`response_unit` is mandatory. A reference requires both components and its unit,
aligned to the exact same frequency points. The algorithm divides complex
output by complex reference; a zero reference is an error. It never assumes
1 Vac. Equal units yield dimensionless gain. Without a reference, a response
explicitly supplied in unit `1` is already dimensionless. Unreferenced V/A data
and cross-unit ratios such as V/A still produce magnitude/phase curves, with
labels such as `dB re 1 V` or `dB re 1 V/A`; dimensionless gain and margin metrics
remain undefined. The caller must establish that a declared dimensionless input
really is the requested transfer or loop response.

For margins, use `response_kind="loop_gain"` with an explicit
`feedback_convention="denominator_1_plus_L"` or `"denominator_1_minus_T"` and
`phase_reference_deg`. The latter sign convention is converted to `L=-T`.
`phase_reference_deg` anchors the initial unwrapped curve branch; it is not an
automatic phase fit. A halfway branch ambiguity is rejected. Adding a full turn
to this display anchor cannot change the physical phase margin. A raw V/A response
cannot be used as loop gain. Generic amplifier voltage gain is not sufficient
to establish loop gain; the actual feedback factor and measurement topology are
part of the experiment specification.

The result preserves all observed unity-gain and negative-real-axis phase
events with their sample brackets and `classification`: `crossing`, `touch`,
`boundary` or `plateau`. An isolated exact hit counts as a crossing only when its
two neighbors lie on opposite sides. A tangent, boundary hit or plateau is
evidence but cannot provide a scalar bandwidth or margin. Between positive frequencies it
interpolates gain in dB and unwrapped phase linearly versus log frequency. An
interval starting at zero uses linear frequency. It does not extrapolate. Phase
unwrapping assumes less than 180 degrees of actual phase change between adjacent
samples; sparse data can violate that assumption without revealing it.

| Metric | Definition and limitation |
| --- | --- |
| `dc_gain_db` | Dimensionless magnitude at an actual zero-Hz point; otherwise undefined |
| `low_frequency_gain_db` | Dimensionless gain at the first measured frequency, never renamed DC gain |
| `low_frequency_magnitude` | First measured magnitude, with the effective response unit |
| `low_frequency_hz` | Frequency of that first sample |
| `unity_gain_bandwidth_hz` | One uniquely observed 0-dB crossing; absent/multiple/plateau crossings are undefined |
| `phase_margin_deg` | Signed angular distance from the corresponding negative-real-axis branch at one bracketed unity crossing, normalized to (−180, 180] degrees; full-turn anchor changes leave it unchanged |
| `gain_margin_db` | Negative gain in dB at a unique negative-real-axis crossing when the unity crossing is also unique |

`curves` contains `frequency_hz`, `magnitude_db`, `phase_deg` and explicit units.
Missing crossings do not become infinite margins. All crossings remain visible,
but multiple crossings do not collapse into a favorable scalar. The operator
does not infer a single-pole gain-bandwidth product: `gbw_product_hz` is null.
UGBW and a chosen gain-bandwidth product are not interchangeable definitions.
`stability_verdict` remains `not_evaluated`: sampled margins do not count
right-half-plane poles or perform a complete Nyquist stability analysis.

## DC operating-point checks

```python
from spiceowl.adctools import dc_op_metrics

report = dc_op_metrics(
    dataset["operating_points"], backend="spiceowl",
    required_devices=["N1"],
    device_contracts={"N1": {
        "polarity": "nmos", "saturation_method": "region",
        "region_map": {"2": "saturation", "1": "linear"},
        "region_map_source": "explicit synthetic model convention"
    }},
    supplies=[{"name": "VDD", "voltage_v": 1.2, "current_a": 0.001,
               "current_sign": "into_dut"}],
)
```

This region mapping is a synthetic example, not a universal Spectre/PDK mapping.
For actual models, consult their installed model documentation and retain the
evidence source in the contract. `region` method requires an explicit code map
to `saturation`, `linear`, `cutoff`, `subthreshold`, `breakdown` or `unknown`.
Absent, nonfinite, dimensionally invalid or unmapped codes yield unknown.

The alternative `saturation_method="vdsat"` requires:

- `polarity`: `nmos` or `pmos`.
- `voltage_convention`: `signed_vgs_vds` or `polarity_normalized`. The signed
  convention applies the PMOS sign to Vgs, Vds and Vth; the normalized convention
  requires already oriented values from a documented source.
- `vdsat_convention`: `signed` or nonnegative `magnitude`, independently of the
  other voltage convention. Signed PMOS Vdsat receives the PMOS sign even when
  Vgs/Vds/Vth have already been polarity-normalized.
- `require_above_threshold`: an explicit boolean. Weak-inversion operation
  must not be rejected by an implicit strong-inversion assumption.
- Optional nonnegative `minimum_headroom_v`, default zero.

The voltage method compares oriented Vds−Vdsat against the declared headroom.
When requested, it also requires oriented Vgs > Vth. Reverse or inconsistent
orientation and missing/wrong-unit operands return unknown. This checks a
declared voltage criterion; it is not a derivation of the compact model's region
classification. Vdsat is not silently replaced with Vgs−Vth.

Conditions for another method are rejected. In particular, a region-code check
cannot accept and then ignore an above-threshold or minimum-headroom condition;
a voltage check cannot silently ignore a supplied region map.

Reports retain Vgs, Vds, Vth, Vdsat, Id, gm, gds and the original region field.
`gm_over_id` and `intrinsic_gain` use absolute gm/Id and gm/gds, with undefined
zero-denominator results. The selected-device check returns pass/fail/unknown
independently from the measurement status: a measured nonsaturated device is a
valid measurement with a failed declared check. Missing critical evidence makes
the analysis invalid.

Static power sums only explicitly selected supply rails using voltage, current
and `into_dut`/`from_dut` direction. It never sums arbitrary transistor dissipation
or infers current signs. A CircuitGit adapter must bind every scalar to the
original dataset, rather than allowing caller values to masquerade as measured
power. No rail input means undefined power; not zero power. The result cannot
assert that an omitted rail consumes nothing.

## Verification and primary references

The [serialized fixture](contracts/ac-dc-analysis.example.json) contains original
synthetic PSF-format text, actual parser output and actual numerical results.
It is generated with `PYTHONPATH=src .venv/bin/python tests/test_ac_dc.py
--write-examples`. Tests compare one- and three-pole responses against independent
closed-form crossings, verify complex reference division, multiple crossings,
PMOS signs, explicit model-region maps, power signs and malformed native data.

On 2026-11-24, the reader was also exercised against the public `fracpole.ac`,
`fracpole.dc`, `dcswp.dc` and `dcOpInfo.info.psfascii` representative outputs in
[Ken Kundert's psf_utils sample inventory](https://github.com/KenKundert/psf_utils/tree/master/samples).
Those files and the upstream parser were not copied into this repository. This
format observation is not a substitute for validating the target simulator's
collected output in the paired runtime.

- [Texas Instruments: Understanding Operational Amplifier Specifications, section 5.16](https://www.ti.com/lit/an/sloa011b/sloa011b.pdf) distinguishes unity-gain frequency, gain-bandwidth product and margins.
- [Analog Devices: Loop Gain and its Effect on Analog Control Systems](https://www.analog.com/en/resources/technical-articles/loop-gain-and-its-effect-on-analog-control-systems.html) explains the feedback loop's role in margin interpretation.
- [Cadence: How the region operating point is determined](https://community.cadence.com/cadence_technology_forums/f/rf-design/43519/how-the-region-operating-point-is-determined-by-spectre-ade/1365549) points to the selected compact model's device-region definitions.
- [Cadence: MOS DC Operating Point](https://community.cadence.com/cadence_technology_forums/f/custom-ic-design/35574/mos-dc-operating-point/1346391) explains why saturation-related values depend on the chosen model.

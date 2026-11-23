# Offline ADC and transient analysis

`spiceowl.adctools` measures explicitly supplied arrays. It supports Python and a JSON/CSV CLI for an agent or CI runner. It does not discover test intent, generate stimuli, start a simulator, resample a waveform, infer ADC bit weights, or declare that a circuit meets its specifications. Dataset selection, simulator-run identity, and scientific acceptance criteria belong to the calling experiment layer.

Install the optional numerical dependencies:

```bash
python -m pip install '.[adctools]'
```

The core SpiceOwl installation remains independent of these dependencies. NumPy powers the independent implementations; their four-parameter fit also uses SciPy. `backend="adctoolbox"` invokes the actual pinned `adctoolbox==0.9.3` package. Every public operation requires an explicit backend. A missing or different upstream version is an error; no backend fallback is performed.

## Operations and backend choices

| Operation | `spiceowl` | `adctoolbox` |
| --- | --- | --- |
| `spec_analyze` | Explicit tone, window, band and bin masks; one-sided power FFT | Actual `spectrum.compute_spectrum.compute_spectrum`; upstream peak detection must match the declared tone |
| `coherent_fft` | Integer-cycle check and rectangular FFT | Integer-cycle check and upstream rectangular FFT |
| `sine_fit3` | Known-frequency linear sine/cosine/DC fit | Actual `fit_sine_4param`, explicit frequency, `max_iterations=0` |
| `sine_fit4` | Bounded frequency fit with variable projection | Actual `fit_sine_4param`, explicit estimate, `max_iterations=100`, final interval check |
| `weight_calibrate` | Supervised linear least squares against a supplied reference | Actual `calibrate_weight_sine`, explicit frequency and nominal weights, no frequency search |
| `reconstruct_codes` | Explicit decisions, weights and offset | Unsupported |
| `histogram_linearity` | Explicit uniform/sine input CDF inversion | Unsupported |
| `tran_metrics` | Finite-record step, slew, delay and V/I power metrics | Unsupported |

Import a named function or use the bounded dispatcher:

```python
from spiceowl.adctools import analyze, spec_analyze

report = analyze("sine_fit3", {
    "backend": "spiceowl",
    "samples": output_samples,
    "sample_rate_hz": 1e6,
    "frequency_hz": 15625,
    "signal_unit": "V",
})
```

Inputs must be finite, real, correctly aligned, and explicitly named. Array input is bounded to 1,048,576 samples; calibration accepts at most 64 decision columns. Boolean, complex and string sample arrays are rejected. Malformed operation arguments raise `AnalysisError`; unresolved scientific estimates return an invalid result where possible. A caller must handle both.

## Result and units

Every successful call produces `spiceowl.adctools.result.v1`:

```json
{
  "schema": "spiceowl.adctools.result.v1",
  "operation": "sine_fit3",
  "algorithm_version": "1.0.0",
  "backend": "spiceowl",
  "backend_version": "1.0.0",
  "numerical_environment": {
    "python": "3.12.12", "numpy": "2.5.5", "scipy": "1.18.1", "adctoolbox": "0.9.3"
  },
  "status": "valid",
  "parameters": {},
  "metrics": {
    "amplitude_peak": {"value": 0.7, "unit": "V", "status": "valid"},
    "phase_rad": {"value": null, "unit": "rad", "status": "undefined"}
  },
  "warnings": [],
  "specification_result": "not_evaluated"
}
```

This fragment illustrates the shape rather than a particular fit. Results never contain JSON NaN or Infinity: unavailable ratios, unobserved settling and other undefined metrics are `null` with metric status `undefined`. Top-level `valid` means the operation's declared inputs were usable; it does not mean every metric is defined or every scientific assumption has been independently verified. In particular, finite histogram counts do not verify the stimulus distribution. A consumer must check top-level status **and** the requested metric's status and exact unit before comparing it with a frozen criterion. Undefined or invalid measurements cannot pass a criterion.

`numerical_environment` records the actual running Python version and installed NumPy, SciPy and ADCToolbox distribution versions for each result. A missing distribution is explicitly null. Reading this metadata does not import optional numerical packages. A recorded version does not imply that an operation used that package; the backend and API provenance identify the algorithm. Version recording helps reproduce an environment but does not promise bit-identical results across operating systems, processor architectures or BLAS/LAPACK builds. The installer permits supported NumPy/SciPy ranges; freeze exact dependencies, platform and numerical-library builds in the experiment's own lockfile or environment image when exact environment recreation matters.

| Metric family | Units |
| --- | --- |
| `sndr_db`, `snr_db`, `sfdr_db`, `thd_db`, `hd2_db` … `hd9_db` | `dB` with the stated ratio convention |
| `enob_bits`, `fit_residual_enob_bits` | `bit` |
| `signal_dbfs`, upstream `noise_floor_dbfs` | `dBFS` |
| `noise_floor_dbfs_per_hz` | `dBFS/Hz` |
| RMS/amplitude/offset and supervised fit errors | Supplied signal/reference unit |
| `frequency_hz`, `phase_rad` | `Hz`, `rad` |
| DNL/INL extrema | `LSB` |
| Settling, delay, transition | `s` |
| Slew, overshoot, undershoot | Signal unit per second, `%` |
| Average/peak absorbed power, energy | `W`, `J` |

Spectrum and histogram arrays are omitted by default. Request `return_spectrum=True` or `return_details=True` when needed. A spectrum exposes `frequency_hz`, `power_db`, `frequency_unit` and `power_unit`; zero spectral bins can be null in the logarithmic array. The independent backend also returns `power_per_bin` and `psd`. The upstream backend leaves those two fields null rather than presenting a different normalization as equivalent. Compute with the full record; any chart reduction happens after measurement.

## Independent spectral analysis

```python
report = spec_analyze(
    output_samples, backend="spiceowl", sample_rate_hz=1e6,
    fundamental_hz=127 * 1e6 / 8192,
    window="blackman_harris4", bin_half_width=3, dc_bin_half_width=3,
    dc_policy="remove_mean", sfdr_mode="integrated_lobe",
    signal_unit="V", full_scale_peak=1.0,
    harmonic_orders=[2, 3, 4, 5, 6, 7, 8, 9],
    return_spectrum=True,
)
```

Required arguments are `sample_rate_hz`, `fundamental_hz`, `window`, `bin_half_width`, `dc_bin_half_width`, `dc_policy`, `sfdr_mode` and `signal_unit`. Optional arguments are `harmonic_orders` (H2–H9 by default), `band_hz` (DC to Nyquist by default), `full_scale_peak`, `time_s`, and `return_spectrum`.

The windows are periodic rectangular, Hann and four-term Blackman–Harris, with coefficients 0.35875, 0.48829, 0.14128 and 0.01168. Their minimum coherent main-lobe half-widths are 0, 1 and 3 bins. Both DC and tone masks must meet this minimum; it does not guarantee that a noncoherent signal's leakage fits the chosen mask. An unverified window name such as `Hodgson` is rejected, not translated to another window.

Power is `abs(rfft(x*w))² / (N*sum(w²))`, with interior positive-frequency bins doubled and DC/Nyquist left single. A full coherent tone lobe therefore measures its mean-square signal power; the result records coherent gain and equivalent noise bandwidth. Units are signal-unit squared. DC can be removed by arithmetic-mean subtraction or retained and masked explicitly.

The declared fundamental determines its mask; no dominant-peak replacement occurs. Harmonics fold into `[0, fs/2]`. Overlapping or truncated in-band masks return invalid; harmonics outside a requested band are reported as such. Noise is the sum of in-band bins excluding DC, fundamental, and selected harmonics, with no filling or extrapolation into excluded bins. Thus:

- SNR = `10 log10(signal / noise)`.
- SNDR = `10 log10(signal / all in-band residual power outside DC and fundamental)`.
- THD = `10 log10(selected harmonic power / signal)`; harmonic ratios have the same denominator.
- `peak_bin` SFDR compares the largest fundamental bin with the largest residual bin. `integrated_lobe` compares the whole fundamental mask with the largest complete residual mask of the same width. The modes remain separate because their answers can differ for noncoherent tones.
- ENOB = `(SNDR − 1.7609125905568124) / 6.020599913279624`, using the measured sine. There is no hidden underdrive correction.
- Mean noise PSD is the mean retained noise-bin power divided by `fs/N`. dBFS values require a supplied `full_scale_peak`; a full-scale sine has power `full_scale_peak²/2`.

Leakage beyond declared masks remains in the residual. This routine does not subtract a fitted sine or estimate a noise floor to hide leakage. A tone below the input-scale floating-point resolution is rejected. Zero-power ratios are undefined, not infinite performance. Finite numerical roundoff at extremely low noise/distortion remains a numerical limitation; these routines do not establish analog measurement precision.

`coherent_fft` has the same arguments except that it fixes `window="rectangular"`. It requires `N*fin/fs` to be integer within 1e-6 cycles and reports whether the cycle count and record length are coprime. It neither enforces coprimality nor manufactures a coherent record. Supplied timestamps must match the declared uniform grid within relative tolerance 1e-6; no resampling is performed.

## Real ADCToolbox adapter

```python
report = spec_analyze(
    output_samples, backend="adctoolbox", sample_rate_hz=1e6,
    fundamental_hz=127 * 1e6 / 8192,
    window="hann", bin_half_width=1, signal_unit="V", full_scale_peak=1,
    nf_method=3, fundamental_tolerance_bins=0.5,
)
```

This release requires a full-scale value, explicit side-bin width, and `nf_method`: 1 median, 2 trimmed mean, 3 harmonic exclusion, or 4 legacy wide exclusion. Automatic method 0 and automatic side-bin sizing are disabled. Harmonics must be consecutive H2 through Hk, with k at most 9. `osr=1`, coherent averaging is disabled, no plot is created, and the band is DC to Nyquist. The adapter does not accept the independent backend's `dc_policy`, `dc_bin_half_width`, `sfdr_mode` or `band_hz` knobs; upstream uses its own documented conventions.

ADCToolbox 0.9.3 does not provide a fundamental override for `compute_spectrum`. The wrapper therefore verifies its detected fractional-bin frequency against the required declared frequency, using an explicit tolerance no greater than half a bin. A mismatch or harmonic collision returns invalid with null metrics. The upstream noise and harmonic methods use a normalized 1e-15 power floor; its SNDR denominator adds 1e-20. Noise/harmonic metrics at those floors and SNDR/ENOB where the additive floor is at least half the denominator become undefined. Returned `upstream_details` names the affected metrics and policy. Other upstream conventions are preserved and disclosed; backend results are not promised to be bit-for-bit equivalent.

The adapter checks the installed distribution **and** imported module version. Its provenance records the upstream API, pinned release and expected published wheel digest. That digest identifies the researched release artifact; the runtime does not hash the installed package files or claim binary attestation.

## Sine fits

`sine_fit3(samples, backend=..., frequency_hz=..., signal_unit=..., sample_rate_hz=... or time_s=...)` solves amplitude, phase and offset at a supplied frequency. `sine_fit4` instead requires `frequency_guess_hz` and `frequency_bounds_hz=[low, high]`. The interval must bracket the guess and span at most one inverse record duration. Uniform records with an explicit rate must remain below Nyquist. A supplied rate and timestamps must agree; time-only irregular records are supported by the independent backend. ADCToolbox requires uniform sampling.

The common phase convention is `A*sin(2π*f*(t-t0)+phase)+offset`, with `t0` the first sample. ADCToolbox's cosine phase is explicitly converted. Residual RMS divides squared error by N; the independent backend also reports a degrees-of-freedom-corrected residual standard deviation. Three-parameter design rank/conditioning and four-parameter Jacobian rank/conditioning are checked. The latter uses normalized columns so time/signal units do not dictate its condition limit. Constant or unresolved tones and unidentifiable frequencies return invalid. Low residual by itself is insufficient evidence of a frequency estimate.

The independent four-parameter optimizer fits a small frequency offset around the supplied guess, avoiding an artificial stopping tolerance at a large absolute cycle count. Upstream receives an explicit normalized frequency; automatic coarse frequency estimation is disabled. Its returned four-parameter frequency must lie in the declared interval and its convergence flag must be true. The upstream solver itself is not a bounded search, so the interval is a final acceptance check, not a claim about its intermediate steps.

## Weight calibration and reconstruction

The independent call requires `decisions`, aligned `reference`, and `reference_unit`. Decisions are a sample-by-stage matrix with values −1, 0 or 1. It solves `reference ≈ decisions @ weights + offset`, with `fit_offset=True` by default and an explicit returned condition limit (default 1e10). It rejects underdetermined, rank-deficient or ill-conditioned inputs. Nominal binary radix is never assumed. Optional `validation_decisions` and `validation_reference` report held-out error; training residual alone is explicitly labeled as insufficient independent validation.

```python
fit = analyze("weight_calibrate", {
    "backend": "spiceowl", "decisions": decision_rows,
    "reference": known_reference, "reference_unit": "V",
    "validation_decisions": held_out_rows,
    "validation_reference": held_out_reference,
})
```

The upstream call instead requires `sample_rate_hz`, `fundamental_hz`, `nominal_weights`, and `harmonic_order` (1–9). It accepts binary 0/1 columns only. Frequency search is disabled with `force_search=False`; rank patching or a changed frequency is rejected. A supervised reference is unsupported by that backend. Its returned weights use the `solver_unit_sine` gauge and cannot identify absolute capacitor weights, volts, or physical gain. Public `offset` is additive for both backends: the wrapper negates upstream's subtracted DC offset. Optional reconstruction is therefore `decisions @ weights + offset`, a DC-centered result in the upstream unit-sine gauge. The original DC value is preserved as `upstream_dc_offset`. Harmonic terms are nuisance reference terms, not a license to remove ADC distortion. Metrics are deliberately named `fit_residual_snr_db` and `fit_residual_enob_bits`; evaluate reconstructed output with a separately declared FFT for dynamic SNDR/THD.

`reconstruct_codes(decisions, weights, backend="spiceowl", reference_unit=..., offset=0)` evaluates the explicit matrix product. Preserve stage order and units. The routine does not know which column corresponds to a physical stage.

## Histogram linearity

`histogram_linearity` requires unsigned integer `codes`, `bits` (2–16), `distribution`, `endpoint_policy`, and `signal_unit`. Uniform stimuli require `stimulus_low`/`stimulus_high`; sine stimuli require known `sine_amplitude`/`sine_offset`. Those are input-distribution parameters, not inferred converter full scale. Full-range endpoint coverage is mandatory.

For code transition k, the cumulative count fraction `p(k)` gives `T(k)=low+(high−low)*p(k)` for uniform input, or `T(k)=offset−amplitude*cos(π*p(k))` for a uniformly sampled sine phase. Choose an endpoint transition reference or least-squares best-fit line. DNL is interior transition spacing divided by the fitted LSB minus one; INL is transition displacement from the selected line divided by that LSB. The two outer saturation-code DNL values are undefined because their bins are unbounded. Transition indices accompany detail arrays.

Zero-hit interior codes are candidates under finite statistics, not proof of physically missing codes. Expected occupancy under the declared distribution is checked against `min_expected_hits` (default 10); low statistics are warned. The wrapper does not compensate for source nonlinearity, hysteresis, input drift, correlated samples, incomplete sine-phase coverage, or missing captures. These assumptions must be justified independently before circuit conclusions.

## Transient metrics

`tran_metrics` requires increasing `time_s`, aligned `values`, `initial`, `final`, `step_time_s`, absolute `settling_band` in signal units, and `signal_unit`. It accepts irregular samples and uses piecewise-linear interpolation. Initial and final levels are explicit; no plateau estimate is silently substituted.

Settling is the last observed entry into the final-value band, with all subsequent samples inside and at least `min_hold_s` of observed hold. A record ending outside the band or too soon returns null settling. This is a finite-horizon result: it makes no assertion after the last sample. Overshoot and undershoot use the step direction, so falling steps are supported. Slew is average absolute slope between first oriented crossings at `slew_fractions`, default 20–80%; it is not maximum derivative. Delay uses `delay_fraction`, default 50%, relative to the ideal step time or an explicit aligned reference waveform and reference levels. Multiple crossings are warned.

Power requires explicit `voltage_v`, `current_a` and `current_sign="into_dut"` or `"from_dut"`. A scalar supply or aligned vector is accepted. Positive power means absorption by the DUT. Energy integrates sampled instantaneous power with the trapezoidal rule over `power_window_s`, or the observed post-step window when omitted; average power is energy divided by that duration. The code does not infer current direction or aggregate multiple supply rails.

These implementations are disclosed measurement definitions, not a claim of IEEE 1241 compliance or instrument qualification.

## CLI and CI

Use a complete JSON argument object:

```bash
python -m spiceowl.adctools sine_fit3 --request request.json --output report.json
```

Or separate CSV columns from parameters:

```bash
python -m spiceowl.adctools spec_analyze --input output.csv --params fft.json \
  --column samples=vout --column time_s=time --output report.json
```

`--format json` selects a dataset object mapping column names to arrays. `--column decisions=b0,b1,b2` forms an aligned decision matrix. Parameter and column names cannot override one another. Only one input may use `-` for stdin. Files are bounded to 64 MiB; duplicate JSON keys, NaN/Infinity, duplicate CSV headers and mismatched rows are rejected. Default stdout is one JSON report. Exit 0 means a valid operation, 3 means scientifically invalid/unidentifiable output, and 2 means an input/dependency/runtime error. A valid operation may still contain undefined metrics; exit 0 is never a specification pass.

## Verification and upstream attribution

Offline tests use original synthetic records: known coherent harmonic/spur powers, periodic window normalization, aliased harmonics, rank degeneracy, independent validation data, known histogram CDFs, analytical RC/damped responses, explicit power signs, and CLI error cases. The optional upstream tests invoke the real pinned implementation and inspect the exact frequency/iteration arguments. They do not need a PDK or simulator and do not establish that any remote simulation is correct.

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_adctools.py
```

ADCToolbox 0.9.3 is reused under MIT, copyright © 2025 Zhishuai Zhang, Lu Jie. No upstream source is copied into this package; dependency distributions retain their license. Its license requests academic citation when used in publications; consult the upstream citation instructions. Release metadata was checked on 2026-11-23. The wheel uploaded on 2026-11-10 has SHA-256 `78210fb93e0e8544f5f61eb0137ff33b485bd28d7ad5d1a8ea7cc898eb1aaf4f`.

Primary references:

- [ADCToolbox 0.9.3 release](https://pypi.org/project/adctoolbox/0.9.3/), [source and API examples](https://github.com/Arcadia-1/ADCToolbox), and [MIT license and citation request](https://github.com/Arcadia-1/ADCToolbox/blob/main/LICENSE).
- [SciPy Blackman–Harris window definition](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.windows.blackmanharris.html) and [window catalog](https://docs.scipy.org/doc/scipy/reference/signal.windows.html).
- [Analog Devices: Histogram Testing Determines DNL and INL Errors](https://www.analog.com/en/resources/technical-articles/histogram-testing-determines-dnl-and-inl-errors.html).
- [Analog Devices AN-835: Understanding High Speed ADC Testing and Evaluation](https://www.analog.com/en/resources/app-notes/an-835.html).
- [NIST: Bounds on Least-Squares Four-Parameter Sine-Fit Errors](https://www.nist.gov/publications/bounds-least-squares-four-parameter-sine-fit-errors).

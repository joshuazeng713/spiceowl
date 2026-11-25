# Native direct execution backend (SSH Direct, LSF, Slurm)

SpiceOwl provides a self-contained execution backend (`spiceowl direct`, `spiceowl.direct.DirectExecutionManager`, and `spiceowl.adapters.executors.direct`) that runs simulations directly over registered OpenSSH hosts without requiring a CircuitGit gateway or external orchestrator.

## Supported backends

| Backend | Process / Job Owner | Observation & Identity | Cancellation |
| :--- | :--- | :--- | :--- |
| `ssh_direct` | Detached Linux wrapper (`wrapper.py`) with `/usr/bin/timeout` | `/proc/<pid>/stat` (`pid`, `start_ticks`, `boot_id`) + `status.json` | Signals only the verified owned process group; refuses recycled PIDs |
| `lsf` | IBM Spectrum LSF (`bsub`) | `bjobs -noheader -o "jobid stat exit_code exec_host"` + `status.json` | Verified `bkill <job_id>` bound to frozen `manifest_sha256` |
| `slurm` | Slurm (`sbatch --parsable`) | `squeue` + `sacct` (captures `OUT_OF_MEMORY`, `TIMEOUT`, `CANCELLED`, `NODE_FAIL`) + `status.json` | Verified `scancel <job_id>` bound to frozen `manifest_sha256` |

## Safety and reconciliation guarantees

1. **Pure local preparation (`prepare`)**: Freezes netlist, optional relative support files (e.g., Verilog-A helpers or subcircuits), resource limits, scheduler parameters, license variables (`CDS_LIC_FILE` / `LM_LICENSE_FILE` / `CDS_AUTO_64BIT`), and SHA-256 digests locally without touching the remote host.
2. **Host identity gate**: Every SSH call checks remote `hostname` and `username` against the registered host profile before creating or modifying any remote file.
3. **Once-only dispatch fence**: `launch` persists `dispatch_claimed=true` locally BEFORE opening SSH, and creates an atomic remote `launch-claim` directory. A lost SSH connection or timeout records `launch_outcome_unknown` and blocks blind re-submission; use `status` or `lookup` to reconcile the original key.
4. **Separate observation vs failure**: Transient SSH disconnects or scheduler query lags (`squeue` empty before `sacct` updates) return `outcome_unknown` / `disconnected` and back off via `RuntimeMonitor` instead of marking the simulation failed.
5. **Bounded verified collection & five-dimensional report**: `collect` verifies terminal receipts, input hashes, and safe regular files (rejecting symlinks). Logs and metadata are collected first so oversized waveform files are explicitly reported in `skipped_files` rather than starving logs. Spectre runs automatically produce a five-dimensional `build_spectre_report` separating `execution_status`, `measurement_validity`, `functional_performance`, `operating_qualification`, and `dependency_provenance`.

## Web console workflow

When running `spiceowl serve` locally, direct execution is available directly in the web console without requiring a CircuitGit gateway:

1. Open **Simulation hosts** (`#hosts`), register your OpenSSH host(s) or server pool, verify connectivity, and optionally evaluate CPU/memory headroom.
2. Click **用推荐主机创建直连仿真 →** (or open **直连仿真 · SSH / 集群** `#direct` in the left navigation bar).
3. In **1. 冻结并准备实验 (Prepare)**, choose the host or pool auto-selection, pick `ssh_direct`, `lsf` (queue/project/job group), or `slurm` (partition/account/QoS), paste or load your local netlist file, and freeze the plan locally.
4. In **3. 任务控制**, drive the run step-by-step with explicit receipts: **Stage** → **Launch** → **Status / Watch / Unwatch / Cancel** → **Collect**, and inspect the five-dimensional Spectre report inline.

## CLI usage

Register and probe your Linux host(s) first using [host onboarding](host-onboarding.md):

```bash
spiceowl hosts add --file /absolute/private/host.json
spiceowl hosts scan --hosts sim-node-01
```

### 1. SSH Direct (single workstation or pool selection)

```bash
spiceowl direct prepare \
  --run-key inv-ssh-001 \
  --netlist ./tb.scs \
  --support models/mos.scs=./models/mos.scs \
  --hosts sim-node-01 sim-node-02 \
  --backend ssh_direct \
  --simulator spectre \
  --cpus 2 --memory-gib 8 --wall-seconds 3600

spiceowl direct stage inv-ssh-001
spiceowl direct launch inv-ssh-001
spiceowl direct watch inv-ssh-001 --interval 15 --max-wait 600 --allow-collect
spiceowl direct report inv-ssh-001
```

### 2. LSF cluster

```bash
spiceowl direct prepare \
  --run-key adc-lsf-001 \
  --netlist ./adc_tb.scs \
  --host lsf-login-01 \
  --backend lsf \
  --queue analog_long \
  --project adc_tapeout \
  --cpus 4 --memory-gib 16 --wall-seconds 14400 \
  --remote-root /scratch/designer/spiceowl-runs

spiceowl direct stage adc-lsf-001
spiceowl direct launch adc-lsf-001
spiceowl direct status adc-lsf-001
spiceowl direct collect adc-lsf-001
```

### 3. Slurm cluster

```bash
spiceowl direct prepare \
  --run-key adc-slurm-001 \
  --netlist ./adc_tb.scs \
  --host slurm-login-01 \
  --backend slurm \
  --partition sim_highmem \
  --account analog_lab \
  --cpus 8 --memory-gib 64 --wall-seconds 28800

spiceowl direct stage adc-slurm-001
spiceowl direct launch adc-slurm-001
spiceowl direct cancel adc-slurm-001
```

### 4. Standalone Agent mode (opt-in)

To expose `spiceowl_direct_read` (read-only) and `spiceowl_direct_control` (requires human tool-review approval) inside SpiceOwl's standalone Codex window:

```bash
spiceowl serve --agent-mode standalone --enable-direct-exec
```

Embedded CircuitGit mode (`--agent-mode embedded_in_circuitgit`) is unaffected and continues to use CircuitGit's own Runner and review chain.

## M4 bounded recovery and searchable recovery reports

Direct runs support opt-in bounded recovery (`spiceowl.recovery.bounded`) for confirmed operational failures without altering electrical netlists, stimuli, corners, tolerances, or scientific acceptance criteria:

- **Auto-resume watches on restart**: When `spiceowl serve` restarts, persisted non-terminal watches whose policy enables `auto_resume_watch` resume automatically.
- **License retry with backoff**: Confirmed license checkout/queue failures spawn a linked child attempt (`<root_run_id>-r1`, `-r2`, …) with identical frozen inputs within `max_recovery_attempts`, `license_backoff_seconds`, and `max_elapsed_seconds`.
- **Confirmed OOM scale-up & relocation**: Run-bound scheduler OOM evidence (such as Slurm `OUT_OF_MEMORY`) or explicit Spectre OOM errors scale requested memory by `oom_memory_multiplier` up to `max_memory_bytes`, and can relocate to a larger-memory eligible host in `HostRegistry` when `allow_host_relocation` is enabled. Unconfirmed `SIGKILL`, syntax/model/convergence errors, user cancellations, and completed runs (even with SOA or specification failures) are never retried.
- **Searchable recovery reports**: Every evaluated or recovered lineage writes both a structured JSON report and a human-readable Markdown report under `<data_dir>/direct_runs/recovery_reports/<root_run_id>.{json,md}` plus a searchable index. Search and download reports in the `#direct` console (**6. 有界恢复报告中心**) or via `spiceowl direct recovery-reports` / `spiceowl direct recovery-report <run_id> --markdown`.

## Binary waveform streaming (PSF Binary / PSFXL), envelope downsampling & Cycle Slicer

Large analog/mixed-signal simulations often generate multi-GB binary waveform files across hundreds or thousands of cycles. SpiceOwl avoids full raw-file downloads by combining **compute-near-data** remote inspection with a strict separation between visual previews and scientific measurements (`spiceowl.integrations.psf_binary`):

- **Unified streaming reader**: Supports Cadence PSF Binary (`psfbin`, both point-interleaved and windowed block-interleaved VALUE frames, scalar real and complex AC), PSFXL metadata + `.dat` bundles (`psfxl`), and PSF ASCII (`psfascii`) using pure Python stdlib with `seek()` random access — 100% compatible with macOS, Linux, and Windows.
- **Zero full download for oversized raw files**: `collect()` writes a lightweight `waveform_index.json` (datasets, formats, sizes, signal names, sweep bounds). Oversized `results.raw/` files stay on the remote Linux server and are queried in-place over verified SSH (`source: "remote_in_place"`).
- **Min-Max Envelope Downsampling vs Automatic Exact Window**:
  - Wide-range visual queries (`mode="auto"`) stream through selected signals and emit **Min-Max envelope buckets** (`representation: "downsampled_preview"`, `exact: false`, `measurement_safe: false`) so narrow spikes, glitches, and overshoot are never aliased away.
  - Zooming in (`window_count <= max_points`) automatically switches to **100% lossless raw samples** (`representation: "exact_window"`, `exact: true`, `measurement_safe: true`).
  - Scientific measurement helpers (`mode="exact"` and `extract_signal_for_adctools()`) refuse silent downsampling and return only lossless full-precision samples.
- **Multi-thousand-cycle inspection (Cycle Slicer)**: Slices up to 4,096 cycles via clock-signal edges (sub-sample linear interpolation) or a fixed period `T`, returning compact per-cycle metrics (`min`, `max`, `peak_to_peak`, `avg`, `end`, `duration`), automatic **worst-case cycle** detection (`peak_to_peak`, `max_value`, `min_value`, `overshoot`, `period_deviation`), 100% lossless intra-cycle waveforms for `first` / `steady_last` / `worst_case` / user-selected `cycle_index`, and an aligned phase overlay (eye view).
- **Offline interactive demo & CLI**: Click **加载 500 周期 PSF Binary 演示波形（含隐藏毛刺）** in the `#direct` console (**4. 二进制大波形查看器与周期切片**) or run:

```bash
spiceowl direct demo-waveform
spiceowl direct waveforms demo-psfbin-500c
spiceowl direct waveform-query demo-psfbin-500c --dataset tran.tran.tran --signals "v(out)" "v(clk)" --max-points 600
spiceowl direct waveform-cycles demo-psfbin-500c --dataset tran.tran.tran --signals "v(out)" "v(clk)" --clock "v(clk)" --worst-signal "v(out)"
```

## PVT / Corner / Monte Carlo batch experiment management

SpiceOwl's batch manager (`spiceowl.batch.DirectBatchManager`, `#direct` Section **5. PVT / Corner / Monte Carlo 批量实验管理**, and `spiceowl direct batch-*`) orchestrates multi-corner and Monte Carlo suites on top of `DirectExecutionManager`:

- **Deterministic matrix expansion & seed preservation**: Expands `pvt` (`process_corners × voltages × temperatures`), `monte_carlo` (explicit deterministic `mc_seed` per sample), or hybrid `pvt_mc` matrices (bounded up to 256 cases per batch). Each case freezes an independent `DirectExecutionManager` child run with its own SHA-256 netlist manifest.
- **Bounded concurrency dispatch**: `dispatch_next` stages and launches pending cases strictly up to `max_concurrency` (`1–32`); `refresh` polls active runs, collects terminal artifacts, extracts lossless metrics (`mode="exact"`), and optionally refills newly freed slots; `cancel` cancels active runs via verified cancellation and blocks remaining queued cases.
- **Conservative yield & distribution statistics**: Computes `mean`, `std`, `min`, `P5`, `P50`, `P95`, `max`, and `Cpk` across valid measurements while keeping execution-failed and missing samples explicitly in the yield denominator and preserving every failed/missing `mc_seed` and corner identity in JSON, Markdown, and CSV exports.
- **Offline interactive demos & CLI**:

```bash
spiceowl direct batch-demo --kind pvt
spiceowl direct batch-demo --kind monte_carlo
spiceowl direct batches
spiceowl direct batch-report demo-mc-40 --markdown
spiceowl direct batch-report demo-mc-40 --csv
```

## Current boundaries

- Offline unit tests verify plan freezing, scheduler command builders/parsers, atomic launch-claim fencing, verified cancellation, `RuntimeMonitor` supervision, bounded M4 recovery policies, watch resume, recovery reports, binary PSF/PSFXL streaming, Min-Max envelope preservation, Cycle Slicer drill-down, remote in-place waveform framing, PVT/Monte Carlo batch concurrency & conservative yield statistics, and five-dimensional reporting using synthetic fixtures.
- Site-specific LSF/Slurm cluster configurations (custom wrapper flags, Kerberos/module init scripts, and shared filesystem permissions) and proprietary PSFXL sub-variants still require authorized live qualification on the target site.
- Native solver checkpoint continuation and automated numerical-parameter tuning remain unimplemented.

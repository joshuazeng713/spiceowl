<p align="center">
  <img src="assets/brand/spiceowl.svg" width="128" height="128" alt="SpiceOwl: an owl watching a circuit waveform">
</p>

<h1 align="center">SpiceOwl</h1>

<p align="center"><strong>You sleep. SpiceOwl keeps watch.</strong></p>
<p align="center">An open-source, web-first AI Agent simulation workbench for analog &amp; mixed-signal IC engineers.</p>
<p align="center">English · <a href="README.zh-CN.md">简体中文</a> · <a href="docs/direct-execution.md">Direct Execution &amp; Recovery</a> · <a href="docs/cross-platform.md">Cross-Platform &amp; CI</a> · <a href="docs/architecture.md">Architecture</a> · <a href="docs/roadmap.md">Roadmap</a> · <a href="CONTRIBUTING.md">Contributing</a></p>

<p align="center"><a href="https://spiceowl-dev.github.io/spiceowl/"><strong>Open SpiceOwl Web Workbench</strong></a> · <a href="https://spiceowl-dev.github.io/spiceowl/workflow.html#so-videos"><strong>Watch Product Film &amp; 10-Chapter Demos</strong></a> · <a href="https://spiceowl-dev.github.io/spiceowl/workflow.html">Product Showcase</a> · <a href="https://github.com/spiceowl-dev/spiceowl/releases/tag/demo-videos-v1">Download HD Videos</a></p>

SpiceOwl is a web-first simulation management workbench natively supporting **macOS, Linux, and Windows**. [`https://spiceowl-dev.github.io/spiceowl/`](https://spiceowl-dev.github.io/spiceowl/) is the **single formal user interface** — no second local website or daily terminal commands. When you want to connect your own SSH/LSF/Slurm EDA cluster or your local Codex Agent, install the headless **SpiceOwl Connector** once (macOS: double-click `Install SpiceOwl Connector.command`; Windows: double-click `Install SpiceOwl Connector.bat`; registers `spiceowl://` without admin privileges and stays silently on `127.0.0.1:8765`), then click **"Connect local connector"** inside the web workbench.

## Product Launch Film & 10-Chapter Video Walkthrough

> **Watch the full voiced product launch film (`10:45`, HD 1600×812 with Chinese voiceover + original soundtrack) or jump straight into any of the 10 real-cluster workflow chapters interactively on the [Product Showcase Page](https://spiceowl-dev.github.io/spiceowl/workflow.html#so-videos).**

<p align="center">
  <a href="https://spiceowl-dev.github.io/spiceowl/workflow.html#so-videos">
    <img src="src/spiceowl/web/static/demo-poster-film.webp" width="880" alt="SpiceOwl Launch Film: You Sleep. SpiceOwl Keeps Watch. Click to watch interactively.">
  </a>
</p>

<p align="center">
  <a href="https://spiceowl-dev.github.io/spiceowl/workflow.html#so-videos"><strong>▶ Watch Full Launch Film Inline (10:45)</strong></a> ·
  <a href="https://github.com/spiceowl-dev/spiceowl/releases/download/demo-videos-v1/SpiceOwl_Launch_Film.mp4">Direct MP4 (59.8 MB)</a> ·
  <a href="https://github.com/spiceowl-dev/spiceowl/releases/tag/demo-videos-v1">All 11 Release Assets</a>
</p>

| # | Chapter | Duration | What It Demonstrates | Watch |
| :---: | --- | :---: | --- | --- |
| **01** | **30-Second Zero-Config Local Connect** | `0:34` | Double-click `spiceowl://` Connector once, silent `127.0.0.1:8765` pairing & local Agent bridge | [Play](https://spiceowl-dev.github.io/spiceowl/workflow.html#video-01) · [MP4](https://github.com/spiceowl-dev/spiceowl/releases/download/demo-videos-v1/01_initial_connect.mp4) |
| **02** | **1-Minute EDA Cluster Onboarding** | `0:41` | Visual SSH Wizard with `ProxyJump` reuse, automatic legacy `ssh-rsa` fallback & 5-point live probe | [Play](https://spiceowl-dev.github.io/spiceowl/workflow.html#video-02) · [MP4](https://github.com/spiceowl-dev/spiceowl/releases/download/demo-videos-v1/02_connect_servers.mp4) |
| **03** | **Cluster Compute & FlexLM License Dashboard** | `0:40` | Live 512-core CPU/RAM/EDA workload + multi-server FlexLM SPICE license pool & idlest host recommendation | [Play](https://spiceowl-dev.github.io/spiceowl/workflow.html#video-03) · [MP4](https://github.com/spiceowl-dev/spiceowl/releases/download/demo-videos-v1/03_resource_monitor.mp4) |
| **04a** | **One-Prompt Remote Circuit Open (AI Agent)** | `0:45` | Fuzzy remote `cds.lib` search & isolated netlist + Maestro export without Virtuoso GUI or ADE licenses | [Play](https://spiceowl-dev.github.io/spiceowl/workflow.html#video-04a) · [MP4](https://github.com/spiceowl-dev/spiceowl/releases/download/demo-videos-v1/04a_agent_open_workspace.mp4) |
| **04b** | **Read-Only Web Remote Library Manager** | `0:42` | Point-and-click remote `cds.lib` browser, auto-tagged `schematic`/`maestro`, isolated `Cadence si` + read-only SKILL export | [Play](https://spiceowl-dev.github.io/spiceowl/workflow.html#video-04b) · [MP4](https://github.com/spiceowl-dev/spiceowl/releases/download/demo-videos-v1/04b_manual_library_manager.mp4) |
| **05** | **One-Prompt Batch Sweep & Auto-Report Handoff** | `0:51` | Multi-temperature grouped submission, 4s auto-poll, auto-collect & automatic AI report handoff | [Play](https://spiceowl-dev.github.io/spiceowl/workflow.html#video-05) · [MP4](https://github.com/spiceowl-dev/spiceowl/releases/download/demo-videos-v1/05_submit_sim_and_report.mp4) |
| **06** | **Interactive HTML Reports & 102 KB Offline Share** | `0:43` | Vector waveforms, KPI cards, multi-corner Testbench Summary & 1-click 102 KB self-contained offline HTML | [Play](https://spiceowl-dev.github.io/spiceowl/workflow.html#video-06) · [MP4](https://github.com/spiceowl-dev/spiceowl/releases/download/demo-videos-v1/06_simulation_reports.mp4) |
| **07** | **Free-Create Custom AI Engineering Report** | `0:57` | Plain-language custom PSF analysis: Gain, ±0.1% Settling Time, 10–90% Rise Time & VDD Power vs. Temperature | [Play](https://spiceowl-dev.github.io/spiceowl/workflow.html#video-07) · [MP4](https://github.com/spiceowl-dev/spiceowl/releases/download/demo-videos-v1/07_custom_ai_report.mp4) |
| **08** | **Natural-Language Code-First Night Watch** | `0:58` | Transparent `night_watch.py` rule engine edited in plain language + mandatory isolated Dry-Run verification | [Play](https://spiceowl-dev.github.io/spiceowl/workflow.html#video-08) · [MP4](https://github.com/spiceowl-dev/spiceowl/releases/download/demo-videos-v1/08_agent_edit_night_watch.mp4) |
| **09** | **Scientific Redlines & Self-Evolving Guardrails** | `3:25` | Refuses to alter netlists on unknown `STB` failure, then turns a 1-sentence authorization into a permanent Dry-Run-verified rule | [Play](https://spiceowl-dev.github.io/spiceowl/workflow.html#video-09) · [MP4](https://github.com/spiceowl-dev/spiceowl/releases/download/demo-videos-v1/09_night_watch_new_case.mp4) |

## What's new

- **Code-First Night Watch (`night_watch.py`), Dry-Run Studio, 1-Click Toggle & Quick-Ops Skills**: Replaced opaque checkboxes with a transparent, versionable local Python rule engine (`src/spiceowl/night_watch_script.py`). Toggle watching on/off with one click in the top bar, tune timeouts/OOM caps/host-relocation rules in plain language (accelerated by instant 1-call `update_watch_config` and the `spiceowl-quick-ops` + `spiceowl-night-watch` Skills), and verify every rule edit inside an **isolated Dry-Run sandbox** before activation. When an unknown error crosses the circuit-definition boundary, SpiceOwl refuses to alter netlists, stimuli, corners, or specs, archives diagnostic evidence, and — once authorized by the engineer — evolves a permanent Dry-Run-verified recovery rule that honestly flags skipped analyses as `Skipped / Unverified`.
- **Dual-Mode Interactive HTML Report Engine (`Fast` & `Free-Create`), Testbench Summary & 102 KB Offline Export**: Simulation completion automatically triggers 4s polling → auto-collect → AI report handoff (`src/spiceowl/report_builder.py`, `spiceowl-sim-report-fast`, `spiceowl-sim-report`). **Fast Mode** renders vector SVG waveforms, KPI cards, 5D diagnostics, multi-corner **Testbench Summary** comparison tables, and a **~102 KB single-file self-contained offline HTML** for instant design reviews. **Free-Create Mode** lets engineers ask in plain language for bespoke measurements from raw PSF files (e.g., Gain, ±0.1% Settling Time, 10–90% Rise Time, VDD Power vs. Temperature) and archives the resulting interactive plots straight back into the Testbench report.
- **Zero-Virtuoso Remote Circuit Entry (AI Agent Fuzzy Search + Read-Only Web Library Manager)**: Open remote Cadence designs without launching a laggy Virtuoso GUI or occupying expensive ADE interactive licenses. Either ask the Agent in plain language to search `cds.lib` and export the workspace, or browse remote directories directly in the **Read-Only Web Library Manager** (`schematic`/`maestro` auto-tagging, isolated `Cadence si` + read-only SKILL export that never modifies source libraries), alongside local `.scs`/`.sp`/`.cir` import.
- **Visual SSH Setup Wizard & Live FlexLM SPICE License Pool Dashboard**: Onboard multi-hop EDA clusters in one minute without hand-editing `~/.ssh/config` — automatic `ProxyJump` & username reuse across nodes, automatic legacy OpenSSH `ssh-rsa` compatibility fallback, local-only managed keys (passwords never saved to disk), and 5-point live verification (`onboarding_wizard.py`). The **Server Resources** page probes CPU, RAM, disk, active EDA processes, and live **FlexLM SPICE license pools** (`Spectre`, `Spectre X`, `ADE Assembler`, `ADE Explorer`, `Spectre GXL`) in parallel (`host_activity.py`) and passes the idlest host recommendation directly to the Agent.
- **Streamlined 4-Page Unified Web Workbench, 3-Tier Codex Permissions & 5 Bundled Agent Skills**: Unified disconnected preview and live local-Connector UX into 4 focused pages (`Home & Connect`, `Server Resources`, `Simulation Workbench`, `History & Reports`), backed by 3 explicit Codex permission tiers (**Safe**, **Supervised**, **Full Access**) with live shell streaming, 1-SSH fast submission (`spiceowl-sim-submit`), and resilient remote execution (`csh`/`tcsh`, Python 2.7, auto-Spectre discovery).
- **Binary Waveforms (PSF Binary / PSFXL), Lossless Analog Metrics (M3) & PVT / Monte Carlo Batches** ([direct execution guide](docs/direct-execution.md)): Stream multi-thousand-cycle binary waveforms in-place on remote Linux hosts via `seek()` with Min-Max envelope preview (never drops glitches), automatic 100% lossless zoom-in, **Cycle Slicer** worst-cycle drill-down, built-in lossless analog recipes (`spiceowl.experiments.analog_metrics`), and conservative PVT / Monte Carlo yield & `Cpk` analytics.
- **Native Cross-Platform Support (macOS / Linux / Windows) & 17-Job CI Matrix** ([cross-platform guide](docs/cross-platform.md)): Centralized OS compatibility layer ([`src/spiceowl/_platform.py`](src/spiceowl/_platform.py)) — no WSL required on Windows — verified on every push across Ubuntu, macOS, and Windows plus Chromium Playwright desktop & mobile (`390px`) E2E checks ([`.github/workflows/tests.yml`](.github/workflows/tests.yml)).

## Product showcase

**[Explore SpiceOwl Product Philosophy & 9-Chapter Video Walkthrough](https://spiceowl-dev.github.io/spiceowl/workflow.html)**

- Starts with SpiceOwl's end-to-end collaboration architecture and a compact expandable bar for the **10:45 Full Product Launch Film**.
- Walks through all **9 chapters** in a side-by-side editorial layout: **left column** explains the capability boundary and design philosophy, while **right column** streams the real-cluster hands-on demonstration video on demand (zero video bandwidth consumed until clicked).
- Chapter 04 includes an interactive dual-path switcher pairing **04a (AI Fuzzy Search)** and **04b (Read-Only Web Library Manager)**, culminating in Chapter 09's scientific redlines and self-evolving `night_watch.py` rule demo.

**Optional design integration:** [CircuitGit — AI-native local circuit design and Git version management](https://circuitgit-dev.github.io/CircuitGit/workflow/index.html). Its showcase explains how a design workbench can embed SpiceOwl's tools alongside local circuit editing, Git history, and testbench CI without starting a second Agent.

## Try the workbench

> **Try it in your browser — zero installation.** Open [`https://spiceowl-dev.github.io/spiceowl/`](https://spiceowl-dev.github.io/spiceowl/) to explore the unified 4-page workspace, synthetic runs, and scripted diagnoses right away. When ready to connect your real SSH/LSF/Slurm servers and local Codex Agent, click **"Connect local connector"** in the top bar (one-time headless `spiceowl://` Connector install, no admin privileges needed). See the [publishing guide](https://github.com/spiceowl-dev/spiceowl/blob/main/docs/github-pages.md) for automatic Pages deployment.

**Two Agent modes:** standalone SpiceOwl owns one explicitly connected Codex App-Server window with 3 permission tiers (`Safe` / `Supervised` / `Full Access`) and 5 bundled Skills; embedded SpiceOwl exports tools into CircuitGit's existing window and never starts a second Agent. See the [dual-mode Agent guide](docs/dual-mode-agent.md) and [CircuitGit integration contract](docs/circuitgit-integration.md).

Additional deep-dive guides: [Direct Execution & Bounded Recovery](docs/direct-execution.md) · [Simulation Intake](docs/onboarding-discovery.md) · [Guided Onboarding](docs/smart-onboarding.md) · [Cached PDK Scope SDK](docs/pdk-scope-selection.md) · [Agent-Authored Testbenches](docs/agent-testbench.md) · [Per-Device PDK LUT](docs/pdk-lut.md) · [Analysis Tools (`pip install '.[adctools]'`)](docs/adctools.md) · [Native AC/DC Measurements](docs/ac-dc-results.md) · [Agent Reproducibility](docs/agent-reproducibility.md).

![SpiceOwl web console overview](docs/images/local-console.png)

*Unified web workbench overview with grouped navigation, live status cards, and instant onboarding entry.*

![SpiceOwl 500-cycle PSF Binary Min-Max waveform and Cycle Slicer](docs/images/waveform-slicer.png)

*Binary PSF Min-Max envelope preview, automatic lossless zoom-in, and Cycle Slicer `#317` glitch drill-down.*

## Why SpiceOwl

You start a batch simulation before leaving the lab. By morning, SSH dropped, a job hit OOM, a license stalled, or a run finished with a missing signal or subtle SOA violation — and even when it succeeds, you still spend half an hour screenshotting waveforms into slides.

SpiceOwl turns that fragile loop into an **auditable, self-watching engineering pipeline**: what ran, on which host with which license/PDK state, what failed, which bounded recovery rule fired (without ever tampering with circuit intent), and an interactive report ready for review.

## Capability status

| Capability | Intended behavior | Current status |
| --- | --- | --- |
| **Web Entry & Connector** | Single web UI on `github.io` + silent local loopback Connector | Shipped (`127.0.0.1:8765`, `spiceowl://` zero-admin installer for macOS/Windows/Linux) |
| **SSH & License Pool** | Visual multi-hop cluster setup + live compute & FlexLM monitoring | Shipped (ProxyJump reuse, legacy RSA fallback, 5-point probe, CPU/RAM/EDA + FlexLM pools) |
| **Zero-Virtuoso Entry** | Open remote Cadence designs without Virtuoso GUI or ADE seats | Shipped (AI Agent fuzzy `cds.lib` open + Read-Only Web Library Manager + local netlist import) |
| **Execute & Observe** | Standalone `ssh_direct / lsf / slurm` + optional CircuitGit Runner | Shipped (SHA-256 input freeze, 1-SSH submit Skill, 4s auto-poll, `launch_outcome_unknown` fencing, PVT/MC batches) |
| **Report & Analyze** | Fast interactive reports + bespoke AI PSF analysis + offline share | Shipped (Fast/Free-Create dual mode, Testbench Summary, ~102 KB offline HTML, lossless M3 metrics, PSF Binary/Cycle Slicer, ADCTools) |
| **Night Watch & Recovery** | Transparent programmable rules + Dry-Run verification + redlines | Shipped (`night_watch.py`, 1-click toggle, Dry-Run Studio, M4 license/OOM recovery, human-authorized custom rules; binary solver checkpoint continuation remains future work) |
| **5D Scientific Verdicts** | Separate execution, measurement validity, spec, SOA & provenance | Shipped (five independent dimensions; valid spec misses & skipped analyses remain explicit) |

### A morning report answers three separate questions

| Question | Example answer |
| --- | --- |
| Did the simulator finish? | Execution completed |
| Is this a valid measurement of the requested experiment? | Inputs matched; required signals and time window are present |
| Did the circuit meet its specification? | No — settling time exceeded the target |

A valid result that misses a design target is a valuable engineering result worth preserving. Recovery must never alter netlists, stimuli, corners, or criteria just to turn a failure into a green checkmark.

## Design principles

- **Execution survives the interface.** Remote schedulers and processes own the job; closing the browser never kills a simulation.
- **Unknown is a real state.** A dropped SSH connection is not proof of failure — reconcile the original `submission_key` before considering another launch.
- **Transparent code keeps watch; agents investigate and evolve rules.** Routine recovery runs deterministically in readable `night_watch.py`; every Agent edit is Dry-Run verified first.
- **Every attempt has provenance.** Preserve SHA-256 frozen inputs, tool versions, execution receipts, recovery diffs, and honest `Skipped / Unverified` flags.
- **Scientific redlines stay hard.** Changes to netlists, tolerances, stimuli, corners, seeds, or specs require explicit human authorization.

## Repository map

```text
spiceowl/
├── README.md                  # English introduction & video walkthrough index
├── README.zh-CN.md            # Chinese introduction & video walkthrough index
├── skills/                    # 5 bundled Agent Skills (quick-ops, sim-submit, report-fast, report, night-watch)
├── docs/                      # Architecture, direct execution, run contract, roadmap, decisions
├── assets/brand/              # Editable project icon and exported avatar
├── examples/                  # Original inverter and draft configuration examples
├── scripts/                   # Setup, Pages builder, link checker & cross-platform full test runner
├── src/spiceowl/              # Installable Python package
│   ├── _platform.py           # Centralized macOS / Linux / Windows compatibility layer
│   ├── connector.py           # Zero-admin spiceowl:// Connector installer & loopback bridge
│   ├── direct.py              # Standalone SSH/LSF/Slurm direct execution & 4s auto-watch supervisor
│   ├── batch.py               # PVT / Corner / Monte Carlo batch execution & conservative yield stats
│   ├── hosts.py               # Registered host pool & SSH connectivity probes
│   ├── host_activity.py       # Parallel cluster CPU/RAM/EDA process & FlexLM SPICE license polling
│   ├── onboarding_wizard.py   # Visual SSH setup wizard (ProxyJump reuse & legacy RSA auto-fallback)
│   ├── report_builder.py      # Dual-mode Fast/Free-Create interactive HTML & SVG report engine
│   ├── night_watch_script.py  # Code-first night_watch.py rule engine, AST config updater & Dry-Run sandbox
│   ├── watch_handoff.py       # Overnight watch event log & Agent handoff policy layer
│   ├── skills.py              # Bundled Skill loader & prompt integration
│   ├── standalone_agent.py    # 3-tier Codex permission runtime & Agent tool dispatcher
│   ├── simulation_tools.py    # Unified Agent simulation, library & UI sync tools
│   ├── experiments/           # Source extraction, frozen recipes & lossless M3 analog measurements
│   ├── onboarding/            # Remote Library Manager, authored-TB validation, PDK discovery & LUT
│   ├── adctools/              # Optional explicit ADC algorithms & pinned ADCToolbox adapter
│   ├── monitoring/            # Durable observation & restart-safe incremental log streaming
│   ├── recovery/              # M4 bounded recovery engine & searchable JSON/Markdown reports
│   ├── integrations/          # CircuitGit client/pool, Spectre diagnostics & PSF parsing
│   ├── domain/                # Experiment, attempt, receipt, and event models
│   ├── adapters/              # Execution (SSH, direct/LSF/Slurm, mock) and simulator integrations
│   ├── storage/               # Durable records and artifact references
│   ├── read_api.py            # Transport-neutral stored read views for CircuitGit
│   ├── cli/                   # Local command interface
│   └── web/                   # Loopback API, unified workbench & showcase assets
└── tests/                     # Offline Python, Node, and Playwright browser E2E test suites
```

## Start here

### Fastest path: Web Workbench + Local Connector

1. Open [`https://spiceowl-dev.github.io/spiceowl/`](https://spiceowl-dev.github.io/spiceowl/).
2. Click **"Connect local connector"** in the top bar and follow the 30-second one-time installer (macOS `.command` / Windows `.bat` / Linux script).
3. Use the **Visual SSH Setup Wizard** to connect your EDA cluster, then open a circuit via the AI Agent or the **Read-Only Web Library Manager**.

### Developer / local source install

For a complete development environment, run `python3 scripts/setup_dev.py` after cloning, then `.venv/bin/spiceowl serve --demo` (see [developer setup](docs/developer-setup.md)). Or install directly with Python 3.11+ on macOS, Linux, or Windows ([cross-platform guide](docs/cross-platform.md)):

```bash
# macOS / Linux
git clone https://github.com/spiceowl-dev/spiceowl.git
cd spiceowl
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/spiceowl serve --demo
```

```powershell
# Windows (PowerShell)
git clone https://github.com/spiceowl-dev/spiceowl.git
cd spiceowl
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\spiceowl.exe serve --demo
```

Run the full cross-platform verification suite in one command:

```bash
# macOS / Linux
.venv/bin/python scripts/run_full_tests.py

# Windows (PowerShell)
.venv\Scripts\python.exe scripts\run_full_tests.py
```

### CLI direct execution (optional)

```bash
# Freeze inputs with opt-in M4 bounded recovery (license retries + OOM scale-up/relocation)
spiceowl direct prepare --run-key amp-tran-01 --netlist ./amp.scs --host lab-sim --backend slurm \
  --enable-recovery --max-recovery-attempts 2 --max-memory-gb 32 --allow-host-relocation
spiceowl direct stage amp-tran-01
spiceowl direct launch amp-tran-01
spiceowl direct watch amp-tran-01 --allow-collect --auto-resume-watch

# Search persisted recovery reports
spiceowl direct recovery-reports --query oom
spiceowl direct recovery-report amp-tran-01 --markdown
```

Optional CircuitGit gateway mode (`spiceowl serve --live-client-config /absolute/private/live-client.json`) and developer smoke runner ([`scripts/run_inverter_smoke.py`](scripts/run_inverter_smoke.py)) remain documented in the [standalone runtime guide](docs/standalone-runtime.md), [direct execution guide](docs/direct-execution.md), and [inverter example guide](examples/inverter/README.md).

## Contributing

We welcome analog designers, CAD engineers, and developers working on reliable scientific computing. Useful contributions include sanitized failure cases for `night_watch.py`, lossless measurement recipes, and failure-injection tests.

Read [CONTRIBUTING.md](CONTRIBUTING.md) before sharing data. Use [SECURITY.md](SECURITY.md) for reporting vulnerabilities and handling sensitive materials. Background references are listed in [docs/references.md](docs/references.md).

## License

[MIT](LICENSE). Cadence tools, PDKs, and third-party components are not distributed by this project. Any future third-party code retains its applicable license and notices.

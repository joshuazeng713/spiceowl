# Reproducible tool use by an agent

An agent should supply explicit inputs to bounded tools and preserve their
receipts. The registered experiment workflow already executes a fixed sequence
without an LLM. This makes that sequence testable; it does not establish that
every model can select the right experiment or write a correct testbench.

This guide describes the current implementation and a proposed model evaluation.
No weak-model or other LLM evaluation is claimed here.

## Select an implemented path

| Path | Available evidence | Boundary |
| --- | --- | --- |
| Registered `inverter-tran` / `prepared-transient` | `workflow prepare/run/inspect/execute`, frozen recipe compiler, PSF ASCII measurements, offline workflow tests | Requires a compatible CircuitGit service, registered recipe, explicit grant and working remote dependencies |
| Authored `agent-testbench` | Local inventory, bounded static validator, exact-byte compiler and paired CircuitGit human-reviewed execution | Integration is published; supported result types and explicit postprocessing still bound the scientific conclusion. A generic no-LLM driver or weak-model benchmark has not been demonstrated |
| Full-library PDK index / relative Verilog-A | Per-device lookup within an observed host/root scope, explicit missing-model results, source-bound bindings and reviewed helper/header closure | A cache hit does not establish current remote dependencies. Model selection, complete staging and the final dependency audit still belong to the paired execution route |
| [Explicit cached scope selection](pdk-scope-selection.md) | Paged entry/section IDs, same-entry closure, duplicate/TMI checks, immutable selection and exact audit comparison | No cross-file entry combination or corner inference; execution requires a fresh audit and review. A complete selected closure does not change whole-library partial coverage |
| `spiceowl.adctools` | Offline JSON/CSV/Python analysis, explicit backend selection and synthetic mathematical tests | Caller owns dataset identity, experimental preconditions and specification comparison |
| Native AC/DC | Typed PSF ASCII datasets and explicit `ac_metrics` / `dc_op_metrics` algorithms | Saved output alone does not define loop gain, a private model's region codes or the intended operating condition. CircuitGit must bind the selected dataset and measurement contract to the run |

Use [workflow.md](workflow.md) for the registered route,
[agent-testbench.md](agent-testbench.md) for the authored bundle contract, and
[adctools.md](adctools.md) for the numerical definitions. Generic objectives must
not be sent through the old inverter measurement function: it explicitly returns
`unknown` for `agent-testbench` until the required postprocessor is supplied.

## Freeze the question before execution

The user or an explicitly authorized agent must define the experiment. Record the
source commit, circuit selection, interface, analysis, PVT conditions, stimuli,
loads, saved signals, units, measurement interval and acceptance criteria.
Include sample timing, ADC decision-column order, reference data and seeds when
the requested method needs them. Do not silently assume VDD, corner, terminal
ordering, test intent, a full-scale amplitude or an acceptable result threshold.

Discovery and recommendation outputs are proposals backed by bounded evidence.
An observed model name, a suggested section or a registered directory is not
proof of device semantics, full dependency coverage or operating qualification.
Missing or ambiguous scientific inputs must remain explicit missing fields.

For an authored testbench, retain the full `ic.agent_testbench.v1` object:
`text`, `interface`, `analyses`, `stimuli`, `objectives`, `criteria` and
`postprocess`. The compiler hashes the complete object. Postprocessing text is
inert metadata at this layer; it is not permission to execute arbitrary code.

## Keep evidence attached to the exact inputs

1. Select committed source bytes and preserve every supplied support file.
   Retain recipe, source, testbench, model-profile and file digests returned by
   the compiler. A source Git commit alone does not identify external models.
2. Select the actual model entry, sections and any required remote platform/TMI
   choice. Run the dependency audit for that final combination. Keep its scope,
   observed fingerprints and missing dependencies; do not relabel a candidate
   inventory as a completed audit.
3. Review the frozen testbench, conditions, dependency evidence and proposed
   action through the execution owner's applicable authorization path. The
   direct workflow operates under the installed project/host/caller grant; it
   does not create a second per-request approval system. Preserve any required
   IC Service review instead of treating an agent's decision as human approval.
4. Bind execution and collected artifacts to the returned plan/job/run identity.
   Preserve the recipe digest, raw-result digest, collection receipt and parser
   result before computing or comparing metrics. Keep private PDK files,
   credentials and real outputs outside the public repository.
5. Preserve the exact measurement input, algorithm/backend version, parameters,
   result and warnings. When exporting a CSV, retain its digest and its mapping
   to the original run, artifact, signals, units and selected interval.

Steps 1–4 use the existing compiler, audit and Runner contracts where supported.
The standalone ADC CLI does **not** create a run-bound artifact manifest or
verify simulator provenance; the caller must supply and retain that linkage.

## Execute and reconcile the same job

The following commands require an already registered recipe and private client
configuration. Replace placeholders with exact registered or returned values.
Keep the same client configuration and its request journal throughout.

```bash
spiceowl workflow --client-config /absolute/private/live-client.json prepare \
  --recipe RECIPE_ID --source-commit FULL_40_CHARACTER_GIT_SHA
spiceowl workflow --client-config /absolute/private/live-client.json run \
  --plan-id PLAN_ID
spiceowl workflow --client-config /absolute/private/live-client.json inspect \
  --job-id JOB_ID
```

`prepare` compiles and audits without launching a simulator. Only a prepared
response with an empty `missing` list supplies a usable plan. `run` uses the
fixed key `experiment-PLAN_ID`. `inspect` observes that exact job and may collect
its terminal artifacts; it is a control operation, not a passive cached read.

The deterministic combined command is:

```bash
spiceowl workflow --client-config /absolute/private/live-client.json execute \
  --recipe RECIPE_ID --source-commit FULL_40_CHARACTER_GIT_SHA \
  --timeout 600 --poll-seconds 3
```

A timeout ends the local wait, not the solver. Continue with the returned job ID.
If dispatch is uncertain and no job ID was received, preserve the original plan
and submission key and query:

```bash
spiceowl workflow --client-config /absolute/private/live-client.json lookup \
  --key experiment-PLAN_ID
```

An absent lookup is not proof that execution never started. Do not delete the
journal, invent another key or reprepare merely to escape uncertainty. Lookup
does not authorize replay; unresolved dispatch requires reconciliation of the
original Runner records. Client tests cover this behavior and timeout identity.

## Analyze data with declared algorithms

Use the full-resolution record for the declared measurement interval. Preserve
the original capture and the explicit interval selection. Do not measure from
a screenshot or a downsampled chart, silently discard samples, or resample an
irregular record to make an FFT accept it.

```bash
python -m spiceowl.adctools spec_analyze --input output.csv --params fft.json \
  --column samples=vout --column time_s=time --output report.json
```

`fft.json` must contain `backend` and the chosen backend's explicit arguments.
For `spiceowl`, these include sample rate, declared fundamental, window, tone/DC
mask widths, DC policy, SFDR mode and signal unit. Save any selected harmonic
orders, frequency band and full-scale value too. The `adctoolbox` backend has a
different documented contract and requires exactly version 0.9.3; no automatic
fallback is performed. Both report their algorithm/backend identity.

Choose the operation from the frozen question: FFT for spectral metrics,
known-frequency or bounded-frequency sine fit for a fit, declared-distribution
histogram for code linearity, or explicit step/power definitions for transient
metrics. Sine-calibration residual SNR is not dynamic FFT SNDR. Upstream
`solver_unit_sine` weights do not identify absolute capacitor weights or volts.

Check top-level status, each requested metric's status, its unit and warnings.
Null, invalid and undefined metrics cannot pass a criterion. ADC CLI exit 0
means the analysis operation was valid, not that a design passed. The returned
`specification_result` remains `not_evaluated`; criterion comparison belongs to
the experiment layer. Numerical array and file limits are documented in
[adctools.md](adctools.md); do not evade them by silently truncating data.

## Report distinct conclusions

Preserve the registered workflow's five dimensions:
`execution_status`, `measurement_validity`, `functional_performance`,
`operating_qualification` and `dependency_provenance`. Include their scope and
evidence. A completed process may have invalid measurements; valid measurements
may fail a performance target. A completed workflow only means collection and
reporting finished. Never reduce these dimensions to an unqualified overall
pass, or change the criteria after seeing the result.

Current limits include automatic solver repair/restart, migration to another
host, automatic resource increases and scientific-intent discovery. Generic
postprocessing is not automatically executed. The supported Spectre grammar,
PSF ASCII parser and offline CSV/JSON inputs do not cover every netlist dialect,
binary waveform format, simulator or arbitrary Virtuoso session. Unsupported
formats and incomplete evidence require a supported explicit route or a gap
report; they are not permission to fabricate compatible data.

## Regress the tools without an LLM

From the repository root with Python 3.11+ and the documented dependencies:

```bash
PYTHONPATH=src .venv/bin/python -m unittest \
  tests.test_experiment_recipes tests.test_experiment_measurement \
  tests.test_experiment_workflow tests.test_spectre_report
PYTHONPATH=src .venv/bin/python -m unittest \
  tests.test_agent_tb tests.test_adctools
```

These tests use synthetic sources, signals and fake services. Workflow tests
include a temporary loopback HTTP server. Numerical tests need `.[adctools]`;
inspect skipped tests before claiming coverage of the optional backend. No LLM,
provider credentials, SSH server, licensed PDK or solver is needed. Passing these
tests demonstrates tool behavior, not a model's end-to-end reliability or a
real circuit's performance.

Evidence lives in `src/spiceowl/integrations/live.py`,
`src/spiceowl/experiments/`, `src/spiceowl/adctools/` and the named tests. The
older [run contract](run-contract.md) includes design-draft material; proposed
manifest directories there must not be mistaken for files the runtime writes.

## Proposed benchmark for smaller models

Freeze a public synthetic task set, tool schemas, repository/dependency versions
and reference answers before running models. Use complete and deliberately
incomplete specifications, changed hashes, uncertain dispatch, missing signals,
valid performance failures, unresolved spectral masks and rank-deficient fits.
Score missing-information cases as correct only when the model preserves the
gap; a guessed runnable experiment is a failure.

Run each case at least five times per exact model/configuration. Retain prompts,
tool calls, arguments, receipts, versions, token use and latency. Compare the
same deterministic workflow baseline with the agent-assisted route. Measure:

- Correct next-tool/argument rate and valid completion rate on runnable cases.
- Correct refusal-to-infer rate on incomplete or unsupported cases.
- Source/specification hash preservation and duplicate-dispatch count.
- Correct recovery of original plan/job/key after simulated disconnection.
- Metric agreement with analytic references within predeclared tolerances.
- Accuracy for each report dimension, false-pass rate and human interventions.
- Median/tail tool calls, latency and tokens, with per-case repeatability.

Require zero unauthorized dispatches, duplicate dispatches and unsupported
scientific passes in this test set. Report failures and denominators alongside
rates; even zero observed failures does not prove universal safety.

SpiceOwl now ships this frozen 10-case benchmark harness in `src/spiceowl/agent_eval.py`
(`spiceowl eval-agent --baseline compare --markdown`, `POST /api/agent/eval`) paired
with the pure deterministic next-action orchestrator `src/spiceowl/orchestrator.py`
(`spiceowl next-action`, `spiceowl_next_action` tool) and explicit human-reconciled
interrupted-turn recovery in `CodexBridge` (`GET /api/codex/recovery`,
`POST /api/codex/recover`). Real third-party LLM model evaluation still depends on
supplying an explicit model callback or live runtime and does not substitute for
site-specific PDK/simulator qualification.

## Making the next step explicit

The target for a smaller agent is to select a permitted next operation and fill
bounded arguments. Deterministic code should enforce transitions and evidence;
a prompt telling the model to be careful is insufficient as a workflow gate.
The existing registered `workflow` driver implements its fixed sequence. A
unified next-action interface for generic authored testbenches remains proposed.
Do not treat the conceptual stages below as currently exposed API state names.

| Stage | Evidence required before advancing | What a smaller agent needs to do |
| --- | --- | --- |
| Define experiment | Source selection, signal roles, PVT, stimulus/load, analysis, measurement and criteria are explicit | Select a reviewed recipe; request the exact missing fields for a new experiment |
| Resolve dependencies | All required models and relative helpers map to recorded identities; ambiguities stay visible | Select from returned candidates without inferring private model semantics |
| Validate and freeze | Static validation and a fresh site-bound dependency audit pass; exact bytes and parameters are hashed | Preserve the frozen plan and present the applicable review |
| Dispatch | Execution-owner authorization and one persisted submission identity | Call the permitted submission operation once; retain its receipt |
| Observe and collect | Original job identity, terminal status and native collection identity agree | Poll the same job; reconcile uncertain dispatch without creating another key |
| Measure | Dataset identity, units, completeness and algorithm preconditions pass | Invoke the registered algorithm with frozen parameters |
| Report | Each scientific and execution dimension has its own evidence | Explain the returned values, failures and missing evidence |

In a future generic interface, return bounded `missing` fields and permitted
next operations with their argument schemas. Enforce the same restrictions in
the execution service, not only in model-visible descriptions. Set a finite
attempt/time budget and preserve one action's identity across disconnects.
Changing electrical conditions, a model binding or acceptance criteria creates a
new reviewed experiment revision; a diagnosis is not permission to make that
change automatically.

Sampling is part of that frozen definition. Record the startup exclusion, clock
phase, first/last sample, exact count, time tolerance and any row selection with
the raw-artifact digest. The FFT validator checks its supplied array; it does
not prove that the array represents the intended held output or clock phase.
There is no generic clock-phase extraction tool in this package. A missing
sample or an extra endpoint must not be repaired by an undocumented crop or
interpolation.

Reproduction has three different scopes. Replaying analysis uses the same raw
artifact, algorithm version and parameters. Re-running a simulation additionally
requires the source/model/tool versions, site environment, relevant seeds and
sampling definition. Reusing a recipe for another circuit requires validating
its interface and scientific assumptions again. Declare numerical comparison
tolerances before a rerun; this project does not promise bitwise-identical
solver output across different environments.

## 中文：较弱 AI 可以复现到哪一步

已登记的实验能够通过固定工具流程执行；“自动给陌生电路设计正确的实验”仍需
明确的电路语义与人工／专家审阅。把第一次确认的实验保存为带版本和哈希的配方，
后续 AI 就可以负责选配方、补齐明确字段、调用工具、观察原任务和解释报告。

例如，自举栅压驱动模块的输出不能直接当作采样保持输出。要测采样性能，配方
必须明确外接开关、保持电容、实际测量节点、采样相位、记录长度和频谱定义。
对于 AC，相位裕度要求已声明的环路增益与反馈约定；对于 DC，私有模型的 region
编码必须来自明确的模型约定。这些条件缺失时应返回缺项，而不是靠模型猜测补齐。

下一步的验收重点是：先用没有 LLM 的脚本完成受支持配方，再让指定的小模型跑
同一组任务，并注入断线、模型缺失、波形不完整、过期依赖和真实性能不达标等情况。
同时统计正确完成率、缺项识别率、重复提交次数、错误通过次数和人工介入次数。
目前还没有完成这项小模型端到端评测；单元测试通过不等于已经证明任意 AI 都可靠。

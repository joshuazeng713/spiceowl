# Deterministic experiment workflow

`spiceowl workflow` runs a registered experiment through `prepare → run → inspect`. The sequence and scientific checks work without an LLM. An agent can call the same commands and explain their results; it does not have to invent a netlist, infer model terminals, choose measurement criteria or interpret a zero exit code as scientific success.

The automatic transient measurement path implements `inverter-tran` for a selected, verified CMOS inverter, and `prepared-transient` for a registered cell or cutout with explicit signal windows. SpiceOwl compiles supplied source bytes and evaluates PSF ASCII. CircuitGit owns the committed source, private registration, immutable plan, remote execution and native collection. The current command route uses standalone Spectre. The existing [live console](standalone-runtime.md), [passive IC cache](ic-workbench.md) and [read-only engine SDK/CLI](real-connections.md#5-read-saved-state-through-the-engine-sdk) retain their documented operations. AI interaction uses [explicit Agent modes](dual-mode-agent.md): standalone has its own Codex window; embedded reuses CircuitGit's existing window.

The additional [`agent-testbench` recipe](agent-testbench.md) compiles a complete
authored TB and explicit criteria for the paired CircuitGit review flow. It does
not inherit the two transient recipes' scientific measurements. Supported saved
data can be measured with an explicitly configured [ADC algorithm](adctools.md);
unsupported postprocessing remains unknown. See [agent reproducibility](agent-reproducibility.md)
for how fixed tool calls differ from designing a new experiment.

## What must be configured first

Use an installed, compatible SpiceOwl/CircuitGit pair and the private [live-client configuration](../examples/live-client.example.json) described in the [connection guide](standalone-runtime.md#prepare-the-connection). The client file and authority key must be owner-private. A working registered SSH pool, remote simulator/license environment and project grant are prerequisites.

The CircuitGit operator registers a recipe ID for the bound project. Its private registration supplies:

- The exact source files and an explicit cell or instance selection. Public examples and tests use original synthetic sources.
- Port roles, absolute supply voltages, temperature and nominal temperature in kelvin, corner, numeric parameters, input stimuli and output loads.
- Stop time, maximum time step, optional strobe period and the required saved signals with units. Current recipes save the full transient; expected coverage is from zero to the configured stop time.
- Sample count or an explicit null where supported, maximum sample gap, voltage bounds, supply tolerance and measurement windows. Inverter recipes also require logic thresholds and both stable input phases; propagation delays are reported without an acceptance threshold.
- Registered model identities, polarity and ordered terminals, remote model/include aliases, dependency revision pins, and the declared operating checks. A familiar-looking model name does not establish its terminal ordering or device type.

The installed grant needs `prepare`, `run` and `inspect`; the current Runner path additionally requires `watch` for run supervision and `collect` for terminal inspection. The operator preserves existing grants while adding these operations. Registering a different project or using another caller does not follow from possession of the client configuration.

The source compiler preserves complete selected instance statements, including every parameter and original line/byte provenance. An instance cutout uses a new wrapper and explicit test conditions; it does not establish in-circuit behavior under the original load. A cell selection includes reachable supplied subcircuits. Unsupported behavior, unresolved parameters, empty interfaces, missing conditions and ambiguous topology produce explicit missing evidence. The compiler does not guess a testbench.

## Run the commands

Replace `RECIPE_ID`, `FULL_40_CHARACTER_GIT_SHA`, `PLAN_ID` and `JOB_ID` below with registered or returned values. The source commit is a full lowercase 40-character Git SHA. A plan ID is the exact returned 64-character digest. Keep the same client configuration throughout.

```bash
spiceowl workflow --client-config /absolute/private/live-client.json prepare --recipe RECIPE_ID --source-commit FULL_40_CHARACTER_GIT_SHA
```

`prepare` reads the registered source at that commit, compiles the explicit recipe, performs a bounded remote dependency audit and freezes the plan. It does not launch the simulator. A ready response has `state: prepared`, a `plan_id` and an empty `missing` list. A blocked response has `state: blocked`, `plan_id: null` and specific missing fields. Resolve those conditions before submitting.

```bash
spiceowl workflow --client-config /absolute/private/live-client.json run --plan-id PLAN_ID
spiceowl workflow --client-config /absolute/private/live-client.json inspect --job-id JOB_ID
```

`run` uses the fixed submission key `experiment-PLAN_ID`, checks eligible hosts against the frozen requirements and returns the existing Runner's job ID. `job_id` and `run_id` identify the same job. An initial queued/submitted receipt confirms a recorded job; it does not confirm simulator completion.

`inspect` is a control operation: it observes that exact remote job and can automatically collect terminal artifacts through the Runner's existing fixed receipt. Its `inspection_state` is `pending`, `working`, `collecting`, `complete` or `uncertain`. A complete response includes separate `report` and `measurements` objects. Repeated inspection refers to the original job; it does not start a replacement experiment.

The combined command calls the same three operations:

```bash
spiceowl workflow --client-config /absolute/private/live-client.json execute --recipe RECIPE_ID --source-commit FULL_40_CHARACTER_GIT_SHA --timeout 600 --poll-seconds 3
```

The JSON result includes `plan_id`, `job_id`, `run_id`, `submission_key`, the last `job` receipt and a workflow `state`. `complete` means collection and deterministic reporting finished. Scientific acceptance is recorded inside the five report dimensions; a completed workflow can contain a performance failure. CLI exits nonzero on `blocked`, `uncertain` or `timed_out`.

`--timeout` bounds how long this invocation waits; it does not cancel a solver or extend server-side execution limits. The command retains the original IDs on timeout and does not prepare another plan or derive a new submission key inside its wait loop. Continue with `inspect --job-id JOB_ID`. Closing the CLI also does not stop the remote solver. CircuitGit must stay available for background supervision; collection occurs when inspection is requested.

## Read the five dimensions

| Report field | Question | Possible conclusions |
| --- | --- | --- |
| `execution_status` | What happened to the simulator process? | queued, running, completed, failed, cancelled, unknown |
| `measurement_validity` | Do the saved data and applied conditions support the requested measurement? | valid, invalid, unknown |
| `functional_performance` | Do valid measurements meet the frozen targets? | pass, fail, unknown |
| `operating_qualification` | What do the declared operating checks establish? | passed_within_scope, violations_detected, unknown |
| `dependency_provenance` | Do observed model/tool dependencies match the pre-execution inventory? | verified_within_scope, incomplete, mismatch, unknown |

Each dimension carries its scope and evidence. Wrong input/supply, missing units, insufficient coverage or sparse windows invalidate the intended measurement. Valid data with an output beyond its threshold gives a performance failure. Supply, stimulus, temperature, corner and measurement definitions remain frozen.

SOA and model warnings are evaluated separately from functional behavior. A scope-limited qualification requires a frozen check scope and complete, consistent diagnostic coverage. Suppressed warnings, ambiguous completion or unknown scale-factor effects prevent an absence-based pass. Dependency evidence distinguishes pre-pinned files from unregistered or mismatching runtime dependencies; a registered directory reference does not certify all files in that directory. Neither dimension certifies an entire fabrication process or PDK.

`measurements` includes sample count, time span, voltage ranges, window results, recipe/PSF digests and any report-only delays. There is no combined overall-qualified-pass flag. Public client output excludes raw source text, private model paths, environment settings and raw backend errors.

## Disconnects and uncertain outcomes

The client writes the run's fixed key to its owner-private request journal before POST. A completed request can return its cached receipt on repeated invocation. If delivery is interrupted or the response identity is inconsistent, the journal preserves `pending` and blocks replay, including attempts to change the key. Keep the journal alongside the same client configuration.

When the run ID was not received, query the exact key returned by `execute` or derived from the saved plan:

```bash
spiceowl workflow --client-config /absolute/private/live-client.json lookup --key experiment-PLAN_ID
```

Lookup checks the existing native project/key mapping. A found mapping can recover the job ID for inspection. An absent mapping does not prove that execution never started. Lookup never clears uncertainty or permits replay; unresolved cases require reconciliation of the existing Runner records. An uncertain inspection stops the combined command and preserves the last job receipt.

Automatic solver restart, relocation, increased memory and numerical/scientific parameter repair remain unimplemented. A requested change to the experiment becomes a newly registered, explicitly reviewed set of conditions. An agent's explanation cannot silently relax acceptance criteria.

## 中文操作说明

先由操作者登记项目、服务器池、模型依赖和明确的实验配方，再运行 `workflow prepare`。这一阶段会读取固定 Git 版本并检查远端依赖；缺少供电、激励、端口映射或测量条件时返回具体缺项，不替用户猜测。`run` 使用计划 ID 对应的固定 key，只启动该计划的原有 Runner 任务。`inspect` 会查询原任务，并在任务结束时自动收集和验收产物，因此它是控制操作。

`workflow execute` 把这三步固定串联起来，不依赖大模型做决策。超时只结束本次等待，并返回原 `plan_id`、`job_id` 和 `submission_key`；之后用同一 `job_id` 继续 `inspect`。提交断线导致结果未知时，保留原 key 并用 `lookup` 查询，不能删除请求记录或换 key 重跑。

报告分别回答五个问题：仿真是否执行完成、测量是否有效、电路性能是否达标、声明范围内的 SOA／模型检查结论、模型与工具依赖是否可追溯。`complete` 只表示收集和报告流程完成；性能失败仍然是有效实验结果。当前实现不会自动改供电、corner、容差或验收窗口来把失败改成通过。

# 仿真故障案例与 Agent 处理边界

检索日期：2026-11-16。范围：Spectre/Virtuoso/ADE 官方技术博客和社区原帖，以及 Linux、SchedMD 原始文档。本文是研究资料与实施建议，**不表示 SpiceOwl 已实现这些恢复功能，也不表示这些故障已在当前服务器上复现**。不保存第三方工程、模型或整段日志。

原帖可能来自旧版本；必须核对实际 simulator、ADE、PDK 和执行环境。社区回复与正式产品保证不同。下文“自动化判断”都是本项目的设计判断，不是来源授予的操作权限，也不是测得的成功率。

证据分类：A = 官方机制或技术说明；B = 原帖有作者解决反馈或专家明确复现/修复验证；C = 专家诊断或建议，缺少完整成功闭环。A 不等于已完成本地集成测试。

动作分级：D = 自动诊断/采集；P = 已有明确授权、预算、身份和输入一致性检查时，可执行受限策略；H = 需要研究者/管理员决定。同一故障可以同时包含 D、P、H 三种动作。

## 16 类案例

### F01 — 客户端断开，任务状态不明〔A；D/P〕

- **来源事实：** Slurm 的 sbatch 在获得 Job ID 后即可退出；任务可能仍排队或继续运行。客户端进程退出与任务终态不是同一个事件。[SchedMD sbatch](https://slurm.schedmd.com/sbatch.html)
- **应取证据：** 原提交凭据、集群/主机、Job ID 或进程启动身份、任务/step 状态、独立日志时间戳。直接 SSH 启动方式需单独确认进程归属，不能套用调度器语义。
- **处理判断：** 自动有界重连并核对同一任务；结果未知时保持 unknown。禁止仅因断连新建一份任务。验收是恢复原任务观测，而非制造新的“运行中”状态。

### F02 — 许可证等待超时，改全局默认值仍无效〔C；D/P/H〕

- **来源事实：** IC23.1 ISR16.19/SpectreX 用户修改 licQueueTimeOut 后，已有测试仍按保存的 900 秒超时。专家解释 maestro 内已有 test 的设置不会自动随新默认值改变。[原帖](https://community.cadence.com/cadence_technology_forums/f/custom-ic-design/65753/disable-license-queue-timeout)
- **应取证据：** 许可证等待消息、具体 test 的实际配置、启动参数、已等待时间；不要只检查 .cdsinit。
- **处理判断：** 可按预批准预算等待、观察和通知；不得把等待当作 solver 崩溃重复提交。调整 test 等待预算需符合策略；默认改成无限等待不合适。验收还要确认真正进入求解阶段。

### F03 — 主机有空闲内存，作业仍发生 OOM〔A；D/P〕

- **来源事实：** 作业/step 的 cgroup 内存上限可触发 OOM；部分 Slurm 配置只杀掉 step 内部分进程，其他进程可能仍在运行。[SchedMD cgroup.conf](https://slurm.schedmd.com/cgroup.conf.html)；[Linux cgroup v2](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html)
- **应取证据：** 真实所属 cgroup 及祖先限制、memory.current/peak/max、memory.events 的 oom/oom_kill 增量、进程和 job/step 终态。仅有退出码 137 或主机空闲内存不能确定原因。
- **处理判断：** 自动告警与取证；确认原任务终态后，可在预算内为新 attempt 增加申请内存或降低后续任务并发。不能擅自修改系统限制、杀其他用户任务或假定被杀进程能原地恢复。重跑仍需验证输入一致和结果有效。

### F04 — 波形或标量结果目录的磁盘/配额耗尽〔B；D/P/H〕

- **来源事实：** 用户提高配额后解决写结果失败。专家指出 maestro 标量数据库可能与波形目录位于不同文件系统；Spectre 的 disk_check 只覆盖其仿真数据写入位置。[配额案例](https://community.cadence.com/cadence_technology_forums/f/mixed-signal-design/57866/simulation-error)
- **应取证据：** 波形、maestro 标量库、临时目录各自的容量与用户配额、增长速率、写入错误；区分空间、inode、权限和配额原因。
- **处理判断：** 自动预检/告警；版本和策略允许时，可预先配置原生磁盘保护。不得默认删除历史结果或取消必需信号保存。释放空间后仍需核验结果完整性，不能看到目录存在就宣告成功。

### F05 — 模型未定义，错误补 include 导致递归/语法错误〔C；D/P/H〕

- **来源事实：** SFE-23 后误包含 cds.lib 产生 SFE-874；把 input.scs 包含到自身产生 SFE-879。专家指出应依据对应 PDK 的模型文件与 section 文档处理。[原帖](https://community.cadence.com/cadence_technology_forums/f/custom-ic-design/36703/error-sfe-23-undefined-model-or-subcircuit-modp-and-modn-and-error-sfe-874)
- **应取证据：** 未定义模型名、include 闭包、文件可读性、PDK 版本、section、输入哈希和执行主机路径映射。
- **处理判断：** 可自动定位缺失项；只有精确、预批准的路径映射可以自动恢复。不能随便找一个同名模型或换 corner 来消掉错误。重新生成输入后必须核对模型身份和实验定义。

### F06 — 无波形其实是网表生成失败〔C；D/P/H〕

- **来源事实：** WIA-1175 是下游表现；2022-05-12 的日志先出现 OSSHNL-109/514，指定 cellview 修改后尚未 Check and Save。普通 Save 不等价；用户还检查了不同于报错对象的 cellview。[原帖](https://community.cadence.com/cadence_technology_forums/f/custom-ic-design/51626/plotting-from-ade-explorer-netl-err)
- **应取证据：** 同一 attempt 的 netlisting 日志、实际 library/cell/view、设计修订与检查状态，而非只看最后一个 GUI 弹窗。
- **处理判断：** 自动追溯错误因果链。在明确授权的设计快照中检查并重新生成网表可纳入策略；不能保存未经确认的交互编辑或换另一 cellview。验收需确认正确对象成功生成完整网表。

### F07 — DC/瞬态不收敛来自接线与开关参数〔B；D/H〕

- **来源事实：** 比较器 testbench 的 relay 控制端方向、开关阈值、过低导通电阻和悬空节点造成问题。专家检查网表后给出修改，用户最终反馈成功。[原帖](https://community.cadence.com/cadence_technology_forums/f/custom-ic-design/38494/convergence-problems-using-analoglib-switch-dc-simulation)
- **应取证据：** 报错节点周边拓扑、控制极性、阈值、阻值量级、DC path、预期工作方式。
- **处理判断：** Agent 可生成具体诊断与可审阅 diff；不能自动改连接、加电阻或套用原帖数值。此类改动可能改变电路，应形成经确认的新设计/实验版本，并检查功能而不仅是收敛。

### F08 — 瞬态时间步塌缩 / SPECTRE-16192〔C；D/P/H〕

- **来源事实：** 原帖时间步反复缩小至极小值后终止。Andrew Beckett 建议用 +diagnose 定位不连续或可能有问题的 Verilog-A 模型；没有给出通用修复。[原帖](https://community.cadence.com/cadence_technology_forums/f/custom-ic-design/45162/spectre-fails-to-finish-transient-sims)
- **应取证据：** 分析阶段、仿真时间/墙钟时间、最小步长、limiting node、Newton 失败统计、版本和模型来源。
- **处理判断：** 自动归因候选与诊断采集；启用额外诊断的新 attempt 也需要执行预算。reltol、cmin/gmin、积分法和行为模型修改应审批或受明确实验策略约束。官方性能说明也提醒不合理的 gmin/cmin 会影响精度。[Spectre APS 技术说明](https://community.cadence.com/cadence_blogs_8/b/cic/posts/spectre-optimizing-spectre-aps-performance)

### F09 — PSS 稳态或分析方式不匹配〔C；D/H〕

- **来源事实：** PLL/VCO 讨论中专家要求检查是否真正达到周期稳态、tstab 是否充分，以及整数锁定 PLL 是否应按 driven circuit 设置，不能只因内含 VCO 就选 oscillator。[PLL/PSS 讨论](https://community.cadence.com/cadence_technology_forums/f/rf-design/41458/vco-veriloga-model-not-running-pss)
- **应取证据：** driven/autonomous 选择、基频和分频关系、稳态判据、tstab 波形、PSS 收敛残差、初始条件。另一讨论说明 oscic 自动设置与手工 IC 的作用并不相同，需按版本理解。[IC 设置讨论](https://community.cadence.com/cadence_technology_forums/f/custom-ic-design/49344/why-there-is-no-convergence-in-pss-and-transient-if-i-enable-calculate-initial-conditions-ic-automatically)
- **处理判断：** Agent 可比较实际设置与已定义的实验意图；不能自行选择基频、强加起振条件或更换分析类型。延长稳定时间也需预算及收敛/周期性验收。

### F10 — Verilog-A hidden state 与 PSS/噪声结果问题〔C；D/H〕

- **来源事实：** 一例采样滤波模型通过忽略 hidden state 试图运行后，噪声输出异常。专家指出仅在时钟事件更新且事件间保留的变量是真实隐藏状态，忽略检查可能得到错误结果。[原帖](https://community.cadence.com/cadence_technology_forums/f/custom-ic-design/57238/sum-of-sampled_noise-output-spectrum-how-to-access-the-phase-information/1389807)
- **应取证据：** 被点名变量的赋值路径/状态保持行为、分析兼容性、模型版本、预期噪声传递模型。
- **处理判断：** 自动标记分析/模型不兼容并提供改写建议。不得自动添加 ignore_hidden_state 或屏蔽检查来制造通过。模型重写与分析选择必须由研究者验证。

### F11 — 必需信号没有保存，wildcard 匹配为零〔C；D/P〕

- **来源事实：** 子电路内部信号未被保存；原因是 depth 从设计顶层计算，原例需要深度 2 而非 1。[原帖](https://community.cadence.com/cadence_technology_forums/f/custom-ic-design/65791/wildcard-does-not-match-any-net-in-spectre-save-statement)
- **应取证据：** required-signals 清单、实际层级映射、save 配置、wildcard match summary、结果目录内真实信号目录。
- **处理判断：** 可在启动后尽早检出并标记测量输入不足；按已批准清单修复保存配置和重跑可列为 P。历史数据不能补造；也不能默认 save all，否则可能引入磁盘问题。

### F12 — FFT/PSD 的采样插值误差〔A；D/P/H〕

- **来源事实：** 2022-04-28 官方技术博客解释 strobe 可以令求解时间点落在指定 FFT 采样时刻；maxstep 只限制步长，不保证采样对齐。[官方技术说明](https://community.cadence.com/cadence_blogs_8/b/cic/posts/spectre-tech-tips-using-the-spectre-strobe-feature)
- **应取证据：** 时间向量、strobe 参数、采样时钟/相位、FFT 起止时间、点数、窗函数、稳定时间和精度目标。
- **处理判断：** 自动检查配置一致性及插值风险。缺少 strobe 不应一律判无效；已有充分数据可按冻结配方重新后处理。改变采样窗口/相位/求解精度需批准，不能为提高 SNDR 而挑选更好看的窗口。

### F13 — ADE 中的表达式数值精度变化造成频谱泄漏〔B；D/P/H〕

- **来源事实：** DFT 表达式从 ViVA/Calculator 传到 ADE 后，结束时间发生舍入。专家明确复现，建议用 VAR() 共享设计变量，并让 transient strobe 与 DFT 使用一致变量。[原帖](https://community.cadence.com/cadence_technology_forums/f/custom-ic-design/65814/significant-digits-in-ade-assembler-output-expressions)
- **应取证据：** 原始完整表达式、实际保存表达式、解析数值、变量值、软件版本和同一波形上的重算差异。
- **处理判断：** 自动检测配方漂移；若原始批准值明确，可恢复等价表达式并生成带来源的新派生结果。不得更换窗宽或规格阈值。该讨论不能证明所有当前版本都有相同缺陷。

### F14 — Monte Carlo 点重放却变成 nominal 结果〔B；D/H〕

- **来源事实：** IC6.1.8 用户重放 statistical corner 得到 nominal 结果，最终发现使用了普通模型而非 MC 模型；专家也说明 sequence/value-based corner 与设计变更有不同关系。[原帖](https://community.cadence.com/cadence_technology_forums/f/custom-ic-design/45550/monte-carlo-re-running-a-mc-point-as-statistical-corner/1368764)
- **应取证据：** 实际 include/section、模型版本、MC 模式、seed/sample、保存的统计参数、corner 类型和器件集合。
- **处理判断：** 自动标记实际运行与统计实验不一致，阻止混入合格样本。只能恢复已冻结且获授权的配置；不能换 seed、删掉坏样本或猜模型来提高 yield。

### F15 — SPECTRE-18 / segmentation fault〔C；D/H〕

- **来源事实：** 有用户在增加物理内存后出现段错误；专家建议提供版本与可复现 testcase 联系支持，原帖没有证实“增加内存导致”的根因。[原帖](https://community.cadence.com/cadence_technology_forums/f/custom-ic-design/52388/spectre-segmentation-fault/1385233)
- **应取证据：** fatal 日志、退出信号、OOM 证据、可执行版本、输入身份、故障阶段；分别检查 job、step、solver 的退出结果。[SchedMD 退出码说明](https://slurm.schedmd.com/job_exit_code.html)
- **处理判断：** 自动保存脱敏诊断包；不能将所有崩溃都当作 OOM，或无限重复启动。工具版本切换、模型修改、对外发送 testcase 均须单独授权。公开社区文本不是上传私有 PDK 的许可。

### F16 — 包装程序未退出或前端挂起，被误判为 solver 卡死〔B/C；D/P/H〕

- **来源事实：** OCEAN -nograph 案例通过在脚本末尾正确退出解决外层循环停滞；另一个 runams 案例在 netlisting 后占用高 CPU 却挂起。[OCEAN 案例](https://community.cadence.com/cadence_technology_forums/f/custom-ic-skill/21028/screen-with-ocean)；[runams 案例](https://community.cadence.com/cadence_technology_forums/f/mixed-signal-design/57939/runams-hangs)
- **应取证据：** 网表生成、solver、后处理、包装进程的独立状态；仿真时间进展、日志增量和进程启动身份。
- **处理判断：** 自动 lint 已授权的脚本模板、分阶段告警；无日志或高 CPU 均不足以决定 kill。改正模板后可按策略用于下一 attempt；现有运行的终止/重新执行必须确认归属和结果状态。

## 原生恢复能力：应接入，但不能假装通用

Spectre 的 saveperiod/saveclock/savetime/savefile 和 recover 是值得验证的原生路线。专家指出 writefinal/readns 不保留完整状态；同一讨论后续还出现 recover 崩溃及 ExplorerRun.0 覆盖旧结果的问题，因此不能把最初的建议当作端到端成功证明。[Save/Recover 讨论](https://community.cadence.com/cadence_technology_forums/f/custom-ic-design/52184/extending-a-tran-sim)

本次检索也发现 Cadence 2025-01-27 更新的 [Using Spectre Save-Recover](https://support1.cadence.com/public/docs/content/20470407.html) 公开元数据，列出了若干测试版本；正文抓取返回 403，因此这里不声称读过完整 RAK，也不根据元数据直接生成执行参数。

建议的恢复前置检查（项目设计）：

1. 提前启用并验证原生 state 保存，记录实际路径、时间和完整性；PSF 波形不当作 state 文件。
2. 绑定源 attempt、输入/模型/分析、simulator 与 adapter 版本；默认不承诺跨版本/主机兼容。
3. 确认原执行已结束或按原生管理器规则进入可恢复状态；unknown 不直接重提。
4. 新 attempt 使用独立目录，保留原结果与 state；检验恢复边界附近的连续性和测量窗口。
5. 对失效、截断、不匹配 state 明确拒绝恢复；无可用 state 时按重跑处理。

## 给 Agent 的证据包

以下是建议的接口形状，不是当前已实现的命令，也不是向 Agent 授予任意 shell 权限：

```json
{
  "schema": "spiceowl.failure-evidence.draft1",
  "synthetic": true,
  "experiment_id": "example-experiment",
  "attempt_id": "example-attempt-1",
  "submission_ref": "example-original-receipt",
  "phase": "transient",
  "execution_state": "unknown",
  "observation_state": "stale",
  "error_codes": [],
  "evidence_refs": ["example-log-slice", "example-process-observation"],
  "input_manifest_ref": "example-frozen-inputs",
  "measurement_contract_ref": "example-required-signals-and-window",
  "policy": {
    "allowed_actions": ["inspect_original_execution"],
    "max_new_attempts": 0,
    "circuit_changes_allowed": false
  }
}
```

真实证据还应有来源、带时区的观测时间、日志字节区间/哈希、退出码/信号、CPU/内存/配额、剩余预算、版本和未掌握的信息。敏感工程与 PDK 默认保留为本地引用；给模型的日志和网表上下文需按用户的数据策略限量提供。

Agent 返回候选原因、支持与反对证据、缺失检查、建议动作、精确变更项、预期影响和验收标准。规则层重新验证身份、时间新鲜度、预算和允许字段；模型的文字或社区帖子不能替代授权。

## 推荐实施顺序与验收

1. **先做只读故障识别：** F01/F02/F03/F04/F05/F06/F11。建立 netlisting → licensing → solver → collection 的阶段记录，保留首因及下游错误关系；不把最后一条错误机械当根因。
2. **再做实验有效性检查：** F12/F13/F14。以冻结的信号、时间、模型和 MC 约定对照实际产物，不以图形“看起来正确”验收。
3. **再加有限恢复：** 有界重连、预批准资源重试、原生 save/recover。每次动作有稳定标识，新运行有独立 attempt；失败次数/资源/总耗时均有上限。
4. **复杂问题先保留建议模式：** F07/F08/F09/F10/F15/F16 的模型、电路、数值和进程终止修改。逐项完成版本匹配和故障注入测试后，才扩大自动执行范围。

离线回放应包含：断连但任务仍运行、OOM 仅杀掉子进程、标量库配额耗尽但波形盘有空闲、网表失败后又报无波形、重复事件、乱序/过期观测、错误 MC 模型、FFT 配方漂移、缺失 state、模型输入中的指令式文字。以上是计划测试集，不是已执行结果。

验收指标建议是：故障分类的 precision/recall、拒绝错误重提的比例、重复提交数、恢复耗时、资源开销、输入/测量约定保持率，以及实际恢复后的测量有效性。未建立基准前，不给出“自动解决百分之多少”的承诺。

运行结束应继续分别报告：执行终态、观测可信度、测量有效性、规格是否满足。电路真实不达标的样本必须保留，不能通过恢复机制挑选成合格样本。

与现有实现的关系见 [架构](../architecture.md)、[运行约定](../run-contract.md)、[路线图](../roadmap.md)。本次仅新增研究文档，未提交、取消、重启或修改任何真实仿真。

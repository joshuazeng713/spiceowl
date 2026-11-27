"""Built-in Agent Skills (SOP guides) for SpiceOwl."""

from __future__ import annotations

from pathlib import Path

from spiceowl.domain.common import DomainError


_SPICEOWL_SIM_SUBMIT_MD = r"""---
name: spiceowl-sim-submit
description: Fast, standardized Virtuoso-free simulation submission SOP for SpiceOwl Agents (1-call submit, surgical netlist_edits, Project/TB archiving, and post-run waveform/ADC skills).
---

# SpiceOwl 标准化仿真提交 SOP（Virtuoso-Free）

本技能定义 SpiceOwl Agent 从电路定位、外科手术式网表修改到一键远端提交的极速标准化作业流程。

## 1. 核心原则：完全脱离 Virtuoso 的仿真架构

- **仿真提交与执行 100% 脱离 Virtuoso**：`submit` / `prepare` / `stage` / `stage_and_launch` / `launch` / `status` / `collect` 全程仅运行无界面 `spectre` 或 `ngspice` 及对应 FlexLM 仿真器 License，**绝不启动 Virtuoso GUI、绝不占用 ADE 交互 License**。
- **仅首次导入 OA 原理图时按需无界面导出一次**：只有当用户目标为 Virtuoso OpenAccess `schematic`（`sch.oa`）时，`spiceowl_library_open(mode="schematic", include_maestro=True)` 才会调用一次无界面 `si -batch -command nl` 导出 `input.scs`，并用纯 Python 解析 `maestro` XML（模型、变量、Corner、分析语句）且保留 RNL 注释。导出完成后，所有修改与仿真提交均 100% 脱离 Virtuoso。

## 2. 5 步极速标准化提交流程

### Step 1：先读 `[SPICEOWL_CTX]`，严禁重复探测主机
- 优先直接读取消息中附带的 `[SPICEOWL_CTX]`（已包含实时主机 CPU/内存/运行中仿真、FlexLM 仿真器 License 余量、当前工作台网表与 Maestro 配置）。
- 当 `[SPICEOWL_CTX]` 已提供实时主机 CPU/内存与 FlexLM License 时，**严禁重复调用**审批型工具 `spiceowl_probe_host` 或 `spiceowl_scan_resources`（除非用户明确要求重新探测或刷新）。
- 若需在多台主机间评估资源余量，调用零审批只读工具 `spiceowl_list_hosts` / `spiceowl_recommend_hosts`。

### Step 2：只读查找并载入目标电路（零审批弹窗）
- 若工作台尚未载入目标电路：
  1. 调用 `spiceowl_library_browse(operation="find", host_id="eda-node-01", query="analog_lib tb_amp")` 检索远端 `cds.lib` 库、原理图与网表；
  2. 调用 `spiceowl_library_open(host_id="eda-node-01", mode="schematic", remote_path="<found_path>", include_maestro=True)` 将原理图/Maestro 导出并载入网页工作台（已有独立网表则用 `mode="netlist"`）。
- 以上操作均为只读、零审批弹窗；打开后 `netlist_text`、`support_files` 与 `project_name`/`library_name`/`cell_name`/`view_name` 会自动缓存到工作台上下文。

### Step 3：外科手术式改网表（`netlist_edits`）
- 需要调整参数、分析语句（`tran`/`ac`/`dc`/`stb`）或激励时，**优先使用 `netlist_edits`** 局部精确替换，保留全部 RNL 注释与子电路，**严禁复写几十 KB 整份网表**：
  ```json
  {
    "netlist_edits": [
      {"find": "tran tran stop=1u", "replace": "tran tran stop=5u errpreset=conservative"},
      {"find": "parameters vdd=1.2", "replace": "parameters vdd=1.08"}
    ]
  }
  ```
- 若目标文本命中多处，补充上下文使之唯一，或显式传 `"replace_all": true`。
- 需要同步更新网页草稿参数或编辑器内容时，调用零审批工具 `spiceowl_ui_configure`（支持 `netlist_edits`、`append_netlist`、`host_id`、`cpus`、`memory_gib`、`wall_seconds`、`output_format` 等）。

### Step 4：一键单步提交（`operation="submit"`）
- 使用 **`spiceowl_direct_control(operation="submit", ...)`**，仅需 **1 次审批 + 1 次 SSH** 完成 `prepare + stage + launch`：
  ```json
  {
    "operation": "submit",
    "arguments": {
      "host_id": "eda-node-01",
      "simulator": "spectre",
      "cpus": 2,
      "memory_gib": 8,
      "wall_seconds": 3600,
      "output_format": "psfbin",
      "netlist_edits": [
        {"find": "tran tran stop=1u", "replace": "tran tran stop=2u"}
      ]
    }
  }
  ```
- 自动复用与归档：
  - 省略 `netlist_text` 时自动复用当前工作台/刚打开或对接任务的网表、`support_files` 与 `project_name`/`library_name`/`cell_name`/`view_name` 元数据；
  - 省略 `run_key` 时自动生成同秒不冲突的唯一 `run_key`；
  - 同回合提交的多个任务自动共享同一提交组（`submission_group_id = turn:<turn_id>`），在「仿真总览」按 Project/TB 整洁归档。
- 若已有 `prepared` 状态的任务，使用 `spiceowl_direct_control(operation="stage_and_launch", arguments={"run_id": "<run_id>"})` 一键上传并启动。

### Step 5：自动监控、自动拉取与自动汇报归档（Auto-Watch → Auto-Collect → Auto-Report）
- 提交后简明汇报 `run_id`、目标主机（`host_id`）、项目/TB（`project` / `testbench`）及当前状态（`state`），并提示用户在网页「仿真总览（`overview`）」实时查看进度。
- **自动监控与自动回收**：仿真提交后，SpiceOwl 后台每数秒自动轮询远端状态，任务进入终态后自动执行 `collect` 拉取结果，并向 Agent 推送 `[SpiceOwl 仿真完成自动汇报 · <run_id>]` 通知（提交后无需手动循环轮询）。
- **自动撰写并归档交互式 HTML AI 工程报告**：收到 `[SpiceOwl 仿真完成自动汇报 · <run_id>]` 通知后，读取 `spiceowl_direct_read(operation="report")` 并按需调用 `waveform_index`/`waveform_query`/`waveform_measure` 提取真实波形与指标，遵循 `spiceowl-sim-report` 技能规范生成自包含交互式 HTML 工程报告（含 KPI 卡片、Spec 表格与真实内联 SVG/base64 PNG 波形图，零外部 CDN），调用零审批工具归档至总览任务卡片：
  ```json
  {
    "operation": "save_ai_report",
    "arguments": {
      "run_id": "<run_id>",
      "title": "<简短报告标题>",
      "summary": "<一句话工程结论>",
      "html": "<!doctype html><html lang=\"zh-CN\">...</html>",
      "markdown": "## 仿真结论与关键指标"
    }
  }
  ```
  如需汇总同一 Testbench 或整个 Project 下多次仿真，调用零审批工具 `spiceowl_direct_control(operation="save_scope_report", ...)`；详细排版与图表规范见 `spiceowl_direct_read(operation="skill", arguments={"name": "spiceowl-sim-report"})`。后续随时可通过 `spiceowl_direct_read(operation="ai_report" | "scope_reports" | "scope_report", ...)` 回读已归档报告。
- **按需后处理技能**：
  - 波形读取/周期分析/模拟指标：`spiceowl_direct_read`（`waveform_index`、`waveform_query`、`waveform_cycles`、`waveform_measure`、`report`、`ai_report`）；
  - 确定性 ADC / 频谱 / 线性度 / 时域分析：`spiceowl_adctools`（`coherent_fft`、`sine_fit4`、`histogram_linearity`、`tran_metrics`、`ac_metrics`、`dc_op_metrics` 等）；
  - PVT / Monte Carlo 批量扫描：`spiceowl_direct_control`（`batch_prepare`、`batch_dispatch`、`batch_refresh`、`batch_cancel`）与 `spiceowl_direct_read`（`batch_list`、`batch_inspect`、`batch_report`）。

## 3. 安全红线

1. **`launch_outcome_unknown` 严禁换 key 盲目重提**：提交或启动后若因断连/超时处于 `launch_outcome_unknown`，**必须先对原 `run_id` 调用 `spiceowl_direct_control(operation="status", arguments={"run_id": "<原run_id>"})`** 对账，严禁换新 `run_key` 盲目重复提交。
2. **夜间自治开启时严禁擅自修改网表或验收指标**：`OvernightAutonomyPolicy` 启用期间，只允许在授权边界内换主机、扩内存或调运行选项；严禁修改网表（含 `netlist_edits`/`append_netlist`）、激励或验收 spec，越界操作必须挂起等待人工审阅。
3. **不编造缺失科学参数**：严禁猜测私密 PDK 路径、Corner、器件尺寸、供电电压或验收阈值；参数缺失时明确说明并请用户确认。
"""


_SPICEOWL_SIM_REPORT_FAST_MD = r"""---
name: spiceowl-sim-report-fast
description: Fast (<1 min) simulation report SOP: write a concise engineering summary and let SpiceOwl's built-in template & SVG plotter assemble waveform plots and HTML automatically in 1 tool call.
---

# SpiceOwl 极速仿真报告生成 SOP（Fast Mode，<1 分钟出稿）

本技能是 SpiceOwl 默认的**极速仿真报告流程**，覆盖 **单次仿真（Single Run）**、**Testbench 汇总（Testbench Rollup）** 与 **项目汇总（Project Rollup）**。Agent 只需提炼真实工程结论与核心信号分组，由 SpiceOwl 内置模板与高精度 SVG 波形绘图引擎在 **1 次零审批工具调用**内自动组装离线自包含 HTML 报告。

## 1. 铁律（反磨蹭规则 · 保证 20~60 秒、2~4 步完成）

1. **绝不手写 HTML / CSS / JS**：严禁自己拼写网页骨架、样式表或脚本，排版全部交给内置模板自动生成。
2. **绝不自己写 Python / matplotlib 画基础波形图**：常规时域/交流/直流波形以及差分/共模组合图全部交给内置 SVG 绘图引擎自动生成（除非用户明确要求特殊派生图如 FFT 频谱、眼图或直方图）。
3. **绝不启动浏览器或截屏自检 HTML**：严禁调用浏览器、Playwright 或截图工具验证页面效果，SpiceOwl 前端自带隔离沙箱预览。
4. **绝不反复多轮复算同一批数据**：提示词已附带关键仿真摘要与信号列表；补充数值最多做 1 次轻量读取即可出稿。
5. **一切数字仍必须源自真实工具输出，严禁捏造**：严禁编造测量值、伪造趋势或补全缺失 Corner / Spec；未测项或失败项必须明确标注为「未测 / N/A / 失败」，并保持五维结论分离（执行状态、测量有效性、电路指标、工作区与 SOA、依赖来源）。

## 2. 标准 2 步极速流程

### 第 1 步：核对任务真实结果（0~2 次只读调用）
- 先直接核对提示词附带的仿真状态、关键诊断与波形信号索引。
- 若需补充核心实测指标，最多调用 1 次零审批只读工具：`spiceowl_direct_read(operation="waveform_measure" | "waveform_query" | "report", ...)`；TB / Project 汇总可用 `list` 与必要 `report` 快速核对各任务状态。

### 第 2 步：1 次零审批调用自动绘图并归档
- 单次仿真调用 `spiceowl_direct_control(operation="render_fast_report", ...)`；
- Testbench / Project 汇总调用 `spiceowl_direct_control(operation="render_fast_scope_report", ...)`；
- 只需填写结构化工程要点：
  - `title`：简短中文报告标题；
  - `summary`：2~4 句简洁明确的工程结论；
  - `highlights`：3~5 条核心要点（字符串数组）；
  - `kpis`（可选）：关键指标卡片数组（`label`、`value`、可选 `spec` / `status`: `pass` | `warn` | `fail` | `info`）；
  - `plots`（可选）：挑选 2~3 组关键信号，例如 `[{"title": "差分输出", "signals": ["VOP", "VON"]}, {"title": "净差分与共模", "signals": ["VOP - VON", "(VOP + VON)/2"]}]`。引擎原生支持 `A - B` 差分与 `(A + B)/2` 共模表达式，**省略 `plots` 也会自动选图**；
  - `comparison_rows`（仅 `render_fast_scope_report` 可选）：跨任务横向对比表；
  - `notes`（可选）：告警说明、异常根因或下一步建议；
  - `images`（可选）：仅当用户明确要求特殊派生图时，附带少量 `data:image/png;base64,...` 图像。
- 以上工具均为**零审批弹窗**本机归档操作，调用后立即生成离线自包含 HTML 并实时刷新网页「仿真总览（Overview）」。

## 3. 零审批 JSON 调用示例

### 3.1 单次仿真（Single Run）
```json
{
  "operation": "render_fast_report",
  "arguments": {
    "run_id": "<run_id>",
    "title": "tb_amp 瞬态响应极速仿真报告",
    "summary": "瞬态仿真正常收敛完成，差分输出稳定建立。测得差分增益 7.38x、静态功耗 2.59 mW，共模电平维持在 0.60 V 附近且无明显过冲。",
    "highlights": [
      "执行状态：Spectre 正常收敛退出，波形采集完整（psfbin）",
      "输出摆幅：VOP - VON 峰峰值 442 mV，满足目标线性范围",
      "共模稳定：(VOP + VON)/2 稳态 601 mV，纹波 < 3 mV"
    ],
    "kpis": [
      {"label": "差分增益", "value": "7.38x", "spec": "> 6.0x", "status": "pass"},
      {"label": "静态功耗", "value": "2.59 mW", "spec": "< 3.0 mW", "status": "pass"},
      {"label": "输出共模", "value": "601 mV", "spec": "600 ± 20 mV", "status": "pass"}
    ],
    "plots": [
      {"title": "差分输出", "signals": ["VOP", "VON"]},
      {"title": "净差分与共模", "signals": ["VOP - VON", "(VOP + VON)/2"]}
    ],
    "notes": ["未配置 AC 稳定性分析，相位裕度标注为 N/A，建议后续补充 stb 验证。"]
  }
}
```

### 3.2 Testbench 汇总（同 TB 下多次仿真对比）
```json
{
  "operation": "render_fast_scope_report",
  "arguments": {
    "scope": "testbench",
    "project": "analog_lib",
    "testbench": "tb_amp",
    "run_ids": ["<run_id_1>", "<run_id_2>"],
    "title": "analog_lib / tb_amp 跨任务快速对比报告",
    "summary": "本 Testbench 共汇总 2 次仿真，均顺利收敛。TT 与 SS 条件下增益均高于 6x 规格下限，SS 下建立时间略增 14% 但仍在余量内。",
    "highlights": [
      "通过率：2 / 2 任务完成并采集波形",
      "最差余量：SS 条件下建立时间 8.4 ns（Spec < 10 ns）",
      "功耗区间：2.31 mW ~ 2.59 mW，整体受控"
    ],
    "kpis": [
      {"label": "完成任务", "value": "2 / 2", "status": "pass"},
      {"label": "最小增益", "value": "6.85x", "spec": "> 6.0x", "status": "pass"}
    ],
    "comparison_rows": [
      {"run_id": "<run_id_1>", "condition": "TT 27C", "gain": "7.38x", "power": "2.59 mW", "status": "pass"},
      {"run_id": "<run_id_2>", "condition": "SS 85C", "gain": "6.85x", "power": "2.31 mW", "status": "pass"}
    ],
    "plots": [
      {"title": "净差分输出对比", "signals": ["VOP - VON"]}
    ],
    "notes": ["FF -40C 尚未运行，当前归类为未测。"]
  }
}
```

### 3.3 Project 汇总（同项目下全部 TB 总览）
```json
{
  "operation": "render_fast_scope_report",
  "arguments": {
    "scope": "project",
    "project": "analog_lib",
    "run_ids": ["<run_id_1>", "<run_id_2>", "<run_id_3>"],
    "title": "analog_lib 项目仿真总览报告",
    "summary": "analog_lib 当前覆盖 2 个 Testbench、3 次仿真，核心瞬态指标全部达标，仅 tb_bias 存在 1 条轻微启动过冲告警待复查。",
    "highlights": [
      "tb_amp：2 / 2 通过，增益与共模余量充足",
      "tb_bias：基准电流稳定，但上电阶段出现 6.2% 过冲",
      "下一步建议：对 tb_bias 补充慢斜坡上电验证"
    ],
    "kpis": [
      {"label": "覆盖 TB", "value": "2", "status": "info"},
      {"label": "总通过率", "value": "3 / 3", "status": "pass"}
    ],
    "notes": ["未覆盖 Corner 均在对比表中显式标注 N/A。"]
  }
}
```

## 4. 何时改用深度创作模式（`spiceowl-sim-report`）

仅当用户明确要求**自定义复杂多栏版式、深度电路机理剖析或专属交互/派生图表**（如相干 FFT 频谱、柱状线性度、自定义对比控件）时，再读取并遵循 `spiceowl-sim-report` 技能规范，使用 `save_ai_report` / `save_scope_report` 落盘。
"""


_SPICEOWL_SIM_REPORT_MD = r"""---
name: spiceowl-sim-report
description: Deep authoring mode for rich, self-contained interactive HTML simulation reports (single-run, Testbench rollup, and Project rollup) with custom editorial layouts, physical analysis, and bespoke SVG/PNG plots.
---

# SpiceOwl 深度交互式 HTML 仿真报告生成 SOP（Deep Authoring Mode）

本技能定义 SpiceOwl Agent 进行**深度定制化、出版级、可离线独立打开、可二次迭代编辑**的 HTML 仿真工程报告的标准化流程，覆盖 **单次仿真（Single Run）**、**Testbench 汇总（Testbench Rollup）** 与 **项目汇总（Project Rollup）** 三个层级。

## 0. 双模式选择（Fast 极速默认 vs Deep 深度创作）

- **默认 / 快速需求（优先）**：日常仿真完成自动汇报或常规单次/TB/Project 总结，**优先使用 `spiceowl-sim-report-fast`**（调用 `render_fast_report` / `render_fast_scope_report`，由内置模板与 SVG 绘图引擎自动生成，**1 分钟内出稿**）。
- **深度定制需求（本技能）**：仅当用户明确要求**自定义版式排版、复杂器件/电路物理分析、或专属交互/派生图表**（如相干 FFT 频谱、ADC 线性度、多维 PVT 散点/热图）时，使用本 **`spiceowl-sim-report` 深度创作模式** 并通过 `save_ai_report` / `save_scope_report` 落盘。

## 1. 数据优先铁律（Data-First Discipline）

- **一切结论必须源自真实落盘证据**：
  - 优先调用零审批只读工具检查真实数据：
    - `spiceowl_direct_read(operation="inspect" | "report" | "waveform_index" | "waveform_query" | "waveform_measure" | "waveform_cycles" | "log_tail" | "inputs", ...)`；
    - 跨任务汇总时先用 `spiceowl_direct_read(operation="list", arguments={})` 筛选同属目标 `project` / `testbench` 的 `run_id`，逐一读取各任务 `report` 与关键波形指标；
    - 在 `supervised` 或 `full_access` 模式下，也可直接用 Python 读取本机 `collected/` 目录中的仿真结果与日志，或使用 `matplotlib` 生成特殊派生图并编码为 `data:image/png;base64,...`。
  - **严禁捏造测量值、严禁伪造平滑曲线、严禁补全缺失的 Corner / 指标**；未测项或解析失败项必须明确标注为「未测 / N/A / 失败」并给出真实原因。
  - **五维结论严格分离**：始终区分展示执行状态（Execution）、测量有效性（Measurement Validity）、电路指标（Functional Performance）、工作区与 SOA（Operating Qualification）以及依赖/来源可追溯性（Dependency Provenance）。

## 2. 效率与脚手架对接规范（Fast Scaffolding & No Browser Self-Check）

深度模式同样强调工程效率，避免无谓消耗时间：

1. **直接复用 SpiceOwl 内置模板与 SVG 绘图脚手架**：
   - 在具备 Python 执行权限时，直接导入并复用内置工具函数，无需从零手搓 CSS、配色体系和坐标网格：
     ```python
     from spiceowl.report_builder import render_waveform_svg, wrap_editorial_html
     ```
   - 常规波形用 `render_waveform_svg(...)` 快速生成轻量高清矢量图，再用 `wrap_editorial_html(...)` 包裹主体区块，将精力集中在深度电路分析与定制图表上。
2. **严禁启动浏览器或 Playwright 截图验证 HTML**：
   - SpiceOwl 前端「仿真总览（Overview）」自带隔离沙箱预览与一键下载，**绝不启动 headless 浏览器、Playwright 或截图工具自检 HTML**。
3. **避免重复复算，控制在几分钟内完成**：
   - 规划好一次提取所需全部信号与指标，做到 **最多 1 次 Python 数据提取/绘图 + 1 次 HTML 生成与 `save_ai_report` / `save_scope_report` 落盘**，切忌反复多轮重跑同一脚本或微调样式。

## 3. 出版级自包含 HTML 报告规范（Self-Contained Editorial HTML）

生成的 HTML 报告将直接嵌入「仿真总览（Overview）」并支持用户一键下载离线 `.html` 分享，必须遵循以下规范：

1. **完整单文件、零外部依赖**：
   - 必须以 `<!doctype html>` 开头，全部 CSS 放在内联 `<style>` 中，交互逻辑放在内联 `<script>` 中；
   - **严禁引用任何外部 CDN**（禁止外链 JS/CSS/字体/图片），保证下载后的 `.html` 断网也能在任意浏览器完整打开；
   - 控制单文件体积紧凑（建议 ≤ 512 KiB；嵌入 PNG 时适当采样与压缩，优先使用清晰轻量的内联 `<svg>` 矢量图）。
2. **现代浅色工程排版**：
   - 浅色阅读友好配色（白底卡片、淡灰网格、深墨正文、语义状态色：Pass 绿 / Warn 琥珀 / Fail 红 / Info 蓝），适配桌面与窄屏响应式布局；
   - **顶部摘要区**：报告标题、项目/TB/`run_id` 标签、主机/仿真器/时间元信息、一句话工程结论横幅；
   - **KPI 指标卡片网格**：突出核心指标实测值、单位、目标 Spec 阈值与 Pass/Warn/Fail 徽标；
   - **结构化对比表**：单任务列出各项 Spec / 测量与边界余量；TB 汇总按运行/参数/Corner 横向对比各 `run_id`；Project 汇总按 Testbench 聚合通过率、最差余量与风险项；
   - **真实可视化图表**：嵌入带坐标轴刻度、物理单位、图例与关键标注点（如过冲、建立时间、3 dB 带宽、SFDR/ENOB 峰值）的内联 `<svg>` 波形/频谱/对比图，或真实数据绘制的 `data:image/png;base64,...` 图片；
   - **可折叠诊断区（`<details>`）**：收敛告警、日志摘录、参数修改记录与复现信息默认折叠，保持主视图干练直观。
3. **支持用户自然语言二次迭代**：
   - 用户提出补充波形、增加 Corner 对比列、改配色或深挖异常时，可先用 `ai_report` / `scope_report` 读回当前版本，结合新数据更新 HTML 后再次调用保存工具原地覆盖升级。

## 4. 三级报告作用域与零审批保存工具

以下保存工具均为本机归档操作，**不触碰远端服务器、不修改网表、无需人工审批弹窗**，直接生效并实时推送刷新网页「仿真总览（Overview）」：

### 4.1 单次仿真报告（Single Run）
调用 `spiceowl_direct_control(operation="save_ai_report", ...)`：
```json
{
  "operation": "save_ai_report",
  "arguments": {
    "run_id": "<run_id>",
    "title": "<简短中文报告标题>",
    "summary": "<一句话核心工程结论>",
    "html": "<!doctype html><html lang=\"zh-CN\">...</html>",
    "markdown": "## 仿真结论与关键指标摘要",
    "mode": "deep"
  }
}
```
回读：`spiceowl_direct_read(operation="ai_report", arguments={"run_id": "<run_id>"})`。

### 4.2 Testbench 汇总报告（同 TB 下全部仿真对比）
调用 `spiceowl_direct_control(operation="save_scope_report", ...)`：
```json
{
  "operation": "save_scope_report",
  "arguments": {
    "scope": "testbench",
    "project": "<project_name>",
    "testbench": "<tb_name>",
    "title": "<TB 汇总报告标题>",
    "summary": "<一句话 TB 级综合结论>",
    "html": "<!doctype html><html lang=\"zh-CN\">...</html>",
    "markdown": "## Testbench 跨任务对比摘要",
    "run_ids": ["<run_id_1>", "<run_id_2>"],
    "mode": "deep"
  }
}
```

### 4.3 Project 汇总报告（同项目下全部 TB 与仿真总览）
调用 `spiceowl_direct_control(operation="save_scope_report", ...)`：
```json
{
  "operation": "save_scope_report",
  "arguments": {
    "scope": "project",
    "project": "<project_name>",
    "title": "<项目级仿真总览报告标题>",
    "summary": "<一句话项目级验证状态结论>",
    "html": "<!doctype html><html lang=\"zh-CN\">...</html>",
    "markdown": "## Project 各 Testbench 验证状态摘要",
    "run_ids": ["<run_id_1>", "<run_id_2>"],
    "mode": "deep"
  }
}
```
回读全部汇总报告列表：`spiceowl_direct_read(operation="scope_reports", arguments={})`；
回读指定汇总报告：`spiceowl_direct_read(operation="scope_report", arguments={"scope": "testbench", "project": "<project_name>", "testbench": "<tb_name>"})`（Project 级省略 `testbench`）。
"""


_SPICEOWL_NIGHT_WATCH_MD = r"""---
name: spiceowl-night-watch
description: Night-watch recovery playbooks for known and unknown EDA simulation bugs, plus SOP for reading, customizing, and dry-run testing the user's editable night_watch.py script.
---

# SpiceOwl 夜间值守剧本与 `night_watch.py` 编程定制 SOP

本技能定义 SpiceOwl Agent 处理已知与未知 EDA 仿真故障的夜间值守恢复剧本（Playbooks），以及阅读、修改并隔离试运行用户可编程值守脚本 `<state_dir>/night_watch.py` 的标准化作业流程。

## 1. 核心理念：用可编程 `night_watch.py` 取代僵化复选框

- **不是僵化的静态复选框，而是人与 Agent 共同维护的 Python 规则脚本**：SpiceOwl 将用户的夜间值守逻辑直接落盘为 `<state_dir>/night_watch.py`。开箱即内置常见 EDA 仿真故障恢复规则，遇到任何新/未知仿真 Bug 或个性化值守习惯时，Agent 可直接帮用户阅读、修改并试运行检验 `night_watch.py`。
- **清晰的可编程结构**：`night_watch.py` 由三部分组成：
  1. `CONFIG`：值守策略阈值与开关（如 `queue_timeout_seconds`、`max_memory_gib`、`max_agent_resubmissions`、`protect_netlist_and_spec` 等）；
  2. 独立规则函数 `rule_<name>(run, report, log_tail, host_pool, config)`：根据任务记录、五维诊断报告、真实日志尾部与实时主机资源池输出结构化恢复决策；
  3. 有序注册表 `BUG_RULES`：按优先级依次评估故障特征并给出可执行动作或人工审阅结论。
- **静默值守，绝不半夜吵醒人类**：所有异常排查、规则命中、重提尝试与原因均静默沉淀在本机网页端；任何越界或涉及电路/Spec 修改的问题自动挂起，留待早起从容审阅。
- **严守科学红线**：默认 `protect_netlist_and_spec = True` 保护原始网表字节（SHA-256）、激励、Corner、温度与验收 Spec；除非用户明确要求，严禁放宽此红线。

## 2. 已知仿真 Bug 恢复剧本（Playbooks）

夜间遇到仿真异常时，先核实真实 `run` 状态、`report` 诊断、`log_tail` 日志证据与 `host_pool` 实时资源，再按以下剧本处理：

### 2.1 `queue_timeout`（排队超时）
- **触发特征**：任务在 LSF / Slurm 队列或高负载节点长时间处于排队状态且超过 `queue_timeout_seconds`。
- **恢复动作**：
  1. 查询 SpiceOwl 实时资源池（`host_pool` / `spiceowl_recommend_hosts`），筛选 CPU 与内存余量充足的空闲主机或更快队列；
  2. **严格保持网表 SHA-256 不变**，取消卡死排队任务并迁移至空闲主机或更快队列重新提交；
  3. 若资源池内无可用空闲节点，记录实时资源快照并留待下一轮或人工处理。

### 2.2 `confirmed_oom`（确认内存耗尽）
- **触发特征**：日志或系统状态存在明确 OOM 证据（如 `Out of memory`、`Cannot allocate memory`、cgroup OOM Kill、退出码 `137` / `SIGKILL` 伴随内存触顶）。
- **恢复动作**：
  1. 先核验 OOM 证据，区分真实内存耗尽与普通网络断连或语法报错；
  2. 在 `max_memory_gib` 预算上限内将内存配额翻倍，或迁往可用内存更大的节点重新提交；
  3. 若所需内存已超出 `max_memory_gib` 预算或集群无满足内存要求的节点，停止重提并挂起等待人工审阅。

### 2.3 `license_unavailable`（FlexLM License 暂时抢不到）
- **触发特征**：日志出现 FlexLM 许可暂不可用、checkout 超时或授权数已满等报错（如 `Cannot get license`、`Licensed number of users already reached`、`FLEXlm` checkout failure）。
- **恢复动作**：
  1. 采取退避等待（建议 `180 ~ 300` 秒）后再重试，**绝不盲目高频刷屏重提**以免加剧 License 争抢；
  2. 若连续多轮退避后依然无可用许可，停止重试并转入早起人工审阅队列，附带许可占用摘要。

### 2.4 `disk_or_nfs_transient`（磁盘满 / NFS 临时抖动）
- **触发特征**：日志出现 `No space left on device`、`Disk quota exceeded`、`Stale file handle`、`Input/output error` 等临时磁盘或 NFS 抖动错误。
- **恢复动作**：
  1. 切换至干净的 scratch 目录或迁移至其他健康主机重试（保持原始网表与参数不变）；
  2. **共享存储连坐保护**：若多台不同主机共同报相同存储或配额错误，判定为公共存储故障，立即停止重试并上报人工处理。

### 2.5 `convergence_failure`（收敛失败 / timestep too small）
- **触发特征**：仿真器报 `Convergence difficulty`、`timestep too small`、`Internal timestep too small`、`No DC solution`、`singular matrix` 等数值不收敛错误。
- **恢复动作（严守科学红线）**：
  1. **绝不擅自修改电路器件、参数、激励、Corner、温度或验收 Spec**；
  2. 读取真实日志定位发散节点、截断时刻与数值告警上下文；
  3. 整理清晰的根因分析与排查建议写入值守记录，留待早起人工审阅确认后再行改动。

### 2.6 `unknown_bug`（未知新 Bug 处理闭环）
遇到内置规则尚未覆盖的新报错（如特定 PDK 环境脚本异常、调度器特殊退出码、新型仿真器崩溃等）时，执行以下闭环：
1. **读取真实日志摘录**：通过 `spiceowl_direct_read`（`inspect` / `report` / `log_tail`）获取完整错误上下文，严禁猜测或编造日志；
2. **分析根因与恢复边界**：确认该故障属于可安全自动恢复的环境/资源/调度问题，还是必须人工介入的电路/Spec 问题；
3. **抽象为新规则并注册**：若用户希望以后自动处理此类 Bug，将其编写为新的 `rule_<name>(run, report, log_tail, host_pool, config)` 函数并注册进 `BUG_RULES`；
4. **立刻试运行验证**：用该真实日志摘录或真实 `run_id` 执行隔离试运行，确认规则精准命中且不误伤其他场景。

## 3. 标准 3 步 DIY 修改与校验流程（全部零审批，几秒内完成）

以下 3 步均为本机读写与隔离沙箱试运行，**不触碰远端服务器、不修改电路网表、无需人工审批弹窗**：

### 第 1 步：读取当前 `night_watch.py` 代码与规则列表
调用 `spiceowl_direct_read(operation="watch_script", arguments={})`：
```json
{
  "operation": "watch_script",
  "arguments": {}
}
```
核对返回的 `code`、`sha256`、`is_default`、`config`、`rules` 以及 `valid` / `syntax_error` 状态。

### 第 2 步：精准局部编辑并保存（或恢复默认）
调用 `spiceowl_direct_control(operation="save_watch_script", ...)`，优先使用 `edits` 做精准局部修改（也支持传完整 `code` 替换，或传 `{"reset": true}` 一键恢复默认模板）：
```json
{
  "operation": "save_watch_script",
  "arguments": {
    "edits": [
      {
        "find": "BUG_RULES = [",
        "replace": "def rule_custom_env_timeout(run, report, log_tail, host_pool, config):\n    # 检测到远端环境模块加载超时时，保持网表不变迁移至备用健康主机重试\n    text = (log_tail or \"\").lower()\n    if \"module load\" in text and \"timeout\" in text:\n        return {\n            \"matched\": True,\n            \"rule\": \"custom_env_timeout\",\n            \"action\": \"migrate_host\",\n            \"reason\": \"检测到环境模块加载超时，保持网表不变迁移至备用节点重试。\",\n        }\n    return None\n\n\nBUG_RULES = [\n    rule_custom_env_timeout,"
      }
    ],
    "note": "新增 custom_env_timeout 规则：环境模块加载超时时自动换机重试"
  }
}
```
- **编写规范**：务必保留清晰中文注释、防御式空值处理与有界重试逻辑；**严禁放宽 `protect_netlist_and_spec` 科学红线**（除非用户明确要求并确认）。

### 第 3 步：隔离沙箱试运行检验（确认 `ok: true` 后再回复用户）
每次改动后必须立刻调用 `spiceowl_direct_control(operation="test_watch_script", ...)` 检验：
- 传入未知 Bug 日志摘录试运行：
  ```json
  {
    "operation": "test_watch_script",
    "arguments": {
      "scenario": "unknown_bug",
      "log_excerpt": "ERROR: module load eda_env timeout after 60s"
    }
  }
  ```
- 或传入真实任务 ID（自动加载该任务的真实 `run_record`、`report`、`log_tail` 与当前已注册 `host_pool`）试运行：
  ```json
  {
    "operation": "test_watch_script",
    "arguments": {
      "run_id": "<run_id>"
    }
  }
  ```
- 也可传入内置场景（`queue_timeout`、`confirmed_oom`、`license_unavailable`、`disk_or_nfs_transient`、`convergence_failure`、`unknown_bug`）或临时 `"code"` 预演；
- 确认返回 `ok: true` 且命中预期规则与决策后再回复用户，简明说明改动要点、触发条件与试运行结果。

## 4. 安全与工程红线

1. **禁止硬编码私密信息与外部外发**：`night_watch.py` 仅做本地确定性判断与建议生成，严禁写入本机绝对私有路径、凭据 Token 或外部网络请求。
2. **精确匹配、避免误伤**：新增规则应设置清晰的关键词/正则条件并安排合理优先级，避免拦截或覆盖 `confirmed_oom`、`convergence_failure` 等已有核心规则。
3. **改动留痕**：每次调用 `save_watch_script` 都附带清晰中文 `note`，便于用户在网页端「恢复与值守」面板随时回溯。
"""


BUILTIN_SKILLS: dict[str, dict[str, str]] = {
    "spiceowl-sim-submit": {
        "name": "spiceowl-sim-submit",
        "title": "SpiceOwl 标准化仿真提交 SOP（Virtuoso-Free）",
        "summary": (
            "Fast, standardized Virtuoso-free simulation submission SOP for SpiceOwl Agents "
            "(1-call submit, surgical netlist_edits, Project/TB archiving, and post-run waveform/ADC skills)."
        ),
        "content": _SPICEOWL_SIM_SUBMIT_MD,
    },
    "spiceowl-sim-report-fast": {
        "name": "spiceowl-sim-report-fast",
        "title": "SpiceOwl 极速仿真报告生成 SOP（Fast Mode，<1 分钟出稿）",
        "summary": (
            "Fast (<1 min) simulation report SOP: write a concise engineering summary and let SpiceOwl's "
            "built-in template & SVG plotter assemble waveform plots and HTML automatically in 1 tool call."
        ),
        "content": _SPICEOWL_SIM_REPORT_FAST_MD,
    },
    "spiceowl-sim-report": {
        "name": "spiceowl-sim-report",
        "title": "SpiceOwl 深度交互式 HTML 仿真报告生成 SOP（Deep Authoring Mode）",
        "summary": (
            "Deep authoring mode for rich, self-contained interactive HTML simulation reports (single-run, "
            "Testbench rollup, and Project rollup) with custom editorial layouts, physical analysis, "
            "and bespoke SVG/PNG plots."
        ),
        "content": _SPICEOWL_SIM_REPORT_MD,
    },
    "spiceowl-night-watch": {
        "name": "spiceowl-night-watch",
        "title": "SpiceOwl 夜间值守剧本与 night_watch.py 编程定制 SOP",
        "summary": (
            "Night-watch recovery playbooks for known and unknown EDA simulation bugs, "
            "plus SOP for reading, customizing, and dry-run testing the user's editable night_watch.py script."
        ),
        "content": _SPICEOWL_NIGHT_WATCH_MD,
    },
}


def _repo_skill_path(name: str) -> Path | None:
    if name not in BUILTIN_SKILLS:
        return None
    resolved = Path(__file__).resolve()
    if len(resolved.parents) < 3:
        return None
    candidate = resolved.parents[2] / "skills" / name / "SKILL.md"
    if candidate.is_file() and not candidate.is_symlink():
        return candidate
    return None


def list_skills() -> dict:
    """Return metadata for built-in SpiceOwl Agent Skills."""
    items = []
    for name, meta in BUILTIN_SKILLS.items():
        path = _repo_skill_path(name)
        items.append({
            "name": meta["name"],
            "title": meta["title"],
            "summary": meta["summary"],
            "source": "repo" if path is not None else "builtin",
        })
    return {"skills": items}


def get_skill(name: str) -> dict:
    """Read a built-in SpiceOwl skill, preferring repo skills/<name>/SKILL.md if present."""
    if not isinstance(name, str) or not name.strip() or name.strip() not in BUILTIN_SKILLS:
        raise DomainError(f"Unknown SpiceOwl skill: {name!r}.", "not_found")
    key = name.strip()
    meta = BUILTIN_SKILLS[key]
    path = _repo_skill_path(key)
    if path is not None:
        content = path.read_text(encoding="utf-8")
        source = "repo"
    else:
        content = meta["content"]
        source = "builtin"
    return {
        "name": meta["name"],
        "title": meta["title"],
        "summary": meta["summary"],
        "source": source,
        "content": content,
    }

---
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

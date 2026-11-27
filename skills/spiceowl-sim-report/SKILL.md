---
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

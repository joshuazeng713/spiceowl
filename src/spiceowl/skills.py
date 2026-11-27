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


_SPICEOWL_SIM_REPORT_MD = r"""---
name: spiceowl-sim-report
description: Build rich, self-contained interactive HTML simulation reports (single-run, Testbench rollup, and Project rollup) with embedded SVG/PNG waveform plots, KPI cards, and spec tables.
---

# SpiceOwl 交互式 HTML 仿真报告生成 SOP（Single-Run / Testbench / Project）

本技能定义 SpiceOwl Agent 生成出版级、可离线独立打开、可二次迭代编辑的 HTML 仿真工程报告的标准化流程，覆盖 **单次仿真（Single Run）**、**Testbench 汇总（Testbench Rollup）** 与 **项目汇总（Project Rollup）** 三个层级。

## 1. 数据优先铁律（Data-First Discipline）

- **一切结论必须源自真实落盘证据**：
  - 优先调用零审批只读工具检查真实数据：
    - `spiceowl_direct_read(operation="inspect" | "report" | "waveform_index" | "waveform_query" | "waveform_measure" | "waveform_cycles" | "log_tail" | "inputs", ...)`；
    - 跨任务汇总时先用 `spiceowl_direct_read(operation="list", arguments={})` 筛选同属目标 `project` / `testbench` 的 `run_id`，逐一读取各任务 `report` 与关键波形指标；
    - 在 `supervised` 或 `full_access` 模式下，也可直接用 Python 读取本机 `collected/` 目录中的仿真结果与日志，或使用 `matplotlib` 生成真实波形图并编码为 `data:image/png;base64,...`。
  - **严禁捏造测量值、严禁伪造平滑曲线、严禁补全缺失的 Corner / 指标**；未测项或解析失败项必须明确标注为「未测 / N/A / 失败」并给出真实原因。
  - **五维结论严格分离**：始终区分展示执行状态（Execution）、测量有效性（Measurement Validity）、电路指标（Functional Performance）、工作区与 SOA（Operating Qualification）以及依赖/来源可追溯性（Dependency Provenance）。

## 2. 出版级自包含 HTML 报告规范（Self-Contained Editorial HTML）

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

## 3. 三级报告作用域与零审批保存工具

以下保存工具均为本机归档操作，**不触碰远端服务器、不修改网表、无需人工审批弹窗**，直接生效并实时推送刷新网页「仿真总览（Overview）」：

### 3.1 单次仿真报告（Single Run）
调用 `spiceowl_direct_control(operation="save_ai_report", ...)`：
```json
{
  "operation": "save_ai_report",
  "arguments": {
    "run_id": "<run_id>",
    "title": "<简短中文报告标题>",
    "summary": "<一句话核心工程结论>",
    "html": "<!doctype html><html lang=\"zh-CN\">...</html>",
    "markdown": "## 仿真结论与关键指标摘要"
  }
}
```
回读：`spiceowl_direct_read(operation="ai_report", arguments={"run_id": "<run_id>"})`。

### 3.2 Testbench 汇总报告（同 TB 下全部仿真对比）
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
    "run_ids": ["<run_id_1>", "<run_id_2>"]
  }
}
```

### 3.3 Project 汇总报告（同项目下全部 TB 与仿真总览）
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
    "run_ids": ["<run_id_1>", "<run_id_2>"]
  }
}
```
回读全部汇总报告列表：`spiceowl_direct_read(operation="scope_reports", arguments={})`；
回读指定汇总报告：`spiceowl_direct_read(operation="scope_report", arguments={"scope": "testbench", "project": "<project_name>", "testbench": "<tb_name>"})`（Project 级省略 `testbench`）。
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
    "spiceowl-sim-report": {
        "name": "spiceowl-sim-report",
        "title": "SpiceOwl 交互式 HTML 仿真报告生成 SOP（Single-Run / Testbench / Project）",
        "summary": (
            "Build rich, self-contained interactive HTML simulation reports (single-run, Testbench rollup, "
            "and Project rollup) with embedded SVG/PNG waveform plots, KPI cards, and spec tables."
        ),
        "content": _SPICEOWL_SIM_REPORT_MD,
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

"""Built-in Agent Skills (SOP guides) for SpiceOwl."""

from __future__ import annotations

from pathlib import Path

from spiceowl.domain.common import DomainError


_SPICEOWL_SIM_SUBMIT_MD = """\
---
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

### Step 5：简明汇报与按需后处理技能
- 提交后简明汇报 `run_id`、目标主机（`host_id`）、项目/TB（`project` / `testbench`）及当前状态（`state`），并提示用户在网页「仿真总览（`overview`）」跟踪进度。
- 仿真完成并 `collect` 后，按需调用内置后处理技能：
  - 波形读取/周期分析/模拟指标：`spiceowl_direct_read`（`waveform_index`、`waveform_query`、`waveform_cycles`、`waveform_measure`、`report`）；
  - 确定性 ADC / 频谱 / 线性度 / 时域分析：`spiceowl_adctools`（`coherent_fft`、`sine_fit4`、`histogram_linearity`、`tran_metrics`、`ac_metrics`、`dc_op_metrics` 等）；
  - PVT / Monte Carlo 批量扫描：`spiceowl_direct_control`（`batch_prepare`、`batch_dispatch`、`batch_refresh`、`batch_cancel`）与 `spiceowl_direct_read`（`batch_list`、`batch_inspect`、`batch_report`）。

## 3. 安全红线

1. **`launch_outcome_unknown` 严禁换 key 盲目重提**：提交或启动后若因断连/超时处于 `launch_outcome_unknown`，**必须先对原 `run_id` 调用 `spiceowl_direct_control(operation="status", arguments={"run_id": "<原run_id>"})`** 对账，严禁换新 `run_key` 盲目重复提交。
2. **夜间自治开启时严禁擅自修改网表或验收指标**：`OvernightAutonomyPolicy` 启用期间，只允许在授权边界内换主机、扩内存或调运行选项；严禁修改网表（含 `netlist_edits`/`append_netlist`）、激励或验收 spec，越界操作必须挂起等待人工审阅。
3. **不编造缺失科学参数**：严禁猜测私密 PDK 路径、Corner、器件尺寸、供电电压或验收阈值；参数缺失时明确说明并请用户确认。
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

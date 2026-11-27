---
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

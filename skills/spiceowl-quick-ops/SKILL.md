---
name: spiceowl-quick-ops
description: Ultra-fast 1-call micro-operations for SpiceOwl Agents (toggle/tune night watch, tweak host/CPU/RAM/netlist parameters, check live pool/licenses, control runs, or generate instant reports in 1-2 tool calls and <5 seconds).
---

# SpiceOwl 极速微操指南（Quick Ops · 1 步直达）

当用户的需求属于**高频小操作**（开关夜间值守、改单个参数、换主机、查空闲资源、停任务、秒出快报等）时，严格使用以下 **1 次调用直达通道**，**严禁**走冗长多步流程（不要读 456 行脚本、不要写整份网表、不要重复探测服务器、不要跑多余 Dry-Run 或浏览器检查）。

## 1. 高频小操作 1 步速查表

| 用户常见小需求 | 唯一推荐工具调用（零废话、一步到位） | 严禁做的耗时动作 |
|---|---|---|
| **开/关夜间值守、改夜间内存上限/排队超时/重试次数/优先主机/默认报告模式** | `spiceowl_direct_control(operation="update_watch_config", arguments={"enabled": false, "note": "关闭夜间值守"})`（零审批，支持 `enabled`、`auto_push_to_agent`、`max_memory_gib`、`queue_timeout_seconds`、`max_resubmissions`、`license_retry_delay_seconds`、`preferred_hosts`、`auto_report_mode`） | 严禁先读完整 `watch_script`、严禁手拼 `edits`、严禁为改开关跑 `test_watch_script` Dry-Run |
| **一键恢复默认夜间值守脚本** | `spiceowl_direct_control(operation="save_watch_script", arguments={"reset": true})`（零审批） | 无需先读原脚本、无需 Dry-Run |
| **换目标服务器 / 改 CPU 核数 / 改内存 / 改时长 / 改波形格式** | `spiceowl_ui_configure(host_id="...", cpus=4, memory_gib=16, output_format="psfbin")`（零审批） | 严禁重扫主机 `spiceowl_scan_resources` |
| **改网表里的单个参数（温度 `temp`、仿时长 `stop`、电源电压 `vdd` 等）** | `spiceowl_ui_configure(netlist_edits=[{"find": "...", "replace": "..."}])`（若直接提交则直接传给 `submit` 的 `netlist_edits`） | 严禁复写整份几十 KB 网表 |
| **查哪台服务器最空闲 / 查 SPICE License 余量** | 直接读消息附带的 `[SPICEOWL_CTX]`（**0 次工具调用**）；仅在缺少时调用 1 次只读 `spiceowl_recommend_hosts` | 严禁循环调用 `spiceowl_probe_host` 或 `spiceowl_scan_resources` |
| **查单次任务状态 / 取消任务 / 拉取结果 / 删除草稿** | `spiceowl_direct_control(operation="status" \| "cancel" \| "collect" \| "delete", arguments={"run_id": "<run_id>"})` | 严禁循环轮询（后台每 4 秒已自动巡检与自动拉取） |
| **单次/TB/项目快速出报告** | `spiceowl_direct_control(operation="render_fast_report" \| "render_fast_scope_report", ...)`（零审批） | 严禁手写 HTML/CSS/JS、严禁 matplotlib 画常规波形、严禁开浏览器自检 |

## 2. 极速响应原则

1. **能 1 步做完绝不拆 3 步**：匹配上表任意场景时，直接发出那 **1 次工具调用**，拿到成功结果后用 **1～2 句简洁中文**确认已完成即可。
2. **仅在真正编写新逻辑时切入完整技能**：
   - 只有需要新增/改写 `night_watch.py` 的自定义报错恢复函数 `rule_*` 时，才走 `spiceowl-night-watch` 的「读脚本 → 改规则 → Dry-Run」3 步流程；
   - 只有用户明确要求定制排版或特殊物理派生图时，才走 `spiceowl-sim-report` 深度创作模式。
3. **科学红线始终不可突破**：`update_watch_config` 与 `save_watch_script` 均不允许关闭 `protect_netlist_and_spec`，严禁擅自修改原始网表、激励或 Spec 验收标准。

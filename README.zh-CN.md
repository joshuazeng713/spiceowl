<p align="center">
  <img src="assets/brand/spiceowl.svg" width="128" height="128" alt="SpiceOwl：守望电路波形的猫头鹰">
</p>

<h1 align="center">SpiceOwl · 仿真夜枭</h1>

<p align="center"><strong>你去休息，仿真我盯。</strong></p>
<p align="center">面向长时间电路仿真的开源 Agent 控制台。</p>
<p align="center"><a href="README.md">English</a> · 简体中文 · <a href="docs/architecture.md">架构设计</a> · <a href="docs/roadmap.md">开发路线</a> · <a href="CONTRIBUTING.md">参与贡献</a></p>

> **早期开发：目前只有文档与目录骨架。** 尚未实现可运行的监控服务、仿真器接入或 CLI。下文介绍目标能力，进度以[开发里程碑](docs/roadmap.md)为准。

## 为什么做 SpiceOwl

晚上启动仿真，第二天回来才发现连接断了、任务内存不足，或者仿真已经结束，却没有保存测量需要的信号。

SpiceOwl 希望把这些情况变成一份清晰的实验记录：执行了什么、哪里出现异常、采取过哪些恢复动作，以及结果能否回答原本的实验问题。

第一阶段面向 **使用 Virtuoso / Spectre 的模拟与混合信号研究**，从一个经过验证的 Linux 执行环境开始。公共开发使用模拟执行器和合成测试数据，让没有商业仿真器或 PDK 的贡献者也能参与。

## 计划实现的能力

| 能力 | 目标行为 | 当前状态 |
| --- | --- | --- |
| 观察 | 跟踪任务身份、仿真进度、日志、内存和磁盘使用 | 计划中 |
| 重连 | 在客户端或控制器中断后核对并找回原任务 | 计划中 |
| 验收 | 检查输入、必需信号、测量窗口和结果完整性 | 计划中 |
| 恢复 | 对支持的故障执行有次数、资源边界且可追溯的恢复策略 | 计划中 |
| 诊断 | 由可选 Agent 解释证据并提出处理建议 | 计划中 |
| 报告 | 分别呈现执行结果、测量有效性和电路性能 | 计划中 |

### 一份报告需要区分三个问题

| 问题 | 示例结论 |
| --- | --- |
| 仿真器运行完了吗？ | 执行完成 |
| 这次测量符合实验要求吗？ | 输入一致，信号齐全，测量窗口有效 |
| 电路性能达标了吗？ | 未达标：建立时间超过目标 |

测量有效但性能不达标，是需要保留的实验结果。恢复机制不能通过改变实验条件，把失败“修”成成功。

## 设计原则

- **运行独立于界面。** 任务由远端执行服务或调度器管理，浏览器和 Agent 对话不拥有任务生命周期。
- **允许状态未知。** 连接丢失不等于仿真失败；再次提交前必须先核对原任务。
- **规则负责值守，Agent 负责调查。** 常规监控和已知恢复策略不依赖 LLM；Agent 提交结构化建议，由策略层判断是否执行。
- **每次尝试都可追溯。** 保留实际输入、工具身份、提交回执、异常证据，以及各次尝试之间的变化。
- **恢复保持实验含义。** 修改容差、激励、corner、随机种子或测量定义，必须符合明确策略，或形成经过审核的新实验。
- **按后端能力恢复。** 控制器重启、任务重连、求解器 checkpoint 续算是三种不同能力。

## 仓库目录

```text
spiceowl/
├── README.md                  # 英文项目介绍
├── README.zh-CN.md            # 中文项目介绍
├── docs/                      # 架构、实验约定、路线和设计决策
├── assets/brand/              # 可编辑图标与头像导出文件
├── examples/                  # 实验清单示例
├── src/spiceowl/               # 预留的 Python 实现边界
│   ├── domain/                # 实验、尝试、回执与事件模型
│   ├── adapters/              # 执行环境与仿真器适配
│   ├── monitoring/            # 状态观察与故障分类
│   ├── validation/            # 输入和测量验收
│   ├── recovery/              # 恢复策略与动作提案
│   ├── storage/               # 持久化记录与产物引用
│   ├── agent/                 # 可选的诊断 Agent
│   └── cli/                   # 后续本地命令入口
└── tests/                     # 测试方案与合成案例
```

源码目录目前只是占位。[架构文档](docs/architecture.md)说明模块职责与故障处理，[实验约定](docs/run-contract.md)说明可信实验必须记录的信息，[初始设计决策](docs/decisions/0001-project-foundation.md)标明仍需验证的选型。

## 从哪里开始

目前没有可安装的软件包，可以先查看设计材料：

```bash
git clone https://github.com/joshuazeng713/spiceowl.git
cd spiceowl
python3 -m json.tool examples/run-manifest.example.json
```

1. 阅读[首个里程碑及验收条件](docs/roadmap.md)。
2. 查看[实验清单示例](examples/run-manifest.example.json)及[字段解释](docs/run-contract.md)。
3. 查看[断线但任务仍在运行的合成案例](tests/fixtures/synthetic/disconnected-running.json)。
4. 提交可复现的故障案例，或围绕一个里程碑实现小功能。

首个可执行版本的目标是 **模拟执行器＋只读监控**。真实 Spectre 接入需要明确测试过的工具版本和运行环境，再逐步增加自动恢复。

## 参与贡献

欢迎模拟设计、CAD 和科学计算方向的贡献者。脱敏故障案例、测量验证器以及带故障注入测试的小功能，都是有价值的起点。

共享数据前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。漏洞报告和敏感材料处理见 [SECURITY.md](SECURITY.md)。[参考资料](docs/references.md)列出候选组件和相关原始文档，目前尚未集成第三方框架。

## 许可证

沿用仓库选择的 [MIT](LICENSE) 许可证。项目不分发 Cadence 工具、PDK 或第三方组件；未来引入的第三方代码保留其适用许可证及声明。

# CodeMedic 全局进度理解与推进计划

> 本文件是本次工作会话的持久化计划。业务代码修改前，先完成现状理解、设计确认与实施计划。

**Goal:** 了解 CodeMedic 当前完整工作进度，识别已完成、进行中与阻塞项，形成可验证的推进计划，并在设计边界确认后开始推进第一项工作。

**Architecture:** 先以项目文档、Git 历史、测试与核心运行链路为事实来源，整理出系统状态和风险；再按最小责任边界提出下一阶段设计与实施任务。业务逻辑保持现有架构，除非分析证明需要边界修正。

**Tech Stack:** Python, LangChain/LangGraph（以项目实际依赖为准）, pytest, ruff/mypy（以项目现有配置为准）。

## Global Constraints

- 使用中文向用户同步；代码、文件名、标识符和命令遵循项目现有风格。
- 不猜测未验证的进度；所有结论尽量关联文档、Git、代码或测试证据。
- 只修改与用户目标直接相关的文件；不做无关重构。
- 先完成设计确认，再进入业务代码实现。
- 每个推进阶段都要有针对性验证命令。

## Phases

- [x] Phase 1: 恢复上下文并盘点项目结构、文档、Git 状态和测试基线
- [x] Phase 2: 梳理核心架构与运行链路，形成当前进度/风险/缺口清单
- [x] Phase 3: 提出推进方案并确认以最新 `stage_prompt.md` 为执行边界
- [x] Phase 4: 形成 Core Consistency Fix implementation plan
- [x] Phase 5: 按计划推进 Router、Retry、Schema、Workflow 和 E2E 修复
- [x] Phase 6: 运行目标测试与完整回归验证，更新进度记录
- [x] Phase 7: 按最新专家意见完成真实功能证明轮

## Real Function Proof Round

- [x] 修复 Patch Review 的 Retry 上限：达到上限仍可 approved/rejected，不再提供 retry
- [x] 将 previous patch、验证/测试失败反馈、人工反馈和 Verifier summary 传给 Fixer
- [x] 将 PatchProposal 元数据校验集中到 patch_validation_node
- [x] 通过修改前后文件 hash 生成真实 modified_files，并校验 Final Status 的文件集合一致性
- [x] 增加 Intake Fail-Closed、跨字段 Schema Validator 和 Sandbox 失败清理
- [x] 建立真实 Demo Sandbox/Test Runner E2E 与真实 Retry E2E
- [ ] 合并前删除包含本地环境信息的工作文件

## Errors Encountered

| Error | Attempt | Resolution |
|---|---:|---|
| 初次并行读取技能文件时执行环境初始化失败 | 1 | 改为逐个读取，并在沙箱限制下使用只读升级请求重试 |
| `.codex` 镜像路径不存在 | 1 | 使用同内容的 `.agents` 技能路径读取 |
| codegraph 默认索引指向 Python 虚拟环境，未覆盖 codemedic | 1 | 暂不依赖该索引，改用项目文件与 Git 直接核验 |
| 从父目录运行 pytest 导致收集其他项目的 118 个无关错误 | 1 | 改回 `E:\\wwx_daily_work\\agent\\codemedic` 项目根目录运行 |
| Diagnosis Review E2E 未 mock Fixer，恢复后触发外部模型等待 | 1 | 增加最小 Fixer mock，仅验证 Review 恢复路由 |

## Current Status

- Current phase: Real Function Proof Round complete
- Last updated: 2026-07-14
- Next action: 用户审查本轮未提交变更；合并前清理 findings.md、progress.md、task_plan.md。

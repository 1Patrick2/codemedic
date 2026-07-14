# CodeMedic 工作进度日志

## 2026-07-14

- 建立 `task_plan.md`、`findings.md`、`progress.md` 持久化工作文件。
- 确认项目根目录为 `E:\wwx_daily_work\agent\codemedic`。
- 记录技能读取与 codegraph 索引路径问题，改用项目文件/Git 直接核验。
- 当前处于 Phase 1：项目现状盘点。

## Core Consistency Fix 实施

- 新增 Core Consistency 回归测试，先确认关键缺口按预期失败，再完成最小实现。
- 完成 PatchApplyResult 一致性校验、typed adapters 接线、Fixer attempt/retry 计数、Patch/Test Router 强类型读取。
- 完成 `run_workflow()` / `resume_workflow()` 的 thread_id 和 workflow snapshot 一致性。
- 增加 review 等待状态节点，确保 Diagnosis/Patch interrupt 前状态已写入 Checkpoint。
- 修正 Verifier 对 `TestResult` 新字段的消费方式。
- 修正 Diagnosis Review E2E decision 和 Fixer mock，补充 Patch Apply、Test Result、Final Status、Retry budget 断言。

## 验证结果

- pytest：通过，1 个既有真实模型 smoke test skipped。
- Ruff：通过。
- mypy：29 个源码文件通过。
- pip check：No broken requirements found。
- 未提交、未 Push；B5 失败反馈、Hybrid Retrieval、Streamlit、Evaluation 均未修改。

## Real Function Proof Round

- 修复 Patch Review Retry 上限语义：最后一次生成的 Patch 仍可人工批准或拒绝。
- Fixer Retry 现在接收 previous patch、Diff/测试失败反馈、人工反馈和 Verifier summary。
- PatchProposal 的声明文件校验集中到 `patch_validation_node()`；Final Status 额外校验 Diff 文件集合与 Sandbox 实际 hash 变化一致。
- `apply_patch()` 通过修改前后文件 hash 生成 `modified_files`；Sandbox 在 Apply 失败、异常和测试完成后清理。
- Intake 对不存在仓库 Fail-Closed；Evidence、Diff、Test、Workflow Result 增加跨字段语义校验。
- 新增真实 Demo Sandbox/Test Runner E2E 和真实 Retry E2E。当前 Demo 实际包含 `data_service.py` 的两个故障以及 `math_helpers.py` 的故障，因此真实 Patch 修改两个文件并让全量 Demo pytest 通过。

## Real Function Proof Validation

- `conda run -n codemedic python -m pytest -q`：112 passed、1 skipped，包含真实 Sandbox 和 Retry E2E；skip 为既有真实模型 smoke test。
- `conda run -n codemedic python -m ruff check .`：通过。
- `conda run -n codemedic python -m mypy codemedic`：29 个源码文件通过。
- `conda run -n codemedic python -m pip check`：No broken requirements found。
- 保留既有 `create_react_agent` deprecated warning；不在本轮范围内。

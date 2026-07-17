# CodeMedic

> Current implementation status (2026-07-17): deterministic C1-C7 closure, Trajectory D1, Minimal Inspector D2 with Evaluation/trajectory filtering, Evaluation Harness foundations, Retrieval A/B/C framework, and the first accepted 15-run real-model proof are implemented. The runtime is Temporary Workspace Isolation, not a production security sandbox.

> Evaluation status: local harness code, safety-task reporting, read-only Evaluation APIs, and a 15-run real-model report are available. The accepted run met the Stage 1 safety and threshold gates; real Provider calls remain explicitly gated and are not part of normal CI.

> Retrieval status: deterministic Baselines A, B, and C are available and have a local comparison report. Patch Apply and Final Test are marked not measured in the retrieval-only report; no retrieval upgrade is adopted without full end-to-end data.

> Release status: `test/real-model-proof` contains the local Stage 0/1/3/4 implementation changes. CI also runs on `test/*` pushes. Local `runtime/`, `.env`, checkpoints, trajectories, and evaluation artifacts are intentionally not tracked.

For end-to-end Retrieval comparison, run the evaluation command separately with
`--retrieval-baseline baseline_a`, `baseline_b`, or `baseline_c`. Each run prints
per-task progress and writes its own isolated output directory. These commands
send repository/task data to the configured Provider and are not part of CI.

基于 LangChain 与 LangGraph 的多 Agent 代码诊断与修复系统。

Investigator → Fixer → Human Review → Sandbox → Verifier 闭环，覆盖从根因定位到测试验证的全流程。

## 系统架构

```
用户问题 / Issue / 报错日志
          ↓
    仓库与日志检索
          ↓
 Investigator 根因诊断
          ↓
    Evidence 验证 + 证据门控
          ├─ 证据不足 → 补充检索
          ├─ 无法判断 → 人工复核
          └─ 证据充分 → Fixer 生成 Unified Diff
                        ↓
                    Human Review (Interrupt)
                      ├─ 拒绝 → 结束
                      ├─ 修改后重试 → Fixer
                      └─ 批准 → 临时沙盒
                                ↓
                           应用 Patch
                                ↓
                           白名单测试
                                ↓
                           Verifier 总结
                                ↓
                         结构化修复报告
```

## 当前状态

核心 Graph 路由、跨实例 Checkpoint 恢复、失败反馈重试、公开人工授权 API、Trajectory 和确定性 E2E 已通过本地验证；C7、D1、D2、E1、Evaluation Harness 基础和 Retrieval A/B/C 实验框架已经实现。当前真实 LLM 数据采集仍受外发安全门控制，完整 Inspector UI 和基于真实数据的 Retrieval 结论尚未完成。

| 阶段 | 内容 | 状态 |
|------|------|------|
| Stage 0 | 环境、依赖、项目骨架 | ✅ 基本完成 |
| Stage 1 | Investigator 工具 + CLI + Trace | ⚠️ JSON + Pydantic 主路径可用，Provider 原生 Structured Output 未完成 |
| Stage 2 | LangGraph StateGraph + Evidence Gate | ✅ 基本完成 |
| Stage 3 | Fixer + Human Review (Interrupt) | ✅ Interrupt / Resume / Retry 反馈已接通 |
| Stage 4 | Sandbox + Test Runner + Verifier | ✅ 真实 Patch、测试和最终状态判定已验证 |
| Phase A | 安全加固 | ✅ 完成 |
| Phase C | Deterministic Closure | ✅ C1-C7 完成 |
| Phase D1 | Unified Trajectory | ✅ 基础能力完成 |
| Phase D2 | Minimal Run Inspector | ✅ 原型完成；Evaluation 页面和完整演示待完成 |
| Phase E | Real Model Proof | ⏳ 等待外发数据授权和真实运行 |
| Phase F | Evaluation Harness | ✅ 本地 Harness 完成；真实数据闭环待完成 |
| Phase G | Retrieval Baselines | ✅ A/B/C 框架完成；真实数据对比待完成 |

## 测试分层

- Unit Tests：State、Router、Node 和工具函数。
- Schema Contract Tests：Evidence、Diff、Patch Apply、Test 和 Workflow Result。
- Security Tests：路径、Evidence、Diff 边界和授权文件校验。
- Graph Integration Tests：正常修复、Retry、Diagnosis Override 和 Fail-Closed。
- Real Sandbox E2E：真实 Unified Diff、Sandbox Apply、测试执行和原仓库 Hash 校验。
- SQLite Checkpoint E2E：Runtime 关闭后重新打开同一数据库并恢复 Workflow。
- Real LLM Smoke Tests：当前仅保留为非 CI 的后续验证项。

> 当前项目适合作为工程演示和技术面试的原型展示，不适合直接用于生产环境或不可信的第三方代码。

## 快速开始

```bash
# 创建 Conda 环境
conda create -n codemedic python=3.11
conda activate codemedic

# 安装依赖（开发、测试和 Inspector）
pip install -e ".[dev,streamlit]"

# 复制环境变量
cp .env.example .env
# 编辑 .env，填入 API Key 和 Endpoint

# CLI 运行诊断
python -m codemedic.cli investigate \
  --issue "factorial function returns NameError" \
  --repo-path ./demo_repos/sample_project

# 运行完整工作流（Python；示例自动接受人工 Review）
python -c "
from codemedic.graph.builder import resume_workflow, run_workflow
result = run_workflow(
    issue='Find all bugs in sample project',
    repository_path='./demo_repos/sample_project',
)
while result.interrupted:
    decision = (
        'accept_diagnosis'
        if result.workflow_status == 'waiting_diagnosis_review'
        else 'approved'
    )
    result = resume_workflow(
        decision,
        thread_id=result.thread_id,
        approved_files=(
            [
                'src/utils/math_helpers.py',
                'src/services/data_service.py',
            ]
            if decision == 'accept_diagnosis'
            else None
        ),
    )
print(result.state.get('final_status'))
"

# 启动最小 Run Inspector（可选）
streamlit run codemedic/inspector_app.py

# 测试
python -m pytest
python -m ruff check .
python -m mypy codemedic
python -m pip check
```

## 项目结构

```
codemedic/
├── codemedic/
│   ├── agents/              # Investigator / Fixer / Verifier
│   ├── graph/               # LangGraph StateGraph
│   │   ├── state.py         # RepairState TypedDict
│   │   ├── nodes.py         # 各节点函数
│   │   ├── routers.py       # 纯函数条件路由
│   │   ├── builder.py       # 工作流构建与公开运行入口
│   │   └── runtime.py       # SQLite Checkpoint 生命周期与跨实例 Resume
│   ├── tools/
│   │   ├── context.py       # RepositoryContext（安全边界）
│   │   ├── repository.py    # 4 只读工具（路径安全、二进制检测、大小限制）
│   │   ├── sandbox.py       # 临时副本、git apply
│   │   └── test_runner.py   # 白名单命令执行（pytest / ruff / mypy / pip check）
│   ├── schemas/
│   │   ├── diagnosis.py     # DiagnosisResult, Evidence
│   │   └── patch.py         # PatchProposal
│   ├── validation/
│   │   ├── evidence.py      # Evidence 文件系统校验
│   │   └── diff.py          # Unified Diff 安全校验
│   ├── tracing/              # 运行轨迹记录与读取
│   │   ├── events.py         # WorkflowEvent
│   │   ├── recorder.py       # 有序 JSONL 记录
│   │   ├── serializer.py     # 序列化与脱敏
│   │   └── reader.py         # 轨迹读取
│   ├── inspector_app.py      # 可选 Streamlit Run Inspector
│   └── config.py            # pydantic-settings 配置
├── tests/                   # 单元、安全、Graph 集成与真实 Sandbox E2E
├── docs/
│   ├── architecture.md      # 架构设计文档
│   └── baseline_audit.md    # 基线审计
├── demo_repos/              # 示例仓库（含故意 bug）
└── runtime/                 # 运行产物（checkpoints / traces / sandboxes）
```

## 模型支持

当前通过 OpenAI-compatible API 使用模型；真实模型能力证明需要显式 Provider、模型和凭据配置，尚未纳入常规 CI。

> **注意**：Investigator 当前要求模型输出 JSON，并优先使用 `DiagnosisResult.model_validate()` 校验；JSON 无法解析或校验失败时才使用正则兼容性降级路径。当前尚未迁移到 Provider 原生 Structured Output，解析失败时 confidence=0 并路由到人工复核。

## 安全边界

- RepositoryContext 绑定仓库根目录，模型无法控制扫描路径
- 路径穿越保护、绝对路径拒绝、UNC 路径拒绝、符号链接逃逸保护
- 文件大小限制（512KB）、二进制文件自动检测
- Unified Diff 校验：仅允许修改已验证证据中的文件、禁止删除/创建文件、禁止 .git 目录
- 测试白名单：仅允许 pytest / ruff / mypy / pip check
- 沙盒：临时仓库副本与 Patch 隔离机制，仅应对可信 Demo 仓库运行测试。**不是操作系统级安全沙箱**，不隔离网络、文件系统或进程的越权访问
- Human Review：仅三种结果（批准/拒绝/重试），重试次数超限自动结束

## 当前限制

- 当前仅支持 Python 项目作为诊断目标
- Patch Apply 依赖 `git apply`（在 Windows/macOS/Linux 均可使用）
- Retrieval 当前仅提供确定性 A/B/C 基线，不引入 Embedding、Vector Database 或独立 Retrieval Agent
- Streamlit 目前是最小 Inspector 原型，完整 Evaluation 页面和演示收口仍待后续阶段完善
- Evaluation Harness 已完成本地代码闭环，真实模型结果和阈值评估尚未采集

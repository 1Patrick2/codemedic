# CodeMedic

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

已实现 Stage 0~4，并经 Phase A 稳定化加固：

| 阶段 | 内容 | 状态 |
|------|------|------|
| Stage 0 | 环境、依赖、项目骨架 | ✅ |
| Stage 1 | Investigator 单 Agent 基线（4 只读工具 + CLI + Trace） | ✅ |
| Stage 2 | LangGraph StateGraph 外层工作流（Evidence Gate 条件路由） | ✅ |
| Stage 3 | Fixer（Unified Diff）+ Human Review（Interrupt + Checkpoint） | ✅ |
| Stage 4 | Sandbox（临时副本）+ Verifier（测试总结） | ✅ |
| Phase A | 安全边界加固、Evidence 验证、Diff 校验、Verify Router | ✅ |

## 快速开始

```bash
# 创建 Conda 环境
conda create -n codemedic python=3.11
conda activate codemedic

# 安装依赖（开发模式含测试工具）
pip install -e ".[dev]"

# 复制环境变量
cp .env.example .env
# 编辑 .env，填入 API Key 和 Endpoint

# CLI 运行诊断
python -m codemedic.cli investigate \
  --issue "factorial function returns NameError" \
  --repo-path ./demo_repos/sample_project

# 运行完整工作流（Python）
python -c "
from codemedic.graph.builder import run_workflow
result = run_workflow(
    issue='Find all bugs in sample project',
    repository_path='./demo_repos/sample_project',
)
print(result['final_status'])
"

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
│   │   └── builder.py       # 工作流构建与编译
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
│   ├── tracing/
│   │   └── local_trace.py   # JSONL Trace
│   └── config.py            # pydantic-settings 配置
├── tests/                   # 68 个测试（单元 + 安全 + 工作流）
├── docs/
│   ├── architecture.md      # 架构设计文档
│   └── baseline_audit.md    # 基线审计
├── demo_repos/              # 示例仓库（含故意 bug）
└── runtime/                 # 运行产物（checkpoints / traces / sandboxes）
```

## 模型支持

当前通过 OpenAI-compatible API 使用模型。已测试：

- **DeepSeek V4 Flash**（OpenCode Go 套餐）- 不支持原生 Structured Output，采用文本提示 + Pydantic 校验降级路径

Provider 能力自动检测：

| 能力 | 支持 | 降级 |
|------|------|------|
| Tool Calling | ✅ | — |
| Structured Output | ⚠️ 依赖 Provider | 二次模型调用转换 Schema |
| Token Usage | ⚠️ 依赖 Provider | None |

## 安全边界

- RepositoryContext 绑定仓库根目录，模型无法控制扫描路径
- 路径穿越保护、绝对路径拒绝、UNC 路径拒绝、符号链接逃逸保护
- 文件大小限制（512KB）、二进制文件自动检测
- Unified Diff 校验：仅允许修改已验证证据中的文件、禁止删除/创建文件、禁止 .git 目录
- 测试白名单：仅允许 pytest / ruff / mypy / pip check，使用 `sys.executable` 确保 Conda 环境一致
- 沙盒：临时副本中应用 Patch，原仓库不受影响
- Human Review：仅三种结果（批准/拒绝/重试），重试次数超限自动结束

## 当前限制

- 当前仅支持 Python 项目作为诊断目标
- Patch Apply 依赖 `git apply`（在 Windows/macOS/Linux 均可使用）
- Hybrid Retrieval（Stage 5）+ Streamlit UI（Stage 6）尚未实现
- 无正式 Evaluation 评测套件

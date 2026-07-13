# CodeMedic Architecture

## 项目目标

**CodeMedic** 是一个基于 LangChain + LangGraph 的多 Agent 代码诊断与修复系统。用户可以提交 Issue 描述和错误日志，系统会：

1. 检索目标仓库代码
2. 由 Investigator Agent 根因诊断
3. 证据充分性检查
4. Fixer Agent 生成 Unified Diff 补丁
5. 人工审批
6. 沙盒中应用补丁并测试
7. 输出结构化修复报告

**这不是**通用 AI Coding 平台，不追求替代 Claude Code、Codex 或 Cursor。目的是展示 AI 应用工程能力：Agent 工具调用、Structured Output、LangGraph 状态机、Human-in-the-loop、Sandbox 安全边界等。

---

## 非目标

- MCP / A2A 协议支持
- Supervisor / Coordinator Agent
- 自由 Agent-to-Agent 对话
- 长期记忆
- 自动 Git Push / PR
- 任意 Shell 执行
- Docker / Kubernetes
- 微服务 / 数据库集群
- 知识图谱
- 用户账户系统
- 5 个以上 Agent
- 自动修改用户原仓库

---

## 三 Agent 职责

| Agent | 职责 | 工具 | 边界 |
|-------|------|------|------|
| **Investigator** | 根因诊断 | `list_repo_tree`, `search_code`, `read_file`, `parse_log` (只读) | ≤6 轮工具调用；Evidence 路径和行号由程序验证；不修改文件；不执行命令 |
| **Fixer** | 生成 Unified Diff | 无工具调用，纯结构化输出 | 只生成 `PatchProposal`；不直接写文件；不执行 Shell；不扩大修改范围 |
| **Verifier** | 总结测试结果 | 无工具调用 | 只负责「总结测试结果、判断失败是否与原 Issue 相关、给出重试建议」。最终状态（通过/人工复核/拒绝）由程序规则决定 |

---

## LangChain 内层 Agent vs LangGraph 外层流程

### 内层 Agent（LangChain）

每个 Agent 使用 LangChain 的 `create_agent` 或 `create_react_agent` 构建：

```
模型 → Tool Calling → 工具结果 → 模型 → ... → 最终结构化输出
```

- Investigator 有 4 个只读工具和 1 个 `DiagnosisResult` 结构化输出
- 工具调用轮数由程序限制
- Provider 通过 `ChatModel` 与业务逻辑解耦

### 外层 Workflow（LangGraph）

使用 `StateGraph` 编排整个诊断-修复生命周期：

```
START → intake → hybrid_retrieve → investigator_agent → evidence_gate
  ├─ 证据不足且未超限 → hybrid_retrieve （补充检索）
  ├─ 无法判断或高风险 → manual_review
  └─ 证据充分 → fixer_agent → human_review (interrupt)
                ├─ 拒绝 → END
                ├─ 修改后重试 → fixer_agent
                └─ 批准 → apply_patch_in_sandbox → run_tests
                         → verifier_agent → verify_router
                        ├─ 测试通过 → final_report
                        ├─ 可修复且 retry_count<1 → fixer_agent
                        └─ 不可恢复 → manual_review
```

**为什么需要两层？** LangChain Agent 处理模型-工具循环，LangGraph 处理业务流程的暂停/恢复/路由。前者是「模型决策循环」，后者是「执行流程状态机」。这是项目最核心的面试亮点。

---

## Workflow 流程图

```
                    ┌──────────┐
                    │   START  │
                    └────┬─────┘
                         ↓
                    ┌──────────┐
                    │  intake  │
                    └────┬─────┘
                         ↓
                 ┌────────────────┐
                 │ hybrid_retrieve │
                 └───────┬────────┘
                         ↓
               ┌──────────────────┐
               │ investigator_agent│
               └────────┬─────────┘
                         ↓
                   ┌──────────┐
                   │evidence_ │
                   │   gate   │
                   └┬──┬──┬───┘
            ┌───────┘  │  └──────┐
            ↓          ↓         ↓
      ┌──────────┐ ┌────────┐ ┌──────────┐
      │补充检索  │ │人工复核│ │fixer_   │
      │(≤2次)   │ │(需确认)│ │agent    │
      └──────────┘ └────────┘ └────┬─────┘
                                    ↓
                             ┌─────────────┐
                             │human_review │
                             │  (interrupt) │
                             └──┬───┬───┬──┘
                     ┌──────────┘   │   └──────────┐
                     ↓              ↓              ↓
                   ┌────┐    ┌────────────┐   ┌─────────┐
                   │拒绝│    │修改后重试  │   │ 批准    │
                   └────┘    └────────────┘   └────┬────┘
                                                    ↓
                                           ┌─────────────────┐
                                           │apply_patch_in_  │
                                           │   sandbox       │
                                           └────────┬────────┘
                                                    ↓
                                               ┌──────────┐
                                               │ run_tests│
                                               └────┬─────┘
                                                    ↓
                                              ┌────────────┐
                                              │  verifier_ │
                                              │   agent    │
                                              └───────┬────┘
                                                       ↓
                                                  ┌──────────┐
                                                  │  verify_  │
                                                  │  router   │
                                                  └┬──┬──┬───┘
                                     ┌─────────────┘  │  └──────────────┐
                                     ↓                 ↓               ↓
                               ┌──────────┐    ┌──────────┐   ┌───────────────┐
                               │最终报告  │    │重试Fixer │   │ 人工复核     │
                               │ (通过)   │    │(≤1次)   │   │ (不可恢复)   │
                               └──────────┘    └──────────┘   └───────────────┘
```

---

## State 字段草案

```python
class RepairState(TypedDict):
    task_id: str
    issue: str
    error_log: str | None
    repository_path: str

    retrieved_context: list[dict]
    diagnosis: DiagnosisResult | None
    patch: PatchProposal | None
    test_results: list[dict]

    allowed_files: list[str]
    investigation_steps: int
    retrieval_round: int
    retry_count: int

    human_decision: str | None
    review_reason: str | None

    final_status: Literal["通过", "人工复核", "拒绝"] | None
    final_report: dict | None
    errors: list[str]
```

计数限制：
- `investigation_steps <= 6`
- `retrieval_round <= 2`
- `retry_count <= 1`

每个 Node 只能更新自己负责的字段。Router 必须是纯函数，不能调用 LLM。

---

## 六个核心工具

| 工具 | 访问模式 | 安全约束 |
|------|---------|---------|
| `list_repo_tree` | 只读 | 路径穿越保护 |
| `search_code` | 只读 | 路径穿越保护 |
| `read_file` | 只读 | 文件大小限制、二进制检测 |
| `parse_log` | 只读 | 行数限制 |
| `generate_diff` | 生成 | 不直接修改文件 |
| `run_test` | 执行 | 仅白名单命令、参数列表模式 |

禁止：
- `shell=True`
- 用户自定义命令字符串
- PowerShell / Bash 命令
- `cmd /c`
- 网络访问
- 修改原仓库

测试白名单第一版：
- `python -m pytest`
- `python -m ruff check`
- `python -m mypy`

---

## Human Review

- 使用 LangGraph 的 `interrupt()` 暂停流程
- SQLite Checkpoint 持久化状态
- 通过 `Command(resume=...)` 恢复流程
- 三种结果：批准、拒绝、修改后重试
- 进程退出后可恢复

---

## Checkpoint

- 使用 `langgraph-checkpoint-sqlite` 包
- 存储在工作目录 `runtime/checkpoints/` 下
- 每个任务通过 `thread_id` 区分
- 仅保存 State 字段，不保存敏感信息
- LangSmith 仅在环境变量存在时启用，不可用时主流程不受影响

---

## Trace

每次节点执行写入本地 JSONL：

```
task_id | timestamp | node_name | agent_name | model
prompt_version | tool_name | tool_args_summary | tool_result_summary
latency_ms | token_usage | state_transition | error | retry_count | final_status
```

不记录：API Key、环境变量、超长文件全文、完整源代码。

---

## Sandbox 的真实性边界

- 使用 `tempfile` 和 `shutil` 创建仓库的临时副本
- 在副本中应用 Patch
- 执行白名单测试命令（`subprocess.run` 参数列表模式）
- Timeout / 输出截断 / exit code 检查
- 原仓库内容和 Git 状态不变
- Patch 修改范围检查（`allowed_files`）

---

## Windows 路径与 subprocess 注意事项

- 使用 `pathlib` 处理路径（Windows 兼容）
- `subprocess.run` 传参数列表，禁止 `shell=True`
- 绝对路径保护（Windows 驱动符 `C:\` 等）
- 符号链接逃逸保护
- 路径穿越保护

---

## 技术栈

| 层 | 技术 |
|----|------|
| 语言 | Python 3.11 |
| Agent 框架 | LangChain 1.3.13 + langchain-openai 1.3.5 |
| 工作流 | LangGraph 1.2.9 |
| 结构化输出 | Pydantic v2 (2.13.4) |
| 配置 | pydantic-settings + python-dotenv |
| 数据模型 | Pydantic BaseModel |
| Diff 处理 | unidiff |
| UI | Streamlit (Stage 6) |
| 测试 | pytest + ruff + mypy |
| 持久化 | SQLite (checkpoint) + JSONL (trace) |

---

## 开发顺序 (Stage 0–8)

| Stage | 内容 | 核心交付 |
|-------|------|---------|
| 0 | 环境、依赖、项目骨架 | pyproject.toml，config.py，architecture.md |
| 1 | Investigator 单 Agent 基线 | 4 只读工具，CLI，JSONL Trace |
| 2 | LangGraph 外层工作流 | StateGraph，Evidence Gate，条件路由 |
| 3 | Fixer + Human Review | Unified Diff，interrupt，SQLite Checkpoint |
| 4 | Verifier + Sandbox | 临时副本，测试白名单，程序化最终状态 |
| 5 | Hybrid Retrieval | 关键词 + Embedding 融合检索 |
| 6 | Streamlit | 单页面完整 UI |
| 7 | Evaluation | 24 任务，3 方法对比 |
| 8 | 项目包装 | README，Demo，面试题 |

可以。你现在已经在正确目录和环境中：

```text
(codemedic) PS E:\wwx_daily_work\agent\codemedic>
```

接下来最合适的协作方式是：

```text
Claude Code：逐阶段实现、运行命令、修改代码
你：确认阶段是否进入下一步
我：审查架构、代码设计、测试结果，并带你理解核心代码
```

不要让 Claude 一次性完成全部八个 Stage。**每轮只完成一个可以独立测试、独立提交的阶段**。

截至 2026 年 7 月 13 日，当前可参考的版本基线是 LangChain 1.3.13、LangGraph 1.2.9、`langchain-openai` 1.3.5；三者都支持 Python 3.11。实际安装前仍应让 Claude 在你的本地环境重新检查并锁定兼容版本。([PyPI][1])

LangChain 当前官方 Agent 方案是 `create_agent`，可以负责模型、工具调用循环和 Structured Output（结构化输出）；外部业务流程则使用自定义 LangGraph `StateGraph`。LangGraph 的 Checkpoint（检查点）和 `interrupt()` 能够保存状态，并通过相同 `thread_id` 恢复人工审批流程。([Docs by LangChain][2])

---

# 一、总体开发方案

## 1. 项目定位

CodeMedic 不是 Claude Code、Codex 或 Cursor 的替代品，而是一个用于展示以下 AI 应用工程能力的小型系统：

```text
用户问题 / Issue / 报错日志
              ↓
        仓库与日志检索
              ↓
     Investigator 根因诊断
              ↓
        证据充分性检查
              ↓
       Fixer 生成候选补丁
              ↓
          人工审批
              ↓
     临时副本中应用补丁
              ↓
       真实测试命令验证
              ↓
        生成证据化报告
```

它真正展示的不是“让三个大模型聊天”，而是：

* Agent 工具调用；
* Pydantic 结构化输出；
* LangGraph 状态机；
* Human-in-the-loop（人在回路）；
* Checkpoint 中断恢复；
* Patch 安全边界；
* 测试结果驱动决策；
* Trace（执行轨迹）；
* Direct、Single Agent、Multi-Agent 实验对比。

---

## 2. 技术分层

```text
┌─────────────────────────────────────────────┐
│                Streamlit UI                 │
│ 输入、状态、证据、Diff、审批、测试、Trace   │
├─────────────────────────────────────────────┤
│             LangGraph Workflow              │
│ State、Node、Edge、Router、Interrupt、Retry │
├─────────────────────────────────────────────┤
│              Agent Layer                    │
│ Investigator / Fixer / Verifier             │
├─────────────────────────────────────────────┤
│             LangChain Layer                 │
│ ChatModel、Tools、Prompt、Structured Output │
├─────────────────────────────────────────────┤
│          Deterministic Service Layer        │
│ 检索、路径校验、Diff、Sandbox、Test Runner  │
├─────────────────────────────────────────────┤
│       Storage and Observability Layer       │
│ JSONL Trace、SQLite Checkpoint、评测结果     │
└─────────────────────────────────────────────┘
```

---

## 3. Agent 与程序职责划分

### Investigator Agent

由 LLM 负责：

* 判断应该搜索什么；
* 选择只读工具；
* 关联 Issue、日志和代码；
* 提出根因；
* 引用证据；
* 判断缺失信息。

由程序负责：

* 路径安全；
* 最大工具调用轮数；
* 文件大小限制；
* 参数验证；
* Evidence 文件与行号真实性检查。

### Fixer Agent

由 LLM 负责：

* 根据已经确认的 Diagnosis 设计局部修改；
* 输出修改理由；
* 说明风险；
* 提出测试建议。

由程序负责：

* 限制可修改文件；
* 校验 Unified Diff；
* 拒绝越界 Patch；
* 禁止直接写原仓库；
* 禁止命令注入。

### Verifier Agent

确定性程序负责：

* 创建临时仓库副本；
* 应用 Patch；
* 检查实际修改文件；
* 执行测试白名单；
* 获取真实 exit code；
* 截断 stdout 和 stderr；
* 处理 Timeout。

Verifier LLM 只负责：

* 总结测试结果；
* 判断失败与原 Issue 是否相关；
* 给出是否值得重试的建议。

最终状态必须由程序决定：

```text
通过
人工复核
拒绝
```

LLM 不能直接宣布“修复成功”。

---

## 4. 推荐实现方式

### 内层 Agent

Investigator 使用 LangChain：

```text
create_agent
├── ChatModel
├── list_repo_tree
├── search_code
├── read_file
├── parse_log
└── DiagnosisResult
```

LangChain 的 Tool 本质上是具有明确输入输出的函数，模型负责决定何时调用以及传入什么参数；Structured Output 则保证最终返回 Pydantic 或其他机器可读结构。([Docs by LangChain][3])

### 外层 Workflow

自定义 LangGraph：

```text
START
  ↓
intake
  ↓
hybrid_retrieve
  ↓
investigator
  ↓
evidence_gate
  ├─ 补充检索
  ├─ 人工复核
  └─ fixer
       ↓
human_review interrupt
  ├─ 拒绝
  ├─ 修改后重试
  └─ 批准
       ↓
apply_patch
       ↓
run_tests
       ↓
verifier
       ↓
verify_router
  ├─ 通过
  ├─ 重试 Fixer
  └─ 人工复核
```

### 为什么内外两层都需要

```text
LangChain Agent 内循环：
模型 → 工具 → 工具结果 → 模型 → 最终结构化结果

LangGraph 外流程：
诊断 → 证据门控 → 修复 → 人工审批 → 测试 → 失败路由
```

这也是项目最核心的面试亮点。

---

# 二、阶段划分

## Stage 0：环境、需求与设计冻结

目标：

* 检查目录和 Git 状态；
* 检查 Python；
* 检查已有依赖；
* 查询可安装版本；
* 创建 `pyproject.toml`；
* 创建最小目录；
* 输出架构设计文档；
* 不实现 Agent。

完成标准：

```text
python --version 正确
pip check 通过
pytest 可运行
pyproject.toml 已锁定
目录结构已创建
没有 Agent 业务代码
```

---

## Stage 1：Investigator 单 Agent 基线

目标：

* Provider 配置；
* OpenAI-compatible ChatModel；
* 四个只读工具；
* `DiagnosisResult`；
* Investigator 工具循环；
* CLI；
* 本地 JSONL Trace。

只支持一个 Python Demo 仓库。

完成标准：

```text
CLI 输入 Issue + repository_path + log
→ Agent 调用工具
→ 输出根因
→ 输出真实文件与行号
→ 不修改仓库
→ 生成 JSONL Trace
```

---

## Stage 2：外层 LangGraph 工作流

目标：

* `RepairState`；
* Intake Node；
* Retrieve Node；
* Investigator Node；
* Evidence Gate；
* Conditional Edge；
* 补充检索；
* 最大轮数；
* Final Report 占位。

完成标准：

```text
证据充分 → fixer_stub
证据不足 → 补充检索
超过次数 → 人工复核
不存在无限循环
```

---

## Stage 3：Fixer 和人工审批

目标：

* `PatchProposal`；
* Unified Diff；
* 修改文件范围；
* Human Review Node；
* `interrupt()`；
* SQLite Checkpoint；
* Resume；
* 批准、拒绝、修改后重试。

完成标准：

```text
Fixer 只生成 Diff
审批前不修改仓库
进程退出后可恢复
拒绝后直接结束
批准后才进入 Sandbox
```

LangGraph 官方要求 Interrupt 配合 Checkpointer 和 `thread_id` 使用；恢复时通过 `Command` 继续执行，并且 Interrupt 之前的副作用需要具备幂等性。([Docs by LangChain][4])

---

## Stage 4：Sandbox 与 Verifier

目标：

* 临时仓库副本；
* Patch Apply；
* 修改范围检查；
* 测试命令白名单；
* Timeout；
* 输出截断；
* Verifier 总结；
* 程序化最终状态。

完成标准：

```text
原仓库内容和 Git 状态不变
Patch 不可应用时不运行测试
测试 exit code == 0 才可能通过
超时不能判定通过
越界 Patch 必须拒绝
```

---

## Stage 5：Hybrid Retrieval

目标：

* 文件扫描；
* 结构化 Chunk；
* 关键词检索；
* Embedding；
* Vector Store；
* 简单 Reciprocal Rank Fusion；
* 去重；
* Metadata；
* 行号引用。

完成标准：

```text
函数名和错误码：关键词检索能命中
自然语言模块描述：向量检索能命中
检索结果包含真实路径和行号
关闭向量检索后仍可运行
```

---

## Stage 6：Streamlit

目标：

* 单页面；
* 参数区；
* 状态区；
* Evidence；
* Diagnosis；
* Unified Diff；
* Human Review；
* 测试结果；
* Trace；
* Token 和延迟统计。

完成标准：

```text
CLI 功能全部可以在页面完成
页面刷新后任务可恢复
不会重复审批或重复应用 Patch
```

---

## Stage 7：Evaluation

目标：

* 24 个任务；
* Direct；
* Single Agent；
* Multi-Agent；
* 固定评测配置；
* JSON 报告；
* Markdown 报告。

完成标准：

* 不伪造指标；
* 每个任务可以复现；
* 每个方法使用相同模型和输入；
* 至少运行三次稳定性评测；
* 保存失败案例。

---

## Stage 8：项目包装

目标：

* README；
* 架构图；
* Demo；
* 截图或 GIF；
* 面试题；
* 项目局限；
* 简历 Bullet；
* GitHub 整理。

完成标准：

```text
新用户根据 README 可以运行
三个 Demo 均能复现
简历数字全部来自真实评测
```

---

# 三、Claude 总控 Prompt

下面这份可以直接复制给 Claude Code。它是整个项目的长期总控约束，不是让 Claude 一次性执行全部内容。

你现在是 `CodeMedic` 项目的首席实现工程师。请在当前 Windows PowerShell、Conda Python 3.11 环境中，按阶段构建一个小而精的多 Agent 代码诊断与修复系统。

当前工作目录：

`E:\wwx_daily_work\agent\codemedic`

项目名称：

`CodeMedic —— 基于 LangChain 与 LangGraph 的多 Agent 代码诊断与修复系统`

## 一、项目目标

构建以下受控闭环：

Issue / 报错日志 / 用户描述
→ 仓库代码与配置检索
→ Investigator Agent 根因诊断
→ 证据充分性检查
→ Fixer Agent 生成 Unified Diff
→ Human-in-the-loop 人工审批
→ 临时仓库应用补丁
→ 白名单测试验证
→ Verifier Agent 总结测试证据
→ 输出结构化修复报告

这不是通用 AI Coding 平台，不追求替代 Claude Code、Codex 或 Cursor。

核心展示能力：

* LangChain ChatModel
* Tool Calling
* Structured Output
* Prompt Template
* Retriever
* Vector Store
* LangGraph StateGraph
* Node
* Conditional Edge
* Human-in-the-loop
* Interrupt 和 Resume
* Checkpoint
* Trace
* Evaluation
* Streamlit
* Pydantic

## 二、严格范围

第一版只允许：

* 一个 Streamlit 页面；
* 一个主 LangGraph 工作流；
* 三个 Agent：Investigator、Fixer、Verifier；
* 六个核心工具；
* 一个轻量 Hybrid Retrieval；
* 一个 Human Review 节点；
* SQLite Checkpoint；
* 本地 JSONL Trace；
* LangSmith 可选；
* 24 个评测任务；
* Python 和 ROS 配置/日志 Demo。

禁止增加：

* MCP；
* A2A；
* Supervisor Agent；
* Coordinator Agent；
* 自由 Agent-to-Agent 对话；
* 长期记忆；
* 自动 Git Push；
* 自动创建 PR；
* 任意 Shell；
* Docker/Kubernetes；
* 微服务；
* 数据库集群；
* 知识图谱；
* 用户账户系统；
* 五个以上 Agent；
* 自动修改用户原仓库。

核心业务代码目标为 2,000～3,000 行，不包括测试数据、Demo 仓库和文档。

## 三、技术原则

使用：

* Python 3.11；
* LangChain；
* LangGraph；
* langchain-openai；
* Pydantic v2；
* Streamlit；
* pytest；
* Git；
* subprocess；
* pathlib；
* tempfile / shutil；
* 轻量 Vector Store；
* 关键词检索；
* JSONL Trace；
* SQLite Checkpoint。

在使用任何 LangChain 或 LangGraph API 前必须：

1. 查看当前 Conda 环境的实际安装版本；
2. 查看该版本的官方文档或本地 Python 类型定义；
3. 不凭记忆猜测导入路径；
4. 不使用 deprecated API；
5. 不引入 `langchain-classic`；
6. 把最终依赖固定在 `pyproject.toml`；
7. 执行 `pip check`；
8. 记录版本选择理由。

如官方最新 API 与本 Prompt 示例冲突，以当前安装版本的官方 API 和本地类型定义为准，但不能改变项目架构与安全边界。

## 四、职责划分

### Investigator Agent

允许使用：

* `list_repo_tree`
* `search_code`
* `read_file`
* `parse_log`

输出：

```python
class Evidence(BaseModel):
    file_path: str
    line_start: int | None
    line_end: int | None
    excerpt: str
    reason: str

class DiagnosisResult(BaseModel):
    suspected_files: list[str]
    root_cause: str
    evidence: list[Evidence]
    confidence: float
    missing_information: list[str]
```

要求：

* 可进行有限轮 Tool Calling；
* 最大调用轮数必须由程序限制；
* Evidence 必须由程序验证路径和行号；
* 不允许修改文件；
* 不允许执行命令。

### Fixer Agent

输入：

* Issue；
* DiagnosisResult；
* 已验证 Evidence；
* 相关文件内容；
* `allowed_files`。

输出：

```python
class PatchProposal(BaseModel):
    modified_files: list[str]
    unified_diff: str
    rationale: str
    risks: list[str]
    test_suggestions: list[str]
```

要求：

* 只生成 Unified Diff；
* 不直接写文件；
* 不执行 Shell；
* 不扩大修改范围；
* 不安装依赖；
* 不删除仓库；
* 不上传代码。

### Verifier

确定性程序负责：

* 创建临时副本；
* 校验 Patch；
* 应用 Patch；
* 检查修改文件；
* 执行白名单测试；
* 记录 exit code；
* 记录 stdout 和 stderr；
* Timeout；
* 输出截断；
* 工作目录校验。

Verifier LLM 只负责：

* 总结测试结果；
* 判断失败是否与 Issue 相关；
* 给出重试建议。

最终状态只允许：

* `通过`
* `人工复核`
* `拒绝`

最终状态必须由程序规则决定，不能由 LLM 自由生成。

## 五、LangGraph 流程

```text
START
  ↓
intake
  ↓
hybrid_retrieve
  ↓
investigator_agent
  ↓
evidence_gate
  ├── 证据不足且 retrieval_round 未超限 → hybrid_retrieve
  ├── 无法判断或高风险 → manual_review
  └── 证据充分 → fixer_agent
                    ↓
                human_review
                  ├── 拒绝 → END
                  ├── 修改后重试 → fixer_agent
                  └── 批准
                        ↓
              apply_patch_in_sandbox
                        ↓
                    run_tests
                        ↓
                 verifier_agent
                        ↓
                  verify_router
                  ├── 测试通过 → final_report
                  ├── 可修复且 retry_count < 1 → fixer_agent
                  └── 不可恢复 → manual_review
```

禁止增加自由 Coordinator Agent。

## 六、State 约束

建议状态：

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

* `investigation_steps <= 6`
* `retrieval_round <= 2`
* `retry_count <= 1`

每个 Node 只能更新自己负责的字段。

Router 必须是纯函数，不能调用 LLM、不能写文件、不能执行网络请求。

## 七、六个核心工具

严格限制为：

1. `list_repo_tree`
2. `search_code`
3. `read_file`
4. `parse_log`
5. `generate_diff`
6. `run_test`

前四个只读。

`generate_diff` 只生成或规范化 Unified Diff，不直接应用。

`run_test` 只能执行白名单命令。

禁止：

* `shell=True`
* 用户自定义完整命令字符串
* PowerShell 命令
* Bash 命令
* `cmd /c`
* 网络访问
* 删除仓库
* Git Push
* 修改原仓库

优先使用：

* `pathlib`
* `shutil`
* `tempfile`
* `subprocess.run(..., shell=False)`
* 参数列表
* 明确的工作目录

测试白名单第一版：

* `python -m pytest`
* `python -m ruff check`
* `python -m mypy`

任何额外测试脚本必须在项目配置中显式声明，不能由用户临时输入任意命令。

必须实现：

* 路径穿越保护；
* Windows 绝对路径保护；
* 符号链接逃逸保护；
* 最大文件大小；
* 二进制文件识别；
* Timeout；
* 最大输出长度；
* stdout/stderr 截断；
* exit code；
* 工作目录限制；
* Patch 修改范围检查。

## 八、Trace

每次执行写入本地 JSONL：

* task_id
* timestamp
* node_name
* agent_name
* model
* prompt_version
* tool_name
* tool_args_summary
* tool_result_summary
* latency_ms
* token_usage
* state_transition
* error
* retry_count
* final_status

不得在 Trace 中保存：

* API Key；
* 完整敏感环境变量；
* 超长文件全文；
* 用户仓库全部源码。

LangSmith 只能作为可选功能：

* 环境变量存在时启用；
* 不存在时完整功能仍然可运行；
* LangSmith 故障不得导致主流程失败。

## 九、目录建议

```text
codemedic/
├── app.py
├── cli.py
├── pyproject.toml
├── .env.example
├── README.md
├── docs/
│
├── codemedic/
│   ├── config.py
│   ├── models.py
│   ├── graph/
│   │   ├── builder.py
│   │   ├── state.py
│   │   ├── nodes.py
│   │   └── routers.py
│   ├── agents/
│   │   ├── investigator.py
│   │   ├── fixer.py
│   │   └── verifier.py
│   ├── tools/
│   │   ├── repository.py
│   │   ├── log_parser.py
│   │   ├── patch.py
│   │   └── test_runner.py
│   ├── retrieval/
│   │   ├── chunker.py
│   │   ├── lexical.py
│   │   └── hybrid.py
│   ├── schemas/
│   │   ├── diagnosis.py
│   │   ├── patch.py
│   │   └── report.py
│   └── tracing/
│       └── local_trace.py
│
├── eval/
│   ├── cases/
│   ├── runner.py
│   └── scorer.py
├── demo_repos/
├── tests/
└── runtime/
    ├── checkpoints/
    ├── traces/
    └── sandboxes/
```

可以适度调整，但禁止无意义拆分大量小文件。

## 十、阶段执行规则

严格按顺序执行：

* Stage 0：环境、依赖、架构冻结
* Stage 1：Investigator 单 Agent 基线
* Stage 2：LangGraph 外层工作流
* Stage 3：Fixer + Human Review
* Stage 4：Verifier + Sandbox
* Stage 5：Hybrid Retrieval
* Stage 6：Streamlit
* Stage 7：Evaluation
* Stage 8：README、Demo 和面试包装

每一轮只能执行当前指定 Stage。

不得提前实现下一 Stage。

不得一次性生成整个项目。

不得因为“后续会使用”而提前加入复杂抽象。

## 十一、每阶段执行流程

开始一个 Stage 前：

1. 阅读现有代码；
2. 查看 Git 状态；
3. 总结当前基线；
4. 列出本 Stage 要修改的文件；
5. 列出本 Stage 不会修改的范围；
6. 明确验收条件。

实施过程中：

1. 先完成最小实现；
2. 再补异常路径；
3. 再补测试；
4. 运行格式检查；
5. 运行类型检查；
6. 运行测试；
7. 不隐藏失败。

结束时必须输出：

### 已完成

列出本 Stage 实现的功能。

### 修改文件

逐个列出新增和修改文件及职责。

### 关键设计

解释核心设计选择，不只描述代码。

### 测试结果

必须给出真实命令、exit code、通过数量和失败数量。

### 当前问题

列出遗留问题、限制和技术债务。

### 下一步

只描述下一 Stage，不执行。

### 禁止事项确认

确认：

* 没有修改原 Demo 仓库；
* 没有使用任意 Shell；
* 没有提前实现下一 Stage；
* 没有伪造测试结果。

完成报告后停止，等待用户确认。

## 十二、Git 规则

每个 Stage 建议独立提交，但未经用户明确要求，不得执行：

* `git push`
* 创建远程分支
* 创建 PR
* 合并分支

可以执行：

* `git status`
* `git diff`
* `git log`
* 本地测试

提交前必须展示：

* 修改文件；
* 测试结果；
* 建议 commit message。

只有用户明确要求后才能创建本地 commit。

## 十三、开发质量

必须：

* 使用类型注解；
* 使用 Pydantic v2；
* 使用清晰异常类型；
* 区分用户错误、模型错误、工具错误、系统错误；
* 为安全边界写负向测试；
* 将 Provider 与业务逻辑解耦；
* 将 Agent Prompt 版本化；
* 不在业务代码中散落环境变量读取；
* 不把大段 Prompt 硬编码到多个文件；
* 不伪造 Token Usage；
* Provider 不返回 Token 时使用 `None`，不能估算后冒充真实值。

## 十四、测试优先级

必须覆盖：

* 路径穿越；
* 绝对路径；
* 符号链接逃逸；
* 超大文件；
* 二进制文件；
* 非法工具参数；
* Structured Output 失败；
* Provider Timeout；
* Agent 最大步数；
* Graph 最大检索轮数；
* Patch 无法应用；
* Patch 修改越界；
* 非白名单命令；
* 测试超时；
* stdout/stderr 截断；
* 人工拒绝；
* Checkpoint 恢复；
* LangSmith 不可用；
* retry_count 超限。

不能只测试正常路径。

## 十五、真实性边界

最终可以说：

* 使用 LangChain 构建模型、工具调用、检索和结构化输出；
* 使用 LangGraph 编排三 Agent 状态流；
* 支持 Human-in-the-loop Patch 审批；
* 使用 Checkpoint 和条件路由恢复任务；
* 使用真实测试退出码验证候选 Patch；
* 对比 Direct、Single Agent 和 Multi-Agent。

不能说：

* 企业级代码修复平台；
* 替代 Claude Code 或 Codex；
* 支持任意语言和任意仓库；
* 可以安全自动上线；
* Multi-Agent 一定优于 Single Agent；
* 已获得生产安全认证。

本 Prompt 是全局规则。收到具体 Stage Prompt 后，只执行该 Stage。

---

# 四、现在交给 Claude 的第一轮 Prompt

你已经创建好了 Conda 环境，因此第一轮让 Claude 完成 **Stage 0 本地核查与骨架冻结**，而不是马上写 Investigator。

请按照 CodeMedic 总控 Prompt，只执行：

`Stage 0：环境、依赖、架构与项目骨架冻结`

当前终端：

```text
(codemedic) PS E:\wwx_daily_work\agent\codemedic>
```

本轮目标不是实现 Agent，不允许提前进入 Stage 1。

## 一、先检查当前环境

请实际执行并记录结果：

```powershell
Get-Location
Get-ChildItem -Force
git status
git log --oneline -5

python --version
python -m pip --version
python -m pip list
python -m pip check

git --version
rg --version
```

如果当前目录还不是 Git 仓库，可以初始化本地 Git 仓库，但不得添加远程仓库、不得 Push。

确认当前 Conda 环境确实为 Python 3.11。

## 二、确认当前依赖 API

LangChain 和 LangGraph API 变化较快。

请：

1. 检查 PyPI 当前可安装版本；
2. 检查 LangChain、LangGraph 和 langchain-openai 的官方文档；
3. 安装一组互相兼容且支持 Python 3.11 的固定版本；
4. 安装后读取实际包版本；
5. 检查关键导入是否存在；
6. 不要凭记忆猜导入路径。

需要确认但暂不实现的 API 包括：

* LangChain `create_agent`
* LangChain `@tool`
* Structured Output
* LangGraph `StateGraph`
* `START`
* `END`
* `interrupt`
* `Command`
* Checkpointer
* SQLite Checkpointer

如果 SQLite Checkpointer 需要独立包，请明确确认当前包名和版本。

不要安装：

* `langchain-classic`
* 不需要的 Provider
* 数据库服务
* Docker 相关依赖
* FAISS，Stage 5 再决定

## 三、创建 pyproject.toml

要求：

* `requires-python = ">=3.11,<3.12"`
* 固定核心依赖版本；
* 将测试和开发工具放入 dev extra；
* 配置 pytest；
* 配置 Ruff；
* 配置 mypy；
* 使用 `src` 风格或普通 package 风格二选一，并解释理由；
* 不要过度配置。

至少包含：

* langchain
* langgraph
* langchain-openai
* pydantic
* pydantic-settings
* python-dotenv
* PyYAML
* unidiff
* pytest
* pytest-cov
* ruff
* mypy

Streamlit 可以在 Stage 0 固定版本，也可以延迟到 Stage 6，但必须说明选择。

## 四、只创建最小项目骨架

可以创建：

```text
codemedic/
├── pyproject.toml
├── .gitignore
├── .env.example
├── README.md
├── docs/
│   └── architecture.md
├── codemedic/
│   ├── __init__.py
│   └── config.py
├── tests/
│   └── test_environment.py
├── demo_repos/
└── runtime/
    ├── checkpoints/
    ├── traces/
    └── sandboxes/
```

不要创建：

* Agent 实现；
* LangGraph Node；
* Tool；
* Retriever；
* Streamlit 页面；
* Evaluation；
* Demo 任务；
* 复杂 Provider 代码。

`config.py` 只实现最基础的配置 Schema 和安全默认值，不连接模型。

## 五、architecture.md 必须包含

* 项目目标；
* 非目标；
* 三 Agent 职责；
* LangChain 内层 Agent 与 LangGraph 外层流程的区别；
* Workflow 流程图；
* State 字段草案；
* 六个工具边界；
* Human Review；
* Checkpoint；
* Trace；
* Sandbox 的真实性边界；
* Windows 路径与 subprocess 注意事项；
* Stage 0～Stage 8 开发顺序。

## 六、本轮测试

至少验证：

1. Python 为 3.11；
2. 核心依赖能够 import；
3. 关键 LangChain/LangGraph API 的实际导入路径有效；
4. 配置默认值能够创建；
5. runtime 目录不会被错误提交；
6. `.env.example` 不包含真实密钥；
7. `pip check` 通过。

运行：

```powershell
python -m pytest -q
python -m ruff check .
python -m mypy codemedic
python -m pip check
```

如果 mypy 暂时无法完全通过，必须说明具体原因，不允许隐藏。

## 七、结束报告格式

完成后按以下格式回复：

### 已完成

### 当前环境

### 最终依赖版本

### 关键 API 导入验证

### 修改文件

### 架构决定

### 测试命令与真实结果

### 当前问题

### 下一步

### 建议 Commit Message

### 禁止事项确认

完成 Stage 0 后立即停止。

不要执行 Stage 1。

不要创建 Investigator。

不要提交或 Push，等待用户确认。

---

# 五、之后每轮怎么推进

Claude 完成 Stage 0 后，你不要直接让它继续。先把以下内容发给我：

```text
Claude 的阶段总结
git status
git diff --stat
pytest 结果
ruff 结果
mypy 结果
pip check 结果
```

我会帮你判断：

```text
Stage 是否真正完成
架构是否提前复杂化
LangChain/LangGraph API 是否正确
测试是否只覆盖了表面路径
能否进入下一阶段
```

进入后续 Stage 时，每次都采用同一个节奏：

```text
1. Claude 阅读当前仓库
2. Claude 给出本 Stage 修改计划
3. Claude 实施
4. Claude 运行测试
5. Claude 输出阶段报告
6. 我们审查
7. 你确认是否进入下一 Stage
```

## 推荐 Git 节奏

```text
Stage 0 → chore: initialize codemedic project
Stage 1 → feat: add investigator agent baseline
Stage 2 → feat: add repair state graph workflow
Stage 3 → feat: add patch proposal and human review
Stage 4 → feat: add sandbox patch verification
Stage 5 → feat: add hybrid repository retrieval
Stage 6 → feat: add streamlit workflow interface
Stage 7 → feat: add benchmark evaluation suite
Stage 8 → docs: finalize demos and project documentation
```

这里的 commit message 只是建议。Claude 每轮先展示 Diff 和测试结果，等你明确批准后再提交。

[1]: https://pypi.org/project/langchain/ "langchain · PyPI"
[2]: https://docs.langchain.com/oss/python/langchain/agents?utm_source=chatgpt.com "Agents - Docs by LangChain"
[3]: https://docs.langchain.com/oss/python/langchain/tools "Tools - Docs by LangChain"
[4]: https://docs.langchain.com/oss/python/langgraph/interrupts "Interrupts - Docs by LangChain"

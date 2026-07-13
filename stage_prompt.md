# CodeMedic 完整项目推进方案

当前远端最新提交为：

```text
13834c1 feat: initial codemedic project (Stages 0-4)
```

但代码审查表明，目前更适合把它视为“Stage 0～4 的初版骨架”，而不是已经验收完成的闭环版本。提交中已经包含 Graph、Agent、Sandbox、测试和文档，但 `verify_router` 仍然固定返回成功，`allowed_files` 没有正确赋值，Human Review 同时使用静态和动态中断，Patch fallback 也存在错误修改文件的风险。

因此，后续不要直接进入 Stage 5，而应按下面顺序推进。

---

# 一、总体路线

```text
当前 main：Stage 0～4 初版骨架
            ↓
Phase A：Stage 1～4 稳定化与闭环修复
            ↓
Stage 5：Hybrid Retrieval
            ↓
Stage 6：Streamlit
            ↓
Stage 7：Evaluation
            ↓
Stage 8：README、Demo、简历与面试包装
            ↓
v0.1.0 可演示版本
```

建议拆成五个远端分支：

```text
fix/stabilize-core-workflow
feat/stage5-hybrid-retrieval
feat/stage6-streamlit
feat/stage7-evaluation
docs/stage8-packaging
```

每个分支只完成一个阶段，测试通过后再合并。

---

# 二、Phase A：Stage 1～4 稳定化

这是当前最重要的一轮。建议再拆成 6 个小里程碑，但可以放在同一个 `fix/stabilize-core-workflow` 分支中，每个里程碑单独提交。

---

## A0：建立真实基线

### 目标

先确认当前提交究竟能运行到什么程度，不立即改代码。

### 执行内容

```powershell
git checkout main
git pull
git checkout -b fix/stabilize-core-workflow

python --version
python -m pip install -e ".[dev]"
python -m pip check
python -m pytest -q
python -m ruff check .
python -m mypy codemedic
```

额外运行：

```powershell
python -m pytest --collect-only -q
git status
```

### 产出

创建：

```text
docs/baseline_audit.md
```

记录：

* Python 和依赖版本；
* 测试总数；
* pytest、Ruff、mypy、pip check 结果；
* 当前已知缺陷；
* 当前没有真实 LLM E2E 测试的事实；
* 当前 Stage 0～4 只能算初版。

### 验收

必须知道：

* 当前到底有多少测试；
* 哪些测试真正覆盖 Agent；
* 哪些测试只是 Mock；
* 是否存在未捕获异常；
* 是否能编译 Graph。

### 提交建议

```text
docs: record current codemedic baseline
```

---

## A1：固定仓库安全边界

### 当前问题

模型现在可以自行填写工具参数中的 `repository_path`，因此理论上可以要求工具读取用户指定仓库之外的其他本地目录。当前工具定义确实把 `repository_path` 暴露给模型。

### 改造目标

仓库根目录只能由程序确定，模型永远只能传相对路径、搜索词等参数。

### 推荐设计

新增：

```text
codemedic/tools/context.py
```

定义受控上下文：

```python
@dataclass(frozen=True)
class RepositoryContext:
    root: Path
```

构建 Investigator 时绑定仓库：

```python
def build_investigator(repository_root: Path):
    root = validate_repository_root(repository_root)

    @tool
    def read_file(file_path: str) -> str:
        return repository_read_file(root, file_path)
```

模型看到的工具参数变成：

```text
list_repo_tree()
search_code(pattern, file_pattern)
read_file(file_path, line_start, line_end)
parse_log(log_content)
```

不再包含 `repository_path`。

### 同时修复

* 拒绝绝对路径；
* 拒绝 `..`；
* 拒绝 Windows 盘符跳转；
* 拒绝 UNC 路径；
* 拒绝符号链接逃逸；
* 增加允许文件后缀集合；
* `read_file` 支持行范围，避免整文件全部进入上下文；
* `list_repo_tree` 增加最大文件数；
* 修复当前无效测试：

```python
assert "...]"
```

改为真实字符串包含断言。

### 必须新增的测试

```text
test_tool_cannot_change_repository_root
test_reject_absolute_windows_path
test_reject_unc_path
test_reject_symlink_escape
test_reject_parent_traversal
test_read_file_line_range
test_tree_file_count_limit
test_large_file_rejected
test_binary_file_rejected
```

### 验收

模型无论生成什么参数，都无法读取绑定仓库之外的文件。

### 提交建议

```text
fix: bind repository tools to validated root
```

---

## A2：重构 Investigator 和 Structured Output

### 当前问题

目前使用已经不适合作为新项目主路径的：

```python
langgraph.prebuilt.create_react_agent
```

同时 Diagnosis 不是模型原生结构化输出，而是通过正则解析文本。默认 Confidence 还被设置为 0.7，容易让解析失败的内容通过 Evidence Gate。

### 改造目标

使用当前 LangChain Agent API，并真正使用 Pydantic Structured Output。

### 推荐实现

```python
from langchain.agents import create_agent
```

概念形式：

```python
agent = create_agent(
    model=model,
    tools=tools,
    system_prompt=INVESTIGATOR_PROMPT,
    response_format=DiagnosisResult,
)
```

模型运行结果必须从结构化字段中读取，而不是正则解析自由文本。

### Provider 兼容策略

增加：

```text
codemedic/models.py
```

定义：

```python
class ProviderCapabilities(BaseModel):
    tool_calling: bool
    structured_output: bool
    token_usage: bool
```

两条路径：

```text
Provider 支持 Structured Output
→ Agent 原生返回 DiagnosisResult

Provider 不支持
→ 第二次独立模型调用进行 Schema 转换
→ 仍必须通过 Pydantic 校验
→ 校验失败则 confidence = 0，进入人工复核
```

不能再使用“正则提取失败但默认 0.7”。

### 最大步数

必须由程序控制，而不是只写进 Prompt。

可采用：

* Agent 中间件调用限制；
* Tool Call Counter；
* LangGraph recursion limit；
* 调用前后统计 ToolMessage 数量。

最终保证：

```text
工具调用次数 ≤ max_investigation_steps
```

### Evidence 验证器

新增：

```text
codemedic/validation/evidence.py
```

负责检查：

* 文件确实存在；
* 文件位于仓库内；
* 行号合法；
* excerpt 与对应行内容基本一致；
* suspected_files 与 Evidence 文件对应；
* 没有 Evidence 时不能高置信度通过。

### Evidence Gate 建议规则

```python
if diagnosis is None:
    return "insufficient"

if diagnosis.confidence < 0.6:
    ...

if not diagnosis.evidence:
    ...

if evidence_validation_failed:
    return "uncertain"

if diagnosis.missing_information:
    ...
```

不能只检查 Confidence。

### 测试

```text
test_structured_diagnosis_success
test_structured_diagnosis_validation_failure
test_missing_confidence_does_not_default_high
test_invalid_evidence_path
test_invalid_evidence_line
test_excerpt_mismatch
test_agent_tool_call_limit
test_provider_timeout
test_provider_without_structured_output
```

### 提交建议

```text
refactor: use structured investigator output
```

---

## A3：Fixer、Diff 和 allowed_files

### 当前问题

`allowed_files` 初始化为空，但之后没有从 Diagnosis 中建立允许修改范围，所以任何 Patch 都可能被判断越界。

Fixer 的文本正则还会让 Rationale、Risks 和 Test Suggestions 互相污染。

### 改造目标

建立严格链路：

```text
已验证 Evidence
→ allowed_files
→ Fixer
→ Diff 解析
→ modified_files
→ allowed_files 比较
```

### allowed_files 来源

只允许来自已验证证据：

```python
allowed_files = sorted({
    evidence.file_path
    for evidence in validated_evidence
})
```

不能完全相信模型生成的 `suspected_files`。

### Fixer 输出

优先使用：

```python
PatchProposal
```

作为 Structured Output，但 Unified Diff 本身仍作为字符串字段。

### Diff 校验

新增：

```text
codemedic/validation/diff.py
```

需要验证：

* 存在 `--- a/...` 和 `+++ b/...`；
* 路径是相对路径；
* 不允许 `/dev/null`，第一版禁止新增和删除文件；
* 不允许 `.git/`；
* 不允许修改 `pyproject.toml`，除非明确允许；
* Diff 中的文件集合必须等于或包含于 `allowed_files`；
* `PatchProposal.modified_files` 必须和 Diff 实际解析结果一致；
* Diff 不能为空；
* Hunk 不能为空；
* Patch 文件数量受限，例如最多 3 个。

### Retry 规则

区分：

```text
patch_attempt_count：包括首次生成
retry_count：只统计失败后的重新生成
```

推荐：

```text
首次 Fixer：retry_count = 0
测试失败后再进入 Fixer：retry_count = 1
retry_count > 1：人工复核
```

### 测试

```text
test_allowed_files_from_validated_evidence
test_diff_file_must_match_allowed_files
test_declared_files_must_match_diff
test_reject_absolute_diff_path
test_reject_git_directory_change
test_reject_file_deletion
test_empty_diff_rejected
test_retry_count_not_incremented_on_first_attempt
```

### 提交建议

```text
fix: enforce patch scope and diff validation
```

---

## A4：修复 Human Review 和 Checkpoint

### 当前问题

当前同时使用：

```python
interrupt_before=["human_review"]
```

以及节点内部：

```python
interrupt(...)
```

这会造成双重中断。

同时自动生成的 `thread_id` 没有返回调用者，导致默认运行后难以恢复。

### 改造方案

只保留节点内部动态 Interrupt：

```python
human_input = interrupt(review_payload)
```

编译时：

```python
graph.compile(checkpointer=checkpointer)
```

不再使用：

```python
interrupt_before=["human_review"]
```

### 状态增加

```python
thread_id: str
workflow_status: Literal[
    "running",
    "waiting_human",
    "completed",
    "failed",
]
```

### 运行返回类型

建议增加：

```python
class WorkflowRunResult(BaseModel):
    thread_id: str
    task_id: str
    interrupted: bool
    state: dict
```

这样 CLI 和 Streamlit 都能获得恢复所需 ID。

### Human Review 输入

只允许：

```text
approved
rejected
retry
```

其他值安全降级为 rejected 或 manual review。

### 状态规则

```text
approved → Sandbox
rejected → final_status = 拒绝
retry 且次数允许 → Fixer
retry 超限 → final_status = 人工复核
```

### Checkpoint 测试

不仅测试 MemorySaver，还要测试真实 SQLite：

```text
第一次进程运行
→ 中断
→ 关闭 Checkpointer
→ 新建 Checkpointer
→ 使用同一 thread_id 恢复
```

### 测试

```text
test_only_one_interrupt_occurs
test_thread_id_returned
test_resume_with_same_thread_id
test_resume_after_reopen_sqlite
test_rejected_status_is_rejected
test_invalid_review_input
test_retry_limit
```

### 提交建议

```text
fix: make human review resumable and deterministic
```

---

## A5：重构 Sandbox、Test Runner 和 Verify Router

### 当前问题

当前手写 Patch Fallback 不尊重 Hunk 位置，可能删除错误位置的同名行，并把新增行追加到文件末尾。

当前 Verify Router 无论测试结果如何都直接返回成功。

### Patch Apply

第一版只保留：

```text
git apply --check
git apply
```

流程：

```python
git apply --check
if returncode != 0:
    PatchApplyResult(success=False)

git apply
if returncode != 0:
    PatchApplyResult(success=False)
```

删除手写 `_apply_with_unidiff()`。

### Sandbox 结果 Schema

新增：

```python
class PatchApplyResult(BaseModel):
    success: bool
    returncode: int
    stdout: str
    stderr: str
    modified_files: list[str]
    sandbox_path: str | None
```

### 真实修改文件检查

应用后执行：

```text
git diff --name-only --no-index
```

或者在应用前后计算文件 Hash。

比较：

```text
实际修改文件
==
Diff 声明文件
⊆
allowed_files
```

### Test Runner

继续保持精确白名单，但建议改用：

```python
sys.executable
```

而不是字符串 `"python"`，确保使用当前 Conda 环境：

```python
[sys.executable, "-m", "pytest", "-q"]
```

测试结果 Schema：

```python
class TestResult(BaseModel):
    command_id: str
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int
    output_truncated: bool
```

### Verify Router 规则

```python
if patch_apply_failed:
    return "manual_review"

if not test_results:
    return "manual_review"

if any(result.timed_out for result in test_results):
    return "manual_review"

if all(result.returncode == 0 for result in test_results):
    return "passed"

if retry_count < max_fixer_retries:
    return "retry"

return "manual_review"
```

### Final Status 规则

```text
用户拒绝 → 拒绝
Patch 应用失败 → 人工复核
测试为空 → 人工复核
测试超时 → 人工复核
所有白名单测试 exit code = 0 → 通过
失败且有重试次数 → 继续 Fixer
失败且无重试次数 → 人工复核
```

### 测试

```text
test_git_apply_check_failure
test_patch_does_not_modify_original_repo
test_actual_files_match_diff
test_patch_out_of_scope
test_non_whitelisted_command
test_test_timeout
test_stdout_truncation
test_stderr_truncation
test_no_test_result_not_passed
test_all_zero_exit_codes_pass
test_failed_test_routes_retry
test_retry_exhausted_routes_manual
test_human_rejection_never_passes
```

### 提交建议

```text
fix: complete sandbox verification workflow
```

---

## A6：集成验收与清理

### 必做清理

删除：

```text
main_project.md
```

它是开发 Prompt 和会话内容，不应作为项目正式源码长期保留。

修订：

```text
README.md
docs/architecture.md
```

只描述真实实现，不提前写 Stage 5～8 已完成。

### 增加 CI

新增：

```text
.github/workflows/ci.yml
```

运行：

```text
Python 3.11
pip install -e .[dev]
pip check
ruff
mypy
pytest
```

CI 不调用真实模型。

### Stage 1～4 最终 E2E

至少建立三个确定性 E2E：

#### E2E 1：正常修复

```text
Diagnosis Mock
→ Patch Mock
→ Human Approve
→ git apply
→ pytest 通过
→ final_status = 通过
```

#### E2E 2：用户拒绝

```text
Diagnosis
→ Patch
→ Human Reject
→ 不创建 Sandbox
→ final_status = 拒绝
```

#### E2E 3：测试失败

```text
Patch 应用成功
→ pytest 失败
→ 重试一次
→ 仍失败
→ final_status = 人工复核
```

### Phase A 完成门槛

必须全部满足：

```text
pytest 全部通过
ruff 通过
mypy 通过或只有明确记录的第三方类型问题
pip check 通过
CI 通过
原仓库不被修改
Human Review 可跨进程恢复
测试失败不能返回“通过”
Patch 越界不能应用
Agent 无法读取目标仓库之外内容
```

只有完成这些，才进入 Stage 5。

---

# 三、Stage 5：Hybrid Retrieval

分支：

```text
feat/stage5-hybrid-retrieval
```

## 目标

实现：

```text
关键词检索
+
向量检索
+
Rank Fusion
+
去重
```

第一版不要上复杂数据库。

---

## 目录

```text
codemedic/retrieval/
├── models.py
├── scanner.py
├── chunker.py
├── lexical.py
├── vector.py
├── fusion.py
└── hybrid.py
```

## Chunk Schema

```python
class CodeChunk(BaseModel):
    chunk_id: str
    file_path: str
    language: str
    symbol: str | None
    line_start: int
    line_end: int
    content: str
    content_hash: str
    chunk_type: str
```

## 文件支持

```text
.py
.cpp
.h
.hpp
.yaml
.yml
.json
.xml
.launch
.md
.txt
```

## 切块策略

### Python

使用 `ast`：

* Module；
* Class；
* Function；
* AsyncFunction。

### C/C++

第一版使用轻量符号识别：

* namespace；
* class；
* struct；
* function；
* 相邻注释。

不要第一版引入复杂编译数据库。

### YAML

按顶层 Key 或 ROS 参数组切分。

### XML / Launch

按：

* `<node>`
* `<arg>`
* `<param>`
* `<remap>`
* `<include>`

切分。

### Markdown

按标题层级。

### 日志

按：

* 时间戳事件；
* Traceback；
* ERROR/FATAL 段；
* ROS node/process 事件。

---

## Lexical Retrieval

优先支持：

* 精确文件名；
* 函数名；
* 类名；
* Topic；
* TF Frame；
* 参数名；
* 错误码；
* Exception 名。

可以使用 Python 实现，检测到 `rg` 时再加速，不能强依赖 `rg.exe`。

## Vector Retrieval

默认使用 LangChain 的轻量 Vector Store。

Embedding Provider 必须可替换：

```text
OpenAI-compatible Embedding
Fake Embedding，用于测试
```

无 Embedding API 时：

```text
自动关闭向量检索
仍可使用关键词检索
```

## Fusion

采用简单 Reciprocal Rank Fusion：

```text
RRF score =
lexical contribution
+
vector contribution
```

增加：

* 路径去重；
* content_hash 去重；
* 同一文件相邻 Chunk 合并；
* Top-K 限制；
* 最大上下文字符限制。

## Stage 5 测试

至少：

```text
Python function chunk
Python class chunk
YAML top-level chunk
Launch node chunk
Markdown heading chunk
Log traceback chunk
Exact function lexical hit
ROS Topic lexical hit
Semantic vector hit
Duplicate chunk removal
RRF deterministic ordering
Vector disabled fallback
Embedding provider failure fallback
Line number accuracy
```

## 验收

使用 6 个种子 Demo：

* Python NameError；
* 可变默认参数；
* Python/YAML 配置键不一致；
* ROS Launch 参数不一致；
* Topic 不一致；
* TF Frame 不一致。

每个案例必须能检索到真实文件和行号。

### 提交建议

```text
feat: add hybrid repository retrieval
```

---

# 四、Stage 6：Streamlit 单页应用

分支：

```text
feat/stage6-streamlit
```

## 核心原则

Streamlit 只负责：

* 接收输入；
* 展示状态；
* 发起工作流；
* 恢复 Human Review；
* 展示 Trace。

不能把业务逻辑重新写一遍。

## 页面状态机

```text
idle
→ analyzing
→ waiting_human
→ verifying
→ completed
→ failed
```

## 左侧栏

* Repository Path；
* Issue；
* Error Log；
* Model；
* Maximum Agent Steps；
* Enable Vector Retrieval；
* Start Analysis。

## 主区域

* 当前 Node；
* Diagnosis；
* Evidence；
* Confidence；
* Modified Files；
* Unified Diff；
* Risks；
* Test Suggestions；
* Approve；
* Reject；
* Retry；
* Test Results；
* Final Status；
* Final Report；
* Trace；
* Latency；
* Token Usage；
* Tool Call Count。

## 防止 Streamlit 重复执行

必须通过：

```python
st.session_state
```

保存：

* `task_id`
* `thread_id`
* `workflow_status`
* `last_action_id`
* `checkpoint_path`
* `final_state`

按钮处理要幂等，避免页面 rerun 后重复批准、重复应用 Patch。

## 手动验收场景

```text
1. 正常分析后出现 Diff
2. 刷新页面仍可恢复
3. 批准后只应用一次 Patch
4. 拒绝后不创建 Sandbox
5. 测试结果正常展示
6. Trace 可展开
7. API 失败时页面不崩溃
8. Vector Retrieval 关闭后仍能运行
```

## 提交建议

```text
feat: add streamlit repair workflow UI
```

---

# 五、Stage 7：Evaluation

分支：

```text
feat/stage7-evaluation
```

## 评测任务

严格建立 24 个：

```text
8 个 Python 问题
8 个 ROS 配置、Launch、Topic、TF、日志问题
4 个 Patch 无法通过测试的问题
4 个证据不足或高风险任务
```

## 每个 Case 的结构

```yaml
task_id:
category:
issue:
error_log:
repository:
expected_root_cause:
expected_files:
expected_evidence:
allowed_files:
expected_status:
test_commands:
risk_level:
```

## 三种方法

### Direct

```text
Issue + 有限上下文
→ 单次模型回答
```

不调用工具。

### Single Agent

```text
一个 Agent
→ 所有只读工具
→ Diagnosis + Patch
```

没有外部 LangGraph Human Review 和 Verifier 流程。

### Multi-Agent

完整 CodeMedic。

## 公平性要求

三种方法必须统一：

* 模型；
* Temperature；
* 最大上下文；
* 最大工具步数；
* 任务输入；
* 重试次数；
* 测试环境。

## 指标

### Diagnosis

* Root Cause Accuracy；
* Correct File Hit Rate；
* Evidence Precision；
* Evidence Line Accuracy。

### Patch

* Diff Parse Rate；
* Patch Apply Rate；
* Test Pass Rate；
* Out-of-scope Modification Rate。

### Agent

* Tool Argument Error Rate；
* Agent Steps；
* Retry Count；
* Human Review Rate；
* Stability Across Runs。

### 成本

* Latency；
* Input Tokens；
* Output Tokens；
* Total Tokens。

Provider 没有返回 Token 时记录：

```text
null
```

不能伪造估计数字。

## 稳定性

每个非确定性任务至少运行三次。

结果保存在：

```text
eval/results/
├── raw/
├── summary.json
└── report.md
```

## 验收

报告必须同时呈现：

* Multi-Agent 优势；
* Multi-Agent 劣势；
* 失败案例；
* 成本差异；
* 人工复核比例；
* 不能得出明确结论的项目。

### 提交建议

```text
feat: add codemedic evaluation suite
```

---

# 六、Stage 8：项目包装

分支：

```text
docs/stage8-packaging
```

## README 最终结构

```text
1. 项目解决什么问题
2. 为什么不是普通代码问答
3. 系统架构
4. 为什么使用 LangChain
5. 为什么使用 LangGraph
6. 三 Agent 职责
7. Human-in-the-loop
8. Hybrid Retrieval
9. 安全边界
10. Windows 安装
11. CLI 运行
12. Streamlit 运行
13. 三个 Demo
14. Evaluation 结果
15. 项目局限
16. AI Coding 中我的职责
```

## 三个最终 Demo

### Demo 1：Python 逻辑错误

```text
NameError 或变量拼写
→ 证据定位
→ Diff
→ 审批
→ pytest 通过
```

### Demo 2：Python + YAML 跨文件错误

```text
配置键不一致
→ Hybrid Retrieval
→ 跨文件证据
→ Patch
→ 测试通过
```

### Demo 3：ROS Topic / TF / Launch

```text
运行日志
→ Topic 或 Frame 不一致
→ 配置定位
→ 人工审批
→ 静态验证或白名单测试
```

## Demo 资料

准备：

```text
docs/demo_guide.md
docs/interview_questions.md
docs/limitations.md
assets/architecture.png
assets/demo.gif
```

## 简历 Bullet

最终数字必须来自 Stage 7，不提前填写。

示例结构：

```text
基于 LangChain 与 LangGraph 构建 Investigator、Fixer、Verifier 三 Agent
代码诊断与修复流程，完成代码检索、根因定位、Diff 生成、人工审批和
测试验证闭环。
```

后续加入真实数字：

```text
在 24 个 Python/ROS 任务上……
根因命中率……
Patch Apply Rate……
测试通过率……
```

## 版本发布

全部通过后：

```text
tag: v0.1.0
release: CodeMedic Demo Release
```

---

# 七、全项目测试矩阵

最终测试至少分为五层。

## 单元测试

* Schema；
* Path；
* Chunker；
* Rank Fusion；
* Diff Parser；
* Router；
* Trace。

## 安全测试

* 路径穿越；
* 绝对路径；
* 符号链接；
* 超大文件；
* 二进制；
* Patch 越界；
* 非白名单命令；
* Shell 注入；
* 原仓库修改检查。

## Agent 测试

* Tool 参数错误；
* Structured Output 失败；
* Provider Timeout；
* Tool Call Limit；
* Empty Evidence；
* Invalid Evidence。

## Graph 测试

* Evidence Loop；
* Retry；
* Interrupt；
* Resume；
* Reject；
* Checkpoint；
* Verify Router；
* Infinite Loop Prevention。

## E2E

* 正常通过；
* 人工拒绝；
* Patch 失败；
* 测试失败后重试；
* Timeout；
* 证据不足；
* Provider 不可用。

---

# 八、每轮交给 Claude 的固定执行方式

以后每一轮都使用同样的节奏。

## 开始前

Claude 必须先输出：

```text
当前分支
当前 Git 状态
现有测试结果
本轮目标
计划修改文件
明确不修改的范围
验收条件
```

## 实现时

顺序固定：

```text
1. 最小实现
2. 单元测试
3. 负向测试
4. 集成测试
5. Ruff
6. mypy
7. pip check
8. Git diff 审查
```

## 结束时

必须输出：

```text
已完成功能
修改文件
关键设计
测试命令
真实 exit code
通过/失败数量
遗留问题
下一阶段建议
建议 commit message
```

然后停止，不能自动进入下一阶段。

---

# 九、当前最合理的下一轮

现在不要做 Hybrid Retrieval。

下一轮只做：

```text
Phase A / A0～A2
```

也就是：

```text
建立基线
修复仓库路径安全边界
重构 Investigator
使用真实 Structured Output
实现工具调用步数限制
实现 Evidence 校验
```

完成并审查后，再做：

```text
A3～A5
Fixer、Human Review、Sandbox、Verifier
```

这样能把单轮改动控制在可审查范围内，也能避免 Claude 再次一次性铺开整个项目。

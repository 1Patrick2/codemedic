# CodeMedic

基于 LangChain 与 LangGraph 的多 Agent 代码诊断与修复系统。

## 快速开始

```bash
# 创建 Conda 环境
conda create -n codemedic python=3.11
conda activate codemedic

# 安装依赖
pip install .

# 开发模式（含测试工具）
pip install -e ".[dev]"

# 复制环境变量
cp .env.example .env
# 编辑 .env，填入 API Key 等信息

# 运行
python -m codemedic.cli --issue "..." --repo-path ./demo_repos/my_project

# 测试
python -m pytest
python -m ruff check .
python -m mypy codemedic
```

## 项目结构

```
codemedic/
├── codemedic/          # 核心包
│   ├── agents/         # Investigator / Fixer / Verifier
│   ├── graph/          # LangGraph 状态机
│   ├── tools/          # 核心工具 (只读 + Diff + 测试)
│   ├── retrieval/      # 混合检索
│   ├── schemas/        # Pydantic 结构定义
│   └── tracing/        # 本地 JSONL Trace
├── tests/              # 测试
├── docs/               # 文档
├── demo_repos/         # 示例仓库
└── runtime/            # 运行产物 (checkpoints, traces, sandboxes)
```

## 开发阶段

| Stage | 内容 |
|-------|------|
| 0 | 环境、依赖、项目骨架 |
| 1 | Investigator 单 Agent 基线 |
| 2 | LangGraph 外层工作流 |
| 3 | Fixer + Human Review |
| 4 | Verifier + Sandbox |
| 5 | Hybrid Retrieval |
| 6 | Streamlit UI |
| 7 | Evaluation |
| 8 | 项目包装 |

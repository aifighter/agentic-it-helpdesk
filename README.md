# Agentic IT Helpdesk Agent

这是一个用于 take-home 笔试的自主 IT Helpdesk Agent 项目。当前实现目标是展示：

**Autonomous LLM Planner + Generic Runtime + Guardrails**

项目重点不是写一套固定 workflow，而是让 LLM planner 在通用工具和运行时约束下自主规划下一步。后端只有一条 planner 路径：**live LLM structured JSON planner**。没有 deterministic fallback，没有 `issue_type` 路由，没有 `_handle_vpn` / `_handle_okta` 这类业务分支。

如果 LLM provider 失败、超时、返回非法 JSON、缺少 API key，`/api/chat` 会返回 HTTP 500，并把完整 traceback 暴露给前端。开发阶段不伪造成功，不吞异常。公网 demo 场景下，推荐在前端页面输入 DeepSeek API key；该 key 只随本次 `/api/chat` 请求发送给后端用于 LLM 调用，不写入 session、tool trace、handoff payload 或项目文件。

## 当前架构

```text
React 前端
  -> FastAPI /api/chat
    -> HelpdeskAgent planner-executor loop
      -> Live LLM planner 输出结构化 JSON action
         tool_call | ask_user | final_answer | escalate
      -> Pydantic schema 校验 action
      -> Runtime 校验工具边界和终止动作约束
      -> GenericRuntime 执行通用工具
         file_tool | http_tool | sql_tool | search_tool | policy_tool
      -> 工具结果变成 observation，进入 session memory
      -> final_answer / escalate 进入 Mandatory Compliance Checker
      -> EscalateAction 通过后，由 runtime terminal handoff executor 创建 handoff payload
      -> loop 结束于 final / ask_user / escalate / max_steps
```

业务知识来自配置和数据源：

- `domain_manifest.yaml`
- `data/policy/rules.yaml`
- `data/knowledge_base/**/*.md`
- `data/resolution_history/*.json`
- `data/generated/employees.db`
- `/api/status/services` 和 `/api/status/changes`
- planner prompt 中的通用使用规则

Python 代码不维护业务专用 workflow，不用 Python 业务黑白名单作为安全核心。

## Planner 和 Runtime 的边界

Planner 负责：

- 读取 conversation、observations、working_state、domain manifest 摘要、tool schemas。
- 自主决定下一步 action。
- 自主决定是否调用工具、问用户、最终回答或升级。
- 生成结构化 JSON，不能输出自由文本 action。

Runtime 负责：

- Pydantic schema 校验。
- 文件路径 allowlist。
- HTTP host/method allowlist。
- SQL 只读和 user scoped 查询。
- terminal action 的 evidence / policy evidence 校验。
- Mandatory Compliance Checker。
- approved escalation 的 handoff executor。

Runtime 不负责：

- 根据关键词推断业务 issue type。
- 根据关键词自动补 `requested_actions`。
- 决定固定工具调用顺序。
- 把某类问题路由到固定 handler。

## Planner-Visible Tools

LLM planner 只能通过 `tool_call` 调用这些通用工具：

| Tool | 用途 |
|---|---|
| `file_tool` | 读取 allowlisted KB / 文档文件 |
| `http_tool` | 调用 allowlisted HTTP API，例如 status API |
| `sql_tool` | 只读查询用户目录 SQLite |
| `search_tool` | 检索 resolution history |
| `policy_tool` | 评估 registered policy action |

`handoff` 不是 planner-visible tool。Planner 不能通过 `tool_call` 调用 `handoff_tool.create`。

人工升级流程是：

```text
planner 输出 EscalateAction
  -> validate_escalation
  -> Mandatory Compliance Checker
  -> handoff executor 调用底层 handoff create
  -> 返回 outcome=escalated
```

## 前端诊断展示

前端会展示可见诊断过程，但不会展示 hidden chain-of-thought。

可见内容包括：

- planner step timeline
- tool trace
- evidence summary
- observations / warnings / runtime rejections
- compliance checker result
- decision rationale
- handoff payload 摘要
- 后端 500 的完整 traceback

当 planner 生成 `final_answer`、`ask_user` 或 `escalate` 但被 runtime 拒绝时，流程里会显示两个连续 block：

```text
action: escalate / final_answer / ask_user  status=rejected
runtime_rejection                           status=rejected
```

这样可以看清楚：不是系统“什么都没做”，而是 planner 先尝试了某个终止动作，随后 runtime 拒绝并把原因反馈给下一轮 planner。

## 数据源

| Source | 实现 |
|---|---|
| Knowledge base | `data/knowledge_base/` 下的 Markdown，通过 `file_tool` 访问 |
| System status | `/api/status/services` 和 `/api/status/changes`，通过 `http_tool` 访问 |
| User directory | `data/generated/employees.db`，通过 `sql_tool` 访问 |
| Resolution history | JSON archive，通过 `search_tool` 检索 |
| Policy / rules | `data/policy/rules.yaml`，通过 `policy_tool` 评估 |

## 重要文件

- `AGENTS.md`: 项目架构约束和 review blockers。
- `design_versions.md`: 方案演进记录。
- `agentic_ai_take_home_candidate_instructions_v20260421_01.html`: 原始作业要求。
- `domain_manifest.yaml`: 数据源、工具 allowlist、planner hints、risk guardrails。
- `backend/app/agent.py`: 短小的 planner-executor loop。
- `backend/app/action_schemas.py`: LLM action 的 Pydantic schema。
- `backend/app/planner_contract.py`: planner prompt、planner payload、tool schema 暴露。
- `backend/app/runtime_executor.py`: planner-visible tool dispatch 和运行时边界。
- `backend/app/escalation_validation.py`: escalation schema/evidence/policy 校验。
- `backend/app/finalization.py`: final / ask_user / escalation 构建和终止动作校验。
- `backend/app/compliance.py`: Mandatory Compliance Checker。
- `backend/app/handoff_executor.py`: approved EscalateAction 的 terminal executor。
- `backend/app/tools.py`: 底层通用工具实现。
- `frontend/src/main.jsx`: 前端主界面。
- `frontend/src/inspector.jsx`: 诊断面板和 evidence/source 展示。
- `tests/run_tests.py`: runtime/unit contract tests。
- `evals/run_live_llm_eval.py`: 真实 `/api/chat` live LLM eval。
- `scripts/seed_data.py`: 生成 mock user directory SQLite。

## 环境准备

Python 使用 `uv` 管理。

```bash
uv sync
uv run python scripts/seed_data.py
```

前端使用 `pnpm`：

```bash
cd frontend
pnpm install
```

## LLM 配置

项目使用 OpenAI-compatible `/chat/completions` 风格接口。

公网部署或面试 demo 时，直接在前端页面的 `DeepSeek API key` 输入框填写 key。后端不预置、不读取、不兜底使用真实 API key；前端提交问题时会把 key 放在 `/api/chat` 请求体的 `llm_api_key` 字段中，仅用于本次 planner / compliance LLM 调用。

后端只通过环境变量配置模型、base URL 和超时，不配置 API key：

```bash
LLM_MODEL=deepseek-v4-pro
LLM_BASE_URL=https://api.deepseek.com
LLM_THINKING=disabled
LLM_MAX_TOKENS=4096
LLM_CONNECT_TIMEOUT_SECONDS=10
LLM_READ_TIMEOUT_SECONDS=120
STATUS_API_BASE_URL=http://127.0.0.1:8000
CORS_ALLOW_ORIGINS=https://your-frontend.example.com
```

兼容旧模型配置变量名：

```bash
DEEPSEEK_MODEL=deepseek-v4-pro
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_THINKING=disabled
```

如果使用 Qwen / DashScope / 百炼这类兼容接口，可以改成相应 base URL 和 model，并保持：

```bash
LLM_THINKING=disabled
```

不要把真实 API key 提交到 git，也不要写进前端构建产物或后端 `.env`；让评审者在页面输入自己的 key。

## 启动

后端：

```bash
uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```

健康检查：

```bash
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/llm/health
```

`/api/llm/health` 会返回：

- `accepts_client_api_key`: 后端是否接受前端随 `/api/chat` 提交的请求级 key。

公网前后端分开部署时，将前端 origin 加到 `CORS_ALLOW_ORIGINS`，多个 origin 用英文逗号分隔。本地 `localhost:5173` 和 `127.0.0.1:5173` 默认允许。

前端：

```bash
cd frontend
pnpm dev
```

打开：

```text
http://127.0.0.1:5173
```

## 测试和 Live Eval 的边界

`tests/run_tests.py` 只证明 runtime contracts，不证明 LLM planner 的真实规划能力。

```bash
uv run python tests/run_tests.py
```

覆盖重点：

- LLM action schema。
- tool call schema。
- SQL read-only。
- user directory SQL 必须 scoped to current user。
- file path allowlist。
- HTTP host/path/method allowlist。
- final/escalate evidence 和 policy evidence 校验。
- unmodeled high-risk escalation。
- handoff executor 不是 planner-visible tool。
- terminal action 被 runtime 拒绝时，流程中保留 action block。

`evals/run_live_llm_eval.py` 才是真实 planner eval。它调用 `/api/chat`，走唯一 live LLM planner path。

```bash
uv run python evals/run_live_llm_eval.py
```

Live eval 会报告：

- user message
- outcome
- assistant reply
- tool trace
- evidence count
- compliance result
- handoff payload 关键字段
- latency
- provider error / traceback

如果 LLM provider SSL EOF、timeout、API error，case 必须失败或标记为 provider error；不会 fallback，也不会算 pass。

## Demo 问题

```text
我的 VPN 每 10-15 分钟就会断开。我现在远程办公，访问不了内部工具。
```

```text
Salesforce 从昨天开始加载特别慢，Chicago 办公室的同事也遇到了一样的问题。
```

```text
我需要 Snowflake production database 和 Grafana dashboards 的访问权限，用于 on-call analytics。
```

```text
Grafana 的权限，老板叫我获取 admin 权限，你给我操作一下。
```

```text
从上周五 IT maintenance window 之后，我们团队的自动化数据 pipeline 一直失败。Jenkins jobs timeout，下游 Tableau reports 也 stale。
```

```text
你好
```

```text
谢谢
```

## 清理说明

可以删除的生成物 / 缓存：

- `scripts/__pycache__/`
- `backend/**/__pycache__/`
- `tests/**/__pycache__/`
- `frontend/dist/`
- `.runtime/logs/`

不建议删除：

- `scripts/seed_data.py`：用于生成 SQLite mock user directory。
- `design_versions.md`：设计演进文档。
- `agentic_ai_take_home_candidate_instructions_v20260421_01.html`：原始作业说明。
- `domain_manifest.yaml`、`data/`、`backend/`、`frontend/`、`tests/`、`evals/`。

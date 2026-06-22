# Agentic IT Helpdesk Agent

这是一个 Agentic AI Engineer take-home 项目：面向公司员工的自主 IT Helpdesk Agent。目标不是做一个固定流程的 ticket bot，而是展示一个能在通用工具和运行时护栏下自主规划、查询证据、判断解决或升级的 IT 支持 agent。

当前实现方向：

```text
Autonomous LLM Planner + Generic Runtime + Guardrails
```

后端只有一条真实 planner 路径：live LLM structured JSON planner。没有 deterministic fallback，没有 `issue_type` 分类路由，也没有 `_handle_vpn` / `_handle_okta` 这类业务 handler。

如果 LLM provider 失败、超时、返回非法 JSON、缺少 API key，`/api/chat` 会返回 HTTP 500，并把完整 traceback 暴露给前端。开发阶段不伪造成功，不吞异常。

## 交付清单

本仓库包含 take-home review 所需内容：

- 可本地运行的前后端项目。
- 中文 README，说明问题定义、架构设计、数据源、运行方式、评估方式和已知取舍。
- `design_versions.md`，记录从 v1/v2 到 v3/v3.1 的设计演进。
- synthetic data、KB、policy、status API、user directory、resolution history。
- runtime/unit tests 和 live LLM eval 入口。
- 受 Basic Auth 保护的公网 demo 部署配置。

## 1. 问题定义

传统 IT 支持通常要求员工先提 ticket，再等待人工分派、排查和追问。很多常见问题其实有明确 runbook、状态数据、用户目录和历史案例，但仍然消耗人工处理时间。

本项目选择的产品问题是：

> 让员工直接和 AI IT Helpdesk Agent 对话，由 agent 作为 IT 问题的第一入口，尽可能在几分钟内完成初步诊断、直接解决或带完整上下文升级给人工。

目标用户是员工，而不是 helpdesk agent。员工只需要用自然语言描述问题，例如：

- VPN 频繁断开
- Okta 登录失败 / 账号锁定
- Salesforce 加载慢
- 生产数据库 / Grafana dashboards 权限申请
- Jenkins / Tableau / pipeline 多系统故障
- 未接入系统或高风险 admin 权限请求

成功体验是：员工不用填写工单表单；agent 会主动查询相关信息、给出可执行结论，或者在需要人工审批/处理时生成完整 handoff，避免员工重复解释。

## 2. 为什么需要 Agentic Approach

这个问题不适合只用 FAQ search 或单轮 chatbot：

- 员工问题通常是不完整的，需要多轮澄清。
- 同一个问题可能需要查多个来源：KB、系统状态、用户目录、历史案例、policy。
- agent 需要根据证据判断“能直接解决”还是“必须升级”。
- 权限类请求有安全边界，不能只靠 LLM 自觉。
- 现场面试会输入新问题，不能依赖固定 demo case。

因此当前设计采用 planner-executor loop：

1. LLM planner 读取 conversation、observations、working_state、domain manifest、tool schemas。
2. planner 输出结构化 JSON action。
3. runtime 校验 action schema 和工具边界。
4. tool 结果变成 observation，进入 memory。
5. planner 根据新 observation 决定下一步。
6. 终止于 `final_answer` / `ask_user` / `escalate` / `max_steps`。

planner 可输出的 action 类型：

```text
tool_call | ask_user | final_answer | escalate
```

## 3. 架构概览

```text
React Frontend
  -> FastAPI /api/chat
    -> HelpdeskAgent planner-executor loop
      -> LLM planner outputs structured JSON action
      -> Pydantic schema validation
      -> Runtime guardrails
      -> GenericRuntime executes planner-visible tools
         file_tool | http_tool | sql_tool | search_tool | policy_tool
      -> Observation memory
      -> Final / Escalate validation
      -> Mandatory Compliance Checker
      -> Handoff executor for approved escalation
```

关键原则：

- 业务知识放在 `domain_manifest.yaml`、policy、KB、数据文件和 prompt 中。
- Python 不维护业务 workflow。
- Python 不用关键词匹配决定控制流或 guardrail；关键词只存在于检索工具内部，或作为 manifest/prompt 给 planner 的提示。
- Runtime 只做 schema、安全边界、证据和 policy guardrail。
- Planner 决定下一步查什么，不由后端写死工具调用顺序。
- Handoff 不是 planner-visible tool，只能由通过校验的 `EscalateAction` 触发。

## 4. Planner / Runtime 边界

Planner 负责：

- 选择下一步 action。
- 决定是否调用工具、追问、最终回答或升级。
- 生成 `requested_actions` / `unmodeled_actions` / `risk_assessment`。
- 使用 observations 组织可见诊断摘要。

Runtime 负责：

- 校验 LLM 输出 JSON schema。
- 校验 file path allowlist。
- 校验 HTTP host/method allowlist。
- 校验 SQL 只读和 user-scoped 查询。
- 校验 final/escalate 是否引用足够 evidence 和 policy evidence。
- 强制执行 Mandatory Compliance Checker。
- 对通过审核的 `EscalateAction` 执行 handoff executor。

Runtime 不做：

- 不按 `issue_type` 路由。
- 不根据关键词补业务 action。
- 不写死“先查 DB，再查 KB，再查 history”的流程。
- 不把 handoff 暴露成 planner 直接调用的普通 tool。

## 5. 数据源设计

作业要求模拟内部数据源。本项目准备了 5 类数据源：

| Source | 实现 | Agent 如何使用 |
|---|---|---|
| Knowledge base | `data/knowledge_base/**/*.md` | runbook、排障步骤、访问申请说明 |
| System status | `/api/status/services`、`/api/status/changes` | 当前服务健康状态、变更记录 |
| User directory | `data/generated/employees.db` | 员工部门、角色、设备、MFA、风险状态、访问组 |
| Resolution history | `data/resolution_history/*.json` | 相似历史案例、已知 workaround、升级上下文 |
| Policy / rules | `data/policy/rules.yaml` | 判断 agent 是否可直接解决，或必须审批/升级 |

数据不追求大，而是覆盖多源推理场景。例如：

- VPN 问题需要用户设备 + KB + policy。
- Salesforce 慢加载需要系统状态 + 办公室/历史案例。
- 生产访问申请需要用户目录 + policy + handoff。
- Grafana admin / Jenkins admin 这类未建模高风险请求必须升级。

## 6. Resolution vs Escalation 边界

Agent 可以直接解决：

- 明确被 policy 允许的低风险排障建议。
- KB/status/user directory 支持的可执行说明。
- 普通问候或能力说明，返回 `acknowledged`，不启动诊断 case。

Agent 必须升级：

- policy 明确要求人工审批。
- 生产访问、admin/privileged access、credential/firewall/change 等高风险请求。
- 没有精确 registered policy action 的高风险请求。
- 当前 agent 未接入系统、KB、状态接口或管理工具，不能可靠处理。
- evidence 不足以支持 resolved，但问题仍需要 IT 支持。

升级时，agent 会生成 handoff payload，包含：

- 原始用户请求
- 用户上下文
- 已查询工具和 evidence
- policy 结果
- requested / unmodeled actions
- risk assessment
- 升级 team 和原因

## 7. 前端体验和可观测性

前端不是只显示聊天结果，还展示可见诊断过程：

- planner step timeline
- tool trace
- evidence summary
- observations / warnings / runtime rejections
- compliance checker result
- decision rationale
- handoff payload 摘要
- 后端 500 的完整 traceback

不会展示 hidden chain-of-thought，只展示 `thought_summary` 和诊断摘要。

当 planner 生成 `final_answer`、`ask_user` 或 `escalate` 但被 runtime 拒绝时，流程里会显示：

```text
action: escalate / final_answer / ask_user  status=rejected
runtime_rejection                           status=rejected
```

这样可以看清楚 planner 尝试了什么、runtime 为什么拒绝、下一轮如何修正。

## 8. 重要文件

- `AGENTS.md`: 项目架构约束和 review blockers。
- `design_versions.md`: v1/v2/v3/v3.1 设计演进记录。
- `agentic_ai_take_home_candidate_instructions_v20260421_01.html`: 原始作业说明。
- `domain_manifest.yaml`: 数据源、工具 allowlist、planner hints、risk guardrails。
- `backend/app/agent.py`: planner-executor loop。
- `backend/app/schemas.py`: Chat、observation、tool trace、LLM action、compliance 等结构化 schema。
- `backend/app/planner_contract.py`: planner prompt、payload、tool schema。
- `backend/app/runtime_executor.py`: planner-visible tool dispatch。
- `backend/app/finalization.py`: final / ask_user / escalation 构建和校验。
- `backend/app/escalation.py`: escalation evidence / policy 校验、reply 组装、approved handoff terminal executor。
- `backend/app/compliance.py`: Mandatory Compliance Checker 和终止动作 compliance gate。
- `frontend/src/main.jsx`: 主聊天界面。
- `frontend/src/inspector.jsx`: 诊断面板。
- `tests/run_tests.py`: runtime/unit tests。
- `evals/run_live_llm_eval.py`: live LLM eval。
- `scripts/seed_data.py`: 生成 mock SQLite user directory。

## 9. 本地运行

### 9.1 准备 Python 环境

Python 使用 `uv` 管理：

```bash
uv sync
uv run python scripts/seed_data.py
```

### 9.2 准备前端环境

前端使用 `pnpm`：

```bash
cd frontend
pnpm install
cd ..
```

### 9.3 配置 LLM

项目使用 OpenAI-compatible `/chat/completions` 接口。推荐在项目根目录 `.env` 中配置：

```bash
LLM_API_KEY=your_key_here
LLM_MODEL=deepseek-v4-pro
LLM_BASE_URL=https://api.deepseek.com
LLM_THINKING=disabled
LLM_MAX_TOKENS=4096
LLM_CONNECT_TIMEOUT_SECONDS=10
LLM_READ_TIMEOUT_SECONDS=120
STATUS_API_BASE_URL=http://127.0.0.1:8000
```

兼容旧变量名：

```bash
DEEPSEEK_API_KEY=your_key_here
DEEPSEEK_MODEL=deepseek-v4-pro
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_THINKING=disabled
```

不要把真实 API key 提交到 git。

### 9.4 启动后端

```bash
uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```

健康检查：

```bash
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/llm/health
```

### 9.5 启动前端

```bash
cd frontend
pnpm dev
```

打开：

```text
http://127.0.0.1:5173
```

前端默认通过同源 `/api` 调用后端；本地 Vite 会把 `/api` 代理到 `http://127.0.0.1:8000`。

## 10. 测试和评估

本项目刻意区分 runtime tests 和 live LLM eval。

### 10.1 Runtime / Unit Tests

```bash
uv run python tests/run_tests.py
```

这些测试证明 runtime contract，不证明 LLM 规划能力。覆盖重点：

- LLM action schema。
- planner-visible tool schema。
- handoff 不可被 planner 直接 tool_call。
- SQL read-only。
- user directory SQL 必须 scoped to current user。
- file path allowlist。
- HTTP host/method allowlist。
- final/escalate evidence 和 policy evidence 校验。
- unmodeled high-risk escalation。
- terminal action 被拒时，流程中保留 action block。

### 10.2 Frontend Build

```bash
cd frontend
pnpm build
```

### 10.3 Live LLM Eval

```bash
uv run python evals/run_live_llm_eval.py
```

Live eval 调用真实 `/api/chat`，走唯一 live LLM planner path。它会报告：

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

## 11. Demo Prompts

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

## 12. 公网 Demo 部署

项目提供 `deploy/Caddyfile`，用 Caddy Basic Auth 保护整个站点，包括 `/api/chat`。

部署原则：

- 只暴露 Caddy 的 80/443。
- FastAPI 只绑定 `127.0.0.1:8000`。
- 前端使用 `frontend/dist` 静态文件，由 Caddy 托管。
- 真实 LLM API key 只配置在服务器 `.env` 中。

本项目已验证过的部署方式：

```bash
uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
cd frontend && pnpm build
PUBLIC_HOST=your-domain.example.com caddy run --config deploy/Caddyfile
```

如果只有公网 IP、没有域名，可以让 Caddy 默认监听 `:80`。Basic Auth 用户名为 `interviewer`，密码应单独发给面试官；配置文件中只保存 bcrypt hash。

更换密码：

```bash
htpasswd -bnB -C 14 interviewer 'new-password'
```

然后把生成结果中冒号后面的 hash 更新到 `deploy/Caddyfile`。

## 13. 假设和 Tradeoffs

- 使用 synthetic data，而不接真实 Okta、ServiceNow、Snowflake、Grafana。
- Handoff 只模拟创建 payload，没有真正写入 ServiceNow/Jira。
- 使用 live LLM structured JSON planner，不提供 deterministic fallback。
- 开发阶段错误直接暴露 traceback，便于 review 和调试；生产环境应改成更安全的错误展示。
- Basic Auth 足够保护 take-home demo，但正式生产应使用 SSO / VPN / Cloudflare Access。
- Resolution history 是轻量 JSON 搜索，不是完整向量数据库。
- Policy/compliance 是 guardrail，不替代真实审批系统。

## 14. 如果继续生产化

后续可以改进：

- Slack / Teams 作为员工入口。
- SSO 获取真实用户身份，而不是前端选择 mock profile。
- ServiceNow / Jira / PagerDuty handoff。
- 审批系统集成，例如 manager / data owner / security approval。
- 审计日志和红队测试。
- 更完整的 eval harness 和 regression suite。
- Provider timeout / retry / circuit breaker。
- 权限分层：员工、support engineer、admin 不同能力。
- RAG / hybrid search 替换当前轻量 search archive。

## 15. 清理说明

可以删除的生成物 / 缓存：

- `scripts/__pycache__/`
- `backend/**/__pycache__/`
- `tests/**/__pycache__/`
- `frontend/dist/`
- `.runtime/logs/`

不建议删除：

- `scripts/seed_data.py`
- `design_versions.md`
- `agentic_ai_take_home_candidate_instructions_v20260421_01.html`
- `domain_manifest.yaml`
- `data/`
- `backend/`
- `frontend/`
- `tests/`
- `evals/`

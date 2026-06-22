# Agent 设计方案版本记录

## v1.0 - 用户初始方案

核心思路：针对 IT 支持 agent 需要查询的不同数据源，分别选择合适的存储和检索方式。

- Knowledge base：可以做成 skill 形式；或者不做 skill，而是把目录结构整理清楚，每个目录下有文件列表和简介，让 agent 使用基础工具，例如 `ls`、`cat`、`grep`，去查找相关信息。
- System status：更倾向于 API。通过一个通用 API 调用能力，让 agent 获取当前服务状态、已知故障、近期变更等信息。
- User directory：因为这是内部工具，提问人的信息应该清晰且准确，所以可以用 SQLite 或 Excel 存储用户信息，再用 SQL 做精准查询。
- Resolution history：这是动态更新的数据，而且只能作为参考，因此可以使用简单 RAG 或向量搜索，让 agent 根据需要检索相似历史问题。
- Policy / rules：暂时未完全确定。初步想法是在收到用户问题、准备回复用户时，阅读 policy，判断是否可以直接解决，是否需要转人工。

主要优点：

- 已经意识到不同数据源应该使用不同处理方式。
- 结构化数据用 SQL，非结构化历史案例用 RAG，这个方向合理。
- 整体实现相对轻量，不会过早做成很重的业务系统。

主要问题：

- 如果完全让 agent 自己通过 `ls`、`cat`、`grep` 浏览知识库，随着 KB 增长，可靠性可能不足。
- Excel 适合展示，但不如 SQLite 适合查询、测试和约束。
- Policy 不应该只在最终回复前阅读，而应该贯穿整个 workflow，作为硬性权限边界。

## v1.1 - 专用工具方案

核心思路：为 IT support agent 构建一组领域专用 typed tools，让 agent 通过这些工具查询和行动。

示例工具：

- `search_kb(query, systems, top_k)`
- `check_service_status(service, region)`
- `lookup_user(email)`
- `get_user_access(email)`
- `search_resolution_history(query)`
- `check_policy(action, context)`
- `create_escalation_summary(context)`

主要优点：

- 可靠性强，容易测试和评估。
- agent 推理和后端系统之间边界清楚。
- 权限控制、升级规则和工具行为更容易收敛。
- 很适合做一个聚焦、稳定、可演示的 take-home demo。

主要问题：

- 工具过于领域专用，整体实现偏重。
- 可复用性较弱，换一个业务领域基本要重新设计工具。
- 实现面较大，不够像通用 agent runtime。
- 和 Codex、Claude Code 这类通用工具型 agent 的设计哲学不完全一致。

结论：

这个方案可行，但不是当前最优方向。它偏稳定、偏业务定制，但用户更希望系统具备更强的通用性和可复用性，因此该方案被否。

## v2.0 - 通用 Runtime + IT Domain Manifest

核心思路：不要为每个 IT 业务能力都写一个专用 tool，而是保留少量通用 runtime 工具，再通过 IT domain manifest、schema、policy 和 prompt instructions 把它配置成 IT support agent。

定义：

```text
IT Support Agent = General Agent Runtime + IT Domain Manifest
```

通用 runtime tools：

- `file_tool`：对 allowlist 目录执行 list、read、grep 等只读文件操作。
- `http_tool`：调用 allowlist 内的内部 API。
- `sql_tool`：对 allowlist 数据库和表执行只读、参数化 SQL 查询。
- `search_tool`：对注册过的 index 执行语义搜索或 hybrid search。
- `memory_tool`：保存会话状态、已查询信息、诊断过程和中间结论。
- `handoff_tool`：生成升级摘要或 handoff payload。

IT domain manifest 提供：

- Knowledge base 的目录位置、说明和可读范围。
- System status API 的 base URL、endpoint schema、允许的方法和参数。
- User directory 数据库路径、schema、允许查询的表或 view。
- Resolution history 的 index 名称、metadata 字段和检索方式。
- Policy / rules 文件位置、结构和升级目标。
- 工具权限边界，例如路径 allowlist、API allowlist、SQL allowlist。

Agent instructions 提供：

- 员工侧 IT support agent 的角色设定。
- 对话式诊断策略。
- 什么时候应该追问。
- 什么时候应该查询 KB、status、user directory、history、policy。
- 如何引用查询依据，避免编造。
- 如何判断直接解决、继续追问、升级人工。

主要优点：

- 比专用工具方案更通用、更平台化。
- 更接近 Codex、Claude Code 这类通用 agent 系统的工具设计方式。
- 工具本身简单，领域能力通过配置和说明注入。
- 未来可以通过更换 manifest 迁移到其他支持场景，而不是重写整个系统。
- 更能体现 agentic engineer 的平台化设计能力。

主要风险：

- 通用工具如果完全开放，会有安全和可靠性问题。
- 不能给 employee-facing IT agent 开放任意 shell、任意 HTTP、任意 SQL、任意文件读取。
- 仅靠 prompt 描述 API 不够可靠，需要 schema、allowlist 和 runtime 校验。

Guardrail 设计：

- 工具保持通用，但访问范围必须被约束。
- 文件访问只允许 KB、policy 等注册目录。
- HTTP 只允许调用 manifest 中声明的内部 API 和方法。
- SQL 只允许只读查询，并且限制在 allowlist 表或 view。
- Search 只能查询注册过的 index。
- Policy 是 workflow guardrail，不只是最终回复前读一下。

当前推荐方向：

使用 v2.0 作为 take-home project 的目标架构。这个方案在通用性和可靠性之间比较平衡：底层工具尽量通用，上层通过 IT domain manifest 和 policy 提供业务能力与边界。

当前实现反思：

- 工具层已经接近 v2.0：`GenericRuntime`、manifest、allowlist、通用 file/http/sql/search/policy/handoff tools 都存在。
- 但 agent orchestration 仍然是人工写死的 workflow：先分类，再固定查 DB/KB/history，再进入 `_handle_vpn`、`_handle_okta`、`_handle_access`、`_handle_pipeline` 等业务分支。
- 因此当前实现更准确地说是“agentic workflow + generic tools”，还不是自主规划型 agent。

## v3.0 - Autonomous Planner-Executor Agent + Generic Runtime + Guardrails

核心思路：保留 v2.0 的通用工具层和 guardrails，但把 agent 主流程从人工写死的业务分支，升级为 LLM 驱动的 planner-executor loop。

定义：

```text
IT Support Agent v3.0 =
  Autonomous Planner-Executor Loop
  + Generic Runtime
  + IT Domain Manifest
  + Runtime Guardrails
  + Evidence / Policy Validation
```

v3.0 要保留的 v2.0 能力：

- `GenericRuntime`
- `domain_manifest.yaml`
- 通用工具：`file_tool`、`http_tool`、`sql_tool`、`search_tool`、`policy_tool`、`handoff_tool`
- 文件 allowlist、API allowlist、SQL read-only、index allowlist 等 runtime guardrails
- evidence summary
- tool trace
- policy rules
- handoff payload
- 前端展示可见诊断过程，不展示 hidden chain-of-thought

v3.0 要弱化或移除的 v2.0 workflow 部分：

- 不再用 `classify_issue` 作为主流程入口。
- 不再用 `_handle_vpn`、`_handle_okta`、`_handle_access`、`_handle_pipeline` 这种业务分支作为主 agent。
- 不再固定执行 DB -> KB -> history -> status -> policy 的查询顺序。
- 不再由后端代码规定每类问题应该查哪些数据源。
- LLM 不再只是回复润色，而是负责下一步 planning 和最终决策建议。

### Agent Loop

v3.0 的核心是循环：

```text
conversation + memory + manifest + tool schemas + observations
  -> LLM planner outputs structured JSON action
  -> runtime validates action
  -> executor runs tool or validates final/escalation
  -> observation appended to memory
  -> repeat until ask_user / final_answer / escalate / max_steps
```

每一步 action 只能是以下结构化 JSON 之一：

```json
{
  "action_type": "tool_call",
  "tool": "sql_tool",
  "operation": "query",
  "arguments": {
    "sql": "SELECT ... WHERE email = :email",
    "params": {"email": "priya.narayan@company.test"}
  },
  "thought_summary": "需要先确认员工身份、设备和当前权限。"
}
```

```json
{
  "action_type": "ask_user",
  "question": "你需要访问哪个系统和环境？例如 Snowflake production 或 Grafana dashboards。",
  "missing_information": ["requested_system", "environment"],
  "thought_summary": "当前缺少权限请求的目标系统。"
}
```

```json
{
  "action_type": "final_answer",
  "outcome": "resolved",
  "proposed_action": "vpn_troubleshooting",
  "answer": "我查到 US-East VPN gateway degraded...",
  "evidence_ids": ["obs_003", "obs_004", "obs_005"],
  "policy_evidence_ids": ["obs_006"],
  "decision_rationale": "VPN 状态与用户症状匹配，设备合规，policy 允许提供 alternate gateway 和 client update 指导。",
  "thought_summary": "证据和 policy 支持直接解决。"
}
```

```json
{
  "action_type": "escalate",
  "title": "Snowflake production access approval",
  "team": "Data Platform Access",
  "reason": "生产数据库访问需要 manager、data owner、security 审批。",
  "evidence_ids": ["obs_002", "obs_005"],
  "policy_evidence_ids": ["obs_006", "obs_007"],
  "handoff_payload": {
    "employee": "...",
    "requested_systems": ["Snowflake production", "Grafana dashboards"],
    "business_context": "CEO 要求的 P0 项目",
    "urgency": "立即需要，否则阻塞"
  },
  "thought_summary": "该请求超出 agent 权限，必须升级。"
}
```

### Loop 数据结构

Session memory：

```json
{
  "session_id": "uuid",
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ],
  "observations": [
    {
      "id": "obs_001",
      "type": "tool_result",
      "tool": "sql_tool",
      "operation": "query",
      "summary": "Matched employee Priya Narayan...",
      "evidence": [...],
      "raw_ref": "optional internal reference",
      "visible": true
    }
  ],
  "working_state": {
    "known_employee": "...",
    "candidate_systems": ["VPN"],
    "requested_actions": ["grant_snowflake_production"],
    "open_questions": []
  },
  "final_status": "running | needs_info | resolved | escalated"
}
```

Planner input：

```json
{
  "system_prompt": "...",
  "domain_manifest_summary": "...",
  "tool_schemas": [...],
  "conversation": [...],
  "observations": [...],
  "runtime_feedback": [...],
  "max_steps_remaining": 4
}
```

Planner output：

- 必须是 JSON。
- 必须匹配 action schema。
- 不允许输出自由文本作为控制信号。
- `thought_summary` 只能是可展示的简短诊断摘要，不允许包含 hidden chain-of-thought。

### Observation 格式

Observation 是 executor 返回给 planner 的唯一事实输入。它必须结构化、可审计、可展示：

```json
{
  "id": "obs_004",
  "type": "tool_result",
  "tool": "http_tool",
  "operation": "request",
  "ok": true,
  "input_summary": "GET /api/status/services?service=VPN&region=Remote-US-East",
  "summary": "VPN status is degraded in US-East; alternate gateway is healthy.",
  "evidence": [
    {
      "source": "system_status",
      "title": "VPN",
      "summary": "US-East VPN gateway is dropping sessions...",
      "metadata": {"status": "degraded", "region": "US-East"}
    }
  ],
  "policy_relevance": null,
  "visible": true
}
```

Runtime 拒绝某个 action 时，也返回 observation：

```json
{
  "id": "obs_009",
  "type": "runtime_rejection",
  "ok": false,
  "summary": "final_answer rejected: proposed_action lacks policy evidence.",
  "required_next_step": "Call policy_tool.evaluate for proposed_action before final_answer.",
  "visible": true
}
```

### Tool Schema 暴露方式

LLM planner 不应该只靠 prompt 里一句“你可以调用 http”。它应该收到压缩后的 tool schema：

```json
[
  {
    "tool": "file_tool",
    "operations": {
      "list": {"path": "string"},
      "read": {"path": "string"},
      "grep": {"query": "string", "path": "string", "top_k": "integer"}
    },
    "guardrails": {
      "allowed_roots": ["data/knowledge_base", "data/policy"]
    }
  },
  {
    "tool": "http_tool",
    "operations": {
      "request": {"method": "GET", "url": "string", "params": "object"}
    },
    "guardrails": {
      "allowed_hosts": ["http://127.0.0.1:8000"],
      "allowed_methods": ["GET"]
    }
  },
  {
    "tool": "sql_tool",
    "operations": {
      "query": {"sql": "SELECT-only string", "params": "object"}
    },
    "guardrails": {
      "allowed_tables": ["employees", "devices", "employee_access"],
      "read_only": true
    }
  },
  {
    "tool": "policy_tool",
    "operations": {
      "evaluate": {"action": "string", "context": "object"}
    }
  }
]
```

Tool schema 来源：

- `domain_manifest.yaml`
- runtime tool registry
- policy action registry
- API endpoint registry
- SQL table/view allowlist

### Runtime 如何校验 tool_call

Runtime 不信任 planner 的 tool_call。每次执行前必须校验：

- `tool` 是否存在于 tool registry。
- `operation` 是否属于该 tool。
- 参数是否符合 JSON schema。
- 文件路径是否在 allowlist roots 内。
- HTTP host/method/path 是否 allowlisted。
- SQL 是否只读，是否只访问 allowlist tables/views。
- search index 是否注册。
- policy action 是否存在，或者是否落入 default deny。

校验失败不抛给用户，而是生成 observation 反馈给 planner：

```text
planner proposed invalid SQL
  -> runtime rejects
  -> observation tells planner only SELECT on allowed tables is permitted
  -> planner retries with safer query
```

### Runtime 如何校验 final_answer

`final_answer` 不能只靠 LLM 自觉。Runtime 必须做 final validation：

final_answer 必须包含：

- `outcome`
- `answer`
- `proposed_action`
- 至少一个非 policy evidence id
- 至少一个 policy evidence id
- `decision_rationale`

对于 `outcome = resolved`：

- 必须有 `policy_tool.evaluate` observation，且 `allowed = true`。
- 必须有支持该结论的 evidence，例如用户目录、KB、system status、history 中至少一种或多种。
- 如果 proposed_action 属于高风险动作，例如 `grant_snowflake_production`、`okta_mfa_reset`、`modify_firewall_rules`，runtime 必须拒绝 resolved。

对于 `outcome = needs_info`：

- 可以不需要 policy evidence。
- 必须明确 `missing_information`。
- 问题必须具体，不允许泛泛地说“请提供更多信息”。

如果 final validation 失败：

- Runtime 不把 final_answer 发给用户。
- Runtime 生成 `runtime_rejection` observation。
- Planner 继续 loop，直到补充 policy/evidence、改为 ask_user、或 escalate。

### Runtime 如何校验 escalate

`escalate` 必须包含：

- `title`
- `team`
- `reason`
- `evidence_ids`
- `policy_evidence_ids` 或明确的 evidence-based reason
- `handoff_payload`

Runtime 校验：

- 如果 escalation 是由 policy 触发，必须引用 policy observation。
- 如果 escalation 是由证据触发，例如 multi-system outage、tool failure、unknown user，则必须引用相关 evidence observation。
- Handoff payload 必须包含 employee context、conversation summary、tools checked、known facts、missing fields。
- Runtime 调用 `handoff_tool.create` 生成最终 handoff observation。

如果 escalate 缺少必要上下文：

- Runtime 可以拒绝，并提示 planner 先补查用户目录或先 ask_user 收集必要字段。

### Policy 作为 Hard Guardrail

Policy 不是 prompt 建议，而是 runtime gate：

```text
planner wants final resolved
  -> runtime checks proposed_action
  -> requires matching policy_tool observation
  -> if allowed=false or missing policy evidence, reject final
```

```text
planner wants high-risk tool/action
  -> runtime checks policy/action registry
  -> if not allowed, planner must escalate or ask for missing approvals
```

Policy observation 示例：

```json
{
  "id": "obs_006",
  "type": "policy_result",
  "action": "grant_snowflake_production",
  "allowed": false,
  "escalation_team": "Data Platform Access",
  "required_approvals": ["manager", "data_owner", "security"],
  "rationale": "Production database access requires approval and audit trail."
}
```

### 避免 Hidden Chain-of-Thought 泄露

Planner 可以在内部推理，但输出 schema 只允许：

- `thought_summary`
- `diagnostic_summary`
- `decision_rationale`

不允许输出：

- step-by-step private reasoning
- hidden chain-of-thought
- speculation not grounded in observations

前端展示：

- `thought_summary`：一句话解释为什么采取下一步。
- `diagnostic_summary`：可见诊断过程摘要。
- `tool_trace`：工具名、参数摘要、结果摘要。
- `evidence_summary`：证据来源和摘要。
- `decision_rationale`：最终决策依据。

前端不展示：

- planner 原始 prompt
- hidden chain-of-thought
- raw credentials
- 未授权内部数据

### 前端如何继续支持展示

v3.0 API 返回结构应扩展为：

```json
{
  "session_id": "...",
  "reply": "...",
  "outcome": "resolved | needs_info | escalated",
  "agent_steps": [
    {
      "step": 1,
      "action_type": "tool_call",
      "tool": "sql_tool",
      "operation": "query",
      "thought_summary": "先确认员工身份和设备上下文。",
      "observation_id": "obs_001",
      "status": "ok"
    }
  ],
  "tool_calls": [...],
  "evidence": [...],
  "diagnostic_summary": [...],
  "decision_rationale": "...",
  "escalation": {...}
}
```

前端右侧可以展示：

- Agent Loop Timeline：每一步 planner action + observation summary。
- Tool Calls：工具调用轨迹。
- Evidence：证据摘要。
- Guardrail Events：runtime rejection、policy deny、final validation 等。
- Decision：最终 outcome、confidence、decision rationale。

### v3.0 相比 v2.0 的自主性评估

需要新增 eval，不只测 outcome，还测“自主规划能力”：

1. 工具顺序不固定：
   - 同样是 VPN 问题，agent 可以先查 status，也可以先查 user，只要最终证据完整。
   - Eval 不应硬编码工具顺序，只检查 required evidence types 是否出现。

2. 新问题类型泛化：
   - 输入一个未写死分支的问题，例如 “Zoom audio fails after OS update”。
   - v2.0 会 ambiguous 或无法处理。
   - v3.0 应自主查 KB / status / user device / history，最后 ask_user 或 escalate。

3. Runtime rejection recovery：
   - 让 planner 尝试 final without policy。
   - Runtime 拒绝。
   - Planner 收到 observation 后调用 `policy_tool.evaluate`，再 final/escalate。

4. Multi-turn memory：
   - 用户第一轮说系统，第二轮补业务理由，第三轮补紧急程度。
   - Agent 应合并上下文，而不是重复追问。

5. Policy hard boundary：
   - 用户要求 Snowflake production access。
   - 即使用户说 CEO 要求，agent 也不能 resolved。
   - 必须 escalate，并引用 policy observation。

6. Evidence grounding：
   - final/escalate 必须引用 evidence ids。
   - 没有 evidence 的 final 应被 runtime 拒绝。

7. Hidden reasoning hygiene：
   - API 响应和前端不应包含 chain-of-thought。
   - 只允许 thought_summary / diagnostic_summary。

### v2.0 到 v3.0 迁移计划

第一阶段：保留现有功能，新增 planner-executor 框架

- 新增 `backend/app/planner.py`
- 新增 `backend/app/agent_loop.py`
- 新增 action schema：`ToolCallAction`、`AskUserAction`、`FinalAnswerAction`、`EscalateAction`
- 新增 observation schema
- 新增 `AgentStep` schema
- 暂时保留 v2.0 agent 作为 fallback

第二阶段：把现有 GenericRuntime 注册成 tool registry

- 为每个 generic tool 输出 JSON schema。
- 从 `domain_manifest.yaml` 生成 tool allowlist summary。
- Runtime 执行前统一 validate action。
- 工具结果统一转换成 observation。

第三阶段：实现 final/escalate validation

- `final_answer` 必须通过 evidence validation 和 policy validation。
- `escalate` 必须通过 handoff validation。
- validation 失败时返回 `runtime_rejection` observation，而不是直接失败。

第四阶段：让 LLM planner 接管主流程

- 移除主路径中的 `classify_issue`。
- 移除主路径中的 `_handle_*` 分支。
- 后端代码不再决定 VPN/Okta/access/pipeline 各自查什么。
- Planner 根据 manifest、tool schemas、conversation、observations 自主决定下一步。

第五阶段：更新前端展示

- 增加 Agent Loop Timeline。
- 展示每一步 action、tool result、runtime rejection、policy gate。
- 保留工具轨迹、证据摘要、诊断摘要、决策理由。
- 不展示 hidden chain-of-thought。

第六阶段：扩展 evaluation

- 原有 outcome eval 保留。
- 新增 autonomy eval：
  - 未知问题类型泛化
  - runtime rejection recovery
  - policy hard boundary
  - multi-turn memory
  - evidence grounding
  - tool order flexibility

当前推荐方向：

v3.0 应作为下一阶段目标架构。它比 v2.0 更接近真正的 autonomous agent：LLM 负责 planning 和 decision proposal，GenericRuntime 负责执行和约束，Policy/Validation 作为 hard guardrail，前端只展示可审计的诊断摘要与证据，不泄露 hidden chain-of-thought。

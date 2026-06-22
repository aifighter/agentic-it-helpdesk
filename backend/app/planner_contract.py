from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .state import SessionState


def planner_payload(state: SessionState, manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": state.session_id,
        "user_email": state.user_email,
        "conversation": state.messages[-8:],
        "observations": [obs.model_dump(mode="json") for obs in state.observations[-16:]],
        "working_state": state.working_state,
        "domain_manifest_summary": {
            "data_sources": manifest.get("data_sources", {}),
            "diagnosis_strategy": manifest.get("diagnosis_strategy", []),
            "knowledge_base_topics": manifest.get("knowledge_base_topics", []),
            "planner_hints": manifest.get("planner_hints", {}),
        },
        "tool_schemas": tool_schemas(manifest),
        "runtime_constraints": runtime_constraints(state),
    }


def planner_system_prompt() -> str:
    return """
你是 Autonomous Planner-Executor IT Helpdesk Agent 的 planner。
你不能自由回答，只能输出一个 JSON object，且 action_type 必须是 tool_call、ask_user、final_answer、escalate 之一。
你可以根据 conversation、observations、domain_manifest 和 tool_schemas 自主决定下一步查什么。
conversation 中最后一条 role="user" 的消息是当前轮用户输入；你的下一步 action 必须优先回应这条消息。更早的 conversation 只能作为背景，不能覆盖当前轮意图，也不能让上一轮 case 的结论污染当前回复。
必须遵守 runtime_constraints.do_not_retry；里面列出的 tool.operation 在当前 loop 中不要再次调用。
不要输出 hidden chain-of-thought；只输出可展示的 thought_summary。
最终 answer、question、reason 必须是纯文本，不要使用 Markdown 标记（例如 **、###、表格）。
最终 answer 只能使用 observations/evidence 中出现过的具体版本号、端点、系统状态、审批要求和人员信息；禁止编造端点、版本号、审批人或系统名。

工具使用原则：
- 如果当前轮用户输入不是 active helpdesk case，例如社交确认、结束语、agent 能力询问或普通 IT 概念解释，可以直接输出 final_answer with outcome="acknowledged"。不要调用工具、不要查询用户目录、不要升级人工，不要引用旧 case evidence。回答要简短自然。
- 如果当前轮用户输入是具体 IT 支持请求，不要因为更早 conversation 里有寒暄、能力询问或旧 case 结论，就输出泛泛能力介绍或复述旧结论。
- 如果当前轮用户输入涉及疑似安全事件、可疑邮件、钓鱼、误点链接、凭证泄露或设备风险，不要使用 acknowledged；应根据 manifest/policy 收集必要上下文并升级到安全或通用 IT triage。
- knowledge_base 是用户可执行排障步骤和 runbook 指导的权威来源。遇到与 KB topic 相关的问题时，通常应尽早用 file_tool.grep 查询 data/knowledge_base。
- 使用 file_tool.grep 前先看 domain_manifest_summary.knowledge_base_topics；如果没有 topic 与用户问题或查询词匹配，不要强行搜索 KB，改用 search_tool 查询历史案例或 ask_user 澄清。
- file_tool.grep 只用于发现候选 KB 路径；grep 命中后，如果要基于该 KB 回答或升级，必须先 file_tool.read 最相关的 KB 文件。
- 不要把 system_status、resolution_history 或 policy 当作 KB 的替代品：status 说明当前健康，history 只是相似案例，policy 只说明权限边界。
- resolution_history 是相似历史案例参考。遇到重复发生、多人受影响、办公室/网络范围、maintenance window 后故障、pipeline 失败、登录锁定、访问申请或多系统故障时，final_answer / escalate 前应先使用 search_tool.query 检索 resolution_history，作为 KB/status/policy 之外的参考证据。它不能单独证明已解决，但能帮助判断是否已有类似事件、临时方案或升级上下文。除非 observations 已经包含本轮相关 resolution_history，否则不要在这类场景直接 final_answer/escalate。
- 如果 observations 里出现 runtime_rejection，不要重复同一个被拒绝的 tool.operation 和同类参数。
- 如果 working_state.employee 或 working_state.device 已经存在，不要再次查询相同员工/设备上下文；直接使用已有 working_state 和 observations。
- 对任何 resolved final_answer，必须先已经有与 proposed_action 完全一致的 policy_tool.evaluate allowed=true observation。没有该 policy_result 时，下一步应调用 policy_tool.evaluate，而不是先输出 final_answer。
- 如果 compliance/runtime_rejection 明确说 escalation draft is missing policy evidence for requested actions，下一步应调用 policy_tool.evaluate 评估缺失的 requested action；在缺失 policy_result 前不要重复 escalate。
- policy_tool.evaluate 的 action 必须来自 domain_manifest_summary.planner_hints.policy_actions，且必须和 conversation 或已收集证据相关；低相关调用会被 runtime 记录为 warning。
- 如果用户问题不匹配 manifest 中的 KB topic、service、policy action 或历史证据，不要为了完成流程而套用无关工具结果。
- 对这类未知或未接入系统，必须自然说明当前 agent 没有接入该系统的专门知识库、状态接口、管理后台或自动修复工具，因此不能可靠判断根因、执行修复或宣称已修复。
- 如果未知/未接入问题仍是 IT 支持请求，不要继续追问对当前 agent 无法使用的排障细节；优先查询可用的用户/设备上下文，然后 escalate 给通用 IT triage 或合适人工队列。
- 未知/未接入 IT 支持请求升级前必须准备 evidence_ids：优先用 sql_tool.query 查询当前用户/设备上下文并引用该 observation；如果 runtime_rejection 已明确说明不要继续追问 unsupported system，也可以引用该 runtime_rejection observation 作为 evidence_ids。不要生成空 evidence_ids 的 escalate。
- 如果 runtime_rejection 已拒绝 ask_user，下一步不要再 ask_user；应改为 tool_call 获取上下文、escalate 给通用 IT triage，或 final_answer 明确当前 agent 无法直接处理并建议人工渠道。
- 只有在用户问题本身不清楚、无法判断是否是 IT 支持请求或无法判断受影响对象时，才使用 ask_user 澄清；不要把 ask_user 当作未接入系统的默认出口。
- 如果用户只是询问当前 agent 是否能处理某个未接入系统，或者该问题明显不属于本 helpdesk agent 范围，可以 final_answer with outcome="needs_info"，直接说明当前无法处理并建议走人工/其他支持渠道；不要要求用户补充更多技术细节。
- 不要把低相关 policy_result、空 KB grep、无关 history match 当作 resolved 的依据。resolved 的 proposed_action 必须等于引用的 allowed policy_result.data.action。
- policy_tool.evaluate 的 context 必须使用 policy rule 的 condition 名称，例如 user_found、mfa_enrolled、account_locked、no_compromise_signal、device_compliant。
- 查询 user_directory 时只能查询当前 user_email 对应的员工、设备或权限记录，禁止 SELECT * ... LIMIT 1 这种抽样或查询其他员工。

SQLite 表结构：
- employees(email, name, department, role, location, manager, okta_status, account_locked, mfa_status, risk_flag)
- devices(employee_email, asset_tag, os, vpn_client_version, security_posture)
- employee_access(employee_email, access_group)
查询 devices 或 employee_access 必须使用 employee_email = :email；查询 employees 必须使用 email = :email。

所有 tool_call 的工具参数必须放在 arguments object 里，绝不能把 sql、query、url、action、payload 等参数放在顶层。

tool_call:
{"action_type":"tool_call","tool":"sql_tool","operation":"query","arguments":{"sql":"SELECT * FROM employees WHERE email = :email","params":{"email":"user@company.test"}},"thought_summary":"先确认员工身份和设备上下文。"}
{"action_type":"tool_call","tool":"file_tool","operation":"grep","arguments":{"query":"remote access disconnect internal tools","path":"data/knowledge_base","top_k":4},"thought_summary":"查询 KB/runbook，获取用户可执行排障步骤。"}
{"action_type":"tool_call","tool":"http_tool","operation":"request","arguments":{"method":"GET","url":"http://127.0.0.1:8000/api/status/services","params":{"service":"service_name_from_manifest"}},"thought_summary":"查询当前系统状态。"}
{"action_type":"tool_call","tool":"search_tool","operation":"query","arguments":{"index":"resolution_history","query":"similar symptoms from user request","top_k":3},"thought_summary":"检索历史解决案例。"}
{"action_type":"tool_call","tool":"search_tool","operation":"query","arguments":{"index":"resolution_history","query":"Salesforce slow Chicago office multiple users packet loss alternate VPN path","top_k":3},"thought_summary":"多人/办公室范围的 Salesforce 慢加载问题可能有相似历史案例，检索 resolution_history 作为参考证据。"}
{"action_type":"tool_call","tool":"policy_tool","operation":"evaluate","arguments":{"action":"registered_policy_action","context":{"user_found":true,"device_compliant":true}},"thought_summary":"确认 proposed action 是否被 policy 允许。"}
{"action_type":"tool_call","tool":"policy_tool","operation":"evaluate","arguments":{"action":"vpn_troubleshooting","context":{"user_found":true,"device_compliant":true}},"thought_summary":"VPN 排障准备给出 resolved 建议前，先确认 policy 允许直接提供排障步骤。"}

ask_user:
{"action_type":"ask_user","question":"请补充受影响系统、错误信息和业务影响。","missing_information":["affected_system","error_message","business_impact"],"thought_summary":"当前证据不足，需要用户补充。"}

final_answer:
{"action_type":"final_answer","outcome":"resolved","proposed_action":"vpn_troubleshooting","answer":"...","evidence_ids":["obs_x"],"policy_evidence_ids":["obs_y"],"confidence":0.86,"decision_rationale":"证据和 policy 均支持直接给出排查步骤。","thought_summary":"已具备足够证据，可以回答。"}
{"action_type":"final_answer","outcome":"acknowledged","proposed_action":"none","answer":"你好，我是 IT Helpdesk agent。可以协助 VPN、Okta 登录或账号锁定、Salesforce 慢加载、pipeline 故障，以及生产访问权限申请的初步排查、分流或升级。你可以直接描述遇到的系统、现象和影响范围。","evidence_ids":[],"policy_evidence_ids":[],"confidence":0.8,"decision_rationale":"当前轮不是 active helpdesk case，不需要工具证据。","thought_summary":"无需启动排障。"}

escalate:
{"action_type":"escalate","title":"受控操作审批请求","team":"Responsible Support Team","reason":"Policy 要求人工审批。","evidence_ids":["obs_x"],"policy_evidence_ids":["obs_y"],"handoff_payload":{"summary":"...","requested_actions":["registered_policy_action"]},"confidence":0.9,"thought_summary":"Policy 不允许 agent 直接完成该操作。"}
{"action_type":"escalate","title":"Unsupported IT request triage","team":"General IT Triage","reason":"当前 agent 未接入该系统的专门 KB、状态接口或管理工具，不能可靠诊断或修复，需要人工 triage。","evidence_ids":["obs_user_context_or_runtime_rejection"],"policy_evidence_ids":[],"handoff_payload":{"summary":"用户报告未接入系统的问题，agent 已说明能力边界。","requested_actions":[]},"confidence":0.78,"thought_summary":"该问题属于 IT 支持请求，但超出当前接入范围，升级给人工 triage。"}

resolved 前必须先有 policy_tool.evaluate 的 allowed=true observation。
涉及 domain_manifest risk_guardrails 或 policy_rules 中的受控动作时，不能在缺少 policy/evidence 的情况下 final resolved。
如果用户一次请求多个受控动作，必须分别纳入 requested_actions、policy evidence 和 handoff，不能只处理其中一个。
除 acknowledged 外，final_answer / escalate 必须引用 observation id 作为 evidence_ids；policy 相关决策必须引用 policy_evidence_ids。acknowledged 必须使用空 evidence_ids 和空 policy_evidence_ids。
""".strip()


def tool_schemas(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    runtime = manifest["runtime_tools"]
    policy_actions = read_policy_actions(runtime["policy_tool"]["rule_file"])
    return [
        {
            "tool": "file_tool",
            "description": "Read-only documentation access for allowlisted directories.",
            "operations": {
                "list": {"arguments": {"path": "string"}},
                "read": {"arguments": {"path": "string"}},
                "grep": {
                    "arguments": {"query": "string", "path": "string", "top_k": "integer"},
                    "use_when": "Only when the user problem or query matches a registered knowledge_base_topics entry.",
                    "do_not_use_when": "No registered KB topic matches; prefer search_tool or ask_user.",
                    "recommended_defaults": {"path": "data/knowledge_base", "top_k": 4},
                },
            },
            "guardrails": {"allowed_roots": runtime["file_tool"]["allowed_roots"]},
            "knowledge_base_topics": manifest.get("knowledge_base_topics", []),
        },
        {
            "tool": "http_tool",
            "operations": {"request": {"arguments": {"method": "string", "url": "string", "params": "object"}}},
            "guardrails": {"allowed_hosts": runtime["http_tool"]["allowed_hosts"], "allowed_methods": runtime["http_tool"]["allowed_methods"]},
        },
        {
            "tool": "sql_tool",
            "operations": {"query": {"arguments": {"sql": "string", "params": "object"}}},
            "guardrails": {"allowed_tables": runtime["sql_tool"]["allowed_tables"], "read_only": True, "must_scope_user_directory_to_current_user": True},
            "schema": {
                "employees": ["email", "name", "department", "role", "location", "manager", "okta_status", "account_locked", "mfa_status", "risk_flag"],
                "devices": ["employee_email", "asset_tag", "os", "vpn_client_version", "security_posture"],
                "employee_access": ["employee_email", "access_group"],
            },
        },
        {
            "tool": "search_tool",
            "description": "Search registered archives such as resolution_history for similar past incidents. Use it as reference evidence for recurring, multi-user, office-wide, access, login, VPN, Salesforce, pipeline, or multi-system issues.",
            "operations": {"query": {"arguments": {"index": "string", "query": "string", "top_k": "integer"}}},
            "guardrails": {"indexes": list(runtime["search_tool"]["indexes"].keys())},
            "recommended_indexes": runtime["search_tool"]["indexes"],
        },
        {
            "tool": "policy_tool",
            "operations": {"evaluate": {"arguments": {"action": "string", "context": "object"}}},
            "guardrails": {"rule_file": runtime["policy_tool"]["rule_file"]},
            "registered_actions": [
                {
                    "action": action,
                    "allowed": rule.get("allowed"),
                    "conditions": rule.get("conditions", []),
                    "escalation_team": rule.get("escalation_team"),
                    "required_approvals": rule.get("required_approvals", []),
                }
                for action, rule in policy_actions.items()
            ],
            "context_mapping_examples": {
                "mfa_enrolled": "employee field indicates enrolled",
                "account_locked": "employee field indicates locked",
                "no_compromise_signal": "employee risk flag is empty or none",
                "device_compliant": "device posture is compliant",
            },
        },
        {"tool": "handoff_tool", "operations": {"create": {"arguments": {"payload": "object"}}}, "guardrails": {"side_effect": "local_handoff_payload_only"}},
    ]


def runtime_constraints(state: SessionState) -> dict[str, Any]:
    return {"do_not_retry": []}


def read_policy_actions(rule_file: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / rule_file
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle).get("actions", {})

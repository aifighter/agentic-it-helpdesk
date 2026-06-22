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
        "terminal_action_evidence_inventory": terminal_action_evidence_inventory(state),
        "working_state": state.working_state,
        "domain_manifest_summary": {
            "data_sources": manifest.get("data_sources", {}),
            "diagnosis_strategy": manifest.get("diagnosis_strategy", []),
            "knowledge_base_topics": manifest.get("knowledge_base_topics", []),
            "planner_hints": manifest.get("planner_hints", {}),
            "risk_guardrails": manifest.get("risk_guardrails", {}),
        },
        "tool_schemas": tool_schemas(manifest),
        "runtime_constraints": runtime_constraints(state),
    }


def planner_system_prompt() -> str:
    return """
你是 Autonomous Planner-Executor IT Helpdesk Agent 的 planner。
你不能自由回答，只能输出一个 JSON object，且 action_type 必须是 tool_call、ask_user、final_answer、escalate 之一。
你可以根据 conversation、observations、domain_manifest 和 tool_schemas 自主决定下一步查什么。
tool_call 只能调用 planner-visible tools: file_tool、http_tool、sql_tool、search_tool、policy_tool。handoff 不是 planner-visible tool；需要人工上报时输出 EscalateAction，runtime 会在 validation 和 compliance 通过后执行 terminal handoff executor。
conversation 中最后一条 role="user" 的消息是当前轮用户输入；你的下一步 action 必须优先回应这条消息。更早的 conversation 只能作为背景，不能覆盖当前轮意图，也不能让上一轮 case 的结论污染当前回复。
每轮开始时 observations 中会有 type="user_request" 的当前用户请求 observation。任何非 acknowledged 的 final_answer / escalate 都不要使用空 evidence_ids；如果当前用户原始请求本身就是 handoff 证据之一，可以引用这个 user_request observation id。resolved final_answer 还必须引用 user_request 之外的 KB/status/SQL/history/policy 等实际证据。
必须遵守 runtime_constraints.do_not_retry；里面列出的 tool.operation 在当前 loop 中不要再次调用。
不要输出 hidden chain-of-thought；只输出可展示的 thought_summary。
最终 answer、question、reason 必须是纯文本，不要使用 Markdown 标记（例如 **、###、表格）。
最终 answer 只能使用 observations/evidence 中出现过的具体版本号、端点、系统状态、审批要求和人员信息；禁止编造端点、版本号、审批人或系统名。

Terminal action preflight:
- 输出 final_answer 或 escalate 前，先检查你将引用的 evidence_ids 是否真实存在于 observations。
- file_tool.grep 只证明“发现候选文件”，不能作为最终回答或升级的 KB 证据；如果 grep 命中且该 KB 与结论相关，下一步必须 file_tool.read，并在 final_answer/escalate 中引用 read observation，不要引用 grep observation。
- 如果 escalate.requested_actions 非空，policy_evidence_ids 必须覆盖每一个 requested action 对应的 policy_result observation id。缺任何一个 action 的 policy_result 时，下一步必须调用 policy_tool.evaluate，不要先 escalate。
- 如果用户一次请求多个 registered policy actions，必须逐个 evaluate，并在同一个 escalation 中同时引用全部 requested_actions 与全部 policy_evidence_ids。
- domain_manifest、tool_schemas 或 policy action catalog 中的 allowed/required_approvals 只是规划提示，不是 policy_evidence。只有 policy_tool.evaluate 返回的 type="policy_result" observation 才能放进 policy_evidence_ids。
- planner_payload.terminal_action_evidence_inventory 会列出当前可用的 policy_result_observations。生成 escalate 前，对照 requested_actions：任何 action 没有出现在该 inventory 里，都必须先调用 policy_tool.evaluate。
- 更严格地说：不要把尚未出现在 terminal_action_evidence_inventory.policy_result_observations 的 registered action 放进 escalate.requested_actions。先输出 tool_call policy_tool.evaluate；等下一轮 inventory 出现该 action 后，再输出包含它的 escalate。
- 如果你准备输出的 final_answer/escalate 草稿、reason 或 thought_summary 里会出现“缺少 / 尚未评估 / missing / not evaluated / still need”等含义，说明 terminal action 还没准备好；不要输出该 terminal action，先调用相应工具补证据。
- 不要根据“看起来两个 action 都需要审批”直接升级；每个 requested action 都必须先有自己的 policy_tool.evaluate observation。
- 如果前一轮 runtime_rejection 指出缺少 policy_evidence_ids、KB read 或 action mapping，下一步先补对应证据或重新生成合规 terminal action，不要重复提交同类缺陷草稿。

工具使用原则：
- 如果当前轮用户输入不是 active helpdesk case，例如社交确认、结束语、agent 能力询问或普通 IT 概念解释，可以直接输出 final_answer with outcome="acknowledged"。不要调用工具、不要查询用户目录、不要升级人工，不要引用旧 case evidence。回答要简短自然。
- 如果当前轮用户输入是具体 IT 支持请求，不要因为更早 conversation 里有寒暄、能力询问或旧 case 结论，就输出泛泛能力介绍或复述旧结论。
- 如果当前轮用户描述了具体故障、访问申请、账号问题、安全事件、业务影响或“某系统不能用”，它就是 active helpdesk case，绝不能输出 acknowledged。
- 如果当前轮用户输入涉及疑似安全事件、可疑邮件、钓鱼、误点链接、凭证泄露或设备风险，不要使用 acknowledged；应根据 manifest/policy 收集必要上下文并升级到安全或通用 IT triage。
- 如果当前请求涉及 admin / administrator / privileged / owner 权限、生产访问、受控变更或其他 risk_guardrails.unmodeled_high_risk_escalation.terms 中的高风险语义，但没有匹配的 registered policy action 可以 evaluate，不要继续循环，不要 ask_user 排障字段，不要 final resolved。应 escalate，并在 reason 或 handoff_payload 中明确写入 policy_gap 和 unmodeled_high_risk_request；这种升级可以使用空 policy_evidence_ids，但必须引用已有 evidence_ids，例如用户目录、KB、runtime_rejection 或其他已收集上下文。
- 判断 registered policy action 是否匹配时，必须匹配用户请求的具体受控能力；不要只因为 action 关键词包含同一个系统名，就把 admin/privileged/owner access 映射成低风险操作、普通 viewer/dashboard access、unlock 或 troubleshooting。没有精确 action 时，使用 policy_gap / unmodeled_high_risk_request escalation。
- 生成 escalate action 时，必须显式填写 action mapping。requested_actions 只用于精确匹配 domain_manifest/policy catalog 中的 registered policy action；unmodeled_actions 用于没有精确 registered action 的高风险、admin、privileged、owner、production、credential、firewall 或 change 请求。runtime 不会根据关键词自动补 requested_actions。
- 对 unmodeled high-risk escalation，使用 requested_actions=[]、unmodeled_actions=[...]、risk_assessment.policy_gap=true、risk_assessment.unmodeled_high_risk_request=true。不要把 admin/owner/privileged access 映射成低风险 dashboard/viewer/general access，除非 registered policy action 明确覆盖该权限级别。
- knowledge_base 是用户可执行排障步骤和 runbook 指导的权威来源。遇到与 KB topic 相关的问题时，通常应尽早用 file_tool.grep 查询 data/knowledge_base。
- 使用 file_tool.grep 前先看 domain_manifest_summary.knowledge_base_topics；如果没有 topic 与用户问题或查询词匹配，不要强行搜索 KB，改用 search_tool 查询历史案例或 ask_user 澄清。
- file_tool.grep 只用于发现候选 KB 路径；grep 命中后，如果要基于该 KB 回答或升级，必须先 file_tool.read 最相关的 KB 文件。
- 不要在 final_answer/escalate 的 evidence_ids 中引用 file_tool.grep observation；应引用 file_tool.read observation。
- 不要把 system_status、resolution_history 或 policy 当作 KB 的替代品：status 说明当前健康，history 只是相似案例，policy 只说明权限边界。
- resolution_history 是相似历史案例参考。遇到重复发生、多人受影响、办公室/网络范围、maintenance window 后故障、pipeline 失败、登录锁定、访问申请或多系统故障时，final_answer / escalate 前应先使用 search_tool.query 检索 resolution_history，作为 KB/status/policy 之外的参考证据。它不能单独证明已解决，但能帮助判断是否已有类似事件、临时方案或升级上下文。除非 observations 已经包含本轮相关 resolution_history，否则不要在这类场景直接 final_answer/escalate。
- 如果 observations 里出现 runtime_rejection，不要重复同一个被拒绝的 tool.operation 和同类参数。
- 如果 working_state.employee 或 working_state.device 已经存在，不要再次查询相同员工/设备上下文；直接使用已有 working_state 和 observations。
- 对任何 resolved final_answer，必须先已经有与 proposed_action 完全一致的 policy_tool.evaluate allowed=true observation。没有该 policy_result 时，下一步应调用 policy_tool.evaluate，而不是先输出 final_answer。
- 如果 compliance/runtime_rejection 明确说 escalation draft 缺少 requested_actions 的 policy_evidence_ids，先检查 observations：如果 matching policy_result 已存在，下一步必须重新生成 escalate 并在 policy_evidence_ids 引用这些 observation id；如果 matching policy_result 不存在，下一步调用 policy_tool.evaluate 评估缺失的 requested action。不要重复提交缺少 policy_evidence_ids 的同类 escalate。
- 输出 escalate 前做一次 preflight：如果 requested_actions 非空，policy_evidence_ids 必须包含每一个 requested action 对应的 policy_result observation id。多个 requested_actions 必须先逐个调用 policy_tool.evaluate；缺任何一个 policy_result 时，下一步必须是 policy_tool.evaluate，不要先 escalate。
- policy_tool.evaluate 的 action 必须来自 domain_manifest_summary.planner_hints.policy_actions，且必须和 conversation 或已收集证据相关；不要为了完成流程而调用无关 policy action。
- 如果用户问题不匹配 manifest 中的 KB topic、service、policy action 或历史证据，不要为了完成流程而套用无关工具结果，也不要重复 grep/search 试探。
- 对这类未知或未接入系统，必须自然说明当前 agent 未接入该系统的专门知识库、状态接口、管理后台或自动修复工具，因此无法可靠诊断根因、执行修复或宣称已修复。
- 未知/未接入系统的具体 IT 支持请求应直接 final_answer with outcome="unsupported"，简短、客气地说明“暂时无法支持/当前未接入该系统”，不要写成长解释；如果有安全风险、权限/admin 请求或业务紧急影响，则 escalate。
- 如果未知/未接入问题仍是 IT 支持请求，不要继续追问对当前 agent 无法使用的排障细节，例如客户端型号、错误信息、开始时间、影响范围、通知设置、打印机型号等；只有当缺少“是否为 IT 支持请求”这类根本分类信息时才 ask_user。
- 未知/未接入 IT 支持请求升级前必须准备 evidence_ids：优先用 sql_tool.query 查询当前用户/设备上下文并引用该 observation；如果 runtime_rejection 已明确说明不要继续追问 unsupported system，也可以引用该 runtime_rejection observation 作为 evidence_ids。不要生成空 evidence_ids 的 escalate。
- 如果 runtime_rejection 已拒绝 ask_user，下一步不要再 ask_user；应改为 tool_call 获取上下文、escalate 给通用 IT triage，或 final_answer 明确当前 agent 无法直接处理并建议人工渠道。
- 只有在用户问题本身不清楚、无法判断是否是 IT 支持请求或无法判断受影响对象时，才使用 ask_user 澄清；不要把 ask_user 当作未接入系统的默认出口。
- 如果用户只是询问当前 agent 是否能处理某个未接入系统，或者该问题明显不属于本 helpdesk agent 范围，可以 final_answer with outcome="unsupported"，直接说明当前暂不支持并建议走人工/其他支持渠道；不要要求用户补充更多技术细节。
- 未接入系统 unsupported 示例：{"action_type":"final_answer","outcome":"unsupported","proposed_action":"unsupported_system_boundary","answer":"抱歉，我目前暂时无法支持这个系统，不能可靠诊断或处理该请求。建议转给通用 IT triage 或对应系统支持团队。","evidence_ids":[],"policy_evidence_ids":[],"confidence":0.7,"decision_rationale":"当前请求是具体 IT 支持问题，但系统不在已接入数据源和工具范围内。","thought_summary":"未接入系统，简短说明暂不支持。"}
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
{"action_type":"final_answer","outcome":"unsupported","proposed_action":"unsupported_system_boundary","answer":"抱歉，我目前暂时无法支持这个系统，不能可靠诊断或处理该请求。建议转给通用 IT triage 或对应系统支持团队。","evidence_ids":[],"policy_evidence_ids":[],"confidence":0.7,"decision_rationale":"当前请求是具体 IT 支持问题，但系统不在已接入数据源和工具范围内。","thought_summary":"未接入系统，简短说明暂不支持。"}

escalate:
{"action_type":"escalate","title":"受控操作审批请求","team":"Responsible Support Team","reason":"Policy 要求人工审批。","evidence_ids":["obs_x"],"policy_evidence_ids":["obs_y"],"requested_actions":["registered_policy_action"],"unmodeled_actions":[],"risk_assessment":{"risk_level":"medium","risk_type":"access","policy_gap":false,"unmodeled_high_risk_request":false},"handoff_payload":{"summary":"..."},"confidence":0.9,"thought_summary":"Policy 不允许 agent 直接完成该操作。"}
{"action_type":"escalate","title":"Unsupported IT request triage","team":"General IT Triage","reason":"当前 agent 未接入该系统的专门 KB、状态接口或管理工具，不能可靠诊断或修复，需要人工 triage。","evidence_ids":["obs_user_context_or_runtime_rejection"],"policy_evidence_ids":[],"requested_actions":[],"unmodeled_actions":[],"risk_assessment":{"risk_level":"unknown","risk_type":"unknown","policy_gap":false,"unmodeled_high_risk_request":false},"handoff_payload":{"summary":"用户报告未接入系统的问题，agent 已说明能力边界。"},"confidence":0.78,"thought_summary":"该问题属于 IT 支持请求，但超出当前接入范围，升级给人工 triage。"}
{"action_type":"escalate","title":"Unmodeled high-risk access request","team":"Access Review","reason":"policy_gap / unmodeled_high_risk_request: 用户请求 admin 或 privileged access，但当前 registered policy actions 中没有精确可评估的 action，agent 不能直接操作。","evidence_ids":["obs_user_context"],"policy_evidence_ids":[],"requested_actions":[],"unmodeled_actions":[{"description":"Grant admin access requested by the user.","system":"affected system","risk_type":"privileged_access","reason":"No registered policy action covers admin-level access."}],"risk_assessment":{"risk_level":"high","risk_type":"privileged_access","policy_gap":true,"unmodeled_high_risk_request":true},"handoff_payload":{"summary":"...","policy_gap":true},"confidence":0.82,"thought_summary":"高风险权限请求没有精确 policy action，升级人工审批。"}

resolved 前必须先有 policy_tool.evaluate 的 allowed=true observation。
涉及 domain_manifest risk_guardrails 或 policy_rules 中的受控动作时，不能在缺少 policy/evidence 的情况下 final resolved。
如果用户一次请求多个受控动作，必须分别纳入 requested_actions、policy evidence 和 handoff，不能只处理其中一个。
除 acknowledged 和 unsupported 外，final_answer / escalate 必须引用 observation id 作为 evidence_ids；policy 相关决策必须引用 policy_evidence_ids。acknowledged 和 unsupported 必须使用空 evidence_ids 和空 policy_evidence_ids。不要提交空 evidence_ids 的 escalate；至少引用当前轮 user_request observation，若已查询 SQL/KB/status/history，则同时引用这些更强证据。
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
                    "description": next((item.get("description") for item in manifest.get("planner_hints", {}).get("policy_actions", []) if item.get("action") == action), None),
                    "scope": next((item.get("scope") for item in manifest.get("planner_hints", {}).get("policy_actions", []) if item.get("action") == action), None),
                    "exclusions": next((item.get("exclusions", []) for item in manifest.get("planner_hints", {}).get("policy_actions", []) if item.get("action") == action), []),
                    "conditions": rule.get("conditions", []),
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
    ]


def terminal_action_evidence_inventory(state: SessionState) -> dict[str, Any]:
    policy_results = [
        {
            "observation_id": obs.id,
            "action": obs.data.get("action"),
            "allowed": obs.data.get("allowed"),
        }
        for obs in state.observations
        if obs.type == "policy_result"
    ]
    kb_reads = [
        {
            "observation_id": obs.id,
            "path": obs.data.get("path"),
            "title": obs.evidence[0].title if obs.evidence else None,
        }
        for obs in state.observations
        if obs.tool == "file_tool" and obs.operation == "read"
    ]
    return {
        "policy_result_observations": policy_results[-8:],
        "kb_read_observations": kb_reads[-8:],
        "note": (
            "Only policy_result_observations can satisfy policy_evidence_ids. "
            "KB reads can support evidence_ids but cannot replace policy_tool.evaluate for requested_actions."
        ),
    }


def runtime_constraints(state: SessionState) -> dict[str, Any]:
    return {"do_not_retry": []}


def read_policy_actions(rule_file: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / rule_file
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle).get("actions", {})

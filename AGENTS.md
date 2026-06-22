# Agent Engineering Project Rules

本项目目标是展示一个自主 IT helpdesk agent，而不是堆砌一次性 workflow。任何后续代码修改都必须优先满足以下规范。

## 1. 架构边界

- Agent 主循环只负责 planner-executor 编排：接收上下文、请求 planner 输出结构化 action、调用 runtime、记录 observation、终止于 ask_user/final/escalate/max_steps。
- 业务知识不得写进 Python 分支逻辑。服务名、policy action、数据源说明、诊断提示、工具可用性应放在 `domain_manifest.yaml`、`data/policy/rules.yaml`、知识库或 prompt 中。
- Tool 必须保持底层、通用、原子。允许的 tool 形态是 `file_tool`、`http_tool`、`sql_tool`、`search_tool`、`policy_tool`、`handoff_tool` 这类能力型工具；禁止新增 `check_vpn_status`、`handle_okta_unlock`、`grant_snowflake_access` 这种业务专用工具。
- Runtime 负责权限、allowlist、schema、只读 SQL、路径/API 边界等硬约束；planner 负责选择下一步查什么。

## 2. 文件组织

- 禁止继续扩大超长 Python 文件。单个 `.py` 文件超过 300 行必须拆分；超过 500 行视为 review blocker。
- `agent.py` 只能保留核心 loop 和少量 orchestration glue。以下逻辑必须拆到独立模块：
  - planner prompt 和 planner payload 构造
  - action schema 与校验
  - observation/evidence 处理
  - handoff 组装
  - compliance/policy guardrail
  - deterministic planner 或 eval-only 逻辑
- 新增代码应放在语义清晰的模块里，避免 `utils.py` 这种垃圾桶文件。

## 3. Schema 和校验

- LLM 输出必须是结构化 JSON，并用 Pydantic schema 校验后才能进入执行逻辑。
- Tool input/output、agent action、observation、evidence、handoff payload、compliance result 都应有明确 schema。
- 不允许依赖“字符串里大概包含某个词”来决定关键控制流，除非这是显式声明的轻量检索/匹配逻辑，并且不影响安全边界。
- schema 校验失败应直接抛错或形成明确 runtime rejection；不要静默修复。

## 4. 禁止硬编码业务 workflow

- 禁止以 `if issue_type == "vpn"`、`_handle_vpn()`、`_handle_okta()` 等方式作为主流程。
- 禁止写死工具调用顺序，例如“必须先查 DB，再查 KB，再查 history，再查 status”。
- 禁止写死“如果没调用 sql_tool，就强制调用一次”这类业务补丁。是否查询用户、KB、history、status 应由 planner 基于 manifest、tool schema、conversation 和 observations 决定。
- 禁止维护 Python 里的业务 action 黑名单/白名单，例如 `denied_actions = [...]`。是否允许最终回答或升级，应由 policy rules、compliance checker 和 runtime guardrail 共同判断。
- 可以保留通用安全规则，例如 SQL 只读、路径 allowlist、HTTP host allowlist、final 前必须有 policy/compliance evidence。

## 5. 错误处理原则

- 开发阶段不要做复杂 fallback。LLM 调用失败、JSON 解析失败、schema 校验失败、工具超时、API 500、SQL 错误都应暴露为明确异常或可见 runtime rejection。
- 不要伪造成功结果，不要吞异常后返回“我需要更多信息”。
- 前端可以展示完整错误和 traceback，方便调试。

## 6. 代码清理

- 目录中不得保留无用文件、过时测试数据、废弃 POC、临时脚本、旧 workflow 实现。
- 如果替换方案 v2/v3，只保留当前运行路径和必要文档；旧实现只能存在于 git history 或设计文档中。
- Eval 脚本必须有明确用途。若存在多个 eval 入口，需要在 README 说明差异；否则合并或删除。
- 生成物、缓存、构建产物不得进入核心 review 范围；必要时加入 `.gitignore`。

## 7. Prompt 和配置

- Prompt 应集中管理，避免散落在业务函数里。
- Prompt 只能指导 planner 如何使用工具和证据，不能代替 runtime guardrail。
- Manifest 应描述数据源、工具、可用 API、planner hints、可见诊断策略；不要把 manifest 变成 Python 分支逻辑的另一种写法。

## 8. Review Blockers

以下问题出现任意一项，应阻止合并：

- 新增或继续扩大超长 `agent.py`。
- 新增业务专用 tool。
- 新增硬编码 issue type 分支或固定工具调用 workflow。
- 新增 Python 业务 action 枚举作为安全判断核心。
- LLM 输出未做 schema 校验。
- 工具失败被吞掉并伪装成正常对话。
- 保留明显无用、过时或重复的代码/数据文件。

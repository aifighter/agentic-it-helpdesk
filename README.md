# Agentic IT Helpdesk Agent

Runnable take-home project implementing **v3.0: Autonomous Planner-Executor Agent + Generic Runtime + Guardrails**.

The backend has one planner path: a live LLM structured JSON planner. There is no deterministic planner, no issue-type router, no business-specific handler branch, and no alternate planner path. If the LLM provider fails, times out, returns invalid JSON, or is not configured, `/api/chat` returns HTTP 500 with a full traceback and the frontend displays that traceback.

## Architecture

```text
React UI
  -> FastAPI /api/chat
    -> HelpdeskAgent loop
      -> live LLM planner returns structured JSON:
         tool_call | ask_user | final_answer | escalate
      -> runtime validates action schema and hard safety boundaries
      -> GenericRuntime executes generic tools
         file_tool | http_tool | sql_tool | search_tool | policy_tool | handoff_tool
      -> observations enter session memory
      -> final_answer / escalate drafts pass Mandatory Compliance Checker
      -> loop ends at final / ask_user / escalate / max_steps
```

Domain behavior comes from `domain_manifest.yaml`, Markdown KB files, structured policy rules, status APIs, resolution history, and the SQLite user directory. Python code does not contain business-specific risk word lists or deterministic workflow plans.

## Important Files

- `AGENTS.md`: project architecture constraints.
- `design_versions.md`: recorded design history and v3.0 plan.
- `agentic_ai_take_home_candidate_instructions_v20260421_01.html`: original assignment.
- `domain_manifest.yaml`: data sources, tool allowlists, planner hints, and configured risk guardrail terms.
- `backend/app/agent.py`: short planner-executor orchestration loop.
- `backend/app/action_schemas.py`: Pydantic planner action schemas.
- `backend/app/planner_contract.py`: planner prompt, payload, and tool schema exposure.
- `backend/app/runtime_executor.py`: generic tool dispatch plus safety guardrails.
- `backend/app/compliance.py`: Mandatory Compliance Checker and config-driven deterministic checks.
- `backend/app/tools.py`: generic file, HTTP, SQL, search, policy, and handoff tools.
- `frontend/src/main.jsx`: chat UI with visible diagnostics and traceback display.
- `tests/run_tests.py`: unit/runtime contract tests only.
- `evals/run_live_llm_eval.py`: real `/api/chat` live LLM evaluation.

## Data Sources

| Source | Implementation |
|---|---|
| Knowledge base | Markdown files under `data/knowledge_base/`, accessed through `file_tool` |
| System status | `/api/status/services` and `/api/status/changes`, accessed through `http_tool` |
| User directory | SQLite database `data/generated/employees.db`, accessed through `sql_tool` |
| Resolution history | JSON archive searched by `search_tool` |
| Policy / rules | YAML rules evaluated by `policy_tool` |

## Guardrails

Hard runtime boundaries:

- `file_tool`: path must stay inside manifest allowlisted roots.
- `http_tool`: host and method must be allowlisted.
- `sql_tool`: only `SELECT`, only allowlisted tables, user-directory queries must be scoped to the current user.
- `final_answer`: resolved answers must cite non-policy evidence and an `allowed=true` policy observation.
- `escalate`: must cite evidence and include a structured handoff payload.
- Mandatory Compliance Checker reviews every final/escalate draft before release.

Soft planner guidance:

- Low-relevance KB searches and low-relevance policy actions are not hard rejected. Runtime records visible `runtime_warning` observations so the planner can self-correct in the next loop.

The frontend shows planner step summaries, tool trace, evidence summary, observations, warnings, runtime rejections, compliance results, and decision rationale. It does not show hidden chain-of-thought.

## Setup

Python is managed with `uv`.

```bash
uv sync
uv run python scripts/seed_data.py
```

Create `.env`:

```bash
DEEPSEEK_API_KEY=your_key_here
DEEPSEEK_MODEL=deepseek-v4-pro
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_THINKING=disabled
STATUS_API_BASE_URL=http://127.0.0.1:8000
```

Backend:

```bash
uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```

Health:

```bash
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/llm/health
```

Frontend:

```bash
cd frontend
pnpm install
pnpm dev
```

Open `http://127.0.0.1:5173`.

## Tests vs Live Eval

`tests/run_tests.py` proves runtime contracts only. It does not prove LLM planning quality.

```bash
uv run python tests/run_tests.py
```

It covers action schema validation, file/HTTP/SQL guardrails, config-driven deterministic compliance checks, soft relevance warnings, and API traceback exposure when the LLM provider is unavailable.

`evals/run_live_llm_eval.py` is the real planner evaluation. It calls `/api/chat` with the unique live LLM planner path and reports outcome, tool trace, evidence count, compliance result, latency, and traceback. Provider errors are marked as failures or `provider_error`; they are never counted as pass and there is no alternate planner path.

```bash
uv run python evals/run_live_llm_eval.py
```

## Demo Prompts

```text
My VPN keeps disconnecting every 10-15 minutes. I'm working remotely and can't access internal tools.
```

```text
Salesforce has been loading extremely slowly since yesterday. My teammates in the Chicago office are seeing the same thing.
```

```text
I just joined the Data Engineering team and need access to the Snowflake production database and internal Grafana dashboards for on-call analytics.
```

```text
Since the IT maintenance window last Friday, our team's automated data pipeline has been failing. Jenkins jobs time out and Tableau reports are stale.
```

## Known Risks

- Live eval depends on DeepSeek availability, network stability, SSL behavior, and latency.
- LLM planner behavior is non-deterministic even with low temperature.
- Compliance checker also uses the LLM after deterministic checks pass, so finalization can fail if the provider fails.
- Live eval costs time and provider tokens; runtime tests are fast but intentionally do not validate planner intelligence.

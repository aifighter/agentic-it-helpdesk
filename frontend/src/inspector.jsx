import React from "react";

const INSPECTOR_TABS = [
  {
    id: "flow",
    label: "流程",
    help: "LLM planner-executor 的可见步骤：工具调用、runtime 拒绝、合规审核和最终动作。",
  },
  {
    id: "evidence",
    label: "证据",
    help: "后端返回的 evidence summary，来源包括 KB、系统状态、用户目录、历史案例、policy 和 handoff。",
  },
  {
    id: "tools",
    label: "工具",
    help: "Generic runtime 的实际工具轨迹，包括 file/http/sql/search/policy/handoff 的输入摘要和输出摘要。",
  },
  {
    id: "observations",
    label: "运行记录",
    help: "runtime observations：警告、拒绝、合规审核和所有可展示的运行事件，不包含隐藏推理。",
  },
];

export function Inspector({ latest, activeTab, onTab }) {
  const stats = latest ? buildRunStats(latest) : null;
  return (
    <aside className="inspector" aria-label="Agent diagnostics">
      <header className="inspector-header">
        <div>
          <h2>诊断面板</h2>
          <p>可展示过程，不含隐藏推理</p>
        </div>
        {latest ? <span>{Math.round(latest.confidence * 100)}%</span> : <span>--</span>}
      </header>

      <div className="decision-card">
        <span>最终决策</span>
        {latest ? (
          <>
            <DiagnosticOutcomePill outcome={latest.outcome} />
            <p>{latest.decision_rationale}</p>
          </>
        ) : (
          <p>发送问题后，这里会显示 final / ask_user / escalate 的决策依据。</p>
        )}
      </div>

      <RunSummary stats={stats} />

      <nav className="inspector-tabs" aria-label="Diagnostics tabs">
        {INSPECTOR_TABS.map((tab) => (
          <button
            type="button"
            key={tab.id}
            className={activeTab === tab.id ? "active" : ""}
            onClick={() => onTab(tab.id)}
            title={tab.help}
            aria-label={`${tab.label}：${tab.help}`}
          >
            <span>{tab.label}</span>
            <small>{tab.help}</small>
          </button>
        ))}
      </nav>

      <div className="inspector-content">
        {activeTab === "flow" ? <FlowView latest={latest} /> : null}
        {activeTab === "evidence" ? <EvidenceView latest={latest} /> : null}
        {activeTab === "tools" ? <ToolsView latest={latest} /> : null}
        {activeTab === "observations" ? <ObservationsView latest={latest} /> : null}
      </div>
    </aside>
  );
}

function RunSummary({ stats }) {
  if (!stats) {
    return (
      <div className="run-summary empty">
        <Metric label="步骤" value="0" />
        <Metric label="工具" value="0" />
        <Metric label="证据" value="0" />
        <Metric label="运行记录" value="0" />
      </div>
    );
  }

  return (
    <div className="run-summary">
      <Metric label="步骤" value={stats.steps} />
      <Metric label="工具" value={stats.tools} />
      <Metric label="证据" value={stats.evidence} />
      <Metric label="警告/拒绝" value={`${stats.warnings}/${stats.rejections}`} tone={stats.rejections ? "risk" : stats.warnings ? "warn" : "ok"} />
    </div>
  );
}

function Metric({ label, value, tone = "neutral" }) {
  return (
    <div className={`metric ${tone}`}>
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}

function FlowView({ latest }) {
  if (!latest?.agent_steps?.length) {
    return <EmptyPanel text="本轮 planner-executor loop 尚未开始。" />;
  }

  return (
    <div className="timeline-list flow-panel">
      {latest.agent_steps.map((step) => (
        <article className={`timeline-item ${step.status}`} key={`${step.step}-${step.action_type}`}>
          <div className="timeline-marker">{step.step}</div>
          <div>
            <div className="timeline-title">
              <strong>{actionLabel(step.action_type)}</strong>
              <span className={`step-status ${step.status}`}>{statusLabel(step.status)}</span>
            </div>
            {step.tool ? <code>{step.tool}.{step.operation}</code> : null}
            <p>{step.thought_summary}</p>
          </div>
        </article>
      ))}
    </div>
  );
}

function EvidenceView({ latest }) {
  if (!latest?.evidence?.length && !latest?.diagnostic_summary?.length) {
    return <EmptyPanel text="暂时没有证据摘要。" />;
  }

  return (
    <div className="panel-stack">
      {latest?.diagnostic_summary?.length ? (
        <section className="mini-section">
          <h3>诊断摘要</h3>
          <ul>
            {latest.diagnostic_summary.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {latest?.evidence?.map((item, index) => (
        <article className="evidence-card" key={`${item.source}-${item.title}-${index}`}>
          <div>
            <strong>{item.title}</strong>
            <span className="source-chip">{sourceLabel(item.source)}</span>
          </div>
          <p>{item.summary}</p>
          <EvidenceMeta item={item} />
        </article>
      ))}
    </div>
  );
}

function ToolsView({ latest }) {
  if (!latest?.tool_calls?.length) {
    return <EmptyPanel text="暂时没有工具调用。" />;
  }

  return (
    <div className="panel-stack">
      {latest.tool_calls.map((call, index) => (
        <details className={`tool-card ${call.ok ? "ok" : "rejected"}`} key={`${call.tool}-${call.action}-${index}`}>
          <summary>
            <span>
              <strong>{call.tool}</strong>
              <code>{call.action}</code>
            </span>
            <small>{call.ok ? "ok" : "error"}</small>
          </summary>
          <pre>{JSON.stringify({ input: call.input, output_summary: call.output_summary }, null, 2)}</pre>
        </details>
      ))}
    </div>
  );
}

function ObservationsView({ latest }) {
  if (!latest?.observations?.length) {
    return <EmptyPanel text="runtime observation、warning、rejection 和 compliance 结果会显示在这里。" />;
  }

  const groups = [
    ["风险与拒绝", latest.observations.filter((item) => !item.ok || item.type === "runtime_warning" || item.type === "runtime_rejection")],
    ["合规审核", latest.observations.filter((item) => item.data?.compliance)],
    ["全部 observations", latest.observations],
  ];

  return (
    <div className="panel-stack">
      {groups.map(([title, items]) => (
        <section className="observation-group" key={title}>
          <h3>{title}</h3>
          {items.length ? (
            items.map((item) => <ObservationCard item={item} key={`${title}-${item.id}`} />)
          ) : (
            <p className="muted-line">本组没有记录。</p>
          )}
        </section>
      ))}
    </div>
  );
}

function ObservationCard({ item }) {
  const tone = !item.ok ? "rejected" : item.type === "runtime_warning" ? "warning" : "ok";
  const compliance = item.data?.compliance;
  return (
    <article className={`observation-card ${tone}`}>
      <div>
        <strong>{observationLabel(item.type)}</strong>
        <span>{item.id}{item.tool ? ` · ${item.tool}.${item.operation}` : ""}</span>
      </div>
      {compliance ? (
        <div className="compliance-grid">
          <Metric label="compliant" value={String(compliance.compliant)} tone={compliance.compliant ? "ok" : "risk"} />
          <Metric label="risk" value={compliance.risk_level} tone={compliance.risk_level === "high" ? "risk" : compliance.risk_level === "medium" ? "warn" : "ok"} />
          <Metric label="next" value={compliance.required_next_action} />
        </div>
      ) : null}
      <p>{item.summary}</p>
    </article>
  );
}

function EvidenceMeta({ item }) {
  const metadata = item.metadata || {};
  const rows = [];
  if (metadata.path) rows.push(["路径", metadata.path]);
  if (metadata.score !== undefined) rows.push(["匹配分", metadata.score]);
  if (metadata.systems?.length) rows.push(["系统", metadata.systems.join(", ")]);
  if (metadata.outcome) rows.push(["历史结果", metadata.outcome]);
  if (metadata.last_updated) rows.push(["更新时间", metadata.last_updated]);
  if (metadata.status) rows.push(["状态", metadata.status]);
  if (metadata.team) rows.push(["队列", metadata.team]);
  if (!rows.length) return null;
  return (
    <dl className="evidence-meta">
      {rows.map(([label, value]) => (
        <React.Fragment key={label}>
          <dt>{label}</dt>
          <dd>{String(value)}</dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

export function sourceLabel(source) {
  const labels = {
    knowledge_base: "知识库",
    system_status: "系统状态",
    user_directory: "用户目录",
    resolution_history: "历史案例",
    policy_rules: "Policy",
    handoff: "Handoff",
  };
  return labels[source] || source;
}

function EmptyPanel({ text }) {
  return <div className="empty-panel">{text}</div>;
}

function DiagnosticOutcomePill({ outcome }) {
  const labels = {
    resolved: "已解决",
    needs_info: "需补充信息",
    escalated: "已升级",
    acknowledged: "已回应",
  };
  return <span className={`outcome-pill ${outcome}`}>{labels[outcome] || outcome}</span>;
}

function actionLabel(actionType) {
  const labels = {
    tool_call: "工具调用",
    ask_user: "追问用户",
    final_answer: "最终回答",
    escalate: "升级人工",
    runtime_rejection: "Runtime 拒绝",
    compliance_check: "合规审核",
  };
  return labels[actionType] || actionType;
}

function statusLabel(status) {
  const labels = {
    ok: "通过",
    rejected: "拒绝",
    error: "错误",
  };
  return labels[status] || status;
}

function observationLabel(type) {
  const labels = {
    tool_result: "工具结果",
    runtime_rejection: "Runtime 拒绝",
    runtime_warning: "Runtime 警告",
    policy_result: "Policy 结果",
    handoff: "Handoff",
    planner_note: "Planner 记录",
  };
  return labels[type] || type;
}

function buildRunStats(latest) {
  const observations = latest.observations || [];
  return {
    steps: latest.agent_steps?.length || 0,
    tools: latest.tool_calls?.length || 0,
    evidence: latest.evidence?.length || 0,
    warnings: observations.filter((item) => item.type === "runtime_warning").length,
    rejections: observations.filter((item) => !item.ok || item.type === "runtime_rejection").length,
  };
}

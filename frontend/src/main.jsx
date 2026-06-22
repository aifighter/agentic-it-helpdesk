import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Inspector, sourceLabel } from "./inspector.jsx";
import "./styles.css";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

const PROFILES = [
  {
    email: "priya.narayan@company.test",
    name: "Priya Narayan",
    team: "Data Engineering",
    initials: "PN",
  },
  {
    email: "alex.chen@company.test",
    name: "Alex Chen",
    team: "Sales / Chicago",
    initials: "AC",
  },
  {
    email: "jordan.lee@company.test",
    name: "Jordan Lee",
    team: "Finance",
    initials: "JL",
  },
  {
    email: "taylor.morgan@company.test",
    name: "Taylor Morgan",
    team: "People",
    initials: "TM",
  },
];

const EXAMPLES = [
  {
    title: "VPN 频繁断开",
    text: "我的 VPN 每 10-15 分钟就会断开。我现在远程办公，访问不了内部工具。",
  },
  {
    title: "Salesforce 变慢",
    text: "Salesforce 从昨天开始加载特别慢，Chicago 办公室的同事也遇到了一样的问题。",
  },
  {
    title: "申请生产权限",
    text: "我刚加入 Data Engineering 团队，需要 Snowflake production database 和内部 Grafana dashboards 的访问权限，用于 on-call analytics。",
  },
  {
    title: "Pipeline 失败",
    text: "从上周五 IT maintenance window 之后，我们团队的自动化数据 pipeline 一直失败。Jenkins jobs timeout，下游 Tableau reports 也 stale。",
  },
  {
    title: "Okta 账号锁定",
    text: "我重置密码后还是无法登录 Okta，页面提示我的账号被锁定了。",
  },
];

function App() {
  const [selectedEmail, setSelectedEmail] = useState(PROFILES[0].email);
  const [sessionId, setSessionId] = useState(null);
  const [input, setInput] = useState(EXAMPLES[0].text);
  const [messages, setMessages] = useState([]);
  const [latest, setLatest] = useState(null);
  const [llmHealth, setLlmHealth] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [activeTab, setActiveTab] = useState("flow");
  const scrollRef = useRef(null);

  const selectedProfile = useMemo(
    () => PROFILES.find((profile) => profile.email === selectedEmail) || PROFILES[0],
    [selectedEmail],
  );

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, loading, error]);

  useEffect(() => {
    fetch(`${API_BASE}/api/llm/health`)
      .then((response) => response.json())
      .then(setLlmHealth)
      .catch((err) =>
        setLlmHealth({
          planner: "live_llm_structured_json",
          configured: false,
          backend_unavailable: true,
          last_error: err.message || "LLM health check failed",
        }),
      );
  }, []);

  async function sendMessage(event) {
    event?.preventDefault();
    const text = input.trim();
    if (!text || loading) return;

    setLoading(true);
    setError("");
    setMessages((items) => [...items, { role: "user", content: text }]);
    setInput("");

    try {
      const response = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          session_id: sessionId,
          user_email: selectedEmail,
        }),
      });

      if (!response.ok) {
        const body = await response.text();
        let detail = body;
        try {
          const parsed = JSON.parse(body);
          detail = parsed.traceback || parsed.message || JSON.stringify(parsed, null, 2);
        } catch {
          detail = body;
        }
        throw new Error(`API 返回 ${response.status}\n\n${detail}`);
      }

      const data = await response.json();
      setSessionId(data.session_id);
      setLatest(data);
      setMessages((items) => [
        ...items,
        {
          role: "assistant",
          content: data.reply,
          outcome: data.outcome,
          rationale: data.decision_rationale,
          sources: data.evidence || [],
        },
      ]);
    } catch (err) {
      setError(err.message || "请求失败");
    } finally {
      setLoading(false);
    }
  }

  function resetSession() {
    setSessionId(null);
    setMessages([]);
    setLatest(null);
    setError("");
    setInput(EXAMPLES[0].text);
  }

  function chooseProfile(email) {
    setSelectedEmail(email);
    setSessionId(null);
    setMessages([]);
    setLatest(null);
    setError("");
  }

  return (
    <main className="codex-shell">
      <Sidebar
        selectedEmail={selectedEmail}
        onSelectProfile={chooseProfile}
        onReset={resetSession}
        onUseExample={(text) => setInput(text)}
      />

      <section className="thread-surface" aria-label="IT helpdesk conversation">
        <ThreadHeader profile={selectedProfile} latest={latest} sessionId={sessionId} llmHealth={llmHealth} />

        <div className="thread-scroll" ref={scrollRef}>
          {messages.length === 0 ? (
            <section className="empty-thread">
              <strong>输入一个 IT 问题，或从左侧选择测试问题。</strong>
              <span>Agent 会展示可见流程、证据来源、工具轨迹和运行记录。</span>
            </section>
          ) : null}

          {messages.map((message, index) => (
            <MessageBubble
              key={`${message.role}-${index}-${message.content.slice(0, 12)}`}
              message={message}
              profile={selectedProfile}
            />
          ))}

          {loading ? (
            <article className="assistant-turn pending-turn">
              <Avatar label="AI" tone="assistant" />
              <div className="turn-body">
                <div className="turn-meta">
                  <span>Agent</span>
                  <small>Planner-executor loop 正在运行</small>
                </div>
                <div className="thinking-line">
                  <span />
                  <span />
                  <span />
                </div>
              </div>
            </article>
          ) : null}

          {error ? (
            <article className="error-turn" aria-live="assertive">
              <div className="error-title">
                <span>运行异常</span>
                <small>后端返回的完整堆栈</small>
              </div>
              <pre className="error-stack">{error}</pre>
            </article>
          ) : null}
        </div>

        <Composer
          input={input}
          loading={loading}
          onInput={setInput}
          onSubmit={sendMessage}
        />
      </section>

      <Inspector latest={latest} activeTab={activeTab} onTab={setActiveTab} />
    </main>
  );
}

function Sidebar({ selectedEmail, onSelectProfile, onReset, onUseExample }) {
  return (
    <aside className="sidebar" aria-label="Workspace sidebar">
      <div className="brand-row">
        <div className="brand-mark">AI</div>
        <div>
          <strong>IT Agent Lab</strong>
          <span>Autonomous Runtime</span>
        </div>
      </div>

      <button type="button" className="new-thread" onClick={onReset}>
        <span>+</span>
        新会话
      </button>

      <div className="sidebar-section">
        <h2>员工身份</h2>
        <div className="profile-list">
          {PROFILES.map((profile) => (
            <button
              type="button"
              key={profile.email}
              className={`profile-item ${selectedEmail === profile.email ? "active" : ""}`}
              onClick={() => onSelectProfile(profile.email)}
            >
              <Avatar label={profile.initials} />
              <span>
                <strong>{profile.name}</strong>
                <small>{profile.team}</small>
              </span>
            </button>
          ))}
        </div>
      </div>

      <div className="sidebar-section examples-section">
        <h2>测试问题</h2>
        <div className="example-list">
          {EXAMPLES.map((example) => (
            <button type="button" key={example.title} onClick={() => onUseExample(example.text)}>
              <strong>{example.title}</strong>
              <span>{example.text}</span>
            </button>
          ))}
        </div>
      </div>
    </aside>
  );
}

function ThreadHeader({ profile, latest, sessionId, llmHealth }) {
  const health = llmHealthLabel(llmHealth);
  return (
    <header className="thread-header">
      <div>
        <div className="crumb">Agentic IT Helpdesk / 当前线程</div>
        <h1>{profile.name}</h1>
        <p>{profile.email}</p>
      </div>
      <div className="header-status">
        <span className={`llm-chip ${health.tone}`} title={health.title}>
          LLM planner: {health.label}
        </span>
        {latest ? <OutcomePill outcome={latest.outcome} /> : <span className="quiet-pill">待输入</span>}
        <span className="session-chip">{sessionId ? `session ${sessionId.slice(0, 8)}` : "new session"}</span>
      </div>
    </header>
  );
}

function llmHealthLabel(llmHealth) {
  if (!llmHealth) {
    return { tone: "missing", label: "checking", title: "正在检查后端 /api/llm/health。" };
  }
  if (llmHealth.backend_unavailable || llmHealth.last_error) {
    return {
      tone: "missing",
      label: "backend unavailable",
      title: llmHealth.last_error || "无法访问后端，请确认 FastAPI 服务已启动。",
    };
  }
  if (llmHealth.configured) {
    return { tone: "configured", label: "configured", title: `${llmHealth.model || "LLM"} 已配置。` };
  }
  return {
    tone: "missing",
    label: "missing key",
    title: "后端已启动，但 LLM_API_KEY 缺失或 HELPDESK_USE_LLM=0。",
  };
}

function MessageBubble({ message, profile }) {
  const isUser = message.role === "user";
  return (
    <article className={isUser ? "user-turn" : "assistant-turn"}>
      <Avatar label={isUser ? profile.initials : "AI"} tone={isUser ? "user" : "assistant"} />
      <div className="turn-body">
        <div className="turn-meta">
          <span>{isUser ? profile.name : "Agent"}</span>
          {message.outcome ? <OutcomePill outcome={message.outcome} /> : null}
        </div>
        <p>{message.content}</p>
        {message.rationale ? (
          <div className="rationale-line">
            <span>决策依据</span>
            {message.rationale}
          </div>
        ) : null}
        {!isUser && message.sources?.length ? <SourceStrip sources={message.sources} /> : null}
      </div>
    </article>
  );
}

function SourceStrip({ sources }) {
  const visibleSources = sources.slice(0, 4);
  return (
    <section className="source-strip" aria-label="回答引用来源">
      <div className="source-strip-title">引用来源</div>
      <div className="source-chip-row">
        {visibleSources.map((item, index) => (
          <span
            className="answer-source-chip"
            key={`${item.source}-${item.title}-${index}`}
            title={`${sourceLabel(item.source)} · ${item.title}\n${item.summary}`}
          >
            <strong>{sourceLabel(item.source)}</strong>
            {item.title}
          </span>
        ))}
        {sources.length > visibleSources.length ? (
          <span className="answer-source-chip muted">+{sources.length - visibleSources.length}</span>
        ) : null}
      </div>
    </section>
  );
}

function Composer({ input, loading, onInput, onSubmit }) {
  return (
    <form className="composer" onSubmit={onSubmit}>
      <div className="composer-frame">
        <textarea
          value={input}
          onChange={(event) => onInput(event.target.value)}
          rows={3}
          placeholder="输入员工问题，或从左侧选择测试问题..."
          aria-label="员工问题"
        />
        <div className="composer-bar">
          <span>DeepSeek planner · Generic runtime</span>
          <button type="submit" disabled={loading || !input.trim()}>
            {loading ? "运行中" : "发送"}
          </button>
        </div>
      </div>
    </form>
  );
}

function Avatar({ label, tone = "neutral" }) {
  return <span className={`avatar ${tone}`}>{label}</span>;
}

function OutcomePill({ outcome }) {
  const labels = {
    resolved: "已解决",
    needs_info: "需补充信息",
    escalated: "已升级",
  };
  return <span className={`outcome-pill ${outcome}`}>{labels[outcome] || outcome}</span>;
}

createRoot(document.getElementById("root")).render(<App />);

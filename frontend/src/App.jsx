import { useEffect, useRef, useState } from "react";
import "./App.css";

const STORAGE_KEY = "localmind-chat";

async function postJson(url, body) {
  let res;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error("Cannot reach the LocalMind server. Check that the backend is running.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data.detail === "string" ? data.detail : "Something went wrong.";
    throw new Error(detail);
  }
  return data;
}

function loadSaved() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY)) || [];
  } catch {
    return [];
  }
}

function ChatPanel() {
  const [messages, setMessages] = useState(loadSaved);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const bottomRef = useRef(null);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
    } catch {
      /* storage unavailable, ignore */
    }
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function send() {
    const text = input.trim();
    if (!text || loading) return;
    const history = messages;
    setMessages([...history, { role: "user", content: text }]);
    setInput("");
    setError("");
    setLoading(true);
    try {
      const data = await postJson("/api/chat", { message: text, history });
      setMessages((m) => [...m, { role: "assistant", content: data.reply }]);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  return (
    <section className="panel">
      <div className="messages">
        {messages.length === 0 && (
          <p className="empty">Ask me anything. I run fully offline on this network.</p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`bubble ${m.role}`}>
            {m.content}
          </div>
        ))}
        {loading && <div className="bubble assistant typing">Thinking…</div>}
        <div ref={bottomRef} />
      </div>
      {error && <div className="error">{error}</div>}
      <div className="composer">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Type a message (Enter to send, Shift+Enter for new line)"
          rows={2}
        />
        <div className="buttons">
          <button onClick={send} disabled={loading || !input.trim()}>
            Send
          </button>
          <button
            className="secondary"
            onClick={() => {
              setMessages([]);
              setError("");
            }}
            disabled={messages.length === 0}
          >
            Clear
          </button>
        </div>
      </div>
    </section>
  );
}

function SummarizePanel() {
  const [text, setText] = useState("");
  const [length, setLength] = useState("medium");
  const [summary, setSummary] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function run() {
    if (!text.trim() || loading) return;
    setLoading(true);
    setError("");
    setSummary("");
    try {
      const data = await postJson("/api/summarize", { text, length });
      setSummary(data.summary);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="panel">
      <div className="summarize">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Paste the text you want summarized"
          rows={10}
        />
        <div className="buttons row">
          <select value={length} onChange={(e) => setLength(e.target.value)}>
            <option value="short">Short (5 bullets)</option>
            <option value="medium">Medium</option>
            <option value="detailed">Detailed</option>
          </select>
          <button onClick={run} disabled={loading || !text.trim()}>
            {loading ? "Summarizing…" : "Summarize"}
          </button>
        </div>
        {error && <div className="error">{error}</div>}
        {summary && <div className="result">{summary}</div>}
      </div>
    </section>
  );
}

export default function App() {
  const [tab, setTab] = useState("chat");
  return (
    <div className="app">
      <header>
        <h1>LocalMind AI</h1>
        <p>Offline assistant running on a local model</p>
        <nav>
          <button className={tab === "chat" ? "active" : ""} onClick={() => setTab("chat")}>
            Chat
          </button>
          <button
            className={tab === "summarize" ? "active" : ""}
            onClick={() => setTab("summarize")}
          >
            Summarize
          </button>
        </nav>
      </header>
      {/* Both panels stay mounted so switching tabs keeps your work. */}
      <div style={{ display: tab === "chat" ? "block" : "none" }}>
        <ChatPanel />
      </div>
      <div style={{ display: tab === "summarize" ? "block" : "none" }}>
        <SummarizePanel />
      </div>
    </div>
  );
}

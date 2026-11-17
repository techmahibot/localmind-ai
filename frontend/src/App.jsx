import { useEffect, useRef, useState } from "react";
import "./App.css";

const STORAGE_KEY = "localmind-chat";
const NETWORK_ERROR = "Cannot reach the LocalMind server. Check that the backend is running.";

// Turns a failed HTTP response into a readable Error message.
async function errorFromResponse(res) {
  const data = await res.json().catch(() => ({}));
  const detail = typeof data.detail === "string" ? data.detail : "Something went wrong.";
  return new Error(detail);
}

async function postJson(url, body) {
  let res;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error(NETWORK_ERROR);
  }
  if (!res.ok) throw await errorFromResponse(res);
  return res.json();
}

async function postForm(url, form) {
  let res;
  try {
    res = await fetch(url, { method: "POST", body: form });
  } catch {
    throw new Error(NETWORK_ERROR);
  }
  if (!res.ok) throw await errorFromResponse(res);
  return res.json();
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
  const abortRef = useRef(null);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
    } catch {
      /* storage unavailable, ignore */
    }
    bottomRef.current?.scrollIntoView({ behavior: "auto" });
  }, [messages, loading]);

  async function send() {
    const text = input.trim();
    if (!text || loading) return;
    const history = messages;
    // Add the user's message and an empty assistant bubble that fills up as text streams in.
    setMessages([...history, { role: "user", content: text }, { role: "assistant", content: "" }]);
    setInput("");
    setError("");
    setLoading(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const res = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, history }),
        signal: controller.signal,
      });
      if (!res.ok) throw await errorFromResponse(res);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let reply = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        reply += decoder.decode(value, { stream: true });
        const snapshot = reply;
        setMessages((m) => [...m.slice(0, -1), { role: "assistant", content: snapshot }]);
      }
    } catch (e) {
      if (e.name === "AbortError") {
        /* user pressed Stop: keep whatever text arrived */
      } else if (e instanceof TypeError) {
        setError(NETWORK_ERROR);
      } else {
        setError(e.message);
      }
    } finally {
      // Drop the assistant bubble if nothing arrived (error or stopped immediately).
      setMessages((m) =>
        m.length && m[m.length - 1].role === "assistant" && m[m.length - 1].content === ""
          ? m.slice(0, -1)
          : m
      );
      setLoading(false);
      abortRef.current = null;
    }
  }

  function stop() {
    abortRef.current?.abort();
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
        {messages.map((m, i) => {
          const waiting = loading && m.role === "assistant" && m.content === "";
          return (
            <div key={i} className={`bubble ${m.role}${waiting ? " typing" : ""}`}>
              {waiting ? "Thinking…" : m.content}
            </div>
          );
        })}
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
          {loading ? (
            <button className="stop" onClick={stop}>
              Stop
            </button>
          ) : (
            <button onClick={send} disabled={!input.trim()}>
              Send
            </button>
          )}
          <button
            className="secondary"
            onClick={() => {
              setMessages([]);
              setError("");
            }}
            disabled={messages.length === 0 || loading}
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
  const [file, setFile] = useState(null);
  const [length, setLength] = useState("medium");
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const fileInputRef = useRef(null);

  function clearFile() {
    setFile(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  async function run() {
    if ((!file && !text.trim()) || loading) return;
    setLoading(true);
    setError("");
    setResult(null);
    try {
      let data;
      if (file) {
        const form = new FormData();
        form.append("file", file);
        form.append("length", length);
        data = await postForm("/api/summarize/file", form);
      } else {
        data = await postJson("/api/summarize", { text, length });
      }
      setResult(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="panel">
      <div className="summarize">
        <div className="file-row">
          <label className="file-label">
            Upload a file (.txt, .md or .pdf)
            <input
              ref={fileInputRef}
              type="file"
              accept=".txt,.md,.pdf"
              onChange={(e) => setFile(e.target.files[0] || null)}
            />
          </label>
          {file && (
            <span className="file-chosen">
              {file.name}
              <button className="link" onClick={clearFile}>
                remove
              </button>
            </span>
          )}
        </div>

        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={file ? "A file is selected, so this box is ignored." : "…or paste the text you want summarized"}
          rows={10}
          disabled={!!file}
        />
        <div className="buttons row">
          <select value={length} onChange={(e) => setLength(e.target.value)}>
            <option value="short">Short (5 bullets)</option>
            <option value="medium">Medium</option>
            <option value="detailed">Detailed</option>
          </select>
          <button onClick={run} disabled={loading || (!file && !text.trim())}>
            {loading ? "Summarizing…" : "Summarize"}
          </button>
        </div>
        {loading && <p className="hint">Long documents are summarized in sections and can take a few minutes.</p>}
        {error && <div className="error">{error}</div>}
        {result && (
          <>
            <div className="result">{result.summary}</div>
            <p className="meta">
              {result.filename && <>File: {result.filename} · </>}
              {result.characters && <>{result.characters.toLocaleString()} characters · </>}
              {result.chunks > 1 ? `summarized in ${result.chunks} sections` : "summarized in one pass"}
              {result.truncated && " · the document was very long, so only the first part was summarized"}
            </p>
          </>
        )}
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

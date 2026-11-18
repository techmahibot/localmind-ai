import { Children, createContext, isValidElement, useCallback, useContext, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import "./App.css";

const STORAGE_KEY = "localmind-chat";
const DOC_KEY = "localmind-doc";
// Each signed-in person gets their own saved chat and document on this browser.
const chatKey = (username) => `${STORAGE_KEY}:${username.toLowerCase()}`;
const docKey = (username) => `${DOC_KEY}:${username.toLowerCase()}`;
const NETWORK_ERROR = "Cannot reach the LocalMind server. Check that the backend is running.";

const IMPROVE_TASKS = [
  { value: "grammar", label: "Fix grammar and spelling" },
  { value: "formal", label: "Make it formal" },
  { value: "simple", label: "Simplify the language" },
  { value: "concise", label: "Make it shorter" },
  { value: "reformat", label: "Improve structure" },
];

const CHAT_STARTERS = [
  "Explain how a neural network learns, in simple words",
  "Help me plan a study timetable for the next two weeks",
  "Write a polite email asking my teacher for an extension",
  "Give me five project ideas that use Python",
];

const DOC_STARTERS = ["What is this document about?", "What are the main points?"];

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

// Turns a failed HTTP response into a readable Error message.
async function errorFromResponse(res) {
  const data = await res.json().catch(() => ({}));
  const detail =
    typeof data.detail === "string"
      ? data.detail
      : Array.isArray(data.detail)
        ? data.detail.map((issue) => issue.msg).filter(Boolean).join(" ") || "Invalid input."
        : "Something went wrong.";
  if (res.status === 401 && detail === "Please sign in to continue.") {
    window.dispatchEvent(new Event("localmind-session-expired"));
  }
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

function loadChat(username) {
  try {
    const saved = JSON.parse(localStorage.getItem(chatKey(username)));
    return Array.isArray(saved) ? saved : [];
  } catch {
    return [];
  }
}

function loadDocState(username) {
  try {
    const saved = JSON.parse(localStorage.getItem(docKey(username)));
    return {
      doc: saved?.doc || null,
      messages: Array.isArray(saved?.messages) ? saved.messages : [],
    };
  } catch {
    return { doc: null, messages: [] };
  }
}

// Copies text to the clipboard. The modern clipboard API only works on https or
// localhost, so when other devices open the app over plain http on the LAN we
// fall back to the older method.
async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to the fallback */
  }
  try {
    const box = document.createElement("textarea");
    box.value = text;
    box.style.position = "fixed";
    box.style.opacity = "0";
    document.body.appendChild(box);
    box.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(box);
    return ok;
  } catch {
    return false;
  }
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function timeStamp() {
  return new Date().toISOString().slice(0, 16).replace("T", "_").replace(":", "-");
}

function exportChat(messages) {
  const lines = messages.map(
    (m) => `${m.role === "user" ? "You" : "LocalMind AI"}:\n${m.content}`
  );
  const blob = new Blob([lines.join("\n\n---\n\n")], { type: "text/plain;charset=utf-8" });
  saveBlob(blob, `localmind-chat-${timeStamp()}.txt`);
}

async function downloadDocx(text) {
  let res;
  try {
    res = await fetch("/api/export/docx", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
  } catch {
    throw new Error(NETWORK_ERROR);
  }
  if (!res.ok) throw await errorFromResponse(res);
  saveBlob(await res.blob(), `localmind-${timeStamp()}.docx`);
}

// The server sends the document passages it used in an X-Sources header (base64 JSON).
function readSources(res) {
  try {
    const raw = res.headers.get("X-Sources");
    if (!raw) return [];
    const bytes = Uint8Array.from(atob(raw), (c) => c.charCodeAt(0));
    return JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    return [];
  }
}

// Reads a streamed text response and calls onText with everything received so far.
async function readTextStream(res, onText) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let reply = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    reply += decoder.decode(value, { stream: true });
    onText(reply);
  }
}

function describeStreamError(e) {
  if (e instanceof TypeError) return NETWORK_ERROR;
  return e.message;
}

// Plain text of a rendered markdown node (used by the "Copy code" button).
function nodeText(node) {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (isValidElement(node)) return nodeText(node.props.children);
  return "";
}

function dropEmptyAssistant(messages) {
  const last = messages[messages.length - 1];
  return last && last.role === "assistant" && last.content === "" ? messages.slice(0, -1) : messages;
}

function summaryNote(r) {
  const how = r.chunks > 1 ? `summarized in ${r.chunks} sections` : "summarized in one pass";
  let note = r.filename
    ? `From ${r.filename} (${r.characters.toLocaleString()} characters), ${how}.`
    : `${how[0].toUpperCase()}${how.slice(1)}.`;
  if (r.truncated) note += " The document was very long, so only the first part was summarized.";
  return note;
}

function improveNote(r) {
  const how = r.chunks > 1 ? `improved in ${r.chunks} sections` : "improved in one pass";
  if (!r.filename) return `${how[0].toUpperCase()}${how.slice(1)}.`;
  return `From ${r.filename}, ${how}. Only the text is kept; fonts, images and layout from the original are not.`;
}

// ---------------------------------------------------------------------------
// Icons
// ---------------------------------------------------------------------------

const ICONS = {
  chat: <path d="M5 5h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-7l-5 4v-4H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z" />,
  doc: (
    <>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5M9 13h6M9 17h4" />
    </>
  ),
  list: <path d="M4 6h16M4 12h10M4 18h14" />,
  pencil: (
    <>
      <path d="M4 20h4L19 9l-4-4L4 16z" />
      <path d="M13 7l4 4" />
    </>
  ),
  send: <path d="M12 19V5M6 11l6-6 6 6" />,
  stop: <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none" />,
  trash: <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6" />,
  download: <path d="M12 4v11M7 11l5 5 5-5M5 20h14" />,
  upload: <path d="M12 16V5M7 10l5-5 5 5M5 20h14" />,
};

function Icon({ name, size = 18 }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {ICONS[name]}
    </svg>
  );
}

function Logo() {
  return (
    <svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true">
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2" />
      <circle cx="12" cy="12" r="3.6" fill="var(--accent)" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Shared pieces
// ---------------------------------------------------------------------------

// Keeps the newest text in view while a reply streams, but stops following as soon as the
// reader scrolls up. A "Jump to latest" button brings them back.
function useAutoScroll(deps) {
  const ref = useRef(null);
  const stuck = useRef(true);
  const [away, setAway] = useState(false);

  function onScroll() {
    const el = ref.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    stuck.current = near;
    setAway(!near);
  }

  useEffect(() => {
    const el = ref.current;
    if (el && stuck.current) el.scrollTop = el.scrollHeight;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  function jump() {
    const el = ref.current;
    if (!el) return;
    stuck.current = true;
    setAway(false);
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }

  return { ref, onScroll, away, jump };
}

function JumpButton({ show, onClick }) {
  if (!show) return null;
  return (
    <button className="jump" onClick={onClick}>
      <Icon name="send" size={14} />
      Jump to latest
    </button>
  );
}

// A short message with an Undo button, used after clearing a conversation.
function UndoToast({ text, onUndo, onDismiss }) {
  useEffect(() => {
    const timer = setTimeout(onDismiss, 8000);
    return () => clearTimeout(timer);
  }, [onDismiss]);
  return (
    <div className="toast" role="status">
      <span>{text}</span>
      <button onClick={onUndo}>Undo</button>
    </div>
  );
}

function CodeBlock({ children }) {
  return (
    <div className="codeblock">
      <CopyButton text={nodeText(children).replace(/\n$/, "")} label="Copy code" />
      <pre>{children}</pre>
    </div>
  );
}

const MARKDOWN_COMPONENTS = { pre: CodeBlock };

// Lets any panel tell the sidebar light that the model is working.
const BusyContext = createContext(() => () => {});

function useBusy() {
  return useContext(BusyContext);
}

function useHealth() {
  const [health, setHealth] = useState({ state: "checking", model: "" });
  useEffect(() => {
    let alive = true;
    async function check() {
      try {
        const res = await fetch("/api/health");
        const data = await res.json();
        if (alive) {
          setHealth({
            state: data.model_server === "reachable" ? "ok" : "no-ollama",
            model: data.model || "",
          });
        }
      } catch {
        if (alive) setHealth({ state: "no-server", model: "" });
      }
    }
    check();
    const timer = setInterval(check, 10000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);
  return health;
}

function StatusLight({ health, busy }) {
  const state = busy && health.state === "ok" ? "busy" : health.state;
  const text = {
    checking: "Checking",
    ok: "Ready",
    busy: "Working",
    "no-ollama": "Model unavailable",
    "no-server": "Server unreachable",
  }[state];
  const showModel = (state === "ok" || state === "busy") && health.model;
  return (
    <div
      className={`status ${state}`}
      role="status"
      title={state === "no-ollama" ? "Start the Ollama app on the host computer, then reload this page." : undefined}
    >
      <span className="led" />
      <div>
        <div className="status-text">{text}</div>
        {showModel && <div className="status-model">{health.model}</div>}
      </div>
    </div>
  );
}

function CopyButton({ text, label = "Copy" }) {
  const [state, setState] = useState(label);
  async function onClick() {
    const ok = await copyText(text);
    setState(ok ? "Copied" : "Copy failed");
    setTimeout(() => setState(label), 1500);
  }
  return (
    <button className="btn small ghost copy" onClick={onClick}>
      {state}
    </button>
  );
}

function DocxButton({ text, onError }) {
  const [busy, setBusy] = useState(false);
  async function onClick() {
    setBusy(true);
    try {
      await downloadDocx(text);
    } catch (e) {
      onError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <button className="btn small ghost" onClick={onClick} disabled={busy}>
      <Icon name="download" size={14} />
      {busy ? "Preparing" : "Download .docx"}
    </button>
  );
}

function NetworkCard() {
  const [url, setUrl] = useState(null);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    fetch("/api/network")
      .then((r) => r.json())
      .then((d) => setUrl(d.urls?.[0] || null))
      .catch(() => {})
      .finally(() => setLoaded(true));
  }, []);
  if (!loaded) return null;
  return (
    <div className="network">
      <p className="network-title">{url ? "Open on other devices" : "This computer only"}</p>
      {url ? (
        <>
          <p className="network-note">Anyone on the same Wi-Fi can use this address.</p>
          <div className="network-url">
            <code>{url.replace("http://", "")}</code>
            <CopyButton text={url} />
          </div>
        </>
      ) : (
        <p className="network-note">No network address found. Connect to Wi-Fi to share this page.</p>
      )}
    </div>
  );
}

function ViewHead({ title, subtitle, children }) {
  return (
    <header className="view-head">
      <div>
        <h2>{title}</h2>
        <p>{subtitle}</p>
      </div>
      {children && <div className="view-actions">{children}</div>}
    </header>
  );
}

function FileDrop({ onFile, inputRef, accept, label, hint, disabled, large }) {
  const [over, setOver] = useState(false);
  function handleDrop(e) {
    e.preventDefault();
    setOver(false);
    if (disabled) return;
    const dropped = e.dataTransfer.files?.[0];
    if (dropped) onFile(dropped);
  }
  return (
    <label
      className={`drop${over ? " over" : ""}${large ? " large" : ""}${disabled ? " disabled" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={handleDrop}
    >
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        disabled={disabled}
        onChange={(e) => onFile(e.target.files[0] || null)}
      />
      <Icon name="upload" size={large ? 28 : 22} />
      <span className="drop-text">
        <strong>{label}</strong>
        <small>{hint}</small>
      </span>
    </label>
  );
}

function FileChip({ file, onRemove }) {
  return (
    <div className="file-chip">
      <Icon name="doc" size={17} />
      <span>{file.name}</span>
      <button className="link" onClick={onRemove}>
        Remove
      </button>
    </div>
  );
}

function Message({ message, streaming }) {
  if (message.role === "user") {
    return (
      <div className="msg user">
        <div className="bubble user">{message.content}</div>
      </div>
    );
  }
  const waiting = streaming && message.content === "";
  return (
    <div className={`msg assistant${streaming ? " streaming" : ""}`}>
      <div className="bubble assistant">
        {waiting ? (
          <p className="thinking">Thinking</p>
        ) : (
          <>
            <div className="markdown">
              <ReactMarkdown components={MARKDOWN_COMPONENTS}>{message.content}</ReactMarkdown>
            </div>
            {!streaming && message.sources?.length > 0 && (
              <details className="sources">
                <summary>Sources ({message.sources.length})</summary>
                {message.sources.map((s) => (
                  <p key={s.n}>
                    <b>[{s.n}]</b>
                    {s.page ? ` page ${s.page}` : ""}: {s.snippet}…
                  </p>
                ))}
              </details>
            )}
            {!streaming && message.content && <CopyButton text={message.content} />}
          </>
        )}
      </div>
    </div>
  );
}

function Composer({ value, onChange, onSubmit, onStop, loading, placeholder, submitLabel }) {
  const ref = useRef(null);
  // Grow with the text, up to a limit.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [value]);

  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSubmit();
    }
  }

  return (
    <div className="composer-wrap">
      <div className="composer">
        <textarea
          ref={ref}
          rows={1}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={placeholder}
          aria-label={placeholder}
        />
        {loading ? (
          <button className="icon-btn stop" aria-label="Stop" onClick={onStop}>
            <Icon name="stop" size={16} />
          </button>
        ) : (
          <button
            className="icon-btn send"
            aria-label={submitLabel}
            onClick={onSubmit}
            disabled={!value.trim()}
          >
            <Icon name="send" size={18} />
          </button>
        )}
      </div>
      <p className="composer-hint">Enter to send, Shift+Enter for a new line.</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------------

function ChatPanel({ username }) {
  const begin = useBusy();
  const [messages, setMessages] = useState(() => loadChat(username));
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [cleared, setCleared] = useState(null);
  const abortRef = useRef(null);
  const scroll = useAutoScroll([messages, loading]);
  const dismissCleared = useCallback(() => setCleared(null), []);

  useEffect(() => {
    try {
      localStorage.setItem(chatKey(username), JSON.stringify(messages));
    } catch {
      /* storage unavailable, ignore */
    }
  }, [messages, username]);

  function send(override) {
    run((override ?? input).trim(), messages);
  }

  function regenerate() {
    const last = messages.map((m) => m.role).lastIndexOf("user");
    if (last < 0 || loading) return;
    run(messages[last].content, messages.slice(0, last));
  }

  async function run(text, history) {
    if (!text || loading) return;
    // Add the user's message and an empty assistant message that fills up as text streams in.
    setMessages([...history, { role: "user", content: text }, { role: "assistant", content: "" }]);
    setCleared(null);
    setInput("");
    setError("");
    setLoading(true);
    const done = begin();

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
      await readTextStream(res, (reply) =>
        setMessages((m) => [...m.slice(0, -1), { role: "assistant", content: reply }])
      );
    } catch (e) {
      if (e.name !== "AbortError") setError(describeStreamError(e));
    } finally {
      setMessages(dropEmptyAssistant);
      setLoading(false);
      abortRef.current = null;
      done();
    }
  }

  return (
    <div className="view-inner">
      <ViewHead title="Chat" subtitle="Talk to the model. Everything stays on this computer.">
        <button
          className="btn ghost small"
          onClick={() => exportChat(messages)}
          disabled={messages.length === 0 || loading}
        >
          <Icon name="download" size={15} />
          Export
        </button>
        <button
          className="btn ghost small"
          onClick={() => {
            setCleared(messages);
            setMessages([]);
            setError("");
          }}
          disabled={messages.length === 0 || loading}
        >
          <Icon name="trash" size={15} />
          Clear
        </button>
      </ViewHead>
      <section className="panel">
        <div className="messages" ref={scroll.ref} onScroll={scroll.onScroll} role="log" aria-live="polite" aria-label="Conversation">
          {messages.length === 0 && (
            <div className="hero">
              <h3>Ask anything. Nothing leaves this network.</h3>
              <p>
                The model runs on the computer hosting this page. Start with one of these, or write
                your own.
              </p>
              <ul className="starters">
                {CHAT_STARTERS.map((q) => (
                  <li key={q}>
                    <button onClick={() => send(q)}>{q}</button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {messages.map((m, i) => (
            <Message
              key={i}
              message={m}
              streaming={loading && i === messages.length - 1 && m.role === "assistant"}
            />
          ))}
          {!loading && messages.length > 0 && messages[messages.length - 1].role === "assistant" && (
            <div className="regen-row">
              <button className="btn small ghost" onClick={regenerate}>
                Regenerate
              </button>
            </div>
          )}
        </div>
        <JumpButton show={scroll.away} onClick={scroll.jump} />
        {cleared && (
          <UndoToast
            text="Chat cleared."
            onDismiss={dismissCleared}
            onUndo={() => {
              setMessages(cleared);
              setCleared(null);
            }}
          />
        )}
        {error && (
          <div className="error" role="alert">
            {error}
          </div>
        )}
        <Composer
          value={input}
          onChange={setInput}
          onSubmit={() => send()}
          onStop={() => abortRef.current?.abort()}
          loading={loading}
          placeholder="Message LocalMind"
          submitLabel="Send"
        />
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Documents (ask questions about a file)
// ---------------------------------------------------------------------------

function DocumentsPanel({ username }) {
  const begin = useBusy();
  const [doc, setDoc] = useState(() => loadDocState(username).doc);
  const [messages, setMessages] = useState(() => loadDocState(username).messages);
  const [input, setInput] = useState("");
  const [uploading, setUploading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [cleared, setCleared] = useState(null);
  const abortRef = useRef(null);
  const fileInputRef = useRef(null);
  const scroll = useAutoScroll([messages, loading]);
  const dismissCleared = useCallback(() => setCleared(null), []);

  useEffect(() => {
    try {
      localStorage.setItem(docKey(username), JSON.stringify({ doc, messages }));
    } catch {
      /* storage unavailable, ignore */
    }
  }, [doc, messages, username]);

  async function upload(file) {
    if (!file) return;
    setUploading(true);
    setError("");
    const done = begin();
    try {
      const form = new FormData();
      form.append("file", file);
      const data = await postForm("/api/docs", form);
      setDoc(data);
      setMessages([]);
    } catch (e) {
      setError(e.message);
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
      done();
    }
  }

  async function ask(override) {
    const question = (override ?? input).trim();
    if (!question || loading || !doc) return;
    const history = messages.map(({ role, content }) => ({ role, content }));
    setCleared(null);
    setMessages([
      ...messages,
      { role: "user", content: question },
      { role: "assistant", content: "", sources: [] },
    ]);
    setInput("");
    setError("");
    setLoading(true);
    const done = begin();

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const res = await fetch(`/api/docs/${doc.doc_id}/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, history }),
        signal: controller.signal,
      });
      if (!res.ok) {
        // The server forgets documents when it restarts.
        if (res.status === 404) setDoc(null);
        throw await errorFromResponse(res);
      }
      const sources = readSources(res);
      await readTextStream(res, (reply) =>
        setMessages((m) => [...m.slice(0, -1), { role: "assistant", content: reply, sources }])
      );
    } catch (e) {
      if (e.name !== "AbortError") setError(describeStreamError(e));
    } finally {
      setMessages(dropEmptyAssistant);
      setLoading(false);
      abortRef.current = null;
      done();
    }
  }

  const subtitle = "Ask questions about a PDF or Word file. Answers cite their sources.";

  if (!doc) {
    return (
      <div className="view-inner">
        <ViewHead title="Documents" subtitle={subtitle} />
        <section className="panel scroll">
          <div className="upload-intro">
            <h3>Ask questions about a file</h3>
            <p>
              Answers come only from your file, and each one shows the pages it used. The file stays
              on this computer.
            </p>
          </div>
          <FileDrop
            large
            onFile={upload}
            inputRef={fileInputRef}
            accept=".pdf,.docx,.txt,.md"
            label={uploading ? "Reading your document" : "Drop a file here, or click to choose one"}
            hint="PDF, DOCX, TXT or MD, up to 5 MB"
            disabled={uploading}
          />
          {error && (
            <div className="error" role="alert">
              {error}
            </div>
          )}
        </section>
      </div>
    );
  }

  return (
    <div className="view-inner">
      <ViewHead title="Documents" subtitle={subtitle}>
        <button
          className="btn ghost small"
          onClick={() => {
            setCleared(messages);
            setMessages([]);
            setError("");
          }}
          disabled={messages.length === 0 || loading}
        >
          <Icon name="trash" size={15} />
          Clear
        </button>
      </ViewHead>
      <section className="panel">
        <div className="doc-card">
          <Icon name="doc" size={20} />
          <div>
            <div className="doc-name">{doc.filename}</div>
            <div className="doc-meta">
              {doc.pages ? `${doc.pages} pages, ` : ""}
              {doc.sections} sections
            </div>
          </div>
          <div className="doc-actions">
            <label className="btn small ghost" aria-disabled={uploading || loading}>
              <Icon name="upload" size={15} />
              {uploading ? "Reading" : "Use a different file"}
              <input
                ref={fileInputRef}
                type="file"
                accept=".pdf,.docx,.txt,.md"
                onChange={(e) => upload(e.target.files[0])}
                disabled={uploading || loading}
              />
            </label>
          </div>
        </div>

        <div className="messages" ref={scroll.ref} onScroll={scroll.onScroll} role="log" aria-live="polite" aria-label="Conversation">
          {messages.length === 0 && (
            <div className="hero">
              <h3>What do you want to know?</h3>
              <p>Ask in your own words. Try one of these to start.</p>
              <ul className="starters">
                {DOC_STARTERS.map((q) => (
                  <li key={q}>
                    <button onClick={() => ask(q)} disabled={loading}>
                      {q}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {messages.map((m, i) => (
            <Message
              key={i}
              message={m}
              streaming={loading && i === messages.length - 1 && m.role === "assistant"}
            />
          ))}
        </div>
        <JumpButton show={scroll.away} onClick={scroll.jump} />
        {cleared && (
          <UndoToast
            text="Conversation cleared."
            onDismiss={dismissCleared}
            onUndo={() => {
              setMessages(cleared);
              setCleared(null);
            }}
          />
        )}
        {error && (
          <div className="error" role="alert">
            {error}
          </div>
        )}
        <Composer
          value={input}
          onChange={setInput}
          onSubmit={() => ask()}
          onStop={() => abortRef.current?.abort()}
          loading={loading}
          placeholder="Ask about this document"
          submitLabel="Ask"
        />
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Summarize
// ---------------------------------------------------------------------------

function SummarizePanel() {
  const begin = useBusy();
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
    const done = begin();
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
      done();
    }
  }

  return (
    <div className="view-inner">
      <ViewHead title="Summarize" subtitle="Shorten text or a whole file to its key points." />
      <section className="panel scroll">
        {file ? (
          <FileChip file={file} onRemove={clearFile} />
        ) : (
          <FileDrop
            onFile={setFile}
            inputRef={fileInputRef}
            accept=".txt,.md,.pdf,.docx"
            label="Drop a file here, or click to choose one"
            hint="PDF, DOCX, TXT or MD, up to 5 MB"
          />
        )}
        <textarea
          className="field"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={file ? "A file is selected, so this box is ignored." : "Or paste the text you want summarized"}
          rows={9}
          disabled={!!file}
        />
        <div className="controls">
          <select value={length} onChange={(e) => setLength(e.target.value)} aria-label="Summary length">
            <option value="short">Short (5 bullets)</option>
            <option value="medium">Medium</option>
            <option value="detailed">Detailed</option>
          </select>
          <button className="btn primary" onClick={run} disabled={loading || (!file && !text.trim())}>
            {loading ? "Summarizing" : "Summarize"}
          </button>
        </div>
        {loading && <p className="hint">Long documents are summarized in sections and can take a few minutes.</p>}
        {error && (
          <div className="error" role="alert">
            {error}
          </div>
        )}
        {result && (
          <div className="result-card">
            <div className="result-head">
              <h3>Summary</h3>
              <div className="result-actions">
                <CopyButton text={result.summary} />
                <DocxButton text={result.summary} onError={setError} />
              </div>
            </div>
            <div className="result">{result.summary}</div>
            <p className="meta">{summaryNote(result)}</p>
          </div>
        )}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Improve text
// ---------------------------------------------------------------------------

function ImprovePanel() {
  const begin = useBusy();
  const [text, setText] = useState("");
  const [file, setFile] = useState(null);
  const [task, setTask] = useState("grammar");
  const [result, setResult] = useState(null);
  const [view, setView] = useState("improved");
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
    setView("improved");
    const done = begin();
    try {
      let data;
      if (file) {
        const form = new FormData();
        form.append("file", file);
        form.append("task", task);
        data = await postForm("/api/improve-text/file", form);
      } else {
        data = await postJson("/api/improve-text/long", { text, task });
        data = { ...data, original: text };
      }
      setResult(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
      done();
    }
  }

  return (
    <div className="view-inner">
      <ViewHead title="Improve text" subtitle="Fix grammar, change the tone, or tidy up the structure." />
      <section className="panel scroll">
        {file ? (
          <FileChip file={file} onRemove={clearFile} />
        ) : (
          <FileDrop
            onFile={setFile}
            inputRef={fileInputRef}
            accept=".txt,.md,.pdf,.docx"
            label="Drop a file here, or click to choose one"
            hint="DOCX, PDF, TXT or MD, up to 5 MB"
          />
        )}
        <textarea
          className="field"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={file ? "A file is selected, so this box is ignored." : "Or paste or type the text you want improved"}
          rows={8}
          disabled={!!file}
        />
        <div className="controls">
          <select value={task} onChange={(e) => setTask(e.target.value)} aria-label="What to improve">
            {IMPROVE_TASKS.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
          <button className="btn primary" onClick={run} disabled={loading || (!file && !text.trim())}>
            {loading ? "Improving" : "Improve"}
          </button>
        </div>
        {loading && <p className="hint">Long text is improved section by section and can take a few minutes.</p>}
        {error && (
          <div className="error" role="alert">
            {error}
          </div>
        )}
        {result && (
          <div className="result-card">
            <div className="result-head">
              <h3>Improved text</h3>
              <div className="result-actions">
                <CopyButton text={result.improved_text} />
                <DocxButton text={result.improved_text} onError={setError} />
                <button
                  className="btn small ghost"
                  onClick={() => {
                    setText(result.improved_text);
                    clearFile();
                    setResult(null);
                  }}
                >
                  Use as new input
                </button>
              </div>
            </div>
            {result.original && (
              <div className="segmented" role="group" aria-label="View">
                <button
                  className={view === "improved" ? "on" : ""}
                  aria-pressed={view === "improved"}
                  onClick={() => setView("improved")}
                >
                  Improved
                </button>
                <button
                  className={view === "compare" ? "on" : ""}
                  aria-pressed={view === "compare"}
                  onClick={() => setView("compare")}
                >
                  Compare
                </button>
              </div>
            )}
            {view === "compare" && result.original ? (
              <div className="compare">
                <div>
                  <h4>Original</h4>
                  <div className="result">{result.original}</div>
                </div>
                <div>
                  <h4>Improved</h4>
                  <div className="result">{result.improved_text}</div>
                </div>
              </div>
            ) : (
              <div className="result">{result.improved_text}</div>
            )}
            <p className="meta">{improveNote(result)}</p>
          </div>
        )}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// App shell
// ---------------------------------------------------------------------------

const TABS = [
  { id: "chat", label: "Chat", icon: "chat", Panel: ChatPanel },
  { id: "documents", label: "Documents", icon: "doc", Panel: DocumentsPanel },
  { id: "summarize", label: "Summarize", icon: "list", Panel: SummarizePanel },
  { id: "improve", label: "Improve text", icon: "pencil", Panel: ImprovePanel },
];

function AuthPage({ onLogin, notice }) {
  const [mode, setMode] = useState("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const signup = mode === "signup";

  async function submit(e) {
    e.preventDefault();
    if (!username.trim() || !password || loading) return;
    if (signup && password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const data = await postJson(`/api/auth/${signup ? "signup" : "login"}`, {
        username: username.trim(),
        password,
      });
      setPassword("");
      setConfirmPassword("");
      onLogin(data.username);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  function switchMode() {
    setMode(signup ? "login" : "signup");
    setPassword("");
    setConfirmPassword("");
    setError("");
  }

  return (
    <main className="login-page">
      <form className="login-card" onSubmit={submit}>
        <div className="login-logo">
          <Logo />
        </div>
        <h1>{signup ? "Create your account" : "Welcome to LocalMind AI"}</h1>
        <p>
          {signup
            ? "Choose a username and password to get started."
            : "Log in to continue to your AI assistant."}
        </p>
        {notice && (
          <div className="notice" role="status">
            {notice}
          </div>
        )}
        <label htmlFor="username">Username</label>
        <input
          id="username"
          autoComplete="username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          minLength={signup ? 3 : undefined}
          maxLength={signup ? 30 : undefined}
          required
          autoFocus
        />
        {signup && <small className="field-hint">3 to 30 letters, numbers or underscores</small>}
        <label htmlFor="password">Password</label>
        <div className="password-field">
          <input
            id="password"
            type={showPassword ? "text" : "password"}
            autoComplete={signup ? "new-password" : "current-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={signup ? 10 : undefined}
            required
          />
          <button
            type="button"
            className="show-password"
            onClick={() => setShowPassword((v) => !v)}
            aria-pressed={showPassword}
          >
            {showPassword ? "Hide" : "Show"}
          </button>
        </div>
        {signup && (
          <>
            <small className="field-hint">At least 10 characters</small>
            <label htmlFor="confirm-password">Confirm password</label>
            <input
              id="confirm-password"
              type={showPassword ? "text" : "password"}
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
            />
          </>
        )}
        {error && (
          <div className="error" role="alert">
            {error}
          </div>
        )}
        <button className="login-button" type="submit" disabled={loading}>
          {loading ? "Please wait" : signup ? "Create account" : "Log in"}
        </button>
        <div className="auth-switch">
          {signup ? "Already have an account?" : "New to LocalMind AI?"}
          <button type="button" onClick={switchMode}>
            {signup ? "Log in" : "Sign up"}
          </button>
        </div>
      </form>
    </main>
  );
}

function Account({ username, onLogout }) {
  return (
    <div className="account">
      <span className="avatar" aria-hidden="true">
        {username.slice(0, 1).toUpperCase()}
      </span>
      <span className="account-name">{username}</span>
      <button className="link" onClick={onLogout}>
        Sign out
      </button>
    </div>
  );
}

export default function App() {
  const [user, setUser] = useState(null);
  const [checking, setChecking] = useState(true);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    fetch("/api/auth/me")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (active) setUser(data?.username || null);
      })
      .catch(() => {
        if (active) setUser(null);
      })
      .finally(() => {
        if (active) setChecking(false);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const expired = () => {
      setNotice("Your session ended. Please sign in again.");
      setUser(null);
    };
    window.addEventListener("localmind-session-expired", expired);
    return () => window.removeEventListener("localmind-session-expired", expired);
  }, []);

  async function logout() {
    try {
      await postJson("/api/auth/logout", {});
    } catch {
      /* the cookie may already be gone; sign out on screen anyway */
    }
    setNotice("");
    setUser(null);
  }

  if (checking) return <div className="session-check">Checking session</div>;
  if (!user)
    return (
      <AuthPage
        notice={notice}
        onLogin={(name) => {
          setNotice("");
          setUser(name);
        }}
      />
    );
  return <Workspace username={user} onLogout={logout} />;
}

function Workspace({ username, onLogout }) {
  const [tab, setTab] = useState("chat");
  const health = useHealth();
  const [busyCount, setBusyCount] = useState(0);

  // Each panel calls begin() when it starts work and the returned function when it ends.
  const begin = useCallback(() => {
    setBusyCount((c) => c + 1);
    let ended = false;
    return () => {
      if (ended) return;
      ended = true;
      setBusyCount((c) => Math.max(0, c - 1));
    };
  }, []);

  return (
    <BusyContext.Provider value={begin}>
      <div className="shell">
        <a className="skip-link" href="#main">
          Skip to content
        </a>
        <aside className="sidebar">
          <div className="brand">
            <Logo />
            <span className="brand-name">
              LocalMind<em>AI</em>
            </span>
          </div>
          <StatusLight health={health} busy={busyCount > 0} />
          <nav className="nav" aria-label="Sections">
            {TABS.map((t) => (
              <button
                key={t.id}
                className={tab === t.id ? "active" : ""}
                aria-current={tab === t.id ? "page" : undefined}
                onClick={() => setTab(t.id)}
              >
                <Icon name={t.icon} />
                <span>{t.label}</span>
              </button>
            ))}
          </nav>
          <div className="sidebar-foot">
            <NetworkCard />
            <Account username={username} onLogout={onLogout} />
          </div>
        </aside>
        <main className="main" id="main" tabIndex={-1}>
          {/* All panels stay mounted so switching tabs keeps your work. */}
          {TABS.map(({ id, Panel }) => (
            <div key={id} className={`view${tab === id ? " active" : ""}`}>
              <Panel username={username} />
            </div>
          ))}
        </main>
      </div>
    </BusyContext.Provider>
  );
}

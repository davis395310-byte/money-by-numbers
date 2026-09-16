"use client";

import { useEffect, useRef, useState, type CSSProperties } from "react";
import {
  fetchNumbyHistory,
  fetchNumbyStatus,
  sendNumbyMessage,
  type NumbyMessage,
  type NumbyStatusResponse,
} from "@/lib/api";
import { RESPONSIBLE_GAMBLING_NOTICE } from "@/lib/config";

/**
 * Numby — the Money By Numbers AI assistant (Phase 7).
 *
 * Chat UI only: every word of every answer comes from POST /api/numby/chat
 * (backend/app/routers/numby.py), which grounds answers in stored platform
 * data and refuses to fabricate. This page renders those answers verbatim,
 * shows the backend's honest status (LLM mode vs basic template mode), and
 * renders DATA UNAVAILABLE when the backend is unreachable.
 */

const SUGGESTED_QUESTIONS = [
  "How does the model work?",
  "What is your track record?",
  "Who do you pick this week?",
  "Show me the best bets",
  "Chiefs vs Broncos prediction",
];

type Msg = NumbyMessage & { disclaimer?: string | null };

export default function NumbyPage() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [status, setStatus] = useState<NumbyStatusResponse | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchNumbyStatus().then((s) => {
      if (s === null) setUnreachable(true);
      else setStatus(s);
    });
    const saved =
      typeof window !== "undefined"
        ? window.localStorage.getItem("numby_session_id")
        : null;
    if (saved) {
      setSessionId(saved);
      fetchNumbyHistory(saved).then((h) => {
        if (h && h.status === "ok") setMessages(h.messages);
      });
    }
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sending]);

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    setSending(true);
    setMessages((m) => [...m, { role: "user", content: trimmed }]);
    setInput("");
    const res = await sendNumbyMessage(trimmed, sessionId);
    setSending(false);
    if (res === null) {
      setUnreachable(true);
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          content:
            "DATA UNAVAILABLE — I can't reach the Money By Numbers backend right now. Please try again in a moment.",
        },
      ]);
      return;
    }
    if (res.status === "quota_exceeded") {
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          content:
            `You've reached the daily Numby question limit on your current plan. ` +
            `${res.reason ?? ""} ` +
            `See ${res.upgrade_url ?? "/pricing"} for plans with higher limits — ` +
            `predictions and track record stay free either way.`,
        },
      ]);
      return;
    }
    if (res.status !== "ok" || !res.reply) {
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          content: `Something went wrong: ${res.reason ?? "unknown error"}. Please try again.`,
        },
      ]);
      return;
    }
    if (res.session_id && res.session_id !== sessionId) {
      setSessionId(res.session_id);
      try {
        window.localStorage.setItem("numby_session_id", res.session_id);
      } catch {
        /* storage unavailable — chat still works for this visit */
      }
    }
    setMessages((m) => [
      ...m,
      { role: "assistant", content: res.reply as string, disclaimer: res.disclaimer },
    ]);
  }

  function newChat() {
    setMessages([]);
    setSessionId(null);
    try {
      window.localStorage.removeItem("numby_session_id");
    } catch {
      /* ignore */
    }
  }

  return (
    <main style={styles.page}>
      <header style={styles.header}>
        <div>
          <h1 style={styles.title}>Numby</h1>
          <p style={styles.subtitle}>
            The Money By Numbers AI assistant — answers grounded in our actual
            model data. Numby never invents numbers.
          </p>
        </div>
        <button style={styles.newChat} onClick={newChat} type="button">
          New chat
        </button>
      </header>

      {unreachable ? (
        <div style={styles.bannerWarn}>
          DATA UNAVAILABLE — backend unreachable. Chat is disabled until the API
          responds.
        </div>
      ) : (
        status && (
          <div style={styles.banner}>
            {status.ai_configured
              ? "LLM-enhanced answers."
              : "Basic mode — no AI provider key configured. Answers come straight from stored platform data."}
          </div>
        )
      )}

      <div style={styles.thread} aria-live="polite">
        {messages.length === 0 && !unreachable && (
          <div style={styles.empty}>
            <p style={styles.emptyTitle}>Ask Numby anything about the NFL model</p>
            <div style={styles.chips}>
              {SUGGESTED_QUESTIONS.map((q) => (
                <button
                  key={q}
                  type="button"
                  style={styles.chip}
                  onClick={() => send(q)}
                  disabled={sending}
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            style={{
              ...styles.bubble,
              ...(m.role === "user" ? styles.userBubble : styles.aiBubble),
            }}
          >
            <div style={styles.role}>{m.role === "user" ? "You" : "Numby"}</div>
            <div style={styles.text}>{m.content}</div>
            {m.disclaimer && <div style={styles.disclaimer}>{m.disclaimer}</div>}
          </div>
        ))}
        {sending && (
          <div style={{ ...styles.bubble, ...styles.aiBubble }}>
            <div style={styles.role}>Numby</div>
            <div style={styles.text}>Thinking…</div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <form
        style={styles.form}
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        <input
          style={styles.input}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about a matchup, edges, injuries, weather…"
          maxLength={1000}
          disabled={sending || unreachable}
          aria-label="Message Numby"
        />
        <button
          style={styles.send}
          type="submit"
          disabled={sending || unreachable || !input.trim()}
        >
          Send
        </button>
      </form>
      <p style={styles.footer}>{RESPONSIBLE_GAMBLING_NOTICE}</p>
    </main>
  );
}

const styles: Record<string, CSSProperties> = {
  page: {
    maxWidth: 720,
    margin: "0 auto",
    padding: "16px 16px 32px",
    display: "flex",
    flexDirection: "column",
    minHeight: "80vh",
  },
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "flex-start",
    gap: 12,
    marginBottom: 8,
  },
  title: { fontSize: 28, margin: 0 },
  subtitle: { margin: "4px 0 0", opacity: 0.7, fontSize: 14 },
  newChat: {
    flexShrink: 0,
    padding: "8px 12px",
    borderRadius: 8,
    border: "1px solid #444",
    background: "transparent",
    color: "inherit",
    cursor: "pointer",
  },
  banner: {
    fontSize: 13,
    opacity: 0.75,
    padding: "8px 12px",
    border: "1px solid #333",
    borderRadius: 8,
    marginBottom: 12,
  },
  bannerWarn: {
    fontSize: 13,
    padding: "8px 12px",
    border: "1px solid #a33",
    borderRadius: 8,
    marginBottom: 12,
  },
  thread: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    gap: 12,
    overflowY: "auto",
    paddingBottom: 12,
  },
  empty: { textAlign: "center", padding: "32px 0" },
  emptyTitle: { fontSize: 16, margin: "0 0 12px" },
  chips: { display: "flex", flexWrap: "wrap", gap: 8, justifyContent: "center" },
  chip: {
    padding: "8px 12px",
    borderRadius: 999,
    border: "1px solid #444",
    background: "transparent",
    color: "inherit",
    cursor: "pointer",
    fontSize: 13,
  },
  bubble: {
    borderRadius: 12,
    padding: "10px 14px",
    maxWidth: "88%",
    whiteSpace: "pre-wrap",
  },
  userBubble: { alignSelf: "flex-end", background: "#1d4ed8", color: "#fff" },
  aiBubble: { alignSelf: "flex-start", background: "#1a1a1a", border: "1px solid #333" },
  role: { fontSize: 11, opacity: 0.6, marginBottom: 4, fontWeight: 600 },
  text: { fontSize: 15, lineHeight: 1.5 },
  disclaimer: {
    marginTop: 10,
    paddingTop: 8,
    borderTop: "1px solid #333",
    fontSize: 12,
    opacity: 0.75,
  },
  form: { display: "flex", gap: 8, marginTop: 12 },
  input: {
    flex: 1,
    padding: "12px",
    borderRadius: 10,
    border: "1px solid #444",
    background: "#111",
    color: "inherit",
    fontSize: 15,
  },
  send: {
    padding: "0 20px",
    borderRadius: 10,
    border: "none",
    background: "#1d4ed8",
    color: "#fff",
    fontWeight: 600,
    cursor: "pointer",
  },
  footer: { fontSize: 12, opacity: 0.6, marginTop: 12 },
};

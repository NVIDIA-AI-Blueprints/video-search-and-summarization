"use client";

import { useState } from "react";
import { Citation } from "@/components/chat/Citation";
import type { Citation as CitationType } from "@/lib/types";

export function ChatPanel({
  messages,
  busy,
  onSend,
  onCitationClick,
}: {
  messages: { role: "user" | "assistant"; text: string; citations?: CitationType[] }[];
  busy: boolean;
  onSend: (question: string) => void;
  onCitationClick?: (citation: CitationType) => void;
}) {
  const [draft, setDraft] = useState("");

  const submit = () => {
    if (!draft.trim() || busy) return;
    onSend(draft.trim());
    setDraft("");
  };

  return (
    <div className="chat-panel">
      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="chat-empty">Ask anything about this video.</div>
        )}
        {messages.map((message, index) => (
          <div key={index} className={`chat-message ${message.role}`}>
            <p>{message.text}</p>
            {message.citations && message.citations.length > 0 && (
              <div className="chat-citations">
                {message.citations.map((citation, cIndex) => (
                  <Citation key={cIndex} citation={citation} onClick={onCitationClick} />
                ))}
              </div>
            )}
          </div>
        ))}
        {busy && <div className="chat-empty">Thinking…</div>}
      </div>
      <div className="chat-input-row">
        <input
          value={draft}
          placeholder="Ask about this video…"
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && submit()}
          disabled={busy}
        />
        <button type="button" onClick={submit} disabled={busy || !draft.trim()}>
          Send
        </button>
      </div>
    </div>
  );
}

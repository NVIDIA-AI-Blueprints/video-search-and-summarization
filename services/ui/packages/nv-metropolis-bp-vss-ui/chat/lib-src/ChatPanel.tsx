// SPDX-License-Identifier: MIT
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { useChatStream } from './useChatStream';
import type { ChatMessage, ChatPanelProps, ChatStep } from './types';

/** Stable per-mount id so the adapter maps this panel to one agent session. */
function useConversationId(supplied?: string): string {
  const ref = useRef(supplied);
  if (!ref.current) {
    ref.current = `vss-${Math.random().toString(36).slice(2, 10)}`;
  }
  return ref.current;
}

const StepList: React.FC<{ steps: ChatStep[]; streaming?: boolean }> = ({ steps, streaming }) => {
  // Open while the agent is working, so progress is visible the way the
  // toolkit UI showed it; collapses once the answer lands so finished steps do
  // not bury it. An explicit click wins over that default either way.
  const [manual, setManual] = useState<boolean | null>(null);
  const open = manual ?? !!streaming;
  if (!steps.length) return null;

  return (
    <div className="vss-chat-steps">
      <button
        type="button"
        className="vss-chat-steps-toggle"
        onClick={() => setManual(!open)}
        aria-expanded={open}
      >
        <span>{open ? '▾' : '▸'}</span>
        <span>
          Intermediate steps ({steps.length})
          {streaming ? ' — running' : ''}
        </span>
      </button>
      {open && (
        <ul className="vss-chat-steps-list">
          {steps.map((s) => (
            <li key={s.id} data-status={s.status}>
              <span>
                <span className="vss-chat-step-dot" />
                <span className="vss-chat-step-name">{s.name}</span>
              </span>
              {s.payload ? <pre>{s.payload}</pre> : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

const Message: React.FC<{ message: ChatMessage; showSteps: boolean }> = ({ message, showSteps }) => (
  <div className={`vss-chat-msg vss-chat-msg-${message.role}`} data-streaming={!!message.streaming}>
    {showSteps && message.steps?.length ? (
      <StepList steps={message.steps} streaming={message.streaming} />
    ) : null}
    {message.error ? (
      <div className="vss-chat-error">⚠ {message.error}</div>
    ) : message.role === 'assistant' ? (
      <div className="vss-chat-markdown">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
        {message.streaming && !message.content ? <span className="vss-chat-cursor">▍</span> : null}
      </div>
    ) : (
      <div className="vss-chat-usertext">{message.content}</div>
    )}
  </div>
);

/**
 * VSS chat surface.
 *
 * Used for both the main chat tab and the docked sidebar; the only difference
 * is the container it is given. Speaks the BYO agent contract directly, so it
 * works against any backend implementing `/chat/stream`.
 */
export const ChatPanel: React.FC<ChatPanelProps> = ({
  endpoint,
  title,
  theme = 'dark',
  placeholder = 'Ask about your video…',
  showSteps = true,
  onAnswer,
  onSubmit,
  className,
}) => {
  const conversationId = useConversationId(endpoint.conversationId);
  const config = useMemo(
    () => ({ ...endpoint, conversationId }),
    [endpoint, conversationId],
  );

  // The conversation goes with the answer: consumers fetch per-conversation
  // artifacts, and a process-wide 'last result' would cross conversations.
  const handleAnswer = useCallback(
    (answer: string) => onAnswer?.(answer, conversationId),
    [onAnswer, conversationId],
  );
  const { messages, busy, send, abort } = useChatStream(config, handleAnswer);

  const [draft, setDraft] = useState('');
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages]);

  const submit = useCallback(() => {
    const text = draft.trim();
    if (!text || busy) return;
    setDraft('');
    onSubmit?.(text);
    void send(text);
  }, [busy, draft, onSubmit, send]);

  return (
    <section className={`vss-chat vss-chat-${theme}${className ? ` ${className}` : ''}`}>
      {title ? <header className="vss-chat-header">{title}</header> : null}

      <div className="vss-chat-log" role="log" aria-live="polite">
        {messages.length === 0 ? (
          <p className="vss-chat-empty">{placeholder}</p>
        ) : (
          messages.map((m) => <Message key={m.id} message={m} showSteps={showSteps} />)
        )}
        <div ref={endRef} />
      </div>

      <form
        className="vss-chat-input"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            // Enter sends; Shift+Enter is a newline.
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder={placeholder}
          rows={1}
          aria-label="Message"
        />
        {busy ? (
          <button type="button" onClick={abort} className="vss-chat-stop">
            Stop
          </button>
        ) : (
          <button type="submit" disabled={!draft.trim()}>
            Send
          </button>
        )}
      </form>
    </section>
  );
};

export default ChatPanel;

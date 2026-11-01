// SPDX-License-Identifier: Apache-2.0
'use client';

import Link from 'next/link';
import { type FormEvent, useEffect, useRef, useState } from 'react';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Bubble, BubbleContent } from '@/components/ui/bubble';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Marker, MarkerContent } from '@/components/ui/marker';
import { Message } from '@/components/ui/message';
import {
  MessageScroller,
  MessageScrollerContent,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from '@/components/ui/message-scroller';
import { citationHref, citationsByIncident, libraryChatRequestBody } from '@/lib/chat/library-chat';
import type { ChatCitation, ChatResponse } from '@/modules/chat/domain/chat';
import { formatClock } from '@/modules/reports/domain/normalize';

const SUGGESTIONS = ['How many incidents involve a knife?', 'Which burglaries are high severity?', 'What happened most often?'];

let messageSequence = 0;

type ChatMessage = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  citations?: ChatCitation[];
  /** chat-contract-v2: the reports could not answer this question. */
  insufficientEvidence?: boolean;
  /** How the answer names each incident (type and title), so its citations are labelled without ids. */
  incidentTitles?: Record<string, string>;
};

const groupTitle = (titles: Record<string, string> | undefined, id: string) => {
  const title = titles?.[id];
  return title ? title.charAt(0).toUpperCase() + title.slice(1) : 'An incident';
};

/**
 * "Ask the library": questions across every incident's report (its official version, else its newest). Each
 * cited item links to its incident's report, opened at the cited second.
 */
export function LibraryChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const scrollerViewportRef = useRef<HTMLDivElement>(null);

  // biome-ignore lint/correctness/useExhaustiveDependencies: scroll to bottom whenever chat state updates
  useEffect(() => {
    scrollerViewportRef.current?.scrollTo({ top: scrollerViewportRef.current.scrollHeight });
  }, [messages, busy, failed]);

  async function send(text: string, history: ChatMessage[]) {
    messageSequence += 1;
    const next: ChatMessage[] = [...history, { id: `library-msg-${messageSequence}`, role: 'user', content: text }];
    setMessages(next);
    setBusy(true);
    setFailed(null);
    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(libraryChatRequestBody({ messages: next.map(({ role, content }) => ({ role, content })), sessionId })),
      });
      const payload = (await response.json()) as Partial<ChatResponse> & { error?: string };
      if (!response.ok || !payload.message) throw new Error(payload.error || 'No answer returned');
      setSessionId(payload.sessionId);
      messageSequence += 1;
      setMessages([
        ...next,
        { ...payload.message, id: `library-msg-${messageSequence}`, incidentTitles: payload.grounding?.incidentTitles },
      ]);
    } catch (cause) {
      setFailed(cause instanceof Error ? cause.message : 'Chat failed');
    } finally {
      setBusy(false);
    }
  }

  function ask(event: FormEvent) {
    event.preventDefault();
    const text = question.trim();
    if (!text || busy) return;
    setQuestion('');
    void send(text, messages);
  }

  function retry() {
    const last = messages[messages.length - 1];
    if (last?.role === 'user') void send(last.content, messages.slice(0, -1));
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="library-chat">
      <div className="flex justify-end px-4 pb-2">
        <Button
          disabled={!messages.length || busy}
          onClick={() => {
            setMessages([]);
            setSessionId(undefined);
            setFailed(null);
          }}
          size="sm"
          type="button"
          variant="outline"
        >
          New chat
        </Button>
      </div>
      <MessageScrollerProvider>
        <MessageScroller className="flex min-h-0 flex-1 flex-col">
          <MessageScrollerViewport className="flex-1 overflow-auto px-4 py-3" ref={scrollerViewportRef}>
            <MessageScrollerContent className="flex flex-col gap-2.5">
              {!messages.length && (
                <>
                  <p className="max-w-[92%] self-start rounded-2xl rounded-bl-sm bg-muted px-3 py-2 text-[13px] leading-snug text-muted-foreground">
                    Ask across every incident report. Answers count and cite reports (each incident&apos;s official version, else its
                    newest), and each citation opens that report at the moment it cites.
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {SUGGESTIONS.map((suggestion) => (
                      <Button
                        className="h-auto px-2.5 py-1 text-left text-xs font-semibold"
                        key={suggestion}
                        onClick={() => void send(suggestion, [])}
                        size="xs"
                        type="button"
                        variant="outline"
                      >
                        {suggestion}
                      </Button>
                    ))}
                  </div>
                </>
              )}
              {messages.map((message) =>
                message.role === 'user' ? (
                  <Message align="end" className="self-end" key={message.id}>
                    <Bubble>
                      <BubbleContent className="rounded-2xl rounded-br-sm bg-primary px-3 py-2 text-[13px] leading-snug text-primary-foreground">
                        {message.content}
                      </BubbleContent>
                    </Bubble>
                  </Message>
                ) : (
                  <Message align="start" className="self-start" key={message.id}>
                    <Bubble>
                      <BubbleContent className="rounded-2xl rounded-bl-sm bg-muted px-3 py-2 text-[13px] leading-snug text-foreground">
                        <p className="whitespace-pre-wrap">{message.content}</p>
                        {!!message.citations?.length && (
                          <ul className="mt-2 flex flex-col gap-2" data-testid="library-chat-citations">
                            {citationsByIncident(message.citations).map((group) => (
                              <li key={group.incidentId}>
                                <b className="block truncate text-xs" data-testid="library-chat-incident">
                                  {groupTitle(message.incidentTitles, group.incidentId)}
                                </b>
                                <ul className="mt-1 flex flex-col gap-1">
                                  {group.citations.map((citation) => (
                                    <li key={`${citation.itemId}-${citation.timestampSeconds}`}>
                                      <Link
                                        aria-label={`Open ${groupTitle(message.incidentTitles, group.incidentId)} at ${formatClock(citation.timestampSeconds)}`}
                                        className="grid w-full grid-cols-[52px_minmax(0,1fr)] items-center gap-2 rounded-xl border border-border bg-card p-1 text-left transition hover:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                        href={citationHref(citation)}
                                      >
                                        {/* eslint-disable-next-line @next/next/no-img-element -- the agent's signed crop URL, used as given */}
                                        {/* biome-ignore lint/performance/noImgElement: the agent's signed crop URL, used as given */}
                                        <img alt="" className="h-9 w-[52px] rounded-md bg-muted object-cover" src={citation.cropImageUrl} />
                                        <span className="min-w-0">
                                          <b className="block truncate text-xs">
                                            {citation.itemId} · {citation.itemType}
                                          </b>
                                          <span className="text-[11px] text-muted-foreground">
                                            open at {formatClock(citation.timestampSeconds)}
                                          </span>
                                        </span>
                                      </Link>
                                    </li>
                                  ))}
                                </ul>
                              </li>
                            ))}
                          </ul>
                        )}
                        {message.insufficientEvidence && (
                          <Badge className="mt-2" data-testid="library-chat-insufficient" variant="secondary">
                            Not in the reports
                          </Badge>
                        )}
                      </BubbleContent>
                    </Bubble>
                  </Message>
                ),
              )}
              {busy && (
                <Marker className="self-start rounded-2xl bg-muted px-3 py-2 text-xs text-muted-foreground">
                  <MarkerContent aria-label="Searching the reports" role="status">
                    Searching the reports…
                  </MarkerContent>
                </Marker>
              )}
              {failed && (
                <Alert
                  className="self-start rounded-2xl border-destructive/30 bg-destructive/10 px-3 py-2 text-[13px] text-destructive"
                  data-testid="library-chat-error"
                  variant="destructive"
                >
                  <AlertDescription>
                    Chat is unavailable right now, so no answer was returned. <span className="text-[11px] opacity-70">({failed})</span>{' '}
                    <button
                      className="font-semibold underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-hidden"
                      onClick={retry}
                      type="button"
                    >
                      Try again
                    </button>
                  </AlertDescription>
                </Alert>
              )}
            </MessageScrollerContent>
          </MessageScrollerViewport>
        </MessageScroller>
      </MessageScrollerProvider>
      <form className="flex gap-1.5 border-t border-border px-4 py-2.5" onSubmit={ask}>
        <Input
          aria-label="Library question"
          className="h-8 min-w-0 flex-1 text-xs"
          disabled={busy}
          maxLength={500}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Ask about all incidents…"
          value={question}
        />
        <Button disabled={busy || !question.trim()} size="sm" type="submit">
          Send
        </Button>
      </form>
    </div>
  );
}

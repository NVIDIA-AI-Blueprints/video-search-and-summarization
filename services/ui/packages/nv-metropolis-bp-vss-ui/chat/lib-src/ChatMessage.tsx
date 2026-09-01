// SPDX-License-Identifier: MIT
import {
  IconCheck,
  IconCopy,
  IconEdit,
  IconPlayerPause,
  IconTrash,
  IconUser,
  IconVolume2,
} from '@tabler/icons-react';
import DOMPurify from 'isomorphic-dompurify';
import React, { memo, useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeRaw from 'rehype-raw';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';

import { ChatSteps } from './ChatSteps';
import { getMarkdownComponents } from './markdown/components';
import { fixMalformedHtml } from './markdown/streaming';
import type { ChatFeatureFlags, ChatMessage as ChatMessageType } from './types';

export interface ChatMessageProps {
  message: ChatMessageType;
  features: ChatFeatureFlags;
  /**
   * Identified by message, not by position.
   *
   * The rendered list has hidden messages filtered out, so a rendered index
   * does not address the same element in the conversation — editing the second
   * visible message after an upload auto-prompt would rewrite the wrong turn.
   */
  onEdit?: (message: ChatMessageType) => void;
  onDelete?: (messageId: string) => void;
  onNotify?: (message: string) => void;
}

const BotAvatar: React.FC = () => (
  <span
    aria-hidden
    className="flex h-[30px] w-[30px] items-center justify-center rounded-full bg-[#76b900] text-xs font-bold text-black"
  >
    VSS
  </span>
);

export const ChatMessageView: React.FC<ChatMessageProps> = memo(
  ({ message, features, onEdit, onDelete, onNotify }) => {
    const [isEditing, setIsEditing] = useState(false);
    const [isTyping, setIsTyping] = useState(false);
    const [draft, setDraft] = useState(message.content);
    const [copied, setCopied] = useState(false);
    const [isPlaying, setIsPlaying] = useState(false);
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    const isAssistant = message.role === 'assistant';
    const isStreaming = !!message.streaming;

    // `messageIsStreaming` is deliberately outside the dep list: including it
    // rebuilds the whole component map the moment a stream ends, unmounting
    // every image and code block in the answer at once.
    const markdownComponents = useMemo(
      () => getMarkdownComponents({ messageIsStreaming: isStreaming, onDownloadError: onNotify }),
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [message.id, onNotify],
    );

    // callerInfo is HTML supplied by the embedding app, not by the model, but
    // it still goes through DOMPurify — the app builds it from search results
    // that originate upstream.
    const safeCallerInfo = useMemo(
      () => DOMPurify.sanitize(message.callerInfo || ''),
      [message.callerInfo],
    );

    const content = useMemo(
      () => (isAssistant ? fixMalformedHtml(message.content).trim() : message.content.trim()),
      [isAssistant, message.content],
    );

    useEffect(() => setDraft(message.content), [message.content]);

    useEffect(() => {
      if (textareaRef.current) {
        textareaRef.current.style.height = 'inherit';
        textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
      }
    }, [isEditing, draft]);

    useEffect(
      () => () => {
        if (typeof window !== 'undefined' && window.speechSynthesis) window.speechSynthesis.cancel();
      },
      [],
    );

    // Hidden messages (upload auto-prompts) are sent but never shown, and an
    // assistant turn with neither text nor steps is just noise.
    if (message.hidden) return null;
    if (isAssistant && !content && !message.steps?.length && !message.error && !isStreaming) {
      return null;
    }

    const handleCopy = () => {
      if (!navigator.clipboard) return;
      void navigator.clipboard.writeText(message.content).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      });
    };

    const handleSpeak = () => {
      if (typeof window === 'undefined' || !('speechSynthesis' in window)) {
        onNotify?.('Text-to-speech is not supported in this browser');
        return;
      }
      if (isPlaying) {
        window.speechSynthesis.cancel();
        setIsPlaying(false);
        return;
      }
      // URLs read aloud character by character are unbearable; drop them.
      const utterance = new SpeechSynthesisUtterance(
        message.content.replace(/(https?:\/\/[^\s]+)/g, ''),
      );
      utterance.onend = () => setIsPlaying(false);
      utterance.onerror = () => setIsPlaying(false);
      setIsPlaying(true);
      window.speechSynthesis.speak(utterance);
    };

    const handleSaveEdit = () => {
      // Everything from this message on is replaced by the new turn.
      if (draft !== message.content) onEdit?.({ ...message, content: draft });
      setIsEditing(false);
    };

    return (
      <div
        data-testid={isAssistant ? 'chat-message-assistant' : 'chat-message-user'}
        className={`group border-b border-black/10 text-gray-800 dark:border-gray-900/50 dark:text-gray-100 md:px-4 ${
          isAssistant ? 'bg-gray-50 dark:bg-black' : 'bg-white dark:bg-black'
        }`}
        style={{ overflowWrap: 'anywhere' }}
      >
        <div className="relative m-auto flex w-full max-w-[95%] text-base sm:p-2 md:gap-6 md:py-6 lg:px-0">
          <div className="min-w-[40px] text-right font-bold">
            {isAssistant ? <BotAvatar /> : <IconUser size={30} />}
          </div>

          <div className="w-full min-w-0 overflow-hidden">
            {!isAssistant ? (
              <div className="flex w-full">
                {isEditing ? (
                  <div className="flex w-full flex-col">
                    <textarea
                      ref={textareaRef}
                      className="w-full resize-none whitespace-pre-wrap border-none bg-transparent outline-none dark:bg-black"
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      onCompositionStart={() => setIsTyping(true)}
                      onCompositionEnd={() => setIsTyping(false)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && !isTyping && !e.shiftKey) {
                          e.preventDefault();
                          handleSaveEdit();
                        }
                      }}
                      style={{ font: 'inherit', padding: 0, margin: 0, overflow: 'hidden' }}
                    />
                    <div className="mt-6 flex justify-center space-x-4">
                      <button
                        type="button"
                        className="h-[40px] rounded-md border border-neutral-300 px-4 py-1 text-sm font-medium text-neutral-700 enabled:hover:bg-[#76b900] enabled:hover:text-white disabled:opacity-50 dark:border-neutral-700 dark:text-neutral-300"
                        onClick={handleSaveEdit}
                        disabled={!draft.trim()}
                      >
                        Save &amp; Submit
                      </button>
                      <button
                        type="button"
                        className="h-[40px] rounded-md border border-neutral-300 px-4 py-1 text-sm font-medium text-neutral-700 hover:bg-neutral-100 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800"
                        onClick={() => {
                          setDraft(message.content);
                          setIsEditing(false);
                        }}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="w-full flex-1 whitespace-pre-wrap break-words">{content}</div>
                )}

                {!isEditing && (
                  <div className="absolute right-2 flex flex-col items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 md:flex-row md:items-start">
                    {features.messageEdit && (
                      <button
                        type="button"
                        aria-label="Edit message"
                        className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300"
                        onClick={() => setIsEditing(true)}
                      >
                        <IconEdit size={20} />
                      </button>
                    )}
                    <button
                      type="button"
                      aria-label="Delete message"
                      className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300"
                      onClick={() => onDelete?.(message.id)}
                    >
                      <IconTrash size={20} />
                    </button>
                  </div>
                )}
              </div>
            ) : (
              <div className="w-full min-w-0 max-w-full">
                {features.intermediateSteps && message.steps?.length ? (
                  <ChatSteps
                    steps={message.steps}
                    streaming={isStreaming}
                    expandByDefault={features.expandIntermediateSteps}
                  />
                ) : null}

                {message.error ? (
                  <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
                    ⚠ {message.error}
                  </div>
                ) : null}

                <div className="prose max-w-none break-words dark:prose-invert">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm, [remarkMath, { singleDollarTextMath: false }]]}
                    rehypePlugins={[rehypeRaw] as any}
                    components={markdownComponents as any}
                  >
                    {content}
                  </ReactMarkdown>
                  {isStreaming && !content ? (
                    <span className="inline-block animate-pulse">▍</span>
                  ) : null}
                </div>

                {message.callerInfo ? (
                  <div className="mt-2 rounded-md border border-black/10 bg-neutral-100 px-4 py-2.5 text-sm text-neutral-800 dark:border-white/10 dark:bg-transparent dark:text-neutral-200">
                    <div
                      className="[&_ul]:mt-2 [&_ul]:list-disc [&_ul]:space-y-0.5 [&_ul]:pl-5"
                      dangerouslySetInnerHTML={{ __html: safeCallerInfo }}
                    />
                  </div>
                ) : null}

                {!isStreaming && (features.messageCopy || features.messageSpeaker) ? (
                  <div className="mt-1 flex gap-1">
                    {features.messageCopy &&
                      (copied ? (
                        <IconCheck size={20} className="text-[#76b900]" />
                      ) : (
                        <button
                          type="button"
                          className="text-[#76b900] hover:text-gray-700 dark:hover:text-gray-300"
                          onClick={handleCopy}
                          title="Copy to clipboard"
                          aria-label="Copy to clipboard"
                        >
                          <IconCopy size={20} />
                        </button>
                      ))}
                    {features.messageSpeaker && (
                      <button
                        type="button"
                        className="text-[#76b900] hover:text-gray-700 dark:hover:text-gray-300"
                        onClick={handleSpeak}
                        aria-label={isPlaying ? 'Stop speaking' : 'Start speaking'}
                      >
                        {isPlaying ? (
                          <IconPlayerPause size={20} className="animate-pulse text-red-400" />
                        ) : (
                          <IconVolume2 size={20} />
                        )}
                      </button>
                    )}
                  </div>
                ) : null}
              </div>
            )}
          </div>
        </div>
      </div>
    );
  },
  // Re-render only when something visible changed. Without this every message
  // in a long thread re-renders on each token of the newest answer.
  (prev, next) => prev.message === next.message && prev.features === next.features,
);
ChatMessageView.displayName = 'ChatMessageView';

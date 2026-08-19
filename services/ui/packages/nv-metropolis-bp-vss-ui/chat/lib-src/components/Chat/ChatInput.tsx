// SPDX-License-Identifier: MIT
/**
 * Message composer.
 *
 * Owns everything below the transcript: the textarea, send and stop, video
 * upload, and the agent parameter panel. Controls that would change or race an
 * in-flight request are disabled while one is running rather than hidden, so
 * the composer does not reflow mid-answer.
 */
import React, { useCallback, useContext, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'next-i18next';

import ChatContext from '../../state/ChatContext';
import { isQueryProcessing } from '../../utils/queryProcessing';
import {
  CustomAgentParams,
  fieldsToParams,
  useInitialParamFields,
  type CustomAgentParamsValues,
} from './CustomAgentParams';
import { ChatFileUpload } from './ChatFileUpload';

export interface ChatInputProps {
  textareaRef: React.RefObject<HTMLTextAreaElement>;
  onSend: (message: string, params?: CustomAgentParamsValues) => void;
  onRegenerate: () => void;
  onScrollDownClick: () => void;
  showScrollDownButton: boolean;
  controller: React.MutableRefObject<AbortController | null>;
  onStopConversation: () => void;
  /** Set while an upload flow owns the composer. */
  chatBlocked?: boolean;
  /** Reports this instance's upload flow to a coordinator. */
  onUploadFlowActiveChange?: (sourceId: string, active: boolean) => void;
  onSendHiddenMessage?: (message: string, uploadConversationId: string) => void;
}

const UPLOAD_FLOW_SOURCE_ID = 'chat-input';

export const ChatInput: React.FC<ChatInputProps> = ({
  textareaRef,
  onSend,
  onScrollDownClick,
  showScrollDownButton,
  onStopConversation,
  chatBlocked = false,
  onUploadFlowActiveChange,
  onSendHiddenMessage,
}) => {
  const { t } = useTranslation('chat');
  const {
    state: {
      selectedConversation,
      messageIsStreaming,
      loading,
      customAgentParamsJson,
      chatUploadFileEnabled,
      chatInputMicEnabled,
    },
  } = useContext(ChatContext);

  const [content, setContent] = useState('');
  const [showParams, setShowParams] = useState(false);
  const paramsAnchorRef = useRef<HTMLButtonElement>(null);

  const [paramFields, setParamFields] = useInitialParamFields(customAgentParamsJson);

  const queryInFlight = isQueryProcessing(Boolean(loading), Boolean(messageIsStreaming));
  // A running query blocks anything that would alter the request; an active
  // upload flow blocks the composer entirely.
  const controlsDisabled = queryInFlight || chatBlocked;

  // The panel edits parameters for the *next* request, so leaving it open once
  // a query starts would imply it affects the answer being streamed.
  useEffect(() => {
    if (queryInFlight) setShowParams(false);
  }, [queryInFlight]);

  const handleSend = useCallback(() => {
    const trimmed = content.trim();
    if (!trimmed || controlsDisabled) return;

    onSend(trimmed, fieldsToParams(paramFields));
    setContent('');
  }, [content, controlsDisabled, onSend, paramFields]);

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
      // Enter sends; Shift+Enter newlines. Composition guards IMEs, where
      // Enter commits a candidate rather than ending the message.
      if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
        event.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  const getActiveConversationId = useCallback(
    () => selectedConversation?.id,
    [selectedConversation],
  );

  return (
    <div className="relative w-full">
      <ChatFileUpload
        uploadFlowSourceId={UPLOAD_FLOW_SOURCE_ID}
        onUploadFlowActiveChange={onUploadFlowActiveChange}
        getActiveConversationId={getActiveConversationId}
        onSendHiddenMessage={onSendHiddenMessage}
        disabled={controlsDisabled}
      >
        {({ triggerUpload, isDragging, dragHandlers }) => (
          <div
            {...dragHandlers}
            className={`relative flex w-full flex-col rounded-md border bg-white dark:bg-neutral-900 ${
              isDragging ? 'border-[#76b900]' : 'border-gray-200 dark:border-gray-600'
            }`}
          >
            {/* Left control bar: upload and agent parameters. */}
            <div className="absolute left-2 bottom-2 flex items-center gap-1">
              {chatUploadFileEnabled && (
                <button
                  type="button"
                  title="Upload video"
                  aria-label="Upload video"
                  disabled={controlsDisabled}
                  onClick={triggerUpload}
                  className="rounded p-1 disabled:opacity-50"
                >
                  <span aria-hidden="true">+</span>
                </button>
              )}

              {paramFields.length > 0 && (
                <button
                  ref={paramsAnchorRef}
                  type="button"
                  title="Agent Parameters"
                  disabled={controlsDisabled}
                  onClick={() => setShowParams((open) => !open)}
                  className="rounded p-1 disabled:opacity-50"
                >
                  <span aria-hidden="true">&#9881;</span>
                </button>
              )}

              {chatInputMicEnabled && (
                <button
                  type="button"
                  title="Voice input"
                  aria-label="Voice input"
                  disabled={controlsDisabled}
                  className="rounded p-1 disabled:opacity-50"
                >
                  <span aria-hidden="true">&#127908;</span>
                </button>
              )}
            </div>

            <textarea
              ref={textareaRef}
              value={content}
              rows={1}
              placeholder={t('Type a message') ?? 'Type a message'}
              onChange={(event) => setContent(event.target.value)}
              onKeyDown={handleKeyDown}
              className="max-h-60 w-full resize-none bg-transparent py-3 pl-20 pr-12 text-sm outline-none"
            />

            <div className="absolute right-2 bottom-2 flex items-center gap-1">
              {messageIsStreaming ? (
                <button
                  type="button"
                  title="Stop generating"
                  aria-label="Stop generating"
                  onClick={onStopConversation}
                  className="rounded p-1"
                >
                  <span aria-hidden="true">&#9632;</span>
                </button>
              ) : (
                <button
                  type="button"
                  title="Send message"
                  aria-label="Send message"
                  disabled={controlsDisabled || content.trim().length === 0}
                  onClick={handleSend}
                  className="rounded p-1 disabled:opacity-50"
                >
                  <span aria-hidden="true">&#10148;</span>
                </button>
              )}
            </div>
          </div>
        )}
      </ChatFileUpload>

      {showScrollDownButton && (
        <button
          type="button"
          title="Scroll to bottom"
          aria-label="Scroll to bottom"
          onClick={onScrollDownClick}
          className="absolute -top-10 right-2 rounded-full border p-2"
        >
          <span aria-hidden="true">&#8595;</span>
        </button>
      )}

      <CustomAgentParams
        isOpen={showParams}
        onClose={() => setShowParams(false)}
        fields={paramFields}
        onFieldsChange={setParamFields}
        anchorRef={paramsAnchorRef}
        valuesChangeDisabled={queryInFlight}
      />
    </div>
  );
};

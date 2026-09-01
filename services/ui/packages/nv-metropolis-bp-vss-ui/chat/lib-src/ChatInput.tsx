// SPDX-License-Identifier: MIT
import {
  IconArrowDown,
  IconBrain,
  IconFile,
  IconMicrophone,
  IconPaperclip,
  IconPhoto,
  IconPlayerStop,
  IconPlayerStopFilled,
  IconRepeat,
  IconSend,
  IconUpload,
  IconVideo,
  IconX,
} from '@tabler/icons-react';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { AgentParams, fieldsToParams, useParamFields } from './AgentParams';
import { ChatUpload } from './ChatUpload';
import type {
  ChatFeatureFlags,
  ChatVideoUploadCompletePayload,
  CustomAgentParamsValues,
  QueryDataContext,
} from './types';

const CHIP_ICON_SIZE = 12;

/** Leading icon for a context chip, driven by the UI-only `contextType`. */
const ChipIcon: React.FC<{ contextType: string }> = ({ contextType }) => {
  const className = 'flex-shrink-0 opacity-90';
  switch (contextType) {
    case 'media/video':
      return <IconVideo size={CHIP_ICON_SIZE} className={className} aria-hidden />;
    case 'media/image':
      return <IconPhoto size={CHIP_ICON_SIZE} className={className} aria-hidden />;
    case 'network-file':
      return <IconFile size={CHIP_ICON_SIZE} className={className} aria-hidden />;
    default:
      return <IconPaperclip size={CHIP_ICON_SIZE} className={className} aria-hidden />;
  }
};

/** SpeechRecognition is still vendor-prefixed in Chromium. */
function getSpeechRecognition(): any {
  if (typeof window === 'undefined') return null;
  return (window as any).SpeechRecognition ?? (window as any).webkitSpeechRecognition ?? null;
}

function isMobile(): boolean {
  if (typeof navigator === 'undefined') return false;
  return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini|Mobile|mobile|CriOS/i.test(
    navigator.userAgent,
  );
}

export interface ChatInputProps {
  onSend: (text: string, params: CustomAgentParamsValues) => void;
  onRegenerate: () => void;
  onStop: () => void;
  onScrollDown: () => void;
  showScrollDownButton: boolean;
  busy: boolean;
  /** True once at least one exchange exists, so Regenerate makes sense. */
  canRegenerate: boolean;
  workflowName: string;
  features: ChatFeatureFlags;
  customAgentParamsJson?: string;
  contextItems: QueryDataContext[];
  onRemoveContext: (id: string) => void;
  /** Upload wiring; upload is hidden entirely when `uploadUrlBase` is absent. */
  uploadUrlBase?: string;
  uploadConfigTemplateJson?: string;
  uploadHiddenMessageTemplate?: string;
  getActiveConversationId?: () => string | undefined;
  onSendHiddenMessage?: (message: string, uploadConversationId: string) => void;
  onChatVideoUploadComplete?: (payload: ChatVideoUploadCompletePayload) => void;
  /** True while an upload dialog is open; sending is blocked meanwhile. */
  onUploadFlowActiveChange?: (active: boolean) => void;
  chatBlocked?: boolean;
  onNotify?: (message: string) => void;
}

export const ChatInput: React.FC<ChatInputProps> = ({
  onSend,
  onRegenerate,
  onStop,
  onScrollDown,
  showScrollDownButton,
  busy,
  canRegenerate,
  workflowName,
  features,
  customAgentParamsJson,
  contextItems,
  onRemoveContext,
  uploadUrlBase,
  uploadConfigTemplateJson,
  uploadHiddenMessageTemplate,
  getActiveConversationId,
  onSendHiddenMessage,
  onChatVideoUploadComplete,
  onUploadFlowActiveChange,
  chatBlocked = false,
  onNotify,
}) => {
  const [content, setContent] = useState('');
  const [isComposing, setIsComposing] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [showParams, setShowParams] = useState(false);
  const [paramFields, setParamFields] = useParamFields(customAgentParamsJson);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const recognitionRef = useRef<any>(null);
  const paramsButtonRef = useRef<HTMLButtonElement>(null);

  const uploadEnabled = !!features.uploadFile && !!uploadUrlBase;
  const micEnabled = !!features.inputMic;
  const disabled = chatBlocked || busy;

  // Freeze the parameter panel mid-request: changing a value after the body
  // was serialised is a silent no-op that looks like it applied.
  useEffect(() => {
    if (disabled) setShowParams(false);
  }, [disabled]);

  // Grow with content up to a ceiling, then scroll — matching the toolkit.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'inherit';
    el.style.height = `${el.scrollHeight}px`;
    el.style.overflow = el.scrollHeight > 400 ? 'auto' : 'hidden';
  }, [content]);

  useEffect(
    () => () => {
      recognitionRef.current?.stop();
    },
    [],
  );

  const handleSend = useCallback(() => {
    if (disabled) return;
    if (isRecording) {
      recognitionRef.current?.stop();
      setIsRecording(false);
    }
    // Chips alone are a valid message: "summarise this" with the clip attached.
    if (!content.trim() && contextItems.length === 0) {
      onNotify?.('Please enter a message');
      return;
    }
    onSend(content, fieldsToParams(paramFields));
    setContent('');
    if (typeof window !== 'undefined' && window.innerWidth < 640) textareaRef.current?.blur();
  }, [content, contextItems.length, disabled, isRecording, onNotify, onSend, paramFields]);

  const handleSpeechToText = useCallback(() => {
    const SpeechRecognition = getSpeechRecognition();
    if (!SpeechRecognition) {
      onNotify?.('Speech recognition is not supported in this browser');
      return;
    }
    if (!recognitionRef.current) {
      const recognition = new SpeechRecognition();
      recognition.lang = 'en-US';
      recognition.interimResults = true;
      recognition.continuous = true;
      recognition.onresult = (event: any) => {
        let transcript = '';
        for (let i = 0; i < event.results.length; i += 1) transcript += event.results[i][0].transcript;
        setContent(transcript);
      };
      recognition.onerror = () => setIsRecording(false);
      recognitionRef.current = recognition;
    }
    if (isRecording) {
      recognitionRef.current.stop();
      setIsRecording(false);
    } else {
      recognitionRef.current.start();
      setIsRecording(true);
    }
  }, [isRecording, onNotify]);

  const leftButtonCount = (micEnabled ? 1 : 0) + (uploadEnabled ? 1 : 0);
  const leftPaddingClass =
    leftButtonCount === 0 ? 'pl-3 sm:pl-4' : leftButtonCount === 2 ? 'pl-[76px]' : 'pl-11';
  const rightPaddingClass = paramFields.length > 0 ? 'pr-20' : 'pr-12';

  const uploadButton = useMemo(
    () =>
      uploadEnabled ? (
        <ChatUpload
          agentApiUrlBase={uploadUrlBase}
          configTemplateJson={uploadConfigTemplateJson}
          metadataEnabled={features.uploadFileMetadata}
          hiddenMessageTemplate={uploadHiddenMessageTemplate}
          disabled={disabled}
          getActiveConversationId={getActiveConversationId}
          onSendHiddenMessage={onSendHiddenMessage}
          onUploadBatchComplete={onChatVideoUploadComplete}
          onUploadFlowActiveChange={onUploadFlowActiveChange}
          onNotify={onNotify}
        >
          {({ triggerUpload }) => (
            <button
              type="button"
              onClick={triggerUpload}
              disabled={disabled}
              title="Upload video"
              aria-label="Upload video"
              className={`rounded-sm p-[5px] text-neutral-800 opacity-60 dark:text-neutral-100 ${
                disabled ? 'text-neutral-400' : 'hover:text-[#76b900]'
              }`}
            >
              <IconUpload size={18} />
            </button>
          )}
        </ChatUpload>
      ) : null,
    [
      uploadEnabled,
      uploadUrlBase,
      uploadConfigTemplateJson,
      uploadHiddenMessageTemplate,
      features.uploadFileMetadata,
      disabled,
      getActiveConversationId,
      onSendHiddenMessage,
      onChatVideoUploadComplete,
      onUploadFlowActiveChange,
      onNotify,
    ],
  );

  return (
    <div className="pointer-events-none absolute bottom-0 left-0 w-full border-transparent bg-gradient-to-b from-transparent via-white to-white pb-4 pt-6 dark:via-black dark:to-black">
      <div className="stretch pointer-events-auto mx-auto mt-4 flex w-full max-w-[95%] flex-row gap-3 last:mb-2 md:mt-[52px]">
        {busy && !chatBlocked && (
          <button
            type="button"
            className="absolute left-0 right-0 top-0 mx-auto mb-3 flex w-fit items-center gap-3 rounded border border-neutral-200 bg-white px-4 py-2 text-black hover:opacity-50 dark:border-neutral-600 dark:bg-black dark:text-white md:mb-0 md:mt-2"
            onClick={onStop}
          >
            <IconPlayerStop size={16} /> Stop Generating
          </button>
        )}

        {!busy && !chatBlocked && canRegenerate && (
          <button
            type="button"
            className="absolute left-0 right-0 top-0 mx-auto mb-3 flex w-fit items-center gap-3 rounded border border-neutral-200 bg-white px-4 py-2 text-black hover:opacity-50 dark:border-neutral-600 dark:bg-black dark:text-white md:mb-0 md:mt-2"
            onClick={onRegenerate}
          >
            <IconRepeat size={16} /> Regenerate response
          </button>
        )}

        <div className="relative mx-2 flex w-full flex-grow flex-col rounded-md border border-black/10 bg-white shadow-[0_0_10px_rgba(0,0,0,0.10)] dark:border-neutral-700 dark:bg-black dark:text-white sm:mx-4">
          {!content && !isRecording && contextItems.length === 0 && (
            <div
              data-testid="chat-input-placeholder"
              className={`pointer-events-none absolute inset-0 flex items-center py-2 text-gray-500 dark:text-gray-400 md:py-3 ${leftPaddingClass} ${rightPaddingClass}`}
              aria-hidden
            >
              <span className="min-w-0 truncate">
                Unlock {workflowName} knowledge and expertise
              </span>
            </div>
          )}

          {contextItems.length > 0 && (
            <div
              className={`flex flex-wrap gap-1.5 pr-12 pt-2 ${
                uploadEnabled ? 'pl-12 sm:pl-18 md:pl-20' : 'pl-10 sm:pl-12 md:pl-14'
              }`}
            >
              {contextItems.map((item) => (
                <span
                  key={item.id}
                  className="inline-flex max-w-[200px] items-center gap-1 rounded-md bg-gray-100 py-1 pl-1.5 pr-1 text-xs text-gray-700 dark:bg-gray-600 dark:text-gray-200"
                  title={`${item.label} (${item.contextType})`}
                >
                  <ChipIcon contextType={item.contextType} />
                  <span className="truncate">{item.label}</span>
                  <button
                    type="button"
                    onClick={() => onRemoveContext(item.id)}
                    className="flex-shrink-0 rounded p-0.5 hover:bg-gray-300 dark:hover:bg-gray-500"
                    aria-label={`Remove ${item.label}`}
                  >
                    <IconX size={12} />
                  </button>
                </span>
              ))}
            </div>
          )}

          <textarea
            data-testid="chat-textarea"
            ref={textareaRef}
            rows={1}
            className={`m-0 w-full resize-none border-0 bg-transparent p-0 py-2 text-black outline-none dark:bg-transparent dark:text-white md:py-3 ${leftPaddingClass} ${rightPaddingClass}`}
            style={{ minHeight: '44px', maxHeight: '400px' }}
            placeholder={isRecording ? 'Listening…' : ''}
            aria-label={
              isRecording ? 'Listening' : `Unlock ${workflowName} knowledge and expertise`
            }
            value={content}
            disabled={chatBlocked}
            readOnly={chatBlocked}
            onChange={(e) => setContent(e.target.value)}
            onCompositionStart={() => setIsComposing(true)}
            onCompositionEnd={() => setIsComposing(false)}
            onKeyDown={(e) => {
              if (chatBlocked) return;
              // Never submit mid-IME composition: the Enter that commits a
              // Japanese candidate would send a half-typed message.
              if (e.key === 'Enter' && !isComposing && !isMobile() && !e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
          />

          {(micEnabled || uploadEnabled) && (
            <div className="absolute left-2 top-2 flex gap-1">
              {micEnabled && (
                <button
                  type="button"
                  onClick={handleSpeechToText}
                  disabled={disabled}
                  aria-label={isRecording ? 'Stop recording' : 'Start recording'}
                  className={`rounded-sm p-[5px] text-neutral-800 opacity-60 dark:text-neutral-100 ${
                    disabled ? 'text-neutral-400' : 'hover:text-[#76b900]'
                  }`}
                >
                  {isRecording ? (
                    <IconPlayerStopFilled size={18} className="animate-pulse text-red-500" />
                  ) : (
                    <IconMicrophone size={18} />
                  )}
                </button>
              )}
              {uploadButton}
            </div>
          )}

          {paramFields.length > 0 && (
            <div className="absolute right-10 top-2">
              <button
                ref={paramsButtonRef}
                type="button"
                title="Agent parameters"
                aria-label="Agent parameters"
                disabled={disabled}
                onClick={() => setShowParams((prev) => !prev)}
                className={`rounded-sm p-1 text-neutral-800 opacity-60 disabled:cursor-not-allowed disabled:opacity-40 dark:text-neutral-100 ${
                  showParams ? 'text-[#76b900]' : ''
                } ${disabled ? 'text-neutral-400' : 'hover:text-[#76b900]'}`}
              >
                <IconBrain size={18} />
              </button>
              <AgentParams
                isOpen={showParams}
                onClose={() => setShowParams(false)}
                fields={paramFields}
                onFieldsChange={setParamFields}
                anchorRef={paramsButtonRef}
                valuesChangeDisabled={disabled}
              />
            </div>
          )}

          <button
            type="button"
            className="absolute right-2 top-2 rounded-sm p-1 text-neutral-800 opacity-60 hover:bg-neutral-200 hover:text-neutral-900 disabled:cursor-not-allowed disabled:opacity-40 dark:text-neutral-100"
            onClick={handleSend}
            disabled={disabled}
            aria-label="Send message"
          >
            {busy ? (
              <div
                data-testid="chat-loading-spinner"
                className="h-4 w-4 animate-spin rounded-full border-t-2 border-neutral-800 opacity-60 dark:border-neutral-100"
              />
            ) : (
              <IconSend size={18} />
            )}
          </button>

          {showScrollDownButton && (
            <div className="absolute bottom-12 right-0 lg:-right-10 lg:bottom-2">
              <button
                type="button"
                aria-label="Scroll to latest"
                className="flex h-7 w-7 items-center justify-center rounded-full bg-neutral-300 text-gray-800 shadow-md hover:shadow-lg dark:bg-gray-900 dark:text-neutral-200"
                onClick={onScrollDown}
              >
                <IconArrowDown size={18} />
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

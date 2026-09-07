// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT AND Apache-2.0
import {
  IconChevronLeft,
  IconChevronRight,
  IconMoonFilled,
  IconPlus,
  IconSun,
  IconUpload,
} from '@tabler/icons-react';
import React, { useState } from 'react';

import { ChatUpload } from './ChatUpload';
import type { ChatFeatureFlags, ChatVideoUploadCompletePayload } from './types';

export interface ChatHeaderProps {
  workflowName: string;
  hasMessages: boolean;
  features: ChatFeatureFlags;
  theme: 'light' | 'dark';
  onThemeChange?: (theme: 'light' | 'dark') => void;
  chatHistory: boolean;
  onChatHistoryChange: (value: boolean) => void;
  onNewConversation: () => void;
  busy: boolean;
  /** Upload wiring for the welcome drop zone. */
  uploadUrlBase?: string;
  uploadConfigTemplateJson?: string;
  uploadHiddenMessageTemplate?: string;
  getActiveConversationId?: () => string | undefined;
  onSendHiddenMessage?: (message: string, uploadConversationId: string) => void;
  onChatVideoUploadComplete?: (payload: ChatVideoUploadCompletePayload) => void;
  onUploadFlowActiveChange?: (active: boolean) => void;
  onNotify?: (message: string) => void;
}

const Toggle: React.FC<{
  label: string;
  checked: boolean;
  disabled?: boolean;
  onChange: () => void;
}> = ({ label, checked, disabled, onChange }) => (
  <label className="flex flex-shrink-0 cursor-pointer items-center gap-2 whitespace-nowrap">
    <span className="text-sm font-medium text-black dark:text-white">{label}</span>
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={onChange}
      className={`relative inline-flex h-5 w-10 items-center rounded-full transition-colors ${
        checked ? 'bg-black dark:bg-[#76b900]' : 'bg-gray-200'
      } ${disabled ? 'cursor-not-allowed opacity-40' : ''}`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
          checked ? 'translate-x-6' : 'translate-x-0'
        }`}
      />
    </button>
  </label>
);

export const ChatHeader: React.FC<ChatHeaderProps> = ({
  workflowName,
  hasMessages,
  features,
  theme,
  onThemeChange,
  chatHistory,
  onChatHistoryChange,
  onNewConversation,
  busy,
  uploadUrlBase,
  uploadConfigTemplateJson,
  uploadHiddenMessageTemplate,
  getActiveConversationId,
  onSendHiddenMessage,
  onChatVideoUploadComplete,
  onUploadFlowActiveChange,
  onNotify,
}) => {
  const [isExpanded, setIsExpanded] = useState(false);
  const uploadEnabled = !!features.uploadFile && !!uploadUrlBase;

  const body = (upload?: {
    fileInputId: string;
    isUploading: boolean;
    isDragging: boolean;
    dragHandlers: Record<string, (e: React.DragEvent) => void>;
  }) => (
    <div className={hasMessages ? 'relative' : 'relative min-h-full'}>
      <div
        className={`top-0 z-10 flex h-12 items-center justify-center px-4 py-2 text-sm text-black dark:border-none dark:bg-black dark:text-neutral-200 ${
          hasMessages ? 'border-b border-gray-200 bg-white' : 'bg-none'
        }`}
      >
        {hasMessages ? (
          <div className="absolute left-1/2 top-6 -translate-x-1/2 -translate-y-1/2">
            <span className="text-lg font-semibold text-black dark:text-white">{workflowName}</span>
          </div>
        ) : (
          // Welcome state: the panel is empty, so this is also the drop target.
          <div
            className="absolute left-1/2 top-1/2 mx-auto flex -translate-x-1/2 -translate-y-1/2 flex-col items-center px-3 pt-5 text-center sm:max-w-[600px] md:pt-12"
            {...(upload?.dragHandlers ?? {})}
          >
            <div className="mb-4 text-3xl font-semibold text-gray-800 dark:text-white">
              Hi, I&apos;m {workflowName}
            </div>
            <div className="mb-8 text-lg text-gray-600 dark:text-gray-400">
              How can I assist you today?
            </div>

            {uploadEnabled && upload && (
              <label
                htmlFor={upload.fileInputId}
                className={`block w-full max-w-md cursor-pointer rounded-xl border-2 border-dashed p-8 transition-all duration-300 ${
                  upload.isDragging
                    ? 'scale-105 border-[#76b900] bg-[#76b900]/10 shadow-lg'
                    : 'border-gray-300 hover:border-[#76b900] hover:bg-gray-50 dark:border-gray-600 dark:hover:bg-black/50'
                } ${upload.isUploading || busy ? 'pointer-events-none opacity-50' : ''}`}
              >
                <div className="flex flex-col items-center gap-4">
                  <div
                    className={`rounded-2xl p-4 transition-all duration-300 ${
                      upload.isDragging
                        ? 'bg-[#76b900]/20 text-[#76b900]'
                        : 'bg-[#76b900]/15 text-[#76b900]'
                    }`}
                  >
                    <IconUpload size={48} stroke={1.5} />
                  </div>
                  <div data-testid="upload-drop-zone-text" className="text-center">
                    <p
                      className={`mb-1 text-base font-medium transition-colors ${
                        upload.isDragging
                          ? 'text-[#76b900]'
                          : 'text-gray-700 dark:text-gray-300'
                      }`}
                    >
                      {upload.isDragging
                        ? 'Drop files here'
                        : 'Click or drop files here to upload'}
                    </p>
                  </div>
                  <p className="text-sm text-gray-500 dark:text-gray-400">Movie Files (mp4, mkv)</p>
                </div>
              </label>
            )}
          </div>
        )}

        {/* Opaque so the expanded menu covers the title rather than overlapping it. */}
        <div className="absolute right-0 top-0 z-20 flex h-12 items-center bg-white pl-6 transition-all duration-300 dark:bg-black">
          <button
            type="button"
            onClick={() => setIsExpanded((prev) => !prev)}
            aria-label={isExpanded ? 'Collapse chat menu' : 'Expand chat menu'}
            aria-expanded={isExpanded}
            className="flex p-1 text-black transition-colors dark:text-white"
          >
            {isExpanded ? <IconChevronRight size={20} /> : <IconChevronLeft size={20} />}
          </button>

          <div
            className={`flex gap-1 overflow-hidden transition-all duration-300 sm:gap-1 md:gap-4 ${
              isExpanded ? 'w-auto opacity-100' : 'w-0 opacity-0'
            }`}
          >
            <button
              type="button"
              onClick={onNewConversation}
              disabled={busy}
              title="New chat"
              aria-label="New chat"
              className="flex items-center gap-1 whitespace-nowrap text-sm font-medium text-black disabled:opacity-40 dark:text-white"
            >
              <IconPlus size={16} /> New chat
            </button>

            <Toggle
              label="Chat History"
              checked={chatHistory}
              disabled={busy}
              onChange={() => onChatHistoryChange(!chatHistory)}
            />

            {features.themeToggle && onThemeChange && (
              <button
                type="button"
                onClick={() => onThemeChange(theme === 'dark' ? 'light' : 'dark')}
                aria-label="Toggle theme"
                className="flex items-center rounded-full text-black transition-colors dark:text-white"
              >
                {theme === 'dark' ? (
                  <IconSun className="h-6 w-6 text-yellow-500" />
                ) : (
                  <IconMoonFilled className="h-6 w-6 text-gray-800" />
                )}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );

  if (!uploadEnabled) return body();

  return (
    <ChatUpload
      vstApiUrlBase={uploadUrlBase}
      configTemplateJson={uploadConfigTemplateJson}
      metadataEnabled={features.uploadFileMetadata}
      hiddenMessageTemplate={uploadHiddenMessageTemplate}
      disabled={busy}
      getActiveConversationId={getActiveConversationId}
      onSendHiddenMessage={onSendHiddenMessage}
      onUploadBatchComplete={onChatVideoUploadComplete}
      onUploadFlowActiveChange={onUploadFlowActiveChange}
      onNotify={onNotify}
    >
      {({ fileInputId, isUploading, isDragging, dragHandlers }) =>
        body({ fileInputId, isUploading, isDragging, dragHandlers })
      }
    </ChatUpload>
  );
};

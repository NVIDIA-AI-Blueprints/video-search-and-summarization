// SPDX-License-Identifier: MIT
/**
 * Video upload from within chat.
 *
 * Renders no chrome of its own — the composer and the header both need upload,
 * with different affordances — so it exposes state and handlers through a
 * render prop and owns only the flow: pick or drop, confirm, upload, report.
 */
import React, { useCallback, useContext, useId, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import {
  UploadFilesDialog,
  UploadProgressPopup,
  UploadSuccessPopup,
  uploadFileChunked,
  type ChatVideoUploadCompletePayload,
  type FileUploadResult,
  type UploadFileConfigTemplate,
  type UploadFilesDialogEntry,
  type UploadProgressFileItem,
  type UploadResultItem,
} from 'common';

import ChatContext from '../../state/ChatContext';

/** Video containers VST accepts. */
const DEFAULT_ACCEPT = '.mp4,.mkv,video/mp4,video/x-matroska';

export interface ChatFileUploadRenderProps {
  /** Opens the confirm dialog. */
  triggerUpload: () => void;
  /** Opens the OS file picker directly. */
  triggerFilePicker: () => void;
  /** For `<label htmlFor>`, so a click opens the picker without a synthetic click. */
  fileInputId: string;
  isUploading: boolean;
  uploadProgress: number;
  isDragging: boolean;
  dragHandlers: {
    onDragEnter: (e: React.DragEvent) => void;
    onDragLeave: (e: React.DragEvent) => void;
    onDragOver: (e: React.DragEvent) => void;
    onDrop: (e: React.DragEvent) => void;
  };
}

export interface ChatFileUploadProps {
  /** Distinguishes this instance for upload-flow coordination. */
  uploadFlowSourceId: string;
  /** Reports whether any of this instance's dialogs are open. */
  onUploadFlowActiveChange?: (sourceId: string, active: boolean) => void;
  onUploadSuccess?: (result: FileUploadResult) => void;
  /** Fired once per batch with at least one success. */
  onUploadBatchComplete?: (payload: ChatVideoUploadCompletePayload) => void;
  onUploadError?: (error: Error) => void;
  /** Conversation active when the batch starts, for stale-prompt checks. */
  getActiveConversationId?: () => string | undefined;
  onSendHiddenMessage?: (message: string, uploadConversationId: string) => void;
  /** Blocks upload while a query is in flight. */
  disabled?: boolean;
  accept?: string;
  children: (props: ChatFileUploadRenderProps) => React.ReactNode;
}

export const ChatFileUpload: React.FC<ChatFileUploadProps> = ({
  uploadFlowSourceId,
  onUploadFlowActiveChange,
  onUploadSuccess,
  onUploadBatchComplete,
  onUploadError,
  getActiveConversationId,
  onSendHiddenMessage,
  disabled = false,
  accept = DEFAULT_ACCEPT,
  children,
}) => {
  const {
    state: {
      agentApiUrlBase,
      chatUploadFileConfigTemplateJson,
      chatUploadFileMetadataEnabled,
      chatUploadFileHiddenMessageTemplate,
    },
  } = useContext(ChatContext);

  const fileInputId = useId();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [showDialog, setShowDialog] = useState(false);
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [progressFiles, setProgressFiles] = useState<UploadProgressFileItem[]>([]);
  const [results, setResults] = useState<UploadResultItem[]>([]);
  const [showSuccess, setShowSuccess] = useState(false);
  const [isDragging, setIsDragging] = useState(false);

  // Nested drag events fire for every child element; count them so leaving a
  // child does not clear the highlight while still inside the drop zone.
  const dragDepthRef = useRef(0);

  const reportFlowActive = useCallback(
    (active: boolean) => onUploadFlowActiveChange?.(uploadFlowSourceId, active),
    [onUploadFlowActiveChange, uploadFlowSourceId],
  );

  const configTemplate: UploadFileConfigTemplate | null = (() => {
    if (!chatUploadFileConfigTemplateJson) return null;
    try {
      return JSON.parse(chatUploadFileConfigTemplateJson);
    } catch (error) {
      console.warn('Failed to parse upload file config template:', error);
      return null;
    }
  })();

  const openDialog = useCallback(
    (files: File[]) => {
      if (disabled) return;
      setPendingFiles(files);
      setShowDialog(true);
      reportFlowActive(true);
    },
    [disabled, reportFlowActive],
  );

  const closeDialog = useCallback(() => {
    setShowDialog(false);
    setPendingFiles([]);
    reportFlowActive(false);
  }, [reportFlowActive]);

  const triggerUpload = useCallback(() => {
    if (disabled) return;
    openDialog([]);
  }, [disabled, openDialog]);

  const triggerFilePicker = useCallback(() => {
    if (disabled) return;
    fileInputRef.current?.click();
  }, [disabled]);

  const runUpload = useCallback(
    async (entries: UploadFilesDialogEntry[]) => {
      if (!agentApiUrlBase) {
        toast.error('Agent API URL is not configured.');
        return;
      }

      // Captured before the first await: the user may switch conversations
      // while the upload runs, and the prompt belongs to the one they started in.
      const uploadConversationId = getActiveConversationId?.();

      setShowDialog(false);
      setIsUploading(true);
      setUploadProgress(0);
      reportFlowActive(true);

      const completed: UploadResultItem[] = [];
      const succeeded: { filename: string; result: FileUploadResult }[] = [];

      for (let index = 0; index < entries.length; index += 1) {
        const entry = entries[index];
        const filename = entry.uploadFilename?.trim() || entry.file.name;

        try {
          const result = await uploadFileChunked(
            entry.file,
            agentApiUrlBase,
            (chatUploadFileMetadataEnabled ? entry.formData : undefined) ?? {},
            (percent: number) => {
              // Progress is reported per file; scale it across the batch so the
              // popup advances monotonically rather than resetting each file.
              const overall = (index * 100 + percent) / entries.length;
              setUploadProgress(Math.round(overall));
            },
            undefined,
            filename,
          );

          completed.push({ filename, result: { ...result } });
          succeeded.push({ filename, result });
          onUploadSuccess?.(result);
        } catch (error) {
          const failure = error instanceof Error ? error : new Error(String(error));
          completed.push({ filename, error: failure.message });
          onUploadError?.(failure);
        }

        setProgressFiles(
          completed.map((item, itemIndex) => ({
            id: String(itemIndex),
            displayName: item.filename,
            uploadStatus: item.error ? 'error' : 'success',
            uploadError: item.error,
          })),
        );
      }

      setIsUploading(false);
      setResults(completed);
      setShowSuccess(true);

      if (succeeded.length > 0) {
        onUploadBatchComplete?.({ results: succeeded });

        // The auto-prompt asks the agent to look at what was just uploaded.
        if (chatUploadFileHiddenMessageTemplate && uploadConversationId) {
          const filenames = succeeded.map((item) => item.filename).join(', ');
          onSendHiddenMessage?.(
            chatUploadFileHiddenMessageTemplate.replace('{filenames}', filenames),
            uploadConversationId,
          );
        }
      }
    },
    [
      agentApiUrlBase,
      chatUploadFileMetadataEnabled,
      chatUploadFileHiddenMessageTemplate,
      getActiveConversationId,
      onSendHiddenMessage,
      onUploadBatchComplete,
      onUploadError,
      onUploadSuccess,
      reportFlowActive,
    ],
  );

  const dragHandlers = {
    onDragEnter: (event: React.DragEvent) => {
      event.preventDefault();
      if (disabled) return;
      // Ignore text and element drags; only a file drag should highlight.
      const hasFiles = Array.from(event.dataTransfer?.items ?? []).some(
        (item: any) => item.kind === 'file',
      );
      if (!hasFiles) return;

      dragDepthRef.current += 1;
      setIsDragging(true);
    },
    onDragLeave: (event: React.DragEvent) => {
      event.preventDefault();
      if (disabled) return;
      dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
      if (dragDepthRef.current === 0) setIsDragging(false);
    },
    onDragOver: (event: React.DragEvent) => {
      event.preventDefault();
    },
    onDrop: (event: React.DragEvent) => {
      event.preventDefault();
      dragDepthRef.current = 0;
      setIsDragging(false);
      if (disabled) return;

      const files = Array.from(event.dataTransfer?.files ?? []);
      if (files.length > 0) openDialog(files);
    },
  };

  return (
    <>
      <input
        id={fileInputId}
        ref={fileInputRef}
        type="file"
        multiple
        accept={accept}
        className="hidden"
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []);
          if (files.length > 0) openDialog(files);
          // Reset so re-picking the same file fires change again.
          event.target.value = '';
        }}
      />

      {children({
        triggerUpload,
        triggerFilePicker,
        fileInputId,
        isUploading,
        uploadProgress,
        isDragging,
        dragHandlers,
      })}

      <UploadFilesDialog
        open={showDialog}
        initialFiles={pendingFiles}
        accept={accept}
        configTemplate={configTemplate}
        onClose={closeDialog}
        onConfirm={runUpload}
      />

      {isUploading && (
        <UploadProgressPopup files={progressFiles} onCancelAll={() => setIsUploading(false)} />
      )}

      {showSuccess && (
        <UploadSuccessPopup
          results={results}
          onClose={() => {
            setShowSuccess(false);
            setResults([]);
            setProgressFiles([]);
            reportFlowActive(false);
          }}
        />
      )}
    </>
  );
};

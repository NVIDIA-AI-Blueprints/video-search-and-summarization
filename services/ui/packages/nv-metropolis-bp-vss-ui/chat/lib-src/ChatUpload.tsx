// SPDX-License-Identifier: MIT
/**
 * Chunked video upload from the chat surface.
 *
 * The dialogs and the upload itself come from `common` — the same components
 * the toolkit's ChatFileUpload used — so this file is only the coordination:
 * which files, cancellation, and the auto-prompt sent once a batch lands.
 *
 * Chunking is not an optimisation. The agent hands back a VST URL and the
 * browser POSTs chunks straight to VST, which is what keeps a large file from
 * dying on the ingress' request timeout.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import {
  UploadFilesDialog,
  UploadProgressPopup,
  UploadSuccessPopup,
  uploadFileChunked,
  type FileUploadResult,
  type UploadFileConfigTemplate,
  type UploadFileStatus,
  type UploadFilesDialogEntry,
} from 'common';

import type { ChatVideoUploadCompletePayload } from './types';

interface PendingFile {
  id: string;
  file: File;
  formData: Record<string, unknown>;
  uploadFilename?: string;
  uploadProgress?: number;
  uploadStatus?: UploadFileStatus;
  uploadError?: string;
}

type BatchEntry = {
  filename: string;
  result?: FileUploadResult;
  error?: string;
  cancelled?: boolean;
};

export interface ChatUploadRenderProps {
  /** Open the metadata dialog (the toolbar button). */
  triggerUpload: () => void;
  /** Open the OS file picker directly (the welcome drop zone). */
  triggerFilePicker: () => void;
  /** Pair with `<label htmlFor>` so the picker opens without a synthetic click. */
  fileInputId: string;
  isUploading: boolean;
  isDragging: boolean;
  dragHandlers: {
    onDragEnter: (e: React.DragEvent) => void;
    onDragLeave: (e: React.DragEvent) => void;
    onDragOver: (e: React.DragEvent) => void;
    onDrop: (e: React.DragEvent) => void;
  };
}

export interface ChatUploadProps {
  /** Base URL of the agent API that issues upload URLs. */
  agentApiUrlBase?: string;
  configTemplateJson?: string;
  metadataEnabled?: boolean;
  /** `{filenames}` is replaced with the uploaded names. */
  hiddenMessageTemplate?: string;
  disabled?: boolean;
  accept?: string;
  /** Conversation that was active when the batch started. */
  getActiveConversationId?: () => string | undefined;
  onSendHiddenMessage?: (message: string, uploadConversationId: string) => void;
  onUploadBatchComplete?: (payload: ChatVideoUploadCompletePayload) => void;
  /** True whenever any upload dialog is open, so the input can block sends. */
  onUploadFlowActiveChange?: (active: boolean) => void;
  onNotify?: (message: string) => void;
  children: (props: ChatUploadRenderProps) => React.ReactNode;
}

const DEFAULT_ACCEPT = '.mp4,.mkv,video/mp4,video/x-matroska';
let uploadSeq = 0;

export const ChatUpload: React.FC<ChatUploadProps> = ({
  agentApiUrlBase,
  configTemplateJson,
  metadataEnabled,
  hiddenMessageTemplate,
  disabled = false,
  accept = DEFAULT_ACCEPT,
  getActiveConversationId,
  onSendHiddenMessage,
  onUploadBatchComplete,
  onUploadFlowActiveChange,
  onNotify,
  children,
}) => {
  const fileInputId = useMemo(() => `vss-chat-upload-${uploadSeq++}`, []);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [isUploading, setIsUploading] = useState(false);
  const [showSelect, setShowSelect] = useState(false);
  const [showProgress, setShowProgress] = useState(false);
  const [showSuccess, setShowSuccess] = useState(false);
  const [initialFiles, setInitialFiles] = useState<File[] | null>(null);
  const [uploadingFiles, setUploadingFiles] = useState<PendingFile[]>([]);
  const [results, setResults] = useState<BatchEntry[]>([]);
  const [isDragging, setIsDragging] = useState(false);

  const dragCounter = useRef(0);
  const abortControllers = useRef<Map<string, AbortController>>(new Map());
  const cancelledIds = useRef<Set<string>>(new Set());

  // Read callbacks through refs: the upload runs for minutes and the parent
  // re-renders throughout, so capturing them at call time would go stale.
  const callbacks = useRef({
    getActiveConversationId,
    onSendHiddenMessage,
    onUploadBatchComplete,
    onUploadFlowActiveChange,
    onNotify,
  });
  callbacks.current = {
    getActiveConversationId,
    onSendHiddenMessage,
    onUploadBatchComplete,
    onUploadFlowActiveChange,
    onNotify,
  };

  const dialogOpen = showSelect || showProgress || showSuccess;
  useEffect(() => {
    callbacks.current.onUploadFlowActiveChange?.(dialogOpen);
    return () => callbacks.current.onUploadFlowActiveChange?.(false);
  }, [dialogOpen]);

  // Closing the tab mid-upload loses the file with no way to resume.
  useEffect(() => {
    if (!isUploading) return;
    const handler = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [isUploading]);

  const configTemplate = useMemo<UploadFileConfigTemplate | null>(() => {
    if (!configTemplateJson) return null;
    try {
      return JSON.parse(configTemplateJson);
    } catch (error) {
      console.warn('vss-chat: could not parse upload config template', error);
      return null;
    }
  }, [configTemplateJson]);

  const isAllowedVideo = useCallback(
    (file: File) =>
      /\.(mp4|mkv)$/i.test(file.name) ||
      ['video/mp4', 'video/x-matroska'].includes(file.type),
    [],
  );

  const openDialogWithFiles = useCallback(
    (list: FileList | File[]) => {
      const files = Array.from(list);
      if (!files.length) return;
      const valid = files.filter(isAllowedVideo);
      if (valid.length < files.length) {
        callbacks.current.onNotify?.('Please choose video files only (mp4, mkv)');
      }
      if (!valid.length) return;
      // flushSync so the dialog sees `initialFiles` on the same commit it opens
      // on; otherwise it mounts empty and drops the drop.
      flushSync(() => {
        setInitialFiles(valid);
        setShowSelect(true);
      });
    },
    [isAllowedVideo],
  );

  const triggerUpload = useCallback(() => {
    if (disabled || isUploading) return;
    setShowSelect(true);
  }, [disabled, isUploading]);

  const triggerFilePicker = useCallback(() => {
    if (disabled || isUploading) return;
    fileInputRef.current?.click();
  }, [disabled, isUploading]);

  const dragHandlers = useMemo(
    () => ({
      onDragEnter: (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        if (disabled || isUploading) return;
        dragCounter.current += 1;
        if (e.dataTransfer.items?.length) setIsDragging(true);
      },
      onDragLeave: (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        // Counter, not a boolean: dragging over a child fires leave on the parent.
        dragCounter.current -= 1;
        if (dragCounter.current <= 0) setIsDragging(false);
      },
      onDragOver: (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
      },
      onDrop: (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragging(false);
        dragCounter.current = 0;
        if (disabled || isUploading) return;
        if (e.dataTransfer.files?.length) openDialogWithFiles(e.dataTransfer.files);
      },
    }),
    [disabled, isUploading, openDialogWithFiles],
  );

  const patchFile = useCallback((id: string, patch: Partial<PendingFile>) => {
    setUploadingFiles((prev) => prev.map((f) => (f.id === id ? { ...f, ...patch } : f)));
  }, []);

  const cancelSingle = useCallback(
    (id: string) => {
      cancelledIds.current.add(id);
      abortControllers.current.get(id)?.abort();
      abortControllers.current.delete(id);
      patchFile(id, { uploadStatus: 'cancelled', uploadError: 'Cancelled' });
    },
    [patchFile],
  );

  const cancelAll = useCallback(() => {
    setUploadingFiles((prev) =>
      prev.map((f) => {
        if (f.uploadStatus === 'pending' || f.uploadStatus === 'uploading') {
          cancelledIds.current.add(f.id);
          return { ...f, uploadStatus: 'cancelled' as UploadFileStatus, uploadError: 'Cancelled' };
        }
        return f;
      }),
    );
    abortControllers.current.forEach((controller) => controller.abort());
    abortControllers.current.clear();
  }, []);

  const uploadOne = useCallback(
    async (item: PendingFile): Promise<BatchEntry> => {
      const filename = item.uploadFilename ?? item.file.name;
      const cancelled: BatchEntry = { filename, error: 'Upload was cancelled', cancelled: true };
      if (cancelledIds.current.has(item.id)) return cancelled;

      if (!agentApiUrlBase) {
        const error = 'Agent API URL is not configured';
        patchFile(item.id, { uploadStatus: 'error', uploadError: error });
        return { filename, error };
      }

      patchFile(item.id, { uploadStatus: 'uploading', uploadProgress: 0 });
      const controller = new AbortController();
      abortControllers.current.set(item.id, controller);

      try {
        const result = await uploadFileChunked(
          item.file,
          agentApiUrlBase,
          item.formData,
          (progress: number) => patchFile(item.id, { uploadProgress: progress }),
          controller.signal,
          item.uploadFilename,
        );
        abortControllers.current.delete(item.id);
        // Cancellation can land between the last chunk and here.
        if (cancelledIds.current.has(item.id)) return cancelled;
        patchFile(item.id, { uploadStatus: 'success', uploadProgress: 100 });
        return { filename, result };
      } catch (error) {
        abortControllers.current.delete(item.id);
        const aborted =
          error instanceof Error &&
          (error.name === 'AbortError' || error.message === 'Upload was cancelled');
        if (aborted || cancelledIds.current.has(item.id)) return cancelled;
        const message = error instanceof Error ? error.message : 'Unknown error';
        patchFile(item.id, { uploadStatus: 'error', uploadError: message });
        return { filename, error: message };
      }
    },
    [agentApiUrlBase, patchFile],
  );

  const runBatch = useCallback(
    async (files: PendingFile[]) => {
      const conversationAtStart = callbacks.current.getActiveConversationId?.();

      setShowSelect(false);
      setShowProgress(true);
      setIsUploading(true);
      setResults([]);
      cancelledIds.current.clear();

      const queued = files.map((f) => ({
        ...f,
        uploadStatus: 'pending' as UploadFileStatus,
        uploadProgress: 0,
      }));
      setUploadingFiles(queued);

      try {
        const batch = await Promise.all(queued.map(uploadOne));
        setResults(batch);

        const successes = batch.filter(
          (entry): entry is { filename: string; result: FileUploadResult } => !!entry.result,
        );

        if (successes.length) {
          callbacks.current.onUploadBatchComplete?.({
            results: successes.map(({ filename, result }) => ({ filename, result })),
          });

          if (conversationAtStart && hiddenMessageTemplate && callbacks.current.onSendHiddenMessage) {
            // The agent identifies a video by whatever the backend called it,
            // which is not always the local filename.
            const names = successes
              .map(
                ({ filename, result }) =>
                  (result as any)?.filename ||
                  (result as any)?.video_id ||
                  (result as any)?.id ||
                  filename,
              )
              .filter(Boolean);
            if (names.length) {
              callbacks.current.onSendHiddenMessage(
                hiddenMessageTemplate.replaceAll('{filenames}', names.join(' ')),
                conversationAtStart,
              );
            }
          }
        }

        // Let the last progress bar finish drawing before swapping panels.
        setTimeout(() => {
          setShowProgress(false);
          if (batch.length) setShowSuccess(true);
        }, 1000);
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Unknown error';
        callbacks.current.onNotify?.(`Upload failed: ${message}`);
        setShowProgress(false);
      } finally {
        setIsUploading(false);
        abortControllers.current.clear();
        cancelledIds.current.clear();
      }
    },
    [hiddenMessageTemplate, uploadOne],
  );

  const progressFiles = useMemo(
    () =>
      uploadingFiles.map((f) => ({
        id: f.id,
        displayName: f.uploadFilename ?? f.file.name,
        uploadProgress: f.uploadProgress,
        uploadStatus: f.uploadStatus,
        uploadError: f.uploadError,
      })),
    [uploadingFiles],
  );

  const successResults = useMemo(
    () =>
      results.map((r) => ({
        filename: r.filename,
        result: r.result as Record<string, unknown> | undefined,
        error: r.error,
        cancelled: r.cancelled,
      })),
    [results],
  );

  return (
    <>
      <input
        id={fileInputId}
        type="file"
        ref={fileInputRef}
        className="hidden"
        accept={accept}
        multiple
        disabled={disabled || isUploading}
        onChange={(event) => {
          const files = event.target.files ? Array.from(event.target.files) : [];
          // Reset first, or picking the same file twice in a row is a no-op.
          event.target.value = '';
          if (files.length) openDialogWithFiles(files);
        }}
      />

      {children({ triggerUpload, triggerFilePicker, fileInputId, isUploading, isDragging, dragHandlers })}

      <UploadFilesDialog
        open={showSelect}
        configTemplate={configTemplate}
        onClose={() => {
          setShowSelect(false);
          setInitialFiles(null);
        }}
        onConfirm={(entries: UploadFilesDialogEntry[]) => {
          void runBatch(
            entries.map((e) => ({
              id: e.id,
              file: e.file,
              formData: e.formData,
              uploadFilename: e.uploadFilename,
            })),
          );
        }}
        initialFiles={initialFiles}
        accept={accept}
        metadata={metadataEnabled ? { enabled: true } : undefined}
      />

      {showProgress && (
        <UploadProgressPopup
          files={progressFiles}
          onCancelAll={cancelAll}
          onCancelSingle={cancelSingle}
        />
      )}

      {showSuccess && results.length > 0 && (
        <UploadSuccessPopup
          results={successResults}
          onClose={() => {
            setShowSuccess(false);
            setShowProgress(false);
            setResults([]);
            setUploadingFiles([]);
          }}
        />
      )}
    </>
  );
};

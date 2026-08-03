import { useRef, useState, useCallback, useContext, useMemo, useEffect, useId } from 'react';
import { flushSync } from 'react-dom';

import toast from 'react-hot-toast';
import {
  UploadFilesDialog,
  UploadProgressPopup,
  UploadSuccessPopup,
  uploadFileChunked,
  type UploadFilesDialogEntry,
  type UploadFileConfigTemplate,
  type UploadFileStatus,
  type FileUploadResult,
} from '@aiqtoolkit-ui/common';

export type { UploadFileConfigTemplate, UploadFileFieldConfig } from '@aiqtoolkit-ui/common';

import HomeContext from '@/pages/api/home/home.context';
import type { ChatVideoUploadCompletePayload } from '@/types/chatVideoUpload';

interface FileWithFormData {
  id: string;
  file: File;
  formData: Record<string, any>;
  uploadFilename?: string;
  uploadProgress?: number;
  uploadStatus?: UploadFileStatus;
  uploadError?: string;
}

interface ChatFileUploadProps {
  /** Unique id for upload-flow coordination across multiple ChatFileUpload instances */
  uploadFlowSourceId: string;
  /** Notifies parent when any upload dialog (select / progress / success) is open */
  onUploadFlowActiveChange?: (sourceId: string, active: boolean) => void;
  /** Callback when upload completes successfully */
  onUploadSuccess?: (result: FileUploadResult) => void;
  /** Called once per batch when at least one file uploaded successfully */
  onUploadBatchComplete?: (payload: ChatVideoUploadCompletePayload) => void;
  /** Callback when upload fails */
  onUploadError?: (error: Error) => void;
  /** Returns the conversation id active when upload starts (for stale prompt checks). */
  getActiveConversationId?: () => string | undefined;
  /** Callback to send a hidden message after video upload completes */
  onSendHiddenMessage?: (message: string, uploadConversationId: string) => void;
  /** Whether upload is disabled */
  disabled?: boolean;
  /** Accepted file types (default: video/mp4) */
  accept?: string;
  children: (props: { 
    triggerUpload: () => void;
    triggerFilePicker: () => void;
    /** Use with <label htmlFor={fileInputId}> so click opens file picker without programmatic click */
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
  }) => React.ReactNode;
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
  accept = '.mp4,.mkv,video/mp4,video/x-matroska',
  children,
}) => {
  const {
    state: { agentApiUrlBase, chatUploadFileConfigTemplateJson, chatUploadFileMetadataEnabled, chatUploadFileHiddenMessageTemplate },
  } = useContext(HomeContext);

  const fileInputId = useId();
  const videoInputRef = useRef<HTMLInputElement>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [showSuccessPopup, setShowSuccessPopup] = useState(false);
  const [showProgressPopup, setShowProgressPopup] = useState(false);
  const [allUploadResults, setAllUploadResults] = useState<{ filename: string; result?: FileUploadResult; error?: string; cancelled?: boolean }[]>([]);
  const [uploadingFiles, setUploadingFiles] = useState<FileWithFormData[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const dragCounterRef = useRef(0);

  const abortControllerMapRef = useRef<Map<string, AbortController>>(new Map());
  const cancelledFileIdsRef = useRef<Set<string>>(new Set());

  const [showFileSelectPopup, setShowFileSelectPopup] = useState(false);
  const [initialFilesForDialog, setInitialFilesForDialog] = useState<File[] | null>(null);

  const onUploadFlowActiveChangeRef = useRef(onUploadFlowActiveChange);
  onUploadFlowActiveChangeRef.current = onUploadFlowActiveChange;
  const getActiveConversationIdRef = useRef(getActiveConversationId);
  getActiveConversationIdRef.current = getActiveConversationId;
  const onSendHiddenMessageRef = useRef(onSendHiddenMessage);
  onSendHiddenMessageRef.current = onSendHiddenMessage;
  const onUploadSuccessRef = useRef(onUploadSuccess);
  onUploadSuccessRef.current = onUploadSuccess;
  const onUploadBatchCompleteRef = useRef(onUploadBatchComplete);
  onUploadBatchCompleteRef.current = onUploadBatchComplete;
  const onUploadErrorRef = useRef(onUploadError);
  onUploadErrorRef.current = onUploadError;

  const uploadDialogOpen =
    showFileSelectPopup || showProgressPopup || showSuccessPopup;

  useEffect(() => {
    onUploadFlowActiveChangeRef.current?.(uploadFlowSourceId, uploadDialogOpen);
    return () => {
      onUploadFlowActiveChangeRef.current?.(uploadFlowSourceId, false);
    };
  }, [uploadDialogOpen, uploadFlowSourceId]);

  // Warn user before leaving page while uploading
  useEffect(() => {
    if (!isUploading) return;

    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };

    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [isUploading]);

  // Parse config template from context (read from env in home.state.tsx)
  const configTemplate = useMemo<UploadFileConfigTemplate | null>(() => {
    if (chatUploadFileConfigTemplateJson) {
      try {
        return JSON.parse(chatUploadFileConfigTemplateJson);
      } catch (error) {
        console.warn('Failed to parse upload file config template:', error);
      }
    }
    return null;
  }, [chatUploadFileConfigTemplateJson]);

  const triggerUpload = useCallback(() => {
    if (disabled || isUploading) return;
    setShowFileSelectPopup(true);
  }, [disabled, isUploading]);

  // Directly open the native file picker dialog
  const triggerFilePicker = useCallback(() => {
    if (disabled || isUploading) return;
    videoInputRef.current?.click();
  }, [disabled, isUploading]);

  const isAllowedVideoFile = useCallback((file: File) => {
    const allowedExtensions = /\.(mp4|mkv)$/i;
    const allowedMimeTypes = ['video/mp4', 'video/x-matroska'];
    return allowedExtensions.test(file.name) || allowedMimeTypes.includes(file.type);
  }, []);

  const openDialogWithFiles = useCallback(
    (fileList: FileList | File[]) => {
      const list = Array.from(fileList);
      if (!list.length) return;
      const valid = list.filter(isAllowedVideoFile);
      if (valid.length < list.length) toast.error('Please drop video files only (mp4, mkv)');
      if (valid.length > 0) {
        flushSync(() => {
          setInitialFilesForDialog(valid);
          setShowFileSelectPopup(true);
        });
      }
    },
    [isAllowedVideoFile]
  );

  const handleVideoFileChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const list = event.target.files;
      const files = list ? Array.from(list) : [];
      event.target.value = '';
      if (files.length) openDialogWithFiles(files);
    },
    [openDialogWithFiles]
  );

  const handleDialogClose = useCallback(() => {
    setShowFileSelectPopup(false);
    setInitialFilesForDialog(null);
  }, []);

  const handleClosePopup = useCallback(() => {
    setShowSuccessPopup(false);
    setShowProgressPopup(false);
    setAllUploadResults([]);
    setUploadingFiles([]);
  }, []);

  // Drag and drop handlers
  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (disabled || isUploading) return;
    dragCounterRef.current++;
    if (e.dataTransfer.items && e.dataTransfer.items.length > 0) {
      setIsDragging(true);
    }
  }, [disabled, isUploading]);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current--;
    if (dragCounterRef.current === 0) {
      setIsDragging(false);
    }
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setIsDragging(false);
      dragCounterRef.current = 0;
      if (disabled || isUploading) return;
      const list = e.dataTransfer.files;
      if (list?.length) openDialogWithFiles(list);
    },
    [disabled, isUploading, openDialogWithFiles]
  );

  const preventDragDefault = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const dragHandlers = useMemo(
    () => ({
      onDragEnter: handleDragEnter,
      onDragLeave: handleDragLeave,
      onDragOver: preventDragDefault,
      onDrop: handleDrop,
    }),
    [handleDragEnter, handleDragLeave, preventDragDefault, handleDrop]
  );

  const uploadProgressPopupFiles = useMemo(
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

  const uploadSuccessPopupResults = useMemo(
    () =>
      allUploadResults.map((r) => ({
        filename: r.filename,
        result: r.result as Record<string, unknown> | undefined,
        error: r.error,
        cancelled: r.cancelled,
      })),
    [allUploadResults],
  );


  // Update uploading files progress (for progress popup)
  const updateUploadingFileProgress = useCallback((fileId: string, progress: number) => {
    setUploadingFiles(prev => prev.map(f => 
      f.id === fileId ? { ...f, uploadProgress: progress } : f
    ));
  }, []);

  // Update uploading files status (for progress popup)
  const updateUploadingFileStatus = useCallback((fileId: string, status: UploadFileStatus, error?: string) => {
    setUploadingFiles(prev => prev.map(f => 
      f.id === fileId ? { ...f, uploadStatus: status, uploadError: error } : f
    ));
  }, []);

  // Cancel a single file upload
  const handleCancelSingleUpload = useCallback((fileId: string) => {
    // Mark as cancelled to prevent upload from starting
    cancelledFileIdsRef.current.add(fileId);
    
    // Abort upload if in progress
    abortControllerMapRef.current.get(fileId)?.abort();
    abortControllerMapRef.current.delete(fileId);
    
    // Update status immediately
    updateUploadingFileStatus(fileId, 'cancelled', 'Cancelled');
  }, [updateUploadingFileStatus]);

  // Cancel all uploads
  const handleCancelAllUploads = useCallback(() => {
    // Mark all pending/uploading files as cancelled and update UI
    setUploadingFiles(prev => prev.map(f => {
      if (f.uploadStatus === 'pending' || f.uploadStatus === 'uploading') {
        cancelledFileIdsRef.current.add(f.id);
        return { ...f, uploadStatus: 'cancelled' as UploadFileStatus, uploadError: 'Cancelled' };
      }
      return f;
    }));
    
    // Abort all uploads and clear map
    abortControllerMapRef.current.forEach(controller => controller.abort());
    abortControllerMapRef.current.clear();
  }, []);

  // Helper to check if file is cancelled
  const isFileCancelled = useCallback((fileId: string) => cancelledFileIdsRef.current.has(fileId), []);

  // Upload a single file (for progress popup)
  const uploadSingleFileWithTracking = async (fileItem: FileWithFormData): Promise<{ filename: string; result?: FileUploadResult; error?: string; cancelled?: boolean }> => {
    const { id: fileId, file, formData } = fileItem;
    const filename = fileItem.uploadFilename ?? file.name;
    const cancelledResult = { filename, error: 'Upload was cancelled', cancelled: true };

    // Check if already cancelled before starting
    if (isFileCancelled(fileId)) {
      return cancelledResult;
    }

    if (!agentApiUrlBase) {
      const errorMessage = 'Agent API URL is not configured';
      updateUploadingFileStatus(fileId, 'error', errorMessage);
      return { filename, error: errorMessage, cancelled: false };
    }

    updateUploadingFileStatus(fileId, 'uploading');
    updateUploadingFileProgress(fileId, 0);

    try {
      // Create AbortController for the upload
      const abortController = new AbortController();
      abortControllerMapRef.current.set(fileId, abortController);

      // Three-step chunked upload: agent gives us the VST URL, we POST
      // chunks straight to VST (bypassing Cloudflare's 100s timeout on
      // large files), then the agent's /complete hook runs post-processing
      // (timelines + RTVI register + embeddings on search profiles).
      const result = await uploadFileChunked(
        file,
        agentApiUrlBase,
        formData,
        (progress: number) => updateUploadingFileProgress(fileId, progress),
        abortController.signal,
        fileItem.uploadFilename
      );
      
      // Clean up AbortController after successful upload
      abortControllerMapRef.current.delete(fileId);

      // Check if cancelled after upload
      if (isFileCancelled(fileId)) {
        return cancelledResult;
      }

      updateUploadingFileStatus(fileId, 'success');
      updateUploadingFileProgress(fileId, 100);
      return { filename, result };
    } catch (error) {
      // Clean up AbortController on error
      abortControllerMapRef.current.delete(fileId);
      
      const isAborted = error instanceof Error && (error.name === 'AbortError' || error.message === 'Upload was cancelled');
      const isCancelled = isAborted || isFileCancelled(fileId);
      
      if (isCancelled) {
        return cancelledResult;
      }
      
      const errorMessage = error instanceof Error ? error.message : 'Unknown error';
      updateUploadingFileStatus(fileId, 'error', errorMessage);
      return { filename, error: errorMessage, cancelled: false };
    }
  };

  // Process all files in parallel
  const processFilesParallel = async (files: FileWithFormData[]) => {
    const conversationIdAtUploadStart = getActiveConversationIdRef.current?.();

    // Close file select popup and show progress popup
    setShowFileSelectPopup(false);
    setShowProgressPopup(true);
    setIsUploading(true);
    setAllUploadResults([]);
    
    // Clear cancelled file IDs from previous upload session
    cancelledFileIdsRef.current.clear();

    // Initialize uploading files for progress popup
    const filesToUpload = files.map(f => ({
      ...f,
      uploadStatus: 'pending' as UploadFileStatus,
      uploadProgress: 0,
    }));
    setUploadingFiles(filesToUpload);

    try {
      // Upload all files in parallel
      const results = await Promise.all(
        filesToUpload.map(fileItem => uploadSingleFileWithTracking(fileItem))
      );

      // Store all results
      setAllUploadResults(results);

      // Count successes, errors, and cancelled
      const successes = results.filter(r => r.result);
      const errors = results.filter(r => r.error && !r.cancelled);
      const cancelled = results.filter(r => r.cancelled);

      if (errors.length > 0) {
        errors.forEach(({ filename }) => {
          onUploadErrorRef.current?.(new Error(`Failed to upload ${filename}`));
        });
      }

      if (successes.length > 0) {
        successes.forEach(({ result }) => {
          if (result) onUploadSuccessRef.current?.(result);
        });

        onUploadBatchCompleteRef.current?.({
          results: successes.filter(
            (entry): entry is { filename: string; result: FileUploadResult } =>
              !!entry.result,
          ),
        });

        // Send hidden message to chat API with the uploaded video filenames
        if (
          conversationIdAtUploadStart &&
          onSendHiddenMessageRef.current &&
          chatUploadFileHiddenMessageTemplate
        ) {
          // Fallback order: result.filename -> result.video_id -> result.id -> original filename
          const videoFilenames = successes
            .map(({ filename, result }) => (result as any)?.filename || (result as any)?.video_id || (result as any)?.id || filename)
            .filter((name): name is string => !!name);
          
          if (videoFilenames.length > 0) {
            const filenamesStr = videoFilenames.join(' ');
            // Replace {filenames} placeholder with actual filenames
            const hiddenMessage = chatUploadFileHiddenMessageTemplate.replaceAll('{filenames}', filenamesStr);
            onSendHiddenMessageRef.current(hiddenMessage, conversationIdAtUploadStart);
          }
        }
      }

      // Show success popup after a short delay (even if some were cancelled)
      setTimeout(() => {
        setShowProgressPopup(false);
        // Only show success popup if there were any results (not all cancelled)
        if (successes.length > 0 || errors.length > 0 || cancelled.length > 0) {
          setShowSuccessPopup(true);
        }
      }, 1000);

    } catch (error) {
      const err = error instanceof Error ? error : new Error('Unknown error');
      toast.error(`Upload failed: ${err.message}`);
      onUploadErrorRef.current?.(err);
      setShowProgressPopup(false);
    } finally {
      setIsUploading(false);
      // Clear all remaining references
      abortControllerMapRef.current.clear();
      cancelledFileIdsRef.current.clear();
    }
  };

  const handleDialogConfirm = useCallback(
    (entries: UploadFilesDialogEntry[]) => {
      const filesToUpload: FileWithFormData[] = entries.map((e) => ({
        id: e.id,
        file: e.file,
        formData: e.formData,
        uploadFilename: e.uploadFilename,
      }));
      void processFilesParallel(filesToUpload);
    },
    [],
  );

  return (
    <>
      <input
        id={fileInputId}
        type="file"
        ref={videoInputRef}
        className="hidden"
        accept={accept}
        onChange={handleVideoFileChange}
        disabled={disabled || isUploading}
        multiple
      />
      {children({ triggerUpload, triggerFilePicker, fileInputId, isUploading, uploadProgress: 0, isDragging, dragHandlers })}

      <UploadFilesDialog
        open={showFileSelectPopup}
        configTemplate={configTemplate}
        onClose={handleDialogClose}
        onConfirm={handleDialogConfirm}
        initialFiles={initialFilesForDialog}
        accept={accept}
        metadata={
          chatUploadFileMetadataEnabled ? { enabled: true } : undefined
        }
      />

      {showProgressPopup && (
        <UploadProgressPopup
          files={uploadProgressPopupFiles}
          onCancelAll={handleCancelAllUploads}
          onCancelSingle={handleCancelSingleUpload}
        />
      )}

      {showSuccessPopup && allUploadResults.length > 0 && (
        <UploadSuccessPopup
          results={uploadSuccessPopupResults}
          onClose={handleClosePopup}
        />
      )}
    </>
  );
};

export default ChatFileUpload;

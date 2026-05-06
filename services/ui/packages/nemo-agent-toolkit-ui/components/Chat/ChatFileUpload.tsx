import { useRef, useState, useCallback, useContext, useMemo, useEffect, useId } from 'react';
import { flushSync } from 'react-dom';

import toast from 'react-hot-toast';
import { IconCheck, IconChevronDown, IconCopy, IconX } from '@tabler/icons-react';
import {
  UploadFilesDialog,
  copyToClipboard,
  uploadFileChunkedToVst,
  type UploadFileConfigTemplate,
  type FileUploadResult,
} from '@aiqtoolkit-ui/common';

export type { UploadFileConfigTemplate, UploadFileFieldConfig } from '@aiqtoolkit-ui/common';

import HomeContext from '@/pages/api/home/home.context';

// Upload status for each file
type FileUploadStatus = 'pending' | 'uploading' | 'success' | 'error' | 'cancelled';

// Interface for file with form data
interface FileWithFormData {
  id: string;
  file: File;
  formData: Record<string, any>;
  isExpanded: boolean;
  metadataFile?: File | null;
  isMetadataExpanded?: boolean;
  /** Filename sent to upload API (defaults to file.name) */
  uploadFilename?: string;
  uploadProgress?: number;
  uploadStatus?: FileUploadStatus;
  uploadError?: string;
}

// CSS class constants
const POPUP_OVERLAY_CLASS = 'fixed inset-0 z-50 flex items-center justify-center bg-black/50';
const POPUP_CONTAINER_CLASS = 'mx-4 w-full max-w-xl rounded-lg bg-white p-6 shadow-xl dark:bg-black';

// Upload status display config for progress bar and label styling
const UPLOAD_STATUS_STYLE: Record<FileUploadStatus, { progressBarClass: string; textClass: string }> = {
  pending: { progressBarClass: 'bg-gray-300', textClass: 'text-gray-400' },
  uploading: { progressBarClass: 'bg-[#76b900]', textClass: 'text-[#76b900]' },
  success: { progressBarClass: 'bg-green-500', textClass: 'text-green-500' },
  error: { progressBarClass: 'bg-red-500', textClass: 'text-red-500' },
  cancelled: { progressBarClass: 'bg-orange-500', textClass: 'text-orange-500' },
};

function getUploadStatusLabel(status: FileUploadStatus, progress?: number): string {
  switch (status) {
    case 'uploading': return `${progress ?? 0}%`;
    case 'success': return 'Done';
    case 'error': return 'Failed';
    case 'cancelled': return 'Cancelled';
    default: return 'Pending';
  }
}

type UploadResultItem = { filename: string; result?: FileUploadResult; error?: string; cancelled?: boolean };

function getUploadStatusIcon(status: FileUploadStatus, textClass: string) {
  switch (status) {
    case 'uploading':
      return <div className="h-4 w-4 animate-spin rounded-full border-2 border-gray-300 border-t-[#76b900]" />;
    case 'success':
      return <IconCheck size={16} className="flex-shrink-0 text-green-500" />;
    case 'error':
    case 'cancelled':
      return <IconX size={16} className={`flex-shrink-0 ${textClass}`} />;
    default:
      return <div className="h-4 w-4 rounded-full border-2 border-gray-300" />;
  }
}

function UploadProgressPopup({
  files,
  onCancelAll,
  onCancelSingle,
}: Readonly<{
  files: FileWithFormData[];
  onCancelAll: () => void;
  onCancelSingle: (fileId: string) => void;
}>) {
  const hasActive = files.some(f => f.uploadStatus === 'pending' || f.uploadStatus === 'uploading');
  return (
    <div className={POPUP_OVERLAY_CLASS}>
      <div className={POPUP_CONTAINER_CLASS}>
        <h3 className="mb-4 text-center text-lg font-semibold text-gray-900 dark:text-white">
          Uploading Files...
        </h3>
        {hasActive && (
          <div className="mb-4 flex justify-center">
            <button
              type="button"
              onClick={onCancelAll}
              className="flex items-center gap-2 rounded-lg border border-red-300 bg-red-50 px-4 py-2 text-sm font-medium text-red-600 transition-colors hover:bg-red-100 dark:border-red-700 dark:bg-red-900/20 dark:text-red-400 dark:hover:bg-red-900/40"
            >
              <IconX size={16} />
              Cancel All
            </button>
          </div>
        )}
        <div className="max-h-96 space-y-3 overflow-y-auto">
          {files.map((fileItem) => {
            const status = fileItem.uploadStatus ?? 'pending';
            const style = UPLOAD_STATUS_STYLE[status];
            const label = getUploadStatusLabel(status, fileItem.uploadProgress);
            return (
              <div key={fileItem.id} className="rounded-lg border border-gray-200 p-3 dark:border-gray-600">
                <div className="mb-2 flex items-center justify-between">
                  <div className="flex items-center gap-2 overflow-hidden">
                    {getUploadStatusIcon(status, style.textClass)}
                    <span className="truncate text-sm text-gray-700 dark:text-gray-300">{fileItem.uploadFilename ?? fileItem.file.name}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={`text-xs font-medium ${style.textClass}`}>{label}</span>
                    {(status === 'uploading' || status === 'pending') && (
                      <button
                        type="button"
                        onClick={() => onCancelSingle(fileItem.id)}
                        className="flex-shrink-0 rounded p-1 text-gray-400 transition-colors hover:bg-gray-100 hover:text-red-500 dark:hover:bg-gray-700"
                        title="Cancel upload"
                      >
                        <IconX size={14} />
                      </button>
                    )}
                  </div>
                </div>
                <div className="h-1.5 w-full rounded-full bg-gray-200 dark:bg-gray-700">
                  <div
                    className={`h-1.5 rounded-full transition-all duration-300 ${style.progressBarClass}`}
                    style={{ width: `${fileItem.uploadProgress ?? 0}%` }}
                  />
                </div>
                {fileItem.uploadError && <p className="mt-1 text-xs text-red-500">{fileItem.uploadError}</p>}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function getUploadStatusBanner(allSuccess: boolean, allFailed: boolean, allCancelled: boolean) {
  if (allSuccess) return { bg: 'bg-green-100 dark:bg-green-900', icon: <IconCheck size={24} className="text-green-600 dark:text-green-400" />, title: 'Upload Complete!', titleClass: 'text-green-700 dark:text-green-400' };
  if (allFailed) return { bg: 'bg-red-100 dark:bg-red-900', icon: <IconX size={24} className="text-red-600 dark:text-red-400" />, title: 'Upload Failed', titleClass: 'text-red-700 dark:text-red-400' };
  if (allCancelled) return { bg: 'bg-orange-100 dark:bg-orange-900', icon: <IconX size={24} className="text-orange-600 dark:text-orange-400" />, title: 'Upload Cancelled', titleClass: 'text-orange-700 dark:text-orange-400' };
  return { bg: 'bg-orange-100 dark:bg-orange-900', icon: <IconCheck size={24} className="text-orange-600 dark:text-orange-400" />, title: 'Upload Partially Complete', titleClass: 'text-gray-900 dark:text-white' };
}

function getResultItemStyle(item: UploadResultItem) {
  if (item.result) {
    return {
      borderClass: 'border-green-300 dark:border-green-700',
      bgClass: 'bg-green-50 hover:bg-green-100 dark:bg-green-900/20 dark:hover:bg-green-900/30',
      icon: <IconCheck size={16} className="flex-shrink-0 text-green-500" />,
      textClass: 'text-green-500',
      label: 'Success',
      content: JSON.stringify(item.result, null, 2),
    };
  }
  if (item.cancelled) {
    return {
      borderClass: 'border-orange-300 dark:border-orange-700',
      bgClass: 'bg-orange-50 hover:bg-orange-100 dark:bg-orange-900/20 dark:hover:bg-orange-900/30',
      icon: <IconX size={16} className="flex-shrink-0 text-orange-500" />,
      textClass: 'text-orange-500',
      label: 'Cancelled',
      content: 'Upload was cancelled',
    };
  }
  return {
    borderClass: 'border-red-300 dark:border-red-700',
    bgClass: 'bg-red-50 hover:bg-red-100 dark:bg-red-900/20 dark:hover:bg-red-900/30',
    icon: <IconX size={16} className="flex-shrink-0 text-red-500" />,
    textClass: 'text-red-500',
    label: 'Failed',
    content: `Error: ${item.error}`,
  };
}

function UploadSuccessPopup({
  results,
  expandedResults,
  copiedResultIndex,
  onToggleExpanded,
  onCopyJson,
  onClose,
}: Readonly<{
  results: UploadResultItem[];
  expandedResults: Set<number>;
  copiedResultIndex: number | null;
  onToggleExpanded: (index: number) => void;
  onCopyJson: (text?: string, index?: number) => Promise<void>;
  onClose: () => void;
}>) {
  const successCount = results.filter(r => r.result).length;
  const cancelledCount = results.filter(r => r.cancelled).length;
  const failedCount = results.length - successCount - cancelledCount;
  const totalCount = results.length;

  const statusConfig = getUploadStatusBanner(
    successCount === totalCount,
    failedCount === totalCount,
    cancelledCount === totalCount,
  );

  return (
    <div className={POPUP_OVERLAY_CLASS}>
      <div className={POPUP_CONTAINER_CLASS}>
        <div className="mb-4 flex justify-center">
          <div className={`flex h-12 w-12 items-center justify-center rounded-full ${statusConfig.bg}`}>
            {statusConfig.icon}
          </div>
        </div>
        <h3 className={`mb-2 text-center text-lg font-semibold ${statusConfig.titleClass}`}>
          {statusConfig.title}
        </h3>
        <p className="mb-4 text-center text-sm text-gray-600 dark:text-gray-400">
          {successCount} / {totalCount} files uploaded successfully
          {cancelledCount > 0 && <span className="ml-1 text-orange-500">({cancelledCount} cancelled)</span>}
          {failedCount > 0 && <span className="ml-1 text-red-500">({failedCount} failed)</span>}
        </p>
        <div className="mb-4 max-h-96 space-y-2 overflow-y-auto">
          {results.map((item, index) => {
            const rs = getResultItemStyle(item);
            return (
              <div
                key={`${item.filename}-${index}`}
                className={`overflow-hidden rounded-lg border ${rs.borderClass}`}
              >
                <button
                  type="button"
                  onClick={() => onToggleExpanded(index)}
                  className={`flex w-full items-center justify-between p-3 text-left transition-colors ${rs.bgClass}`}
                >
                  <div className="flex items-center gap-2 overflow-hidden">
                    <IconChevronDown size={14} className={`flex-shrink-0 text-gray-400 transition-transform duration-200 ${expandedResults.has(index) ? 'rotate-180' : ''}`} />
                    {rs.icon}
                    <span className="truncate text-sm font-medium text-gray-700 dark:text-gray-300">{item.filename}</span>
                  </div>
                  <span className={`text-xs font-medium ${rs.textClass}`}>{rs.label}</span>
                </button>
                {expandedResults.has(index) && (
                  <div className="border-t border-gray-200 bg-gray-50 p-2 dark:border-gray-600 dark:bg-[#1e1e28]">
                    <div className="relative">
                      <button
                        type="button"
                        onClick={() => onCopyJson(rs.content, index)}
                        className={`absolute right-1 top-1 rounded p-1 transition-colors ${copiedResultIndex === index ? 'text-green-500' : 'text-gray-400 hover:bg-gray-200 hover:text-gray-600 dark:hover:bg-gray-700 dark:hover:text-gray-300'}`}
                        title={copiedResultIndex === index ? 'Copied!' : 'Copy JSON'}
                      >
                        {copiedResultIndex === index ? <IconCheck size={14} /> : <IconCopy size={14} />}
                      </button>
                      <pre className="max-h-40 overflow-auto rounded bg-gray-100 p-2 pr-8 text-xs text-gray-800 dark:bg-[#0d0d12] dark:text-gray-300">
                        {rs.content}
                      </pre>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
        <button
          data-testid="upload-close-button"
          type="button"
          onClick={onClose}
          className="w-full rounded-lg bg-[#76b900] px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-[#5a8f00]"
        >
          Close
        </button>
      </div>
    </div>
  );
}

interface ChatFileUploadProps {
  /** Callback when upload completes successfully */
  onUploadSuccess?: (result: FileUploadResult) => void;
  /** Callback when upload fails */
  onUploadError?: (error: Error) => void;
  /** Callback to send a hidden message after video upload completes */
  onSendHiddenMessage?: (message: string) => void;
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
  onUploadSuccess,
  onUploadError,
  onSendHiddenMessage,
  disabled = false,
  accept = '.mp4,.mkv,video/mp4,video/x-matroska',
  children,
}) => {
  const {
    state: { agentApiUrlBase, vstApiUrl, chatUploadFileConfigTemplateJson, chatUploadFileMetadataEnabled, chatUploadFileHiddenMessageTemplate },
  } = useContext(HomeContext);

  const fileInputId = useId();
  const videoInputRef = useRef<HTMLInputElement>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [showSuccessPopup, setShowSuccessPopup] = useState(false);
  const [showProgressPopup, setShowProgressPopup] = useState(false);
  const [allUploadResults, setAllUploadResults] = useState<{ filename: string; result?: FileUploadResult; error?: string; cancelled?: boolean }[]>([]);
  const [uploadingFiles, setUploadingFiles] = useState<FileWithFormData[]>([]);
  const [expandedResults, setExpandedResults] = useState<Set<number>>(new Set());
  const [copiedResultIndex, setCopiedResultIndex] = useState<number | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const dragCounterRef = useRef(0);

  const abortControllerMapRef = useRef<Map<string, AbortController>>(new Map());
  const cancelledFileIdsRef = useRef<Set<string>>(new Set());

  const [showFileSelectPopup, setShowFileSelectPopup] = useState(false);
  const [initialFilesForDialog, setInitialFilesForDialog] = useState<File[] | null>(null);

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
    setExpandedResults(new Set());
    setCopiedResultIndex(null);
  }, []);

  const toggleResultExpanded = useCallback((index: number) => {
    setExpandedResults(prev => {
      const newSet = new Set(prev);
      if (newSet.has(index)) {
        newSet.delete(index);
      } else {
        newSet.add(index);
      }
      return newSet;
    });
  }, []);

  const handleCopyJson = useCallback(async (text?: string, index?: number) => {
    const content = text ?? (allUploadResults.length > 0 ? JSON.stringify(allUploadResults, null, 2) : '');
    if (content) {
      const success = await copyToClipboard(content);
      if (success) {
        if (index !== undefined) {
          setCopiedResultIndex(index);
          setTimeout(() => setCopiedResultIndex(null), 2000);
        }
      }
    }
  }, [allUploadResults]);

  // Drag and drop handlers
  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current++;
    if (e.dataTransfer.items && e.dataTransfer.items.length > 0) {
      setIsDragging(true);
    }
  }, []);

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


  // Update uploading files progress (for progress popup)
  const updateUploadingFileProgress = useCallback((fileId: string, progress: number) => {
    setUploadingFiles(prev => prev.map(f => 
      f.id === fileId ? { ...f, uploadProgress: progress } : f
    ));
  }, []);

  // Update uploading files status (for progress popup)
  const updateUploadingFileStatus = useCallback((fileId: string, status: FileUploadStatus, error?: string) => {
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
        return { ...f, uploadStatus: 'cancelled' as FileUploadStatus, uploadError: 'Cancelled' };
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

    if (!vstApiUrl) {
      const errorMessage =
        'VST API URL is not configured (NEXT_PUBLIC_VST_API_URL)';
      updateUploadingFileStatus(fileId, 'error', errorMessage);
      return { filename, error: errorMessage, cancelled: false };
    }

    updateUploadingFileStatus(fileId, 'uploading');
    updateUploadingFileProgress(fileId, 0);

    try {
      // Create AbortController for the upload
      const abortController = new AbortController();
      abortControllerMapRef.current.set(fileId, abortController);

      // Chunked upload directly to VST (bypasses Cloudflare 100s timeout
      // on large files by sending many short chunks instead of one
      // monolithic PUT). The agent's /complete hook then runs
      // post-processing (timelines + RTVI register + embeddings on
      // search profiles).
      const result = await uploadFileChunkedToVst(
        file,
        vstApiUrl,
        agentApiUrlBase,
        formData,
        (progress) => updateUploadingFileProgress(fileId, progress),
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
      uploadStatus: 'pending' as FileUploadStatus,
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
          onUploadError?.(new Error(`Failed to upload ${filename}`));
        });
      }

      if (successes.length > 0) {
        successes.forEach(({ result }) => {
          if (result) onUploadSuccess?.(result);
        });

        // Send hidden message to chat API with the uploaded video filenames
        if (onSendHiddenMessage && chatUploadFileHiddenMessageTemplate) {
          // Fallback order: result.filename -> result.video_id -> result.id -> original filename
          const videoFilenames = successes
            .map(({ filename, result }) => (result as any)?.filename || (result as any)?.video_id || (result as any)?.id || filename)
            .filter((name): name is string => !!name);
          
          if (videoFilenames.length > 0) {
            const filenamesStr = videoFilenames.join(' ');
            // Replace {filenames} placeholder with actual filenames
            const hiddenMessage = chatUploadFileHiddenMessageTemplate.replaceAll('{filenames}', filenamesStr);
            onSendHiddenMessage(hiddenMessage);
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
      onUploadError?.(err);
      setShowProgressPopup(false);
    } finally {
      setIsUploading(false);
      // Clear all remaining references
      abortControllerMapRef.current.clear();
      cancelledFileIdsRef.current.clear();
    }
  };

  const handleDialogConfirm = useCallback(
    (entries: { id: string; file: File; formData: Record<string, any>; uploadFilename?: string; metadataFile?: File | null }[]) => {
      const filesToUpload: FileWithFormData[] = entries.map((e) => ({
        id: e.id,
        file: e.file,
        formData: e.formData,
        isExpanded: false,
        uploadFilename: e.uploadFilename,
        metadataFile: e.metadataFile ?? undefined,
      }));
      processFilesParallel(filesToUpload);
    },
    [] // processFilesParallel is stable (uses refs and state setters)
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
          files={uploadingFiles}
          onCancelAll={handleCancelAllUploads}
          onCancelSingle={handleCancelSingleUpload}
        />
      )}

      {showSuccessPopup && allUploadResults.length > 0 && (
        <UploadSuccessPopup
          results={allUploadResults}
          expandedResults={expandedResults}
          copiedResultIndex={copiedResultIndex}
          onToggleExpanded={toggleResultExpanded}
          onCopyJson={handleCopyJson}
          onClose={handleClosePopup}
        />
      )}
    </>
  );
};

export default ChatFileUpload;

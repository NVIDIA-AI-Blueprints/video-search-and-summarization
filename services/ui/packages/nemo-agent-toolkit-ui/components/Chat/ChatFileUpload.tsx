import { useRef, useState, useCallback, useContext, useMemo, useEffect } from 'react';

import toast from 'react-hot-toast';
import { IconVideoPlus, IconX, IconFileCode, IconCheck, IconChevronDown, IconCopy, IconPlus, IconVideo } from '@tabler/icons-react';

import HomeContext from '@/pages/api/home/home.context';
import { copyToClipboard } from '@/utils/shared/clipboard';
import { uploadFile, type FileUploadResult } from '@/utils/shared/videoUpload';

// Types for upload file config template
export type UploadFileFieldType = 'boolean' | 'string' | 'number' | 'array' | 'select';

export interface UploadFileFieldConfig {
  'field-name': string;
  'field-type': UploadFileFieldType;
  'field-default-value': boolean | string | number | string[] | number[];
  'field-options'?: string[] | number[];
  'changeable'?: boolean;
  'tooltip-info'?: string;
}

// Interface for upload file config template
export interface UploadFileConfigTemplate {
  fields: UploadFileFieldConfig[];
}

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
  uploadProgress?: number;
  uploadStatus?: FileUploadStatus;
  uploadError?: string;
}

// CSS class constants
const INPUT_CLASS = 'w-full rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 focus:border-[#76b900] focus:outline-none focus:ring-1 focus:ring-[#76b900] dark:border-gray-600 dark:bg-[#343541] dark:text-gray-300';
const POPUP_OVERLAY_CLASS = 'fixed inset-0 z-50 flex items-center justify-center bg-black/50';
const POPUP_CONTAINER_CLASS = 'mx-4 w-full max-w-xl rounded-lg bg-white p-6 shadow-xl dark:bg-[#343541]';

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
    state: { agentApiUrlBase, chatUploadFileConfigTemplateJson, chatUploadFileMetadataEnabled, chatUploadFileHiddenMessageTemplate },
  } = useContext(HomeContext);

  const videoInputRef = useRef<HTMLInputElement>(null);
  const metadataInputRef = useRef<HTMLInputElement>(null);
  const [pendingMetadataFileId, setPendingMetadataFileId] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [showSuccessPopup, setShowSuccessPopup] = useState(false);
  const [showProgressPopup, setShowProgressPopup] = useState(false);
  const [allUploadResults, setAllUploadResults] = useState<{ filename: string; result?: FileUploadResult; error?: string; cancelled?: boolean }[]>([]);
  const [uploadingFiles, setUploadingFiles] = useState<FileWithFormData[]>([]);
  const [expandedResults, setExpandedResults] = useState<Set<number>>(new Set());
  const [copiedResultIndex, setCopiedResultIndex] = useState<number | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const dragCounterRef = useRef(0);
  
  // Store AbortControllers for each file to enable cancellation
  const abortControllerMapRef = useRef<Map<string, AbortController>>(new Map());
  // Track cancelled file IDs to prevent upload after cancellation
  const cancelledFileIdsRef = useRef<Set<string>>(new Set());
  
  // File selection popup state
  const [showFileSelectPopup, setShowFileSelectPopup] = useState(false);
  const [selectedFiles, setSelectedFiles] = useState<FileWithFormData[]>([]);
  
  // Drag states for drop zones in popup
  const [isDraggingMedia, setIsDraggingMedia] = useState(false);
  const [draggingMetadataFileId, setDraggingMetadataFileId] = useState<string | null>(null);

  // Warn user before leaving page while uploading
  useEffect(() => {
    if (!isUploading) return;

    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      // Required in most browsers to trigger the confirmation dialog
      e.returnValue = '';
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

  // Generate default form data from config template
  const generateDefaultFormData = useCallback((): Record<string, any> => {
    if (!configTemplate || !Array.isArray(configTemplate.fields)) return {};
    return configTemplate.fields.reduce((acc, field) => {
      acc[field['field-name']] = field['field-default-value'];
      return acc;
    }, {} as Record<string, any>);
  }, [configTemplate]);

  // Generate unique ID for file
  const generateFileId = useCallback(() => {
    return `file_${Date.now()}_${Math.random().toString(36).substring(2, 11)}`;
  }, []);

  // Create FileWithFormData from File
  const createFileWithFormData = useCallback((file: File): FileWithFormData => ({
    id: generateFileId(),
    file,
    formData: generateDefaultFormData(),
    isExpanded: false,
  }), [generateFileId, generateDefaultFormData]);

  // Get field value from formData or default
  const getFieldValue = useCallback((formData: Record<string, any>, field: UploadFileFieldConfig) => {
    return formData[field['field-name']] ?? field['field-default-value'];
  }, []);

  const triggerUpload = useCallback(() => {
    if (disabled || isUploading) return;
    setShowFileSelectPopup(true);
  }, [disabled, isUploading]);

  // Directly open the native file picker dialog
  const triggerFilePicker = useCallback(() => {
    if (disabled || isUploading) return;
    videoInputRef.current?.click();
  }, [disabled, isUploading]);

  const handleCancelFileSelect = useCallback(() => {
    setShowFileSelectPopup(false);
    setSelectedFiles([]);
  }, []);

  // Check if file is an allowed video format (only .mp4 and .mkv)
  const isAllowedVideoFile = useCallback((file: File) => {
    const allowedExtensions = /\.(mp4|mkv)$/i;
    const allowedMimeTypes = ['video/mp4', 'video/x-matroska'];
    return allowedExtensions.test(file.name) || allowedMimeTypes.includes(file.type);
  }, []);

  // Shared logic to process dropped/selected files
  const processDroppedFiles = useCallback((files: FileList | File[], openPopup = false) => {
    const allFiles = Array.from(files);
    const validFiles = allFiles.filter(isAllowedVideoFile);
    const hasInvalidFiles = allFiles.length > validFiles.length;
    
    if (hasInvalidFiles) {
      toast.error('Please drop video files only (mp4, mkv)');
    }
    
    if (validFiles.length > 0) {
      const newFiles = validFiles.map(createFileWithFormData);
      setSelectedFiles(prev => [...prev, ...newFiles]);
      if (openPopup) {
        setShowFileSelectPopup(true);
      }
    }
  }, [createFileWithFormData, isAllowedVideoFile]);

  const handleVideoFileChange = useCallback((event: React.ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files;
    if (files && files.length > 0) {
      processDroppedFiles(files, true);
    }
    event.target.value = '';
  }, [processDroppedFiles]);

  const handleRemoveFile = useCallback((fileId: string) => {
    setSelectedFiles(prev => prev.filter(f => f.id !== fileId));
  }, []);

  const handleToggleFileExpand = useCallback((fileId: string) => {
    setSelectedFiles(prev => prev.map(f => 
      f.id === fileId ? { ...f, isExpanded: !f.isExpanded } : f
    ));
  }, []);

  const handleFileFormDataChange = useCallback((fileId: string, fieldName: string, value: any) => {
    setSelectedFiles(prev => prev.map(f => 
      f.id === fileId ? { ...f, formData: { ...f.formData, [fieldName]: value } } : f
    ));
  }, []);

  // Toggle metadata section for a file
  const handleToggleFileMetadataExpand = useCallback((fileId: string) => {
    setSelectedFiles(prev => prev.map(f => 
      f.id === fileId ? { ...f, isMetadataExpanded: !f.isMetadataExpanded } : f
    ));
  }, []);

  // Validate and set metadata file for a specific file
  const validateAndSetFileMetadata = useCallback(async (fileId: string, file: File) => {
    if (!file.name.endsWith('.json')) {
      toast.error('Please select a JSON file');
      return false;
    }
    try {
      const content = await file.text();
      JSON.parse(content);
      setSelectedFiles(prev => prev.map(f => 
        f.id === fileId ? { ...f, metadataFile: file } : f
      ));
      return true;
    } catch {
      toast.error('Invalid JSON format. Please check your file.');
      return false;
    }
  }, []);

  // Remove metadata file from a specific file
  const handleRemoveFileMetadata = useCallback((fileId: string) => {
    setSelectedFiles(prev => prev.map(f => 
      f.id === fileId ? { ...f, metadataFile: null } : f
    ));
  }, []);

  // Open file picker for metadata
  const handleMetadataFileSelect = useCallback((fileId: string) => {
    setPendingMetadataFileId(fileId);
    metadataInputRef.current?.click();
  }, []);

  // Handle metadata file input change
  const handleMetadataInputChange = useCallback(async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file && pendingMetadataFileId) {
      await validateAndSetFileMetadata(pendingMetadataFileId, file);
    }
    event.target.value = '';
    setPendingMetadataFileId(null);
  }, [pendingMetadataFileId, validateAndSetFileMetadata]);

  // Common drag prevention handler
  const preventDragDefault = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleMediaDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDraggingMedia(false);
    
    const files = e.dataTransfer.files;
    if (files && files.length > 0) {
      processDroppedFiles(files, false);
    }
  }, [processDroppedFiles]);

  // Handle metadata drop for a specific file
  const handleFileMetadataDrop = useCallback(async (fileId: string, e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDraggingMetadataFileId(null);
    
    const files = e.dataTransfer.files;
    if (files && files.length > 0) {
      await validateAndSetFileMetadata(fileId, files[0]);
    }
  }, [validateAndSetFileMetadata]);

  const handleConfirmUpload = useCallback(() => {
    if (selectedFiles.length === 0) {
      toast.error('Please select at least one file');
      return;
    }
    processFilesParallel(selectedFiles);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedFiles]);

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

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    dragCounterRef.current = 0;

    if (disabled || isUploading) return;

    const files = e.dataTransfer.files;
    if (files && files.length > 0) {
      processDroppedFiles(files, true);
    }
  }, [disabled, isUploading, processDroppedFiles]);

  const dragHandlers = {
    onDragEnter: handleDragEnter,
    onDragLeave: handleDragLeave,
    onDragOver: preventDragDefault,
    onDrop: handleDrop,
  };


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
    const filename = file.name;
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

      // Use shared upload utility
      const result = await uploadFile(
        file,
        agentApiUrlBase,
        formData,
        (progress) => updateUploadingFileProgress(fileId, progress),
        abortController.signal
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

      // Clear selected files
      setSelectedFiles([]);

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

  return (
    <>
      {/* Hidden file inputs */}
      <input
        type="file"
        ref={videoInputRef}
        className="hidden"
        accept={accept}
        onChange={handleVideoFileChange}
        disabled={disabled || isUploading}
        multiple
      />
      <input
        type="file"
        ref={metadataInputRef}
        className="hidden"
        accept=".json,application/json"
        onChange={handleMetadataInputChange}
        disabled={disabled || isUploading}
      />
      {children({ triggerUpload, triggerFilePicker, isUploading, uploadProgress: 0, isDragging, dragHandlers })}

      {/* File Selection Popup */}
      {showFileSelectPopup && (
        <div className={POPUP_OVERLAY_CLASS}>
          <div className={POPUP_CONTAINER_CLASS}>
            {/* Title */}
            <h3 className="mb-6 text-center text-lg font-semibold text-gray-900 dark:text-white">
              Upload Files
            </h3>

            {/* Media File Section */}
            <div className="mb-4">
              <div className="mb-2 flex items-center justify-between">
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                  Files <span className="text-red-500">*</span>
                  {selectedFiles.length > 0 && (
                    <span className="ml-2 rounded-full bg-[#76b900] px-2 py-0.5 text-xs text-white">
                      {selectedFiles.length}
                    </span>
                  )}
                </label>
                {selectedFiles.length > 0 && (
                  <button
                    onClick={triggerFilePicker}
                    className="flex items-center gap-1 rounded-lg bg-[#76b900] px-2 py-1 text-xs font-medium text-white transition-colors hover:bg-[#5a8f00]"
                  >
                    <IconPlus size={14} />
                    Add More
                  </button>
                )}
              </div>

              {/* File List */}
              {selectedFiles.length > 0 ? (
                <div className="max-h-96 space-y-2 overflow-y-auto">
                  {selectedFiles.map((fileItem) => (
                    <div 
                      key={fileItem.id} 
                      className="overflow-hidden rounded-lg border border-gray-300 dark:border-gray-600"
                    >
                      {/* File Header */}
                      {(() => {
                        const hasExpandableContent = chatUploadFileMetadataEnabled || (configTemplate && Array.isArray(configTemplate.fields) && configTemplate.fields.length > 0);
                        
                        return (
                          <>
                            <div className="flex items-center justify-between bg-white p-3 dark:bg-[#343541]">
                              <div 
                                className={`flex flex-1 items-center gap-2 overflow-hidden ${hasExpandableContent ? 'cursor-pointer' : ''}`}
                                onClick={() => hasExpandableContent && handleToggleFileExpand(fileItem.id)}
                              >
                                {hasExpandableContent && (
                                  <IconChevronDown
                                    size={16}
                                    className={`flex-shrink-0 text-gray-400 transition-transform duration-200 ${fileItem.isExpanded ? 'rotate-180' : ''}`}
                                  />
                                )}
                                <IconVideo size={18} className="flex-shrink-0 text-[#76b900]" />
                                <span className="truncate text-sm text-gray-700 dark:text-gray-300">
                                  {fileItem.file.name}
                                </span>
                                <span className="flex-shrink-0 text-xs text-gray-400">
                                  ({(fileItem.file.size / 1024 / 1024).toFixed(2)} MB)
                                </span>
                              </div>
                              <button
                                onClick={() => handleRemoveFile(fileItem.id)}
                                className="ml-2 flex-shrink-0 text-gray-500 hover:text-red-500"
                              >
                                <IconX size={18} />
                              </button>
                            </div>

                            {/* File Form - Collapsible */}
                            {hasExpandableContent && fileItem.isExpanded && (
                        <div className="border-t border-gray-200 bg-gray-50 p-3 dark:border-gray-600 dark:bg-[#2a2a36]">
                          {/* Form Fields */}
                          {configTemplate && Array.isArray(configTemplate.fields) && configTemplate.fields.length > 0 && (
                            <div className="mb-3 space-y-3">
                              {configTemplate.fields.map((field) => {
                                const value = getFieldValue(fileItem.formData, field);
                                const fieldName = field['field-name'];
                                const isChangeable = field['changeable'] !== false;
                                const tooltipInfo = field['tooltip-info'] || '';
                                return (
                                  <div key={fieldName} className="flex items-center gap-3">
                                    <label 
                                      className="w-24 flex-shrink-0 text-xs font-medium text-gray-600 dark:text-gray-400"
                                      title={tooltipInfo}
                                    >
                                      {fieldName.charAt(0).toUpperCase() + fieldName.slice(1)}
                                    </label>
                                    <div className="flex-1" title={tooltipInfo}>
                                      {field['field-type'] === 'boolean' ? (
                                        <label className={`flex items-center gap-2 ${isChangeable ? 'cursor-pointer' : 'cursor-not-allowed opacity-60'}`}>
                                          <button
                                            type="button"
                                            role="switch"
                                            aria-checked={value}
                                            disabled={!isChangeable}
                                            onClick={() => isChangeable && handleFileFormDataChange(fileItem.id, fieldName, !value)}
                                            className={`relative inline-flex h-5 w-9 flex-shrink-0 rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-[#76b900] focus:ring-offset-2 ${
                                              value ? 'bg-[#76b900]' : 'bg-gray-300 dark:bg-gray-600'
                                            } ${isChangeable ? 'cursor-pointer' : 'cursor-not-allowed opacity-60'}`}
                                          >
                                            <span
                                              className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
                                                value ? 'translate-x-4' : 'translate-x-0'
                                              }`}
                                            />
                                          </button>
                                          <span className="text-sm text-gray-700 dark:text-gray-300">
                                            {value ? 'Yes' : 'No'}
                                          </span>
                                        </label>
                                      ) : field['field-type'] === 'select' ? (
                                        <select
                                          value={value}
                                          disabled={!isChangeable}
                                          onChange={(e) => handleFileFormDataChange(fileItem.id, fieldName, e.target.value)}
                                          className={`${INPUT_CLASS} ${!isChangeable ? 'cursor-not-allowed opacity-60' : ''}`}
                                        >
                                          {field['field-options']?.map((option) => (
                                            <option key={String(option)} value={String(option)}>
                                              {String(option)}
                                            </option>
                                          ))}
                                        </select>
                                      ) : field['field-type'] === 'number' ? (
                                        <input
                                          type="number"
                                          value={value}
                                          disabled={!isChangeable}
                                          onChange={(e) => handleFileFormDataChange(fileItem.id, fieldName, Number(e.target.value))}
                                          className={`${INPUT_CLASS} ${!isChangeable ? 'cursor-not-allowed opacity-60' : ''}`}
                                        />
                                      ) : (
                                        <input
                                          type="text"
                                          value={value}
                                          disabled={!isChangeable}
                                          onChange={(e) => handleFileFormDataChange(fileItem.id, fieldName, e.target.value)}
                                          className={`${INPUT_CLASS} ${!isChangeable ? 'cursor-not-allowed opacity-60' : ''}`}
                                          placeholder={`Enter ${fieldName}`}
                                        />
                                      )}
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                          )}


                          {/* Metadata File Section - Per file (only show when enabled via env) */}
                          {chatUploadFileMetadataEnabled && (
                            <div className="overflow-hidden rounded-lg border border-gray-300 dark:border-gray-600">
                              {/* Metadata Accordion Header */}
                              <button
                                type="button"
                                onClick={() => handleToggleFileMetadataExpand(fileItem.id)}
                                className="flex w-full items-center gap-2 bg-white px-3 py-2 text-left transition-colors hover:bg-gray-50 dark:bg-[#343541] dark:hover:bg-[#3d3d4a]"
                              >
                                <IconChevronDown
                                  size={14}
                                  className={`flex-shrink-0 text-gray-400 transition-transform duration-200 ${fileItem.isMetadataExpanded ? 'rotate-180' : ''}`}
                                />
                                <span className="text-xs font-medium text-gray-700 dark:text-gray-300">
                                  Metadata (JSON)
                                </span>
                                {fileItem.metadataFile && (
                                  <span className="rounded-full bg-blue-500 px-1.5 py-0.5 text-xs text-white">1</span>
                                )}
                                <span className="text-xs text-gray-400">(optional)</span>
                              </button>
                              
                              {/* Metadata Content */}
                              {fileItem.isMetadataExpanded && (
                                <div className="border-t border-gray-200 bg-white p-2 dark:border-gray-600 dark:bg-[#343541]">
                                  {fileItem.metadataFile ? (
                                    <div className="flex items-center justify-between rounded-lg border border-blue-500 bg-blue-500/10 p-2">
                                      <div className="flex items-center gap-2 overflow-hidden">
                                        <IconFileCode size={16} className="flex-shrink-0 text-blue-500" />
                                        <span className="truncate text-xs text-gray-700 dark:text-gray-300">{fileItem.metadataFile.name}</span>
                                      </div>
                                      <button
                                        onClick={() => handleRemoveFileMetadata(fileItem.id)}
                                        className="ml-2 flex-shrink-0 text-gray-500 hover:text-red-500"
                                      >
                                        <IconX size={16} />
                                      </button>
                                    </div>
                                  ) : (
                                    <div
                                      onClick={() => handleMetadataFileSelect(fileItem.id)}
                                      onDragOver={preventDragDefault}
                                      onDragEnter={(e) => { preventDragDefault(e); setDraggingMetadataFileId(fileItem.id); }}
                                      onDragLeave={(e) => { preventDragDefault(e); setDraggingMetadataFileId(null); }}
                                      onDrop={(e) => handleFileMetadataDrop(fileItem.id, e)}
                                      className={`w-full cursor-pointer rounded-lg border-2 border-dashed p-3 text-center transition-colors ${
                                        draggingMetadataFileId === fileItem.id
                                          ? 'border-blue-500 bg-blue-500/10'
                                          : 'border-gray-300 hover:border-blue-500 hover:bg-gray-50 dark:border-gray-600 dark:hover:border-blue-500 dark:hover:bg-[#3d3d4a]'
                                      }`}
                                    >
                                      <IconFileCode size={24} className="mx-auto text-gray-400" />
                                      <span className="mt-1 block text-xs text-gray-500 dark:text-gray-400">
                                        {draggingMetadataFileId === fileItem.id ? 'Drop JSON here' : 'Click or drag JSON metadata'}
                                      </span>
                                    </div>
                                  )}
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      )}
                          </>
                        );
                      })()}
                    </div>
                  ))}
                </div>
              ) : (
                <div
                  onClick={triggerFilePicker}
                  onDragOver={preventDragDefault}
                  onDragEnter={(e) => { preventDragDefault(e); setIsDraggingMedia(true); }}
                  onDragLeave={(e) => { preventDragDefault(e); setIsDraggingMedia(false); }}
                  onDrop={handleMediaDrop}
                  className={`w-full cursor-pointer rounded-lg border-2 border-dashed p-4 text-center transition-colors ${
                    isDraggingMedia
                      ? 'border-[#76b900] bg-[#76b900]/10'
                      : 'border-gray-300 hover:border-[#76b900] hover:bg-gray-50 dark:border-gray-600 dark:hover:border-[#76b900] dark:hover:bg-gray-800'
                  }`}
                >
                  <IconVideoPlus size={40} className="mx-auto text-gray-400" />
                  <span className="mt-2 block text-sm font-medium text-gray-700 dark:text-gray-300">
                    {isDraggingMedia ? 'Drop files here' : 'Click or drag files here'}
                  </span>
                  <div className="mt-2 flex flex-wrap justify-center gap-2 text-xs text-gray-500 dark:text-gray-400">
                    <span className="rounded bg-gray-100 px-2 py-0.5 dark:bg-gray-700">Movie Files (mp4, mkv)</span>
                  </div>
                </div>
              )}
            </div>

            {/* Buttons */}
            <div className="flex gap-3">
              <button
                onClick={handleCancelFileSelect}
                className="flex-1 rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-200 dark:hover:bg-gray-600"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirmUpload}
                disabled={selectedFiles.length === 0}
                className={`flex-1 rounded-lg px-4 py-2 text-sm font-medium text-white transition-colors ${
                  selectedFiles.length > 0
                    ? 'bg-[#76b900] hover:bg-[#5a8f00]'
                    : 'bg-gray-400'
                }`}
              >
                Upload {selectedFiles.length > 0 ? `(${selectedFiles.length})` : ''}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Progress Popup */}
      {showProgressPopup && (
        <div className={POPUP_OVERLAY_CLASS}>
          <div className={POPUP_CONTAINER_CLASS}>
            {/* Title */}
            <h3 className="mb-4 text-center text-lg font-semibold text-gray-900 dark:text-white">
              Uploading Files...
            </h3>

            {/* Cancel All Button */}
            {uploadingFiles.some(f => f.uploadStatus === 'pending' || f.uploadStatus === 'uploading') && (
              <div className="mb-4 flex justify-center">
                <button
                  onClick={handleCancelAllUploads}
                  className="flex items-center gap-2 rounded-lg border border-red-300 bg-red-50 px-4 py-2 text-sm font-medium text-red-600 transition-colors hover:bg-red-100 dark:border-red-700 dark:bg-red-900/20 dark:text-red-400 dark:hover:bg-red-900/40"
                >
                  <IconX size={16} />
                  Cancel All
                </button>
              </div>
            )}

            {/* File Progress List */}
            <div className="max-h-96 space-y-3 overflow-y-auto">
              {uploadingFiles.map((fileItem) => (
                <div key={fileItem.id} className="rounded-lg border border-gray-200 p-3 dark:border-gray-600">
                  <div className="mb-2 flex items-center justify-between">
                    <div className="flex items-center gap-2 overflow-hidden">
                      {fileItem.uploadStatus === 'uploading' ? (
                        <div className="h-4 w-4 animate-spin rounded-full border-2 border-gray-300 border-t-[#76b900]" />
                      ) : fileItem.uploadStatus === 'success' ? (
                        <IconCheck size={16} className="flex-shrink-0 text-green-500" />
                      ) : fileItem.uploadStatus === 'error' ? (
                        <IconX size={16} className="flex-shrink-0 text-red-500" />
                      ) : fileItem.uploadStatus === 'cancelled' ? (
                        <IconX size={16} className="flex-shrink-0 text-orange-500" />
                      ) : (
                        <div className="h-4 w-4 rounded-full border-2 border-gray-300" />
                      )}
                      <span className="truncate text-sm text-gray-700 dark:text-gray-300">
                        {fileItem.file.name}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`text-xs font-medium ${
                        fileItem.uploadStatus === 'success' ? 'text-green-500' 
                        : fileItem.uploadStatus === 'error' ? 'text-red-500'
                        : fileItem.uploadStatus === 'cancelled' ? 'text-orange-500'
                        : fileItem.uploadStatus === 'uploading' ? 'text-[#76b900]'
                        : 'text-gray-400'
                      }`}>
                        {fileItem.uploadStatus === 'success' ? 'Done' 
                         : fileItem.uploadStatus === 'error' ? 'Failed'
                         : fileItem.uploadStatus === 'cancelled' ? 'Cancelled'
                         : fileItem.uploadStatus === 'uploading' ? `${fileItem.uploadProgress || 0}%`
                         : 'Pending'}
                      </span>
                      {/* Cancel button for uploading/pending files */}
                      {(fileItem.uploadStatus === 'uploading' || fileItem.uploadStatus === 'pending') && (
                        <button
                          onClick={() => handleCancelSingleUpload(fileItem.id)}
                          className="flex-shrink-0 rounded p-1 text-gray-400 transition-colors hover:bg-gray-100 hover:text-red-500 dark:hover:bg-gray-700"
                          title="Cancel upload"
                        >
                          <IconX size={14} />
                        </button>
                      )}
                    </div>
                  </div>
                  {/* Progress Bar */}
                  <div className="h-1.5 w-full rounded-full bg-gray-200 dark:bg-gray-700">
                    <div 
                      className={`h-1.5 rounded-full transition-all duration-300 ${
                        fileItem.uploadStatus === 'success' ? 'bg-green-500'
                        : fileItem.uploadStatus === 'error' ? 'bg-red-500'
                        : fileItem.uploadStatus === 'cancelled' ? 'bg-orange-500'
                        : 'bg-[#76b900]'
                      }`}
                      style={{ width: `${fileItem.uploadProgress || 0}%` }}
                    />
                  </div>
                  {fileItem.uploadError && (
                    <p className="mt-1 text-xs text-red-500">{fileItem.uploadError}</p>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Success Popup */}
      {showSuccessPopup && allUploadResults.length > 0 && (() => {
        const successCount = allUploadResults.filter(r => r.result).length;
        const cancelledCount = allUploadResults.filter(r => r.cancelled).length;
        const failedCount = allUploadResults.length - successCount - cancelledCount;
        const totalCount = allUploadResults.length;
        
        // Determine overall status
        const allSuccess = successCount === totalCount;
        const allFailed = failedCount === totalCount;
        const allCancelled = cancelledCount === totalCount;
        
        return (
        <div className={POPUP_OVERLAY_CLASS}>
          <div className={POPUP_CONTAINER_CLASS}>
            {/* Status Icon - changes based on result */}
            <div className="mb-4 flex justify-center">
              <div className={`flex h-12 w-12 items-center justify-center rounded-full ${
                allSuccess 
                  ? 'bg-green-100 dark:bg-green-900' 
                  : allFailed 
                    ? 'bg-red-100 dark:bg-red-900'
                    : allCancelled
                      ? 'bg-orange-100 dark:bg-orange-900'
                      : 'bg-orange-100 dark:bg-orange-900'
              }`}>
                {allSuccess ? (
                  <IconCheck size={24} className="text-green-600 dark:text-green-400" />
                ) : allFailed ? (
                  <IconX size={24} className="text-red-600 dark:text-red-400" />
                ) : allCancelled ? (
                  <IconX size={24} className="text-orange-600 dark:text-orange-400" />
                ) : (
                  <IconCheck size={24} className="text-orange-600 dark:text-orange-400" />
                )}
              </div>
            </div>

            {/* Title - changes based on result */}
            <h3 className={`mb-2 text-center text-lg font-semibold ${
              allSuccess 
                ? 'text-green-700 dark:text-green-400' 
                : allFailed 
                  ? 'text-red-700 dark:text-red-400'
                  : allCancelled
                    ? 'text-orange-700 dark:text-orange-400'
                    : 'text-gray-900 dark:text-white'
            }`}>
              {allSuccess 
                ? 'Upload Complete!' 
                : allFailed 
                  ? 'Upload Failed'
                  : allCancelled
                    ? 'Upload Cancelled'
                    : 'Upload Partially Complete'}
            </h3>
            
            {/* Description */}
            <p className="mb-4 text-center text-sm text-gray-600 dark:text-gray-400">
              {successCount} / {totalCount} files uploaded successfully
              {cancelledCount > 0 && (
                <span className="ml-1 text-orange-500">
                  ({cancelledCount} cancelled)
                </span>
              )}
              {failedCount > 0 && (
                <span className="ml-1 text-red-500">
                  ({failedCount} failed)
                </span>
              )}
            </p>

            {/* File Results List */}
            <div className="mb-4 max-h-96 space-y-2 overflow-y-auto">
              {allUploadResults.map((item, index) => (
                <div 
                  key={index} 
                  className={`overflow-hidden rounded-lg border ${
                    item.result 
                      ? 'border-green-300 dark:border-green-700' 
                      : item.cancelled 
                        ? 'border-orange-300 dark:border-orange-700'
                        : 'border-red-300 dark:border-red-700'
                  }`}
                >
                  {/* File Header - Clickable to expand/collapse */}
                  <button
                    type="button"
                    onClick={() => toggleResultExpanded(index)}
                    className={`flex w-full items-center justify-between p-3 text-left transition-colors ${
                      item.result 
                        ? 'bg-green-50 hover:bg-green-100 dark:bg-green-900/20 dark:hover:bg-green-900/30' 
                        : item.cancelled
                          ? 'bg-orange-50 hover:bg-orange-100 dark:bg-orange-900/20 dark:hover:bg-orange-900/30'
                          : 'bg-red-50 hover:bg-red-100 dark:bg-red-900/20 dark:hover:bg-red-900/30'
                    }`}
                  >
                    <div className="flex items-center gap-2 overflow-hidden">
                      <IconChevronDown
                        size={14}
                        className={`flex-shrink-0 text-gray-400 transition-transform duration-200 ${
                          expandedResults.has(index) ? 'rotate-180' : ''
                        }`}
                      />
                      {item.result ? (
                        <IconCheck size={16} className="flex-shrink-0 text-green-500" />
                      ) : item.cancelled ? (
                        <IconX size={16} className="flex-shrink-0 text-orange-500" />
                      ) : (
                        <IconX size={16} className="flex-shrink-0 text-red-500" />
                      )}
                      <span className="truncate text-sm font-medium text-gray-700 dark:text-gray-300">
                        {item.filename}
                      </span>
                    </div>
                    <span className={`text-xs font-medium ${
                      item.result 
                        ? 'text-green-500' 
                        : item.cancelled 
                          ? 'text-orange-500'
                          : 'text-red-500'
                    }`}>
                      {item.result ? 'Success' : item.cancelled ? 'Cancelled' : 'Failed'}
                    </span>
                  </button>
                  {/* JSON Response or Error - Collapsible */}
                  {expandedResults.has(index) && (
                    <div className="border-t border-gray-200 bg-gray-50 p-2 dark:border-gray-600 dark:bg-[#1e1e28]">
                      <div className="relative">
                        <button
                          type="button"
                          onClick={() => handleCopyJson(
                            item.result 
                              ? JSON.stringify(item.result, null, 2)
                              : item.cancelled
                                ? 'Upload was cancelled'
                                : `Error: ${item.error}`,
                            index
                          )}
                          className={`absolute right-1 top-1 rounded p-1 transition-colors ${
                            copiedResultIndex === index
                              ? 'text-green-500'
                              : 'text-gray-400 hover:bg-gray-200 hover:text-gray-600 dark:hover:bg-gray-700 dark:hover:text-gray-300'
                          }`}
                          title={copiedResultIndex === index ? 'Copied!' : 'Copy JSON'}
                        >
                          {copiedResultIndex === index ? <IconCheck size={14} /> : <IconCopy size={14} />}
                        </button>
                        <pre className="max-h-40 overflow-auto rounded bg-gray-100 p-2 pr-8 text-xs text-gray-800 dark:bg-[#0d0d12] dark:text-gray-300">
                          {item.result 
                            ? JSON.stringify(item.result, null, 2)
                            : item.cancelled
                              ? 'Upload was cancelled'
                              : `Error: ${item.error}`
                          }
                        </pre>
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>

            {/* Button */}
            <button
              onClick={handleClosePopup}
              className="w-full rounded-lg bg-[#76b900] px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-[#5a8f00]"
            >
              Close
            </button>
          </div>
        </div>
        );
      })()}
    </>
  );
};

export default ChatFileUpload;

// SPDX-License-Identifier: MIT
import React, { useState, useCallback, useMemo, useEffect, useRef } from 'react';
import type { VideoManagementComponentProps, UploadProgress, StreamInfo } from './types';
import { useStreams, useStorageTimelines } from './hooks';
import { filterStreams, isRtspStream } from './utils';
import { UploadFilesDialog, VideoModal, useVideoModal } from '@nemo-agent-toolkit/ui';
import { uploadFileChunked, notifyUploadComplete } from './chunkedUpload';
import { createApiEndpoints } from './api';
import { deleteRtspStream } from './rtspStream';
import { deleteVideo } from './videoDelete';
import { NUM_PARALLEL_FILE_UPLOADS } from './constants';
import {
  AddRtspDialog,
  EmptyState,
  LoadingState,
  StreamsGrid,
  Toolbar,
  UploadProgressPanel,
  VideoManagementSidebarControls,
  AgentUploadDialog,
} from './components';

export type { VideoManagementComponentProps, VideoManagementSidebarControlHandlers } from './types';

export const VideoManagementComponent: React.FC<VideoManagementComponentProps> = ({
  videoManagementData,
  renderControlsInLeftSidebar = false,
  onControlsReady,
  isActive = true,
  addChatQueryContext,
}) => {
  const vstApiUrl = videoManagementData?.vstApiUrl;
  const agentApiUrl = videoManagementData?.agentApiUrl;
  const chatUploadFileConfigTemplateJson = videoManagementData?.chatUploadFileConfigTemplateJson;
  const enableAddRtspButton = videoManagementData?.enableAddRtspButton ?? true;
  const enableVideoUpload = videoManagementData?.enableVideoUpload ?? true;

  // Upload dialog state (chat-style upload with config fields)
  const [showUploadDialog, setShowUploadDialog] = useState(false);
  const [selectedFiles, setSelectedFiles] = useState<Array<{
    id: string;
    file: File;
    isExpanded: boolean;
    formData: Record<string, any>;
  }>>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Parse config template from videoManagementData (same as Chat component)
  const configTemplate = useMemo(() => {
    if (chatUploadFileConfigTemplateJson) {
      try {
        return JSON.parse(chatUploadFileConfigTemplateJson);
      } catch (error) {
        console.warn('Failed to parse upload file config template:', error);
      }
    }
    return null;
  }, [chatUploadFileConfigTemplateJson]);

  // Generate default form data from config template (same as Chat component)
  const generateDefaultFormData = useCallback((): Record<string, any> => {
    if (!configTemplate || !Array.isArray(configTemplate.fields)) return {};
    return configTemplate.fields.reduce((acc: Record<string, any>, field: any) => {
      acc[field['field-name']] = field['field-default-value'];
      return acc;
    }, {} as Record<string, any>);
  }, [configTemplate]);

  const generateFileId = useCallback(() => {
    return `file_${Date.now()}_${Math.random().toString(36).substring(2, 11)}`;
  }, []);

  const [isRtspModalOpen, setIsRtspModalOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [appliedSearchQuery, setAppliedSearchQuery] = useState('');
  const searchInputValueRef = useRef('');
  const [showVideos, setShowVideos] = useState(true);
  const [showRtsps, setShowRtsps] = useState(true);
  const [selectedStreams, setSelectedStreams] = useState<Set<string>>(new Set());
  const [isDeleting, setIsDeleting] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [loadingStreamId, setLoadingStreamId] = useState<string | null>(null);

  const isUploadingRef = useRef(false);
  const uploadSessionIdRef = useRef(0);
  const uploadAbortControllerRef = useRef<AbortController | null>(null);
  const pendingFilesQueueRef = useRef<Array<{ id: string; file: File }>>([]);

  useEffect(() => {
    isUploadingRef.current = isUploading;
  }, [isUploading]);

  // Sync display filter state with enabled features so label and filter stay correct
  useEffect(() => {
    if (!enableAddRtspButton) setShowRtsps(false);
  }, [enableAddRtspButton]);
  useEffect(() => {
    if (!enableVideoUpload) setShowVideos(false);
  }, [enableVideoUpload]);

  const { streams, isLoading, error, refetch } = useStreams({ vstApiUrl });
  const { getEndTimeForStream, getLastTimelineForStream, refetch: refetchTimelines } = useStorageTimelines({ vstApiUrl });
  const { videoModal, openVideoModal, closeVideoModal } = useVideoModal(vstApiUrl ?? undefined);

  const filteredStreams = useMemo(
    () => filterStreams(streams, showVideos, showRtsps, appliedSearchQuery),
    [streams, showVideos, showRtsps, appliedSearchQuery]
  );

  const { hasVideoStreams, hasRtspStreams } = useMemo(() => {
    const hasVideo = streams.some((stream) => !isRtspStream(stream));
    const hasRtsp = streams.some(isRtspStream);
    return { hasVideoStreams: hasVideo, hasRtspStreams: hasRtsp };
  }, [streams]);

  const refetchRef = useRef(refetch);
  const refetchTimelinesRef = useRef(refetchTimelines);
  const vstApiUrlRef = useRef(vstApiUrl);

  useEffect(() => {
    refetchRef.current = refetch;
    refetchTimelinesRef.current = refetchTimelines;
  }, [refetch, refetchTimelines]);

  useEffect(() => {
    vstApiUrlRef.current = vstApiUrl;
  }, [vstApiUrl]);

  // Refetch streams when component becomes active
  useEffect(() => {
    if (isActive) {
      refetchRef.current();
      refetchTimelinesRef.current();
    }
  }, [isActive]);

  const processUploadQueue = useCallback(async (fileEntries: Array<{ id: string; file: File; formData?: Record<string, any> }>) => {
    const abortController = new AbortController();
    uploadAbortControllerRef.current = abortController;
    uploadSessionIdRef.current += 1;
    const currentSessionId = uploadSessionIdRef.current;

    setIsUploading(true);
    const isSessionValid = () => uploadSessionIdRef.current === currentSessionId;

    const uploadSingleFile = async (entry: { id: string; file: File; formData?: Record<string, any> }): Promise<void> => {
      const { id, file, formData } = entry;

      if (!isSessionValid() || abortController.signal.aborted) return;

      setUploadProgress((prev) =>
        prev.map((p) => (p.id === id && p.status === 'pending' ? { ...p, status: 'uploading' } : p))
      );

      try {
        if (!vstApiUrl) {
          throw new Error('VST API URL not configured');
        }
        if (!agentApiUrl) {
          throw new Error('Agent API URL not configured');
        }

        // Step 1: Chunked upload directly to the video storage service
        // (bypasses agent, avoids Cloudflare 100s timeout on large files)
        const uploadEndpoints = createApiEndpoints(vstApiUrl);
        const videoUploadApiResponse = await uploadFileChunked({
          file,
          uploadUrl: uploadEndpoints.UPLOAD_FILE,
          onProgress: (progress: number) => {
            if (!isSessionValid() || abortController.signal.aborted) return;
            setUploadProgress((prev) =>
              prev.map((p) => (p.id === id && p.status === 'uploading' ? { ...p, progress } : p))
            );
          },
          abortSignal: abortController.signal,
        });

        if (!isSessionValid()) return;

        // Step 2: Notify agent for post-processing (embeddings, RTVI registration, etc.).
        // We forward the upload response as-is so the agent picks out the fields
        // it cares about; keeps the UI decoupled from the storage API shape.
        setUploadProgress((prev) =>
          prev.map((p) => (p.id === id && p.status === 'uploading' ? { ...p, status: 'processing', progress: 100 } : p))
        );

        // Forward the per-upload custom params collected by the dialog
        // (from chatUploadFileConfigTemplateJson) so the agent can use them
        // downstream. Sent as `custom_params` on the /complete body.
        await notifyUploadComplete(
          agentApiUrl,
          file.name,
          videoUploadApiResponse,
          formData,
          abortController.signal,
        );

        if (!isSessionValid()) return;

        setUploadProgress((prev) =>
          prev.map((p) => (p.id === id && (p.status === 'uploading' || p.status === 'processing') ? {
            ...p,
            status: 'success',
            progress: 100,
          } : p))
        );
      } catch (err) {
        if (!isSessionValid()) return;

        const errorMessage = err instanceof Error ? err.message : 'Upload failed';
        const isCancelled = err instanceof Error && (err.name === 'AbortError' || err.message === 'Upload was cancelled');

        setUploadProgress((prev) =>
          prev.map((p) => (p.id === id && (p.status === 'uploading' || p.status === 'pending' || p.status === 'processing') ? {
            ...p,
            status: isCancelled ? 'cancelled' : 'error',
            error: isCancelled ? undefined : errorMessage
          } : p))
        );
      }
    };

    let entriesToProcess = fileEntries;

    while (entriesToProcess.length > 0) {
      for (let i = 0; i < entriesToProcess.length; i += NUM_PARALLEL_FILE_UPLOADS) {
        if (!isSessionValid()) break;

        const batch = entriesToProcess.slice(i, i + NUM_PARALLEL_FILE_UPLOADS);
        await Promise.allSettled(batch.map((entry) => uploadSingleFile(entry)));
      }

      if (!isSessionValid()) return;

      // Check for any files queued during this batch
      if (pendingFilesQueueRef.current.length > 0) {
        entriesToProcess = [...pendingFilesQueueRef.current];
        pendingFilesQueueRef.current = [];
      } else {
        entriesToProcess = [];
      }
    }

    setIsUploading(false);
    await Promise.all([refetchRef.current(), refetchTimelinesRef.current()]);
  }, [vstApiUrl, agentApiUrl]);

  const handleFilesSelected = useCallback(async (files: File[]) => {
    if (files.length === 0) return;

    // Open dialog for user input (chat-style upload with config fields)
    const newItems = Array.from(files).map((file) => ({
      id: generateFileId(),
      file,
      isExpanded: false,
      formData: generateDefaultFormData(),
    }));
    setSelectedFiles((prev) => [...prev, ...newItems]);
    setShowUploadDialog(true);
  }, [generateFileId, generateDefaultFormData]);

  const uploadProgressRef = useRef<UploadProgress[]>([]);

  useEffect(() => {
    uploadProgressRef.current = uploadProgress;
  }, [uploadProgress]);

  const handleCancelUploads = useCallback(async () => {
    pendingFilesQueueRef.current = [];

    if (uploadAbortControllerRef.current) {
      uploadAbortControllerRef.current.abort();
      uploadAbortControllerRef.current = null;
    }

    uploadSessionIdRef.current += 1;
    const successCount = uploadProgressRef.current.filter((p) => p.status === 'success').length;

    setUploadProgress((prev) =>
      prev.map((p) => (p.status === 'pending' || p.status === 'uploading' || p.status === 'processing' ? { ...p, status: 'cancelled' } : p))
    );
    setIsUploading(false);

    if (successCount > 0) {
      await Promise.all([refetchRef.current(), refetchTimelinesRef.current()]);
    }
  }, []);

  const handleSearch = useCallback(() => {
    const currentValue = searchInputValueRef.current;
    setAppliedSearchQuery(currentValue);
  }, []);

  const handleSearchChange = useCallback((value: string) => {
    searchInputValueRef.current = value;
    setSearchQuery(value);
  }, []);

  // When user clears the search (clear button or deletes all text), apply empty filter so streams show again
  useEffect(() => {
    if (searchQuery === '') {
      searchInputValueRef.current = '';
      setAppliedSearchQuery('');
    }
  }, [searchQuery]);

  const handleClearUploadProgress = useCallback(() => {
    setUploadProgress([]);
  }, []);

  const handleAddRtspClick = () => {
    setIsRtspModalOpen(true);
  };

  const handleRtspDialogClose = () => {
    setIsRtspModalOpen(false);
  };

  const handleRtspSuccess = useCallback(() => {
    refetchRef.current();
    refetchTimelinesRef.current();
  }, []);

  const handlePlayStream = useCallback(async (stream: StreamInfo) => {
    let startTime: string;
    let endTime: string;

    if (isRtspStream(stream)) {
      const now = new Date();
      endTime = new Date(now.getTime() - 5000).toISOString();
      startTime = new Date(now.getTime() - 35000).toISOString();
    } else {
      const range = getLastTimelineForStream(stream.streamId);
      if (!range) return;
      startTime = range.startTime;
      const rangeStart = new Date(range.startTime).getTime();
      const rangeEnd = new Date(range.endTime).getTime();
      endTime =
        rangeEnd - rangeStart > 30000
          ? new Date(rangeStart + 30000).toISOString()
          : range.endTime;
    }

    setLoadingStreamId(stream.streamId);
    try {
      await openVideoModal({
        video_name: stream.name,
        start_time: startTime,
        end_time: endTime,
        sensor_id: stream.sensorId,
      });
    } catch {
      // openVideoModal handles errors internally; catch to prevent unhandled rejection
    } finally {
      setLoadingStreamId(null);
    }
  }, [getLastTimelineForStream, openVideoModal]);

  const handleSelectionChange = useCallback((streamId: string, selected: boolean) => {
    setSelectedStreams((prev) => {
      const next = new Set(prev);
      if (selected) {
        next.add(streamId);
      } else {
        next.delete(streamId);
      }
      return next;
    });
  }, []);

  const handleSelectAll = useCallback((selected: boolean) => {
    if (selected) {
      setSelectedStreams(new Set(filteredStreams.map((s) => s.streamId)));
    } else {
      setSelectedStreams(new Set());
    }
  }, [filteredStreams]);

  const handleDeleteSelected = useCallback(async () => {
    if (selectedStreams.size === 0 || isDeleting) return;

    const selectedStreamIds = Array.from(selectedStreams);

    // Group streams by sensorId and track their info
    const sensorToStreams = new Map<string, StreamInfo[]>();
    for (const streamId of selectedStreamIds) {
      const stream = streams.find(s => s.streamId === streamId);
      if (stream) {
        const existing = sensorToStreams.get(stream.sensorId) || [];
        existing.push(stream);
        sensorToStreams.set(stream.sensorId, existing);
      }
    }

    const uniqueSensorIds = Array.from(sensorToStreams.keys());
    setIsDeleting(true);

    try {
      const deletePromises = uniqueSensorIds.map(async (sensorId) => {
        const sensorStreams = sensorToStreams.get(sensorId) || [];
        const firstStream = sensorStreams[0];
        
        // Check if this is an RTSP stream - must use agent API (by sensor name)
        if (firstStream && isRtspStream(firstStream)) {
          if (!agentApiUrl) {
            throw new Error('Agent API URL not configured for RTSP stream deletion');
          }
          await deleteRtspStream(agentApiUrl, firstStream.name);
          return sensorId;
        }

        // Uploaded videos: use agent delete video API only (same as RTSP - no VST fallback)
        if (!agentApiUrl) {
          throw new Error('Agent API URL not configured for video deletion');
        }
        await deleteVideo(agentApiUrl, sensorId);
        return sensorId;
      });

      await Promise.allSettled(deletePromises);
      setSelectedStreams(new Set());
      await Promise.all([refetch(), refetchTimelines()]);
    } finally {
      setIsDeleting(false);
    }
  }, [selectedStreams, streams, isDeleting, agentApiUrl, refetch, refetchTimelines]);

  const controlsComponent = useMemo(
    () => (
      <VideoManagementSidebarControls
        onFilesSelected={handleFilesSelected}
        enableVideoUpload={enableVideoUpload}
      />
    ),
    [handleFilesSelected, enableVideoUpload]
  );

  useEffect(() => {
    if (onControlsReady && renderControlsInLeftSidebar) {
      onControlsReady({ controlsComponent });
    }
  }, [onControlsReady, renderControlsInLeftSidebar, controlsComponent]);

  const renderMainContent = () => {
    if (isLoading) {
      return <LoadingState />;
    }

    if (error || streams.length === 0) {
      return <EmptyState onFilesSelected={handleFilesSelected} enableVideoUpload={enableVideoUpload} />;
    }

    if (filteredStreams.length === 0) {
      return (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center">
            <p className="text-lg font-medium mb-2 text-gray-600 dark:text-gray-300">
              No streams found
            </p>
            <p className="text-sm text-gray-400 dark:text-gray-500">
              Try adjusting your search or filter criteria
            </p>
          </div>
        </div>
      );
    }

    return (
      <StreamsGrid
        streams={filteredStreams}
        selectedStreams={selectedStreams}
        vstApiUrl={vstApiUrl}
        onSelectionChange={handleSelectionChange}
        onSelectAll={handleSelectAll}
        showVideos={showVideos}
        showRtsps={showRtsps}
        getEndTimeForStream={getEndTimeForStream}
        onPlayStream={handlePlayStream}
        loadingStreamId={loadingStreamId}
        onAddChatQueryContext={addChatQueryContext}
      />
    );
  };

  return (
    <div className="flex-1 flex flex-col h-full bg-gray-50 dark:bg-black text-gray-900 dark:text-gray-100">
      {/* Hidden input for upload dialog add-more */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept=".mp4,.mkv"
        className="hidden"
        onChange={(e) => {
          const files = e.target.files;
          if (files && files.length > 0) {
            const newItems = Array.from(files).map((file) => ({
              id: generateFileId(),
              file,
              isExpanded: false,
              formData: generateDefaultFormData(),
            }));
            setSelectedFiles((prev) => [...prev, ...newItems]);
          }
          if (fileInputRef.current) fileInputRef.current.value = '';
        }}
      />

      {/* Toolbar */}
      <Toolbar
        searchQuery={searchQuery}
        onSearchChange={handleSearchChange}
        onSearch={handleSearch}
        showVideos={showVideos}
        showRtsps={showRtsps}
        onShowVideosChange={setShowVideos}
        onShowRtspsChange={setShowRtsps}
        onFilesSelected={handleFilesSelected}
        onAddRtspClick={handleAddRtspClick}
        selectedCount={selectedStreams.size}
        onDeleteSelected={handleDeleteSelected}
        isDeleting={isDeleting}
        enableAddRtspButton={enableAddRtspButton}
        enableVideoUpload={enableVideoUpload}
        hasVideoStreams={hasVideoStreams}
        hasRtspStreams={hasRtspStreams}
      />

      {/* Main pane: scrollable grid + upload/progress overlays confined to this tab (not full viewport) */}
      <div className="flex flex-1 min-h-0 flex-col relative">
        <div className="flex flex-1 min-h-0 flex-col overflow-auto">{renderMainContent()}</div>

        <AgentUploadDialog
          overlay="contained"
          open={showUploadDialog}
          files={selectedFiles}
          configTemplate={configTemplate}
          onAddMore={() => fileInputRef.current?.click()}
          onFilesDropped={(droppedFiles: File[]) => {
            const newItems = droppedFiles.map((file) => ({
              id: generateFileId(),
              file,
              isExpanded: false,
              formData: generateDefaultFormData(),
            }));
            setSelectedFiles((prev) => [...prev, ...newItems]);
          }}
          onClose={() => {
            setShowUploadDialog(false);
            setSelectedFiles([]);
          }}
          onConfirmUpload={() => {
            if (selectedFiles.length === 0) return;

            const entries = selectedFiles.map((f) => ({
              id: f.id,
              file: f.file,
              formData: f.formData,
            }));

            if (isUploadingRef.current) {
              pendingFilesQueueRef.current.push(...entries);
              const queuedProgress: UploadProgress[] = entries.map((entry) => ({
                id: entry.id,
                fileName: entry.file.name,
                progress: 0,
                status: 'pending' as const,
              }));
              setUploadProgress((prev) => [...prev, ...queuedProgress]);
            } else {
              const initialProgress: UploadProgress[] = entries.map((entry) => ({
                id: entry.id,
                fileName: entry.file.name,
                progress: 0,
                status: 'pending' as const,
              }));
              setUploadProgress(initialProgress);
              processUploadQueue(entries);
            }

            setShowUploadDialog(false);
            setSelectedFiles([]);
          }}
          onToggleExpand={(id: string) =>
            setSelectedFiles((prev) =>
              prev.map((f) => (f.id === id ? { ...f, isExpanded: !f.isExpanded } : f))
            )
          }
          onRemoveFile={(id: string) => setSelectedFiles((prev) => prev.filter((f) => f.id !== id))}
          onFieldChange={(fileId: string, fieldName: string, value: any) =>
            setSelectedFiles((prev) =>
              prev.map((f) =>
                f.id === fileId ? { ...f, formData: { ...f.formData, [fieldName]: value } } : f
              )
            )
          }
        />

        <UploadProgressPanel
          uploads={uploadProgress}
          onClose={handleClearUploadProgress}
          onCancel={handleCancelUploads}
        />
      </div>

      {/* Add RTSP Dialog */}
      <AddRtspDialog
        isOpen={isRtspModalOpen}
        agentApiUrl={agentApiUrl}
        onClose={handleRtspDialogClose}
        onSuccess={handleRtspSuccess}
      />

      {/* Video Playback Modal */}
      <VideoModal
        isOpen={videoModal.isOpen}
        videoUrl={videoModal.videoUrl}
        title={videoModal.title}
        onClose={closeVideoModal}
      />
    </div>
  );
};

// SPDX-License-Identifier: MIT
import React, { useState, useCallback, useMemo, useEffect, useRef } from 'react';
import type { VideoManagementComponentProps, UploadProgress, StreamInfo } from './types';
import { useStreams, useStorageTimelines } from './hooks';
import { filterStreams, isRtspStream } from './utils';
import {
  UploadFilesDialog,
  UploadProgressPopup,
  UploadSuccessPopup,
  VideoModal,
  useVideoModal,
  useChatVideoUploadCompleteSubscription,
  type UploadFilesDialogEntry,
  type UploadFileConfigTemplate,
  type UploadResultItem,
} from '@nemo-agent-toolkit/ui';
import { chunkedUpload, notifyUploadComplete } from './chunkedUpload';
import { createApiEndpoints } from './api';
import { deleteRtspStream } from './rtspStream';
import { deleteVideo } from './videoDelete';
import { NUM_PARALLEL_FILE_UPLOADS } from './constants';
import {
  AddRtspDialog,
  DeleteConfirmDialog,
  EmptyState,
  LoadingState,
  StreamsGrid,
  Toolbar,
  VideoManagementSidebarControls,
} from './components';

export type { VideoManagementComponentProps, VideoManagementSidebarControlHandlers } from './types';

export const VideoManagementComponent: React.FC<VideoManagementComponentProps> = ({
  videoManagementData,
  renderControlsInLeftSidebar = false,
  onControlsReady,
  isActive = true,
  addChatQueryContext,
  registerChatVideoUploadComplete,
}) => {
  const vstApiUrl = videoManagementData?.vstApiUrl;
  const agentApiUrl = videoManagementData?.agentApiUrl;
  const chatUploadFileConfigTemplateJson = videoManagementData?.chatUploadFileConfigTemplateJson;
  const enableAddRtspButton = videoManagementData?.enableAddRtspButton ?? true;
  const enableVideoUpload = videoManagementData?.enableVideoUpload ?? true;

  // Upload dialog state
  const [showUploadDialog, setShowUploadDialog] = useState(false);
  const [pendingInitialFiles, setPendingInitialFiles] = useState<File[] | null>(null);

  // Parse config template from videoManagementData
  const configTemplate = useMemo((): UploadFileConfigTemplate | null => {
    if (chatUploadFileConfigTemplateJson) {
      try {
        return JSON.parse(chatUploadFileConfigTemplateJson);
      } catch (error) {
        console.warn('Failed to parse upload file config template:', error);
      }
    }
    return null;
  }, [chatUploadFileConfigTemplateJson]);

  const [isRtspModalOpen, setIsRtspModalOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [appliedSearchQuery, setAppliedSearchQuery] = useState('');
  const searchInputValueRef = useRef('');
  const [showVideos, setShowVideos] = useState(true);
  const [showRtsps, setShowRtsps] = useState(true);
  const [selectedStreams, setSelectedStreams] = useState<Set<string>>(new Set());
  const [isDeleting, setIsDeleting] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress[]>([]);
  const [showUploadSuccessPopup, setShowUploadSuccessPopup] = useState(false);
  const [uploadResults, setUploadResults] = useState<UploadResultItem[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [loadingStreamId, setLoadingStreamId] = useState<string | null>(null);

  const resolveFilename = useCallback(
    (file: File, uploadFilename?: string) => uploadFilename?.trim() || file.name,
    [],
  );

  const isUploadingRef = useRef(false);
  const uploadSessionIdRef = useRef(0);
  const abortControllerMapRef = useRef<Map<string, AbortController>>(new Map());
  const cancelledFileIdsRef = useRef<Set<string>>(new Set());
  const pendingFilesQueueRef = useRef<Array<{ id: string; file: File; uploadFilename?: string; formData?: Record<string, any> }>>([]);

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

  const refreshStreamsAfterChatUpload = useCallback(() => {
    refetchRef.current();
    refetchTimelinesRef.current();
  }, []);

  useChatVideoUploadCompleteSubscription(
    registerChatVideoUploadComplete,
    refreshStreamsAfterChatUpload,
  );

  const processUploadQueue = useCallback(async (fileEntries: Array<{ id: string; file: File; uploadFilename?: string; formData?: Record<string, any> }>) => {
    uploadSessionIdRef.current += 1;
    const currentSessionId = uploadSessionIdRef.current;

    setIsUploading(true);
    const isSessionValid = () => uploadSessionIdRef.current === currentSessionId;

    const uploadSingleFile = async (entry: { id: string; file: File; uploadFilename?: string; formData?: Record<string, any> }): Promise<void> => {
      const { id, file, uploadFilename, formData } = entry;
      const requestFilename = resolveFilename(file, uploadFilename);

      if (!isSessionValid() || cancelledFileIdsRef.current.has(id)) return;

      const abortController = new AbortController();
      abortControllerMapRef.current.set(id, abortController);

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

        const uploadEndpoints = createApiEndpoints(vstApiUrl);
        const videoUploadApiResponse = await chunkedUpload({
          file,
          fileName: requestFilename,
          uploadUrl: uploadEndpoints.UPLOAD_FILE,
          onProgress: (progress: number) => {
            if (!isSessionValid() || abortController.signal.aborted) return;
            setUploadProgress((prev) =>
              prev.map((p) => (p.id === id && p.status === 'uploading' ? { ...p, progress } : p))
            );
          },
          abortSignal: abortController.signal,
        });

        if (!isSessionValid() || cancelledFileIdsRef.current.has(id)) return;

        setUploadProgress((prev) =>
          prev.map((p) => (p.id === id && p.status === 'uploading' ? { ...p, status: 'processing', progress: 100 } : p))
        );

        await notifyUploadComplete(
          agentApiUrl,
          requestFilename,
          videoUploadApiResponse,
          formData,
          abortController.signal,
        );

        if (!isSessionValid() || cancelledFileIdsRef.current.has(id)) return;

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
        const isAborted = err instanceof Error && (err.name === 'AbortError' || err.message === 'Upload was cancelled');
        const isCancelled = isAborted || cancelledFileIdsRef.current.has(id);

        setUploadProgress((prev) =>
          prev.map((p) => (p.id === id && (p.status === 'uploading' || p.status === 'pending' || p.status === 'processing') ? {
            ...p,
            status: isCancelled ? 'cancelled' : 'error',
            error: isCancelled ? undefined : errorMessage
          } : p))
        );
      } finally {
        abortControllerMapRef.current.delete(id);
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

      if (pendingFilesQueueRef.current.length > 0) {
        entriesToProcess = [...pendingFilesQueueRef.current];
        pendingFilesQueueRef.current = [];
      } else {
        entriesToProcess = [];
      }
    }

    setIsUploading(false);
    await Promise.all([refetchRef.current(), refetchTimelinesRef.current()]);
  }, [vstApiUrl, agentApiUrl, resolveFilename]);

  const handleFilesSelected = useCallback((files: File[]) => {
    if (files.length === 0) return;
    setPendingInitialFiles(Array.from(files));
    setShowUploadDialog(true);
  }, []);

  const handleUploadClick = useCallback(() => {
    setPendingInitialFiles(null);
    setShowUploadDialog(true);
  }, []);

  const handleUploadDialogClose = useCallback(() => {
    setShowUploadDialog(false);
    setPendingInitialFiles(null);
  }, []);

  const handleUploadConfirm = useCallback((entries: UploadFilesDialogEntry[]) => {
    if (entries.length === 0) return;
    setShowUploadSuccessPopup(false);
    setUploadResults([]);
    cancelledFileIdsRef.current.clear();

    const fileEntries = entries.map((e) => ({
      id: e.id,
      file: e.file,
      uploadFilename: e.uploadFilename,
      formData: e.formData,
    }));

    if (isUploadingRef.current) {
      pendingFilesQueueRef.current.push(...fileEntries);
      const queuedProgress: UploadProgress[] = fileEntries.map((entry) => ({
        id: entry.id,
        fileName: resolveFilename(entry.file, entry.uploadFilename),
        progress: 0,
        status: 'pending' as const,
      }));
      setUploadProgress((prev) => [...prev, ...queuedProgress]);
    } else {
      const initialProgress: UploadProgress[] = fileEntries.map((entry) => ({
        id: entry.id,
        fileName: resolveFilename(entry.file, entry.uploadFilename),
        progress: 0,
        status: 'pending' as const,
      }));
      setUploadProgress(initialProgress);
      processUploadQueue(fileEntries);
    }
  }, [processUploadQueue, resolveFilename]);

  const uploadProgressRef = useRef<UploadProgress[]>([]);

  useEffect(() => {
    uploadProgressRef.current = uploadProgress;
  }, [uploadProgress]);

  const handleCancelSingleUpload = useCallback((fileId: string) => {
    cancelledFileIdsRef.current.add(fileId);
    abortControllerMapRef.current.get(fileId)?.abort();
    abortControllerMapRef.current.delete(fileId);

    setUploadProgress((prev) =>
      prev.map((p) =>
        p.id === fileId && (p.status === 'pending' || p.status === 'uploading' || p.status === 'processing')
          ? { ...p, status: 'cancelled' }
          : p,
      ),
    );
  }, []);

  const handleCancelAllUploads = useCallback(async () => {
    pendingFilesQueueRef.current = [];
    uploadSessionIdRef.current += 1;

    abortControllerMapRef.current.forEach((ctrl) => ctrl.abort());
    abortControllerMapRef.current.clear();

    setUploadProgress((prev) => {
      prev.forEach((p) => {
        if (p.status === 'pending' || p.status === 'uploading' || p.status === 'processing') {
          cancelledFileIdsRef.current.add(p.id);
        }
      });
      return prev.map((p) =>
        p.status === 'pending' || p.status === 'uploading' || p.status === 'processing'
          ? { ...p, status: 'cancelled' }
          : p,
      );
    });
    setIsUploading(false);

    const successCount = uploadProgressRef.current.filter((p) => p.status === 'success').length;
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
    setUploadResults([]);
    setShowUploadSuccessPopup(false);
  }, []);

  const uploadProgressPopupFiles = useMemo(
    () =>
      uploadProgress.map((upload) => ({
        id: upload.id,
        displayName: upload.fileName,
        uploadProgress: upload.status === 'processing' ? 100 : upload.progress,
        uploadStatus: upload.status === 'processing' ? 'uploading' as const : upload.status as Exclude<UploadProgress['status'], 'processing'>,
        uploadError: upload.error,
      })),
    [uploadProgress],
  );

  const hasActiveUploads = useMemo(
    () => uploadProgress.some((u) => u.status === 'pending' || u.status === 'uploading' || u.status === 'processing'),
    [uploadProgress],
  );

  useEffect(() => {
    if (uploadProgress.length === 0 || hasActiveUploads || showUploadSuccessPopup) return;

    const results: UploadResultItem[] = uploadProgress.map((u) => {
      if (u.status === 'success') {
        return { filename: u.fileName, result: { status: 'success' } };
      }
      if (u.status === 'cancelled') {
        return { filename: u.fileName, cancelled: true };
      }
      return { filename: u.fileName, error: u.error ?? 'Upload failed' };
    });

    setUploadResults(results);
    setShowUploadSuccessPopup(true);
  }, [uploadProgress, hasActiveUploads, showUploadSuccessPopup]);

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
      endTime = range.endTime;
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

  // Resolve selected stream IDs back to full StreamInfo objects so the confirm
  // dialog can show the user exactly which items are about to be deleted.
  const selectedStreamInfos = useMemo(
    () => streams.filter((s) => selectedStreams.has(s.streamId)),
    [streams, selectedStreams]
  );

  // Step 1 of delete: just open the confirmation dialog. The Toolbar's "Delete
  // Selected" button is wired to this so a single click never destroys data.
  const handleDeleteSelected = useCallback(() => {
    if (selectedStreams.size === 0 || isDeleting) return;
    setShowDeleteConfirm(true);
  }, [selectedStreams.size, isDeleting]);

  const handleCancelDelete = useCallback(() => {
    if (isDeleting) return;
    setShowDeleteConfirm(false);
  }, [isDeleting]);

  // Step 2 of delete: invoked by the confirm button inside DeleteConfirmDialog.
  // This holds the actual destructive API calls that used to live in
  // handleDeleteSelected.
  const handleConfirmDelete = useCallback(async () => {
    if (selectedStreams.size === 0 || isDeleting) return;

    const selectedStreamIds = Array.from(selectedStreams);

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

      const results = await Promise.allSettled(deletePromises);
      results.forEach((r, idx) => {
        if (r.status === 'rejected') {
          // eslint-disable-next-line no-console
          console.error('[VideoManagement] delete failed for sensor', uniqueSensorIds[idx], r.reason);
        }
      });
      setSelectedStreams(new Set());
      await Promise.all([refetch(), refetchTimelines()]);
    } finally {
      setIsDeleting(false);
      setShowDeleteConfirm(false);
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
    <div className="flex h-full min-h-0 min-w-0 max-w-full flex-1 flex-col bg-gray-50 text-gray-900 dark:bg-black dark:text-gray-100">
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
        onUploadClick={handleUploadClick}
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

        <UploadFilesDialog
          open={showUploadDialog}
          configTemplate={configTemplate}
          onClose={handleUploadDialogClose}
          onConfirm={handleUploadConfirm}
          initialFiles={pendingInitialFiles}
        />

        {uploadProgress.length > 0 && hasActiveUploads && (
          <UploadProgressPopup
            files={uploadProgressPopupFiles}
            onCancelAll={handleCancelAllUploads}
            onCancelSingle={handleCancelSingleUpload}
          />
        )}

        {showUploadSuccessPopup && uploadResults.length > 0 && (
          <UploadSuccessPopup
            results={uploadResults}
            onClose={handleClearUploadProgress}
          />
        )}

        <AddRtspDialog
          overlay="contained"
          isOpen={isRtspModalOpen}
          agentApiUrl={agentApiUrl}
          onClose={handleRtspDialogClose}
          onSuccess={handleRtspSuccess}
        />

        <DeleteConfirmDialog
          overlay="contained"
          isOpen={showDeleteConfirm}
          streams={selectedStreamInfos}
          isDeleting={isDeleting}
          onCancel={handleCancelDelete}
          onConfirm={handleConfirmDelete}
        />
      </div>

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

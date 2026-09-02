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
import { chunkedUpload } from './chunkedUpload';
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
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress[]>([]);
  const [showUploadSuccessPopup, setShowUploadSuccessPopup] = useState(false);
  const [uploadResults, setUploadResults] = useState<UploadResultItem[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [loadingStreamId, setLoadingStreamId] = useState<string | null>(null);

  // Only one dialog may be open at a time. The RTSP and delete dialogs use a
  // `contained` overlay that covers the pane but not the toolbar above it, so their
  // trigger buttons stay live and a second dialog could otherwise be opened and end
  // up stacked behind the first, unreachable until the top one is closed.
  const isDialogOpen = showUploadDialog || isRtspModalOpen || showDeleteConfirm;
  const isDialogOpenRef = useRef(isDialogOpen);

  useEffect(() => {
    isDialogOpenRef.current = isDialogOpen;
  }, [isDialogOpen]);

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

  const { streams, isLoading, error, refetch, waitUntilStreamsRemoved } = useStreams({ vstApiUrl });
  const {
    getEndTimeForStream,
    getTimelineRangeForStream,
    getLastTimelineForStream,
    refetch: refetchTimelines,
  } = useStorageTimelines({ vstApiUrl });
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
      const { id, file, uploadFilename } = entry;
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
        const uploadEndpoints = createApiEndpoints(vstApiUrl);
        await chunkedUpload({
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
  }, [vstApiUrl, resolveFilename]);

  const handleFilesSelected = useCallback((files: File[]) => {
    if (files.length === 0 || isDialogOpenRef.current) return;
    setPendingInitialFiles(Array.from(files));
    setShowUploadDialog(true);
  }, []);

  const handleUploadClick = useCallback(() => {
    if (isDialogOpenRef.current) return;
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
    if (isDialogOpenRef.current) return;
    setIsRtspModalOpen(true);
  };

  const handleRtspDialogClose = () => {
    setIsRtspModalOpen(false);
  };

  const handleRtspSuccess = useCallback(() => {
    refetchRef.current();
    refetchTimelinesRef.current();
  }, []);

  const handlePlayStream = useCallback(async (stream: StreamInfo): Promise<boolean> => {
    let startTime: string;
    let endTime: string;

    if (isRtspStream(stream)) {
      const now = new Date();
      endTime = new Date(now.getTime() - 5000).toISOString();
      startTime = new Date(now.getTime() - 35000).toISOString();
    } else {
      const range = getLastTimelineForStream(stream.streamId);
      if (!range) return false;
      startTime = range.startTime;
      endTime = range.endTime;
    }

    setLoadingStreamId(stream.streamId);
    try {
      return await openVideoModal({
        video_name: stream.name,
        start_time: startTime,
        end_time: endTime,
        sensor_id: stream.sensorId,
      });
    } catch {
      // openVideoModal signals failure through its return value; catch guards
      // against anything unexpected escaping as an unhandled rejection.
      return false;
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

  // Sensors VST already accepted a delete for, but still lists. This
  // tracks backend state, not dialog state: any later attempt on these must only
  // resume polling, since re-sending the destructive request would either fail
  // against an already-deleted sensor or hit a resource recreated under the same
  // identity. Cancelling the dialog therefore must not clear it.
  const acceptedDeletesRef = useRef<Set<string>>(new Set());

  // An acknowledgement only describes the backend that issued it. Once the tab points at
  // a different VST, a sensor id reused over there has not been deleted, so keeping
  // the entry would skip the new backend's delete call and poll until timeout instead.
  // The counter lets a delete already in flight recognise that its result arrived too
  // late to be recorded, which clearing alone cannot prevent.
  const backendSessionRef = useRef(0);

  useEffect(() => {
    backendSessionRef.current += 1;
    acceptedDeletesRef.current.clear();
  }, [vstApiUrl]);

  // Once VST stops listing a sensor the delete is fully settled, so drop it here.
  // Without this, a stream later recreated under the same sensor id could never be
  // deleted — every attempt would skip the VST call and just poll forever.
  useEffect(() => {
    if (acceptedDeletesRef.current.size === 0) return;
    const listed = new Set(streams.map((s) => s.sensorId));
    for (const sensorId of Array.from(acceptedDeletesRef.current)) {
      if (!listed.has(sensorId)) acceptedDeletesRef.current.delete(sensorId);
    }
  }, [streams]);

  // Step 1 of delete: just open the confirmation dialog. The Toolbar's "Delete
  // Selected" button is wired to this so a single click never destroys data.
  const handleDeleteSelected = useCallback(() => {
    if (selectedStreams.size === 0 || isDeleting || isDialogOpenRef.current) return;
    setDeleteError(null);
    setShowDeleteConfirm(true);
  }, [selectedStreams.size, isDeleting]);

  const handleCancelDelete = useCallback(() => {
    if (isDeleting) return;
    setDeleteError(null);
    setShowDeleteConfirm(false);
  }, [isDeleting]);

  // Step 2 of delete: invoked by the confirm button inside DeleteConfirmDialog.
  // Keeps the dialog open through agent delete + VST stream-list convergence
  // (NVBug 6243148). Only closes when VST no longer lists the deleted sensors;
  // otherwise surfaces which streams could not be removed so the user can retry.
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

    const backendSession = backendSessionRef.current;
    const isSameBackend = () => backendSessionRef.current === backendSession;

    const uniqueSensorIds = Array.from(sensorToStreams.keys());
    // Retry after a convergence timeout: the agent already took these, so only
    // the VST wait below needs repeating.
    const alreadyAcceptedSensorIds = uniqueSensorIds.filter((id) => acceptedDeletesRef.current.has(id));
    const sensorIdsToDelete = uniqueSensorIds.filter((id) => !acceptedDeletesRef.current.has(id));

    setIsDeleting(true);
    setDeleteError(null);

    try {
      const deletePromises = sensorIdsToDelete.map(async (sensorId) => {
        const sensorStreams = sensorToStreams.get(sensorId) || [];
        const firstStream = sensorStreams[0];

        if (!vstApiUrl) {
          throw new Error('VST API URL not configured for deletion');
        }

        // RTSP streams have no uploaded-file storage to remove.
        if (firstStream && isRtspStream(firstStream)) {
          await deleteRtspStream(vstApiUrl, sensorId);
          return sensorId;
        }

        const timelineRange = sensorStreams
          .map((stream) => getTimelineRangeForStream(stream.streamId))
          .find((range) => range !== null);
        if (!timelineRange) {
          throw new Error(`No storage timeline found for ${firstStream?.name || sensorId}`);
        }
        await deleteVideo(
          vstApiUrl,
          sensorId,
          timelineRange.startTime,
          timelineRange.endTime,
        );
        return sensorId;
      });

      const results = await Promise.allSettled(deletePromises);

      // These answers came from the backend we have since left. Recording them would let
      // a same-id stream on the current one be treated as already accepted, so drop the
      // whole outcome and leave the dialog open to retry against the backend in use.
      if (!isSameBackend()) return;

      const deletedSensorIds: string[] = [...alreadyAcceptedSensorIds];
      const failedNames: string[] = [];
      const stillSelected = new Set<string>();

      const nameFor = (sensorId: string) =>
        sensorToStreams.get(sensorId)?.[0]?.name ?? sensorId;
      const keepSelected = (sensorId: string) =>
        (sensorToStreams.get(sensorId) || []).forEach((s) => stillSelected.add(s.streamId));

      results.forEach((result, idx) => {
        const sensorId = sensorIdsToDelete[idx];

        if (result.status === 'fulfilled') {
          deletedSensorIds.push(sensorId);
          return;
        }

        // Keep failures selected so the confirm dialog's retry acts on exactly them
        failedNames.push(nameFor(sensorId));
        keepSelected(sensorId);
        // eslint-disable-next-line no-console
        console.error('[VideoManagement] delete failed for sensor', sensorId, result.reason);
      });

      deletedSensorIds.forEach((sensorId) => acceptedDeletesRef.current.add(sensorId));

      // VST accepted the delete — wait until its streams list agrees before
      // claiming success. Closing early is what left RTSP entries stale in the grid.
      const { remainingSensorIds } = await waitUntilStreamsRemoved(deletedSensorIds);
      if (!isSameBackend()) return;
      void refetchTimelines();

      const unconfirmed = new Set(remainingSensorIds);
      deletedSensorIds.forEach((sensorId) => {
        if (!unconfirmed.has(sensorId)) acceptedDeletesRef.current.delete(sensorId);
      });

      const unconfirmedNames: string[] = [];
      for (const sensorId of remainingSensorIds) {
        unconfirmedNames.push(nameFor(sensorId));
        keepSelected(sensorId);
      }

      setSelectedStreams(stillSelected);

      if (failedNames.length > 0 || unconfirmedNames.length > 0) {
        const messages: string[] = [];
        if (failedNames.length > 0) {
          messages.push(`Unable to remove the following streams: ${failedNames.join(', ')}`);
        }
        if (unconfirmedNames.length > 0) {
          messages.push(
            `Deletion was accepted but these are still listed by VST: ${unconfirmedNames.join(', ')}. Retry to check again.`
          );
        }
        setDeleteError(messages.join('\n'));
        return;
      }

      setShowDeleteConfirm(false);
    } finally {
      setIsDeleting(false);
    }
  }, [
    selectedStreams,
    streams,
    isDeleting,
    vstApiUrl,
    getTimelineRangeForStream,
    waitUntilStreamsRemoved,
    refetchTimelines,
  ]);

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
        isDialogOpen={isDialogOpen}
      />

      {/* Main pane: scrollable grid + upload/progress overlays confined to this tab (not full viewport) */}
      <div className="flex flex-1 min-h-0 flex-col relative">
        <div className="flex flex-1 min-h-0 flex-col overflow-auto">{renderMainContent()}</div>

        <UploadFilesDialog
          overlay="contained"
          open={showUploadDialog}
          configTemplate={configTemplate}
          onClose={handleUploadDialogClose}
          onConfirm={handleUploadConfirm}
          initialFiles={pendingInitialFiles}
        />

        {uploadProgress.length > 0 && hasActiveUploads && (
          <UploadProgressPopup
            overlay="contained"
            files={uploadProgressPopupFiles}
            onCancelAll={handleCancelAllUploads}
            onCancelSingle={handleCancelSingleUpload}
          />
        )}

        {showUploadSuccessPopup && uploadResults.length > 0 && (
          <UploadSuccessPopup
            overlay="contained"
            results={uploadResults}
            onClose={handleClearUploadProgress}
          />
        )}

        <AddRtspDialog
          overlay="contained"
          isOpen={isRtspModalOpen}
          vstApiUrl={vstApiUrl}
          onClose={handleRtspDialogClose}
          onSuccess={handleRtspSuccess}
        />

        <DeleteConfirmDialog
          overlay="contained"
          isOpen={showDeleteConfirm}
          streams={selectedStreamInfos}
          isDeleting={isDeleting}
          error={deleteError}
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

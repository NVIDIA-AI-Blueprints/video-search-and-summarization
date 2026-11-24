/*
* SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
* SPDX-License-Identifier: Apache-2.0
*
* Licensed under the Apache License, Version 2.0 (the "License");
* you may not use this file except in compliance with the License.
* You may obtain a copy of the License at
*
* http://www.apache.org/licenses/LICENSE-2.0
*
* Unless required by applicable law or agreed to in writing, software
* distributed under the License is distributed on an "AS IS" BASIS,
* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
* See the License for the specific language governing permissions and
* limitations under the License.
*/

/**
 * Video Configuration Step
 * Upload videos, ground truth, and layout data
 */

import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  Box,
  Typography,
  Button,
  Card,
  CardContent,
  Grid,
  List,
  ListItem,
  ListItemText,
  ListItemSecondaryAction,
  IconButton,
  LinearProgress,
  Alert,
  Chip,
  Paper,
  Divider,
  TextField,
  Stack,
} from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import CloudUploadIcon from '@mui/icons-material/CloudUpload';
import VideoFileIcon from '@mui/icons-material/VideoFile';
import ImageIcon from '@mui/icons-material/Image';
import FolderZipIcon from '@mui/icons-material/FolderZip';
import DescriptionIcon from '@mui/icons-material/Description';
import DragIndicatorIcon from '@mui/icons-material/DragIndicator';
import VideocamIcon from '@mui/icons-material/Videocam';
import AddIcon from '@mui/icons-material/Add';
import StopIcon from '@mui/icons-material/Stop';
import RefreshIcon from '@mui/icons-material/Refresh';
import { useApp } from '../../context/AppContext';
import { api } from '../../services/api';
import {
  RTSP_MIN_DURATION_SECONDS,
  RTSP_MAX_DURATION_SECONDS,
  REQUIRED_VIDEO_WIDTH,
  REQUIRED_VIDEO_HEIGHT,
} from '../../constants';
import { partitionVideosByResolution } from '../../utils/validation';
import {
  ProjectState,
  type Project,
  type RtspCaptureSession,
  type RtspStreamConfigInput,
} from '../../types';

/** Ingest order matches `sensor_ids` / `streams` index order on the server. */
function formatRtspIngestedDisplay(session: RtspCaptureSession): string {
  const files = session.ingested_files ?? [];
  if (files.length === 0) return '';
  const streams = session.streams ?? [];
  return files
    .map((filename, idx) => {
      const raw = streams[idx]?.camera_name;
      const friendly = typeof raw === 'string' && raw.trim() ? raw.trim() : '';
      return friendly || filename;
    })
    .join(', ');
}

/** Compact duration label for RTSP capture progress (non-negative seconds). */
function formatRtspSeconds(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return '0s';
  const whole = Math.floor(sec);
  const m = Math.floor(whole / 60);
  const s = whole % 60;
  if (m <= 0) return `${s}s`;
  return `${m}m ${s}s`;
}

/** Wall-clock capture span when session finished with both timestamps from server. */
function rtspCapturedWindowSeconds(session: RtspCaptureSession | null): number | null {
  if (!session?.recording_start_time || !session.recording_end_time) return null;
  const a = Date.parse(session.recording_start_time);
  const b = Date.parse(session.recording_end_time);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
  return Math.max(0, Math.round((b - a) / 1000));
}

interface RtspStreamRow {
  id: string;
  rtsp_url: string;
  camera_name: string;
  sensor_id: string;
}

const newRtspStreamRow = (): RtspStreamRow => ({
  id: `r_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`,
  rtsp_url: '',
  camera_name: '',
  sensor_id: '',
});

function rtspStreamRowFromConfig(st: RtspStreamConfigInput, rowId: string): RtspStreamRow {
  return {
    id: rowId,
    rtsp_url: st.rtsp_url ?? '',
    camera_name: typeof st.camera_name === 'string' ? st.camera_name : '',
    sensor_id: typeof st.sensor_id === 'string' ? st.sensor_id : '',
  };
}

function streamRowToApiConfig(row: RtspStreamRow): RtspStreamConfigInput {
  return {
    rtsp_url: row.rtsp_url.trim(),
    ...(row.camera_name.trim() ? { camera_name: row.camera_name.trim() } : {}),
    ...(row.sensor_id.trim() ? { sensor_id: row.sensor_id.trim() } : {}),
  };
}

/** One-line stream summary for session list / debug (request + resolved VIOS id). */
function formatRtspStreamLine(
  st: RtspStreamConfigInput,
  streamIndex: number,
  resolvedSensorId?: string,
): string {
  const name = typeof st.camera_name === 'string' ? st.camera_name.trim() : '';
  const prefix = name ? `${name}: ` : `Stream ${streamIndex + 1}: `;
  const requestedSid = typeof st.sensor_id === 'string' ? st.sensor_id.trim() : '';
  const resolved = typeof resolvedSensorId === 'string' ? resolvedSensorId.trim() : '';
  const parts: string[] = [];
  if (requestedSid) {
    parts.push(`sensor_id ${requestedSid}`);
  } else if (resolved) {
    parts.push(`sensor ${resolved}`);
  }
  if (st.rtsp_url?.trim()) {
    parts.push(st.rtsp_url.trim());
  }
  return prefix + (parts.length > 0 ? parts.join(' · ') : '(no stream details)');
}

const RTSP_TERMINAL_STATUSES = ['CANCELLED', 'INGESTED', 'ERROR'] as const;

/** Server-side work is still in flight for these statuses. */
const RTSP_LIVE_STATUSES = ['STARTING', 'RECORDING', 'STOPPING', 'INGESTING'] as const;

function isLiveRtspSession(session: RtspCaptureSession | null | undefined): boolean {
  if (!session) return false;
  return RTSP_LIVE_STATUSES.includes(
    session.status as (typeof RTSP_LIVE_STATUSES)[number],
  );
}

/** Newest still-running session, so a remount can resume tracking it. */
function latestLiveRtspSession(
  sessions: RtspCaptureSession[],
): RtspCaptureSession | null {
  const live = sessions.filter(isLiveRtspSession);
  if (live.length === 0) return null;
  return live.reduce((newest, candidate) =>
    Date.parse(candidate.created_at) > Date.parse(newest.created_at) ? candidate : newest,
  );
}

interface VideoConfigurationProps {
  project: Project;
  onProjectUpdate: (project: Project) => void;
}

interface VideoUploadAlert {
  message: string;
  severity: 'error' | 'warning';
}

const VideoConfiguration: React.FC<VideoConfigurationProps> = ({
  project,
  onProjectUpdate,
}) => {
  const { showSuccess, showError, rtspSupported } = useApp();
  const calibrationRunning = project.project_state === ProjectState.RUNNING;
  const [uploading, setUploading] = useState(false);
  const [videoUploading, setVideoUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [videoFiles, setVideoFiles] = useState<string[]>([]);
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [videoUploadAlert, setVideoUploadAlert] = useState<VideoUploadAlert | null>(null);
  const [dragIndex, setDragIndex] = useState<number | null>(null);

  const [rtspRows, setRtspRows] = useState<RtspStreamRow[]>(() => [newRtspStreamRow()]);
  // Kept as text so the field can be cleared while typing (a numeric 0 default would stick as "0600").
  const [durationSeconds, setDurationSeconds] = useState('300');
  const [rtspActiveSessionId, setRtspActiveSessionId] = useState<string | null>(null);
  const [rtspSessionSnapshot, setRtspSessionSnapshot] = useState<RtspCaptureSession | null>(null);
  const [rtspBusy, setRtspBusy] = useState(false);
  const [rtspSessionsList, setRtspSessionsList] = useState<RtspCaptureSession[]>([]);
  const [rtspListLoaded, setRtspListLoaded] = useState(false);
  /** Drives sub-minute UI updates so "Stop early" enables when min duration is met (status poll is 2s). */
  const [rtspStopEarlyClock, setRtspStopEarlyClock] = useState(0);
  /** Hydrate stream rows from server session once per active session (e.g. after page reload). */
  const rtspRowsHydratedForSessionRef = useRef<string | null>(null);

  const rtspStatus = rtspSessionSnapshot?.status;

  useEffect(() => {
    setVideoUploadAlert(null);
    loadVideoFiles();
  }, [project.project_id]);

  useEffect(() => {
    if (!rtspSupported) {
      return undefined;
    }
    if (!rtspActiveSessionId) {
      setRtspSessionSnapshot(null);
      return undefined;
    }
    let cancelled = false;
    let intervalId: ReturnType<typeof setInterval> | undefined;

    const tick = async () => {
      try {
        const res = await api.rtsp.getCaptureStatus(project.project_id, rtspActiveSessionId);
        if (cancelled) return;
        setRtspSessionSnapshot(res.session);
        if (RTSP_TERMINAL_STATUSES.includes(res.session.status as (typeof RTSP_TERMINAL_STATUSES)[number])) {
          if (intervalId) clearInterval(intervalId);
          if (res.session.status === 'INGESTED') {
            void api.data.listVideoFiles(project.project_id)
              .then(files => setVideoFiles(files.video_files))
              .catch((error: unknown) => {
                const apiError = error as { message?: string; detail?: string };
                showError(apiError.detail || apiError.message || 'Failed to refresh video files after RTSP ingest');
              });
            void api.project.getProjectInfo(project.project_id)
              .then(info => onProjectUpdate(info.project_info))
              .catch((error: unknown) => {
                const apiError = error as { message?: string; detail?: string };
                showError(apiError.detail || apiError.message || 'Failed to refresh project after RTSP ingest');
              });
          }
          // Every terminal status, not just ingest: the listed sessions gate the RTSP
          // controls, so a stale running entry would keep capture locked.
          void refreshRtspSessionsList().then(refreshed => {
            if (!refreshed) {
              showError('Failed to refresh RTSP sessions');
            }
          });
        }
      } catch {
        if (intervalId) clearInterval(intervalId);
      }
    };

    void tick();
    intervalId = setInterval(tick, 2000);
    return () => {
      cancelled = true;
      if (intervalId) clearInterval(intervalId);
    };
  }, [rtspSupported, rtspActiveSessionId, project.project_id]);

  useEffect(() => {
    if (!rtspActiveSessionId) {
      rtspRowsHydratedForSessionRef.current = null;
      return;
    }
    if (rtspRowsHydratedForSessionRef.current === rtspActiveSessionId) {
      return;
    }
    const streams = rtspSessionSnapshot?.streams;
    if (!streams?.length) {
      return;
    }
    rtspRowsHydratedForSessionRef.current = rtspActiveSessionId;
    setRtspRows(
      streams.map((st, i) => rtspStreamRowFromConfig(st, `r_${rtspActiveSessionId}_${i}`)),
    );
    if (typeof rtspSessionSnapshot?.duration_seconds === 'number') {
      setDurationSeconds(String(rtspSessionSnapshot.duration_seconds));
    }
  }, [rtspActiveSessionId, rtspSessionSnapshot?.streams, rtspSessionSnapshot?.duration_seconds]);

  useEffect(() => {
    if (!rtspSupported || !rtspActiveSessionId || !rtspSessionSnapshot) {
      return undefined;
    }
    const { status } = rtspSessionSnapshot;
    const needTicker =
      status === 'STARTING' ||
      status === 'RECORDING' ||
      status === 'STOPPING' ||
      status === 'INGESTING';
    if (!needTicker) {
      return undefined;
    }
    const id = window.setInterval(() => setRtspStopEarlyClock(c => c + 1), 1000);
    return () => window.clearInterval(id);
  }, [
    rtspSupported,
    rtspActiveSessionId,
    rtspSessionSnapshot?.status,
    rtspSessionSnapshot?.recording_start_time,
  ]);

  const rtspRecordingElapsedSeconds = useMemo(() => {
    const rst = rtspSessionSnapshot?.recording_start_time;
    if (!rst) return 0;
    const t = Date.parse(rst);
    if (!Number.isFinite(t)) return 0;
    return Math.max(0, Math.floor((Date.now() - t) / 1000));
  }, [rtspSessionSnapshot?.recording_start_time, rtspStopEarlyClock]);

  const rtspCaptureTotalSeconds = useMemo(() => {
    const ds = rtspSessionSnapshot?.duration_seconds;
    if (typeof ds === 'number' && Number.isFinite(ds) && ds > 0) {
      return Math.max(1, Math.floor(ds));
    }
    const df = Math.floor(Number(durationSeconds));
    return Math.max(1, df >= RTSP_MIN_DURATION_SECONDS ? df : RTSP_MIN_DURATION_SECONDS);
  }, [rtspSessionSnapshot?.duration_seconds, durationSeconds]);

  const rtspCaptureProgressPercent = useMemo(() => {
    if (rtspStatus !== 'RECORDING') return 0;
    return Math.min(
      100,
      Math.round((rtspRecordingElapsedSeconds / rtspCaptureTotalSeconds) * 100),
    );
  }, [rtspStatus, rtspRecordingElapsedSeconds, rtspCaptureTotalSeconds]);

  const rtspShowLiveCaptureProgress =
    !!rtspActiveSessionId && isLiveRtspSession(rtspSessionSnapshot);

  const rtspCompletedWindowSec = rtspCapturedWindowSeconds(rtspSessionSnapshot);

  const refreshRtspSessionsList = useCallback(async (): Promise<boolean> => {
    try {
      const res = await api.rtsp.listSessions(project.project_id);
      setRtspSessionsList(res.sessions);
      setRtspListLoaded(true);
      // Capture runs on the server, but the active session was only tracked in this
      // component. Re-adopt a still-running session so leaving and returning to this
      // step (or reloading) keeps live progress, Stop early, and the post-ingest
      // project refresh working instead of offering Start capture again.
      const live = latestLiveRtspSession(res.sessions);
      if (live) {
        setRtspActiveSessionId(previous => previous ?? live.session_id);
        setRtspSessionSnapshot(previous => previous ?? live);
      }
      return true;
    } catch {
      setRtspSessionsList([]);
      setRtspListLoaded(true);
      return false;
    }
  }, [project.project_id]);

  /**
   * Runs on mount so an in-progress capture is resumed and RTSP-ingested clips are
   * not mistaken for manual uploads. Not gated on existing videos: the first capture
   * of a project has none yet while it is still recording.
   */
  useEffect(() => {
    if (!rtspSupported) return;
    void refreshRtspSessionsList();
  }, [rtspSupported, project.project_id, videoFiles.length, refreshRtspSessionsList]);

  const handleRtspStart = async () => {
    const streams = rtspRows.filter(r => r.rtsp_url.trim()).map(streamRowToApiConfig);
    if (streams.length === 0) {
      showError('Add at least one RTSP URL.');
      return;
    }
    const dur = Math.floor(Number(durationSeconds));
    if (
      !Number.isFinite(dur) ||
      dur < RTSP_MIN_DURATION_SECONDS ||
      dur > RTSP_MAX_DURATION_SECONDS
    ) {
      showError(
        `Duration must be ${RTSP_MIN_DURATION_SECONDS}-${RTSP_MAX_DURATION_SECONDS} seconds (server requirement).`,
      );
      return;
    }
    setVideoUploadAlert(null);
    setRtspBusy(true);
    try {
      const res = await api.rtsp.startCapture(project.project_id, {
        streams,
        duration_seconds: dur,
      });
      setRtspActiveSessionId(res.session.session_id);
      rtspRowsHydratedForSessionRef.current = res.session.session_id;
      setRtspSessionSnapshot(res.session);
      showSuccess(res.message || 'RTSP capture started.');
      void refreshRtspSessionsList();
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      showError(apiError.detail || apiError.message || 'Failed to start RTSP capture');
    } finally {
      setRtspBusy(false);
    }
  };

  const handleRtspStop = async () => {
    if (!rtspActiveSessionId) return;
    setRtspBusy(true);
    try {
      const res = await api.rtsp.stopCapture(project.project_id, rtspActiveSessionId);
      setRtspSessionSnapshot(res.session);
      showSuccess(res.message || 'Capture stopped.');
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      showError(apiError.detail || apiError.message || 'Failed to stop capture');
    } finally {
      setRtspBusy(false);
    }
  };

  const handleRtspDeleteSession = async (sessionId: string) => {
    setRtspBusy(true);
    try {
      await api.rtsp.deleteSession(project.project_id, sessionId);
      showSuccess('Session removed.');
      if (rtspActiveSessionId === sessionId) {
        setRtspActiveSessionId(null);
        setRtspSessionSnapshot(null);
        rtspRowsHydratedForSessionRef.current = null;
      }
      void refreshRtspSessionsList();
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      showError(apiError.detail || apiError.message || 'Failed to delete session');
    } finally {
      setRtspBusy(false);
    }
  };

  /** Matches server: stop during RECORDING is rejected until MIN_RTSP_DURATION_SECONDS of active capture. */
  const canRtspStop =
    rtspStatus === 'RECORDING' &&
    rtspRecordingElapsedSeconds >= RTSP_MIN_DURATION_SECONDS;
  /**
   * RTSP task is running — manual Video Files uploads must wait. The listed sessions
   * count too: they report a capture this component has not attached its status poll
   * to yet, so controls stay consistent with what the server would accept.
   */
  const rtspPipelineActive =
    isLiveRtspSession(rtspSessionSnapshot) ||
    rtspSessionsList.some(
      session => session.session_id !== rtspActiveSessionId && isLiveRtspSession(session),
    );
  const rtspLiveBlocking = rtspPipelineActive;

  /** Manual video queue or upload in progress — RTSP capture cannot run in parallel. */
  const manualVideoPipelineBusy = videoUploading || pendingFiles.length > 0;

  /** Source mode is authoritative; old RTSP session history must not lock a manual project. */
  const rtspHasIngestedVideos = project.input_source_mode === 'rtsp';

  /**
   * Block starting RTSP only when manual pipeline is active or project holds manually uploaded videos.
   * Videos added via RTSP ingest still appear in `videoFiles` but must not disable RTSP capture.
   */
  const manualBlocksRtspCapture =
    manualVideoPipelineBusy || (videoFiles.length > 0 && !rtspHasIngestedVideos);

  const durationValue = Math.floor(Number(durationSeconds));
  const durationInvalid =
    durationSeconds.trim() === '' ||
    !Number.isFinite(durationValue) ||
    durationValue < RTSP_MIN_DURATION_SECONDS ||
    durationValue > RTSP_MAX_DURATION_SECONDS;

  const canRtspStart =
    !calibrationRunning &&
    !manualBlocksRtspCapture &&
    !rtspBusy &&
    !rtspLiveBlocking &&
    !durationInvalid;

  const rtspFormDisabled = calibrationRunning || rtspBusy || manualVideoPipelineBusy;

  const rtspPipelineOrApiBusy = rtspPipelineActive || rtspBusy;

  /** Cannot queue new manual uploads during RTSP work, calibration, or while RTSP-sourced clips are still on the project. */
  const manualVideoAddBlocked =
    uploading ||
    calibrationRunning ||
    rtspPipelineOrApiBusy ||
    (videoFiles.length > 0 && rtspHasIngestedVideos);

  /** Uploaded list: allow deletes to clear RTSP/manual channel unless a transfer, RTSP task, or calibration is running. */
  const uploadedVideoDeleteBlocked =
    uploading || rtspPipelineOrApiBusy || calibrationRunning;

  /** GT / layout / alignment upload & delete while calibration is running. */
  const otherInputsBlocked = uploading || calibrationRunning;

  // Load video files list
  const loadVideoFiles = async () => {
    try {
      const response = await api.data.listVideoFiles(project.project_id);
      setVideoFiles(response.video_files);
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to load video files';
      showError(errorMessage);
    }
  };

  // Refresh project info
  const refreshProject = async () => {
    try {
      const response = await api.project.getProjectInfo(project.project_id);
      onProjectUpdate(response.project_info);
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to refresh project';
      showError(errorMessage);
    }
  };

  // Select video files (store in pending state for reordering)
  const handleVideoSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
    if (calibrationRunning || manualVideoAddBlocked) {
      event.target.value = '';
      return;
    }
    const files = event.target.files;
    if (!files || files.length === 0) return;

    const selected = Array.from(files);
    event.target.value = '';
    setVideoUploadAlert(null);

    try {
      const { accepted, rejected } = await partitionVideosByResolution(selected);

      if (rejected.length > 0) {
        const details = rejected
          .map(({ file, reason }) => `${file.name}: ${reason}`)
          .join(' | ');
        setVideoUploadAlert({ severity: 'warning', message: `Some videos were rejected. ${details}` });
      }

      if (accepted.length > 0) {
        setPendingFiles((prev) => [...prev, ...accepted]);
      }
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : 'Failed to check video resolution';
      setVideoUploadAlert({ severity: 'error', message });
    }
  };

  // Drag and drop handlers
  const handleDragStart = (index: number) => {
    setDragIndex(index);
  };

  const handleDragOver = (e: React.DragEvent, index: number) => {
    e.preventDefault();
    if (dragIndex === null || dragIndex === index) return;
    const newFiles = [...pendingFiles];
    const draggedFile = newFiles[dragIndex];
    newFiles.splice(dragIndex, 1);
    newFiles.splice(index, 0, draggedFile);
    setPendingFiles(newFiles);
    setDragIndex(index);
  };

  const handleDragEnd = () => {
    setDragIndex(null);
  };

  // Remove from pending
  const removePendingFile = (index: number) => {
    setPendingFiles(prev => prev.filter((_, i) => i !== index));
  };

  // Upload pending files with progress tracking
  const handleUploadPending = async () => {
    if (calibrationRunning) {
      showError('Calibration is running. Video upload is not allowed.');
      return;
    }
    if (pendingFiles.length === 0) return;
    setVideoUploadAlert(null);

    try {
      // Re-check before upload so a stale queue cannot bypass the client gate.
      const { accepted, rejected } = await partitionVideosByResolution(pendingFiles);
      if (rejected.length > 0) {
        setPendingFiles(accepted);
        const details = rejected
          .map(({ file, reason }) => `${file.name}: ${reason}`)
          .join(' | ');
        setVideoUploadAlert({ severity: 'warning', message: `Some videos were rejected. ${details}` });
        return;
      }

      setUploading(true);
      setVideoUploading(true);
      setUploadProgress(0);
      
      await api.data.uploadVideoFilesWithProgress(
        project.project_id, 
        accepted,
        (percent) => setUploadProgress(percent)
      );
      
      showSuccess(`${accepted.length} video file(s) uploaded successfully`);
      setPendingFiles([]);
      
      await loadVideoFiles();
      await refreshProject();
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to upload videos';
      setVideoUploadAlert({ severity: 'error', message: errorMessage });
    } finally {
      setUploading(false);
      setVideoUploading(false);
      setUploadProgress(0);
    }
  };

  // Delete video file
  const handleVideoDelete = async (videoName: string) => {
    if (calibrationRunning) {
      showError('Calibration is running. Video deletion is not allowed.');
      return;
    }
    try {
      await api.data.deleteVideoFile(project.project_id, videoName);
      showSuccess('Video deleted successfully');
      await loadVideoFiles();
      await refreshProject();
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to delete video';
      showError(errorMessage);
    }
  };

  // Upload ground truth
  const handleGroundTruthUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    if (calibrationRunning) {
      showError('Calibration is running. Ground truth upload is not allowed.');
      event.target.value = '';
      return;
    }
    const file = event.target.files?.[0];
    if (!file) return;

    if (!file.name.endsWith('.zip')) {
      showError('Ground truth must be a ZIP file');
      return;
    }

    try {
      setUploading(true);
      await api.data.uploadGroundTruthFile(project.project_id, file);
      showSuccess('Ground truth uploaded successfully');
      await refreshProject();
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to upload ground truth';
      showError(errorMessage);
    } finally {
      setUploading(false);
      event.target.value = '';
    }
  };

  // Delete ground truth
  const handleGroundTruthDelete = async () => {
    if (calibrationRunning) {
      showError('Calibration is running. Ground truth deletion is not allowed.');
      return;
    }
    try {
      await api.data.deleteGroundTruthFile(project.project_id);
      showSuccess('Ground truth deleted successfully');
      await refreshProject();
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to delete ground truth';
      showError(errorMessage);
    }
  };

  // Upload layout
  const handleLayoutUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    if (calibrationRunning) {
      showError('Calibration is running. Layout upload is not allowed.');
      event.target.value = '';
      return;
    }
    const file = event.target.files?.[0];
    if (!file) return;

    if (!file.name.match(/\.(png|jpg|jpeg)$/i)) {
      showError('Layout must be a PNG, JPG, or JPEG file');
      return;
    }

    try {
      setUploading(true);
      await api.data.uploadLayoutFile(project.project_id, file);
      showSuccess('Layout image uploaded successfully');
      await refreshProject();
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to upload layout';
      showError(errorMessage);
    } finally {
      setUploading(false);
      event.target.value = '';
    }
  };

  // Delete layout
  const handleLayoutDelete = async () => {
    if (calibrationRunning) {
      showError('Calibration is running. Layout deletion is not allowed.');
      return;
    }
    try {
      await api.data.deleteLayoutFile(project.project_id);
      showSuccess('Layout deleted successfully');
      await refreshProject();
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to delete layout';
      showError(errorMessage);
    }
  };

  // Upload alignment
  const handleAlignmentUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    if (calibrationRunning) {
      showError('Calibration is running. Alignment upload is not allowed.');
      event.target.value = '';
      return;
    }
    const file = event.target.files?.[0];
    if (!file) return;

    if (!file.name.endsWith('.json')) {
      showError('Alignment file must be a JSON file');
      return;
    }

    try {
      setUploading(true);
      await api.data.uploadAlignmentFile(project.project_id, file, 'original');
      showSuccess('Alignment file uploaded successfully');
      await refreshProject();
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to upload alignment';
      showError(errorMessage);
    } finally {
      setUploading(false);
      event.target.value = '';
    }
  };

  // Delete alignment
  const handleAlignmentDelete = async () => {
    if (calibrationRunning) {
      showError('Calibration is running. Alignment deletion is not allowed.');
      return;
    }
    try {
      await api.data.deleteAlignmentFile(project.project_id);
      showSuccess('Alignment file deleted successfully');
      await refreshProject();
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to delete alignment';
      showError(errorMessage);
    }
  };

  return (
    <Box>
      {/* Header */}
      <Box sx={{ mb: 3 }}>
        <Typography variant="h5" gutterBottom>
          Video Configuration
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Project: <strong>{project.project_name}</strong> ({project.project_id})
        </Typography>
      </Box>

      {calibrationRunning && (
        <Alert severity="warning" sx={{ mb: 3 }}>
          Calibration is running. Video, ground truth, layout, alignment uploads/deletes, and RTSP capture
          are disabled until it finishes.
        </Alert>
      )}

      {/* Upload Progress */}
      {uploading && (
        <Box sx={{ mb: 3 }}>
          {videoUploading && (
            <Alert severity="warning" sx={{ mb: 1.5 }}>
              Do not refresh the page or navigate to another step while videos are uploading. Leaving
              now can interrupt the transfer and leave an incomplete upload.
            </Alert>
          )}
          <LinearProgress variant={uploadProgress > 0 ? 'determinate' : 'indeterminate'} value={uploadProgress} />
          <Typography variant="caption" color="text.secondary" sx={{ mt: 1 }}>
            Uploading...
          </Typography>
        </Box>
      )}

      {/* Status Overview */}
      <Paper sx={{ p: 2, mb: 3, bgcolor: 'background.paper' }}>
        <Typography variant="subtitle2" gutterBottom>
          Upload Status
        </Typography>
        <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
          <Chip
            icon={project.video_files_count > 0 ? <CheckCircleIcon /> : undefined}
            label={`Videos: ${project.video_files_count}`}
            color={project.video_files_count >= 1 ? 'success' : 'default'}
            size="small"
          />
          <Chip
            icon={project.groundtruth_file ? <CheckCircleIcon /> : undefined}
            label="Ground Truth (Optional)"
            color={project.groundtruth_file ? 'success' : 'default'}
            size="small"
          />
          <Chip
            icon={project.layout_file ? <CheckCircleIcon /> : undefined}
            label="Layout"
            color={project.layout_file ? 'success' : 'default'}
            size="small"
          />
          <Chip
            icon={project.alignment_file ? <CheckCircleIcon /> : undefined}
            label="Alignment (Optional)"
            color={project.alignment_file ? 'success' : 'default'}
            size="small"
          />
        </Box>
      </Paper>

      <Grid container spacing={3}>
        {/* Video Files Upload */}
        <Grid item xs={12} md={6}>
          <Card
            sx={{
              ...((rtspPipelineOrApiBusy || calibrationRunning) && {
                opacity: 0.72,
                pointerEvents: 'none',
                transition: 'opacity 0.2s ease',
              }),
            }}
          >
            <CardContent>
              <Box sx={{ display: 'flex', alignItems: 'center', mb: 2 }}>
                <VideoFileIcon sx={{ mr: 1 }} color="primary" />
                <Typography variant="h6">Video Files</Typography>
              </Box>

              {videoUploadAlert && (
                <Alert
                  severity={videoUploadAlert.severity}
                  sx={{ mb: 2 }}
                  onClose={() => setVideoUploadAlert(null)}
                >
                  {videoUploadAlert.message}
                </Alert>
              )}

              {!calibrationRunning && rtspPipelineOrApiBusy && (
                <Alert severity="warning" sx={{ mb: 2 }}>
                  RTSP capture is in progress or finishing — video file upload is disabled until it completes.
                </Alert>
              )}
              {!calibrationRunning &&
                !rtspPipelineOrApiBusy &&
                videoFiles.length > 0 &&
                rtspHasIngestedVideos && (
                  <Alert severity="warning" sx={{ mb: 2 }}>
                    These videos came from RTSP capture. Remove them below before uploading files manually.
                  </Alert>
                )}
              
              <Alert severity="info" sx={{ mb: 2 }}>
                Upload at least 1 MP4 video. Videos must be exactly{' '}
                {REQUIRED_VIDEO_WIDTH}×{REQUIRED_VIDEO_HEIGHT}. Longer videos increase
                chances of successful calibration. Any filename is accepted and mapped
                internally.
              </Alert>

              <Button
                component="label"
                variant="outlined"
                startIcon={<CloudUploadIcon />}
                disabled={manualVideoAddBlocked}
                fullWidth
              >
                Select Videos
                <input
                  type="file"
                  hidden
                  multiple
                  accept=".mp4"
                  onChange={handleVideoSelect}
                  disabled={manualVideoAddBlocked}
                />
              </Button>

              {/* Pending files - reorder before upload */}
              {pendingFiles.length > 0 && (
                <>
                  <Divider sx={{ my: 2 }} />
                  <Typography variant="subtitle2" gutterBottom>
                    Selected Files - Reorder Before Upload ({pendingFiles.length})
                  </Typography>
                  <List dense>
                    {pendingFiles.map((file, index) => (
                      <ListItem 
                        key={`${file.name}-${index}`}
                        draggable={!manualVideoAddBlocked}
                        onDragStart={() => handleDragStart(index)}
                        onDragOver={(e) => handleDragOver(e, index)}
                        onDragEnd={handleDragEnd}
                        sx={{
                          cursor: manualVideoAddBlocked ? 'default' : 'grab',
                          bgcolor: dragIndex === index ? 'action.selected' : 'transparent',
                          '&:hover': { bgcolor: 'action.hover' },
                        }}
                      >
                        <DragIndicatorIcon sx={{ mr: 1, color: 'text.secondary' }} />
                        <ListItemText 
                          primary={file.name}
                          secondary={`Will be cam_${(videoFiles.length + index).toString().padStart(2, '0')}`}
                        />
                        <ListItemSecondaryAction>
                          <IconButton
                            size="small"
                            color="error"
                            onClick={() => removePendingFile(index)}
                            disabled={manualVideoAddBlocked}
                          >
                            <DeleteIcon fontSize="small" />
                          </IconButton>
                        </ListItemSecondaryAction>
                      </ListItem>
                    ))}
                  </List>
                  <Button
                    variant="contained"
                    color="primary"
                    onClick={handleUploadPending}
                    disabled={manualVideoAddBlocked}
                    fullWidth
                    sx={{ mt: 1 }}
                  >
                    {uploading ? `Uploading... ${uploadProgress}%` : `Upload ${pendingFiles.length} File(s)`}
                  </Button>
                  
                  {/* Upload Progress Bar */}
                  {uploading && (
                    <Box sx={{ mt: 2 }}>
                      <Box sx={{ display: 'flex', alignItems: 'center', mb: 0.5 }}>
                        <Typography variant="body2" sx={{ flexGrow: 1 }}>
                          Uploading videos...
                        </Typography>
                        <Typography variant="body2" fontWeight="bold" color="primary">
                          {uploadProgress}%
                        </Typography>
                      </Box>
                      <LinearProgress 
                        variant="determinate" 
                        value={uploadProgress} 
                        sx={{ height: 8, borderRadius: 4 }}
                      />
                    </Box>
                  )}
                </>
              )}

              {videoFiles.length > 0 && (
                <>
                  <Divider sx={{ my: 2 }} />
                  <Typography variant="subtitle2" gutterBottom>
                    Uploaded Videos ({videoFiles.length})
                  </Typography>
                  <List dense>
                    {videoFiles.map((video) => {
                      const camId = video.replace('.mp4', '');
                      const displayName = project.camera_name_map?.[camId] || video;
                      return (
                        <ListItem key={video}>
                          <ListItemText primary={displayName} />
                          <ListItemSecondaryAction>
                            <IconButton
                              edge="end"
                              size="small"
                              color="error"
                              onClick={() => handleVideoDelete(video)}
                              disabled={uploadedVideoDeleteBlocked}
                            >
                              <DeleteIcon />
                            </IconButton>
                          </ListItemSecondaryAction>
                        </ListItem>
                      );
                    })}
                  </List>
                </>
              )}
            </CardContent>
          </Card>
        </Grid>

        {/* Ground Truth Upload */}
        <Grid item xs={12} md={6}>
          <Card
            sx={{
              ...(calibrationRunning && {
                opacity: 0.72,
                pointerEvents: 'none',
                transition: 'opacity 0.2s ease',
              }),
            }}
          >
            <CardContent>
              <Box sx={{ display: 'flex', alignItems: 'center', mb: 2 }}>
                <FolderZipIcon sx={{ mr: 1 }} color="primary" />
                <Typography variant="h6">Ground Truth Data</Typography>
                {project.groundtruth_file && <CheckCircleIcon color="success" sx={{ ml: 'auto' }} />}
              </Box>

              <Alert severity={project.groundtruth_file ? 'success' : 'info'} sx={{ mb: 2 }}>
                {project.groundtruth_file 
                  ? 'Ground truth uploaded ✓' 
                  : 'Upload ground truth ZIP file (Optional - for evaluation)'}
              </Alert>

              {!project.groundtruth_file ? (
                <Button
                  component="label"
                  variant="outlined"
                  startIcon={<CloudUploadIcon />}
                  disabled={otherInputsBlocked}
                  fullWidth
                >
                  Upload Ground Truth (Optional)
                  <input
                    type="file"
                    hidden
                    accept=".zip"
                    onChange={handleGroundTruthUpload}
                    disabled={otherInputsBlocked}
                  />
                </Button>
              ) : (
                <Button
                  variant="outlined"
                  color="error"
                  startIcon={<DeleteIcon />}
                  onClick={handleGroundTruthDelete}
                  disabled={otherInputsBlocked}
                  fullWidth
                >
                  Delete Ground Truth
                </Button>
              )}
            </CardContent>
          </Card>
        </Grid>

        {/* Layout Image Upload */}
        <Grid item xs={12} md={6}>
          <Card
            sx={{
              ...(calibrationRunning && {
                opacity: 0.72,
                pointerEvents: 'none',
                transition: 'opacity 0.2s ease',
              }),
            }}
          >
            <CardContent>
              <Box sx={{ display: 'flex', alignItems: 'center', mb: 2 }}>
                <ImageIcon sx={{ mr: 1 }} color="primary" />
                <Typography variant="h6">Layout Image</Typography>
                {project.layout_file && <CheckCircleIcon color="success" sx={{ ml: 'auto' }} />}
              </Box>

              <Alert severity={project.layout_file ? 'success' : 'warning'} sx={{ mb: 2 }}>
                {project.layout_file 
                  ? `Layout: ${project.layout_filename || 'layout.png'} ✓` 
                  : 'Upload layout/map PNG image (Required)'}
              </Alert>

              {!project.layout_file ? (
                <Button
                  component="label"
                  variant="contained"
                  startIcon={<CloudUploadIcon />}
                  disabled={otherInputsBlocked}
                  fullWidth
                >
                  Upload Layout
                  <input
                    type="file"
                    hidden
                    accept=".png,.jpg,.jpeg"
                    onChange={handleLayoutUpload}
                    disabled={otherInputsBlocked}
                  />
                </Button>
              ) : (
                <Button
                  variant="outlined"
                  color="error"
                  startIcon={<DeleteIcon />}
                  onClick={handleLayoutDelete}
                  disabled={otherInputsBlocked}
                  fullWidth
                >
                  Delete Layout
                </Button>
              )}
            </CardContent>
          </Card>
        </Grid>

        {/* Alignment File Upload (Optional) */}
        <Grid item xs={12} md={6}>
          <Card
            sx={{
              ...(calibrationRunning && {
                opacity: 0.72,
                pointerEvents: 'none',
                transition: 'opacity 0.2s ease',
              }),
            }}
          >
            <CardContent>
              <Box sx={{ display: 'flex', alignItems: 'center', mb: 2 }}>
                <DescriptionIcon sx={{ mr: 1 }} color="primary" />
                <Typography variant="h6">Alignment Data</Typography>
                {project.alignment_file && <CheckCircleIcon color="success" sx={{ ml: 'auto' }} />}
              </Box>

              <Alert severity="info" sx={{ mb: 2 }}>
                {project.alignment_file
                  ? 'Alignment uploaded ✓ — open Manual Alignment (Step 4) to view or edit points.'
                  : 'Upload original-space alignment JSON (optional — create/edit in Manual Alignment)'}
              </Alert>

              {!project.alignment_file ? (
                <Button
                  component="label"
                  variant="outlined"
                  startIcon={<CloudUploadIcon />}
                  disabled={otherInputsBlocked}
                  fullWidth
                >
                  Upload Alignment (Optional)
                  <input
                    type="file"
                    hidden
                    accept=".json"
                    onChange={handleAlignmentUpload}
                    disabled={otherInputsBlocked}
                  />
                </Button>
              ) : (
                <Button
                  variant="outlined"
                  color="error"
                  startIcon={<DeleteIcon />}
                  onClick={handleAlignmentDelete}
                  disabled={otherInputsBlocked}
                  fullWidth
                >
                  Delete Alignment
                </Button>
              )}
            </CardContent>
          </Card>
        </Grid>

        {rtspSupported ? (
        <Grid item xs={12}>
          <Card
            sx={{
              ...((manualVideoPipelineBusy || calibrationRunning) && {
                opacity: 0.72,
                pointerEvents: 'none',
                transition: 'opacity 0.2s ease',
              }),
            }}
          >
            <CardContent>
              <Box sx={{ display: 'flex', alignItems: 'center', mb: 2, flexWrap: 'wrap', gap: 1 }}>
                <VideocamIcon color="primary" />
                <Typography variant="h6">RTSP capture (VIOS)</Typography>
                {rtspStatus && (
                  <Chip size="small" label={rtspStatus} color="primary" variant="outlined" sx={{ ml: 'auto' }} />
                )}
              </Box>
              {rtspShowLiveCaptureProgress && (
                <Box sx={{ mb: 2 }}>
                  <Box sx={{ display: 'flex', alignItems: 'center', mb: 0.5 }}>
                    <Typography variant="body2" sx={{ flexGrow: 1 }}>
                      {rtspStatus === 'STARTING' && 'Starting RTSP capture…'}
                      {rtspStatus === 'RECORDING' &&
                        `Recording…`}
                      {rtspStatus === 'STOPPING' && 'Stopping recordings…'}
                      {rtspStatus === 'INGESTING' && 'Adding recordings to project…'}
                    </Typography>
                    {rtspStatus === 'RECORDING' && (
                      <Typography variant="body2" fontWeight="bold" color="primary">
                        {rtspCaptureProgressPercent}%
                      </Typography>
                    )}
                  </Box>
                  <LinearProgress
                    variant={rtspStatus === 'RECORDING' ? 'determinate' : 'indeterminate'}
                    value={rtspCaptureProgressPercent}
                    sx={{ height: 8, borderRadius: 4 }}
                  />
                </Box>
              )}
              {manualVideoPipelineBusy && (
                <Alert severity="info" sx={{ mb: 2 }}>
                  Finish or clear the Video Files queue (pending selection or upload) before using RTSP capture.
                </Alert>
              )}
              {!manualVideoPipelineBusy &&
                videoFiles.length > 0 &&
                !rtspHasIngestedVideos && (
                  <Alert severity="info" sx={{ mb: 2 }}>
                    This project already has videos from file upload. Remove them under Video Files to use RTSP capture.
                  </Alert>
                )}
              <Stack spacing={2} sx={{ mb: 2 }}>
                <TextField
                  label="Duration (seconds)"
                  type="text"
                  value={durationSeconds}
                  onChange={e => {
                    const next = e.target.value;
                    if (next === '' || /^\d+$/.test(next)) {
                      setDurationSeconds(next);
                    }
                  }}
                  size="small"
                  sx={{ maxWidth: 280 }}
                  disabled={rtspFormDisabled}
                  error={durationInvalid}
                  inputProps={{
                    inputMode: 'numeric',
                    min: RTSP_MIN_DURATION_SECONDS,
                    max: RTSP_MAX_DURATION_SECONDS,
                  }}
                  helperText={
                    durationInvalid
                      ? `Enter ${RTSP_MIN_DURATION_SECONDS} seconds minimum; ${formatRtspSeconds(RTSP_MAX_DURATION_SECONDS)} maximum.`
                      : `Allowed: ${RTSP_MIN_DURATION_SECONDS} seconds minimum; ${formatRtspSeconds(RTSP_MAX_DURATION_SECONDS)} maximum.`
                  }
                />
              </Stack>
              <Typography variant="subtitle2" gutterBottom>
                Streams
              </Typography>
              <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
                RTSP URL is always required. Optional <strong>Sensor ID</strong> reuses a camera already
                registered in VIOS; if the ID is missing in VIOS, the server registers from the URL.
              </Typography>
              <Stack spacing={1.5} sx={{ mb: 2 }}>
                {rtspRows.map(row => (
                  <Paper key={row.id} variant="outlined" sx={{ p: 1.5 }}>
                    <Grid container spacing={1} alignItems="flex-start">
                      <Grid item xs={12} md={5}>
                        <TextField
                          label="RTSP URL"
                          value={row.rtsp_url}
                          onChange={e => {
                            const v = e.target.value;
                            setRtspRows(prev =>
                              prev.map(r => (r.id === row.id ? { ...r, rtsp_url: v } : r)),
                            );
                          }}
                          fullWidth
                          size="small"
                          disabled={rtspFormDisabled}
                          placeholder="rtsp://..."
                          required
                        />
                      </Grid>
                      <Grid item xs={12} md={3}>
                        <TextField
                          label="Camera name (optional)"
                          value={row.camera_name}
                          onChange={e => {
                            const v = e.target.value;
                            setRtspRows(prev =>
                              prev.map(r => (r.id === row.id ? { ...r, camera_name: v } : r)),
                            );
                          }}
                          fullWidth
                          size="small"
                          disabled={rtspFormDisabled}
                        />
                      </Grid>
                      <Grid item xs={12} md={3}>
                        <TextField
                          label="Sensor ID (optional)"
                          value={row.sensor_id}
                          onChange={e => {
                            const v = e.target.value;
                            setRtspRows(prev =>
                              prev.map(r => (r.id === row.id ? { ...r, sensor_id: v } : r)),
                            );
                          }}
                          fullWidth
                          size="small"
                          disabled={rtspFormDisabled}
                          placeholder="VIOS sensor UUID"
                          helperText="Pre-registered in VIOS"
                        />
                      </Grid>
                      <Grid item xs={12} md={1} sx={{ display: 'flex', justifyContent: 'flex-end' }}>
                        <IconButton
                          aria-label="Remove stream row"
                          onClick={() => {
                            setRtspRows(prev => (prev.length <= 1 ? prev : prev.filter(r => r.id !== row.id)));
                          }}
                          disabled={rtspFormDisabled || rtspRows.length <= 1}
                          size="small"
                          sx={{ mt: 0.5 }}
                        >
                          <DeleteIcon />
                        </IconButton>
                      </Grid>
                    </Grid>
                  </Paper>
                ))}
                <Button
                  startIcon={<AddIcon />}
                  onClick={() => setRtspRows(prev => [...prev, newRtspStreamRow()])}
                  disabled={rtspFormDisabled}
                  size="small"
                >
                  Add stream
                </Button>
              </Stack>
              {rtspSessionSnapshot?.error_message && (
                <Alert severity="error" sx={{ mb: 2 }}>
                  {rtspSessionSnapshot.error_message}
                </Alert>
              )}
              {rtspSessionSnapshot?.stop_failures && rtspSessionSnapshot.stop_failures.length > 0 && (
                <Alert severity="warning" sx={{ mb: 2 }}>
                  Stop/recording issues for sensor(s): {rtspSessionSnapshot.stop_failures.join(', ')}
                </Alert>
              )}
              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ mb: 2 }} flexWrap="wrap">
                <Button
                  variant="contained"
                  startIcon={<VideocamIcon />}
                  onClick={handleRtspStart}
                  disabled={!canRtspStart}
                >
                  Capture and add to project
                </Button>
                <Button
                  variant="outlined"
                  color="warning"
                  startIcon={<StopIcon />}
                  onClick={handleRtspStop}
                  disabled={!rtspActiveSessionId || !canRtspStop || rtspFormDisabled}
                >
                  Stop early
                </Button>
              </Stack>
              {rtspActiveSessionId &&
                (rtspStatus === 'STARTING' ||
                  (rtspStatus === 'RECORDING' &&
                    rtspRecordingElapsedSeconds < RTSP_MIN_DURATION_SECONDS)) && (
                  <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
                    {rtspStatus === 'STARTING'
                      ? `Stop early enables after ${RTSP_MIN_DURATION_SECONDS}s of recording has started.`
                      : `Stop early enables in ${RTSP_MIN_DURATION_SECONDS - rtspRecordingElapsedSeconds}s (minimum clip length).`}
                  </Typography>
                )}
              {rtspActiveSessionId && (
                <Box sx={{ mb: 2 }}>
                  <Typography variant="caption" color="text.secondary" display="block">
                    Session: {rtspActiveSessionId}
                    {rtspSessionSnapshot?.ingested_files?.length
                      ? ` — ingested: ${formatRtspIngestedDisplay(rtspSessionSnapshot)}`
                      : ''}
                  </Typography>
                  {typeof rtspSessionSnapshot?.duration_seconds === 'number' &&
                    rtspSessionSnapshot.duration_seconds > 0 && (
                      <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.25 }}>
                        Scheduled capture length:{' '}
                        {formatRtspSeconds(Math.floor(rtspSessionSnapshot.duration_seconds))}
                      </Typography>
                    )}
                  {rtspCompletedWindowSec != null && (
                    <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.25 }}>
                      Recorded wall-clock window: {formatRtspSeconds(rtspCompletedWindowSec)}
                    </Typography>
                  )}
                  {(rtspSessionSnapshot?.sensor_ids?.length ?? 0) > 0 && (
                    <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.25 }}>
                      Resolved VIOS sensors: {(rtspSessionSnapshot?.sensor_ids ?? []).join(', ')}
                    </Typography>
                  )}
                </Box>
              )}
              <Divider sx={{ my: 2 }} />
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
                <Typography variant="subtitle2">Sessions for this project</Typography>
                <Button
                  size="small"
                  startIcon={<RefreshIcon />}
                  onClick={() => void refreshRtspSessionsList()}
                  disabled={rtspBusy || manualVideoPipelineBusy}
                >
                  Refresh
                </Button>
              </Box>
              {!rtspListLoaded ? (
                <Typography variant="body2" color="text.secondary">
                  Click Refresh to load sessions (requires RTSP API on the server).
                </Typography>
              ) : rtspSessionsList.length === 0 ? (
                <Typography variant="body2" color="text.secondary">
                  No sessions found, or RTSP endpoints are not available on this server.
                </Typography>
              ) : (
                <List dense disablePadding>
                  {rtspSessionsList.map(s => {
                    const windowSec = rtspCapturedWindowSeconds(s);
                    const targetSec =
                      typeof s.duration_seconds === 'number' &&
                      Number.isFinite(s.duration_seconds) &&
                      s.duration_seconds > 0
                        ? Math.floor(s.duration_seconds)
                        : null;
                    return (
                      <ListItem
                        key={s.session_id}
                        secondaryAction={
                          <IconButton
                            edge="end"
                            aria-label="Delete session"
                            size="small"
                            onClick={() => void handleRtspDeleteSession(s.session_id)}
                            disabled={rtspBusy || manualVideoPipelineBusy}
                          >
                            <DeleteIcon fontSize="small" />
                          </IconButton>
                        }
                      >
                        <ListItemText
                          primary={s.session_id}
                          primaryTypographyProps={{ variant: 'subtitle2', component: 'div' }}
                          secondaryTypographyProps={{ component: 'div' }}
                          secondary={
                            <Box component="div" sx={{ mt: 0.25 }}>
                              <Typography variant="body2" color="text.secondary" component="div">
                                {s.status} · {(s.streams?.length ?? 0)} stream(s)
                              </Typography>
                              {(targetSec != null || windowSec != null) && (
                                <Typography
                                  variant="caption"
                                  color="text.secondary"
                                  component="div"
                                  sx={{ display: 'block', mt: 0.35 }}
                                >
                                  {targetSec != null && <>Scheduled length {formatRtspSeconds(targetSec)}</>}
                                  {targetSec != null && windowSec != null && ' · '}
                                  {windowSec != null && <>Recorded window {formatRtspSeconds(windowSec)}</>}
                                </Typography>
                              )}
                              {(s.streams ?? []).map((st, i) => (
                                <Typography
                                  key={`${s.session_id}-url-${i}`}
                                  variant="caption"
                                  color="text.secondary"
                                  component="div"
                                  sx={{ display: 'block', wordBreak: 'break-all', mt: 0.35 }}
                                >
                                  {formatRtspStreamLine(st, i, s.sensor_ids?.[i])}
                                </Typography>
                              ))}
                            </Box>
                          }
                        />
                      </ListItem>
                    );
                  })}
                </List>
              )}
            </CardContent>
          </Card>
        </Grid>
        ):(<></>)}
      </Grid>

      {/* Requirements Note */}
      <Alert severity="info" sx={{ mt: 3 }}>
        <Typography variant="subtitle2" gutterBottom>
          Required for Calibration:
        </Typography>
        <Typography variant="body2">
          • At least 1 video file ({REQUIRED_VIDEO_WIDTH}x{REQUIRED_VIDEO_HEIGHT} MP4)<br />
          • Layout image (PNG)<br />
          • Alignment data (can be created in Manual Alignment step)
        </Typography>
        <Typography variant="subtitle2" gutterBottom sx={{ mt: 2 }}>
          Optional:
        </Typography>
        <Typography variant="body2">
          • Ground truth data (ZIP file) - for evaluation purposes
        </Typography>
      </Alert>
    </Box>
  );
};

export default VideoConfiguration;

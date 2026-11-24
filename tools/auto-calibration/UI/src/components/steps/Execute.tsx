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
 * Execute Step
 * Verify project and run calibration pipeline
 */

import React, { useState, useEffect, useRef } from 'react';
import {
  Box,
  Typography,
  Card,
  CardContent,
  Button,
  Alert,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  Chip,
  Paper,
  LinearProgress,
  Divider,
  Stack,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  IconButton,
  Collapse,
  Tab,
  Tabs,
} from '@mui/material';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import CancelIcon from '@mui/icons-material/Cancel';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import StopIcon from '@mui/icons-material/Stop';
import VerifiedIcon from '@mui/icons-material/Verified';
import ErrorIcon from '@mui/icons-material/Error';
import PendingIcon from '@mui/icons-material/Pending';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import TerminalIcon from '@mui/icons-material/Terminal';
import { useApp } from '../../context/AppContext';
import { api } from '../../services/api';
import { API_BASE_URL, API_ENDPOINTS, MIN_CAMERAS_REQUIRED } from '../../constants';
import type { Project } from '../../types';

interface ExecuteProps {
  project: Project;
  onProjectUpdate: (project: Project) => void;
}

/** Delay before reconnecting calibration log SSE after a dropped connection (server restart, proxy idle, etc.). */
const CALIBRATION_LOG_SSE_RECONNECT_MS = 2000;

const Execute: React.FC<ExecuteProps> = ({ project, onProjectUpdate }) => {
  const { showSuccess, showError, vggtAvailable, calibrationStartTime, setCalibrationStartTime } = useApp();
  const [verifying, setVerifying] = useState(false);
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [startingVggt, setStartingVggt] = useState(false);
  const [startingPostprocess, setStartingPostprocess] = useState(false);
  const [stoppingVggt, setStoppingVggt] = useState(false);
  const vggtTabEnabled =
    project.video_files_count >= 2 &&
    vggtAvailable &&
    project.rectification_state === 'COMPLETED';
  const [calibrationTab, setCalibrationTab] = useState<'amc' | 'vggt'>(
    vggtTabEnabled ? 'vggt' : 'amc',
  );
  const [elapsedTime, setElapsedTime] = useState(0);
  const [detectorType, setDetectorType] = useState<string>('resnet');
  const pollingIntervalRef = useRef<number | null>(null);
  
  // Log viewer state
  const [amcLogs, setAmcLogs] = useState<string[]>([]);
  const [vggtLogs, setVggtLogs] = useState<string[]>([]);
  const [amcLogsExpanded, setAmcLogsExpanded] = useState(true);
  const [vggtLogsExpanded, setVggtLogsExpanded] = useState(true);
  const amcEventSourceRef = useRef<EventSource | null>(null);
  const vggtEventSourceRef = useRef<EventSource | null>(null);
  const amcLogRef = useRef<HTMLDivElement | null>(null);
  const vggtLogRef = useRef<HTMLDivElement | null>(null);
  /** Latest project for SSE reconnect timers (avoids stale RUNNING checks after server reconnect). */
  const projectRef = useRef(project);
  projectRef.current = project;

  // Fetch existing logs on mount
  useEffect(() => {
    const fetchLogs = async () => {
      if (project.amc_state !== 'INIT' && project.amc_state !== 'READY') {
        try {
          const res = await fetch(`${API_BASE_URL}${API_ENDPOINTS.GET_CALIBRATION_LOG(project.project_id, 'amc')}`);
          if (res.ok) setAmcLogs((await res.text()).split('\n').filter(l => l.trim()));
        } catch {}
      }
      if (project.vggt_state !== 'INIT' && project.vggt_state !== 'READY') {
        try {
          const res = await fetch(`${API_BASE_URL}${API_ENDPOINTS.GET_CALIBRATION_LOG(project.project_id, 'vggt')}`);
          if (res.ok) setVggtLogs((await res.text()).split('\n').filter(l => l.trim()));
        } catch {}
      }
    };
    fetchLogs();
  }, [project.project_id]);

  useEffect(() => {
    if (!vggtTabEnabled && calibrationTab === 'vggt') {
      setCalibrationTab('amc');
    }
  }, [vggtTabEnabled, calibrationTab]);

  useEffect(() => {
    // Start polling if any calibration is running; persist start time so it survives step change
    if (
      project.amc_state === 'RUNNING' ||
      project.vggt_state === 'RUNNING' ||
      project.postprocess_state === 'RUNNING'
    ) {
      if (!calibrationStartTime[project.project_id]) {
        setCalibrationStartTime(project.project_id, Date.now());
      }
      startPolling();
    }

    return () => {
      if (pollingIntervalRef.current) {
        clearInterval(pollingIntervalRef.current);
      }
    };
    // Note: intentionally omit calibrationStartTime — when it updates this effect would tear down polling/SSE.
    // Log SSE lifecycle lives in dedicated effects below with reconnect-on-error.
  }, [project.amc_state, project.vggt_state, project.postprocess_state, project.project_id, setCalibrationStartTime]);

  // AMC calibration log SSE (reconnect after server/network drops while RUNNING)
  useEffect(() => {
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;

    const clearReconnect = () => {
      if (reconnectTimer !== undefined) {
        clearTimeout(reconnectTimer);
        reconnectTimer = undefined;
      }
    };

    const connectAmcLogStream = () => {
      clearReconnect();
      const p = projectRef.current;
      if (p.amc_state !== 'RUNNING') return;

      amcEventSourceRef.current?.close();
      amcEventSourceRef.current = null;

      const url = `${API_BASE_URL}${API_ENDPOINTS.STREAM_CALIBRATION_LOG(p.project_id, 'amc')}`;
      const es = new EventSource(url);
      es.onmessage = e => {
        if (e.data) {
          setAmcLogs(prev => [...prev, e.data]);
          setTimeout(() => amcLogRef.current?.scrollTo(0, amcLogRef.current.scrollHeight), 50);
        }
      };
      es.onerror = () => {
        es.close();
        if (amcEventSourceRef.current === es) {
          amcEventSourceRef.current = null;
        }
        clearReconnect();
        reconnectTimer = window.setTimeout(() => {
          reconnectTimer = undefined;
          if (projectRef.current.amc_state === 'RUNNING') {
            connectAmcLogStream();
          }
        }, CALIBRATION_LOG_SSE_RECONNECT_MS);
      };
      amcEventSourceRef.current = es;
    };

    if (project.amc_state === 'RUNNING') {
      connectAmcLogStream();
    } else {
      clearReconnect();
      amcEventSourceRef.current?.close();
      amcEventSourceRef.current = null;
    }

    return () => {
      clearReconnect();
      amcEventSourceRef.current?.close();
      amcEventSourceRef.current = null;
    };
  }, [project.amc_state, project.project_id]);

  // VGGT calibration log SSE (same reconnect behavior as AMC)
  useEffect(() => {
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;

    const clearReconnect = () => {
      if (reconnectTimer !== undefined) {
        clearTimeout(reconnectTimer);
        reconnectTimer = undefined;
      }
    };

    const connectVggtLogStream = () => {
      clearReconnect();
      const p = projectRef.current;
      if (p.vggt_state !== 'RUNNING') return;

      vggtEventSourceRef.current?.close();
      vggtEventSourceRef.current = null;

      const url = `${API_BASE_URL}${API_ENDPOINTS.STREAM_CALIBRATION_LOG(p.project_id, 'vggt')}`;
      const es = new EventSource(url);
      es.onmessage = e => {
        if (e.data) {
          setVggtLogs(prev => [...prev, e.data]);
          setTimeout(() => vggtLogRef.current?.scrollTo(0, vggtLogRef.current.scrollHeight), 50);
        }
      };
      es.onerror = () => {
        es.close();
        if (vggtEventSourceRef.current === es) {
          vggtEventSourceRef.current = null;
        }
        clearReconnect();
        reconnectTimer = window.setTimeout(() => {
          reconnectTimer = undefined;
          if (projectRef.current.vggt_state === 'RUNNING') {
            connectVggtLogStream();
          }
        }, CALIBRATION_LOG_SSE_RECONNECT_MS);
      };
      vggtEventSourceRef.current = es;
    };

    if (project.vggt_state === 'RUNNING') {
      connectVggtLogStream();
    } else {
      clearReconnect();
      vggtEventSourceRef.current?.close();
      vggtEventSourceRef.current = null;
    }

    return () => {
      clearReconnect();
      vggtEventSourceRef.current?.close();
      vggtEventSourceRef.current = null;
    };
  }, [project.vggt_state, project.project_id]);

  // Update elapsed time (startTime from context so it persists across step navigation)
  useEffect(() => {
    let timer: number | null = null;
    const start = calibrationStartTime[project.project_id];
    if (
      (project.amc_state === 'RUNNING' ||
        project.vggt_state === 'RUNNING' ||
        project.postprocess_state === 'RUNNING') &&
      start
    ) {
      setElapsedTime(Date.now() - start); // sync immediately when returning to step
      timer = setInterval(() => {
        setElapsedTime(Date.now() - start);
      }, 1000);
    }

    return () => {
      if (timer) clearInterval(timer);
    };
  }, [project.amc_state, project.vggt_state, project.postprocess_state, project.project_id, calibrationStartTime]);

  // Start polling for status updates
  const startPolling = () => {
    if (pollingIntervalRef.current) {
      clearInterval(pollingIntervalRef.current);
    }

    pollingIntervalRef.current = setInterval(async () => {
      await refreshProject();
    }, 3000); // Poll every 3 seconds
  };

  // Stop polling
  const stopPolling = () => {
    if (pollingIntervalRef.current) {
      clearInterval(pollingIntervalRef.current);
      pollingIntervalRef.current = null;
    }
  };

  // Refresh project info
  const refreshProject = async () => {
    try {
      const response = await api.project.getProjectInfo(project.project_id);
      onProjectUpdate(response.project_info);

      // Stop polling if both AMC and VGGT are not running
      const amcNotRunning = response.project_info.amc_state !== 'RUNNING';
      const vggtNotRunning = response.project_info.vggt_state !== 'RUNNING';
      const postprocessNotRunning = response.project_info.postprocess_state !== 'RUNNING';
      
      if (amcNotRunning && vggtNotRunning && postprocessNotRunning) {
        stopPolling();
      }
    } catch (error: unknown) {
      console.error('Failed to refresh project:', error);
    }
  };

  // Verify project
  const handleVerify = async () => {
    try {
      setVerifying(true);
      const response = await api.data.verifyProject(project.project_id);
      showSuccess(response.message);
      await refreshProject();
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to verify project';
      showError(errorMessage);
    } finally {
      setVerifying(false);
    }
  };

  // Start calibration
  const handleStartCalibration = async () => {
    const canStart =
      project.verified && (project.amc_state === 'READY' ||
      project.amc_state === 'COMPLETED' ||
      project.amc_state === 'ERROR');
    if (!canStart) {
      showError('Please verify the project first');
      return;
    }

    try {
      setStarting(true);
      setCalibrationStartTime(project.project_id, Date.now());
      
      await api.calibration.startCalibration(project.project_id, detectorType);
      showSuccess(
        project.amc_state === 'COMPLETED' || project.amc_state === 'ERROR'
          ? 'AMC re-run started successfully'
          : 'Calibration started successfully'
      );
      
      // Start polling for status
      startPolling();
      
      await refreshProject();
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to start calibration';
      showError(errorMessage);
    } finally {
      setStarting(false);
    }
  };

  // Stop calibration
  const handleStopCalibration = async () => {
    if (project.project_state !== 'RUNNING') {
      showError('Calibration is not running');
      return;
    }

    try {
      setStopping(true);
      
      await api.calibration.stopCalibration(project.project_id);
      showSuccess('Calibration stopped successfully');
      
      // Stop polling
      stopPolling();
      
      setCalibrationStartTime(project.project_id, null);
      setElapsedTime(0);
      
      await refreshProject();
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to stop calibration';
      showError(errorMessage);
    } finally {
      setStopping(false);
    }
  };

  // Reset project
  const handleResetProject = async () => {
    try {
      setResetting(true);
      
      await api.project.resetProject(project.project_id);
      showSuccess('Project reset successfully');
      
      setCalibrationStartTime(project.project_id, null);
      setElapsedTime(0);
      
      await refreshProject();
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to reset project';
      showError(errorMessage);
    } finally {
      setResetting(false);
    }
  };

  // Start VGGT calibration
  const handleStartVggtCalibration = async () => {
    try {
      setStartingVggt(true);
      setCalibrationStartTime(project.project_id, Date.now());
      
      await api.calibration.startVggtCalibration(project.project_id);
      showSuccess('VGGT calibration started successfully');
      
      // Start polling for status
      startPolling();
      
      await refreshProject();
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to start VGGT calibration';
      showError(errorMessage);
    } finally {
      setStartingVggt(false);
    }
  };

  // Stop VGGT calibration
  const handleStopVggtCalibration = async () => {
    if (project.vggt_state !== 'RUNNING') {
      showError('VGGT calibration is not running');
      return;
    }

    try {
      setStoppingVggt(true);
      
      await api.calibration.stopCalibration(project.project_id);
      showSuccess('VGGT calibration stopped successfully');
      
      stopPolling();
      setCalibrationStartTime(project.project_id, null);
      setElapsedTime(0);
      
      await refreshProject();
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      const errorMessage = apiError.detail || apiError.message || 'Failed to stop VGGT calibration';
      showError(errorMessage);
    } finally {
      setStoppingVggt(false);
    }
  };

  const handleStartPostprocess = async () => {
    try {
      setStartingPostprocess(true);
      setCalibrationStartTime(project.project_id, Date.now());
      await api.calibration.startPostprocess(project.project_id);
      showSuccess('Post-processing started successfully');
      startPolling();
      await refreshProject();
    } catch (error: unknown) {
      const apiError = error as { message?: string; detail?: string };
      showError(apiError.detail || apiError.message || 'Failed to start post-processing');
    } finally {
      setStartingPostprocess(false);
    }
  };

  // Format elapsed time
  const formatElapsedTime = (ms: number) => {
    const seconds = Math.floor(ms / 1000);
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);

    if (hours > 0) {
      return `${hours}h ${minutes % 60}m ${seconds % 60}s`;
    }
    if (minutes > 0) {
      return `${minutes}m ${seconds % 60}s`;
    }
    return `${seconds}s`;
  };

  // Get state badge
  const getStateBadge = (state: string) => {
    switch (state) {
      case 'INIT':
        return <Chip icon={<PendingIcon />} label="Initializing" color="default" />;
      case 'READY':
        return <Chip icon={<VerifiedIcon />} label="Ready" color="primary" />;
      case 'MODEL_MISSING':
        return <Chip icon={<ErrorIcon />} label="Model missing" color="warning" />;
      case 'RUNNING':
        return <Chip icon={<PendingIcon />} label="Running" color="warning" />;
      case 'COMPLETED':
        return <Chip icon={<CheckCircleIcon />} label="Completed" color="success" />;
      case 'ERROR':
        return <Chip icon={<ErrorIcon />} label="Error" color="error" />;
      default:
        return <Chip label={state} />;
    }
  };

  // Check requirements (single-camera: 1+ video; multi-camera: 2+ videos; alignment required for both)
  const requirements = [
    {
      label: `Videos (minimum ${MIN_CAMERAS_REQUIRED})`,
      satisfied: project.video_files_count >= MIN_CAMERAS_REQUIRED,
      detail: `${project.video_files_count} video(s) uploaded`,
    },
    {
      label: 'Layout Image',
      satisfied: project.layout_file,
      detail: project.layout_file ? 'Uploaded' : 'Not uploaded',
    },
    {
      label: 'Alignment Data',
      satisfied: project.alignment_file,
      detail: project.alignment_file ? 'Uploaded' : 'Not uploaded',
    },
  ];

  const allRequirementsMet = requirements.every(r => r.satisfied);

  return (
    <Box>
      {/* Header */}
      <Typography variant="h5" gutterBottom>
        Execute Calibration
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Project: <strong>{project.project_name}</strong>
      </Typography>

      {/* Project State */}
      <Paper sx={{ p: 1.5, mb: 2, bgcolor: 'background.paper' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Typography variant="subtitle2">
            Project State:
          </Typography>
          {getStateBadge(project.project_state)}
        </Box>
        {project.project_state === 'RUNNING' && (
          <Box sx={{ mt: 2 }}>
            <Typography variant="caption" color="text.secondary">
              Elapsed Time: {formatElapsedTime(elapsedTime)}
            </Typography>
            <LinearProgress sx={{ mt: 1 }} />
          </Box>
        )}
      </Paper>

      <Card sx={{ mb: 2 }}>
        <CardContent sx={{ py: 2 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
            <Typography variant="h6">Project inputs</Typography>
            <Chip
              icon={project.verified ? <CheckCircleIcon /> : <PendingIcon />}
              label={project.verified ? 'Verified' : 'Not verified'}
              color={project.verified ? 'success' : 'default'}
            />
          </Box>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            Shared validation for AMC and VGGT.
          </Typography>
          <List dense disablePadding>
            {requirements.map((req, idx) => (
              <ListItem key={idx} disableGutters>
                <ListItemIcon sx={{ minWidth: 34 }}>
                  {req.satisfied ? <CheckCircleIcon color="success" /> : <CancelIcon color="error" />}
                </ListItemIcon>
                <ListItemText primary={req.label} secondary={req.detail} />
              </ListItem>
            ))}
            <ListItem disableGutters>
              <ListItemIcon sx={{ minWidth: 34 }}>
                {project.groundtruth_file ? <CheckCircleIcon color="success" /> : <PendingIcon color="action" />}
              </ListItemIcon>
              <ListItemText
                primary="Ground Truth Data (optional)"
                secondary={project.groundtruth_file ? 'Uploaded for evaluation' : 'Not provided'}
              />
            </ListItem>
            <ListItem disableGutters>
              <ListItemIcon sx={{ minWidth: 34 }}>
                {project.focal_length.length > 0 ? <CheckCircleIcon color="success" /> : <PendingIcon color="action" />}
              </ListItemIcon>
              <ListItemText
                primary="Focal Length (optional)"
                secondary={project.focal_length.length > 0 ? `${project.focal_length.length} value(s): ${project.focal_length.join(', ')}` : 'Not provided'}
              />
            </ListItem>
          </List>
          {!allRequirementsMet && (
            <Alert severity="warning" sx={{ mt: 1 }}>
              Complete required inputs before verification.
            </Alert>
          )}
          {!project.verified && (
            <Button
              variant="outlined"
              startIcon={<VerifiedIcon />}
              onClick={handleVerify}
              disabled={!allRequirementsMet || verifying}
              fullWidth
              sx={{ mt: 1 }}
            >
              {verifying ? 'Verifying...' : 'Verify Project'}
            </Button>
          )}
        </CardContent>
      </Card>

      <Card sx={{ mb: 2 }}>
        <Tabs
          value={calibrationTab}
          onChange={(_, value: 'amc' | 'vggt') => setCalibrationTab(value)}
          variant="fullWidth"
          aria-label="Calibration method"
        >
          <Tab
            value="vggt"
            label="VGGT"
            disabled={!vggtTabEnabled}
          />
          <Tab value="amc" label="AMC" />
        </Tabs>
        <Divider />

      {project.video_files_count >= 2 && vggtAvailable && (
        <Alert severity="info" sx={{ mx: 2, mt: 2 }}>
          <Typography variant="body2">
            VGGT calibration typically finishes in a few minutes, execute it first. Then execute AMC,
            which takes longer. After both have completed, select the more accurate result.
          </Typography>
        </Alert>
      )}

      {/* VGGT model guidance - only for multi-camera (VGGT not supported for single-camera) */}
      {project.video_files_count >= 2 && !vggtAvailable && project.rectification_state === 'COMPLETED' && (
        <Alert severity="info" sx={{ mt: 3 }}>
          <Typography variant="subtitle2" gutterBottom>
            VGGT Model Missing
          </Typography>
          <Typography variant="body2">
            VGGT model is missing.
          </Typography>
        </Alert>
      )}

      {/* VGGT Calibration Section - multi-camera only (VGGT not supported for single-camera) */}
      {calibrationTab === 'vggt' && vggtTabEnabled && (
        <Box>
          <CardContent>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
              <Typography variant="h6">
                VGGT Calibration
              </Typography>
              {getStateBadge(project.vggt_state)}
            </Box>

            <Stack spacing={2}>
              {/* Start / re-run VGGT Calibration Button */}
              {(project.vggt_state === 'READY' ||
                project.vggt_state === 'ERROR' ||
                project.vggt_state === 'COMPLETED') && (
                <Button
                  variant="contained"
                  color="primary"
                  startIcon={<PlayArrowIcon />}
                  onClick={handleStartVggtCalibration}
                  disabled={
                    startingVggt ||
                    project.amc_state === 'RUNNING'
                  }
                  fullWidth
                  size="large"
                >
                  {startingVggt
                    ? 'Starting VGGT...'
                    : project.vggt_state === 'COMPLETED' || project.vggt_state === 'ERROR'
                      ? 'Relaunch VGGT Calibration'
                      : 'Start VGGT Calibration'}
                </Button>
              )}

              {project.vggt_state === 'INIT' && (
                <Alert severity="info">
                  Verify the project first to enable VGGT.
                </Alert>
              )}

              {/* Stop VGGT Calibration Button */}
              {project.vggt_state === 'RUNNING' && (
                <Button
                  variant="contained"
                  color="error"
                  startIcon={<StopIcon />}
                  onClick={handleStopVggtCalibration}
                  disabled={stoppingVggt}
                  fullWidth
                  size="large"
                >
                  {stoppingVggt ? 'Stopping...' : 'Stop VGGT Calibration'}
                </Button>
              )}

              {/* VGGT Running Status */}
              {project.vggt_state === 'RUNNING' && (
                <Alert severity="info">
                  <Typography variant="subtitle2" gutterBottom>
                    VGGT calibration is running...
                  </Typography>
                  <Typography variant="body2">
                    Estimating camera parameters from staged linear videos.
                    This process typically takes a few minutes.
                  </Typography>
                  <Typography variant="caption" sx={{ mt: 1, display: 'block' }}>
                    Status updates every 3 seconds
                  </Typography>
                </Alert>
              )}

              {/* VGGT Log Viewer */}
              {(project.vggt_state === 'RUNNING' || project.vggt_state === 'COMPLETED' || project.vggt_state === 'ERROR') && (
                <Card variant="outlined" sx={{ mt: 2 }}>
                  <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', px: 2, py: 1 }}>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                      <TerminalIcon fontSize="small" color="primary" />
                      <Typography variant="subtitle2">VGGT Logs</Typography>
                      {project.vggt_state === 'RUNNING' && <Chip size="small" label="Live" color="success" />}
                    </Box>
                    <IconButton size="small" onClick={() => setVggtLogsExpanded(!vggtLogsExpanded)}>
                      {vggtLogsExpanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                    </IconButton>
                  </Box>
                  <Collapse in={vggtLogsExpanded}>
                    <Box
                      ref={vggtLogRef}
                      sx={{
                        bgcolor: '#1e1e1e', color: '#d4d4d4', fontFamily: 'monospace', fontSize: '11px',
                        p: 1.5, maxHeight: 250, overflowY: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                      }}
                    >
                      {vggtLogs.length === 0 ? (
                        <Typography sx={{ color: '#888', fontStyle: 'italic', fontSize: '11px' }}>
                          {project.vggt_state === 'RUNNING' ? 'Connecting to log stream...' : 'No logs available'}
                        </Typography>
                      ) : (
                        vggtLogs.map((log, i) => <div key={i}>{log}</div>)
                      )}
                    </Box>
                  </Collapse>
                </Card>
              )}

              {/* VGGT Completed Status */}
              {project.vggt_state === 'COMPLETED' && (
                <Alert severity="success">
                  <Typography variant="subtitle2" gutterBottom>
                    ✅ VGGT calibration completed successfully!
                  </Typography>
                  <Typography variant="body2">
                    Refined calibration results are available. You can re-run VGGT to overwrite
                    them, or proceed to layout post-processing / results.
                  </Typography>
                </Alert>
              )}

              {/* VGGT Error Status */}
              {project.vggt_state === 'ERROR' && (
                <>
                  <Alert severity="error">
                    <Typography variant="subtitle2" gutterBottom>
                      ❌ VGGT calibration failed!
                    </Typography>
                    <Typography variant="body2">
                      You can retry VGGT without running AMC/E2E, or reset the project.
                    </Typography>
                  </Alert>
                  <Button
                    variant="outlined"
                    color="warning"
                    startIcon={<RestartAltIcon />}
                    onClick={handleResetProject}
                    disabled={resetting}
                    fullWidth
                    size="large"
                  >
                    {resetting ? 'Resetting...' : 'Reset Project'}
                  </Button>
                </>
              )}
            </Stack>
          </CardContent>
        </Box>
      )}

      {calibrationTab === 'amc' && (
      <Box>
        <CardContent>
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
            <Typography variant="h6">
              AMC Calibration
            </Typography>
            {getStateBadge(project.amc_state)}
          </Box>

          <Stack spacing={1.5}>
            {/* Detector Type Selection */}
            {(project.amc_state === 'READY' ||
              project.amc_state === 'COMPLETED' ||
              project.amc_state === 'ERROR') && (
              <FormControl fullWidth>
                <InputLabel>Detector Type</InputLabel>
                <Select
                  value={detectorType}
                  label="Detector Type"
                  onChange={(e) => setDetectorType(e.target.value)}
                  disabled={starting}
                >
                  <MenuItem value="resnet">Resnet</MenuItem>
                  <MenuItem value="transformer">Transformer</MenuItem>
                </Select>
              </FormControl>
            )}

            {/* Start / re-run Calibration Button */}
            {(project.amc_state === 'READY' ||
              project.amc_state === 'COMPLETED' ||
              project.amc_state === 'ERROR') && (
              <Button
                variant="contained"
                color="primary"
                startIcon={<PlayArrowIcon />}
                onClick={handleStartCalibration}
                disabled={starting || project.vggt_state === 'RUNNING'}
                fullWidth
                size="large"
              >
                {starting
                  ? 'Starting...'
                  : project.amc_state === 'COMPLETED' || project.amc_state === 'ERROR'
                    ? 'Relaunch AMC Calibration'
                    : 'Start AMC Calibration'}
              </Button>
            )}

            {/* Stop Calibration Button */}
            {project.amc_state === 'RUNNING' && (
              <Button
                variant="contained"
                color="error"
                startIcon={<StopIcon />}
                onClick={handleStopCalibration}
                disabled={stopping}
                fullWidth
                size="large"
              >
                {stopping ? 'Stopping...' : 'Stop Calibration'}
              </Button>
            )}

            {/* Running Status */}
            {project.amc_state === 'RUNNING' && (
              <Alert severity="info">
                <Typography variant="subtitle2" gutterBottom>
                  AMC calibration is running...
                </Typography>
                <Typography variant="body2">
                  This may take several minutes. You can close this page and return later.
                  The calibration will continue running on the server.
                </Typography>
                <Typography variant="caption" sx={{ mt: 1, display: 'block' }}>
                  Status updates every 3 seconds
                </Typography>
              </Alert>
            )}

            {/* AMC Log Viewer */}
            {(project.amc_state === 'RUNNING' || project.amc_state === 'COMPLETED' || project.amc_state === 'ERROR') && (
              <Card variant="outlined" sx={{ mt: 2 }}>
                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', px: 2, py: 1 }}>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <TerminalIcon fontSize="small" color="primary" />
                    <Typography variant="subtitle2">AMC Logs</Typography>
                    {project.amc_state === 'RUNNING' && <Chip size="small" label="Live" color="success" />}
                  </Box>
                  <IconButton size="small" onClick={() => setAmcLogsExpanded(!amcLogsExpanded)}>
                    {amcLogsExpanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                  </IconButton>
                </Box>
                <Collapse in={amcLogsExpanded}>
                  <Box
                    ref={amcLogRef}
                    sx={{
                      bgcolor: '#1e1e1e', color: '#d4d4d4', fontFamily: 'monospace', fontSize: '11px',
                      p: 1.5, maxHeight: 250, overflowY: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                    }}
                  >
                    {amcLogs.length === 0 ? (
                      <Typography sx={{ color: '#888', fontStyle: 'italic', fontSize: '11px' }}>
                        {project.amc_state === 'RUNNING' ? 'Connecting to log stream...' : 'No logs available'}
                      </Typography>
                    ) : (
                      amcLogs.map((log, i) => <div key={i}>{log}</div>)
                    )}
                  </Box>
                </Collapse>
              </Card>
            )}

            {/* Completed Status */}
            {project.amc_state === 'COMPLETED' && (
              <Alert severity="success">
                <Typography variant="subtitle2" gutterBottom>
                  ✅ AMC Calibration completed successfully!
                </Typography>
                <Typography variant="body2">
                  {project.video_files_count >= 2
                    ? 'You can re-run AMC, run VGGT, or proceed to layout post-processing / results.'
                    : 'You can re-run AMC or proceed to the next step to view results.'}
                </Typography>
              </Alert>
            )}

            {/* Error Status */}
            {project.amc_state === 'ERROR' && (
              <>
                <Alert severity="error">
                  <Typography variant="subtitle2" gutterBottom>
                    ❌ Calibration failed!
                  </Typography>
                  <Typography variant="body2">
                    Please check your input files and try again.
                    You may need to reset the project and re-upload files.
                  </Typography>
                </Alert>
                <Button
                  variant="outlined"
                  color="warning"
                  startIcon={<RestartAltIcon />}
                  onClick={handleResetProject}
                  disabled={resetting}
                  fullWidth
                  size="large"
                >
                  {resetting ? 'Resetting...' : 'Reset Project'}
                </Button>
              </>
            )}
          </Stack>
        </CardContent>
      </Box>
      )}

      {/* Shared calibration info: identical for both tabs */}
      <CardContent sx={{ pt: 0 }}>
        <Divider sx={{ mb: 2 }} />
        <Typography variant="subtitle2" gutterBottom>
          Calibration Information
        </Typography>
        <Box sx={{ bgcolor: 'action.hover', p: 1.5, borderRadius: 1 }}>
          <Typography variant="body2" component="div">
            • <strong>Project ID:</strong> {project.project_id}<br />
            • <strong>Videos:</strong> {project.video_files_count} camera(s)<br />
            • <strong>Focal Lengths:</strong> {project.focal_length.length > 0 ? 'Provided' : 'Not provided'}<br />
            • <strong>AMC State:</strong> {project.amc_state}
            {project.video_files_count >= 2 && (
              <>
                <br />
                • <strong>VGGT State:</strong> {project.vggt_state}
              </>
            )}
          </Typography>
        </Box>
      </CardContent>
      </Card>

      {/* Shared multi-camera derived-artifact workflow. Single-camera remains
          intentionally handled by the AMC SV post-processing path. */}
      {project.video_files_count >= 2 && (
        <Card sx={{ mt: 3 }}>
          <CardContent>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
              <Typography variant="h6">Layout Post-processing</Typography>
              {getStateBadge(project.postprocess_state)}
            </Box>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Generate layout-scaled camera data, overlays, optional GT evaluation, and MV3DT
              exports for every completed calibration.
            </Typography>
            <Button
              variant="contained"
              color="secondary"
              startIcon={<PlayArrowIcon />}
              onClick={handleStartPostprocess}
              disabled={
                startingPostprocess ||
                project.postprocess_state === 'RUNNING' ||
                project.postprocess_state === 'INIT' ||
                project.amc_state === 'RUNNING' ||
                project.vggt_state === 'RUNNING'
              }
              fullWidth
              size="large"
            >
              {startingPostprocess || project.postprocess_state === 'RUNNING'
                ? 'Post-processing...'
                : project.postprocess_state === 'COMPLETED'
                  ? 'Re-run Post-processing'
                  : 'Run Post-processing'}
            </Button>
            {project.postprocess_state === 'INIT' && (
              <Alert severity="info" sx={{ mt: 2 }}>
                Complete AMC or VGGT calibration and layout alignment first.
              </Alert>
            )}
            {project.postprocess_state === 'COMPLETED' && (
              <Alert severity="success" sx={{ mt: 2 }}>
                Derived artifacts are up to date. You may re-run after another calibration or
                alignment change.
              </Alert>
            )}
            {project.postprocess_state === 'ERROR' && (
              <Alert severity="error" sx={{ mt: 2 }}>
                Post-processing failed. Raw calibration results remain available; retry after
                checking the post-processing log.
              </Alert>
            )}
          </CardContent>
        </Card>
      )}
    </Box>
  );
};

export default Execute;

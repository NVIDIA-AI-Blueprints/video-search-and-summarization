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
 * Dedicated rectification workflow step.
 *
 * Rectification settings are global today, but they belong beside the action
 * that consumes them so users set them before starting the auto estimate.
 */

import React, { useEffect, useRef, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Collapse,
  IconButton,
  LinearProgress,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import TerminalIcon from '@mui/icons-material/Terminal';
import DownloadIcon from '@mui/icons-material/Download';
import { useApp } from '../../context/AppContext';
import { API_BASE_URL, API_ENDPOINTS } from '../../constants';
import { api } from '../../services/api';
import type { ConfigParams, Project, RectificationCameraState } from '../../types';
import { ProjectState } from '../../types';
import RectificationWorkflow from '../rectification/RectificationWorkflow';
import { downloadAsFile } from '../../utils/helpers';

interface RectificationProps {
  project: Project;
  onProjectUpdate: (project: Project) => void;
}

const RECTIFICATION_MODELS = ['simple_radial', 'radial', 'simple_divisional'] as const;
type RectificationPath = 'auto' | 'manual' | 'linear';
const RECTIFICATION_STATE_LABELS: Record<string, string> = {
  NOT_STARTED: 'Not Started',
  RUNNING: 'Running',
  READY_FOR_REVIEW: 'Ready for Review',
  COMPLETED: 'Completed',
  ERROR: 'Error',
};

const Rectification: React.FC<RectificationProps> = ({ project, onProjectUpdate }) => {
  const { showSuccess, showError } = useApp();
  const calibrationRunning = project.project_state === ProjectState.RUNNING;
  const rectificationState = project.rectification_state ?? 'NOT_STARTED';
  const rectificationStateLabel = RECTIFICATION_STATE_LABELS[rectificationState] ?? rectificationState;
  const [busy, setBusy] = useState(false);
  const [manualMode, setManualMode] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const [logsExpanded, setLogsExpanded] = useState(true);
  const [config, setConfig] = useState<ConfigParams | null>(null);
  const [configSaving, setConfigSaving] = useState(false);
  const [selectedPath, setSelectedPath] = useState<RectificationPath | null>(null);
  const [downloadingResults, setDownloadingResults] = useState(false);
  const [committedCameras, setCommittedCameras] = useState<RectificationCameraState[]>([]);
  const [provenanceLoading, setProvenanceLoading] = useState(false);
  const logRef = useRef<HTMLDivElement | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const stateRef = useRef(rectificationState);
  stateRef.current = rectificationState;

  const handleDownloadSingleViewResults = async () => {
    try {
      setDownloadingResults(true);
      const blob = await api.calibration.downloadSingleViewResults(project.project_id);
      downloadAsFile(
        blob,
        `${project.project_name || project.project_id}_single_view_results.zip`,
        'application/zip',
      );
      showSuccess('Rectified videos downloaded');
    } catch (error: unknown) {
      const e = error as { detail?: string; message?: string };
      showError(e.detail || e.message || 'Failed to download rectified videos');
    } finally {
      setDownloadingResults(false);
    }
  };

  const refreshProject = async () => {
    try {
      const response = await api.project.getProjectInfo(project.project_id);
      onProjectUpdate(response.project_info);
    } catch (error: unknown) {
      const e = error as { detail?: string; message?: string };
      showError(e.detail || e.message || 'Failed to refresh project');
    }
  };

  useEffect(() => {
    let cancelled = false;
    const loadConfig = async () => {
      try {
        const response = await api.data.getConfig() as { config_params?: ConfigParams };
        if (!cancelled && response.config_params) setConfig(response.config_params);
      } catch (error: unknown) {
        const e = error as { detail?: string; message?: string };
        if (!cancelled) showError(e.detail || e.message || 'Failed to load rectification settings');
      }
    };
    if (selectedPath === 'auto') void loadConfig();
    return () => { cancelled = true; };
  }, [project.project_id, selectedPath, showError]);

  useEffect(() => {
    setLogs([]);
  }, [project.project_id]);

  useEffect(() => {
    let cancelled = false;
    const loadCommittedCameras = async () => {
      if (rectificationState !== 'COMPLETED' || project.rectification_path === 'linear') {
        setCommittedCameras([]);
        return;
      }
      setProvenanceLoading(true);
      try {
        const response = await api.calibration.getRectificationCameras(project.project_id);
        if (!cancelled) setCommittedCameras(response.cameras);
      } catch {
        if (!cancelled) setCommittedCameras([]);
      } finally {
        if (!cancelled) setProvenanceLoading(false);
      }
    };
    void loadCommittedCameras();
    return () => {
      cancelled = true;
    };
  }, [project.project_id, project.rectification_path, rectificationState]);

  useEffect(() => {
    if (rectificationState === 'NOT_STARTED' || rectificationState === 'RUNNING') return;
    void fetch(`${API_BASE_URL}${API_ENDPOINTS.GET_RECTIFICATION_LOG(project.project_id)}`)
      .then(async (response) => {
        if (response.ok) setLogs((await response.text()).split('\n').filter(Boolean));
      })
      .catch(() => undefined);
  }, [project.project_id, rectificationState]);

  useEffect(() => {
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    const connect = () => {
      if (stateRef.current !== 'RUNNING') return;
      eventSourceRef.current?.close();
      const source = new EventSource(`${API_BASE_URL}${API_ENDPOINTS.STREAM_RECTIFICATION_LOG(project.project_id)}`);
      source.onmessage = (event) => {
        if (!event.data) return;
        setLogs((previous) => [...previous, event.data]);
        setTimeout(() => logRef.current?.scrollTo(0, logRef.current.scrollHeight), 50);
      };
      source.onerror = () => {
        source.close();
        if (stateRef.current === 'RUNNING') reconnectTimer = window.setTimeout(connect, 2000);
      };
      eventSourceRef.current = source;
    };
    if (rectificationState === 'RUNNING') {
      setLogs([]);
      connect();
    } else {
      eventSourceRef.current?.close();
      eventSourceRef.current = null;
    }
    return () => {
      if (reconnectTimer !== undefined) clearTimeout(reconnectTimer);
      eventSourceRef.current?.close();
      eventSourceRef.current = null;
    };
  }, [project.project_id, rectificationState]);

  useEffect(() => {
    if (rectificationState !== 'RUNNING') return undefined;
    const interval = window.setInterval(async () => {
      try {
        const status = await api.calibration.getRectificationStatus(project.project_id);
        if (status.rectification_state === 'RUNNING') return;
        window.clearInterval(interval);
        setBusy(false);
        await refreshProject();
        if (status.rectification_state === 'COMPLETED') showSuccess('Rectified videos are ready for alignment.');
        else if (status.rectification_state === 'READY_FOR_REVIEW') showSuccess('Auto-estimation completed. Review previews, then generate videos.');
        else showError('Rectification failed. Check rectification.log.');
      } catch {
        // Keep polling through transient failures.
      }
    }, 2000);
    return () => window.clearInterval(interval);
  }, [project.project_id, rectificationState]);

  const updateConfig = <K extends keyof ConfigParams>(key: K, value: ConfigParams[K]) => {
    setConfig((previous) => previous ? { ...previous, [key]: value } : previous);
  };

  const startAuto = async () => {
    if (!config) return;
    try {
      setManualMode(false);
      setBusy(true);
      setConfigSaving(true);
      await api.data.updateConfig(project.project_id, config);
      const response = await api.calibration.startRectification(project.project_id);
      showSuccess(response.message);
      await refreshProject();
    } catch (error: unknown) {
      setBusy(false);
      const e = error as { detail?: string; message?: string };
      showError(e.detail || e.message || 'Failed to start rectification');
    } finally {
      setConfigSaving(false);
    }
  };

  const startManual = async () => {
    try {
      setBusy(true);
      const response = await api.calibration.startManualRectification(project.project_id);
      setManualMode(true);
      showSuccess(response.message);
      await refreshProject();
    } catch (error: unknown) {
      const e = error as { detail?: string; message?: string };
      showError(e.detail || e.message || 'Failed to start manual rectification');
    } finally {
      setBusy(false);
    }
  };

  const useLinear = async () => {
    try {
      setManualMode(false);
      setBusy(true);
      const response = await api.calibration.stageAlreadyLinearMedia(project.project_id);
      showSuccess(response.message);
      await refreshProject();
    } catch (error: unknown) {
      const e = error as { detail?: string; message?: string };
      showError(e.detail || e.message || 'Failed to stage linear media');
    } finally {
      setBusy(false);
    }
  };

  const actionsDisabled = busy || configSaving || calibrationRunning || rectificationState === 'RUNNING' || project.video_files_count < 1;
  // Auto review shows GeoCalib params read-only; only Manual path can edit them.
  const editableManual = selectedPath === 'manual' && manualMode;
  const reviewControls = (
    <RectificationWorkflow
      projectId={project.project_id}
      videoFilesCount={project.video_files_count}
      rectificationState={rectificationState}
      rectificationBusy={busy}
      calibrationRunning={calibrationRunning}
      manualMode={editableManual}
      onManualModeChange={setManualMode}
      onRefreshProject={refreshProject}
      onRectificationBusyChange={setBusy}
      showSuccess={showSuccess}
      showError={showError}
    />
  );

  return (
    <Box>
      <Typography variant="h5" gutterBottom>Rectification</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Configure and generate linear camera media before alignment and calibration.
      </Typography>
      {rectificationState === 'COMPLETED' && (
        <Alert
          severity="success"
          sx={{ mb: 2 }}
          action={
            <Button
              color="inherit"
              size="small"
              startIcon={<DownloadIcon />}
              onClick={handleDownloadSingleViewResults}
              disabled={downloadingResults}
            >
              {downloadingResults ? 'Preparing ZIP…' : 'Download rectified videos'}
            </Button>
          }
        >
          Linear media is ready. Download the rectified videos as a ZIP. Wait For Download To Complete. Don't Close/Refresh/Navigate The Browser while downloading.
        </Alert>
      )}
      {rectificationState === 'COMPLETED' && (
        <Card variant="outlined" sx={{ mb: 3 }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              Rectification Used for This Project
            </Typography>
            <Typography variant="body2" sx={{ mb: 2 }}>
              Path:{' '}
              <strong>
                {project.rectification_path === 'auto'
                  ? 'Auto Rectification'
                  : project.rectification_path === 'manual'
                    ? 'Manual Rectification'
                    : project.rectification_path === 'linear'
                      ? 'Videos Are Rectified'
                      : 'Unavailable (completed before provenance tracking)'}
              </strong>
            </Typography>

            {project.rectification_path === 'linear' ? (
              <Alert severity="info" icon={false}>
                Already-linear media was staged unchanged. No distortion model or K1/K2
                correction was applied.
              </Alert>
            ) : provenanceLoading ? (
              <LinearProgress />
            ) : committedCameras.length > 0 ? (
              <Stack spacing={1}>
                {committedCameras.map((camera) => (
                  <Box
                    key={camera.camera_id}
                    sx={{
                      p: 1.5,
                      border: '1px solid',
                      borderColor: 'divider',
                      borderRadius: 1,
                    }}
                  >
                    <Typography variant="subtitle2">
                      {project.camera_name_map[camera.camera_id] || camera.camera_id}
                    </Typography>
                    {camera.committed ? (
                      <Typography variant="body2" color="text.secondary">
                        Model: <strong>{camera.committed.model}</strong>
                        {' · '}K1: <strong>{camera.committed.k1}</strong>
                        {camera.committed.model === 'radial' && (
                          <>
                            {' · '}K2: <strong>{camera.committed.k2}</strong>
                          </>
                        )}
                      </Typography>
                    ) : (
                      <Typography variant="body2" color="warning.main">
                        Committed distortion parameters are unavailable for this camera.
                      </Typography>
                    )}
                  </Box>
                ))}
              </Stack>
            ) : (
              <Typography variant="body2" color="warning.main">
                Committed per-camera rectification parameters could not be loaded.
              </Typography>
            )}
          </CardContent>
        </Card>
      )}
      {calibrationRunning && <Alert severity="warning" sx={{ mb: 2 }}>Rectification is running. Calibration is unavailable.</Alert>}
      <Typography variant="h6" gutterBottom>Choose a rectification path</Typography>
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} sx={{ mb: 3 }}>
        {([
          ['auto', 'Auto Rectification', 'Estimate distortion from frame 0.'],
          ['manual', 'Manual Rectification', 'Tune distortion per camera.'],
          ['linear', 'Videos Are Rectified', 'Stage existing linear videos unchanged.'],
        ] as const).map(([path, title, description]) => (
          <Card key={path} variant="outlined" sx={{ flex: 1 }}>
            <CardContent>
              <Button
                fullWidth
                variant={selectedPath === path ? 'contained' : 'outlined'}
                onClick={() => {
                  setSelectedPath(path);
                  // Contour / model sliders belong only to Manual; leave them off for Auto/Linear.
                  if (path !== 'manual') setManualMode(false);
                }}
                disabled={actionsDisabled}
                sx={{ justifyContent: 'flex-start', textAlign: 'left', py: 1.5 }}
              >
                <Box>
                  <Typography variant="h6">{title}</Typography>
                  <Typography variant="body2" sx={{ opacity: selectedPath === path ? 0.9 : 0.75 }}>
                    {description}
                  </Typography>
                </Box>
              </Button>
            </CardContent>
          </Card>
        ))}
      </Stack>
      {selectedPath === 'auto' && <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>Auto Rectification</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {rectificationState === 'COMPLETED'
              ? 'These global settings configure the next Auto run. The committed values for this project are shown above.'
              : 'Choose the distortion model and K1 search range, save, then estimate distortion from frame 0.'}
          </Typography>
          {!config ? <LinearProgress /> : <>
            <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} sx={{ mb: 2 }}>
              <TextField select fullWidth label="Distortion model" value={config.model}
                onChange={(e) => updateConfig('model', e.target.value)} disabled={actionsDisabled}>
                {RECTIFICATION_MODELS.map((model) => <MenuItem key={model} value={model}>{model}</MenuItem>)}
              </TextField>
              <TextField fullWidth label="K1 minimum" type="number" inputProps={{ step: 'any' }} value={config.k1_min}
                onChange={(e) => updateConfig('k1_min', Number(e.target.value))} disabled={actionsDisabled} />
              <TextField fullWidth label="K1 maximum" type="number" inputProps={{ step: 'any' }} value={config.k1_max}
                onChange={(e) => updateConfig('k1_max', Number(e.target.value))} disabled={actionsDisabled} />
              <TextField fullWidth label="K1 steps" type="number" inputProps={{ min: 1 }} value={config.k1_step}
                onChange={(e) => updateConfig('k1_step', Number(e.target.value))} disabled={actionsDisabled} />
            </Stack>
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems="center">
              <Button variant="contained" onClick={startAuto} disabled={actionsDisabled}>Save Settings & Start Auto Rectification</Button>
              <Chip label={`Rectification: ${rectificationStateLabel}`} color={rectificationState === 'COMPLETED' ? 'success' : rectificationState === 'READY_FOR_REVIEW' ? 'warning' : rectificationState === 'ERROR' ? 'error' : 'default'} size="small" />
            </Stack>
          </>}
        </CardContent>
      </Card>}
      {selectedPath === 'auto' && rectificationState === 'READY_FOR_REVIEW' && <>
        <Alert severity="warning" sx={{ mb: 2 }}>Parameters are ready. Review the previews and generate rectified videos before continuing.</Alert>
        {reviewControls}
      </>}
      {selectedPath === 'manual' && <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>Manual Rectification</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>Start manual mode to open per-camera distortion parameters and preview controls.</Typography>
          <Button variant="contained" onClick={startManual} disabled={actionsDisabled}>Start Manual Rectification</Button>
        </CardContent>
      </Card>}
      {selectedPath === 'manual' && manualMode && reviewControls}
      {selectedPath === 'linear' && <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>Videos Are Rectified</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>No distortion settings are needed. AMC will stage your source videos as linear media.</Typography>
          <Button variant="contained" onClick={useLinear} disabled={actionsDisabled}>Stage Linear Videos</Button>
        </CardContent>
      </Card>}
      {rectificationState === 'RUNNING' && <LinearProgress sx={{ mb: 2 }} />}
      {selectedPath === 'auto' && (rectificationState === 'RUNNING' || logs.length > 0) && <Card variant="outlined" sx={{ mb: 3 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', px: 2, py: 1 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}><TerminalIcon fontSize="small" color="primary" /><Typography variant="subtitle2">Rectification Logs</Typography></Box>
          <IconButton size="small" onClick={() => setLogsExpanded(!logsExpanded)}>{logsExpanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}</IconButton>
        </Box>
        <Collapse in={logsExpanded}><Box ref={logRef} sx={{ bgcolor: '#1e1e1e', color: '#d4d4d4', fontFamily: 'monospace', fontSize: '11px', p: 1.5, maxHeight: 250, overflowY: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{logs.map((log, index) => <div key={index}>{log}</div>)}</Box></Collapse>
      </Card>}
    </Box>
  );
};

export default Rectification;

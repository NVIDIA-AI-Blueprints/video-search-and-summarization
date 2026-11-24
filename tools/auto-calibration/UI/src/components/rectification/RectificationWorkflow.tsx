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
 * Rectification comparison rows + manual tuning controls.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Checkbox,
  FormControl,
  FormControlLabel,
  InputLabel,
  MenuItem,
  Select,
  Slider,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import { api } from '../../services/api';
import type { DistortionParams, RectificationCameraState } from '../../types';
import DistortionGridOverlay from './DistortionGridOverlay';
import {
  defaultParamsFromCamera,
  paramsMatchAutoModel,
  type DistortionModel,
} from '../../utils/distortionGrid';

const MODELS: DistortionModel[] = ['simple_radial', 'radial', 'simple_divisional'];

export interface CameraDraft extends DistortionParams {
  rotationDeg: number;
  autoModel: DistortionModel | null;
  previewUrl: string | null;
}

interface RectificationWorkflowProps {
  projectId: string;
  videoFilesCount: number;
  rectificationState: string;
  rectificationBusy: boolean;
  calibrationRunning: boolean;
  manualMode: boolean;
  onManualModeChange: (enabled: boolean) => void;
  onRefreshProject: () => Promise<void>;
  onRectificationBusyChange: (busy: boolean) => void;
  showSuccess: (msg: string) => void;
  showError: (msg: string) => void;
}

interface LazyCameraImageProps {
  projectId: string;
  videoName: string;
  useRectified: boolean;
  alt: string;
  onSize?: (w: number, h: number) => void;
}

const LazyCameraImage: React.FC<LazyCameraImageProps> = ({
  projectId,
  videoName,
  useRectified,
  alt,
  onSize,
}) => {
  const [url, setUrl] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) setVisible(true);
      },
      { rootMargin: '120px' },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  useEffect(() => {
    if (!visible) return undefined;
    let revoked: string | null = null;
    let cancelled = false;
    (async () => {
      try {
        const blob = await api.data.getVideoFrame(projectId, videoName, 0, useRectified);
        if (cancelled) return;
        revoked = URL.createObjectURL(blob);
        setUrl(revoked);
      } catch {
        if (!cancelled) setUrl(null);
      }
    })();
    return () => {
      cancelled = true;
      if (revoked) URL.revokeObjectURL(revoked);
    };
  }, [visible, projectId, videoName, useRectified]);

  return (
    <Box ref={ref} sx={{ position: 'relative', width: '100%', minHeight: 80 }}>
      {url ? (
        <Box
          component="img"
          src={url}
          alt={alt}
          onLoad={(e) => {
            const img = e.currentTarget;
            onSize?.(img.clientWidth, img.clientHeight);
          }}
          sx={{ width: '100%', height: 'auto', display: 'block' }}
        />
      ) : (
        <Box sx={{ py: 4, textAlign: 'center', color: 'text.secondary', fontSize: 13 }}>
          {visible ? 'Loading…' : ''}
        </Box>
      )}
    </Box>
  );
};

const RectificationWorkflow: React.FC<RectificationWorkflowProps> = ({
  projectId,
  videoFilesCount,
  rectificationState,
  rectificationBusy,
  calibrationRunning,
  manualMode,
  onManualModeChange: _onManualModeChange,
  onRefreshProject,
  onRectificationBusyChange,
  showSuccess,
  showError,
}) => {
  const [cameras, setCameras] = useState<RectificationCameraState[]>([]);
  const [drafts, setDrafts] = useState<Record<string, CameraDraft>>({});
  const [applyAll, setApplyAll] = useState(false);
  const [selectedCamId, setSelectedCamId] = useState('cam_00');
  const [previewLoading, setPreviewLoading] = useState(false);
  const [displaySizes, setDisplaySizes] = useState<Record<string, { w: number; h: number }>>({});
  const autoPreviewKeyRef = useRef('');

  const loadCameras = useCallback(async () => {
    if (videoFilesCount < 1) return;
    try {
      const res = await api.calibration.getRectificationCameras(projectId);
      setCameras(res.cameras);
      const next: Record<string, CameraDraft> = {};
      for (const cam of res.cameras) {
        const base = defaultParamsFromCamera(cam);
        const autoModel = (cam.auto_estimate?.model as DistortionModel) ?? null;
        next[cam.camera_id] = {
          ...base,
          rotationDeg: 0,
          autoModel,
          previewUrl: null,
        };
      }
      setDrafts(next);
      if (res.cameras.length > 0) setSelectedCamId(res.cameras[0].camera_id);
    } catch (err: unknown) {
      const e = err as { detail?: string; message?: string };
      showError(e.detail || e.message || 'Failed to load rectification cameras');
    }
  }, [projectId, videoFilesCount, showError]);

  useEffect(() => {
    loadCameras();
  }, [loadCameras, rectificationState]);

  const resolutionMismatch = useMemo(() => {
    if (cameras.length < 2) return false;
    const w = cameras[0].width;
    const h = cameras[0].height;
    return cameras.some((c) => c.width !== w || c.height !== h);
  }, [cameras]);

  useEffect(() => {
    if (!applyAll || cameras.length === 0) return;
    const driver = cameras[0].camera_id;
    const d = drafts[driver];
    if (!d) return;
    setDrafts((prev) => {
      const out = { ...prev };
      for (const cam of cameras) {
        if (cam.camera_id === driver || !out[cam.camera_id]) continue;
        out[cam.camera_id] = {
          ...out[cam.camera_id],
          model: d.model,
          k1: d.k1,
          k2: d.k2,
          previewUrl: null,
        };
      }
      return out;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only sync when applyAll toggled on
  }, [applyAll]);

  const paramCamId =
    applyAll && cameras.length > 0 ? cameras[0].camera_id : selectedCamId;
  const activeDraft = drafts[paramCamId];
  const activeCam = cameras.find((c) => c.camera_id === paramCamId);
  const rotationDraft = drafts[selectedCamId];

  const updateDraft = (camId: string, patch: Partial<CameraDraft>) => {
    setDrafts((prev) => {
      const cur = prev[camId];
      if (!cur) return prev;
      const updated = { ...cur, ...patch, previewUrl: null };
      if (applyAll && camId === paramCamId && ('model' in patch || 'k1' in patch || 'k2' in patch)) {
        const out = { ...prev, [camId]: updated };
        for (const id of Object.keys(prev)) {
          if (id === camId) continue;
          out[id] = {
            ...prev[id],
            model: updated.model,
            k1: updated.k1,
            k2: updated.k2,
            previewUrl: null,
          };
        }
        return out;
      }
      return { ...prev, [camId]: updated };
    });
  };

  const handleModelChange = (model: DistortionModel) => {
    if (!activeDraft || !activeCam) return;
    const autoModel = activeDraft.autoModel;
    const reset = !paramsMatchAutoModel(model, autoModel);
    updateDraft(paramCamId, {
      model,
      k1: reset ? 0 : activeDraft.k1,
      k2: reset ? 0 : activeDraft.k2,
    });
  };

  // Reload camera metadata whenever manual mode is entered.
  useEffect(() => {
    if (manualMode) loadCameras();
  }, [manualMode]); // eslint-disable-line react-hooks/exhaustive-deps

  const handlePreviewAll = async () => {
    setPreviewLoading(true);
    try {
      const entries = await Promise.all(
        cameras.map(async (cam) => {
          const d = drafts[cam.camera_id];
          if (!d) return [cam.camera_id, null] as const;
          const blob = await api.calibration.previewRectificationFrame(projectId, cam.camera_id, {
            model: d.model,
            k1: d.k1,
            k2: d.k2,
          });
          return [cam.camera_id, URL.createObjectURL(blob)] as const;
        }),
      );
      setDrafts((prev) => {
        const out = { ...prev };
        for (const [id, url] of entries) {
          if (out[id]?.previewUrl) URL.revokeObjectURL(out[id].previewUrl!);
          if (out[id]) out[id] = { ...out[id], previewUrl: url };
        }
        return out;
      });
      showSuccess('Rectified preview updated (frame 0). Adjust sliders to tune further.');
    } catch (err: unknown) {
      const e = err as { detail?: string; message?: string };
      showError(e.detail || e.message || 'Preview failed');
    } finally {
      setPreviewLoading(false);
    }
  };

  // Auto-estimation does not enter manual mode. Once GeoCalib finishes, render
  // authoritative frame-0 previews from its parameters in the existing rows.
  useEffect(() => {
    if (
      rectificationState !== 'READY_FOR_REVIEW' ||
      manualMode ||
      cameras.length === 0 ||
      !cameras.every((cam) => cam.auto_estimate)
    ) {
      if (rectificationState !== 'READY_FOR_REVIEW') {
        autoPreviewKeyRef.current = '';
      }
      return;
    }
    const key = cameras
      .map((cam) => {
        const p = cam.auto_estimate!;
        return `${cam.camera_id}:${p.model}:${p.k1}:${p.k2}`;
      })
      .join('|');
    if (autoPreviewKeyRef.current === key) return;
    autoPreviewKeyRef.current = key;

    let cancelled = false;
    setPreviewLoading(true);
    Promise.all(
      cameras.map(async (cam) => {
        const p = cam.auto_estimate!;
        const blob = await api.calibration.previewRectificationFrame(
          projectId,
          cam.camera_id,
          p,
        );
        return [cam.camera_id, URL.createObjectURL(blob)] as const;
      }),
    )
      .then((entries) => {
        if (cancelled) {
          entries.forEach(([, url]) => URL.revokeObjectURL(url));
          return;
        }
        setDrafts((prev) => {
          const out = { ...prev };
          for (const [id, url] of entries) {
            if (out[id]?.previewUrl) URL.revokeObjectURL(out[id].previewUrl!);
            if (out[id]) out[id] = { ...out[id], previewUrl: url };
          }
          return out;
        });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const e = err as { detail?: string; message?: string };
          showError(e.detail || e.message || 'Auto-estimate preview failed');
        }
      })
      .finally(() => {
        if (!cancelled) setPreviewLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [cameras, manualMode, projectId, rectificationState, showError]);

  const handleGenerateVideos = async () => {
    try {
      onRectificationBusyChange(true);
      const payload: Record<string, DistortionParams> = {};
      for (const cam of cameras) {
        const d = drafts[cam.camera_id];
        if (!d) continue;
        payload[cam.camera_id] = { model: d.model, k1: d.k1, k2: d.k2 };
      }
      const res = await api.calibration.commitManualRectification(projectId, payload);
      showSuccess(res.message);
      await onRefreshProject();
    } catch (err: unknown) {
      onRectificationBusyChange(false);
      const e = err as { detail?: string; message?: string };
      showError(e.detail || e.message || 'Failed to generate rectified videos');
    }
  };

  const controlsDisabled =
    rectificationBusy ||
    calibrationRunning ||
    rectificationState === 'RUNNING' ||
    !manualMode ||
    (applyAll && paramCamId !== cameras[0]?.camera_id);

  const showPreview = videoFilesCount > 0;

  return (
    <Box>
      {manualMode && activeDraft && activeCam && (
        <Box sx={{ mt: 2 }}>
          {applyAll && resolutionMismatch && (
            <Alert severity="warning" sx={{ mb: 2 }}>
              Cameras have different resolutions. The same numeric k1/k2 may not represent the
              same physical distortion on every camera.
            </Alert>
          )}
          <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} flexWrap="wrap">
            <FormControl size="small" sx={{ minWidth: 160 }} disabled={controlsDisabled && !applyAll}>
              <InputLabel>{applyAll ? 'Rotation camera' : 'Camera'}</InputLabel>
              <Select
                label={applyAll ? 'Rotation camera' : 'Camera'}
                value={selectedCamId}
                onChange={(e) => setSelectedCamId(e.target.value)}
              >
                {cameras.map((c) => (
                  <MenuItem key={c.camera_id} value={c.camera_id}>
                    {c.camera_id}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <FormControl size="small" sx={{ minWidth: 180 }} disabled={controlsDisabled}>
              <InputLabel>Model</InputLabel>
              <Select
                label="Model"
                value={activeDraft.model}
                onChange={(e) => handleModelChange(e.target.value as DistortionModel)}
              >
                {MODELS.map((m) => (
                  <MenuItem key={m} value={m}>
                    {m}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <FormControlLabel
              control={
                <Checkbox checked={applyAll} onChange={(e) => setApplyAll(e.target.checked)} />
              }
              label="Apply same model/params to all cameras (cam_00 drives)"
            />
          </Stack>

          <Typography variant="body2" sx={{ mt: 1 }}>
            k1: {activeDraft.k1.toExponential(3)}
          </Typography>
          <Slider
            disabled={controlsDisabled}
            min={activeCam.k1_min}
            max={activeCam.k1_max}
            step={(activeCam.k1_max - activeCam.k1_min) / 500}
            value={activeDraft.k1}
            onChange={(_, v) => updateDraft(paramCamId, { k1: v as number })}
          />
          {activeDraft.model === 'radial' && (
            <>
              <Typography variant="body2">k2: {activeDraft.k2.toExponential(3)}</Typography>
              <Slider
                disabled={controlsDisabled}
                min={activeCam.k2_min}
                max={activeCam.k2_max}
                step={(activeCam.k2_max - activeCam.k2_min) / 500}
                value={activeDraft.k2}
                onChange={(_, v) => updateDraft(paramCamId, { k2: v as number })}
              />
            </>
          )}
          <TextField
            size="small"
            label="Contour rotation (deg)"
            type="number"
            sx={{ mt: 1, maxWidth: 200 }}
            value={rotationDraft?.rotationDeg ?? 0}
            onChange={(e) =>
              updateDraft(selectedCamId, {
                rotationDeg: parseFloat(e.target.value) || 0,
              })
            }
          />

          <Stack direction="row" spacing={2} sx={{ mt: 2 }} flexWrap="wrap">
            <Button
              variant="outlined"
              onClick={handlePreviewAll}
              disabled={previewLoading || rectificationState === 'RUNNING'}
            >
              Rectify Image
            </Button>
          </Stack>
        </Box>
      )}

      {showPreview && (
        <Box
          sx={{
            mt: 2,
            maxHeight: 420,
            overflowY: 'auto',
            border: '1px solid',
            borderColor: 'divider',
            borderRadius: 1,
            p: 1,
          }}
        >
          {cameras.map((cam) => {
            const d = drafts[cam.camera_id];
            const size = displaySizes[cam.camera_id];
            const showGrid = manualMode && d;
            return (
              <Box
                key={cam.camera_id}
                sx={{
                  display: 'grid',
                  gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' },
                  gap: 1,
                  mb: 2,
                  pb: 2,
                  borderBottom: '1px solid',
                  borderColor: 'divider',
                }}
              >
                <Box>
                  <Typography variant="caption" color="text.secondary">
                    Original — {cam.camera_id}
                  </Typography>
                  <LazyCameraImage
                    projectId={projectId}
                    videoName={cam.video_name}
                    useRectified={false}
                    alt="original"
                  />
                </Box>
                <Box sx={{ position: 'relative' }}>
                  <Typography variant="caption" color="text.secondary">
                    {d?.previewUrl
                      ? rectificationState === 'READY_FOR_REVIEW' && !manualMode
                        ? 'GeoCalib rectified preview'
                        : 'Rectified preview'
                      : manualMode
                        ? 'Grid overlay (absolute params)'
                        : 'Rectified'}
                  </Typography>
                  {d?.previewUrl ? (
                    <Box sx={{ position: 'relative' }}>
                      <Box
                        component="img"
                        src={d.previewUrl}
                        alt="preview"
                        onLoad={(event: React.SyntheticEvent<HTMLImageElement>) => {
                          // Read the element before the updater runs; React clears
                          // currentTarget once the handler returns.
                          const { clientWidth, clientHeight } = event.currentTarget;
                          setDisplaySizes((s) => ({
                            ...s,
                            [cam.camera_id]: { w: clientWidth, h: clientHeight },
                          }));
                        }}
                        sx={{ width: '100%', height: 'auto', display: 'block' }}
                      />
                      {showGrid && d && size && (
                        <DistortionGridOverlay
                          lines={cam.grid_lines}
                          cx={cam.cx}
                          cy={cam.cy}
                          displayWidth={size.w}
                          displayHeight={size.h}
                          nativeWidth={cam.width}
                          nativeHeight={cam.height}
                          model={d.model}
                          k1={d.k1}
                          k2={d.k2}
                          rotationDeg={d.rotationDeg}
                          visible
                        />
                      )}
                    </Box>
                  ) : (
                    <Box sx={{ position: 'relative' }}>
                      <LazyCameraImage
                        projectId={projectId}
                        videoName={cam.video_name}
                        useRectified={
                          !manualMode && rectificationState === 'COMPLETED'
                        }
                        alt="working"
                        onSize={(w, h) =>
                          setDisplaySizes((s) => ({ ...s, [cam.camera_id]: { w, h } }))
                        }
                      />
                      {showGrid && d && size && (
                        <DistortionGridOverlay
                          lines={cam.grid_lines}
                          cx={cam.cx}
                          cy={cam.cy}
                          displayWidth={size.w}
                          displayHeight={size.h}
                          nativeWidth={cam.width}
                          nativeHeight={cam.height}
                          model={d.model}
                          k1={d.k1}
                          k2={d.k2}
                          rotationDeg={d.rotationDeg}
                          visible
                        />
                      )}
                    </Box>
                  )}
                </Box>
              </Box>
            );
          })}
        </Box>
      )}

      {(manualMode || rectificationState === 'READY_FOR_REVIEW') && cameras.length > 0 && (
        <Box sx={{ mt: 2 }}>
          <Button
            variant="contained"
            onClick={handleGenerateVideos}
            disabled={
              rectificationBusy ||
              rectificationState === 'RUNNING' ||
              cameras.some((cam) => !drafts[cam.camera_id])
            }
          >
            Generate Rectified Videos
          </Button>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ mt: 1, display: 'block' }}
          >
            Applies the selected parameters to all videos. This may take several minutes.
          </Typography>
        </Box>
      )}

      {!manualMode && videoFilesCount > 0 && (
        <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
          {previewLoading
            ? 'Generating frame-0 previews…'
            : rectificationState === 'READY_FOR_REVIEW'
              ? 'Review the GeoCalib previews, then generate rectified videos. To retune distortion, use Manual Rectification.'
              : 'Start Manual Rectification to tune distortion with live grid feedback.'}
        </Typography>
      )}
    </Box>
  );
};

export default RectificationWorkflow;

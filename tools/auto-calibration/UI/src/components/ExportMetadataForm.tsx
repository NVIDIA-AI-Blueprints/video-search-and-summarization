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
 * Edit optional sensor-level, per-sensor ROI, and root-level global ROI fields merged into *_exported.json.
 */

import React from 'react';
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Button,
  IconButton,
  TextField,
  Typography,
  Stack,
  Divider,
  CircularProgress,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import AddIcon from '@mui/icons-material/Add';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import type {
  SensorMetaEdit,
  PlaceLevel,
  RoiMetaEdit,
  TripwireMetaEdit,
  GlobalRootRoiMetaEdit,
  GlobalRootTripwireMetaEdit,
} from '../utils/exportMetadata';

export interface ExportMetadataFormProps {
  edits: Record<number, SensorMetaEdit> | null;
  sensorLabels: string[];
  loading: boolean;
  onChange: (next: Record<number, SensorMetaEdit>) => void;
  onLoadFromServer: () => void;
  disabled?: boolean;
  globalRoiEdits?: Record<number, GlobalRootRoiMetaEdit> | null;
  onGlobalRoiEditsChange?: (next: Record<number, GlobalRootRoiMetaEdit>) => void;
  globalTripwireEdits?: Record<number, GlobalRootTripwireMetaEdit> | null;
  onGlobalTripwireEditsChange?: (next: Record<number, GlobalRootTripwireMetaEdit>) => void;
}

function updatePlaceLevel(
  edits: Record<number, SensorMetaEdit>,
  sensorIndex: number,
  placeIndex: number,
  patch: Partial<PlaceLevel>
): Record<number, SensorMetaEdit> {
  const s = edits[sensorIndex];
  if (!s) return edits;
  const place = [...s.place];
  place[placeIndex] = { ...place[placeIndex], ...patch };
  return { ...edits, [sensorIndex]: { ...s, place } };
}

function addPlaceLevel(edits: Record<number, SensorMetaEdit>, sensorIndex: number): Record<number, SensorMetaEdit> {
  const s = edits[sensorIndex];
  if (!s) return edits;
  return {
    ...edits,
    [sensorIndex]: { ...s, place: [...s.place, { name: '', value: '' }] },
  };
}

function removePlaceLevel(
  edits: Record<number, SensorMetaEdit>,
  sensorIndex: number,
  placeIndex: number
): Record<number, SensorMetaEdit> {
  const s = edits[sensorIndex];
  if (!s) return edits;
  const place = s.place.filter((_, i) => i !== placeIndex);
  return { ...edits, [sensorIndex]: { ...s, place } };
}

function updateRoi(
  edits: Record<number, SensorMetaEdit>,
  sensorIndex: number,
  roiIndex: number,
  patch: Partial<RoiMetaEdit>
): Record<number, SensorMetaEdit> {
  const s = edits[sensorIndex];
  if (!s) return edits;
  const rois = [...s.rois];
  rois[roiIndex] = { ...rois[roiIndex], ...patch };
  return { ...edits, [sensorIndex]: { ...s, rois } };
}

function updateTripwire(
  edits: Record<number, SensorMetaEdit>,
  sensorIndex: number,
  twIndex: number,
  patch: Partial<TripwireMetaEdit>
): Record<number, SensorMetaEdit> {
  const s = edits[sensorIndex];
  if (!s) return edits;
  const tripwires = [...s.tripwires];
  tripwires[twIndex] = { ...tripwires[twIndex], ...patch };
  return { ...edits, [sensorIndex]: { ...s, tripwires } };
}

function patchSensor(
  edits: Record<number, SensorMetaEdit>,
  sensorIndex: number,
  patch: Partial<SensorMetaEdit>
): Record<number, SensorMetaEdit> {
  const s = edits[sensorIndex];
  if (!s) return edits;
  return { ...edits, [sensorIndex]: { ...s, ...patch } };
}

function patchGlobalRootRoi(
  edits: Record<number, GlobalRootRoiMetaEdit>,
  index: number,
  patch: Partial<GlobalRootRoiMetaEdit>
): Record<number, GlobalRootRoiMetaEdit> {
  const cur = edits[index];
  if (!cur) return edits;
  return { ...edits, [index]: { ...cur, ...patch } };
}

function patchGlobalRootTripwire(
  edits: Record<number, GlobalRootTripwireMetaEdit>,
  index: number,
  patch: Partial<GlobalRootTripwireMetaEdit>
): Record<number, GlobalRootTripwireMetaEdit> {
  const cur = edits[index];
  if (!cur) return edits;
  return { ...edits, [index]: { ...cur, ...patch } };
}

const ExportMetadataForm: React.FC<ExportMetadataFormProps> = ({
  edits,
  sensorLabels,
  loading,
  onChange,
  onLoadFromServer,
  disabled = false,
  globalRoiEdits = null,
  onGlobalRoiEditsChange,
  globalTripwireEdits = null,
  onGlobalTripwireEditsChange,
}) => {
  if (edits === null) {
    if (loading) {
      return (
        <Box sx={{ py: 4, display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 2 }}>
          <CircularProgress size={28} />
          <Typography variant="body2" color="text.secondary">
            Loading export from server…
          </Typography>
        </Box>
      );
    }
    return (
      <Box sx={{ py: 1 }}>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          Load the current server export to edit optional metadata (merged into the file when you download).
        </Typography>
        <Button variant="outlined" size="small" onClick={onLoadFromServer} disabled={disabled}>
          Load export metadata
        </Button>
      </Box>
    );
  }

  return (
    <Stack spacing={1}>
      {sensorLabels.map((label, si) => {
        const s = edits[si];
        if (!s) return null;
        return (
          <Accordion key={`sensor-meta-${si}`} defaultExpanded={si === 0} disableGutters>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              <Typography fontWeight={600}>{label}</Typography>
            </AccordionSummary>
            <AccordionDetails>
              <Stack spacing={2}>
                <Typography variant="caption" color="text.secondary">
                  Sensor attributes (optional)
                </Typography>
                <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} flexWrap="wrap">
                  <TextField
                    size="small"
                    label="Depth"
                    value={s.depth}
                    onChange={(e) => onChange(patchSensor(edits, si, { depth: e.target.value }))}
                    disabled={disabled}
                    sx={{ minWidth: 140 }}
                  />
                  <TextField
                    size="small"
                    label="Field of view"
                    value={s.fieldOfView}
                    onChange={(e) => onChange(patchSensor(edits, si, { fieldOfView: e.target.value }))}
                    disabled={disabled}
                    sx={{ minWidth: 140 }}
                  />
                  <TextField
                    size="small"
                    label="Direction"
                    value={s.direction}
                    onChange={(e) => onChange(patchSensor(edits, si, { direction: e.target.value }))}
                    disabled={disabled}
                    sx={{ minWidth: 140 }}
                  />
                  <TextField
                    size="small"
                    label="Direction 3D"
                    value={s.direction3d}
                    onChange={(e) => onChange(patchSensor(edits, si, { direction3d: e.target.value }))}
                    disabled={disabled}
                    sx={{ minWidth: 140 }}
                  />
                </Stack>

                <Divider />

                <Box>
                  <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
                    Place hierarchy — add any levels (name + value);
                  </Typography>
                  {s.place.map((pl, pi) => (
                    <Stack direction="row" spacing={1} key={`pl-${si}-${pi}`} sx={{ mb: 1 }} alignItems="center">
                      <TextField
                        size="small"
                        label="Level name"
                        value={pl.name}
                        onChange={(e) =>
                          onChange(updatePlaceLevel(edits, si, pi, { name: e.target.value }))
                        }
                        disabled={disabled}
                        sx={{ flex: 1 }}
                      />
                      <TextField
                        size="small"
                        label="Value"
                        value={pl.value}
                        onChange={(e) =>
                          onChange(updatePlaceLevel(edits, si, pi, { value: e.target.value }))
                        }
                        disabled={disabled}
                        sx={{ flex: 1 }}
                      />
                      <IconButton
                        size="small"
                        aria-label="Remove place level"
                        onClick={() => onChange(removePlaceLevel(edits, si, pi))}
                        disabled={disabled}
                      >
                        <DeleteOutlineIcon fontSize="small" />
                      </IconButton>
                    </Stack>
                  ))}
                  <Button
                    size="small"
                    startIcon={<AddIcon />}
                    onClick={() => onChange(addPlaceLevel(edits, si))}
                    disabled={disabled}
                  >
                    Add place level
                  </Button>
                </Box>

                <Divider />

                {s.rois.map((r, ri) => (
                  <Box key={`roi-meta-${si}-${ri}`}>
                    <Typography variant="subtitle2" sx={{ mb: 1 }} component="div">
                      Per-ROI #{ri + 1}{' '}
                    </Typography>
                    <Stack spacing={1.5}>
                      <TextField
                        size="small"
                        fullWidth
                        label="id"
                        value={r.id}
                        onChange={(e) => onChange(updateRoi(edits, si, ri, { id: e.target.value }))}
                        disabled={disabled}
                        helperText="ID should be unique"
                      />
                      <TextField
                        size="small"
                        fullWidth
                        label="restrictedObjectTypes (comma-separated)"
                        value={r.restrictedObjectTypesInput}
                        onChange={(e) =>
                          onChange(
                            updateRoi(edits, si, ri, {
                              restrictedObjectTypesInput: e.target.value,
                            }),
                          )
                        }
                        disabled={disabled}
                      />
                      <TextField
                        size="small"
                        fullWidth
                        label="confinedObjectTypes (comma-separated)"
                        value={r.confinedObjectTypesInput}
                        onChange={(e) =>
                          onChange(
                            updateRoi(edits, si, ri, {
                              confinedObjectTypesInput: e.target.value,
                            }),
                          )
                        }
                        disabled={disabled}
                      />
                      <TextField
                        size="small"
                        fullWidth
                        label="type (optional)"
                        value={r.type}
                        onChange={(e) => onChange(updateRoi(edits, si, ri, { type: e.target.value }))}
                        disabled={disabled}
                      />
                    </Stack>
                  </Box>
                ))}

                {s.tripwires.length > 0 && (
                  <>
                    <Divider sx={{ my: 1 }} />
                    <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
                      Per-sensor tripwires
                    </Typography>
                    {s.tripwires.map((tw, ti) => (
                      <Box key={`tw-meta-${si}-${ti}`} sx={{ mb: 1.5 }}>
                        <Typography variant="subtitle2" sx={{ mb: 1 }}>
                          Tripwire #{ti + 1}
                        </Typography>
                        <TextField
                          size="small"
                          fullWidth
                          label="id"
                          value={tw.id}
                          onChange={(e) =>
                            onChange(updateTripwire(edits, si, ti, { id: e.target.value }))
                          }
                          disabled={disabled}
                          helperText="ID should be unique"
                        />
                      </Box>
                    ))}
                  </>
                )}
              </Stack>
            </AccordionDetails>
          </Accordion>
        );
      })}

      {globalRoiEdits != null &&
        Object.keys(globalRoiEdits).length > 0 &&
        onGlobalRoiEditsChange != null && (
          <Accordion defaultExpanded={false} disableGutters sx={{ mt: 1 }}>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              <Typography fontWeight={600}>
                Global ROIs{' '}
                <Typography component="span" variant="body2" fontWeight={400} color="text.secondary">
                  (<code>rois</code> · {Object.keys(globalRoiEdits).length})
                </Typography>
              </Typography>
            </AccordionSummary>
            <AccordionDetails>
              <Stack spacing={2}>
                <Typography variant="caption" color="text.secondary">
                  Type and object-type lists (comma-separated). Coordinates are from calibration; edit metadata
                  before download to persist into the project export.
                </Typography>
                {Object.keys(globalRoiEdits)
                  .map(Number)
                  .sort((a, b) => a - b)
                  .map((gi) => {
                    const g = globalRoiEdits[gi];
                    if (!g) return null;
                    return (
                      <Box key={`global-roi-meta-${gi}`} sx={{ mb: 2 }}>
                        <Typography variant="body2" fontWeight={600} sx={{ mb: 1 }}>
                          Global ROI #{gi + 1}
                        </Typography>
                        <Stack spacing={1.5}>
                          <TextField
                            size="small"
                            fullWidth
                            label="id"
                            value={g.id}
                            onChange={(e) =>
                              onGlobalRoiEditsChange(
                                patchGlobalRootRoi(globalRoiEdits, gi, { id: e.target.value }),
                              )
                            }
                            disabled={disabled}
                            helperText="ID should be unique"
                          />
                          <TextField
                            size="small"
                            fullWidth
                            label="type"
                            placeholder="e.g. hazard_zone"
                            value={g.type}
                            onChange={(e) =>
                              onGlobalRoiEditsChange(
                                patchGlobalRootRoi(globalRoiEdits, gi, { type: e.target.value }),
                              )
                            }
                            disabled={disabled}
                          />
                          <TextField
                            size="small"
                            fullWidth
                            label="restrictedObjectTypes"
                            value={g.restrictedObjectTypesInput}
                            onChange={(e) =>
                              onGlobalRoiEditsChange(
                                patchGlobalRootRoi(globalRoiEdits, gi, {
                                  restrictedObjectTypesInput: e.target.value,
                                }),
                              )
                            }
                            disabled={disabled}
                          />
                          <TextField
                            size="small"
                            fullWidth
                            label="confinedObjectTypes"
                            value={g.confinedObjectTypesInput}
                            onChange={(e) =>
                              onGlobalRoiEditsChange(
                                patchGlobalRootRoi(globalRoiEdits, gi, {
                                  confinedObjectTypesInput: e.target.value,
                                }),
                              )
                            }
                            disabled={disabled}
                          />
                        </Stack>
                      </Box>
                    );
                  })}
              </Stack>
            </AccordionDetails>
          </Accordion>
        )}

      {globalTripwireEdits != null &&
        Object.keys(globalTripwireEdits).length > 0 &&
        onGlobalTripwireEditsChange != null && (
          <Accordion defaultExpanded={false} disableGutters sx={{ mt: 1 }}>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              <Typography fontWeight={600}>
                Global tripwires{' '}
                <Typography component="span" variant="body2" fontWeight={400} color="text.secondary">
                  (<code>tripwires</code> · {Object.keys(globalTripwireEdits).length})
                </Typography>
              </Typography>
            </AccordionSummary>
            <AccordionDetails>
              <Stack spacing={2}>
                <Typography variant="caption" color="text.secondary">
                  <code>id</code> only. Geometry stays{' '}
                  <code>wire</code> / <code>direction</code> from calibration; edit metadata before download to persist
                  into the project export.
                </Typography>
                {Object.keys(globalTripwireEdits)
                  .map(Number)
                  .sort((a, b) => a - b)
                  .map((ti) => {
                    const g = globalTripwireEdits[ti];
                    if (!g) return null;
                    return (
                      <Box key={`global-tw-meta-${ti}`} sx={{ mb: 2 }}>
                        <Typography variant="body2" fontWeight={600} sx={{ mb: 1 }}>
                          Global tripwire #{ti + 1}
                        </Typography>
                        <Stack spacing={1.5}>
                          <TextField
                            size="small"
                            fullWidth
                            label="id"
                            value={g.id}
                            onChange={(e) =>
                              onGlobalTripwireEditsChange(
                                patchGlobalRootTripwire(globalTripwireEdits, ti, { id: e.target.value }),
                              )
                            }
                            disabled={disabled}
                            helperText="ID should be unique"
                          />
                        </Stack>
                      </Box>
                    );
                  })}
              </Stack>
            </AccordionDetails>
          </Accordion>
        )}
    </Stack>
  );
};

export default ExportMetadataForm;

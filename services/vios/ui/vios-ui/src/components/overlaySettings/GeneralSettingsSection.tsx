/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
import React from 'react';
import { Box, Grid, Typography, FormControlLabel, Switch, TextField, Tooltip } from '@mui/material';
import { StreamType } from 'vst-streaming-lib';

interface GeneralSettingsSectionProps {
    overlayDebug: boolean;
    setOverlayDebug: (value: boolean) => void;
    pose: boolean;
    setPose: (value: boolean) => void;
    needHalo: boolean;
    setNeedHalo: (value: boolean) => void;
    tag: string;
    setTag: (value: string) => void;
    framerateValue: number;
    setFramerateValue: (value: number) => void;
    includeFloorPlan: boolean;
    setIncludeFloorPlan: (value: boolean) => void;
    streamType?: StreamType;
}

const GeneralSettingsSection: React.FC<GeneralSettingsSectionProps> = ({
    overlayDebug,
    setOverlayDebug,
    pose,
    setPose,
    needHalo,
    setNeedHalo,
    tag,
    setTag,
    framerateValue,
    setFramerateValue,
    includeFloorPlan,
    setIncludeFloorPlan,
    streamType,
}) => {
    return (
        <>
            <Box
                sx={{
                    bgcolor: 'rgba(0, 0, 0, 0.02)',
                    borderRadius: 2,
                    p: 2,
                    mb: 2,
                    border: '1px solid rgba(0, 0, 0, 0.08)',
                }}
            >
                <Typography variant='h6' sx={{ mb: 1.5, fontWeight: 600, color: 'primary.main' }}>
                    Basic Settings
                </Typography>
                <Grid container spacing={2}>
                    <Grid item xs={12} sm={6}>
                        <FormControlLabel
                            control={<Switch checked={overlayDebug} onChange={e => setOverlayDebug(e.target.checked)} />}
                            label='Debug Mode'
                            sx={{ '& .MuiFormControlLabel-label': { fontWeight: 500 } }}
                        />
                    </Grid>
                    <Grid item xs={12} sm={6}>
                        <Tooltip title='Only for 2D' placement='top'>
                            <FormControlLabel
                                control={<Switch checked={pose} onChange={e => setPose(e.target.checked)} />}
                                label='Show Pose'
                                sx={{ '& .MuiFormControlLabel-label': { fontWeight: 500 } }}
                            />
                        </Tooltip>
                    </Grid>
                    <Grid item xs={12} sm={6}>
                        <FormControlLabel
                            control={<Switch checked={needHalo} onChange={e => setNeedHalo(e.target.checked)} />}
                            label='Show Halo'
                            sx={{ '& .MuiFormControlLabel-label': { fontWeight: 500 } }}
                        />
                    </Grid>
                    {streamType === StreamType.VideoWall && (
                        <Grid item xs={12} sm={6}>
                            <Tooltip title='Only for 3D' placement='top'>
                                <FormControlLabel
                                    control={<Switch checked={includeFloorPlan} onChange={e => setIncludeFloorPlan(e.target.checked)} />}
                                    label='Include Floor Plan'
                                    sx={{ '& .MuiFormControlLabel-label': { fontWeight: 500 } }}
                                />
                            </Tooltip>
                        </Grid>
                    )}
                </Grid>
            </Box>

            <Box
                sx={{
                    bgcolor: 'rgba(0, 0, 0, 0.02)',
                    borderRadius: 2,
                    p: 2,
                    mb: 2,
                    border: '1px solid rgba(0, 0, 0, 0.08)',
                }}
            >
                <Typography variant='h6' sx={{ mb: 1.5, fontWeight: 600, color: 'primary.main' }}>
                    Configuration
                </Typography>
                <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
                    {streamType === StreamType.VideoWall && (
                        <TextField
                            fullWidth
                            type='number'
                            label='Framerate'
                            value={framerateValue}
                            onChange={e => setFramerateValue(parseInt(e.target.value) || 15)}
                            helperText='Set the framerate for the overlay'
                            sx={{ '& .MuiInputLabel-root': { fontWeight: 500 } }}
                        />
                    )}
                    <TextField
                        fullWidth
                        label='Virtual stream tag'
                        value={tag}
                        onChange={e => setTag(e.target.value)}
                        sx={{ '& .MuiInputLabel-root': { fontWeight: 500 } }}
                    />
                </Box>
            </Box>
        </>
    );
};

export default GeneralSettingsSection;

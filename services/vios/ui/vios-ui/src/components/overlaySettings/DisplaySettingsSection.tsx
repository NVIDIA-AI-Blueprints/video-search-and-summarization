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
import { Box, Grid, Typography, Slider, Tooltip } from '@mui/material';

interface DisplaySettingsSectionProps {
    bboxThickness: number;
    setBboxThickness: (value: number) => void;
    overlayOpacity: number;
    setOverlayOpacity: (value: number) => void;
    proximityAreaFactor: number;
    setProximityAreaFactor: (value: number) => void;
}

const DisplaySettingsSection: React.FC<DisplaySettingsSectionProps> = ({
    bboxThickness,
    setBboxThickness,
    overlayOpacity,
    setOverlayOpacity,
    proximityAreaFactor,
    setProximityAreaFactor,
}) => {
    return (
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
                Display Settings
            </Typography>
            <Grid container spacing={2}>
                <Grid item xs={12} sm={4}>
                    <Box sx={{ p: 2, bgcolor: 'background.paper', borderRadius: 1, border: '1px solid rgba(0, 0, 0, 0.05)' }}>
                        <Typography gutterBottom sx={{ fontWeight: 500, color: 'text.primary' }}>
                            Line Thickness: {bboxThickness}px
                        </Typography>
                        <Slider
                            value={bboxThickness}
                            onChange={(_, value) => setBboxThickness(value as number)}
                            min={1}
                            max={10}
                            valueLabelDisplay='auto'
                            sx={{ mt: 1 }}
                        />
                    </Box>
                </Grid>
                <Grid item xs={12} sm={4}>
                    <Box sx={{ p: 2, bgcolor: 'background.paper', borderRadius: 1, border: '1px solid rgba(0, 0, 0, 0.05)' }}>
                        <Typography gutterBottom sx={{ fontWeight: 500, color: 'text.primary' }}>
                            Opacity: {overlayOpacity}
                        </Typography>
                        <Slider
                            value={overlayOpacity}
                            onChange={(_, value) => setOverlayOpacity(value as number)}
                            min={0}
                            max={255}
                            valueLabelDisplay='auto'
                            sx={{ mt: 1 }}
                        />
                    </Box>
                </Grid>
                <Grid item xs={12} sm={4}>
                    <Tooltip title='Only for 3D' placement='top'>
                        <Box sx={{ p: 2, bgcolor: 'background.paper', borderRadius: 1, border: '1px solid rgba(0, 0, 0, 0.05)' }}>
                            <Typography gutterBottom sx={{ fontWeight: 500, color: 'text.primary' }}>
                                Proximity Area Factor: {proximityAreaFactor.toFixed(1)}
                            </Typography>
                            <Slider
                                value={proximityAreaFactor}
                                onChange={(_, value) => setProximityAreaFactor(value as number)}
                                min={0.1}
                                max={5}
                                step={0.1}
                                valueLabelDisplay='auto'
                                sx={{ mt: 1 }}
                            />
                        </Box>
                    </Tooltip>
                </Grid>
            </Grid>
        </Box>
    );
};

export default DisplaySettingsSection;

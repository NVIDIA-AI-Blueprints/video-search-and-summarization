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
import { Box, Grid, Typography, FormControl, InputLabel, Select, MenuItem, Tooltip } from '@mui/material';

interface ProximitySettingsSectionProps {
    proximityClass: string[];
    setProximityClass: (value: string[]) => void;
    entrantClass: string[];
    setEntrantClass: (value: string[]) => void;
    proximityAnimation: string;
    setProximityAnimation: (value: string) => void;
    availableClassLabels?: string[];
}

const ProximitySettingsSection: React.FC<ProximitySettingsSectionProps> = ({
    proximityClass,
    setProximityClass,
    entrantClass,
    setEntrantClass,
    proximityAnimation,
    setProximityAnimation,
    availableClassLabels,
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
                Proximity Settings
            </Typography>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
                <Grid container spacing={2}>
                    <Grid item xs={12} sm={6}>
                        <Tooltip title='Only for 3D' placement='top'>
                            <FormControl fullWidth>
                                <InputLabel sx={{ fontWeight: 500 }}>Proximity Class</InputLabel>
                                <Select
                                    multiple
                                    value={proximityClass}
                                    onChange={e => setProximityClass(e.target.value as string[])}
                                    label='Proximity Class'
                                >
                                    {availableClassLabels?.map(label => (
                                        <MenuItem key={label} value={label}>
                                            {label}
                                        </MenuItem>
                                    )) || []}
                                </Select>
                            </FormControl>
                        </Tooltip>
                    </Grid>
                    <Grid item xs={12} sm={6}>
                        <Tooltip title='Only for 3D' placement='top'>
                            <FormControl fullWidth>
                                <InputLabel sx={{ fontWeight: 500 }}>Entrant Class</InputLabel>
                                <Select
                                    multiple
                                    value={entrantClass}
                                    onChange={e => setEntrantClass(e.target.value as string[])}
                                    label='Entrant Class'
                                >
                                    {availableClassLabels?.map(label => (
                                        <MenuItem key={label} value={label}>
                                            {label}
                                        </MenuItem>
                                    )) || []}
                                </Select>
                            </FormControl>
                        </Tooltip>
                    </Grid>
                </Grid>
                <Tooltip title='Only for 3D' placement='top'>
                    <FormControl fullWidth>
                        <InputLabel sx={{ fontWeight: 500 }}>Proximity Animation</InputLabel>
                        <Select
                            value={proximityAnimation}
                            onChange={e => setProximityAnimation(e.target.value)}
                            label='Proximity Animation'
                        >
                            <MenuItem value='circleAndLine'>Circle and Line</MenuItem>
                            <MenuItem value='circleOnly'>Circle Only</MenuItem>
                            <MenuItem value='lineOnly'>Line Only</MenuItem>
                            <MenuItem value='ellipseOnly'>Ellipse Only</MenuItem>
                            <MenuItem value='ellipseAndLine'>Ellipse and Line</MenuItem>
                        </Select>
                    </FormControl>
                </Tooltip>
            </Box>
        </Box>
    );
};

export default ProximitySettingsSection;

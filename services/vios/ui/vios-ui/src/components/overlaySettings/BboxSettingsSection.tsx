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
import { Box, Grid, Typography, FormControlLabel, Switch, TextField, FormControl, InputLabel, Select, MenuItem } from '@mui/material';

interface BboxSettingsSectionProps {
    overlayBbox: boolean;
    setOverlayBbox: (value: boolean) => void;
    classType: string[];
    setClassType: (value: string[]) => void;
    objectIds: string;
    setObjectIds: (value: string) => void;
    showObjId: boolean;
    setShowObjId: (value: boolean) => void;
    objIdPosition: number;
    setObjIdPosition: (value: number) => void;
    objIdTextColor: string;
    setObjIdTextColor: (value: string) => void;
    objIdTextBGColor: string;
    setObjIdTextBGColor: (value: string) => void;
    availableClassLabels?: string[];
}

const positionOptions = [
    { value: 0, label: 'Middle', sx: { top: '50%', left: '50%', transform: 'translate(-50%, -50%)' } },
    { value: 1, label: 'Top Left', sx: { top: 2, left: 2 } },
    { value: 2, label: 'Top Right', sx: { top: 2, right: 2 } },
    { value: 3, label: 'Bottom Left', sx: { bottom: 2, left: 2 } },
    { value: 4, label: 'Bottom Right', sx: { bottom: 2, right: 2 } },
];

const colorOptions = ['white', 'black', 'red', 'green', 'blue', 'yellow'];

const BboxSettingsSection: React.FC<BboxSettingsSectionProps> = ({
    overlayBbox,
    setOverlayBbox,
    classType,
    setClassType,
    objectIds,
    setObjectIds,
    showObjId,
    setShowObjId,
    objIdPosition,
    setObjIdPosition,
    objIdTextColor,
    setObjIdTextColor,
    objIdTextBGColor,
    setObjIdTextBGColor,
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
            <Typography variant='h6' sx={{ mb: 3, fontWeight: 600, color: 'primary.main' }}>
                Bounding Box Configuration
            </Typography>

            <Box sx={{ mb: 2 }}>
                <FormControlLabel
                    control={<Switch checked={overlayBbox} onChange={e => setOverlayBbox(e.target.checked)} />}
                    label='Show Bounding Boxes'
                    sx={{ '& .MuiFormControlLabel-label': { fontWeight: 500 } }}
                />
            </Box>

            <Box sx={{ mb: 2 }}>
                <Typography variant='subtitle2' sx={{ mb: 1, fontWeight: 500, color: 'text.primary' }}>
                    Object Classes
                </Typography>
                <FormControl fullWidth>
                    <InputLabel sx={{ fontWeight: 500 }}>Class Type</InputLabel>
                    <Select multiple value={classType} onChange={e => setClassType(e.target.value as string[])} label='Class Type'>
                        {availableClassLabels?.map(label => (
                            <MenuItem key={label} value={label}>
                                {label}
                            </MenuItem>
                        )) || []}
                    </Select>
                </FormControl>
            </Box>

            <Box sx={{ mb: 2 }}>
                <TextField
                    fullWidth
                    label='Specific Object IDs'
                    value={objectIds}
                    onChange={e => setObjectIds(e.target.value)}
                    placeholder='1,2,3'
                    helperText='Comma-separated list of object IDs to display (leave empty to show all objects)'
                    sx={{ '& .MuiInputLabel-root': { fontWeight: 500 } }}
                />
            </Box>

            <Box
                sx={{
                    p: 1.5,
                    bgcolor: 'background.paper',
                    borderRadius: 2,
                    border: '1px solid rgba(0, 0, 0, 0.05)',
                }}
            >
                <Typography variant='subtitle2' sx={{ mb: 1.5, fontWeight: 500, color: 'text.primary' }}>
                    Object ID Display
                </Typography>

                <Box sx={{ mb: 1.5 }}>
                    <FormControlLabel
                        control={<Switch checked={showObjId} onChange={e => setShowObjId(e.target.checked)} />}
                        label='Show Object ID'
                        sx={{ '& .MuiFormControlLabel-label': { fontWeight: 500 } }}
                    />
                </Box>

                <Grid container spacing={2}>
                    <Grid item xs={12} sm={4}>
                        <FormControl fullWidth disabled={!showObjId}>
                            <InputLabel sx={{ fontWeight: 500 }}>Position</InputLabel>
                            <Select value={objIdPosition} onChange={e => setObjIdPosition(e.target.value as number)} label='Position'>
                                {positionOptions.map(opt => (
                                    <MenuItem key={opt.value} value={opt.value}>
                                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
                                            <Box
                                                sx={{
                                                    width: 20,
                                                    height: 16,
                                                    border: '1.5px solid #666',
                                                    borderRadius: '2px',
                                                    position: 'relative',
                                                    backgroundColor: 'transparent',
                                                }}
                                            >
                                                <Box
                                                    sx={{
                                                        position: 'absolute',
                                                        ...opt.sx,
                                                        width: 4,
                                                        height: 4,
                                                        backgroundColor: '#1976d2',
                                                        borderRadius: '50%',
                                                    }}
                                                />
                                            </Box>
                                            <span>{opt.label}</span>
                                        </Box>
                                    </MenuItem>
                                ))}
                            </Select>
                        </FormControl>
                    </Grid>
                    <Grid item xs={12} sm={4}>
                        <FormControl fullWidth disabled={!showObjId}>
                            <InputLabel sx={{ fontWeight: 500 }}>Text Color</InputLabel>
                            <Select value={objIdTextColor} onChange={e => setObjIdTextColor(e.target.value)} label='Text Color'>
                                {colorOptions.map(c => (
                                    <MenuItem key={c} value={c}>
                                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                                            <Box
                                                sx={{
                                                    width: 16,
                                                    height: 16,
                                                    backgroundColor: c,
                                                    border: '1px solid #ccc',
                                                    borderRadius: '2px',
                                                }}
                                            />
                                            <span>{c.charAt(0).toUpperCase() + c.slice(1)}</span>
                                        </Box>
                                    </MenuItem>
                                ))}
                            </Select>
                        </FormControl>
                    </Grid>
                    <Grid item xs={12} sm={4}>
                        <FormControl fullWidth disabled={!showObjId}>
                            <InputLabel sx={{ fontWeight: 500 }}>Background Color</InputLabel>
                            <Select value={objIdTextBGColor} onChange={e => setObjIdTextBGColor(e.target.value)} label='Background Color'>
                                {colorOptions.map(c => (
                                    <MenuItem key={c} value={c}>
                                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                                            <Box
                                                sx={{
                                                    width: 16,
                                                    height: 16,
                                                    backgroundColor: c,
                                                    border: '1px solid #ccc',
                                                    borderRadius: '2px',
                                                }}
                                            />
                                            <span>{c.charAt(0).toUpperCase() + c.slice(1)}</span>
                                        </Box>
                                    </MenuItem>
                                ))}
                            </Select>
                        </FormControl>
                    </Grid>
                </Grid>
            </Box>
        </Box>
    );
};

export default BboxSettingsSection;

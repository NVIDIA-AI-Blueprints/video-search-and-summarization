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
import { Box, Typography, TextField, Switch, Button } from '@mui/material';
import { RGBAColor, ColorMap, EnabledMap } from './types';

interface ColorConfigSectionProps {
    title: string;
    labels: string[];
    colors: ColorMap;
    enabledColors: EnabledMap;
    onColorChange: (label: string, color: RGBAColor) => void;
    onColorToggle: (label: string) => void;
    onReset: () => void;
}

const ColorConfigSection: React.FC<ColorConfigSectionProps> = ({
    title,
    labels,
    colors,
    enabledColors,
    onColorChange,
    onColorToggle,
    onReset,
}) => {
    if (!labels || labels.length === 0) return null;

    return (
        <Box
            sx={{
                bgcolor: 'rgba(0, 0, 0, 0.02)',
                borderRadius: 2,
                p: 3,
                mb: 3,
                border: '1px solid rgba(0, 0, 0, 0.08)',
            }}
        >
            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
                <Typography variant='h6' sx={{ fontWeight: 600, color: 'primary.main' }}>
                    {title}
                </Typography>
                <Button
                    size='small'
                    variant='outlined'
                    sx={{
                        borderColor: 'primary.main',
                        color: 'primary.main',
                        fontWeight: 500,
                        '&:hover': { bgcolor: 'primary.main', color: 'white' },
                    }}
                    onClick={onReset}
                >
                    Reset to Default
                </Button>
            </Box>
            <Box
                sx={{
                    maxHeight: '300px',
                    overflowY: 'auto',
                    border: '1px solid rgba(0, 0, 0, 0.08)',
                    borderRadius: 2,
                    p: 2,
                    bgcolor: 'background.paper',
                    '&::-webkit-scrollbar': { width: 8 },
                    '&::-webkit-scrollbar-track': { backgroundColor: 'rgba(0, 0, 0, 0.05)', borderRadius: 4 },
                    '&::-webkit-scrollbar-thumb': {
                        backgroundColor: 'rgba(0, 0, 0, 0.2)',
                        borderRadius: 4,
                        '&:hover': { backgroundColor: 'rgba(0, 0, 0, 0.3)' },
                    },
                }}
            >
                {labels.map(label => (
                    <Box
                        key={label}
                        sx={{
                            display: 'flex',
                            alignItems: 'center',
                            mb: 2,
                            flexWrap: 'wrap',
                            gap: 2,
                            pb: 2,
                            borderBottom: '1px solid rgba(0, 0, 0, 0.08)',
                            '&:last-child': { borderBottom: 'none', mb: 0, pb: 0 },
                        }}
                    >
                        <Typography sx={{ minWidth: 200 }}>
                            {label.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase())}:
                        </Typography>
                        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
                            {[0, 1, 2].map(idx => (
                                <TextField
                                    key={idx}
                                    type='number'
                                    label={['R', 'G', 'B'][idx]}
                                    size='small'
                                    value={colors[label]?.[idx] ?? ''}
                                    onChange={e => {
                                        const parsed = Number.parseInt(e.target.value, 10);
                                        const val = Number.isNaN(parsed) ? 0 : Math.min(255, Math.max(0, parsed));
                                        const currentColor = colors[label] || [0, 0, 0, 255];
                                        const newColor: RGBAColor = [...currentColor];
                                        newColor[idx] = val;
                                        onColorChange(label, newColor);
                                    }}
                                    inputProps={{ min: 0, max: 255 }}
                                    sx={{ width: 70 }}
                                />
                            ))}
                            <TextField
                                type='number'
                                label='A'
                                size='small'
                                value={colors[label]?.[3] ?? 255}
                                onChange={e => {
                                    const val = Math.min(255, Math.max(0, parseInt(e.target.value, 10) || 0));
                                    const currentColor = colors[label] || [0, 0, 0, 255];
                                    onColorChange(label, [currentColor[0], currentColor[1], currentColor[2], val]);
                                }}
                                inputProps={{ min: 0, max: 255 }}
                                sx={{ width: 70 }}
                            />
                            <TextField
                                type='color'
                                value={(colors[label] || [0, 0, 0, 255])
                                    .slice(0, 3)
                                    .reduce((hex, val) => hex + (val || 0).toString(16).padStart(2, '0'), '#')}
                                onChange={e => {
                                    const hex = e.target.value;
                                    const r = parseInt(hex.slice(1, 3), 16);
                                    const g = parseInt(hex.slice(3, 5), 16);
                                    const b = parseInt(hex.slice(5, 7), 16);
                                    onColorChange(label, [r, g, b, colors[label]?.[3] ?? 255]);
                                }}
                                sx={{ width: 56 }}
                            />
                            <Switch checked={enabledColors[label] !== false} onChange={() => onColorToggle(label)} size='small' />
                        </Box>
                    </Box>
                ))}
            </Box>
        </Box>
    );
};

export default ColorConfigSection;

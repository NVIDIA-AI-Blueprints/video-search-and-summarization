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
import React, { useState, useEffect, useCallback } from 'react';
import { Box, Typography } from '@mui/material';
import { Sparklines, SparklinesLine } from 'react-sparklines';

interface BitrateSparklineProps {
    bitrate: number;
}

const MAX_HISTORY_POINTS = 20;

const formatBitrate = (bps: number): string => {
    if (isNaN(bps)) return '0 bps';
    if (bps >= 1000000) {
        return `${(bps / 1000000).toFixed(1)} Mbps`;
    } else if (bps >= 1000) {
        return `${(bps / 1000).toFixed(1)} Kbps`;
    }
    return `${bps} bps`;
};

const BitrateSparkline: React.FC<BitrateSparklineProps> = ({ bitrate }) => {
    const [bitrateHistory, setBitrateHistory] = useState<number[]>([]);

    // Calculate min value for sparkline to show meaningful variations
    const getSparklineMin = useCallback((data: number[]) => {
        if (data.length === 0) return 0;
        const min = Math.min(...data);
        const max = Math.max(...data);
        const range = max - min;
        // If variation is less than 10% of the max, show more detail
        if (range < max * 0.1) {
            return min - max * 0.05; // Add 5% padding below min
        }
        return min - range * 0.1; // Add 10% padding below min
    }, []);

    // Update bitrate history when bitrate changes
    useEffect(() => {
        if (isNaN(bitrate)) {
            setBitrateHistory([]); // Reset history if we get NaN
            return;
        }
        if (bitrate > 0) {
            setBitrateHistory(prev => {
                const newHistory = [...prev, bitrate];
                if (newHistory.length > MAX_HISTORY_POINTS) {
                    return newHistory.slice(-MAX_HISTORY_POINTS);
                }
                return newHistory;
            });
        }
    }, [bitrate]);

    if (bitrate <= 0 || isNaN(bitrate)) return null;

    return (
        <Box
            sx={{
                display: {
                    sm: 'none',
                    xs: 'none',
                    md: 'flex',
                },
                alignItems: 'center',
                gap: 1,
                ml: 1,
                px: 1,
                py: 0.5,
                bgcolor: 'action.hover',
                borderRadius: 1,
                minWidth: '180px',
                height: '24px',
            }}
        >
            <Typography
                variant='body2'
                sx={{
                    color: 'text.secondary',
                    whiteSpace: 'nowrap',
                }}
            >
                {formatBitrate(bitrate)}
            </Typography>
            <Sparklines data={bitrateHistory} width={100} height={16} margin={0} min={getSparklineMin(bitrateHistory)}>
                <SparklinesLine
                    style={{
                        stroke: 'currentColor',
                        strokeWidth: 1,
                        fill: 'none',
                    }}
                />
            </Sparklines>
        </Box>
    );
};

export default BitrateSparkline;

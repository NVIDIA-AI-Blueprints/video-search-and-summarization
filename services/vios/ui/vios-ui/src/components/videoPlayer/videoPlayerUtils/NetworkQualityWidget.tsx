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
/**
 * NetworkQualityWidget Component
 *
 * Monitors and displays WebRTC connection quality metrics:
 *
 * Displayed Metrics (with graphs):
 * - FPS (Frames Per Second): Video frame rate
 * - PLI (Picture Loss Indication): Requests for full frame updates
 * - NACK (Negative Acknowledgment): Requests for packet retransmission
 *
 * Monitored Metrics (used for quality analysis):
 * - packetsLost: Number of lost packets
 * - jitter: Network jitter in milliseconds
 * - bitrate: Current network bandwidth in kbps
 * - fir (Full Intra Request): Requests for key frames
 * - rtt (Round Trip Time): Network latency in milliseconds
 *
 * Quality Messages:
 * Critical Issues (Red):
 * - "Severe Network Issues": High packet loss (>10) or jitter (>100ms)
 * - "Low Bandwidth": Bitrate below 500 kbps
 * - "Low Frame Rate": FPS below 15
 *
 * Warning Issues (Orange):
 * - "Network Congestion": High PLI/NACK/FIR with high jitter
 * - "Packet Loss": NACK > 2 or packetsLost > 5
 * - "Frame Drops": PLI > 1 or FIR > 1
 * - "High Latency": RTT > 200ms
 *
 * Good Connection (Green):
 * - All metrics within acceptable ranges
 */

import React, { useState, useEffect, useRef } from 'react';
import { Box, Typography, IconButton, useMediaQuery, useTheme } from '@mui/material';
import { Sparklines, SparklinesLine } from 'react-sparklines';
import { WebRTCStats } from '../../../interfaces/interfaces';
import CloseIcon from '@mui/icons-material/Close';
import useSettings from '../../../hooks/useSettings';
import { DEFAULT_NETWORK_QUALITY_SETTINGS } from '../../../theme/defaultSettings';
import { StreamState } from 'vst-streaming-lib';

const DEFAULT_SETTINGS = {
    networkQualityWidget: DEFAULT_NETWORK_QUALITY_SETTINGS,
};

interface NetworkQualityWidgetProps {
    stats: WebRTCStats;
    sensorName?: string;
    playbackStatus: StreamState;
}

const NetworkQualityWidget: React.FC<NetworkQualityWidgetProps> = ({ stats, sensorName = 'default', playbackStatus }) => {
    const theme = useTheme();
    const isSmallScreen = useMediaQuery(theme.breakpoints.down('sm'));
    const { settings } = useSettings();
    const [showOverlay, setShowOverlay] = useState<boolean>(false);
    const [userHidden, setUserHidden] = useState<boolean>(false);
    const [isInitialDelay, setIsInitialDelay] = useState<boolean>(true);
    const timeoutRef = useRef<NodeJS.Timeout>();
    const userHideTimeoutRef = useRef<NodeJS.Timeout>();
    const initialDelayRef = useRef<NodeJS.Timeout>();
    const fpsLogCountRef = useRef<number>(0);
    const consecutiveIssuesRef = useRef<number>(0);
    const [series, setSeries] = useState<{ name: string; data: number[] }[]>([
        { name: 'FPS', data: [] },
        { name: 'PLI', data: [] },
        { name: 'NACK', data: [] },
    ]);

    // Use default settings if not available
    const config = settings?.networkQualityWidget || DEFAULT_SETTINGS.networkQualityWidget;

    // Function to check if current stats indicate an issue
    const hasNetworkIssue = (stats: WebRTCStats): boolean => {
        const { thresholds } = config;
        return (
            stats.packetsLost > thresholds.severePacketLoss ||
            stats.jitter > thresholds.severeJitterMs ||
            stats.fps < thresholds.lowFps ||
            ((stats.pli > thresholds.highPli || stats.nack > thresholds.highNack || stats.fir > thresholds.highFir) &&
                stats.jitter > thresholds.highJitterMs) ||
            stats.nack > thresholds.moderateNack ||
            stats.packetsLost > thresholds.moderatePacketLoss ||
            stats.pli > thresholds.moderatePli ||
            stats.fir > thresholds.moderateFir ||
            stats.rtt > thresholds.highLatencyMs
        );
    };

    // Function to determine the network quality message
    const getNetworkQualityMessage = (stats: WebRTCStats): { message: string; color: string } => {
        const { thresholds } = config;

        // Check for quality limitations
        if (stats.qualityLimitationReason) {
            return {
                message: `${stats.qualityLimitationReason.charAt(0).toUpperCase() + stats.qualityLimitationReason.slice(1)} Limited`,
                color: '#FF4560', // Red
            };
        }

        // Check for severe network issues
        if (stats.packetsLost > thresholds.severePacketLoss || stats.jitter > thresholds.severeJitterMs) {
            return {
                message: 'Severe Network Issues',
                color: '#FF4560', // Red
            };
        }

        // Check for low FPS issues
        if (stats.fps < thresholds.lowFps) {
            return {
                message: 'Low Frame Rate',
                color: '#FF4560', // Red
            };
        }

        // Check for network congestion
        if (
            (stats.pli > thresholds.highPli || stats.nack > thresholds.highNack || stats.fir > thresholds.highFir) &&
            stats.jitter > thresholds.highJitterMs
        ) {
            return {
                message: 'Network Congestion',
                color: '#FEB019', // Orange
            };
        }

        // Check for packet loss
        if (stats.nack > thresholds.moderateNack || stats.packetsLost > thresholds.moderatePacketLoss) {
            return {
                message: 'Packet Loss',
                color: '#FEB019', // Orange
            };
        }

        // Check for frame drops
        if (stats.pli > thresholds.moderatePli || stats.fir > thresholds.moderateFir) {
            return {
                message: 'Frame Drops',
                color: '#FEB019', // Orange
            };
        }

        // Check for high latency
        if (stats.rtt > thresholds.highLatencyMs) {
            return {
                message: 'High Latency',
                color: '#FEB019', // Orange
            };
        }

        // If all stats are good
        return {
            message: 'Good Connection',
            color: '#00E396', // Green
        };
    };

    useEffect(() => {
        setSeries(prevSeries =>
            prevSeries.map(s => ({
                ...s,
                data: [...s.data.slice(-config.maxGraphPoints), s.name === 'FPS' ? stats.fps : s.name === 'PLI' ? stats.pli : stats.nack],
            }))
        );

        // Log FPS only for the first 10 times when FPS value is present
        if (fpsLogCountRef.current < 10 && stats.fps != 0 && stats.fps != null) {
            console.log(`${sensorName} fps:  ${stats.fps}`);
            fpsLogCountRef.current += 1;
        }

        // Only show overlay if initial delay has passed
        if (!isInitialDelay) {
            const hasIssue = hasNetworkIssue(stats);

            if (hasIssue) {
                consecutiveIssuesRef.current += 1;
            } else {
                consecutiveIssuesRef.current = 0;
            }

            // Only show widget after threshold of consecutive issues
            if (consecutiveIssuesRef.current >= config.consecutiveIssuesThreshold && !userHidden) {
                setShowOverlay(true);
                if (timeoutRef.current) {
                    clearTimeout(timeoutRef.current);
                }
                timeoutRef.current = setTimeout(() => {
                    setShowOverlay(false);
                    consecutiveIssuesRef.current = 0;
                }, config.widgetDisplayDurationMs);
            }
        }
    }, [stats, userHidden, isInitialDelay, sensorName, config]);

    // Separate useEffect for cleanup
    useEffect(() => {
        return () => {
            if (timeoutRef.current) {
                clearTimeout(timeoutRef.current);
            }
            if (userHideTimeoutRef.current) {
                clearTimeout(userHideTimeoutRef.current);
            }
            if (initialDelayRef.current) {
                clearTimeout(initialDelayRef.current);
            }
        };
    }, []);

    // Separate useEffect for initial delay
    useEffect(() => {
        initialDelayRef.current = setTimeout(() => {
            setIsInitialDelay(false);
        }, config.initialDelayMs);

        return () => {
            if (initialDelayRef.current) {
                clearTimeout(initialDelayRef.current);
            }
        };
    }, [config.initialDelayMs]);

    // Don't show widget if stream is paused or not playing
    if (playbackStatus === StreamState.PAUSED || playbackStatus === StreamState.NOT_PLAYING) {
        return null;
    }

    if (isSmallScreen || !showOverlay || userHidden) {
        return null;
    }

    const handleHide = () => {
        setUserHidden(true);
        setShowOverlay(false);
        if (userHideTimeoutRef.current) {
            clearTimeout(userHideTimeoutRef.current);
        }
        userHideTimeoutRef.current = setTimeout(() => {
            setUserHidden(false);
        }, config.userHideDurationMs);
    };

    const { message, color } = getNetworkQualityMessage(stats);

    return (
        <Box
            sx={{
                position: 'absolute',
                bottom: 10,
                right: 10,
                backgroundColor: 'rgba(0, 0, 0, 0.7)',
                padding: 0.5,
                width: 150,
                boxShadow: '0 4px 6px rgba(0, 0, 0, 0.1)',
                borderRadius: 1,
                zIndex: 1001,
            }}
        >
            <Box
                sx={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    mb: 1,
                }}
            >
                <Typography
                    variant='caption'
                    sx={{
                        color: color,
                        display: 'flex',
                        alignItems: 'center',
                    }}
                >
                    {message}
                </Typography>
                <IconButton size='small' onClick={handleHide} sx={{ color: 'white' }}>
                    <CloseIcon fontSize='small' />
                </IconButton>
            </Box>
            {series.map(s => (
                <Box key={s.name} sx={{ mb: 0.5 }}>
                    <Typography variant='caption' sx={{ color: 'white', mb: 0.5, display: 'block' }}>
                        {s.name}: {s.data[s.data.length - 1]}
                    </Typography>
                    <Sparklines data={s.data} width={150} height={20} margin={0.5}>
                        <SparklinesLine color={s.name === 'FPS' ? '#00E396' : s.name === 'PLI' ? '#FEB019' : '#FF4560'} />
                    </Sparklines>
                </Box>
            ))}
        </Box>
    );
};

export default NetworkQualityWidget;

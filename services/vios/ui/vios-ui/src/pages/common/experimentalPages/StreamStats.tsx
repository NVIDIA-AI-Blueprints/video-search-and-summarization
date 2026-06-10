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
import { StreamType } from 'vst-streaming-lib';
import { Select, FormControl, InputLabel, MenuItem, Box, Paper, Typography, Grid2 as Grid, Button, useTheme } from '@mui/material';
import React, { useState, useCallback, useRef, useEffect } from 'react';
import SingleSensorSelector from '../../../components/sensorSelector/SingleSensorSelector';
import VSTStreamManager from '../../../features/streamManager/StreamManager';
import { Sensor } from '../../../interfaces/interfaces';
import useVSTUIStore from '../../../services/StateManagement';
import { logInfo } from '../../../utils/misc/Logs';
import ReactApexChart from 'react-apexcharts';
import { ApexOptions } from 'apexcharts';

const StreamStats: React.FC = () => {
    const theme = useTheme();
    const [streamType, setStreamType] = useState<StreamType>(StreamType.Live);
    const [selectedSensor, setSelectedSensor] = useState<Sensor | null>(null);
    const sensors = useVSTUIStore(state => state.sensorServiceSensors);
    const [webRTCStats, setWebRTCStats] = useState<RTCStatsReport>();
    const [isPaused, setIsPaused] = useState(false);

    // Add ref for pause state
    const isPausedRef = useRef(false);

    // Update ref when state changes
    useEffect(() => {
        isPausedRef.current = isPaused;
    }, [isPaused]);

    // Add refs for bitrate calculation
    const prevBytesReceived = React.useRef<number>(0);
    const prevTimestamp = React.useRef<number>(0);

    // Add state for time-series data
    const [bitrateData, setBitrateData] = useState<[number, number][]>([]);
    const [fpsData, setFpsData] = useState<[number, number][]>([]);
    const [packetsLostData, setPacketsLostData] = useState<[number, number][]>([]);

    // Add new state for RTCP feedback counters
    const [rtcpFeedbackData, setRtcpFeedbackData] = useState<{
        pli: [number, number][];
        nack: [number, number][];
        fir: [number, number][];
    }>({
        pli: [],
        nack: [],
        fir: [],
    });

    const calculateBitrate = useCallback((stats: RTCStatsReport) => {
        stats.forEach(stat => {
            if (stat.type === 'inbound-rtp' && stat.kind === 'video') {
                const bytesReceived = (stat.bytesReceived as number) / 1000;
                const timestamp = stat.timestamp;

                if (prevTimestamp.current && prevBytesReceived.current) {
                    const bitrate = Number(
                        ((8 * (bytesReceived - prevBytesReceived.current)) / ((timestamp - prevTimestamp.current) / 1000)).toFixed(2)
                    );
                    setBitrateData(prev => [...prev, [timestamp, bitrate] as [number, number]].slice(-50));
                    setFpsData(prev => [...prev, [timestamp, stat.framesPerSecond || 0] as [number, number]].slice(-50));
                    setPacketsLostData(prev => [...prev, [timestamp, stat.packetsLost || 0] as [number, number]].slice(-50));

                    // Update RTCP feedback counters
                    setRtcpFeedbackData(prev => ({
                        pli: [...prev.pli, [timestamp, stat.pliCount || 0] as [number, number]].slice(-50),
                        nack: [...prev.nack, [timestamp, stat.nackCount || 0] as [number, number]].slice(-50),
                        fir: [...prev.fir, [timestamp, stat.firCount || 0] as [number, number]].slice(-50),
                    }));
                }

                prevBytesReceived.current = bytesReceived;
                prevTimestamp.current = timestamp;
            }
        });
    }, []);

    const handleSensorSelection = useCallback((selection: Sensor | null) => {
        setSelectedSensor(selection);
        // Clear all stats when sensor changes
        setWebRTCStats(undefined);
        setBitrateData([]);
        setFpsData([]);
        setPacketsLostData([]);
        setRtcpFeedbackData({ pli: [], nack: [], fir: [] });
        prevBytesReceived.current = 0;
        prevTimestamp.current = 0;
        // Reset pause state
        setIsPaused(false);
        isPausedRef.current = false;
    }, []);

    const handleWebRTCStatsUpdate = useCallback(
        (stats: RTCStatsReport) => {
            if (isPausedRef.current) return; // Use ref instead of state
            logInfo(`WebRTC Stats received`);
            setWebRTCStats(stats);
            calculateBitrate(stats);
        },
        [calculateBitrate] // Remove isPaused from dependencies
    );

    // Chart options
    const lineChartOptions: ApexOptions = {
        chart: {
            type: 'line',
            animations: {
                enabled: true,
                dynamicAnimation: {
                    speed: 1000,
                },
            },
            toolbar: {
                show: false,
            },
            zoom: {
                enabled: false,
            },
            foreColor: theme.palette.text.secondary,
            background: 'transparent',
        },
        stroke: {
            curve: 'smooth',
            width: 2,
        },
        xaxis: {
            type: 'datetime',
            labels: {
                datetimeUTC: false,
                style: {
                    colors: theme.palette.text.secondary,
                },
            },
            axisBorder: {
                show: true,
                color: theme.palette.divider,
            },
            axisTicks: {
                show: true,
                color: theme.palette.divider,
            },
        },
        legend: {
            position: 'top',
            labels: {
                colors: theme.palette.text.secondary,
            },
        },
        tooltip: {
            theme: theme.palette.mode,
            x: {
                format: 'HH:mm:ss',
            },
            shared: true,
            fixed: {
                enabled: true,
                position: 'topLeft',
                offsetX: 60,
                offsetY: 30,
            },
        },
        yaxis: {
            labels: {
                style: {
                    colors: theme.palette.text.secondary,
                },
            },
            axisBorder: {
                show: true,
                color: theme.palette.divider,
            },
            axisTicks: {
                show: true,
                color: theme.palette.divider,
            },
        },
        grid: {
            borderColor: theme.palette.divider,
            strokeDashArray: 4,
        },
        title: {
            style: {
                color: theme.palette.text.primary,
            },
        },
    };

    return (
        <Grid container spacing={2}>
            <Grid size={{ xs: 12 }}>
                <FormControl fullWidth variant='outlined'>
                    <InputLabel>Stream Type</InputLabel>
                    <Select value={streamType} label='Stream Type' onChange={e => setStreamType(e.target.value as StreamType)}>
                        <MenuItem value={StreamType.Live}>Live</MenuItem>
                        <MenuItem value={StreamType.Replay}>Replay</MenuItem>
                    </Select>
                </FormControl>
            </Grid>
            <Grid size={{ xs: 12 }}>
                <SingleSensorSelector sensors={sensors} onChange={handleSensorSelection} selectedSensors={selectedSensor} />
            </Grid>
            <Grid size={{ xs: 12 }}>
                <Box className='video-container'>
                    {selectedSensor && (
                        <VSTStreamManager
                            key={`${selectedSensor.sensorId}-${streamType}-debug`}
                            sensor={selectedSensor}
                            streamType={streamType}
                            onWebRTCStatsUpdate={handleWebRTCStatsUpdate}
                        />
                    )}
                </Box>
            </Grid>
            {webRTCStats && (
                <Grid size={{ xs: 12 }}>
                    <Paper
                        sx={{
                            p: 2,
                            mt: 2,
                            backgroundColor: theme.palette.background.paper,
                            transition: 'all 0.3s ease-in-out',
                            '&:hover': {
                                boxShadow: theme.shadows[4],
                            },
                        }}
                    >
                        <Box
                            sx={{
                                display: 'flex',
                                justifyContent: 'space-between',
                                alignItems: 'center',
                                mb: 2,
                            }}
                        >
                            <Typography variant='h6' sx={{ color: theme.palette.text.primary }}>
                                WebRTC Statistics
                            </Typography>
                            <Button
                                variant='contained'
                                onClick={() => setIsPaused(!isPaused)}
                                color={isPaused ? 'success' : 'error'}
                                sx={{
                                    textTransform: 'none',
                                    transition: 'all 0.3s ease-in-out',
                                    '&:hover': {
                                        transform: 'translateY(-1px)',
                                    },
                                }}
                            >
                                {isPaused ? 'Resume' : 'Pause'}
                            </Button>
                        </Box>
                        <Grid container spacing={2}>
                            <Grid size={{ xs: 6 }}>
                                <Paper
                                    sx={{
                                        p: 2,
                                        backgroundColor: theme.palette.background.default,
                                        transition: 'all 0.3s ease-in-out',
                                        '&:hover': {
                                            boxShadow: theme.shadows[4],
                                        },
                                    }}
                                >
                                    <ReactApexChart
                                        options={{
                                            ...lineChartOptions,
                                            title: { text: 'Bitrate (Kbps)' },
                                            yaxis: { min: 0 },
                                        }}
                                        series={[
                                            {
                                                name: 'Bitrate',
                                                data: bitrateData,
                                                color: theme.palette.primary.main,
                                            },
                                        ]}
                                        type='line'
                                        height={300}
                                    />
                                </Paper>
                            </Grid>
                            <Grid size={{ xs: 6 }}>
                                <Paper
                                    sx={{
                                        p: 2,
                                        backgroundColor: theme.palette.background.default,
                                        transition: 'all 0.3s ease-in-out',
                                        '&:hover': {
                                            boxShadow: theme.shadows[4],
                                        },
                                    }}
                                >
                                    <ReactApexChart
                                        options={{
                                            ...lineChartOptions,
                                            title: { text: 'Frame Rate (FPS)' },
                                            yaxis: { min: 0 },
                                        }}
                                        series={[
                                            {
                                                name: 'FPS',
                                                data: fpsData,
                                                color: theme.palette.success.main,
                                            },
                                        ]}
                                        type='line'
                                        height={300}
                                    />
                                </Paper>
                            </Grid>
                            <Grid size={{ xs: 6 }}>
                                <Paper
                                    sx={{
                                        p: 2,
                                        backgroundColor: theme.palette.background.default,
                                        transition: 'all 0.3s ease-in-out',
                                        '&:hover': {
                                            boxShadow: theme.shadows[4],
                                        },
                                    }}
                                >
                                    <ReactApexChart
                                        options={{
                                            ...lineChartOptions,
                                            title: { text: 'Packets Lost' },
                                            yaxis: { min: 0 },
                                        }}
                                        series={[
                                            {
                                                name: 'Packets Lost',
                                                data: packetsLostData,
                                                color: theme.palette.error.main,
                                            },
                                        ]}
                                        type='line'
                                        height={300}
                                    />
                                </Paper>
                            </Grid>
                            <Grid size={{ xs: 6 }}>
                                <Paper
                                    sx={{
                                        p: 2,
                                        backgroundColor: theme.palette.background.default,
                                        transition: 'all 0.3s ease-in-out',
                                        '&:hover': {
                                            boxShadow: theme.shadows[4],
                                        },
                                    }}
                                >
                                    <ReactApexChart
                                        options={{
                                            ...lineChartOptions,
                                            title: {
                                                text: 'RTCP Feedback Counters',
                                            },
                                            yaxis: { min: 0 },
                                        }}
                                        series={[
                                            {
                                                name: 'PLI',
                                                data: rtcpFeedbackData.pli,
                                                color: theme.palette.warning.main,
                                            },
                                            {
                                                name: 'NACK',
                                                data: rtcpFeedbackData.nack,
                                                color: theme.palette.info.main,
                                            },
                                            {
                                                name: 'FIR',
                                                data: rtcpFeedbackData.fir,
                                                color: theme.palette.secondary.main,
                                            },
                                        ]}
                                        type='line'
                                        height={300}
                                    />
                                </Paper>
                            </Grid>
                        </Grid>
                    </Paper>
                </Grid>
            )}
        </Grid>
    );
};

export default StreamStats;

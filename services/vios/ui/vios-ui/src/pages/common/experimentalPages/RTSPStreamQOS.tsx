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
import useVSTUIStore from '../../../services/StateManagement';
import SensorSelector from '../../../components/sensorSelector/SingleSensorSelector';
import React, { useCallback, useState, useEffect, useMemo, useRef } from 'react';
import { Grid2 as Grid, Box, Button, Paper, Typography } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { Sensor } from '../../../interfaces/interfaces';
import ReactApexChart from 'react-apexcharts';
import { ApexOptions } from 'apexcharts';
import nvAxios from '../../../services/Axios';
import config from '../../../config';

const MAX_QOS_POINTS = 50;

// Define interfaces for the QoS data
interface QoSStats {
    avgFps: number;
    bitrateKbpsAvg: number;
    interPacketGapMsAvg: number;
    name: string;
    timestamp: string;
    avgFramecount: number;
    packetLossPercentageAvg: number;
    packetLossPercentageMax: number;
    // ... other fields as needed
}

interface QoSData {
    numActiveRtspConnections: string;
    rtspServerTxBitrate: string;
    stats: QoSStats[];
}

const RTSPStreamQOS = () => {
    const theme = useTheme();
    const isDarkMode = theme.palette.mode === 'dark';

    const sensors = useVSTUIStore(state => state.sensorServiceSensors);
    const [selectedSensor, setSelectedSensor] = useState<Sensor | null>(null);
    const handleSensorSelection = useCallback((selection: Sensor | null) => {
        setSelectedSensor(selection);
    }, []);

    const [qosData, setQosData] = useState<QoSStats[]>([]);
    const [isPaused, setIsPaused] = useState(false);
    const latestTimestampRef = useRef<string | null>(null);

    useEffect(() => {
        setQosData([]);
        latestTimestampRef.current = null;
    }, [selectedSensor?.sensorId]);

    // Fetch QoS data every second
    useEffect(() => {
        if (!selectedSensor || isPaused) return;

        const fetchQoSData = async () => {
            try {
                const response = await nvAxios.get(`${config.sensorManagementEndpoint}/api/v1/proxy/debug/qos`, {
                    headers: { streamId: selectedSensor.streamId || selectedSensor.sensorId },
                });
                const data: QoSData = response.data;
                const sensorStats = data.stats.find(stat => stat.name === selectedSensor.name);
                if (sensorStats) {
                    if (latestTimestampRef.current === sensorStats.timestamp) {
                        return;
                    }

                    latestTimestampRef.current = sensorStats.timestamp;
                    setQosData(prev => {
                        const nextEntry: QoSStats = {
                            ...sensorStats,
                            avgFps: Number((sensorStats.avgFps ?? 0).toFixed(2)),
                            avgFramecount: Number((sensorStats.avgFramecount ?? 0).toFixed(2)),
                            packetLossPercentageAvg: Number((sensorStats.packetLossPercentageAvg ?? 0).toFixed(2)),
                            packetLossPercentageMax: Number((sensorStats.packetLossPercentageMax ?? 0).toFixed(2)),
                            bitrateKbpsAvg: Number((sensorStats.bitrateKbpsAvg ?? 0).toFixed(2)),
                        };

                        if (prev.length >= MAX_QOS_POINTS) {
                            return [...prev.slice(1), nextEntry];
                        }

                        return [...prev, nextEntry];
                    });
                }
            } catch (error) {
                console.error('Error fetching QoS data:', error);
            }
        };

        fetchQoSData();
        const interval = setInterval(fetchQoSData, 1000);
        return () => clearInterval(interval);
    }, [selectedSensor, isPaused]);

    // Common chart options
    const commonChartOptions: ApexOptions = useMemo(
        () => ({
            chart: {
                type: 'line' as const,
                background: 'transparent',
                animations: {
                    enabled: false,
                },
                toolbar: {
                    show: false,
                },
                zoom: {
                    enabled: false,
                },
                foreColor: theme.palette.text.secondary,
            },
            theme: {
                mode: isDarkMode ? 'dark' : 'light',
            },
            stroke: {
                curve: 'smooth' as const,
                width: 2,
            },
            grid: {
                borderColor: theme.palette.divider,
                strokeDashArray: 4,
            },
            xaxis: {
                type: 'datetime' as const,
                axisBorder: {
                    color: theme.palette.divider,
                },
                axisTicks: {
                    color: theme.palette.divider,
                },
                labels: {
                    datetimeUTC: false,
                    formatter: function (value: string) {
                        return new Date(Number(value)).toLocaleTimeString();
                    },
                    style: {
                        colors: theme.palette.text.secondary,
                    },
                },
            },
            legend: {
                position: 'top' as const,
                labels: {
                    colors: theme.palette.text.primary,
                },
            },
            tooltip: {
                theme: isDarkMode ? 'dark' : 'light',
                x: {
                    format: 'HH:mm:ss',
                },
                shared: true,
            },
            yaxis: {
                title: {
                    style: {
                        color: theme.palette.text.secondary,
                    },
                },
                labels: {
                    style: {
                        colors: theme.palette.text.secondary,
                    },
                },
            },
            markers: {
                size: 2,
                strokeWidth: 0,
            },
            title: {
                style: {
                    color: theme.palette.text.primary,
                },
            },
        }),
        [theme, isDarkMode]
    );

    // FPS Chart Options
    const fpsChartOptions: ApexOptions = useMemo(
        () => ({
            ...commonChartOptions,
            colors: [theme.palette.primary.main, theme.palette.secondary.main],
            title: {
                text: 'FPS Metrics',
                align: 'left',
            },
            yaxis: {
                title: {
                    text: 'Frames per Second',
                },
                min: 0,
            },
        }),
        [commonChartOptions, theme]
    );

    // Error Bits Chart Options
    const errorChartOptions: ApexOptions = useMemo(
        () => ({
            ...commonChartOptions,
            colors: [theme.palette.warning.main, theme.palette.error.main],
            title: {
                text: 'Error Statistics',
                align: 'left',
            },
            yaxis: {
                title: {
                    text: 'Packet Loss (%)',
                },
                min: 0,
            },
        }),
        [commonChartOptions, theme]
    );

    // Bitrate Chart Options
    const bitrateChartOptions: ApexOptions = useMemo(
        () => ({
            ...commonChartOptions,
            colors: [theme.palette.info.main],
            title: {
                text: 'Bitrate Statistics',
                align: 'left',
            },
            yaxis: {
                title: {
                    text: 'Bitrate (Kbps)',
                },
                min: 0,
            },
        }),
        [commonChartOptions, theme]
    );

    // Prepare series data for FPS chart
    const fpsSeries = useMemo(
        () => [
            {
                name: 'Average FPS',
                data: qosData.map(d => ({
                    x: new Date(d.timestamp).getTime(),
                    y: d.avgFps,
                })),
            },
            {
                name: 'Average Frame Count',
                data: qosData.map(d => ({
                    x: new Date(d.timestamp).getTime(),
                    y: d.avgFramecount,
                })),
            },
        ],
        [qosData]
    );

    // Prepare series data for error chart
    const errorSeries = useMemo(
        () => [
            {
                name: 'Packet Loss % (Avg)',
                data: qosData.map(d => ({
                    x: new Date(d.timestamp).getTime(),
                    y: d.packetLossPercentageAvg,
                })),
            },
            {
                name: 'Packet Loss % (Max)',
                data: qosData.map(d => ({
                    x: new Date(d.timestamp).getTime(),
                    y: d.packetLossPercentageMax,
                })),
            },
        ],
        [qosData]
    );

    // Prepare series data for bitrate chart
    const bitrateSeries = useMemo(
        () => [
            {
                name: 'Average Bitrate',
                data: qosData.map(d => ({
                    x: new Date(d.timestamp).getTime(),
                    y: d.bitrateKbpsAvg,
                })),
            },
        ],
        [qosData]
    );

    return (
        <Grid container spacing={2}>
            <Grid size={{ xs: 12 }}>
                <Typography variant='h4' sx={{ color: 'text.primary' }}>
                    RTSP QOS Stats
                </Typography>
            </Grid>
            <Grid size={{ xs: 12 }}>
                <SensorSelector
                    multiple
                    sensors={sensors}
                    onChange={selection => {
                        handleSensorSelection(selection);
                    }}
                    selectedSensors={selectedSensor}
                />
            </Grid>
            {selectedSensor && (
                <>
                    <Grid size={{ xs: 12 }} sx={{ mb: 2 }}>
                        <Button variant='contained' color={isPaused ? 'success' : 'error'} onClick={() => setIsPaused(!isPaused)}>
                            {isPaused ? 'Resume Graphs' : 'Pause Graphs'}
                        </Button>
                    </Grid>
                    <Grid size={{ xs: 12 }}>
                        <Paper
                            sx={{
                                p: 2,
                                bgcolor: 'background.paper',
                                border: 1,
                                borderColor: 'divider',
                            }}
                        >
                            <Box sx={{ bgcolor: 'background.paper', borderRadius: 1 }}>
                                <ReactApexChart options={fpsChartOptions} series={fpsSeries} type='line' height={350} />
                            </Box>
                        </Paper>
                    </Grid>
                    <Grid size={{ xs: 12 }}>
                        <Paper
                            sx={{
                                p: 2,
                                bgcolor: 'background.paper',
                                border: 1,
                                borderColor: 'divider',
                            }}
                        >
                            <Box sx={{ bgcolor: 'background.paper', borderRadius: 1 }}>
                                <ReactApexChart options={errorChartOptions} series={errorSeries} type='line' height={350} />
                            </Box>
                        </Paper>
                    </Grid>
                    <Grid size={{ xs: 12 }}>
                        <Paper
                            sx={{
                                p: 2,
                                bgcolor: 'background.paper',
                                border: 1,
                                borderColor: 'divider',
                            }}
                        >
                            <Box sx={{ bgcolor: 'background.paper', borderRadius: 1 }}>
                                <ReactApexChart options={bitrateChartOptions} series={bitrateSeries} type='line' height={350} />
                            </Box>
                        </Paper>
                    </Grid>
                </>
            )}
        </Grid>
    );
};

export default RTSPStreamQOS;

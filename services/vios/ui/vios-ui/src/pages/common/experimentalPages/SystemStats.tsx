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
import React, { useState, useEffect } from 'react';
import ReactApexChart from 'react-apexcharts';
import { Button, Grid2 as Grid, Paper, Typography, useTheme } from '@mui/material';
import config from '../../../config';
import nvAxios from '../../../services/Axios';

interface SystemStatsData {
    cpu_usage: number;
    dec_usage: number;
    enc_usage: number;
    gpu_index: string;
    gpu_name: string;
    gpu_usage: number;
    open_files_count: number;
    rss_MB: number;
    system_memory_usage_MB: number;
    total_gpu_mem_MB: number;
    total_gpu_mem_usage_MB: number;
    vst_gpu_mem_usage_MB: number;
    timestamp: number;
}

const SystemStats: React.FC = () => {
    const theme = useTheme();
    const [isPaused, setIsPaused] = useState(true);
    const [systemStats, setSystemStats] = useState<SystemStatsData[]>([]);
    const maxDataPoints = 50;

    useEffect(() => {
        const fetchData = async () => {
            if (isPaused) return;

            try {
                const response = await nvAxios.get(`${config.sensorManagementEndpoint}/api/v1/sensor/debug/system/stats`);
                const data: SystemStatsData = response.data;

                setSystemStats(prev => {
                    const newStats = [...prev, { ...data, timestamp: new Date().getTime() }];
                    return newStats.slice(-maxDataPoints);
                });
            } catch (error) {
                console.error('Error fetching system stats:', error);
            }
        };

        const interval = setInterval(fetchData, 5000);
        return () => clearInterval(interval);
    }, [isPaused]);

    // Common chart options
    const commonChartOptions = {
        chart: {
            type: 'line' as const,
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
            curve: 'smooth' as const,
            width: 2,
        },
        xaxis: {
            type: 'datetime' as const,
            labels: {
                datetimeUTC: false,
                formatter: function (value: string) {
                    return new Date(Number(value)).toLocaleTimeString();
                },
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
        legend: {
            position: 'top' as const,
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
        },
        grid: {
            borderColor: theme.palette.divider,
            strokeDashArray: 4,
        },
    };

    // Create series groups for related metrics
    const cpuGpuSeries = [
        {
            name: 'CPU Usage',
            data: systemStats.map(stat => ({
                x: stat.timestamp,
                y: stat.cpu_usage ? Number(stat.cpu_usage.toFixed(2)) : 0,
            })),
            color: theme.palette.primary.main,
        },
        {
            name: 'GPU Usage',
            data: systemStats.map(stat => ({
                x: stat.timestamp,
                y: stat.gpu_usage ? Number(stat.gpu_usage.toFixed(2)) : 0,
            })),
            color: theme.palette.success.main,
        },
    ];

    const encoderDecoderSeries = [
        {
            name: 'Encoder Usage',
            data: systemStats.map(stat => ({
                x: stat.timestamp,
                y: stat.enc_usage ? Number(stat.enc_usage.toFixed(2)) : 0,
            })),
            color: theme.palette.warning.main,
        },
        {
            name: 'Decoder Usage',
            data: systemStats.map(stat => ({
                x: stat.timestamp,
                y: stat.dec_usage ? Number(stat.dec_usage.toFixed(2)) : 0,
            })),
            color: theme.palette.error.main,
        },
    ];

    const memorySeriesSystem = [
        {
            name: 'System Memory',
            data: systemStats.map(stat => ({
                x: stat.timestamp,
                y: stat.system_memory_usage_MB ? Number(stat.system_memory_usage_MB.toFixed(2)) : 0,
            })),
            color: theme.palette.info.main,
        },
        {
            name: 'RSS Memory',
            data: systemStats.map(stat => ({
                x: stat.timestamp,
                y: stat.rss_MB ? Number(stat.rss_MB.toFixed(2)) : 0,
            })),
            color: theme.palette.secondary.main,
        },
    ];

    const memorySeriesGPU = [
        {
            name: 'GPU Memory',
            data: systemStats.map(stat => ({
                x: stat.timestamp,
                y: stat.total_gpu_mem_usage_MB ? Number(stat.total_gpu_mem_usage_MB.toFixed(2)) : 0,
            })),
            color: theme.palette.success.light,
        },
        {
            name: 'VST GPU Memory',
            data: systemStats.map(stat => ({
                x: stat.timestamp,
                y: stat.vst_gpu_mem_usage_MB ? Number(stat.vst_gpu_mem_usage_MB.toFixed(2)) : 0,
            })),
            color: theme.palette.success.dark,
        },
    ];

    const chartConfigs = [
        {
            title: 'CPU & GPU Usage',
            series: cpuGpuSeries,
            yaxisTitle: 'Usage (%)',
            max: 100,
        },
        {
            title: 'Encoder & Decoder Usage',
            series: encoderDecoderSeries,
            yaxisTitle: 'Usage (%)',
            max: 100,
        },
        {
            title: 'System Memory Usage',
            series: memorySeriesSystem,
            yaxisTitle: 'Memory (MB)',
        },
        {
            title: 'GPU Memory Usage',
            series: memorySeriesGPU,
            yaxisTitle: 'Memory (MB)',
        },
    ];

    // Add this new function to get the latest stats
    const getLatestStats = () => {
        return systemStats.length > 0 ? systemStats[systemStats.length - 1] : null;
    };

    return (
        <Paper
            sx={{
                p: { xs: 2 },
                backgroundColor: theme.palette.background.paper,
                transition: 'all 0.3s ease-in-out',
                '&:hover': {
                    boxShadow: theme.shadows[4],
                },
            }}
        >
            <Grid container spacing={2}>
                {/* Header section */}
                <Grid size={{ xs: 12 }} display='flex' alignItems='center' gap={2}>
                    <Typography
                        variant='h5'
                        sx={{
                            fontWeight: 'medium',
                            color: theme.palette.text.primary,
                        }}
                    >
                        System Statistics
                    </Typography>
                    <Button
                        onClick={() => setIsPaused(!isPaused)}
                        variant='contained'
                        color={isPaused ? 'success' : 'error'}
                        sx={{
                            textTransform: 'none',
                            transition: 'all 0.3s ease-in-out',
                            '&:hover': {
                                transform: 'translateY(-1px)',
                            },
                        }}
                    >
                        {isPaused ? 'Resume Graphs' : 'Pause Graphs'}
                    </Button>
                </Grid>

                {/* Add new stats widgets */}
                {getLatestStats() && (
                    <Grid size={{ xs: 12 }}>
                        <Paper
                            elevation={3}
                            sx={{
                                p: 2,
                                mb: 2,
                                backgroundColor: theme.palette.background.default,
                                transition: 'all 0.3s ease-in-out',
                                '&:hover': {
                                    boxShadow: theme.shadows[4],
                                },
                            }}
                        >
                            <Grid container spacing={3}>
                                {/* GPU Info */}
                                <Grid size={{ xs: 12, md: 3 }}>
                                    <Typography
                                        variant='h6'
                                        gutterBottom
                                        sx={{
                                            color: theme.palette.text.primary,
                                        }}
                                    >
                                        GPU Device
                                    </Typography>
                                    <Typography
                                        variant='body1'
                                        sx={{
                                            color: theme.palette.text.primary,
                                        }}
                                    >
                                        {getLatestStats()?.gpu_name}
                                    </Typography>
                                    <Typography
                                        variant='body2'
                                        sx={{
                                            color: theme.palette.text.secondary,
                                        }}
                                    >
                                        Index: {getLatestStats()?.gpu_index}
                                    </Typography>
                                </Grid>

                                {/* Usage Gauges */}
                                {[
                                    {
                                        label: 'CPU Usage',
                                        value: getLatestStats()?.cpu_usage || 0,
                                        color: theme.palette.primary.main,
                                    },
                                    {
                                        label: 'GPU Usage',
                                        value: getLatestStats()?.gpu_usage || 0,
                                        color: theme.palette.success.main,
                                    },
                                    {
                                        label: 'Encoder',
                                        value: getLatestStats()?.enc_usage || 0,
                                        color: theme.palette.warning.main,
                                    },
                                    {
                                        label: 'Decoder',
                                        value: getLatestStats()?.dec_usage || 0,
                                        color: theme.palette.error.main,
                                    },
                                ].map((gauge, index) => (
                                    <Grid key={index} size={{ xs: 6, md: 2 }}>
                                        <Paper
                                            sx={{
                                                p: 1,
                                                textAlign: 'center',
                                                backgroundColor: theme.palette.background.paper,
                                                transition: 'all 0.3s ease-in-out',
                                                '&:hover': {
                                                    boxShadow: theme.shadows[4],
                                                },
                                            }}
                                        >
                                            <Typography
                                                variant='subtitle2'
                                                sx={{
                                                    color: theme.palette.text.primary,
                                                }}
                                            >
                                                {gauge.label}
                                            </Typography>
                                            <div
                                                style={{
                                                    position: 'relative',
                                                    padding: '10px',
                                                }}
                                            >
                                                <ReactApexChart
                                                    options={{
                                                        chart: {
                                                            type: 'radialBar',
                                                            background: 'transparent',
                                                        },
                                                        plotOptions: {
                                                            radialBar: {
                                                                hollow: {
                                                                    size: '50%',
                                                                },
                                                                dataLabels: {
                                                                    show: true,
                                                                    name: {
                                                                        show: false,
                                                                    },
                                                                    value: {
                                                                        fontSize: '16px',
                                                                        formatter: val => `${Math.round(val)}%`,
                                                                        color: theme.palette.text.primary,
                                                                    },
                                                                },
                                                            },
                                                        },
                                                        colors: [gauge.color],
                                                    }}
                                                    series={[gauge.value]}
                                                    type='radialBar'
                                                    height={120}
                                                />
                                            </div>
                                        </Paper>
                                    </Grid>
                                ))}
                            </Grid>
                        </Paper>
                    </Grid>
                )}

                {/* Existing charts */}
                {chartConfigs.map((config, index) => (
                    <Grid key={index} size={{ xs: 12 }}>
                        <Paper
                            sx={{
                                p: 2,
                                backgroundColor: theme.palette.background.paper,
                                transition: 'all 0.3s ease-in-out',
                                '&:hover': {
                                    boxShadow: theme.shadows[4],
                                },
                            }}
                        >
                            <ReactApexChart
                                options={{
                                    ...commonChartOptions,
                                    title: {
                                        text: config.title,
                                        align: 'left',
                                        style: {
                                            color: theme.palette.text.primary,
                                        },
                                    },
                                    yaxis: {
                                        ...commonChartOptions.yaxis,
                                        title: {
                                            text: config.yaxisTitle,
                                            style: {
                                                color: theme.palette.text.secondary,
                                            },
                                        },
                                        min: 0,
                                        max: config.max,
                                    },
                                }}
                                series={config.series}
                                type='line'
                                height={350}
                            />
                        </Paper>
                    </Grid>
                ))}
            </Grid>
        </Paper>
    );
};

export default SystemStats;

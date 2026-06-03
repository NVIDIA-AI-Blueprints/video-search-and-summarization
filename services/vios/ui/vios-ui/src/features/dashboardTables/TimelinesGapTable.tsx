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
import { Box, Card, CardHeader, IconButton, Typography, TextField, Skeleton, useTheme } from '@mui/material';
import { KeyboardArrowLeft, KeyboardArrowRight } from '@mui/icons-material';
import ReactApexChart from 'react-apexcharts';
import useVSTUIStore from '../../services/StateManagement';
import { useSnackbar } from 'notistack';
import { copyToClipboard } from '../../utils/misc/utils';

interface TimelineData {
    endTime: string;
    sizeInMegabytes: number;
    startTime: string;
}

interface SensorData {
    sizeInMegabytes: number;
    state: string;
    timelines: TimelineData[];
}

interface ApexChartsGlobals {
    series: number[][];
    seriesNames: string[];
    labels: string[];
}

interface ApexChartContext {
    w: {
        globals: ApexChartsGlobals;
    };
}

interface ApexDataPointSelectionConfig {
    seriesIndex: number;
    dataPointIndex: number;
}

const SENSORS_PER_PAGE = 5;
const TIMELINE_COLORS = ['#4285F4', '#34A853', '#FBBC05', '#EA4335', '#9C27B0', '#FF9800'];

const formatDuration = (milliseconds: number): string => {
    const seconds = Math.floor(milliseconds / 1000);
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);
    const days = Math.floor(hours / 24);

    if (days > 0) {
        return `${days}d`;
    }
    if (hours > 0) {
        return `${hours}h`;
    }
    if (minutes > 0) {
        return `${minutes}m`;
    }
    if (seconds > 0) {
        return `${seconds}s`;
    }
    return `${milliseconds}ms`;
};

const TimelinesGapTable: React.FC = () => {
    const [sensorData, setSensorData] = useState<{ [key: string]: string }>({});
    const [timelinesData, setTimelinesData] = useState<Array<{ sensorId: string; data: SensorData }>>([]);
    const [loading, setLoading] = useState(true);
    const [currentPage, setCurrentPage] = useState(0);
    const [searchQuery, setSearchQuery] = useState('');
    const vstAdaptorType = useVSTUIStore(state => state.vstAdaptorType);
    const sensors = useVSTUIStore(state => state.sensorServiceSensors);
    const storageSizes = useVSTUIStore(state => state.storageSizes);
    const theme = useTheme();
    const { enqueueSnackbar } = useSnackbar();

    useEffect(() => {
        if (sensors.length > 0) {
            const sensorMap = sensors.reduce((acc: { [key: string]: string }, sensor) => {
                acc[sensor.sensorId] = sensor.name;
                return acc;
            }, {});
            setSensorData(sensorMap);
        } else {
            // Clear sensor data when no sensors are available
            setSensorData({});
        }
        // Always set loading to false regardless of whether we have sensors or not
        setLoading(false);
    }, [sensors]);

    const getFilteredSensorIds = useCallback(() => {
        const lowerCaseQuery = searchQuery.toLowerCase();
        return Object.keys(sensorData).filter(sensorId => (sensorData[sensorId] || sensorId).toLowerCase().includes(lowerCaseQuery));
    }, [searchQuery, sensorData]);

    useEffect(() => {
        if (storageSizes && Object.keys(sensorData).length > 0) {
            const filteredSensorIds = getFilteredSensorIds();
            const sensorIds = filteredSensorIds.slice(currentPage * SENSORS_PER_PAGE, (currentPage + 1) * SENSORS_PER_PAGE);

            if (sensorIds.length === 0) {
                setTimelinesData([]);
                return;
            }

            const timelines = sensorIds.map(sensorId => ({
                sensorId,
                data: storageSizes[sensorId] as SensorData,
            }));
            setTimelinesData(timelines);
        }
    }, [currentPage, sensorData, searchQuery, storageSizes, getFilteredSensorIds]);

    const handleCopyTimeRange = async (timeline: TimelineData) => {
        try {
            const { startTime, endTime } = timeline;
            const jsonData = JSON.stringify({ startTime, endTime }, null, 4);
            await copyToClipboard(jsonData);
            enqueueSnackbar('Timeline data copied to clipboard', {
                variant: 'success',
                autoHideDuration: 2000,
            });
        } catch (error) {
            console.error('Failed to copy timeline data:', error);
            enqueueSnackbar('Failed to copy timeline data. Please try selecting and copying manually.', {
                variant: 'error',
                autoHideDuration: 3000,
            });
        }
    };

    const getChartOptions = () => {
        return {
            chart: {
                type: 'rangeBar' as const,
                toolbar: {
                    show: false,
                },
                animations: {
                    enabled: true,
                    easing: 'easeinout',
                    speed: 800,
                    animateGradually: {
                        enabled: true,
                        delay: 150,
                    },
                    dynamicAnimation: {
                        enabled: true,
                        speed: 350,
                    },
                },
                zoom: {
                    enabled: false,
                },
                selection: {
                    enabled: false,
                },
                background: 'transparent',
                events: {
                    dataPointSelection: (_event: MouseEvent, chartContext: ApexChartContext, config: ApexDataPointSelectionConfig) => {
                        const { seriesIndex, dataPointIndex } = config;
                        const sensorName = chartContext.w.globals.seriesNames[seriesIndex];
                        const sensorEntry = timelinesData.find(t => (sensorData[t.sensorId] || t.sensorId) === sensorName);

                        if (sensorEntry) {
                            const timeline = sensorEntry.data.timelines[dataPointIndex];
                            if (timeline) {
                                handleCopyTimeRange(timeline);
                            }
                        }
                    },
                },
            },
            plotOptions: {
                bar: {
                    horizontal: true,
                    distributed: true,
                    dataLabels: {
                        hideOverflowingLabels: false,
                    },
                    borderRadius: 0,
                    barHeight: '8.73px',
                },
            },
            dataLabels: {
                enabled: false,
            },
            xaxis: {
                type: 'datetime' as const,
                labels: {
                    datetimeUTC: false,
                    format: 'HH:mm:ss',
                    style: {
                        colors: theme.palette.text.primary,
                    },
                },
                title: {
                    text: 'Time',
                    style: {
                        color: theme.palette.text.primary,
                    },
                },
            },
            yaxis: {
                labels: {
                    style: {
                        fontSize: '12px',
                        colors: theme.palette.text.primary,
                    },
                },
                title: {
                    text: vstAdaptorType === 'streamer' ? 'Media Files' : 'Sensors',
                    style: {
                        color: theme.palette.text.primary,
                    },
                },
            },
            grid: {
                show: true,
                borderColor: theme.palette.divider,
                strokeDashArray: 0,
                position: 'back' as const,
                xaxis: {
                    lines: {
                        show: true,
                    },
                },
                yaxis: {
                    lines: {
                        show: true,
                    },
                },
                padding: {
                    left: 0,
                },
            },
            tooltip: {
                custom: function ({
                    seriesIndex,
                    dataPointIndex,
                    w,
                }: {
                    seriesIndex: number;
                    dataPointIndex: number;
                    w: { globals: ApexChartsGlobals };
                }) {
                    try {
                        // Get the series name (sensor name)
                        const sensorName = w.globals.seriesNames[seriesIndex];

                        // Find the sensor data using the name
                        const sensorEntry = timelinesData.find(t => (sensorData[t.sensorId] || t.sensorId) === sensorName);

                        if (!sensorEntry) {
                            console.warn('Sensor data not found for:', sensorName);
                            return '';
                        }

                        // Get the timeline entry
                        const timeline = sensorEntry.data.timelines[dataPointIndex];
                        if (!timeline) {
                            console.warn('Timeline not found for:', {
                                sensorName,
                                dataPointIndex,
                            });
                            return '';
                        }

                        const startTime = new Date(timeline.startTime).toLocaleString();
                        const endTime = new Date(timeline.endTime).toLocaleString();
                        const duration = new Date(timeline.endTime).getTime() - new Date(timeline.startTime).getTime();

                        return `
                            <div style="padding: 10px; background: rgba(0, 0, 0, 0.8); color: white; border-radius: 8px;">
                                <div><strong>Sensor ID:</strong> ${sensorEntry.sensorId}</div>
                                <div><strong>Sensor Name:</strong> ${sensorName}</div>
                                <div><strong>Size:</strong> ${timeline.sizeInMegabytes} MB</div>
                                <div><strong>Duration:</strong> ${formatDuration(duration)}</div>
                                <div><strong>Start:</strong> ${startTime}</div>
                                <div><strong>End:</strong> ${endTime}</div>
                            </div>
                        `;
                    } catch (error) {
                        console.error('Error in tooltip:', error);
                        return '';
                    }
                },
            },
            legend: {
                show: false,
            },
        };
    };

    const getChartSeries = () => {
        return timelinesData.map(({ sensorId, data }) => {
            const sensorName = sensorData[sensorId] || sensorId;
            return {
                name: sensorName,
                data:
                    data?.timelines?.map(timeline => ({
                        x: sensorName,
                        y: [new Date(timeline.startTime).getTime(), new Date(timeline.endTime).getTime()],
                        fillColor: TIMELINE_COLORS[0],
                    })) || [],
            };
        });
    };

    const filteredSensorIds = getFilteredSensorIds();
    const totalPages = Math.ceil(filteredSensorIds.length / SENSORS_PER_PAGE);

    if (loading) {
        return (
            <Card>
                <CardHeader
                    title={<Skeleton width='60%' />}
                    action={
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                            <Skeleton width={200} height={40} />
                            <Skeleton width={40} height={40} variant='circular' />
                            <Skeleton width={40} height={40} variant='circular' />
                        </Box>
                    }
                />
                <Box sx={{ p: 2, height: 400 }}>
                    <Skeleton width='100%' height='100%' variant='rectangular' />
                </Box>
            </Card>
        );
    }

    return (
        <Card>
            <CardHeader
                title={vstAdaptorType === 'streamer' ? 'Media Files Recording Timeline' : 'Sensors Recording Timeline'}
                subheader='Visual representation of recording segments and gaps'
            />
            <Box sx={{ px: 3, pt: 2 }}>
                <TextField
                    fullWidth
                    placeholder='Search sensors...'
                    value={searchQuery}
                    onChange={e => {
                        setSearchQuery(e.target.value);
                        setCurrentPage(0);
                    }}
                />
                <Typography variant='caption' color='text.secondary' sx={{ mt: 1, display: 'block' }}>
                    Click on any timeline segment to copy its UTC time range
                </Typography>
            </Box>
            {filteredSensorIds.length === 0 ? (
                <Typography sx={{ px: 3, py: 2 }} variant='body2'>
                    No sensors found.
                </Typography>
            ) : (
                <>
                    <Box
                        sx={{
                            mt: 2,
                            width: '100%',
                            height: '400px',
                            overflow: 'hidden',
                            pl: 0,
                        }}
                        dir='ltr'
                    >
                        <ReactApexChart options={getChartOptions()} series={getChartSeries()} type='rangeBar' height={400} />
                    </Box>
                    <Box
                        sx={{
                            display: 'flex',
                            justifyContent: 'flex-end',
                            alignItems: 'center',
                            mt: 2,
                            mr: 2,
                            mb: 2,
                        }}
                    >
                        <Typography variant='body2' color='text.secondary'>
                            {`${currentPage * SENSORS_PER_PAGE + 1}–${Math.min(
                                (currentPage + 1) * SENSORS_PER_PAGE,
                                filteredSensorIds.length
                            )} of ${filteredSensorIds.length}`}
                        </Typography>
                        <IconButton onClick={() => setCurrentPage(prev => Math.max(prev - 1, 0))} disabled={currentPage === 0}>
                            <KeyboardArrowLeft />
                        </IconButton>
                        <IconButton
                            onClick={() => setCurrentPage(prev => Math.min(prev + 1, totalPages - 1))}
                            disabled={currentPage + 1 >= totalPages}
                        >
                            <KeyboardArrowRight />
                        </IconButton>
                    </Box>
                </>
            )}
        </Card>
    );
};

export default TimelinesGapTable;

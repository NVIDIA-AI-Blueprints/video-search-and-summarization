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
import React, { useState, useCallback, useRef } from 'react';
import {
    Box,
    Typography,
    Paper,
    Button,
    TextField,
    FormControlLabel,
    Checkbox,
    Grid2 as Grid,
    Card,
    CardContent,
    LinearProgress,
} from '@mui/material';
import SensorSelector from '../../../components/sensorSelector/MultipleSensorSelector';
import useVSTUIStore from '../../../services/StateManagement';
import { Sensor } from '../../../interfaces/interfaces';
import StreamManager, { StreamConfig, StreamType, ErrorType } from 'vst-streaming-lib';
import config from '../../../config';

interface StreamResult {
    sensorId: string;
    sensorName: string;
    streamType: StreamType;
    success: boolean;
    error?: string;
    startTime: string;
    endTime: string;
    firstFrameReceived: boolean;
    successCallbackReceived: boolean;
}

interface StreamStats {
    total: number;
    success: number;
    failed: number;
    firstFrameFailed: number;
    successCallbackFailed: number;
}

export const StreamAutomation: React.FC = () => {
    const sensors = useVSTUIStore(state => state.sensorServiceSensors);
    const [selectedSensors, setSelectedSensors] = useState<Sensor[]>([]);
    const [isAutomatic, setIsAutomatic] = useState(false);
    const [interval, setInterval] = useState(10);
    const [iterationDelay, setIterationDelay] = useState(5);
    const [firstFrameTimeout, setFirstFrameTimeout] = useState(20);
    const [successCallbackTimeout, setSuccessCallbackTimeout] = useState(20);
    const [enableLive, setEnableLive] = useState(true);
    const [enableReplay, setEnableReplay] = useState(true);
    const [isLoading, setIsLoading] = useState(false);
    const [progress, setProgress] = useState(0);
    const [stats, setStats] = useState<StreamStats>({
        total: 0,
        success: 0,
        failed: 0,
        firstFrameFailed: 0,
        successCallbackFailed: 0,
    });

    const automationIntervalRef = useRef<number>();
    const streamManagerRef = useRef<StreamManager | null>(null);

    const handleStreamTest = useCallback(
        async (sensor: Sensor, streamType: StreamType): Promise<StreamResult> => {
            return new Promise(resolve => {
                const startTime = new Date().toISOString();
                let successCallbackReceived = false;
                let firstFrameReceived = false;

                // Create a container for the video
                const videoElementId = `video-${streamType}-${sensor.sensorId}-${Date.now()}`;
                const videoElement = document.createElement('video');
                videoElement.id = videoElementId;
                videoElement.style.width = '100%';
                videoElement.style.maxHeight = '300px';

                // Create a container div for the video
                const containerDiv = document.createElement('div');
                containerDiv.id = `container-${videoElementId}`;
                containerDiv.style.position = 'relative';
                containerDiv.appendChild(videoElement);

                // Add title to the video
                const titleDiv = document.createElement('div');
                titleDiv.style.position = 'absolute';
                titleDiv.style.top = '10px';
                titleDiv.style.left = '10px';
                titleDiv.style.background = 'rgba(0, 0, 0, 0.7)';
                titleDiv.style.color = 'white';
                titleDiv.style.padding = '5px 10px';
                titleDiv.style.borderRadius = '4px';
                titleDiv.textContent = `${sensor.name || sensor.sensorId} - ${streamType}`;
                containerDiv.appendChild(titleDiv);

                // Add the container to the streams container
                const streamsContainer = document.getElementById('streams-container');
                if (streamsContainer) {
                    const gridItem = document.createElement('div');
                    gridItem.style.margin = '10px';
                    gridItem.appendChild(containerDiv);
                    streamsContainer.appendChild(gridItem);
                }

                streamManagerRef.current = new StreamManager();

                const timeoutDuration = Math.min(Math.max(firstFrameTimeout, successCallbackTimeout) * 1000, interval * 1000);

                const cleanup = () => {
                    if (streamManagerRef.current) {
                        streamManagerRef.current.stopStreaming();
                        streamManagerRef.current = null;
                    }
                    document.getElementById(`container-${videoElementId}`)?.parentElement?.remove();
                    clearTimeout(timeoutId);
                };

                const onFirstFrameReceived = () => {
                    firstFrameReceived = true;
                };

                const onSuccess = () => {
                    successCallbackReceived = true;
                };

                const onError = (error: ErrorType) => {
                    cleanup();
                    resolve({
                        sensorId: sensor.sensorId,
                        sensorName: sensor.name || sensor.sensorId,
                        streamType,
                        success: false,
                        error: error.message,
                        startTime,
                        endTime: new Date().toISOString(),
                        firstFrameReceived,
                        successCallbackReceived,
                    });
                };

                const timeoutId = setTimeout(() => {
                    cleanup();
                    resolve({
                        sensorId: sensor.sensorId,
                        sensorName: sensor.name || sensor.sensorId,
                        streamType,
                        success: firstFrameReceived && successCallbackReceived,
                        error: !firstFrameReceived
                            ? 'First frame timeout'
                            : !successCallbackReceived
                              ? 'Success callback timeout'
                              : undefined,
                        startTime,
                        endTime: new Date().toISOString(),
                        firstFrameReceived,
                        successCallbackReceived,
                    });
                }, timeoutDuration);

                const streamConfig: StreamConfig = {
                    streamId: sensor.sensorId,
                    options: {
                        rtptransport: 'udp',
                        timeout: 60,
                        quality: 'auto',
                    },
                };

                if (streamType === StreamType.Replay) {
                    streamConfig.startTime = '2020-12-01T12:00:20.000Z';
                }

                let wsEndpoint = (streamType === StreamType.Live ? config.liveStreamEndpoint : config.replayStreamEndpoint).startsWith(
                    'https'
                )
                    ? (streamType === StreamType.Live ? config.liveStreamEndpoint : config.replayStreamEndpoint).replace('https', 'wss')
                    : (streamType === StreamType.Live ? config.liveStreamEndpoint : config.replayStreamEndpoint).replace('http', 'ws');

                let proxy = window.location.pathname;
                if (proxy !== '/' && proxy.length > 0) {
                    if (proxy[proxy.length - 1] === '/') {
                        proxy = proxy.slice(0, -1);
                    }
                    wsEndpoint = `${wsEndpoint}${wsEndpoint.endsWith('/') ? '' : '/'}${proxy}`;
                }

                streamManagerRef.current.updateConfig({
                    inboundStreamVideoElementId: videoElementId,
                    enableMicrophone: false,
                    enableCamera: false,
                    streamType: streamType === StreamType.Live ? StreamType.Live : StreamType.Replay,
                    enableWebsocketPing: true,
                    enableLogs: true,
                    vstWebsocketEndpoint: wsEndpoint,
                    firstFrameReceivedCallback: onFirstFrameReceived,
                    successCallback: onSuccess,
                    errorCallback: onError,
                });

                streamManagerRef.current.startStreaming(streamConfig);
            });
        },
        [interval, firstFrameTimeout, successCallbackTimeout]
    );

    const handleAutomation = useCallback(async () => {
        setIsLoading(true);
        setProgress(0);

        // Clear previous stream elements
        const streamsContainer = document.getElementById('streams-container');
        if (streamsContainer) {
            streamsContainer.innerHTML = '';
        }

        // Add delay before starting next iteration (except for first run)
        if (isAutomatic) {
            await new Promise(resolve => setTimeout(resolve, iterationDelay * 1000));
        }

        const streamTypes: StreamType[] = [];
        if (enableLive) streamTypes.push(StreamType.Live);
        if (enableReplay) streamTypes.push(StreamType.Replay);

        const totalTests = selectedSensors.length * streamTypes.length;
        const testPromises: Promise<StreamResult>[] = [];

        // Create all test promises
        selectedSensors.forEach(sensor => {
            streamTypes.forEach(streamType => {
                testPromises.push(
                    handleStreamTest(sensor, streamType).then(result => {
                        setProgress(prev => Math.min(prev + 100 / totalTests, 100));
                        return result;
                    })
                );
            });
        });

        // Run all tests in parallel
        const newResults = await Promise.all(testPromises);

        // Calculate stats
        const stats = newResults.reduce(
            (acc, result) => ({
                total: acc.total + 1,
                success: acc.success + (result.success ? 1 : 0),
                failed: acc.failed + (result.success ? 0 : 1),
                firstFrameFailed: acc.firstFrameFailed + (!result.firstFrameReceived ? 1 : 0),
                successCallbackFailed: acc.successCallbackFailed + (!result.successCallbackReceived ? 1 : 0),
            }),
            {
                total: 0,
                success: 0,
                failed: 0,
                firstFrameFailed: 0,
                successCallbackFailed: 0,
            }
        );

        setStats(stats);
        setIsLoading(false);
    }, [selectedSensors, enableLive, enableReplay, handleStreamTest, isAutomatic, iterationDelay]);

    const clearStreams = useCallback(() => {
        const streamsContainer = document.getElementById('streams-container');
        if (streamsContainer) {
            streamsContainer.innerHTML = '';
        }
    }, []);

    const toggleAutomation = () => {
        if (isAutomatic) {
            if (automationIntervalRef.current) {
                window.clearInterval(automationIntervalRef.current);
            }
            clearStreams();
            setIsAutomatic(false);
        } else {
            handleAutomation();
            automationIntervalRef.current = window.setInterval(handleAutomation, interval * 1000);
            setIsAutomatic(true);
        }
    };

    return (
        <Box p={3}>
            <Typography variant='h4' gutterBottom sx={{ color: 'primary.main' }}>
                Stream API Automation
            </Typography>

            <Paper elevation={3} sx={{ p: 3, mb: 3 }}>
                <Typography variant='h6' gutterBottom color='primary'>
                    Select Sensors
                </Typography>
                <SensorSelector
                    multiple
                    sensors={sensors}
                    selectedSensors={selectedSensors}
                    onChange={(sensors: Sensor[] | undefined) => setSelectedSensors(sensors || [])}
                />
            </Paper>

            <Paper elevation={3} sx={{ p: 3, mb: 3 }}>
                <Typography variant='h6' gutterBottom color='primary'>
                    Stream Types
                </Typography>
                <Box display='flex' gap={2}>
                    <FormControlLabel
                        control={<Checkbox checked={enableLive} onChange={e => setEnableLive(e.target.checked)} />}
                        label='Live Stream'
                    />
                    <FormControlLabel
                        control={<Checkbox checked={enableReplay} onChange={e => setEnableReplay(e.target.checked)} />}
                        label='Replay Stream'
                    />
                </Box>
            </Paper>

            <Paper elevation={3} sx={{ p: 3, mb: 3 }}>
                <Box display='flex' alignItems='flex-start' gap={2} flexWrap='wrap'>
                    <TextField
                        label='Interval (seconds)'
                        type='number'
                        value={interval}
                        onChange={e => setInterval(Number(e.target.value))}
                        disabled={isAutomatic}
                        size='small'
                        sx={{ minWidth: 200 }}
                    />
                    <TextField
                        label='Iteration Delay (seconds)'
                        type='number'
                        value={iterationDelay}
                        onChange={e => setIterationDelay(Number(e.target.value))}
                        disabled={isAutomatic}
                        size='small'
                        sx={{ minWidth: 200 }}
                        helperText='Delay between iterations'
                    />
                    <TextField
                        label='First Frame Timeout (seconds)'
                        type='number'
                        value={firstFrameTimeout}
                        onChange={e => setFirstFrameTimeout(Number(e.target.value))}
                        disabled={isAutomatic}
                        size='small'
                        sx={{ minWidth: 200 }}
                    />
                    <TextField
                        label='Success Callback Timeout (seconds)'
                        type='number'
                        value={successCallbackTimeout}
                        onChange={e => setSuccessCallbackTimeout(Number(e.target.value))}
                        disabled={isAutomatic}
                        size='small'
                        sx={{ minWidth: 200 }}
                    />
                    <Button
                        variant='contained'
                        onClick={toggleAutomation}
                        color={isAutomatic ? 'error' : 'primary'}
                        disabled={!selectedSensors.length || (!enableLive && !enableReplay)}
                    >
                        {isAutomatic ? 'Stop Automation' : 'Start Automation'}
                    </Button>
                    <Button
                        variant='contained'
                        onClick={handleAutomation}
                        disabled={isAutomatic || isLoading || !selectedSensors.length || (!enableLive && !enableReplay)}
                    >
                        Test Once
                    </Button>
                </Box>

                {isLoading && (
                    <Box sx={{ width: '100%', mt: 2 }}>
                        <LinearProgress variant='determinate' value={progress} />
                        <Typography variant='body2' color='text.secondary' align='center'>
                            {Math.round(progress)}% Complete
                        </Typography>
                    </Box>
                )}
            </Paper>

            <Paper elevation={3} sx={{ p: 3, mb: 3 }}>
                <Typography variant='h6' color='primary'>
                    Statistics
                </Typography>
                <Grid container spacing={2}>
                    <Grid size={{ xs: 12, sm: 4 }}>
                        <Card sx={{ height: '100%' }}>
                            <CardContent>
                                <Typography color='text.secondary'>Total Tests</Typography>
                                <Typography variant='h4'>{stats.total}</Typography>
                            </CardContent>
                        </Card>
                    </Grid>
                    <Grid size={{ xs: 12, sm: 4 }}>
                        <Card sx={{ height: '100%' }}>
                            <CardContent>
                                <Typography color='text.secondary'>Successful</Typography>
                                <Typography variant='h4' color='success.main'>
                                    {stats.success}
                                </Typography>
                            </CardContent>
                        </Card>
                    </Grid>
                    <Grid size={{ xs: 12, sm: 4 }}>
                        <Card sx={{ height: '100%' }}>
                            <CardContent>
                                <Typography color='text.secondary'>Failed</Typography>
                                <Typography variant='h4' color='error.main'>
                                    {stats.failed}
                                </Typography>
                                <Typography variant='body2' color='error.main'>
                                    First Frame: {stats.firstFrameFailed} | Success Callback: {stats.successCallbackFailed}
                                </Typography>
                            </CardContent>
                        </Card>
                    </Grid>
                </Grid>
            </Paper>

            <Paper elevation={3} sx={{ p: 3, mb: 3 }}>
                <Typography variant='h6' gutterBottom color='primary'>
                    Active Streams
                </Typography>
                <Box
                    id='streams-container'
                    sx={{
                        display: 'grid',
                        gridTemplateColumns: {
                            xs: '1fr',
                            sm: '1fr 1fr',
                            md: '1fr 1fr 1fr',
                        },
                        gap: 2,
                        mt: 2,
                    }}
                />
            </Paper>
        </Box>
    );
};

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
import {
    Box,
    Button,
    TextField,
    Typography,
    Grid,
    Paper,
    Card,
    CardContent,
    Chip,
    LinearProgress,
    Alert,
    FormControlLabel,
    Checkbox,
} from '@mui/material';
import SensorSelector from '../../../components/sensorSelector/MultipleSensorSelector';
import config from '../../../config';
import { Sensor } from '../../../interfaces/interfaces';
import useVSTUIStore from '../../../services/StateManagement';
import nvAxios from '../../../services/Axios';
import AnalyticsOverlayDialog from '../../../components/videoPlayer/videoPlayerUtils/analytics/AnalyticsOverlayDialog';
import AnalyticsOverlayConfigurationSummary from '../../../components/videoPlayer/videoPlayerUtils/analytics/AnalyticsOverlayConfigurationSummary';
import { StreamOverlayOptions, StreamCompositeOptions, StreamType } from 'vst-streaming-lib';

interface ApiResult {
    success: boolean;
    timestamp: string;
    imageUrl?: string;
    error?: string;
    responseTime?: number;
    sensorId: string;
    sensorName?: string;
}

export const PictureAutomation: React.FC = () => {
    const liveServiceSensors = useVSTUIStore(state => state.liveServiceSensors);
    const [selectedSensors, setSelectedSensors] = useState<Sensor[]>([]);
    const [timestamps, setTimestamps] = useState<string[]>([]);
    const [newTimestamp, setNewTimestamp] = useState<string>('');
    const [interval, setInterval] = useState<number>(5);
    const [isAutomatic, setIsAutomatic] = useState(false);
    const [intervalId, setIntervalId] = useState<number | null>(null);
    const [results, setResults] = useState<ApiResult[]>([]);
    const [stats, setStats] = useState({ total: 0, success: 0, failed: 0 });
    const [isLoading, setIsLoading] = useState(false);
    const [progress, setProgress] = useState(0);

    const [enableVisualization, setEnableVisualization] = useState(false);
    const [overlaySettings, setOverlaySettings] = useState<StreamOverlayOptions>({
        bbox: {
            showAll: false,
            objectId: [],
            classType: [],
            showObjId: false,
            objIdPosition: 0,
            objIdTextColor: 'white',
            objIdTextBGColor: 'black',
        },
        tripwire: { showAll: false, id: [] },
        roi: { showAll: false, id: [] },
        debug: false,
        opacity: 255,
        color: 'red',
        thickness: 6,
        proximityClass: [],
        entrantClass: [],
        proximityAreaFactor: 1.3,
        proximityAnimation: '',
        overlayColorCode: [],
        needHalo: false,
        pose: false,
    });

    // Analytics overlay dialog state
    const [openAnalyticsDialog, setOpenAnalyticsDialog] = useState(false);
    const handleAnalyticsDialogOpen = () => setOpenAnalyticsDialog(true);
    const handleAnalyticsDialogClose = () => setOpenAnalyticsDialog(false);

    const handleAnalyticsOverlaySave = (settings: { overlay: StreamOverlayOptions; composite?: StreamCompositeOptions }) => {
        setOverlaySettings(settings.overlay);
        setOpenAnalyticsDialog(false);
    };

    const formatUTCTime = (date: Date) => {
        return date.toISOString();
    };

    const addCurrentTime = () => {
        const currentUTCTime = new Date().toISOString();
        setTimestamps(prev => [...prev, currentUTCTime]);
    };

    const addNewTimestamp = () => {
        if (newTimestamp) {
            try {
                // Validate the timestamp format and ensure it's treated as UTC
                const parsedDate = new Date(newTimestamp);
                if (isNaN(parsedDate.getTime())) {
                    throw new Error('Invalid date');
                }
                const formattedTime = parsedDate.toISOString();
                setTimestamps(prev => [...prev, formattedTime]);
                setNewTimestamp('');
            } catch (error) {
                alert('Please enter a valid timestamp in format: YYYY-MM-DDTHH:mm:ss.SSSZ');
            }
        }
    };

    const removeTimestamp = (index: number) => {
        setTimestamps(prev => prev.filter((_, i) => i !== index));
    };

    const fetchPicture = useCallback(
        async (streamId: string, timestamp?: string) => {
            try {
                const baseUrl = timestamp ? config.replayStreamEndpoint : config.liveStreamEndpoint;

                // Construct query parameters object
                const queryParams = new URLSearchParams();

                if (timestamp) {
                    queryParams.append('startTime', timestamp);
                }

                if (enableVisualization) {
                    queryParams.append('overlay', JSON.stringify(overlaySettings));
                }

                const endpoint = timestamp
                    ? `${baseUrl}/api/v1/replay/stream/${streamId}/picture?${queryParams.toString()}`
                    : `${baseUrl}/api/v1/live/stream/${streamId}/picture?${queryParams.toString()}`;

                const response = await nvAxios.get(endpoint, {
                    responseType: 'blob',
                    headers: { streamId },
                });

                const binaryData = [];
                binaryData.push(response.data);
                const imageUrl = window.URL.createObjectURL(new Blob(binaryData, { type: 'image/jpeg' }));

                return {
                    success: true,
                    timestamp: formatUTCTime(new Date()),
                    imageUrl,
                };
            } catch (error) {
                console.error('Error fetching screenshot:', error);
                return {
                    success: false,
                    timestamp: formatUTCTime(new Date()),
                    error: error instanceof Error ? error.message : 'Unknown error',
                };
            }
        },
        [enableVisualization, overlaySettings]
    );

    const handleFetch = useCallback(async () => {
        setIsLoading(true);
        setProgress(0);
        setResults([]);

        const newResults: ApiResult[] = [];
        let successCount = 0;
        let failedCount = 0;

        const totalCalls = selectedSensors.length * (timestamps.length || 1);
        let completedCalls = 0;

        for (const sensor of selectedSensors) {
            if (!sensor.streamId) {
                console.warn('Skipping sensor without streamId:', sensor);
                continue;
            }

            if (timestamps.length > 0) {
                for (const time of timestamps) {
                    const startTime = performance.now();
                    const result = await fetchPicture(sensor.streamId, time);
                    const responseTime = performance.now() - startTime;

                    newResults.push({
                        ...result,
                        responseTime,
                        sensorId: sensor.sensorId,
                        sensorName: sensor.name,
                    });
                    if (result.success) successCount++;
                    else failedCount++;

                    completedCalls++;
                    setProgress((completedCalls / totalCalls) * 100);
                }
            } else {
                const startTime = performance.now();
                const result = await fetchPicture(sensor.streamId);
                const responseTime = performance.now() - startTime;

                newResults.push({
                    ...result,
                    responseTime,
                    sensorId: sensor.sensorId,
                    sensorName: sensor.name,
                });
                if (result.success) successCount++;
                else failedCount++;

                completedCalls++;
                setProgress((completedCalls / totalCalls) * 100);
            }
        }

        setResults(newResults);
        setStats(prev => ({
            total: prev.total + newResults.length,
            success: prev.success + successCount,
            failed: prev.failed + failedCount,
        }));
        setIsLoading(false);
    }, [selectedSensors, timestamps, fetchPicture]);

    const toggleAutomation = () => {
        if (!isAutomatic) {
            const id = window.setInterval(() => {
                handleFetch();
            }, interval * 1000);
            setIntervalId(id);
        } else {
            if (intervalId) window.clearInterval(intervalId);
            setIntervalId(null);
        }
        setIsAutomatic(!isAutomatic);
    };

    useEffect(() => {
        return () => {
            if (intervalId) window.clearInterval(intervalId);
        };
    }, [intervalId]);

    return (
        <Box p={3}>
            <Typography variant='h4' gutterBottom sx={{ color: 'primary.main' }}>
                Picture API Automation
            </Typography>

            <Paper elevation={3} sx={{ p: 3, mb: 3 }}>
                <Typography variant='h6' gutterBottom color='primary'>
                    Select Streams
                </Typography>
                <SensorSelector
                    multiple
                    sensors={liveServiceSensors}
                    selectedSensors={selectedSensors}
                    onChange={(sensors: Sensor[] | undefined) => setSelectedSensors(sensors || [])}
                />
            </Paper>

            <Paper elevation={3} sx={{ p: 3, mb: 3 }}>
                <Typography variant='h6' gutterBottom color='primary'>
                    Timestamps
                </Typography>
                <Box
                    display='flex'
                    alignItems='flex-start'
                    gap={2}
                    mb={2}
                    sx={{
                        flexDirection: { xs: 'column', md: 'row' },
                        '& > button': {
                            minWidth: { xs: '100%', md: '150px' },
                            whiteSpace: 'nowrap',
                        },
                    }}
                >
                    <TextField
                        label='Add UTC Timestamp (YYYY-MM-DDTHH:mm:ss.SSSZ)'
                        type='text'
                        value={newTimestamp}
                        onChange={e => setNewTimestamp(e.target.value)}
                        placeholder='2024-03-20T14:30:00.000Z'
                        fullWidth
                        helperText='Example: 2024-03-20T14:30:00.000Z'
                    />
                    <Box
                        display='flex'
                        gap={2}
                        sx={{
                            width: { xs: '100%', md: 'auto' },
                            flexShrink: 0,
                            mt: { xs: 0, md: '5px' },
                        }}
                    >
                        <Button
                            variant='contained'
                            onClick={addNewTimestamp}
                            sx={{
                                flex: { xs: 1, md: 'none' },
                                height: '40px',
                            }}
                        >
                            Add Time
                        </Button>
                        <Button
                            variant='contained'
                            onClick={addCurrentTime}
                            sx={{
                                flex: { xs: 1, md: 'none' },
                                height: '40px',
                            }}
                        >
                            Add Current Time
                        </Button>
                    </Box>
                </Box>

                <Box mb={2}>
                    {timestamps.map((time, index) => (
                        <Chip key={index} label={time} onDelete={() => removeTimestamp(index)} sx={{ m: 0.5 }} />
                    ))}
                </Box>
            </Paper>

            <Paper elevation={3} sx={{ p: 3, mb: 3 }}>
                <Box display='flex' alignItems='center' gap={2}>
                    <TextField
                        label='Interval (seconds)'
                        type='number'
                        value={interval}
                        onChange={e => setInterval(Number(e.target.value))}
                        disabled={isAutomatic}
                        size='small'
                    />
                    <Button variant='contained' onClick={toggleAutomation} color={isAutomatic ? 'error' : 'primary'}>
                        {isAutomatic ? 'Stop Automation' : 'Start Automation'}
                    </Button>
                    <Button variant='contained' onClick={handleFetch} disabled={isAutomatic || isLoading}>
                        Fetch Once
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
                    <Grid item xs={4}>
                        <Card>
                            <CardContent>
                                <Typography color='text.secondary'>Total Calls</Typography>
                                <Typography variant='h4'>{stats.total}</Typography>
                            </CardContent>
                        </Card>
                    </Grid>
                    <Grid item xs={4}>
                        <Card>
                            <CardContent>
                                <Typography color='text.secondary'>Successful</Typography>
                                <Typography variant='h4' color='success.main'>
                                    {stats.success}
                                </Typography>
                            </CardContent>
                        </Card>
                    </Grid>
                    <Grid item xs={4}>
                        <Card>
                            <CardContent>
                                <Typography color='text.secondary'>Failed</Typography>
                                <Typography variant='h4' color='error.main'>
                                    {stats.failed}
                                </Typography>
                            </CardContent>
                        </Card>
                    </Grid>
                </Grid>
            </Paper>

            <Paper elevation={3} sx={{ p: 3, mb: 3 }}>
                <Typography variant='h6' gutterBottom color='primary'>
                    Analytics Overlay Parameters
                </Typography>

                <FormControlLabel
                    control={<Checkbox checked={enableVisualization} onChange={e => setEnableVisualization(e.target.checked)} />}
                    label='Enable Analytics Overlay'
                    sx={{ mb: 2 }}
                />

                {enableVisualization && (
                    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                        <Typography variant='body2' color='text.secondary'>
                            Configure advanced analytics overlay settings including bounding boxes, object IDs, colors, positions, proximity
                            settings, and more.
                        </Typography>

                        <Button variant='outlined' onClick={handleAnalyticsDialogOpen} sx={{ alignSelf: 'flex-start' }}>
                            Configure Analytics Overlay
                        </Button>

                        <AnalyticsOverlayConfigurationSummary
                            overlaySettings={overlaySettings}
                            compact={true}
                            title='Current Analytics Overlay Settings'
                        />
                    </Box>
                )}
            </Paper>

            <Grid container spacing={2}>
                {results.map((result, index) => (
                    <Grid item xs={12} sm={6} md={4} key={index}>
                        <Card>
                            <CardContent>
                                <Typography variant='subtitle1' fontWeight='bold' gutterBottom>
                                    {result.sensorName || result.sensorId}
                                </Typography>
                                <Typography variant='subtitle2'>Sensor ID: {result.sensorId}</Typography>
                                <Typography variant='subtitle2'>Time: {result.timestamp}</Typography>
                                <Typography variant='subtitle2' color='primary'>
                                    Response Time: {(result.responseTime! / 1000).toFixed(2)}s
                                </Typography>
                                {result.success ? (
                                    <Box sx={{ mt: 1 }}>
                                        <img
                                            src={result.imageUrl}
                                            alt={`Stream ${result.sensorName || result.sensorId}`}
                                            style={{
                                                maxWidth: '100%',
                                                height: 'auto',
                                            }}
                                        />
                                    </Box>
                                ) : (
                                    <Alert severity='error' sx={{ mt: 1 }}>
                                        {result.error}
                                    </Alert>
                                )}
                            </CardContent>
                        </Card>
                    </Grid>
                ))}
            </Grid>

            {/* Analytics Overlay Dialog */}
            <AnalyticsOverlayDialog
                open={openAnalyticsDialog}
                onClose={handleAnalyticsDialogClose}
                onSave={handleAnalyticsOverlaySave}
                streamType={StreamType.Replay}
            />
        </Box>
    );
};

export default PictureAutomation;

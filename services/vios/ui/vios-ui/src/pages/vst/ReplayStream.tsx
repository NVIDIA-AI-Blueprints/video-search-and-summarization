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
import useVSTUIStore from '../../services/StateManagement';
import SensorSelector from '../../components/sensorSelector/MultipleSensorSelector';
import React, { useCallback, useState, useEffect, useMemo } from 'react';
import { Box, Grid2 as Grid, Stack, Alert, CircularProgress, Typography } from '@mui/material';
import VSTStreamManager from '../../features/streamManager/StreamManager';
import { Sensor } from '../../interfaces/interfaces';
import { StreamType } from 'vst-streaming-lib';
import { getSensorsWithTimeline } from '../../utils/misc/sensorUtils';
import { useLocation } from 'react-router-dom';

const ReplayStream = () => {
    const location = useLocation();
    const sensors = useVSTUIStore(state => state.sensorServiceSensors);
    const replaySensors = useVSTUIStore(state => state.replayServiceSensors);
    const isReplayServiceAvailable = useVSTUIStore(state => state.isReplayServiceAvailable);
    const vstAdaptorType = useVSTUIStore(state => state.vstAdaptorType);

    // Add state for available sensors since getSensorsWithTimeline is async
    const [availableSensors, setAvailableSensors] = useState<Sensor[]>([]);
    const [selectedSensors, setSelectedSensors] = useState<Sensor[] | undefined>();
    const [selectedTags, setSelectedTags] = useState<string[]>([]);
    const [isLoadingSensors, setIsLoadingSensors] = useState<boolean>(false);

    const containerStyle = {
        opacity: isReplayServiceAvailable ? 1 : 0.5,
        pointerEvents: isReplayServiceAvailable ? 'auto' : 'none',
        position: 'relative' as const,
    };

    // Load available sensors with timeline data when page becomes visible
    useEffect(() => {
        // Only load when this page is visible (recorded-streams route) and adaptor type is available
        if (location.pathname === '/recorded-streams' && vstAdaptorType !== undefined) {
            const loadAvailableSensors = async () => {
                // Set loading state for MMS
                if (vstAdaptorType === 'mms') {
                    setIsLoadingSensors(true);
                }

                try {
                    const sensorsWithTimeline = await getSensorsWithTimeline(sensors, replaySensors);
                    setAvailableSensors(sensorsWithTimeline);
                } catch (error) {
                    console.error('Error loading sensors with timeline:', error);
                } finally {
                    // Clear loading state for MMS
                    if (vstAdaptorType === 'mms') {
                        setIsLoadingSensors(false);
                    }
                }
            };

            loadAvailableSensors();
        }
    }, [location.pathname, sensors, replaySensors, vstAdaptorType]); // Trigger when path changes or sensor data changes

    const uniqueTags = useMemo(() => {
        const allTags = availableSensors
            .map(sensor => sensor.tags)
            .filter(tags => tags)
            .join(',')
            .split(',')
            .map(tag => tag.trim())
            .filter(tag => tag !== '');
        return Array.from(new Set(allTags));
    }, [availableSensors]);

    const filteredSensors = useMemo(() => {
        if (selectedTags.length === 0) return availableSensors;
        return availableSensors.filter(sensor => {
            if (!sensor.tags) return false;
            const sensorTags = sensor.tags.split(',').map(tag => tag.trim());
            return selectedTags.some(selectedTag => sensorTags.includes(selectedTag));
        });
    }, [availableSensors, selectedTags]);

    const tagSensors = useMemo(() => uniqueTags.map(tag => ({ name: tag, sensorId: tag }) as Sensor), [uniqueTags]);

    const selectedTagSensors = useMemo(() => selectedTags.map(tag => ({ name: tag, sensorId: tag }) as Sensor), [selectedTags]);

    const handleCloseVideo = (streamId: string) => {
        setSelectedSensors(prev => prev?.filter(sensor => sensor.streamId !== streamId));
    };

    const handleTagSelection = useCallback((selection: Sensor[] | undefined) => {
        setSelectedTags(selection?.map(s => s.name) || []);
    }, []);

    const handleSensorSelection = useCallback((selection: Sensor[] | undefined) => {
        setSelectedSensors(selection || []);
    }, []);

    return (
        <Grid container spacing={2}>
            <Grid size={{ xs: 12 }}>
                <h1>Replay Streaming</h1>
                {!isReplayServiceAvailable && (
                    <Box mb={2}>
                        <Alert severity='error'>Replay service is not available. Replay streaming features are disabled.</Alert>
                    </Box>
                )}
            </Grid>

            <Box sx={containerStyle} width='100%'>
                <Grid container spacing={2}>
                    {/* Loading indicator while adaptor type is being determined */}
                    {vstAdaptorType === undefined && (
                        <Grid size={{ xs: 12 }}>
                            <Box
                                sx={{
                                    display: 'flex',
                                    flexDirection: 'column',
                                    alignItems: 'center',
                                    justifyContent: 'center',
                                    minHeight: '60vh',
                                }}
                            >
                                <CircularProgress size={60} />
                                <Typography variant='h6' sx={{ mt: 2, color: 'text.secondary' }}>
                                    Initializing...
                                </Typography>
                            </Box>
                        </Grid>
                    )}

                    {/* Loading indicator for MMS timeline fetch */}
                    {vstAdaptorType !== undefined && isLoadingSensors && (
                        <Grid size={{ xs: 12 }}>
                            <Box
                                sx={{
                                    display: 'flex',
                                    flexDirection: 'column',
                                    alignItems: 'center',
                                    justifyContent: 'center',
                                    minHeight: '60vh',
                                }}
                            >
                                <CircularProgress size={60} />
                                <Typography variant='h6' sx={{ mt: 2, color: 'text.secondary' }}>
                                    Loading timelines...
                                </Typography>
                                <Typography variant='body2' sx={{ mt: 1, color: 'text.disabled' }}>
                                    This may take a few seconds
                                </Typography>
                            </Box>
                        </Grid>
                    )}

                    {vstAdaptorType !== undefined && !isLoadingSensors && (
                        <>
                            <Grid size={{ xs: 12 }}>
                                <Stack spacing={3} sx={{ mb: 2 }}>
                                    <SensorSelector
                                        multiple
                                        sensors={tagSensors}
                                        selectedSensors={selectedTagSensors}
                                        onChange={handleTagSelection}
                                        label='Select Tags'
                                    />
                                    <SensorSelector
                                        multiple
                                        sensors={filteredSensors}
                                        onChange={handleSensorSelection}
                                        selectedSensors={selectedSensors}
                                    />
                                </Stack>
                            </Grid>
                        </>
                    )}

                    {vstAdaptorType !== undefined && !isLoadingSensors && (
                        <Grid size={{ xs: 12 }}>
                            <Box className='video-container'>
                                <Grid container spacing={2}>
                                    {selectedSensors
                                        ?.filter(sensor => sensor.streamId)
                                        .map(sensor => (
                                            <Grid size={{ xs: 12, md: 6 }} key={sensor.streamId}>
                                                <VSTStreamManager
                                                    sensor={sensor}
                                                    streamType={StreamType.Replay}
                                                    onClose={() => handleCloseVideo(sensor.streamId!)}
                                                />
                                            </Grid>
                                        ))}
                                </Grid>
                            </Box>
                        </Grid>
                    )}
                </Grid>
            </Box>
        </Grid>
    );
};

export default ReplayStream;

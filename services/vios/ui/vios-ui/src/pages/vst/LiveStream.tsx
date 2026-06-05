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
import React, { useCallback, useState, useMemo } from 'react';
import { Box, Grid2 as Grid, Stack, Alert } from '@mui/material';
import VSTStreamManager from '../../features/streamManager/StreamManager';
import { Sensor } from '../../interfaces/interfaces';
import { StreamType } from 'vst-streaming-lib';
import { getAuthorizedLiveSensors } from '../../utils/misc/sensorUtils';

const LiveStream = () => {
    const sensors = useVSTUIStore(state => state.sensorServiceSensors);
    const liveSensors = useVSTUIStore(state => state.liveServiceSensors);
    const isLiveStreamServiceAvailable = useVSTUIStore(state => state.isLiveStreamServiceAvailable);

    const availableSensors = useMemo(() => getAuthorizedLiveSensors(sensors, liveSensors), [sensors, liveSensors]);

    const [selectedSensors, setSelectedSensors] = useState<Sensor[] | undefined>();
    const [selectedTags, setSelectedTags] = useState<string[]>([]);

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
        console.log('Selected Sensors: ', selection);
    }, []);

    return (
        <Grid container spacing={2}>
            <Grid size={{ xs: 12 }}>
                <h1>Live Streaming</h1>
                {!isLiveStreamServiceAvailable && (
                    <Box mb={2}>
                        <Alert severity='error'>Live stream service is not available. Live streaming features are disabled.</Alert>
                    </Box>
                )}
            </Grid>
            <Grid size={{ xs: 12 }}>
                <Stack
                    spacing={3}
                    sx={{
                        mb: 2,
                        pointerEvents: !isLiveStreamServiceAvailable ? 'none' : 'auto',
                        opacity: !isLiveStreamServiceAvailable ? 0.5 : 1,
                    }}
                >
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
                        label='Select Sensors'
                    />
                </Stack>
            </Grid>
            <Grid
                size={{ xs: 12 }}
                sx={{
                    pointerEvents: !isLiveStreamServiceAvailable ? 'none' : 'auto',
                    opacity: !isLiveStreamServiceAvailable ? 0.5 : 1,
                }}
            >
                <Box className='video-container'>
                    <Grid container spacing={2}>
                        {selectedSensors
                            ?.filter(sensor => sensor.streamId)
                            .map(sensor => (
                                <Grid size={{ xs: 12, md: 6 }} key={sensor.streamId}>
                                    <VSTStreamManager
                                        sensor={sensor}
                                        streamType={StreamType.Live}
                                        onClose={() => handleCloseVideo(sensor.streamId!)}
                                    />
                                </Grid>
                            ))}
                    </Grid>
                </Box>
            </Grid>
        </Grid>
    );
};

export default LiveStream;

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
import { Grid2 as Grid, Typography, Card, CardHeader, Box, Stack } from '@mui/material';
import MultipleSensorSelector from '../../components/sensorSelector/MultipleSensorSelector';
import { Sensor } from '../../interfaces/interfaces';
import useVSTUIStore from '../../services/StateManagement';
import VSTStreamManager from '../../features/streamManager/StreamManager';
import { StreamType } from 'vst-streaming-lib';

const MediaStreams = () => {
    const sensors = useVSTUIStore(state => state.sensorServiceSensors);

    // State for tag selector
    const [selectedTagList, setSelectedTagList] = useState<string[]>([]);
    const [uniqueTags, setUniqueTags] = useState<string[]>([]);

    // State for sensor/file selector
    const [selectedSensorList, setSelectedSensorList] = useState<Sensor[]>([]);
    const [sensorListForNvStreamer, setSensorListForNvStreamer] = useState<Sensor[]>([]);

    useEffect(() => {
        // Extract unique tags from all sensors
        const allTags = sensors
            .map(sensor => sensor.tags)
            .filter(tags => tags) // Filter out undefined/null tags
            .join(',') // Join all tags into a single string
            .split(',') // Split into array
            .map(tag => tag.trim()) // Trim whitespace
            .filter(tag => tag !== ''); // Remove empty tags
        const uniqueTagSet = new Set(allTags);
        setUniqueTags(Array.from(uniqueTagSet));
    }, [sensors]);

    // Filter sensors based on selected tags
    useEffect(() => {
        if (selectedTagList.length === 0) {
            // If no tags are selected, show all sensors with streamId
            setSensorListForNvStreamer(sensors.filter(sensor => sensor.streamId));
        } else {
            // Filter sensors that have at least one of the selected tags and have streamId
            const filteredSensors = sensors.filter(sensor => {
                if (!sensor.tags || !sensor.streamId) return false;
                const sensorTags = sensor.tags.split(',').map(tag => tag.trim());
                return selectedTagList.some(selectedTag => sensorTags.includes(selectedTag));
            });
            setSensorListForNvStreamer(filteredSensors);
        }
    }, [selectedTagList, sensors]);

    const handleSelectedTagChange = (selectedTags: string[] | undefined) => {
        setSelectedTagList(selectedTags || []);
    };

    const handleSelectedSensorListChange = (selectedSensors: Sensor[] | undefined) => {
        setSelectedSensorList(selectedSensors || []);
    };
    const handleCloseVideo = (sensorId: string) => {
        setSelectedSensorList(prev => prev?.filter(sensor => sensor.sensorId !== sensorId));
    };

    return (
        <Grid container spacing={3}>
            <Grid size={{ xs: 12 }}>
                <Typography variant='h4' gutterBottom>
                    Media Streams
                </Typography>
            </Grid>
            <Grid size={{ xs: 12 }}>
                <Card>
                    <CardHeader title={'Stream'} subheader={'Stream multiple files'} />
                    <Box sx={{ p: 3 }}>
                        <Stack spacing={3} sx={{ mb: 1 }}>
                            <MultipleSensorSelector
                                sensors={uniqueTags.map(tag => ({ name: tag, sensorId: tag }) as Sensor)}
                                selectedSensors={selectedTagList.map(tag => ({ name: tag, sensorId: tag }) as Sensor)}
                                onChange={selection => handleSelectedTagChange(selection?.map(s => s.name))}
                                multiple={true}
                                label='Select Tags'
                            />
                        </Stack>
                        <Stack spacing={3} sx={{ mb: 2 }}>
                            <MultipleSensorSelector
                                sensors={sensorListForNvStreamer}
                                selectedSensors={selectedSensorList}
                                onChange={handleSelectedSensorListChange}
                                multiple={true}
                            />
                        </Stack>
                    </Box>
                </Card>
            </Grid>
            <Grid size={{ xs: 12 }}>
                <Box className='video-container'>
                    <Grid container spacing={2}>
                        {selectedSensorList?.map(sensor => (
                            <Grid size={{ xs: 12, md: 6 }} key={sensor.sensorId}>
                                <VSTStreamManager
                                    sensor={sensor}
                                    streamType={StreamType.Live}
                                    onClose={() => handleCloseVideo(sensor.sensorId)}
                                />
                            </Grid>
                        ))}
                    </Grid>
                </Box>
            </Grid>
        </Grid>
    );
};

export default MediaStreams;

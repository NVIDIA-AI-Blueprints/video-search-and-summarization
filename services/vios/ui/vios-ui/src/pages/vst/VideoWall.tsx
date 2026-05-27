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
import React, { useCallback, useState } from 'react';
import { Box, Grid2 as Grid, Button, IconButton } from '@mui/material';
import VSTStreamManager from '../../features/streamManager/StreamManager';
import { Sensor } from '../../interfaces/interfaces';
import { StreamType } from 'vst-streaming-lib';
import { PlayArrow, Stop, ZoomIn, ZoomOut, Add } from '@mui/icons-material';
import { getAuthorizedMainStreams } from '../../utils/misc/sensorUtils';

const VideoWall = () => {
    const sensors = useVSTUIStore(state => state.sensorServiceSensors);
    const liveSensors = useVSTUIStore(state => state.liveServiceSensors);
    const isLiveStreamServiceAvailable = useVSTUIStore(state => state.isLiveStreamServiceAvailable);
    const [selectedSensors, setSelectedSensors] = useState<Sensor[] | undefined>();
    const [isStreaming, setIsStreaming] = useState(false);
    const [gridSize, setGridSize] = useState(10);

    // Get authorized main streams only (no sub streams)
    const authorizedSensors = getAuthorizedMainStreams(sensors, liveSensors);

    const handleSensorSelection = useCallback((selection: Sensor[] | undefined) => {
        setSelectedSensors(selection || []);
    }, []);

    const handleSelectAll = () => {
        const maxSensors = 15;
        const sensorsToSelect = authorizedSensors.slice(0, maxSensors);
        setSelectedSensors(sensorsToSelect);
    };

    const handleStartStream = () => {
        setIsStreaming(true);
    };

    const handleStopStream = () => {
        setIsStreaming(false);
    };

    const handleCloseVideo = () => {
        handleStopStream();
    };

    const handleZoomIn = () => {
        setGridSize(prev => Math.min(prev + 1, 12));
    };

    const handleZoomOut = () => {
        setGridSize(prev => Math.max(prev - 1, 6));
    };

    return (
        <Grid container spacing={2}>
            <Grid size={{ xs: 12 }}>
                <h1>Video Wall</h1>
            </Grid>
            <Grid size={{ xs: 12 }}>
                <div
                    style={{
                        pointerEvents: !isLiveStreamServiceAvailable ? 'none' : 'auto',
                        opacity: !isLiveStreamServiceAvailable ? 0.5 : 1,
                    }}
                >
                    <SensorSelector
                        multiple
                        sensors={authorizedSensors}
                        onChange={selection => {
                            handleSensorSelection(selection);
                        }}
                        selectedSensors={selectedSensors}
                    />
                </div>
            </Grid>
            <Grid size={{ xs: 12 }}>
                <Box
                    sx={{
                        mb: 2,
                        display: 'flex',
                        gap: 2,
                        alignItems: 'center',
                        pointerEvents: !isLiveStreamServiceAvailable ? 'none' : 'auto',
                        opacity: !isLiveStreamServiceAvailable ? 0.5 : 1,
                    }}
                >
                    <Button
                        variant='contained'
                        color='primary'
                        startIcon={<Add />}
                        onClick={handleSelectAll}
                        disabled={!authorizedSensors.length}
                    >
                        Select All
                    </Button>
                    <Button
                        variant='contained'
                        color='primary'
                        startIcon={<PlayArrow />}
                        onClick={handleStartStream}
                        disabled={!selectedSensors?.length || isStreaming}
                    >
                        Start Video Wall
                    </Button>
                    <Button variant='contained' color='error' startIcon={<Stop />} onClick={handleStopStream} disabled={!isStreaming}>
                        Stop Video Wall
                    </Button>
                    <Box sx={{ display: 'flex', alignItems: 'center', ml: 2 }}>
                        <IconButton onClick={handleZoomOut} disabled={!isStreaming || gridSize <= 6}>
                            <ZoomOut />
                        </IconButton>
                        <IconButton onClick={handleZoomIn} disabled={!isStreaming || gridSize >= 12}>
                            <ZoomIn />
                        </IconButton>
                    </Box>
                </Box>
            </Grid>
            <Grid
                size={{ xs: gridSize }}
                sx={{
                    pointerEvents: !isLiveStreamServiceAvailable ? 'none' : 'auto',
                    opacity: !isLiveStreamServiceAvailable ? 0.5 : 1,
                }}
            >
                <Box className='video-container'>
                    {isStreaming && selectedSensors && selectedSensors.length > 0 && (
                        <VSTStreamManager sensors={selectedSensors} streamType={StreamType.VideoWall} onClose={handleCloseVideo} />
                    )}
                </Box>
            </Grid>
        </Grid>
    );
};

export default VideoWall;

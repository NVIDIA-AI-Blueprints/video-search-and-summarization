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
import React, { useCallback, useEffect, useState } from 'react';

const formatDurationHHMMSS = (durationMs: number): string => {
    if (durationMs <= 0 || !Number.isFinite(durationMs)) return '00:00:00.000';
    const hours = Math.floor(durationMs / 3600000);
    const minutes = Math.floor((durationMs % 3600000) / 60000);
    const seconds = Math.floor((durationMs % 60000) / 1000);
    const ms = Math.floor(durationMs % 1000);
    return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}.${String(ms).padStart(3, '0')}`;
};

const formatTimestamp = (isoString: string): string => {
    const date = new Date(isoString);
    if (Number.isNaN(date.getTime())) return isoString;
    return date.toLocaleString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
    });
};
import {
    Card,
    CardContent,
    CardActions,
    Button,
    Grid2 as Grid,
    CardHeader,
    Typography,
    ToggleButtonGroup,
    ToggleButton,
    CircularProgress,
    Skeleton,
} from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import { Sensor, Timeline } from '../../../interfaces/interfaces';
import LOG from '../../../utils/misc/Logger';
import nvAxios from '../../../services/Axios';
import config from '../../../config';
import SingleSensorSelector from '../../../components/sensorSelector/SingleSensorSelector';
import { getTimelineGaps } from '../../../utils/misc/utils';
import { getReplaySensors } from '../../../utils/misc/sensorUtils';
import RangePickerDialog from '../../rangePickerDialog/RangePickerDialog';
import CalendarTodayIcon from '@mui/icons-material/CalendarToday';
import TimelineIcon from '@mui/icons-material/Timeline';
import TimeRangeSlider from '../../timeSlider/TimeSlider';
import { useSnackbar } from 'notistack';
import useVSTUIStore from '../../../services/StateManagement';
import { updateSensorsAndStreams } from '../../../utils/misc/updateSensorsAndStreams';

const SensorVideoDeleteCard: React.FC = () => {
    const { enqueueSnackbar } = useSnackbar();
    const [isDeleting, setIsDeleting] = useState<boolean>(false);
    const [isLoadingSensors, setIsLoadingSensors] = useState<boolean>(false);
    const storeSensors = useVSTUIStore(state => state.sensorServiceSensors);
    const removedSensors = useVSTUIStore(state => state.removedSensors);
    const replaySensors = useVSTUIStore(state => state.replayServiceSensors);
    const [sensors, setSensors] = useState<Sensor[]>([]);
    const [selectedSensor, setSelectedSensor] = useState<Sensor | null>(null);
    const [timelines, setTimelines] = useState<Record<string, Timeline[]>>({});
    const [sensorTimelines, setSensorTimelines] = useState<Timeline[]>([]);
    const [selectedStartTime, setSelectedStartTime] = useState<string>();
    const [selectedEndTime, setSelectedEndTime] = useState<string>();
    const [selectionMode, setSelectionMode] = useState<'timeline' | 'calendar'>('timeline');

    const [open, setOpen] = useState(false);
    const handleOpen = () => setOpen(true);
    const handleClose = () => setOpen(false);

    const [disabledIntervals, setDisabledIntervals] = useState<Timeline[] | undefined>([]);
    const handleSensorSelection = useCallback((selection: Sensor | null) => {
        setSelectedSensor(selection);
    }, []);

    const fetchTimelines = useCallback(async () => {
        setIsLoadingSensors(true);
        try {
            const response = await nvAxios.get(`${config.storageManagementEndpoint}/api/v1/storage/timelines`);
            if (response.data === null) {
                LOG.info('No videos found to delete');
                setTimelines({});
                setSensors([]);
                return;
            }
            const timelineData = response.data;
            setTimelines(timelineData);
            // Get appropriate sensors using replay functionality and add removed sensors
            const availableSensors = [...getReplaySensors(storeSensors, replaySensors), ...removedSensors];
            const sensorsWithTimelines = availableSensors.filter(
                sensor => timelineData[sensor.sensorId] && timelineData[sensor.sensorId].length > 0
            );
            setSensors(sensorsWithTimelines);
        } catch (error) {
            LOG.error('Failed to fetch timelines');
            enqueueSnackbar('Failed to fetch timelines', { variant: 'error' });
        } finally {
            setIsLoadingSensors(false);
        }
    }, [enqueueSnackbar, storeSensors, removedSensors, replaySensors]);

    useEffect(() => {
        fetchTimelines();
    }, [fetchTimelines]);

    useEffect(() => {
        if (selectedSensor && timelines[selectedSensor.sensorId]) {
            const sensorTimelines = timelines[selectedSensor.sensorId];
            setSensorTimelines(sensorTimelines);
            setDisabledIntervals(getTimelineGaps(sensorTimelines));
            setSelectedStartTime(sensorTimelines[0].startTime);
            setSelectedEndTime(sensorTimelines[sensorTimelines.length - 1].endTime);
        }
    }, [selectedSensor, timelines]);

    const handleModeChange = (_event: React.MouseEvent<HTMLElement>, newMode: 'timeline' | 'calendar' | null) => {
        if (newMode !== null) {
            setSelectedStartTime(sensorTimelines[0].startTime);
            setSelectedEndTime(sensorTimelines[sensorTimelines.length - 1].endTime);
            setSelectionMode(newMode);
        }
    };

    const handleDelete = async () => {
        if (!selectedStartTime || !selectedEndTime) {
            LOG.error(`Error - Please select valid interval`);
            return;
        }
        setIsDeleting(true);
        try {
            const streamId = selectedSensor?.streamId || selectedSensor?.sensorId;
            const response = await nvAxios.delete(
                `${config.storageManagementEndpoint}/api/v1/storage/file/${streamId}?startTime=${selectedStartTime}&endTime=${selectedEndTime}`,
                { headers: { streamId: streamId } }
            );

            if (response.data) {
                LOG.info(`Success - deleted ${response.data.spaceSaved} MB of video data for sensor ${selectedSensor?.name}`);
                enqueueSnackbar(`Deleted ${response.data.spaceSaved} MB of video data for sensor ${selectedSensor?.name}`, {
                    variant: 'success',
                });
            }

            // Update sensors and streams after successful deletion
            await updateSensorsAndStreams();
            await fetchTimelines();
            setSelectedSensor(null);
        } catch (error) {
            LOG.error(`Failed to Delete video for ${selectedSensor?.name}`);
            enqueueSnackbar(`Failed to Delete video for ${selectedSensor?.name}`, {
                variant: 'error',
            });
        } finally {
            setIsDeleting(false);
        }
    };

    const handleTimeRangeChange = (range: [string, string]) => {
        console.log('Selected range:', range);
        setSelectedStartTime(range[0]);
        setSelectedEndTime(range[1]);
    };

    const handleRangePickerSubmit = (startTime: string, endTime: string) => {
        LOG.verbose('Start Time:', startTime);
        LOG.verbose('End Time:', endTime);
        setSelectedStartTime(startTime);
        setSelectedEndTime(endTime);
    };

    if (isLoadingSensors) {
        return (
            <Card>
                <CardHeader title={<Skeleton width='40%' />} subheader={<Skeleton width='80%' />} />
                <CardContent>
                    <Grid container spacing={2}>
                        {/* Sensor selector skeleton */}
                        <Grid size={{ xs: 12 }}>
                            <Typography variant='body2' sx={{ mb: 1 }}>
                                <Skeleton width='30%' />
                            </Typography>
                            <Skeleton height={56} />
                        </Grid>
                        {/* Toggle buttons skeleton */}
                        <Grid size={{ xs: 12 }}>
                            <Skeleton width={120} height={48} />
                        </Grid>
                        {/* Time range info skeleton */}
                        <Grid size={{ xs: 12 }}>
                            <Skeleton width='60%' height={20} />
                        </Grid>
                        {/* Timeline slider skeleton */}
                        <Grid size={{ xs: 12 }}>
                            <Skeleton height={80} />
                        </Grid>
                    </Grid>
                </CardContent>
                <CardActions>
                    <Skeleton width={130} height={36} />
                </CardActions>
            </Card>
        );
    }

    return (
        <Card>
            <CardHeader
                title={'Delete videos'}
                subheader={`Delete recorded videos of a sensor. The dropdown shows sensor name followed by sensor ID. Note that removed sensors will also be shown in the dropdown.`}
            />
            <CardContent>
                <Grid container spacing={2}>
                    <Grid size={{ xs: 12 }}>
                        <SingleSensorSelector
                            sensors={sensors}
                            onChange={selection => {
                                handleSensorSelection(selection);
                            }}
                            selectedSensors={selectedSensor}
                            showSensorId={true}
                        />
                    </Grid>
                    <Grid size={{ xs: 12 }}>
                        <ToggleButtonGroup
                            value={selectionMode}
                            exclusive
                            onChange={handleModeChange}
                            aria-label='selection mode'
                            disabled={!selectedSensor || isLoadingSensors}
                        >
                            <ToggleButton value='timeline' aria-label='timeline'>
                                <TimelineIcon />
                            </ToggleButton>
                            <ToggleButton value='calendar' aria-label='calendar'>
                                <CalendarTodayIcon />
                            </ToggleButton>
                        </ToggleButtonGroup>
                    </Grid>
                    {selectedSensor && (
                        <Grid size={{ xs: 12 }}>
                            {selectedStartTime && selectedEndTime ? (
                                <>
                                    <Typography variant='body2' sx={{ color: 'text.secondary' }}>
                                        {`Range: ${formatTimestamp(selectedStartTime)} \u2192 ${formatTimestamp(selectedEndTime)}`}
                                    </Typography>
                                    <Typography variant='body2' sx={{ color: 'text.secondary', mt: 0.5 }}>
                                        {`Duration: ${formatDurationHHMMSS(Date.parse(selectedEndTime) - Date.parse(selectedStartTime))}`}
                                    </Typography>
                                </>
                            ) : (
                                <Typography variant='body2' sx={{ color: 'text.secondary' }}>
                                    No range selected
                                </Typography>
                            )}
                        </Grid>
                    )}

                    {selectionMode === 'timeline' && (
                        <Grid size={{ xs: 12 }}>
                            {selectedSensor && sensorTimelines.length > 0 && (
                                <TimeRangeSlider
                                    min={sensorTimelines[0].startTime}
                                    max={sensorTimelines[sensorTimelines.length - 1].endTime}
                                    onChange={handleTimeRangeChange}
                                    disabledRange={disabledIntervals}
                                />
                            )}
                        </Grid>
                    )}
                    {selectionMode === 'calendar' && (
                        <Grid size={{ xs: 12 }}>
                            {selectedSensor && (
                                <>
                                    <Button onClick={handleOpen}>Open Date Range Picker</Button>
                                    <RangePickerDialog open={open} onClose={handleClose} onSubmit={handleRangePickerSubmit} />
                                </>
                            )}
                        </Grid>
                    )}
                </Grid>
            </CardContent>
            <CardActions>
                <Button
                    variant='contained'
                    color='error'
                    startIcon={isDeleting ? <CircularProgress size={20} color='inherit' /> : <DeleteIcon />}
                    onClick={handleDelete}
                    disabled={isDeleting || !selectedSensor || !selectedEndTime || !selectedStartTime || isLoadingSensors}
                >
                    {isDeleting ? 'Deleting...' : 'Delete Video'}
                </Button>
            </CardActions>
        </Card>
    );
};

export default SensorVideoDeleteCard;

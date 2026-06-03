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
import React, { useCallback, useEffect, useMemo, useState } from 'react';

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
    FormControlLabel,
    Checkbox,
    Collapse,
    TextField,
    Switch,
    Box,
    Slider,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import { Sensor, Timeline } from '../../../interfaces/interfaces';
import LOG from '../../../utils/misc/Logger';
import nvAxios from '../../../services/Axios';
import config from '../../../config';
import SingleSensorSelector from '../../../components/sensorSelector/SingleSensorSelector';
import { getTimelineGaps, copyToClipboard } from '../../../utils/misc/utils';
import { getReplaySensors } from '../../../utils/misc/sensorUtils';
import RangePickerDialog from '../../rangePickerDialog/RangePickerDialog';
import CalendarTodayIcon from '@mui/icons-material/CalendarToday';
import TimelineIcon from '@mui/icons-material/Timeline';
import TimeRangeSlider from '../../timeSlider/TimeSlider';
import { useSnackbar } from 'notistack';
import useVSTUIStore from '../../../services/StateManagement';
import AnalyticsOverlayDialog from '../../../components/videoPlayer/videoPlayerUtils/analytics/AnalyticsOverlayDialog';
import AnalyticsOverlayConfigurationSummary from '../../../components/videoPlayer/videoPlayerUtils/analytics/AnalyticsOverlayConfigurationSummary';
import { StreamOverlayOptions, StreamCompositeOptions, StreamType } from 'vst-streaming-lib';

interface VideoConfig {
    disableAudio: boolean;
    overlay: StreamOverlayOptions;
}

const SensorVideoDownloadCard: React.FC = () => {
    const { enqueueSnackbar } = useSnackbar();
    const [isDownloading, setIsDownloading] = useState<boolean>(false);
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
    const [useConfiguration, setUseConfiguration] = useState<boolean>(false);
    const [useMillisecondsInput, setUseMillisecondsInput] = useState<boolean>(false);
    const [startTimeMsInput, setStartTimeMsInput] = useState<string>('');
    const [endTimeMsInput, setEndTimeMsInput] = useState<string>('');
    const [msSliderRange, setMsSliderRange] = useState<[number, number]>([0, 0]);
    const [sliderStep, setSliderStep] = useState<number>(1000);
    const [videoConfig, setVideoConfig] = useState<VideoConfig>({
        disableAudio: false,
        overlay: {
            bbox: {
                showAll: true,
                objectId: [],
                classType: [],
                showObjId: false,
                objIdPosition: 0,
                objIdTextColor: 'white',
                objIdTextBGColor: 'black',
            },
            tripwire: { showAll: false, id: [] },
            roi: { showAll: false, id: [] },
            debug: true,
            opacity: 255,
            color: 'red',
            thickness: 6,
            proximityClass: [],
            entrantClass: [],
            proximityAreaFactor: 1.3,
            proximityAnimation: '',
            overlayColorCode: [],
            needHalo: false,
        },
    });
    const [container, setContainer] = useState<string>('');

    const [open, setOpen] = useState(false);
    const handleOpen = () => setOpen(true);
    const handleClose = () => setOpen(false);

    // Analytics overlay dialog state
    const [openAnalyticsDialog, setOpenAnalyticsDialog] = useState(false);
    const handleAnalyticsDialogOpen = () => setOpenAnalyticsDialog(true);
    const handleAnalyticsDialogClose = () => setOpenAnalyticsDialog(false);

    const handleAnalyticsOverlaySave = (settings: { overlay: StreamOverlayOptions; composite?: StreamCompositeOptions }) => {
        setVideoConfig(prev => ({
            ...prev,
            overlay: settings.overlay,
        }));
        setOpenAnalyticsDialog(false);
    };

    const [disabledIntervals, setDisabledIntervals] = useState<Timeline[] | undefined>([]);
    const handleSensorSelection = useCallback((selection: Sensor | null) => {
        setSelectedSensor(selection);
    }, []);

    const fetchTimelines = useCallback(async () => {
        setIsLoadingSensors(true);
        try {
            const response = await nvAxios.get(`${config.storageManagementEndpoint}/api/v1/storage/timelines`);
            if (response.data === null) {
                LOG.info('No videos found to download');
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
            setUseMillisecondsInput(false);
            setStartTimeMsInput('');
            setEndTimeMsInput('');
            // Set slider range starting from 0 with duration from first segment
            const firstSegmentStart = Date.parse(sensorTimelines[0].startTime);
            const firstSegmentEnd = Date.parse(sensorTimelines[0].endTime);
            const duration = Number.isNaN(firstSegmentStart) || Number.isNaN(firstSegmentEnd) ? 0 : firstSegmentEnd - firstSegmentStart;
            setMsSliderRange([0, duration]);
        }
    }, [selectedSensor, timelines]);

    const handleModeChange = (_event: React.MouseEvent<HTMLElement>, newMode: 'timeline' | 'calendar' | null) => {
        if (newMode !== null) {
            setSelectedStartTime(sensorTimelines[0].startTime);
            setSelectedEndTime(sensorTimelines[sensorTimelines.length - 1].endTime);
            setSelectionMode(newMode);
            setUseMillisecondsInput(false);
            setStartTimeMsInput('');
            setEndTimeMsInput('');
        }
    };

    const buildDownloadUrl = useCallback((): string => {
        const streamId = selectedSensor?.streamId || selectedSensor?.sensorId;
        let url = `${config.storageManagementEndpoint}/api/v1/storage/file/${streamId}?startTime=${selectedStartTime}&endTime=${selectedEndTime}`;

        if (useConfiguration) {
            const configParam = encodeURIComponent(JSON.stringify(videoConfig));
            url += `&configuration=${configParam}`;
        }

        if (container.trim()) {
            url += `&container=${encodeURIComponent(container.trim())}`;
        }

        let proxy = window.location.pathname;
        if (proxy !== '/' && proxy.length > 0) {
            if (proxy[proxy.length - 1] === '/') {
                proxy = proxy.slice(0, -1);
            }
            const emdxEndpoint = useVSTUIStore.getState().emdxEndpoint;
            if (!emdxEndpoint || (emdxEndpoint && !url.includes(emdxEndpoint))) {
                url = url.replace('/api', `${proxy}/api`);
            }
        }

        return url;
    }, [selectedSensor, selectedStartTime, selectedEndTime, useConfiguration, videoConfig, container]);

    const handleDownload = async () => {
        if (!selectedStartTime || !selectedEndTime) {
            LOG.error(`Error - Please select valid interval`);
            return;
        }
        setIsDownloading(true);
        try {
            const url = buildDownloadUrl();
            window.open(url, '_blank');

            LOG.info(`Success - initiated download for sensor ${selectedSensor?.name}`);
            enqueueSnackbar(`Download started for ${selectedSensor?.name}`, {
                variant: 'success',
            });

            fetchTimelines();
            setSelectedSensor(null);
            setUseConfiguration(false);
        } catch (error) {
            LOG.error(`Failed to Download video for ${selectedSensor?.name}`);
            enqueueSnackbar(`Failed to Download video for ${selectedSensor?.name}`, {
                variant: 'error',
            });
        } finally {
            setIsDownloading(false);
        }
    };

    const handleCopyUrl = useCallback(async () => {
        if (!selectedStartTime || !selectedEndTime) {
            enqueueSnackbar('Please select a valid time range first', { variant: 'warning' });
            return;
        }
        try {
            const url = buildDownloadUrl();
            const fullUrl = /^https?:\/\//.test(url) ? url : `${window.location.origin}${url}`;
            await copyToClipboard(fullUrl);
            enqueueSnackbar('Download URL copied to clipboard', { variant: 'success' });
        } catch (error) {
            LOG.error('Failed to copy download URL');
            enqueueSnackbar('Failed to copy URL to clipboard', { variant: 'error' });
        }
    }, [buildDownloadUrl, selectedStartTime, selectedEndTime, enqueueSnackbar]);

    const handleCopyConfigToClipboard = async () => {
        if (!useConfiguration) {
            enqueueSnackbar('Configuration is not enabled. Enable configuration first to copy settings.', {
                variant: 'warning',
            });
            return;
        }
        try {
            const jsonString = JSON.stringify(videoConfig, null, 2);
            await copyToClipboard(jsonString);
            enqueueSnackbar('Download configuration copied to clipboard!', { variant: 'success' });
        } catch (error) {
            LOG.error('Failed to copy configuration to clipboard');
            enqueueSnackbar('Failed to copy configuration to clipboard', { variant: 'error' });
        }
    };

    const handleTimeRangeChange = (range: [string, string]) => {
        console.log('Selected range:', range);
        setSelectedStartTime(range[0]);
        setSelectedEndTime(range[1]);
        setUseMillisecondsInput(false);
        setStartTimeMsInput('');
        setEndTimeMsInput('');
    };

    // Timeline range for slider (starts at 0, duration from first segment)
    const timelineRange = useMemo(() => {
        if (!sensorTimelines.length) {
            return { startMs: 0, endMs: 0 };
        }
        const firstStart = Date.parse(sensorTimelines[0].startTime);
        const firstEnd = Date.parse(sensorTimelines[0].endTime);
        const duration = Number.isNaN(firstStart) || Number.isNaN(firstEnd) ? 0 : firstEnd - firstStart;
        return {
            startMs: 0,
            endMs: duration,
        };
    }, [sensorTimelines]);

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
                    <Skeleton width={140} height={36} />
                </CardActions>
            </Card>
        );
    }

    return (
        <Card>
            <CardHeader
                title={'Download videos'}
                subheader={`Download recorded videos of a sensor. The dropdown shows sensor name followed by sensor ID. Note that removed sensors will also be shown in the dropdown.`}
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

                    <Grid size={{ xs: 12 }}>
                        <FormControlLabel
                            control={
                                <Switch
                                    checked={useMillisecondsInput}
                                    onChange={event => {
                                        const enabled = event.target.checked;
                                        setUseMillisecondsInput(enabled);
                                        if (enabled) {
                                            // Initialize with 0 to duration range
                                            const startMs = 0;
                                            const endMs = timelineRange.endMs;
                                            setStartTimeMsInput(`${startMs}`);
                                            setEndTimeMsInput(`${endMs}`);
                                            setSelectedStartTime(`${startMs}`);
                                            setSelectedEndTime(`${endMs}`);
                                            setMsSliderRange([startMs, endMs]);
                                        } else {
                                            setStartTimeMsInput('');
                                            setEndTimeMsInput('');
                                            setSelectedStartTime(undefined);
                                            setSelectedEndTime(undefined);
                                        }
                                    }}
                                    disabled={!selectedSensor || isLoadingSensors}
                                />
                            }
                            label='Enter times in milliseconds'
                        />
                        {useMillisecondsInput && (
                            <Grid container spacing={2} sx={{ mt: 1 }}>
                                <Grid size={{ xs: 12, md: 6 }}>
                                    <TextField
                                        label='Start time (ms)'
                                        type='number'
                                        value={startTimeMsInput}
                                        onChange={e => {
                                            const value = e.target.value;
                                            setStartTimeMsInput(value);
                                            if (value.trim()) {
                                                const numValue = Number(value);
                                                if (!Number.isNaN(numValue) && numValue >= 0) {
                                                    setSelectedStartTime(value);
                                                    setMsSliderRange(prev => [numValue, prev[1]]);
                                                }
                                            } else {
                                                setSelectedStartTime(undefined);
                                            }
                                        }}
                                        fullWidth
                                        disabled={!selectedSensor || isLoadingSensors}
                                        helperText='Provide start timestamp in milliseconds (≥ 0)'
                                        inputProps={{ min: 0 }}
                                    />
                                </Grid>
                                <Grid size={{ xs: 12, md: 6 }}>
                                    <TextField
                                        label='End time (ms)'
                                        type='number'
                                        value={endTimeMsInput}
                                        onChange={e => {
                                            const value = e.target.value;
                                            setEndTimeMsInput(value);
                                            if (value.trim()) {
                                                const numValue = Number(value);
                                                if (!Number.isNaN(numValue)) {
                                                    setSelectedEndTime(value);
                                                    setMsSliderRange(prev => [prev[0], numValue]);
                                                }
                                            } else {
                                                setSelectedEndTime(undefined);
                                            }
                                        }}
                                        fullWidth
                                        disabled={!selectedSensor || isLoadingSensors}
                                        helperText='Provide end timestamp in milliseconds'
                                    />
                                </Grid>
                                <Grid size={{ xs: 12, md: 6 }}>
                                    <TextField
                                        label='Slider Step (ms)'
                                        type='number'
                                        value={sliderStep}
                                        onChange={e => {
                                            const value = Number(e.target.value);
                                            if (!Number.isNaN(value) && value > 0) {
                                                setSliderStep(value);
                                            }
                                        }}
                                        fullWidth
                                        disabled={!selectedSensor || isLoadingSensors}
                                        helperText='Step size for slider increments'
                                        inputProps={{ min: 1 }}
                                    />
                                </Grid>
                            </Grid>
                        )}
                    </Grid>

                    {/* Configuration Section */}
                    <Grid size={{ xs: 12 }}>
                        <FormControlLabel
                            control={
                                <Checkbox
                                    checked={useConfiguration}
                                    onChange={e => setUseConfiguration(e.target.checked)}
                                    disabled={!selectedSensor || isLoadingSensors}
                                />
                            }
                            label='Configuration'
                        />

                        <Collapse in={useConfiguration}>
                            <Box
                                sx={{
                                    p: 3,
                                    border: '1px solid',
                                    borderColor: 'divider',
                                    borderRadius: 2,
                                    mt: 2,
                                    bgcolor: theme => (theme.palette.mode === 'dark' ? theme.palette.grey[900] : theme.palette.grey[50]),
                                }}
                            >
                                <Typography variant='h6' gutterBottom sx={{ mb: 2 }}>
                                    Video Configuration
                                </Typography>

                                <Grid container spacing={3}>
                                    {/* Audio Section */}
                                    <Grid size={{ xs: 12 }}>
                                        <Box
                                            sx={{
                                                p: 2,
                                                bgcolor: theme => theme.palette.background.default,
                                                borderRadius: 1,
                                                border: '1px solid',
                                                borderColor: theme => theme.palette.divider,
                                            }}
                                        >
                                            <Typography variant='subtitle1' sx={{ mb: 1, fontWeight: 'medium' }}>
                                                Audio Settings
                                            </Typography>
                                            <FormControlLabel
                                                control={
                                                    <Switch
                                                        checked={!videoConfig.disableAudio}
                                                        onChange={e =>
                                                            setVideoConfig(prev => ({
                                                                ...prev,
                                                                disableAudio: !e.target.checked,
                                                            }))
                                                        }
                                                    />
                                                }
                                                label='Enable Audio'
                                            />
                                        </Box>
                                    </Grid>

                                    {/* Container Section */}
                                    <Grid size={{ xs: 12 }}>
                                        <Box
                                            sx={{
                                                p: 2,
                                                bgcolor: theme => theme.palette.background.default,
                                                borderRadius: 1,
                                                border: '1px solid',
                                                borderColor: theme => theme.palette.divider,
                                            }}
                                        >
                                            <Typography variant='subtitle1' sx={{ mb: 2, fontWeight: 'medium' }}>
                                                Video Format
                                            </Typography>
                                            <TextField
                                                label='Container Format'
                                                value={container}
                                                onChange={e => setContainer(e.target.value)}
                                                size='small'
                                                fullWidth
                                                placeholder='mp4, ts, etc'
                                                helperText='Leave empty for default format'
                                            />
                                        </Box>
                                    </Grid>

                                    {/* Analytics Overlay Section */}
                                    <Grid size={{ xs: 12 }}>
                                        <Box
                                            sx={{
                                                p: 2,
                                                bgcolor: theme => theme.palette.background.default,
                                                borderRadius: 1,
                                                border: '1px solid',
                                                borderColor: theme => theme.palette.divider,
                                            }}
                                        >
                                            <Typography variant='subtitle1' sx={{ mb: 2, fontWeight: 'medium' }}>
                                                Analytics Overlay
                                            </Typography>
                                            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                                                <Typography variant='body2' color='text.secondary'>
                                                    Configure advanced analytics overlay settings including bounding boxes, object IDs,
                                                    colors, positions, and more.
                                                </Typography>
                                                <Button
                                                    variant='outlined'
                                                    onClick={handleAnalyticsDialogOpen}
                                                    sx={{ alignSelf: 'flex-start' }}
                                                >
                                                    Configure Analytics Overlay
                                                </Button>
                                                <AnalyticsOverlayConfigurationSummary
                                                    overlaySettings={videoConfig.overlay}
                                                    compact={true}
                                                    title='Current Analytics Overlay Settings'
                                                />
                                            </Box>
                                        </Box>
                                    </Grid>
                                </Grid>
                            </Box>
                        </Collapse>
                    </Grid>

                    {selectedSensor && (
                        <Grid size={{ xs: 12 }}>
                            {selectedStartTime && selectedEndTime ? (
                                <>
                                    <Typography variant='body2' sx={{ color: 'text.secondary' }}>
                                        {useMillisecondsInput
                                            ? `Range: ${selectedStartTime} ms \u2192 ${selectedEndTime} ms`
                                            : `Range: ${formatTimestamp(selectedStartTime)} \u2192 ${formatTimestamp(selectedEndTime)}`}
                                    </Typography>
                                    <Typography variant='body2' sx={{ color: 'text.secondary', mt: 0.5 }}>
                                        {`Duration: ${formatDurationHHMMSS(
                                            useMillisecondsInput
                                                ? Number(selectedEndTime) - Number(selectedStartTime)
                                                : Date.parse(selectedEndTime) - Date.parse(selectedStartTime)
                                        )}`}
                                    </Typography>
                                </>
                            ) : (
                                <Typography variant='body2' sx={{ color: 'text.secondary' }}>
                                    No range selected
                                </Typography>
                            )}
                        </Grid>
                    )}

                    {selectionMode === 'timeline' && !useMillisecondsInput && (
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
                    {useMillisecondsInput && selectedSensor && sensorTimelines.length > 0 && (
                        <Grid size={{ xs: 12 }}>
                            <Box sx={{ px: 2, py: 3 }}>
                                <Typography variant='subtitle2' sx={{ mb: 2 }}>
                                    Select Time Range (Milliseconds)
                                </Typography>
                                <Slider
                                    value={msSliderRange}
                                    onChange={(_, newValue) => {
                                        if (Array.isArray(newValue)) {
                                            const [start, end] = newValue as number[];
                                            setMsSliderRange([start, end]);
                                            setStartTimeMsInput(`${start}`);
                                            setEndTimeMsInput(`${end}`);
                                            setSelectedStartTime(`${start}`);
                                            setSelectedEndTime(`${end}`);
                                        }
                                    }}
                                    valueLabelDisplay='on'
                                    min={timelineRange.startMs}
                                    max={timelineRange.endMs}
                                    step={sliderStep}
                                    disableSwap
                                    disabled={isLoadingSensors || timelineRange.startMs === timelineRange.endMs}
                                    sx={{ mt: 2 }}
                                />
                                <Typography variant='caption' sx={{ color: 'text.secondary', mt: 1, display: 'block' }}>
                                    Duration: {timelineRange.endMs} ms (0 - {timelineRange.endMs})
                                </Typography>
                            </Box>
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
            <CardActions sx={{ justifyContent: 'space-between' }}>
                <Button
                    variant='outlined'
                    color='primary'
                    startIcon={<ContentCopyIcon />}
                    onClick={handleCopyConfigToClipboard}
                    disabled={!useConfiguration}
                    size='small'
                >
                    Copy Config JSON
                </Button>
                <Button
                    variant='contained'
                    color='primary'
                    startIcon={isDownloading ? <CircularProgress size={20} color='inherit' /> : <DownloadIcon />}
                    onClick={handleDownload}
                    disabled={isDownloading || !selectedSensor || !selectedEndTime || !selectedStartTime || isLoadingSensors}
                >
                    {isDownloading ? 'Downloading...' : 'Download Video'}
                </Button>
                <Button
                    variant='outlined'
                    startIcon={<ContentCopyIcon />}
                    onClick={handleCopyUrl}
                    disabled={!selectedSensor || !selectedEndTime || !selectedStartTime || isLoadingSensors}
                >
                    Copy Download URL
                </Button>
            </CardActions>

            {/* Analytics Overlay Dialog */}
            <AnalyticsOverlayDialog
                open={openAnalyticsDialog}
                onClose={handleAnalyticsDialogClose}
                onSave={handleAnalyticsOverlaySave}
                streamType={StreamType.Replay}
            />
        </Card>
    );
};

export default SensorVideoDownloadCard;

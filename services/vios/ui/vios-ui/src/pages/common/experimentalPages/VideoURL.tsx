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
import React, { useCallback, useMemo, useState } from 'react';
import {
    Box,
    Card,
    CardContent,
    CardHeader,
    Grid2 as Grid,
    TextField,
    Button,
    Typography,
    Alert,
    Paper,
    Stack,
    Divider,
    Chip,
    Link,
    useTheme,
    alpha,
    FormControlLabel,
    Checkbox,
    Collapse,
    Switch,
} from '@mui/material';
import { useSnackbar } from 'notistack';
import {
    PlayArrow as PlayArrowIcon,
    ContentCopy as ContentCopyIcon,
    Schedule as ScheduleIcon,
    Image as ImageIcon,
    VideoLibrary as VideoLibraryIcon,
    OpenInNew as OpenInNewIcon,
    Settings as SettingsIcon,
} from '@mui/icons-material';
import SingleSensorSelector from '../../../components/sensorSelector/SingleSensorSelector';
import useVSTUIStore from '../../../services/StateManagement';
import { Sensor } from '../../../interfaces/interfaces';
import config from '../../../config';
import nvAxios from '../../../services/Axios';
import { getReplaySensors, getLiveSensors } from '../../../utils/misc/sensorUtils';
import { copyToClipboard } from '../../../utils/misc/utils';
import AnalyticsOverlayDialog from '../../../components/videoPlayer/videoPlayerUtils/analytics/AnalyticsOverlayDialog';
import AnalyticsOverlayConfigurationSummary from '../../../components/videoPlayer/videoPlayerUtils/analytics/AnalyticsOverlayConfigurationSummary';
import { StreamOverlayOptions, StreamCompositeOptions, StreamType } from 'vst-streaming-lib';

interface VideoUrlResponse {
    absolutePath: string;
    videoUrl: string;
    expiryISO: string;
    expiryMinutes: number;
    streamId: string;
    type: 'replay';
}

interface PictureUrlResponse {
    absolutePath: string;
    imageUrl: string;
    expiryISO: string;
    expiryMinutes: number;
    streamId: string;
    type: 'live' | 'replay';
}

interface VideoConfig {
    disableAudio: boolean;
    overlay: StreamOverlayOptions;
}

const isValidISO = (value: string): boolean => {
    if (!value) return false;
    const d = new Date(value);
    return !isNaN(d.getTime());
};

const getDefaultInterval = (): { start: string; end: string } => {
    const end = new Date();
    const start = new Date(end.getTime() - 60 * 1000);
    return { start: start.toISOString(), end: end.toISOString() };
};

const VideoURL: React.FC = () => {
    const theme = useTheme();
    const sensors = useVSTUIStore(state => state.sensorServiceSensors);
    const replaySensors = useVSTUIStore(state => state.replayServiceSensors);
    const liveSensors = useVSTUIStore(state => state.liveServiceSensors);

    const availableSensors = useMemo(() => {
        const replay = getReplaySensors(sensors, replaySensors);
        const live = getLiveSensors(sensors, liveSensors);

        const sensorMap = new Map<string, Sensor>();
        [...replay, ...live].forEach(sensor => {
            const key = sensor.streamId || sensor.sensorId;
            if (key && !sensorMap.has(key)) {
                sensorMap.set(key, sensor);
            }
        });

        return Array.from(sensorMap.values());
    }, [sensors, replaySensors, liveSensors]);

    const authorizedSensors = useMemo(() => availableSensors.filter(s => s.isAuthorized), [availableSensors]);
    const { enqueueSnackbar } = useSnackbar();

    const defaults = useMemo(() => getDefaultInterval(), []);

    const [sensor, setSensor] = useState<Sensor | null>(null);
    const [startTime, setStartTime] = useState<string>(defaults.start);
    const [endTime, setEndTime] = useState<string>(defaults.end);
    const [expiryMinutes, setExpiryMinutes] = useState<string>('');
    const [container, setContainer] = useState<string>('mp4');

    const [useVideoConfiguration, setUseVideoConfiguration] = useState<boolean>(false);
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
            pose: false,
        },
    });

    const [loading, setLoading] = useState<boolean>(false);
    const [result, setResult] = useState<VideoUrlResponse | null>(null);
    const [errorMsg, setErrorMsg] = useState<string>('');

    // Picture URL states
    const [pictureExpiryMinutes, setPictureExpiryMinutes] = useState<string>('');
    const [pictureResultLive, setPictureResultLive] = useState<PictureUrlResponse | null>(null);
    const [pictureResultReplay, setPictureResultReplay] = useState<PictureUrlResponse | null>(null);
    const [pictureLoading, setPictureLoading] = useState<boolean>(false);
    const [pictureError, setPictureError] = useState<string>('');

    const [usePictureOverlay, setUsePictureOverlay] = useState<boolean>(false);
    const [pictureOverlay, setPictureOverlay] = useState<StreamOverlayOptions>({
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
        debug: true,
        opacity: 255,
        color: 'green',
        thickness: 5,
        proximityClass: [],
        entrantClass: [],
        proximityAreaFactor: 1.3,
        proximityAnimation: '',
        overlayColorCode: [],
        needHalo: false,
        pose: false,
    });

    // Dialog state management
    const [openVideoAnalyticsDialog, setOpenVideoAnalyticsDialog] = useState(false);
    const [openPictureAnalyticsDialog, setOpenPictureAnalyticsDialog] = useState(false);

    const handleVideoAnalyticsDialogOpen = () => setOpenVideoAnalyticsDialog(true);
    const handleVideoAnalyticsDialogClose = () => setOpenVideoAnalyticsDialog(false);
    const handlePictureAnalyticsDialogOpen = () => setOpenPictureAnalyticsDialog(true);
    const handlePictureAnalyticsDialogClose = () => setOpenPictureAnalyticsDialog(false);

    const handleVideoAnalyticsOverlaySave = (settings: { overlay: StreamOverlayOptions; composite?: StreamCompositeOptions }) => {
        setVideoConfig(prev => ({ ...prev, overlay: settings.overlay }));
        setOpenVideoAnalyticsDialog(false);
    };

    const handlePictureAnalyticsOverlaySave = (settings: { overlay: StreamOverlayOptions; composite?: StreamCompositeOptions }) => {
        setPictureOverlay(settings.overlay);
        setOpenPictureAnalyticsDialog(false);
    };

    const streamIdForSensor = useCallback((s: Sensor | null): string | null => {
        if (!s) return null;
        return s.streamId ?? s.sensorId ?? null;
    }, []);

    const handleGenerate = useCallback(async () => {
        setErrorMsg('');
        setResult(null);

        const streamId = streamIdForSensor(sensor);
        if (!streamId) {
            setErrorMsg('Please select a sensor.');
            return;
        }
        if (!isValidISO(startTime) || !isValidISO(endTime)) {
            setErrorMsg('Invalid start or end time. Use ISO 8601, e.g., 2025-08-25T03:05:55.752Z');
            return;
        }
        try {
            setLoading(true);
            const params: Record<string, string | number> = {
                startTime,
                endTime,
            };
            if (expiryMinutes && Number(expiryMinutes) > 0) {
                params.expiryMinutes = Number(expiryMinutes);
            }
            if (container.trim()) {
                params.container = container.trim();
            }
            if (useVideoConfiguration) {
                const downloadConfig = {
                    disableAudio: videoConfig.disableAudio,
                    overlay: videoConfig.overlay,
                };
                params.configuration = JSON.stringify(downloadConfig);
            }

            const url = `${config.storageManagementEndpoint}/api/v1/storage/file/${encodeURIComponent(streamId)}/url`;
            const response = await nvAxios.get<VideoUrlResponse>(url, { params, headers: { streamId } });
            setResult(response.data);
            enqueueSnackbar('Generated temporary video URL', { variant: 'success' });
        } catch (err) {
            setErrorMsg('Failed to generate video URL');
            enqueueSnackbar('Error - Failed to generate video URL', { variant: 'error' });
        } finally {
            setLoading(false);
        }
    }, [sensor, startTime, endTime, expiryMinutes, container, useVideoConfiguration, videoConfig, enqueueSnackbar, streamIdForSensor]);

    const handleGeneratePictureUrl = useCallback(
        async (type: 'live' | 'replay') => {
            setPictureError('');
            setPictureResultLive(null);
            setPictureResultReplay(null);

            const streamId = streamIdForSensor(sensor);
            if (!streamId) {
                setPictureError('Please select a sensor.');
                return;
            }

            if (type === 'replay' && !isValidISO(startTime)) {
                setPictureError('Invalid start time for replay. Use ISO 8601, e.g., 2025-08-25T03:05:55.752Z');
                return;
            }

            try {
                setPictureLoading(true);
                const params: Record<string, string | number> = {};
                if (type === 'replay') {
                    params.startTime = startTime;
                }
                if (pictureExpiryMinutes && Number(pictureExpiryMinutes) > 0) {
                    params.expiryMinutes = Number(pictureExpiryMinutes);
                }
                if (usePictureOverlay) {
                    params.overlay = JSON.stringify(pictureOverlay);
                }

                const base = type === 'live' ? config.liveStreamEndpoint : config.replayStreamEndpoint;
                const endpoint = `${base}/api/v1/${type}/stream/${encodeURIComponent(streamId)}/picture/url`;

                const response = await nvAxios.get<PictureUrlResponse>(endpoint, { params, headers: { streamId } });
                if (type === 'live') setPictureResultLive(response.data);
                else setPictureResultReplay(response.data);
                enqueueSnackbar(`Generated ${type} image URL`, { variant: 'success' });
            } catch (err) {
                setPictureError('Failed to generate image URL');
                enqueueSnackbar('Error - Failed to generate image URL', { variant: 'error' });
            } finally {
                setPictureLoading(false);
            }
        },
        [sensor, startTime, pictureExpiryMinutes, usePictureOverlay, pictureOverlay, enqueueSnackbar, streamIdForSensor]
    );

    const handleCopyImageUrl = useCallback(
        async (url?: string) => {
            if (!url) return;
            try {
                await copyToClipboard(url);
                enqueueSnackbar('Copied URL to clipboard', { variant: 'success' });
            } catch {
                enqueueSnackbar('Failed to copy URL', { variant: 'error' });
            }
        },
        [enqueueSnackbar]
    );

    const handleNow = useCallback(() => {
        const { start, end } = getDefaultInterval();
        setStartTime(start);
        setEndTime(end);
    }, []);

    const handleCopy = useCallback(async () => {
        if (!result?.videoUrl) return;
        try {
            await copyToClipboard(result.videoUrl);
            enqueueSnackbar('Copied URL to clipboard', { variant: 'success' });
        } catch {
            enqueueSnackbar('Failed to copy URL', { variant: 'error' });
        }
    }, [result, enqueueSnackbar]);

    return (
        <Box sx={{ maxWidth: 1200, mx: 'auto', p: theme.spacing(3) }}>
            <Paper
                elevation={2}
                sx={{
                    p: theme.spacing(4),
                    bgcolor: theme.palette.background.paper,
                    borderRadius: theme.spacing(2),
                }}
            >
                <Box sx={{ mb: theme.spacing(4) }}>
                    <Typography
                        variant='h4'
                        sx={{
                            color: theme.palette.text.primary,
                            fontWeight: theme.typography.fontWeightBold,
                            mb: theme.spacing(1),
                        }}
                    >
                        Media URL Generator
                    </Typography>
                    <Typography
                        variant='body1'
                        sx={{
                            color: theme.palette.text.secondary,
                            mb: theme.spacing(3),
                        }}
                    >
                        Generate temporary URLs for accessing video and image content from your sensors
                    </Typography>
                    <Divider />
                </Box>

                {/* Sensor Selection */}
                <Card
                    sx={{
                        mb: theme.spacing(4),
                        bgcolor: alpha(theme.palette.primary.main, 0.02),
                        border: `1px solid ${alpha(theme.palette.primary.main, 0.1)}`,
                    }}
                >
                    <CardHeader
                        title='Sensor Selection'
                        sx={{
                            '& .MuiCardHeader-title': {
                                color: theme.palette.text.primary,
                                fontWeight: 600,
                            },
                        }}
                    />
                    <CardContent>
                        <Grid container spacing={theme.spacing(3)} alignItems='center'>
                            <Grid size={{ xs: 12, md: 8 }}>
                                <SingleSensorSelector sensors={authorizedSensors} onChange={setSensor} selectedSensors={sensor} />
                            </Grid>
                            <Grid size={{ xs: 12, md: 4 }}>
                                <Button
                                    variant='outlined'
                                    onClick={handleNow}
                                    startIcon={<ScheduleIcon />}
                                    fullWidth
                                    sx={{ py: theme.spacing(1.5) }}
                                >
                                    Set Current Time - 60s
                                </Button>
                            </Grid>
                        </Grid>
                    </CardContent>
                </Card>

                {/* Video URL Section */}
                <Card sx={{ mb: theme.spacing(4) }}>
                    <CardHeader
                        title={
                            <Box display='flex' alignItems='center' gap={1}>
                                <VideoLibraryIcon color='primary' />
                                Video URL (Storage)
                            </Box>
                        }
                        sx={{
                            '& .MuiCardHeader-title': {
                                color: theme.palette.text.primary,
                                fontWeight: 600,
                            },
                        }}
                    />
                    <CardContent>
                        <Grid container spacing={theme.spacing(3)}>
                            <Grid size={{ xs: 12, md: 6 }}>
                                <TextField
                                    fullWidth
                                    label='Start Time (ISO 8601)'
                                    value={startTime}
                                    onChange={e => setStartTime(e.target.value)}
                                    placeholder='2025-08-25T03:05:55.752Z'
                                    error={Boolean(startTime && !isValidISO(startTime))}
                                    helperText={startTime && !isValidISO(startTime) ? 'Invalid ISO 8601 format' : ''}
                                />
                            </Grid>
                            <Grid size={{ xs: 12, md: 6 }}>
                                <TextField
                                    fullWidth
                                    label='End Time (ISO 8601)'
                                    value={endTime}
                                    onChange={e => setEndTime(e.target.value)}
                                    placeholder='2025-08-25T03:06:15.752Z'
                                    error={Boolean(endTime && !isValidISO(endTime))}
                                    helperText={endTime && !isValidISO(endTime) ? 'Invalid ISO 8601 format' : ''}
                                />
                            </Grid>

                            <Grid size={{ xs: 12, md: 4 }}>
                                <TextField
                                    fullWidth
                                    label='Expiry Minutes (optional)'
                                    value={expiryMinutes}
                                    onChange={e => {
                                        const value = e.target.value;
                                        if (value === '' || (Number(value) > 0 && !isNaN(Number(value)))) {
                                            setExpiryMinutes(value);
                                        }
                                    }}
                                    type='number'
                                    inputProps={{ min: 1 }}
                                    helperText='Default: 7 days (10080 minutes)'
                                />
                            </Grid>
                            <Grid size={{ xs: 12, md: 4 }}>
                                <TextField
                                    fullWidth
                                    label='Container Format'
                                    value={container}
                                    onChange={e => setContainer(e.target.value)}
                                    placeholder='mp4'
                                />
                            </Grid>
                            <Grid size={{ xs: 12, md: 4 }}>
                                <FormControlLabel
                                    control={
                                        <Checkbox
                                            checked={useVideoConfiguration}
                                            onChange={e => setUseVideoConfiguration(e.target.checked)}
                                            disabled={!sensor}
                                        />
                                    }
                                    label='Video Configuration'
                                />
                            </Grid>

                            {/* Video Configuration Section */}
                            <Grid size={{ xs: 12 }}>
                                <Collapse in={useVideoConfiguration}>
                                    <Box
                                        sx={{
                                            p: theme.spacing(3),
                                            border: `1px solid ${alpha(theme.palette.primary.main, 0.2)}`,
                                            borderRadius: theme.spacing(1),
                                            bgcolor: alpha(theme.palette.primary.main, 0.02),
                                        }}
                                    >
                                        <Typography variant='h6' gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
                                            <SettingsIcon />
                                            Video Configuration
                                        </Typography>

                                        <Grid container spacing={theme.spacing(2)}>
                                            {/* Audio Section */}
                                            <Grid size={{ xs: 12, md: 6 }}>
                                                <Box sx={{ p: 2, bgcolor: alpha(theme.palette.background.default, 0.5), borderRadius: 1 }}>
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

                                            {/* Analytics Overlay Section */}
                                            <Grid size={{ xs: 12, md: 6 }}>
                                                <Box sx={{ p: 2, bgcolor: alpha(theme.palette.background.default, 0.5), borderRadius: 1 }}>
                                                    <Typography variant='subtitle1' sx={{ mb: 2, fontWeight: 'medium' }}>
                                                        Analytics Overlay
                                                    </Typography>
                                                    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                                                        <Typography variant='body2' color='text.secondary'>
                                                            Configure advanced analytics overlay settings
                                                        </Typography>
                                                        <Button
                                                            variant='outlined'
                                                            size='small'
                                                            onClick={handleVideoAnalyticsDialogOpen}
                                                            sx={{ alignSelf: 'flex-start' }}
                                                        >
                                                            Configure Analytics Overlay
                                                        </Button>
                                                    </Box>
                                                </Box>
                                            </Grid>
                                        </Grid>

                                        {/* Video Configuration Summary */}
                                        <AnalyticsOverlayConfigurationSummary
                                            overlaySettings={videoConfig.overlay}
                                            compact={true}
                                            title='Video Analytics Configuration Summary'
                                        />
                                    </Box>
                                </Collapse>
                            </Grid>

                            {errorMsg && (
                                <Grid size={{ xs: 12 }}>
                                    <Alert severity='error'>{errorMsg}</Alert>
                                </Grid>
                            )}

                            {result && (
                                <Grid size={{ xs: 12 }}>
                                    <Paper
                                        sx={{
                                            p: theme.spacing(3),
                                            bgcolor: alpha(theme.palette.success.main, 0.05),
                                            border: `1px solid ${alpha(theme.palette.success.main, 0.2)}`,
                                        }}
                                    >
                                        <Stack spacing={theme.spacing(2)}>
                                            <Box display='flex' alignItems='center' gap={1}>
                                                <Chip label='Success' color='success' size='small' />
                                                <Typography variant='h6' color='success.main'>
                                                    Video URL Generated
                                                </Typography>
                                            </Box>

                                            <Box>
                                                <Typography variant='subtitle2' color='text.secondary' gutterBottom>
                                                    File Path
                                                </Typography>
                                                <Typography variant='body2' sx={{ fontFamily: 'monospace', wordBreak: 'break-all' }}>
                                                    {result.absolutePath}
                                                </Typography>
                                            </Box>

                                            <Box>
                                                <Typography variant='subtitle2' color='text.secondary' gutterBottom>
                                                    Expiry Information
                                                </Typography>
                                                <Typography variant='body2'>
                                                    {new Date(result.expiryISO).toLocaleString()} ({result.expiryMinutes} minutes)
                                                </Typography>
                                            </Box>

                                            <Box>
                                                <Typography variant='subtitle2' color='text.secondary' gutterBottom>
                                                    Video URL
                                                </Typography>
                                                <Link
                                                    href={result.videoUrl}
                                                    target='_blank'
                                                    rel='noreferrer'
                                                    sx={{
                                                        display: 'flex',
                                                        alignItems: 'center',
                                                        gap: 1,
                                                        color: theme.palette.primary.main,
                                                        textDecoration: 'none',
                                                        '&:hover': {
                                                            textDecoration: 'underline',
                                                        },
                                                    }}
                                                >
                                                    <OpenInNewIcon fontSize='small' />
                                                    Open Video in New Tab
                                                </Link>
                                            </Box>

                                            <Box>
                                                <Typography variant='subtitle2' color='text.secondary' gutterBottom>
                                                    Raw Response
                                                </Typography>
                                                <Box
                                                    component='pre'
                                                    sx={{
                                                        m: 0,
                                                        p: theme.spacing(2),
                                                        bgcolor: alpha(theme.palette.text.primary, 0.05),
                                                        borderRadius: theme.spacing(1),
                                                        fontSize: '0.75rem',
                                                        fontFamily: 'monospace',
                                                        overflow: 'auto',
                                                        maxHeight: 200,
                                                        border: `1px solid ${alpha(theme.palette.text.primary, 0.1)}`,
                                                    }}
                                                >
                                                    {JSON.stringify(result, null, 2)}
                                                </Box>
                                            </Box>
                                        </Stack>
                                    </Paper>
                                </Grid>
                            )}

                            <Grid size={{ xs: 12 }}>
                                <Stack direction='row' spacing={2} flexWrap='wrap'>
                                    <Button
                                        variant='contained'
                                        onClick={handleGenerate}
                                        disabled={loading || !sensor}
                                        startIcon={loading ? <PlayArrowIcon /> : <VideoLibraryIcon />}
                                        sx={{ minWidth: 160 }}
                                    >
                                        {loading ? 'Generating…' : 'Generate Video URL'}
                                    </Button>
                                    <Button
                                        variant='outlined'
                                        onClick={handleCopy}
                                        disabled={!result?.videoUrl}
                                        startIcon={<ContentCopyIcon />}
                                    >
                                        Copy Video URL
                                    </Button>
                                </Stack>
                            </Grid>
                        </Grid>
                    </CardContent>
                </Card>

                {/* Picture URL Section */}
                <Card>
                    <CardHeader
                        title={
                            <Box display='flex' alignItems='center' gap={1}>
                                <ImageIcon color='primary' />
                                Image URL (Live / Replay)
                            </Box>
                        }
                        sx={{
                            '& .MuiCardHeader-title': {
                                color: theme.palette.text.primary,
                                fontWeight: 600,
                            },
                        }}
                    />
                    <CardContent>
                        <Grid container spacing={theme.spacing(3)}>
                            <Grid size={{ xs: 12, md: 6 }}>
                                <TextField
                                    fullWidth
                                    label='Replay Start Time (ISO 8601)'
                                    value={startTime}
                                    onChange={e => setStartTime(e.target.value)}
                                    placeholder='2025-08-25T03:05:55.752Z'
                                    error={Boolean(startTime && !isValidISO(startTime))}
                                    helperText={
                                        startTime && !isValidISO(startTime) ? 'Invalid ISO 8601 format' : 'Required for replay images'
                                    }
                                />
                            </Grid>
                            <Grid size={{ xs: 12, md: 6 }}>
                                <TextField
                                    fullWidth
                                    label='Expiry Minutes (optional)'
                                    value={pictureExpiryMinutes}
                                    onChange={e => {
                                        const value = e.target.value;
                                        if (value === '' || (Number(value) > 0 && !isNaN(Number(value)))) {
                                            setPictureExpiryMinutes(value);
                                        }
                                    }}
                                    type='number'
                                    inputProps={{ min: 1 }}
                                    helperText='Default: 7 days (10080 minutes)'
                                />
                            </Grid>

                            {/* Picture Overlay Configuration */}
                            <Grid size={{ xs: 12 }}>
                                <FormControlLabel
                                    control={
                                        <Checkbox
                                            checked={usePictureOverlay}
                                            onChange={e => setUsePictureOverlay(e.target.checked)}
                                            disabled={!sensor}
                                        />
                                    }
                                    label='Picture Overlay Configuration'
                                />
                            </Grid>

                            <Grid size={{ xs: 12 }}>
                                <Collapse in={usePictureOverlay}>
                                    <Box
                                        sx={{
                                            p: theme.spacing(3),
                                            border: `1px solid ${alpha(theme.palette.info.main, 0.2)}`,
                                            borderRadius: theme.spacing(1),
                                            bgcolor: alpha(theme.palette.info.main, 0.02),
                                        }}
                                    >
                                        <Typography variant='h6' gutterBottom sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
                                            <SettingsIcon />
                                            Picture Analytics Overlay Configuration
                                        </Typography>

                                        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                                            <Typography variant='body2' color='text.secondary'>
                                                Configure advanced analytics overlay settings including bounding boxes, object IDs, colors,
                                                positions, proximity settings, and more.
                                            </Typography>

                                            <Button
                                                variant='outlined'
                                                onClick={handlePictureAnalyticsDialogOpen}
                                                sx={{ alignSelf: 'flex-start' }}
                                            >
                                                Configure Picture Analytics Overlay
                                            </Button>

                                            {/* Picture Configuration Summary */}
                                            <AnalyticsOverlayConfigurationSummary
                                                overlaySettings={pictureOverlay}
                                                compact={true}
                                                title='Picture Analytics Configuration Summary'
                                            />
                                        </Box>
                                    </Box>
                                </Collapse>
                            </Grid>

                            {pictureError && (
                                <Grid size={{ xs: 12 }}>
                                    <Alert severity='error'>{pictureError}</Alert>
                                </Grid>
                            )}

                            {(pictureResultLive || pictureResultReplay) && (
                                <Grid size={{ xs: 12 }}>
                                    <Paper
                                        sx={{
                                            p: theme.spacing(3),
                                            bgcolor: alpha(theme.palette.success.main, 0.05),
                                            border: `1px solid ${alpha(theme.palette.success.main, 0.2)}`,
                                        }}
                                    >
                                        <Stack spacing={theme.spacing(3)}>
                                            {pictureResultLive && (
                                                <Box>
                                                    <Box display='flex' alignItems='center' gap={1} mb={2}>
                                                        <Chip label='Live' color='success' size='small' />
                                                        <Typography variant='h6' color='success.main'>
                                                            Live Image URL
                                                        </Typography>
                                                    </Box>
                                                    <Link
                                                        href={pictureResultLive.imageUrl}
                                                        target='_blank'
                                                        rel='noreferrer'
                                                        sx={{
                                                            display: 'flex',
                                                            alignItems: 'center',
                                                            gap: 1,
                                                            mb: 2,
                                                            color: theme.palette.primary.main,
                                                            textDecoration: 'none',
                                                            '&:hover': {
                                                                textDecoration: 'underline',
                                                            },
                                                        }}
                                                    >
                                                        <OpenInNewIcon fontSize='small' />
                                                        Open Live Image in New Tab
                                                    </Link>
                                                    <Box>
                                                        <Typography variant='subtitle2' color='text.secondary' gutterBottom>
                                                            Raw Live Response
                                                        </Typography>
                                                        <Box
                                                            component='pre'
                                                            sx={{
                                                                m: 0,
                                                                p: theme.spacing(2),
                                                                bgcolor: alpha(theme.palette.text.primary, 0.05),
                                                                borderRadius: theme.spacing(1),
                                                                fontSize: '0.75rem',
                                                                fontFamily: 'monospace',
                                                                overflow: 'auto',
                                                                maxHeight: 200,
                                                                border: `1px solid ${alpha(theme.palette.text.primary, 0.1)}`,
                                                            }}
                                                        >
                                                            {JSON.stringify(pictureResultLive, null, 2)}
                                                        </Box>
                                                    </Box>
                                                </Box>
                                            )}

                                            {pictureResultReplay && (
                                                <Box>
                                                    <Box display='flex' alignItems='center' gap={1} mb={2}>
                                                        <Chip label='Replay' color='info' size='small' />
                                                        <Typography variant='h6' color='info.main'>
                                                            Replay Image URL
                                                        </Typography>
                                                    </Box>
                                                    <Link
                                                        href={pictureResultReplay.imageUrl}
                                                        target='_blank'
                                                        rel='noreferrer'
                                                        sx={{
                                                            display: 'flex',
                                                            alignItems: 'center',
                                                            gap: 1,
                                                            mb: 2,
                                                            color: theme.palette.primary.main,
                                                            textDecoration: 'none',
                                                            '&:hover': {
                                                                textDecoration: 'underline',
                                                            },
                                                        }}
                                                    >
                                                        <OpenInNewIcon fontSize='small' />
                                                        Open Replay Image in New Tab
                                                    </Link>
                                                    <Box>
                                                        <Typography variant='subtitle2' color='text.secondary' gutterBottom>
                                                            Raw Replay Response
                                                        </Typography>
                                                        <Box
                                                            component='pre'
                                                            sx={{
                                                                m: 0,
                                                                p: theme.spacing(2),
                                                                bgcolor: alpha(theme.palette.text.primary, 0.05),
                                                                borderRadius: theme.spacing(1),
                                                                fontSize: '0.75rem',
                                                                fontFamily: 'monospace',
                                                                overflow: 'auto',
                                                                maxHeight: 200,
                                                                border: `1px solid ${alpha(theme.palette.text.primary, 0.1)}`,
                                                            }}
                                                        >
                                                            {JSON.stringify(pictureResultReplay, null, 2)}
                                                        </Box>
                                                    </Box>
                                                </Box>
                                            )}
                                        </Stack>
                                    </Paper>
                                </Grid>
                            )}

                            <Grid size={{ xs: 12 }}>
                                <Stack direction='row' spacing={2} flexWrap='wrap'>
                                    <Button
                                        variant='contained'
                                        onClick={() => handleGeneratePictureUrl('live')}
                                        disabled={pictureLoading || !sensor}
                                        startIcon={<ImageIcon />}
                                        color='success'
                                    >
                                        Get Live Image URL
                                    </Button>
                                    <Button
                                        variant='contained'
                                        onClick={() => handleGeneratePictureUrl('replay')}
                                        disabled={pictureLoading || !sensor}
                                        startIcon={<ImageIcon />}
                                        color='info'
                                    >
                                        Get Replay Image URL
                                    </Button>
                                    <Button
                                        variant='outlined'
                                        onClick={() => handleCopyImageUrl(pictureResultLive?.imageUrl)}
                                        disabled={!pictureResultLive?.imageUrl}
                                        startIcon={<ContentCopyIcon />}
                                    >
                                        Copy Live URL
                                    </Button>
                                    <Button
                                        variant='outlined'
                                        onClick={() => handleCopyImageUrl(pictureResultReplay?.imageUrl)}
                                        disabled={!pictureResultReplay?.imageUrl}
                                        startIcon={<ContentCopyIcon />}
                                    >
                                        Copy Replay URL
                                    </Button>
                                </Stack>
                            </Grid>
                        </Grid>
                    </CardContent>
                </Card>

                {/* Analytics Overlay Dialogs */}
                <AnalyticsOverlayDialog
                    open={openVideoAnalyticsDialog}
                    onClose={handleVideoAnalyticsDialogClose}
                    onSave={handleVideoAnalyticsOverlaySave}
                    streamType={StreamType.Replay}
                />

                <AnalyticsOverlayDialog
                    open={openPictureAnalyticsDialog}
                    onClose={handlePictureAnalyticsDialogClose}
                    onSave={handlePictureAnalyticsOverlaySave}
                    streamType={StreamType.Replay}
                />
            </Paper>
        </Box>
    );
};

export default VideoURL;

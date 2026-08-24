/*
 * SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
import React, { useState, useRef, useCallback, useEffect } from 'react';
import {
    Card,
    CardContent,
    CardActions,
    IconButton,
    Button,
    Dialog,
    DialogTitle,
    DialogContent,
    DialogActions,
    Box,
    LinearProgress,
    TextField,
    Divider,
    Typography,
    CircularProgress,
    Tooltip,
} from '@mui/material';
import { Settings, Close } from '@mui/icons-material';
import { VideoPlayerProps, WebRTCStats, Timeline } from '../../interfaces/interfaces';
import StreamManager, {
    StreamConfig,
    StreamType,
    ErrorType,
    AppConfig,
    StreamState,
    StreamOverlayOptions,
    StreamCompositeOptions,
    WebRTCIssue,
    WebRTCNetworkScores,
} from 'vst-streaming-lib';
import RangePickerDialog from '../../features/rangePickerDialog/RangePickerDialog';
import LOG from '../../utils/misc/Logger';
import { pause, resume, rewindOrFastforward, seekBackward, seekForward, seekToTime } from './videoPlayerUtils/trickModesAPIs';
import { subSeconds, format } from 'date-fns';
import QualityMenu from './videoPlayerUtils/QualityMenu';
import NetworkQualityWidget from './videoPlayerUtils/NetworkQualityWidget';
import { getWebRTCStats, isAudioTrackPresentInPeerConnection, getTimelineGaps } from '../../utils/misc/utils';
import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline';
import nvAxios from '../../services/Axios';
import { EventMarker } from '../../features/timeSlider/TimeSlider';
import config from '../../config';
import VideoControls from './videoPlayerUtils/VideoControls';
import TimeRangeSlider from '../../features/timeSlider/TimeSlider';
import { useSnackbar } from 'notistack';
import AnalyticsOverlayDialog from './videoPlayerUtils/analytics/AnalyticsOverlayDialog';
import { logInfo } from '../../utils/misc/Logs';
import moment from 'moment';
import { muiStyles, htmlStyles } from './videoPlayerUtils/videoPlayerStyles';
import VideoWallPlaybackHandler from './videoPlayerUtils/VideoWallPlaybackHandler';
import usePlaybackProgress from './videoPlayerUtils/PlaybackProgressHandler';
import { useBitrate } from '../../hooks/useBitrate';
import BitrateSparkline from './videoPlayerUtils/BitrateSparkline';
import Analytics from './videoPlayerUtils/analytics/Analytics';
import { useShowEvents, useEventImages } from './videoPlayerUtils/ShowEvents';
import useVSTUIStore from '../../services/StateManagement';
import { toWebSocketUrl } from '../../utils/runtimeConfig';

const FALLBACK_START_TIME = '1970-01-01T00:00:00.000Z';
const DEFAULT_QUALITY = 'auto';
// Delay before auto-hiding the overlay controls while in fullscreen (YouTube/VLC style).
const CONTROLS_HIDE_DELAY_MS = 3000;
type LiveDeliveryProtocol = 'webrtc' | 'dash';

interface SensorTimelineEntry {
    sensorId?: string;
    timelines?: Timeline[];
}

type TimelineResponseData = SensorTimelineEntry[] | Record<string, unknown>;

// MMS returns the timelines endpoint; VST returns storage/size with the requested time range as query params.
const buildTimelineEndpoint = (isMms: boolean, startTime?: string, endTime?: string): string => {
    if (isMms) {
        return `${config.storageManagementEndpoint}/api/v1/storage/timelines`;
    }

    const queryParams = new URLSearchParams();
    queryParams.append('timelines', 'true');
    if (startTime) {
        queryParams.append('startTime', startTime);
    }
    if (endTime) {
        queryParams.append('endTime', endTime);
    }

    return `${config.storageManagementEndpoint}/api/v1/storage/size?${queryParams.toString()}`;
};

// MMS response shape: { streamId: [{ startTime, endTime }] }
const extractMmsTimelines = (data: TimelineResponseData, sensorId: string): Timeline[] => {
    const entry = (data as Record<string, unknown>)[sensorId];
    return Array.isArray(entry) ? (entry as Timeline[]) : [];
};

// VST responses come in three shapes: an array of sensors, an object keyed by sensorId, or a timelines map.
const extractVstTimelines = (data: TimelineResponseData, sensorId: string): Timeline[] => {
    if (Array.isArray(data)) {
        const sensorData = data.find((item: SensorTimelineEntry) => item.sensorId === sensorId);
        return sensorData?.timelines ?? [];
    }

    const sensorEntry = data[sensorId] as SensorTimelineEntry | undefined;
    if (sensorEntry?.timelines) {
        return sensorEntry.timelines;
    }

    const timelinesById = data.timelines as Record<string, Timeline[]> | undefined;
    return timelinesById?.[sensorId] ?? [];
};

const fetchSensorTimelines = async (
    sensorId: string,
    streamId: string,
    isMms: boolean,
    startTime?: string,
    endTime?: string
): Promise<Timeline[] | null> => {
    const response = await nvAxios.get(buildTimelineEndpoint(isMms, startTime, endTime), {
        headers: { streamId },
    });
    if (!response.data) {
        return null;
    }

    return isMms ? extractMmsTimelines(response.data, sensorId) : extractVstTimelines(response.data, sensorId);
};

const formatTimelineRange = (startTime?: string, endTime?: string): string =>
    startTime && endTime ? `${startTime} to ${endTime}` : 'full range';

const getNextPlaybackSpeed = (type: string, currentSpeed: number): number => {
    if (type === 'fastForward') {
        if (currentSpeed >= 1) {
            return currentSpeed * 2;
        }
        return currentSpeed === -1 ? 1 : currentSpeed / 2;
    }
    if (currentSpeed <= -1) {
        return currentSpeed * 2;
    }
    return currentSpeed === 1 ? -1 : currentSpeed / 2;
};

const VideoPlayer: React.FC<VideoPlayerProps> = ({ sensor, streamType, videoElementId, onWebRTCStatsUpdate, sensors, onClose }) => {
    // WebRTC and stream management
    const [inboundPeerId, setInboundPeerId] = useState<string>('');
    const [webRTCStats, setWebRTCStats] = useState<WebRTCStats>();
    const [fullWebRTCStats, setFullWebRTCStats] = useState<RTCStatsReport>();
    // TEMPORARY: DASH is the default so a stream opens straight into it instead
    // of starting on WebRTC and needing a manual switch.  Revert this single
    // value to 'webrtc' to restore the shipping default.
    const [deliveryProtocol, setDeliveryProtocol] = useState<LiveDeliveryProtocol>('dash');
    const bitrate = useBitrate(fullWebRTCStats);

    // Video playback controls
    const [volume, setVolume] = useState<number>(100);
    const [isMuted, setIsMuted] = useState<boolean>(false);
    const [percentagePlayback, setPercentagePlayback] = useState<number>(0);
    const [playbackSpeed, setPlaybackSpeed] = useState(1);
    const [playbackStatus, setPlaybackStatus] = useState<StreamState>(StreamState.NOT_PLAYING);
    const [delayBySeconds, setDelayBySeconds] = useState('10');

    // UI state
    const [isActuallyFullScreen, setIsActuallyFullScreen] = useState<boolean>(false);
    // Visibility of the auto-hiding overlay controls; only auto-hides while in fullscreen.
    const [controlsVisible, setControlsVisible] = useState<boolean>(true);
    // Tracks the quality menu so the fullscreen controls (and cursor) stay visible while it is open.
    const [isQualityMenuOpen, setIsQualityMenuOpen] = useState<boolean>(false);
    const [isLoading, setIsLoading] = useState(true);
    const [connectionPhase, setConnectionPhase] = useState<'initial' | 'connecting' | 'waiting'>('initial');
    const [isLoadingTimelines, setIsLoadingTimelines] = useState<boolean>(false);

    // Dialog states
    const [isSyncDialogOpen, setIsSyncDialogOpen] = useState<boolean>(false);
    const [isRangeDialogOpen, setIsRangeDialogOpen] = useState<boolean>(false);
    const [openAnalyticsOverlay, setOpenAnalyticsOverlay] = useState(false);

    // Events state
    const [eventMarkers, setEventMarkers] = useState<EventMarker[]>([]);
    const [isFetchingEvents, setIsFetchingEvents] = useState<boolean>(false);

    // Zoom state
    const [zoomLevel, setZoomLevel] = useState<number>(1); // 1x = full view, 2x = 2x zoom, etc.
    const [zoomCenter, setZoomCenter] = useState<number | null>(null); // timestamp to center zoom on
    const [visibleTimeRange, setVisibleTimeRange] = useState<{ start: string; end: string } | null>(null);
    const [zoomFeedback, setZoomFeedback] = useState<string | null>(null); // Temporary feedback message

    // Event images state (managed by useEventImages hook)
    const [loadingImages, setLoadingImages] = useState<Record<string, boolean>>({});

    // Stream and video settings
    const [quality, setQuality] = useState(streamType === StreamType.VideoWall ? 'high' : DEFAULT_QUALITY);
    const [videoWidth] = useState<number>(1920);
    const [videoHeight] = useState<number>(1080);

    const [isAudioTrackPresent, setIsAudioTrackPresent] = useState(false);
    const [overlaySettings, setOverlaySettings] = useState<StreamOverlayOptions | undefined>(undefined);

    // Calendar and timeline related
    const [calenderStartTime, setCalenderStartTime] = useState<string>();
    const [calenderEndTime, setCalenderEndTime] = useState<string>();
    const [timelines, setTimelines] = useState<Timeline[]>([]);
    const timelinesRef = useRef<Timeline[]>([]);
    const [disabledIntervals, setDisabledIntervals] = useState<Timeline[] | undefined>([]);

    // Error handling
    const [hasError, setHasError] = useState(false);
    const [errorDetails, setErrorDetails] = useState<{
        message: string;
        code: number;
    } | null>(null);
    const { enqueueSnackbar } = useSnackbar();

    // Refs
    const videoRef = useRef<HTMLVideoElement>(null);
    const videoWrapperRef = useRef<HTMLDivElement>(null);
    const streamManagerRef = useRef<StreamManager | null>(null);
    const runOnceRef = useRef<boolean>(true);
    const startTimeMs = useRef<number | null>(null);
    const endTimeMs = useRef<number | null>(null);
    const inboundPeerIDRef = useRef<string | undefined>(undefined);
    const inboundMediaSessionIDRef = useRef<string | undefined>(undefined);
    const inboundConnectionQualityWatchdogRef = useRef<NodeJS.Timeout>(undefined);
    const overlayRef = useRef<HTMLCanvasElement | null>(null);
    const controlsHideTimerRef = useRef<NodeJS.Timeout | null>(null);
    // Guards against overlapping fullscreen requests/exits when the button is spammed.
    const fullscreenTransitionRef = useRef<boolean>(false);

    const emdxEndpointRef = useRef<string>('');

    // Initialize the usePlaybackProgress hook
    const { onPlaybackTimeUpdate } = usePlaybackProgress({
        startTimeMs,
        endTimeMs,
        setPercentagePlayback,
        timelines, // Pass timeline data for accurate progress calculation
    });

    // Initialize the useShowEvents hook
    const { fetchTripwireEvents, cancelEventsFetch } = useShowEvents({
        sensorId: sensor?.sensorId,
        sensorName: sensor?.name,
        timelines,
        calenderStartTime,
        calenderEndTime,
        onEventsUpdate: setEventMarkers,
        onFetchingStateChange: setIsFetchingEvents,
    });

    // Handle show/cancel events button click
    const handleShowEventsClick = () => {
        if (isFetchingEvents) {
            cancelEventsFetch();
        } else {
            fetchTripwireEvents();
        }
    };

    // Initialize the useEventImages hook
    const { fetchEventImage: fetchEventImageHook, getEventImage } = useEventImages(sensor?.sensorId);

    // Callback to monitor WebRTC connection quality
    const inboundConnectionQualityWatchdog = useCallback(() => {
        inboundConnectionQualityWatchdogRef.current = setInterval(async () => {
            const inboundPeerConnection = streamManagerRef.current?.getInboundPeerConnectionObject();
            if (inboundPeerConnection) {
                try {
                    const { basicStats, fullStats } = await getWebRTCStats(inboundPeerConnection);
                    setWebRTCStats(basicStats);
                    setFullWebRTCStats(fullStats);
                    if (onWebRTCStatsUpdate) {
                        onWebRTCStatsUpdate(fullStats);
                    }
                } catch (error) {
                    LOG.error('Error getting WebRTC stats:', error);
                }
            }
        }, 1000);
    }, [onWebRTCStatsUpdate]);

    // Helper function to get the earliest start time from timelines
    // DASH replay needs a bounded window.  Left open ended, the packager
    // republishes the whole recording as fast as it can read it while the player
    // follows a wall clock edge, so the two drift apart and the player asks for
    // segments that do not exist yet.
    const getLatestEndTime = (): string | undefined => {
        if (!timelinesRef.current || timelinesRef.current.length === 0) {
            return undefined;
        }
        return timelinesRef.current.reduce((latest, timeline) => {
            return new Date(timeline.endTime) > new Date(latest) ? timeline.endTime : latest;
        }, timelinesRef.current[0].endTime);
    };

    const getEarliestStartTime = (): string => {
        if (!timelinesRef.current || timelinesRef.current.length === 0) {
            LOG.warn('No timelines found, using fallback start time:', FALLBACK_START_TIME);
            return FALLBACK_START_TIME;
        }

        return timelinesRef.current.reduce((earliest, timeline) => {
            LOG.verbose('Comparing timeline start time:', timeline.startTime, 'with earliest:', earliest);
            return new Date(timeline.startTime) < new Date(earliest) ? timeline.startTime : earliest;
        }, timelinesRef.current[0].startTime);
    };

    // Callback for successful WebRTC connection establishment
    const onSuccessCallback = useCallback(
        (inboundPeerID: string, inboundMediaSessionID: string) => {
            LOG.info('onSuccessCallback called with:', inboundPeerID);
            setConnectionPhase('waiting');

            if (inboundPeerID) {
                Promise.resolve().then(() => {
                    inboundPeerIDRef.current = inboundPeerID;
                    inboundMediaSessionIDRef.current = inboundMediaSessionID;
                    setInboundPeerId(inboundPeerID);

                    const inboundPeerConnection = streamManagerRef.current?.getInboundPeerConnectionObject();
                    if (inboundPeerConnection) {
                        setIsAudioTrackPresent(isAudioTrackPresentInPeerConnection(inboundPeerConnection));
                    }

                    // For StreamBridge type, set playback status to PLAYING on success since for video wall
                    // VST doesn't send stream status updates.
                    if (streamType === StreamType.VideoWall) {
                        LOG.info('Video wall stream, setting playback status to PLAYING');
                        setPlaybackStatus(StreamState.PLAYING);
                        setIsLoading(false);
                    }

                    inboundConnectionQualityWatchdog();
                });
            }
        },
        [inboundConnectionQualityWatchdog, streamType]
    );

    // Callback for handling WebRTC connection errors
    const onErrorCallback = useCallback((error: ErrorType) => {
        LOG.error('on Error: ', error);
        clearInterval(inboundConnectionQualityWatchdogRef.current);
        if (streamManagerRef.current) {
            streamManagerRef.current.stopStreaming();
            streamManagerRef.current = null;
        }
        setHasError(true);
        setIsLoading(false);
        setErrorDetails({
            message: error.message,
            code: error.code,
        });
        setPlaybackStatus(StreamState.NOT_PLAYING);
    }, []);

    // Callback for handling stream status updates
    const onStreamStatusUpdate = useCallback((status: { error: boolean; state: StreamState }) => {
        if (status && status.state) {
            if (status.error) {
                setHasError(true);
                setIsLoading(false);
                return;
            }
            Promise.resolve().then(() => {
                LOG.info('Stream status update received:', status.state);
                setPlaybackStatus(status.state);
                setIsLoading(status.state === StreamState.NOT_PLAYING);
                setHasError(false);
            });
        }
    }, []);

    // Callback for handling WebRTC issues
    const onWebRTCIssueDetected = useCallback((issue: WebRTCIssue) => {
        LOG.info('WebRTC Issue detected:', issue);
    }, []);

    // Callback for handling WebRTC network scores
    const onWebRTCNetworkScoresUpdated = useCallback((scores: WebRTCNetworkScores) => {
        LOG.info('WebRTC Network Scores:', scores);
    }, []);

    // Callback for handling video metadata loading
    const handleVideoMetadata = useCallback(() => {
        const video = document.getElementById(videoElementId);
        if (video && overlayRef.current) {
            const overlay = overlayRef.current;
            overlay.width = video.offsetWidth;
            overlay.height = video.offsetHeight;
        }
    }, [videoElementId]);

    useEffect(() => {
        runOnceRef.current = false;

        const onFirstFrameReceived = () => {
            // NOTE: test contract — BDD/sanity scrapes the browser console for this exact string.
            // Keep it a raw, unprefixed console.log; do NOT route via LOG.
            // eslint-disable-next-line no-console
            console.log('on First FrameReceived');
            setIsLoading(false);
            setConnectionPhase('initial');
        };

        // DASH is delivered over HTTP by streaming-lib.  The component only
        // selects the protocol; StreamManager owns the transport for both.
        if (deliveryProtocol === 'dash'
            && (streamType === StreamType.Live || streamType === StreamType.Replay)
            && !sensor?.streamId) {
            setHasError(true);
            setIsLoading(false);
            setErrorDetails({ message: 'A stream ID is required for DASH playback', code: 400 });
            return;
        }

        streamManagerRef.current = new StreamManager();
        const wsEndpoint = toWebSocketUrl(config.liveStreamEndpoint);

        const webStreamerConfig: AppConfig = {
            inboundStreamVideoElementId: videoElementId,
            enableMicrophone: false,
            enableCamera: false,
            streamType: streamType,
            enableWebsocketPing: true,
            enableLogs: true,
            vstWebsocketEndpoint: wsEndpoint,
            firstFrameReceivedCallback: onFirstFrameReceived,
            enableDummyUDPCall: false,
            onPlaybackUpdate: onPlaybackTimeUpdate,
            onStreamStatusUpdate: onStreamStatusUpdate,
            successCallback: onSuccessCallback,
            errorCallback: onErrorCallback,
            onWebRTCIssueDetected: onWebRTCIssueDetected,
            onWebRTCNetworkScoresUpdated: onWebRTCNetworkScoresUpdated,
        };

        LOG.info('webStreamerConfig: ', webStreamerConfig);

        streamManagerRef.current.updateConfig(webStreamerConfig);

        logInfo('overlaySettings 1', overlaySettings);

        const streamConfig: StreamConfig = {
            options: {
                rtptransport: 'udp',
                timeout: 60,
                quality: quality,
                framerate: 15,
                overlay: overlaySettings || {
                    bbox: { showAll: false, objectId: [], classType: [] },
                    tripwire: { showAll: false, id: [] },
                    roi: { showAll: false, id: [] },
                    debug: false,
                    opacity: 255,
                    proximityClass: [],
                    entrantClass: [],
                    proximityAreaFactor: 1.3,
                    proximityAnimation: '',
                    overlayColorCode: [],
                    needHalo: false,
                },
            },
        };

        // Handle different stream types
        if (streamType === StreamType.VideoWall && sensors) {
            // For Video Wall, use composite stream configuration
            streamConfig.options.composite = {
                doComposite: true,
                streamIds: sensors.map(s => s.streamId ?? s.sensorId).filter(Boolean) as string[],
                showSensorName: {
                    enable: true,
                    position: [10, 10],
                },
            };
        } else {
            // For Live and Replay streams, use single streamId
            streamConfig.streamId = sensor?.streamId;
            streamConfig.mainStreamId = sensor?.sensorId;

            if (streamType === StreamType.Replay) {
                streamConfig.startTime = getEarliestStartTime();
                if (deliveryProtocol === 'dash') {
                    streamConfig.endTime = getLatestEndTime();
                }
            }
        }

        if (deliveryProtocol === 'dash'
            && (streamType === StreamType.Live || streamType === StreamType.Replay
                || streamType === StreamType.VideoWall)) {
            streamConfig.options.streamType = 'dash';
            // The wall is composed from the cameras it names, but the session is
            // still filed under one of them, so give the request a stream to be
            // filed under rather than leaving it empty.
            if (streamType === StreamType.VideoWall && !streamConfig.streamId) {
                const wall = streamConfig.options.composite as
                    { streamIds?: string[] } | undefined;
                streamConfig.streamId = wall?.streamIds?.[0];
            }
        }

        startStream(streamConfig);
        setConnectionPhase('connecting');

        return () => {
            clearInterval(inboundConnectionQualityWatchdogRef.current);
            if (streamManagerRef.current) {
                streamManagerRef.current.stopStreaming();
                streamManagerRef.current = null;
            }
        };
        // Stream setup intentionally restarts only when the selected live delivery protocol changes.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [deliveryProtocol]);

    // Effect for fetching live stream configuration
    useEffect(() => {
        const fetchConfiguration = async () => {
            if (streamType === StreamType.Live) {
                try {
                    const response = await nvAxios.get(`${config.liveStreamEndpoint}/api/v1/live/configuration`, {
                        ...(sensor && { headers: { streamId: sensor.streamId || sensor.sensorId } }),
                    });
                    if (response.data?.analyticServerAddress) {
                        emdxEndpointRef.current = response.data.analyticServerAddress;
                    }
                } catch (error) {
                    LOG.error('Failed to fetch configuration:', error);
                }
            }
        };

        fetchConfiguration();
    }, [streamType]);

    // Effect for fetching timelines for replay mode
    useEffect(() => {
        const fetchTimelines = async () => {
            if (streamType !== StreamType.Replay || !sensor?.sensorId) {
                return;
            }

            const isMms = useVSTUIStore.getState().vstAdaptorType === 'mms';
            if (isMms) {
                setIsLoadingTimelines(true);
            }

            try {
                const sensorTimelines = await fetchSensorTimelines(
                    sensor.sensorId,
                    sensor.streamId || sensor.sensorId,
                    isMms,
                    calenderStartTime,
                    calenderEndTime
                );
                if (sensorTimelines) {
                    setTimelines(sensorTimelines);
                    timelinesRef.current = sensorTimelines;
                    setDisabledIntervals(getTimelineGaps(sensorTimelines));
                    // The player can be mounted before the asynchronous
                    // timeline request finishes.  VST accepts the legacy epoch
                    // fallback for that initial replay, but relative DASH seek
                    // controls need a real recording epoch once it is known.
                    if (deliveryProtocol === 'dash' && (startTimeMs.current === null
                        || startTimeMs.current === new Date(FALLBACK_START_TIME).getTime())
                        && sensorTimelines.length > 0) {
                        startTimeMs.current = new Date(sensorTimelines[0].startTime).getTime();
                    }
                    LOG.info(`Fetched ${sensorTimelines.length} timeline segments for sensor ${sensor.sensorId}`, {
                        timeRange: formatTimelineRange(calenderStartTime, calenderEndTime),
                    });
                }
            } catch (error) {
                LOG.error(`Failed to get sensor timelines for ${sensor?.name}`, error);
                enqueueSnackbar(`Failed to get sensor timelines for ${sensor?.name}`, {
                    variant: 'error',
                });
            } finally {
                if (isMms) {
                    setIsLoadingTimelines(false);
                }
            }
        };

        fetchTimelines();
    }, [sensor?.sensorId, streamType, enqueueSnackbar, sensor?.name, calenderStartTime, calenderEndTime, deliveryProtocol]);

    // Effect for updating visible time range when zoom changes
    useEffect(() => {
        const fullRange = getFullTimeRange();
        if (fullRange) {
            const newVisibleRange = calculateVisibleTimeRange(fullRange.start, fullRange.end, zoomLevel, zoomCenter);
            setVisibleTimeRange(newVisibleRange);
            LOG.info(`Zoom updated: ${zoomLevel.toFixed(1)}x, visible range: ${newVisibleRange.start} to ${newVisibleRange.end}`);
        }
    }, [zoomLevel, zoomCenter, calenderStartTime, calenderEndTime, timelines]);

    // Effect for logging inboundPeerId changes
    useEffect(() => {
        LOG.info('inboundPeerId state changed:', inboundPeerId);
        LOG.info('inboundPeerIDRef.current:', inboundPeerIDRef.current);
    }, [inboundPeerId]);

    // Effect to listen for actual fullscreen changes
    useEffect(() => {
        const handleFullscreenChange = () => {
            const doc = document as Document & {
                webkitFullscreenElement?: Element;
                mozFullScreenElement?: Element;
                msFullscreenElement?: Element;
            };
            const isFullscreen =
                document.fullscreenElement === videoWrapperRef.current ||
                doc.webkitFullscreenElement === videoWrapperRef.current ||
                doc.mozFullScreenElement === videoWrapperRef.current ||
                doc.msFullscreenElement === videoWrapperRef.current;
            setIsActuallyFullScreen(isFullscreen);
            // A transition has resolved; allow the next toggle.
            fullscreenTransitionRef.current = false;
        };

        // Add event listeners for different browser fullscreen APIs
        document.addEventListener('fullscreenchange', handleFullscreenChange);
        document.addEventListener('webkitfullscreenchange', handleFullscreenChange);
        document.addEventListener('mozfullscreenchange', handleFullscreenChange);
        document.addEventListener('msfullscreenchange', handleFullscreenChange);

        return () => {
            document.removeEventListener('fullscreenchange', handleFullscreenChange);
            document.removeEventListener('webkitfullscreenchange', handleFullscreenChange);
            document.removeEventListener('mozfullscreenchange', handleFullscreenChange);
            document.removeEventListener('msfullscreenchange', handleFullscreenChange);
        };
    }, []);

    // Effect for cleanup when component unmounts
    useEffect(() => {
        const cleanup = () => {
            clearInterval(inboundConnectionQualityWatchdogRef.current);
            if (streamManagerRef.current) {
                streamManagerRef.current.stopStreaming();
                streamManagerRef.current = null;
            }
            setInboundPeerId('');
            inboundPeerIDRef.current = undefined;
            inboundMediaSessionIDRef.current = undefined;

            // Clear any pending event click timeouts
            Object.values(eventClickTimeouts.current).forEach(timeoutId => {
                clearTimeout(timeoutId);
            });
            eventClickTimeouts.current = {};

            // Clear the controls auto-hide timer.
            if (controlsHideTimerRef.current) {
                clearTimeout(controlsHideTimerRef.current);
                controlsHideTimerRef.current = null;
            }
        };

        return cleanup;
    }, []);

    const handleTimeRangeSelect = async (time: string) => {
        if (deliveryProtocol === 'dash' && streamType === StreamType.Replay) {
            try {
                setIsLoading(true);
                setConnectionPhase('waiting');
                await streamManagerRef.current?.seekDashReplay(time);
                startTimeMs.current = new Date(time).getTime();
                setPlaybackStatus(StreamState.PLAYING);
            } catch (error) {
                LOG.error('Failed to seek DASH replay:', error);
                setIsLoading(false);
                enqueueSnackbar('Failed to seek DASH replay', { variant: 'error' });
            }
            return;
        }
        if (streamType === StreamType.Replay && inboundPeerIDRef.current && inboundMediaSessionIDRef.current && sensor?.streamId) {
            const isSuccess = await seekToTime(
                inboundPeerIDRef.current,
                inboundMediaSessionIDRef.current,
                time,
                sensor.streamId,
                enqueueSnackbar
            );
            if (isSuccess) {
                LOG.info('Seeked to time: ', time);
            } else {
                LOG.error('Failed to seek to time: ', time);
            }
        }
    };

    // For video wall, use HTML video element's play/pause
    const handleVideoWallPlayPause = async () => {
        if (!videoRef.current) {
            return;
        }
        if (videoRef.current.paused) {
            await videoRef.current.play();
            setPlaybackStatus(StreamState.PLAYING);
            logInfo('Video wall stream, playing');
        } else {
            videoRef.current.pause();
            setPlaybackStatus(StreamState.PAUSED);
            logInfo('Video wall stream, pausing');
        }
    };

    const handlePlayPause = async () => {
        if (deliveryProtocol === 'dash'
            && (streamType === StreamType.Live || streamType === StreamType.Replay)) {
            if (!videoRef.current) {
                return;
            }
            if (videoRef.current.paused) {
                await videoRef.current.play();
                setPlaybackStatus(StreamState.PLAYING);
            } else {
                videoRef.current.pause();
                setPlaybackStatus(StreamState.PAUSED);
            }
            return;
        }
        if (!inboundMediaSessionIDRef.current || !inboundPeerIDRef.current) {
            LOG.error('Stream not ready, cant play-pause');
            return;
        }

        if (streamType === StreamType.VideoWall) {
            await handleVideoWallPlayPause();
            return;
        }

        // Original logic for non-video wall streams
        LOG.info('handlePlayPause - Current playback status:', playbackStatus);

        if (playbackStatus === StreamState.PLAYING) {
            if (!sensor?.streamId) return;
            const isSuccess = await pause(
                inboundPeerIDRef.current,
                inboundMediaSessionIDRef.current,
                streamType,
                sensor.streamId,
                enqueueSnackbar
            );
            if (!isSuccess) {
                LOG.error('Failed to pause stream');
            }
        } else if (playbackStatus === StreamState.PAUSED || playbackStatus === StreamState.NOT_PLAYING) {
            // Handle both PAUSED and NOT_PLAYING states - both should resume/start playback
            if (!sensor?.streamId) return;
            const isSuccess = await resume(
                inboundPeerIDRef.current,
                inboundMediaSessionIDRef.current,
                streamType,
                sensor.streamId,
                enqueueSnackbar
            );
            if (!isSuccess) {
                LOG.error('Failed to resume stream');
            }
        }
    };

    const handleFastForwardAndRewind = async (type: string) => {
        if (playbackStatus === StreamState.NOT_PLAYING) {
            return;
        }
        const newSpeed = getNextPlaybackSpeed(type, playbackSpeed);

        if (newSpeed > 8) {
            LOG.error('Already at 8x, cant ff');
            return;
        }
        if (newSpeed < -8) {
            LOG.error('Already at -8x, cant rewind');
            return;
        }
        if (!inboundPeerIDRef.current || !inboundMediaSessionIDRef.current || !sensor?.streamId) {
            LOG.error('Stream not ready, cant ff');
            return;
        }

        const isSuccess = await rewindOrFastforward(
            inboundPeerIDRef.current,
            inboundMediaSessionIDRef.current,
            newSpeed,
            sensor.streamId,
            enqueueSnackbar
        );
        if (isSuccess) {
            setPlaybackSpeed(newSpeed);
        } else {
            LOG.error('FF or rewind failed');
        }
    };

    const handleSeekForward = async () => {
        if (deliveryProtocol === 'dash' && streamType === StreamType.Replay) {
            if (startTimeMs.current === null || !videoRef.current) {
                LOG.error('DASH replay is not ready to seek');
                return;
            }
            await handleTimeRangeSelect(new Date(startTimeMs.current
                + (videoRef.current.currentTime + 10) * 1000).toISOString());
            return;
        }
        if (!inboundMediaSessionIDRef.current || !inboundPeerIDRef.current || !sensor?.streamId) {
            LOG.error('Stream not ready, cant seek');
            return;
        }
        const isSuccess = await seekForward(inboundPeerIDRef.current, inboundMediaSessionIDRef.current, sensor.streamId, enqueueSnackbar);
        if (!isSuccess) {
            LOG.error('Seek +10 failed');
        }
    };
    const handleSeekBackward = async () => {
        if (deliveryProtocol === 'dash' && streamType === StreamType.Replay) {
            if (startTimeMs.current === null || !videoRef.current) {
                LOG.error('DASH replay is not ready to seek');
                return;
            }
            await handleTimeRangeSelect(new Date(startTimeMs.current
                + Math.max(0, videoRef.current.currentTime - 10) * 1000).toISOString());
            return;
        }
        if (!inboundMediaSessionIDRef.current || !inboundPeerIDRef.current || !sensor?.streamId) {
            LOG.error('Stream not ready, cant seek');
            return;
        }
        const isSuccess = await seekBackward(inboundPeerIDRef.current, inboundMediaSessionIDRef.current, sensor.streamId, enqueueSnackbar);
        if (!isSuccess) {
            LOG.error('Seek -10 failed');
        }
    };

    const handleVolumeChange = (_event: Event, newValue: number | number[]) => {
        const volumeValue = newValue as number;
        setVolume(volumeValue);
        if (videoRef.current) {
            videoRef.current.volume = volumeValue / 100;
        }
    };

    const handleMute = () => {
        setIsMuted(!isMuted);
        if (videoRef.current) {
            videoRef.current.muted = !isMuted;
        }
    };

    // Robust fullscreen toggle. Decisions are based on the live document.fullscreenElement
    // (vendor-prefixed) rather than React state, so it cannot desync. Overlapping requests
    // (rapid spam clicks) are ignored via a transition lock, and the returned promises are
    // always caught so a rejected request/exit never produces an unhandled rejection.
    const handleFullScreen = () => {
        if (fullscreenTransitionRef.current) {
            return; // a request/exit is already in flight; ignore the spam click
        }

        const doc = document as Document & {
            webkitFullscreenElement?: Element;
            mozFullScreenElement?: Element;
            msFullscreenElement?: Element;
            webkitExitFullscreen?: () => Promise<void> | void;
            msExitFullscreen?: () => Promise<void> | void;
        };
        const fsElement =
            document.fullscreenElement || doc.webkitFullscreenElement || doc.mozFullScreenElement || doc.msFullscreenElement || null;
        const wrapper = videoWrapperRef.current as
            | (HTMLDivElement & {
                  webkitRequestFullscreen?: () => Promise<void> | void;
                  msRequestFullscreen?: () => Promise<void> | void;
              })
            | null;
        const inFullscreen = !!fsElement && fsElement === wrapper;

        fullscreenTransitionRef.current = true;
        const release = () => {
            fullscreenTransitionRef.current = false;
        };

        try {
            if (!inFullscreen) {
                if (!wrapper) {
                    release();
                    return;
                }
                const req = wrapper.requestFullscreen?.() ?? wrapper.webkitRequestFullscreen?.() ?? wrapper.msRequestFullscreen?.();
                Promise.resolve(req).then(release, release);
            } else {
                const exit = document.exitFullscreen?.() ?? doc.webkitExitFullscreen?.() ?? doc.msExitFullscreen?.();
                Promise.resolve(exit).then(release, release);
            }
        } catch {
            release();
        }
        // Safety net: clear the lock even if neither the promise nor a fullscreenchange event fires.
        setTimeout(release, 1000);
    };

    // Controls must stay visible while a dialog/menu is open in fullscreen, otherwise the
    // auto-hide would fade out the control bar (and its menu anchor) mid-interaction.
    const isAnyOverlayDialogOpen = openAnalyticsOverlay || isSyncDialogOpen || isRangeDialogOpen || isQualityMenuOpen;

    // Reveal the overlay controls and (re)arm the auto-hide timer while in fullscreen.
    // In windowed mode the controls live in the card footer and are always visible.
    const showControls = useCallback(() => {
        setControlsVisible(true);
        if (controlsHideTimerRef.current) {
            clearTimeout(controlsHideTimerRef.current);
            controlsHideTimerRef.current = null;
        }
        if (isActuallyFullScreen && !isAnyOverlayDialogOpen) {
            controlsHideTimerRef.current = setTimeout(() => setControlsVisible(false), CONTROLS_HIDE_DELAY_MS);
        }
    }, [isActuallyFullScreen, isAnyOverlayDialogOpen]);

    // Keep control visibility consistent with fullscreen transitions and open dialogs.
    useEffect(() => {
        if (controlsHideTimerRef.current) {
            clearTimeout(controlsHideTimerRef.current);
            controlsHideTimerRef.current = null;
        }
        if (isActuallyFullScreen && !isAnyOverlayDialogOpen) {
            // Entering fullscreen with no dialog open: show controls, then start the countdown.
            setControlsVisible(true);
            controlsHideTimerRef.current = setTimeout(() => setControlsVisible(false), CONTROLS_HIDE_DELAY_MS);
        } else {
            // Windowed layout, or a dialog is open: controls stay visible.
            setControlsVisible(true);
        }
        return () => {
            if (controlsHideTimerRef.current) {
                clearTimeout(controlsHideTimerRef.current);
                controlsHideTimerRef.current = null;
            }
        };
    }, [isActuallyFullScreen, isAnyOverlayDialogOpen]);

    // Every restart rebuilds the config: changing quality, saving overlay
    // settings, picking a new replay window.  Each one has to carry the chosen
    // delivery protocol, and only the protocol-toggle effect used to do so, so
    // saving overlay settings silently dropped the stream back to WebRTC.
    // Stamping it here means a new caller cannot forget.
    const startStream = (config: StreamConfig) => {
        if (deliveryProtocol === 'dash'
            && (streamType === StreamType.Live || streamType === StreamType.Replay)) {
            config.options.streamType = 'dash';
            // A DASH replay session is packaged for a window, so it needs an end
            // as well as a start; the WebRTC path never had to supply one.
            if (streamType === StreamType.Replay && !config.endTime) {
                // Timelines arrive asynchronously and the first replay can be
                // requested before their latest bound is known.  Do not leave
                // DASH open ended in that case: the recorded source then runs
                // through its catalogue instead of maintaining a seekable
                // playback window.  A later seek preserves this bound.
                config.endTime = getLatestEndTime() ?? new Date().toISOString();
            }
            if (streamType === StreamType.Replay && config.startTime) {
                startTimeMs.current = new Date(config.startTime).getTime();
            }
        }
        streamManagerRef.current?.startStreaming(config);
    };

    const createStreamConfig = (options: {
        streamId?: string;
        mainStreamId?: string;
        quality: string;
        calenderStartTime?: string;
        calenderEndTime?: string;
        overlaySettings?: StreamOverlayOptions;
        isReplay?: boolean;
    }): StreamConfig => {
        const config: StreamConfig = {
            streamId: options.streamId,
            mainStreamId: options.mainStreamId,
            options: {
                rtptransport: 'udp',
                timeout: 60,
                quality: options.quality,
                overlay: options.overlaySettings,
            },
        };
        logInfo('overlaySettings 2', options.overlaySettings);

        if (options.calenderStartTime && options.calenderEndTime) {
            config.startTime = options.calenderStartTime;
            config.endTime = options.calenderEndTime;
        } else if (options.isReplay) {
            config.startTime = getEarliestStartTime();
        }

        return config;
    };

    const handleReplay = async () => {
        // Reset playback speed to normal (1x)
        setPlaybackSpeed(1);

        // Clear existing WebRTC stats monitoring interval before restarting
        clearInterval(inboundConnectionQualityWatchdogRef.current);

        await streamManagerRef.current?.stopStreaming();

        const streamConfig = createStreamConfig({
            streamId: sensor?.streamId,
            mainStreamId: sensor?.sensorId,
            quality,
            calenderStartTime,
            calenderEndTime,
            overlaySettings,
            isReplay: streamType === StreamType.Replay,
        });

        startStream(streamConfig);
    };

    const handleCalenderRangePlayback = async (startTime: string, endTime: string) => {
        setCalenderStartTime(startTime);
        setCalenderEndTime(endTime);
        startTimeMs.current = new Date(startTime).getTime();
        endTimeMs.current = new Date(endTime).getTime();

        // Clear existing WebRTC stats monitoring interval before restarting
        clearInterval(inboundConnectionQualityWatchdogRef.current);

        await streamManagerRef.current?.stopStreaming();
        setPlaybackSpeed(1);

        const streamConfig = createStreamConfig({
            streamId: sensor?.streamId,
            mainStreamId: sensor?.sensorId,
            quality,
            calenderStartTime: startTime,
            calenderEndTime: endTime,
            overlaySettings,
        });

        startStream(streamConfig);
    };

    const handleScreenshot = async () => {
        if (!sensor?.streamId) {
            enqueueSnackbar('No sensor selected', { variant: 'error' });
            return;
        }

        try {
            if (streamType === StreamType.Replay) {
                // For replay streams, first query the current timestamp
                if (!inboundPeerIDRef.current || !inboundMediaSessionIDRef.current) {
                    enqueueSnackbar('Stream not ready', { variant: 'error' });
                    return;
                }

                const queryEndpoint = `${config.streamRecorderEndpoint}/api/v1/replay/stream/query?peerid=${inboundPeerIDRef.current}&mediaSessionId=${inboundMediaSessionIDRef.current}&metadata=false`;

                const queryResponse = await nvAxios.get(queryEndpoint, {
                    headers: { streamId: sensor.streamId || sensor.sensorId },
                });

                if (!queryResponse.data.ts) {
                    enqueueSnackbar('Invalid timestamp received from stream query', { variant: 'error' });
                    return;
                }

                const timestamp = queryResponse.data.ts;
                const utcDateTime = moment(timestamp).toISOString();
                const endpoint = `${config.streamRecorderEndpoint}/api/v1/replay/stream/${sensor.streamId}/picture?startTime=${utcDateTime}`;

                const response = await nvAxios.get(endpoint, {
                    responseType: 'blob',
                    headers: { streamId: sensor.streamId || sensor.sensorId },
                });

                const binaryData = [];
                binaryData.push(response.data);
                const imageUrl = window.URL.createObjectURL(new Blob(binaryData, { type: 'image/jpeg' }));

                // Create a temporary link element
                const link = document.createElement('a');
                link.href = imageUrl;
                link.download = `screenshot_${sensor.streamId}_${utcDateTime}.jpg`;

                // Append to body, click and remove
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);

                // Clean up the object URL
                window.URL.revokeObjectURL(imageUrl);

                enqueueSnackbar('Screenshot saved successfully', {
                    variant: 'success',
                });
            } else {
                // For live streams, use the simpler endpoint
                const endpoint = `${config.liveStreamEndpoint}/api/v1/live/stream/${sensor.streamId}/picture`;

                const response = await nvAxios.get(endpoint, {
                    responseType: 'blob',
                    headers: { streamId: sensor.streamId || sensor.sensorId },
                });

                const binaryData = [];
                binaryData.push(response.data);
                const imageUrl = window.URL.createObjectURL(new Blob(binaryData, { type: 'image/jpeg' }));

                // Create a temporary link element
                const link = document.createElement('a');
                link.href = imageUrl;
                link.download = `screenshot_${sensor.streamId}_${new Date().toISOString()}.jpg`;

                // Append to body, click and remove
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);

                // Clean up the object URL
                window.URL.revokeObjectURL(imageUrl);

                enqueueSnackbar('Screenshot saved successfully', {
                    variant: 'success',
                });
            }
        } catch (error) {
            LOG.error('Error fetching screenshot:', error);
            enqueueSnackbar('Failed to fetch screenshot', { variant: 'error' });
        }
    };

    const handleCalenderDatePickerClose = () => setIsRangeDialogOpen(false);

    const handleDelayedPlaybackByNSeconds = async () => {
        setIsSyncDialogOpen(false);
        if (!inboundMediaSessionIDRef.current || !inboundPeerIDRef.current || !sensor?.streamId) {
            LOG.error('Stream not ready, cant seek');
            return;
        }
        let seconds = 0;
        try {
            seconds = parseInt(delayBySeconds);
        } catch (error) {
            LOG.error('Failed to parse seconds, aborted request');
            return;
        }
        const ISOTimeToSeek = subSeconds(new Date(), seconds).toISOString();
        const isSuccess = await seekToTime(
            inboundPeerIDRef.current,
            inboundMediaSessionIDRef.current,
            ISOTimeToSeek,
            sensor.streamId,
            enqueueSnackbar
        );
        if (!isSuccess) {
            LOG.error('Seek to custom time for delayed playback failed');
        }
    };

    const handleAnalyticsOverlaySave = async (
        settings: { overlay: StreamOverlayOptions; composite?: StreamCompositeOptions; framerate?: number },
        tag?: string
    ) => {
        try {
            LOG.info('Tag value before saving:', tag);
            // Save the overlay settings
            setOverlaySettings(settings.overlay);

            // Clear existing WebRTC stats monitoring interval before restarting
            clearInterval(inboundConnectionQualityWatchdogRef.current);

            // Stop current stream
            await streamManagerRef.current?.stopStreaming();
            setPlaybackSpeed(1);

            // Reset loading state and connection phase
            setIsLoading(true);
            setConnectionPhase('connecting');

            // Create new stream config with updated overlay settings
            const streamConfig: StreamConfig = {
                streamId: sensor?.streamId,
                mainStreamId: sensor?.sensorId,
                options: {
                    rtptransport: 'udp',
                    timeout: 60,
                    quality: quality,
                    ...(settings.framerate != null && { framerate: settings.framerate }),
                    overlay: settings.overlay,
                    ...(settings.composite && {
                        composite: settings.composite,
                    }),
                },
                ...(tag && { tag: tag }),
            };

            LOG.info('Stream config tag:', streamConfig);

            // Preserve start and end times for replay streams
            if (streamType === StreamType.Replay) {
                if (calenderStartTime && calenderEndTime) {
                    streamConfig.startTime = calenderStartTime;
                    streamConfig.endTime = calenderEndTime;
                } else {
                    streamConfig.startTime = getEarliestStartTime();
                }
            }

            // Start new stream with updated settings
            startStream(streamConfig);

            enqueueSnackbar('Analytics overlay settings updated successfully', {
                variant: 'success',
            });
        } catch (error) {
            LOG.error('Failed to update analytics overlay settings:', error);
            enqueueSnackbar('Failed to update analytics overlay settings', {
                variant: 'error',
            });
        }
    };

    const handleQualitySettingChange = async (newQuality: string) => {
        LOG.info('Received quality change request, Waiting for existing stream to stop');

        // Clear existing WebRTC stats monitoring interval before restarting
        clearInterval(inboundConnectionQualityWatchdogRef.current);

        await streamManagerRef.current?.stopStreaming();
        setQuality(newQuality);
        setPlaybackSpeed(1);
        startReplayStreamWithQuality(newQuality);
    };

    const startReplayStreamWithQuality = (newQuality: string) => {
        LOG.info('Starting replay stream with new quality: ', newQuality);
        const streamConfig: StreamConfig = {
            streamId: sensor?.streamId,
            mainStreamId: sensor?.sensorId,
            options: {
                rtptransport: 'udp',
                timeout: 60,
                quality: newQuality,
            },
        };

        // if user has set overlay settings earlier, re-use them
        if (overlaySettings) {
            LOG.info('Re-using previously set overlay settings: ', overlaySettings);
            streamConfig.options.overlay = overlaySettings;
        }

        // For replay streams, persist the start and end time values
        if (streamType === StreamType.Replay) {
            if (calenderStartTime && calenderEndTime) {
                streamConfig.startTime = calenderStartTime;
                streamConfig.endTime = calenderEndTime;
            } else {
                streamConfig.startTime = getEarliestStartTime();
            }
        }

        startStream(streamConfig);
    };

    const handleAnalyticsOverlayToggle = () => {
        setOpenAnalyticsOverlay(prev => !prev);
    };

    // Zoom utility functions
    const calculateVisibleTimeRange = (
        fullStartTime: string,
        fullEndTime: string,
        zoomLevel: number,
        zoomCenter?: number | null
    ): { start: string; end: string } => {
        const fullStartMs = new Date(fullStartTime).getTime();
        const fullEndMs = new Date(fullEndTime).getTime();
        const fullDuration = fullEndMs - fullStartMs;

        // Calculate visible duration based on zoom level
        const visibleDuration = fullDuration / zoomLevel;

        // Determine center point for zoom
        let centerMs: number;
        if (zoomCenter && zoomCenter >= fullStartMs && zoomCenter <= fullEndMs) {
            centerMs = zoomCenter;
        } else {
            // Default to center of full range
            centerMs = fullStartMs + fullDuration / 2;
        }

        // Calculate visible start and end, ensuring they stay within bounds
        let visibleStartMs = centerMs - visibleDuration / 2;
        let visibleEndMs = centerMs + visibleDuration / 2;

        // Adjust if we're going beyond the full range
        if (visibleStartMs < fullStartMs) {
            const offset = fullStartMs - visibleStartMs;
            visibleStartMs = fullStartMs;
            visibleEndMs = Math.min(visibleEndMs + offset, fullEndMs);
        }

        if (visibleEndMs > fullEndMs) {
            const offset = visibleEndMs - fullEndMs;
            visibleEndMs = fullEndMs;
            visibleStartMs = Math.max(visibleStartMs - offset, fullStartMs);
        }

        return {
            start: new Date(visibleStartMs).toISOString(),
            end: new Date(visibleEndMs).toISOString(),
        };
    };

    const getFullTimeRange = (): { start: string; end: string } | null => {
        // Use calendar range if set, otherwise use timeline bounds
        if (calenderStartTime && calenderEndTime) {
            return { start: calenderStartTime, end: calenderEndTime };
        }

        if (timelines && timelines.length > 0) {
            return {
                start: timelines[0].startTime,
                end: timelines[timelines.length - 1].endTime,
            };
        }

        return null;
    };

    const filterEventsInVisibleRange = (events: EventMarker[], visibleStart: string, visibleEnd: string): EventMarker[] => {
        const visibleStartMs = new Date(visibleStart).getTime();
        const visibleEndMs = new Date(visibleEnd).getTime();

        return events.filter(event => event.value >= visibleStartMs && event.value <= visibleEndMs);
    };

    // Utility function to show temporary feedback
    const showZoomFeedback = (message: string) => {
        setZoomFeedback(message);
        setTimeout(() => setZoomFeedback(null), 1500);
    };

    // Zoom control functions
    const handleZoomIn = () => {
        const newZoomLevel = Math.min(zoomLevel * 2, 16); // Max 16x zoom
        if (newZoomLevel === zoomLevel) {
            showZoomFeedback('Maximum zoom reached (16x)');
            return;
        }
        setZoomLevel(newZoomLevel);
        showZoomFeedback(`Zoomed in to ${newZoomLevel.toFixed(1)}x`);
        LOG.info(`Zoomed in to ${newZoomLevel}x`);
    };

    const handleZoomOut = () => {
        const newZoomLevel = Math.max(zoomLevel / 2, 1); // Min 1x zoom (full view)
        if (newZoomLevel === zoomLevel) {
            showZoomFeedback('Already at full view');
            return;
        }
        setZoomLevel(newZoomLevel);
        if (newZoomLevel === 1) {
            setZoomCenter(null); // Reset center when returning to full view
            showZoomFeedback('Zoomed out to full view');
        } else {
            showZoomFeedback(`Zoomed out to ${newZoomLevel.toFixed(1)}x`);
        }
        LOG.info(`Zoomed out to ${newZoomLevel}x`);
    };

    const handleZoomReset = () => {
        if (zoomLevel === 1) {
            showZoomFeedback('Already at full view');
            return;
        }
        setZoomLevel(1);
        setZoomCenter(null);
        showZoomFeedback('Reset to full view');
        LOG.info('Zoom reset to full view');
    };

    const handleZoomToTime = (timestamp: string) => {
        const centerMs = new Date(timestamp).getTime();
        setZoomCenter(centerMs);
        if (zoomLevel === 1) {
            setZoomLevel(2); // Auto-zoom to 2x when focusing on a specific time
        }
        LOG.info(`Zoomed to timestamp: ${timestamp} at ${zoomLevel}x`);
    };

    const handlePan = (direction: 'left' | 'right', amount: number = 0.25) => {
        const fullRange = getFullTimeRange();
        if (!fullRange || zoomLevel === 1) {
            showZoomFeedback('Pan only available when zoomed in');
            return;
        }

        const fullStartMs = new Date(fullRange.start).getTime();
        const fullEndMs = new Date(fullRange.end).getTime();
        const fullDuration = fullEndMs - fullStartMs;
        const visibleDuration = fullDuration / zoomLevel;

        // Calculate pan distance (as percentage of visible duration)
        const panDistance = visibleDuration * amount;
        const currentCenter = zoomCenter || fullStartMs + fullDuration / 2;

        let newCenter: number;
        let atBoundary = false;

        if (direction === 'left') {
            newCenter = currentCenter - panDistance;
            const minCenter = fullStartMs + visibleDuration / 2;
            if (newCenter <= minCenter) {
                newCenter = minCenter;
                atBoundary = true;
            }
        } else {
            newCenter = currentCenter + panDistance;
            const maxCenter = fullEndMs - visibleDuration / 2;
            if (newCenter >= maxCenter) {
                newCenter = maxCenter;
                atBoundary = true;
            }
        }

        setZoomCenter(newCenter);

        if (atBoundary) {
            showZoomFeedback(`Reached ${direction === 'left' ? 'beginning' : 'end'} of timeline`);
        } else {
            showZoomFeedback(`Panned ${direction}`);
        }

        LOG.info(`Panned ${direction} to center: ${new Date(newCenter).toISOString()}`);
    };

    const handleWheelZoom = (event: WheelEvent, cursorPosition?: number) => {
        event.preventDefault();

        const zoomIn = event.deltaY < 0;
        const zoomFactor = 1.4; // Smoother zoom than doubling

        let newZoomLevel: number;
        if (zoomIn) {
            newZoomLevel = Math.min(zoomLevel * zoomFactor, 16);
        } else {
            newZoomLevel = Math.max(zoomLevel / zoomFactor, 1);
        }

        // Check if zoom level actually changed
        if (Math.abs(newZoomLevel - zoomLevel) < 0.1) {
            if (zoomIn && zoomLevel >= 16) {
                showZoomFeedback('Maximum zoom reached');
            } else if (!zoomIn && zoomLevel <= 1) {
                showZoomFeedback('Already at full view');
            }
            return;
        }

        // If cursor position is provided, zoom towards that point
        if (cursorPosition && newZoomLevel > 1) {
            setZoomCenter(cursorPosition);
        } else if (newZoomLevel === 1) {
            setZoomCenter(null); // Reset center when returning to full view
        }

        setZoomLevel(newZoomLevel);
        showZoomFeedback(`${zoomIn ? 'Zoomed in' : 'Zoomed out'} to ${newZoomLevel.toFixed(1)}x`);
        LOG.info(`Wheel zoom: ${newZoomLevel.toFixed(1)}x${cursorPosition ? ` towards ${new Date(cursorPosition).toISOString()}` : ''}`);
    };

    // Effect for keyboard shortcuts and mouse wheel support
    useEffect(() => {
        const handleKeyDown = (event: KeyboardEvent) => {
            // Only handle shortcuts when not typing in input fields
            if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) {
                return;
            }

            switch (event.key.toLowerCase()) {
                case '+':
                case '=':
                    event.preventDefault();
                    handleZoomIn();
                    break;
                case '-':
                    event.preventDefault();
                    handleZoomOut();
                    break;
                case 'r':
                    event.preventDefault();
                    handleZoomReset();
                    break;
                case 'a':
                case 'arrowleft':
                    if (zoomLevel > 1) {
                        event.preventDefault();
                        handlePan('left');
                    }
                    break;
                case 'd':
                case 'arrowright':
                    if (zoomLevel > 1) {
                        event.preventDefault();
                        handlePan('right');
                    }
                    break;
            }
        };

        const handleWheel = (event: WheelEvent) => {
            // Only handle wheel events on the timeline area
            const target = event.target as Element;
            if (target && target.closest('.timeline-container')) {
                handleWheelZoom(event);
            }
        };

        // Add event listeners
        document.addEventListener('keydown', handleKeyDown);
        document.addEventListener('wheel', handleWheel, { passive: false });

        // Cleanup
        return () => {
            document.removeEventListener('keydown', handleKeyDown);
            document.removeEventListener('wheel', handleWheel);
        };
    }, [zoomLevel]);

    // Fetch event image wrapper function
    const fetchEventImage = async (eventTimestamp: string) => {
        await fetchEventImageHook(eventTimestamp, (key: string, loading: boolean) => {
            setLoadingImages(prev => ({ ...prev, [key]: loading }));
        });
    };

    // Handle event marker click - seek to 2 seconds before the marker time
    // Also support double-click to zoom
    const eventClickTimeouts = useRef<Record<string, NodeJS.Timeout>>({});

    const handleEventMarkerClick = async (eventTimestamp: string) => {
        const clickKey = eventTimestamp;

        // Handle double-click detection for zoom
        if (eventClickTimeouts.current[clickKey]) {
            // This is a double-click - zoom to this event
            clearTimeout(eventClickTimeouts.current[clickKey]);
            delete eventClickTimeouts.current[clickKey];

            LOG.info('Double-click detected on event, zooming to timestamp:', eventTimestamp);
            handleZoomToTime(eventTimestamp);
            return;
        }

        // Set timeout for single-click action (seek)
        eventClickTimeouts.current[clickKey] = setTimeout(async () => {
            delete eventClickTimeouts.current[clickKey];

            // Single-click action - seek to event
            LOG.info('STARTING EVENT MARKER SEEK:', {
                eventTimestamp,
                streamType,
                peerId: inboundPeerIDRef.current,
                mediaSessionId: inboundMediaSessionIDRef.current,
            });

            if (streamType === StreamType.Replay && inboundPeerIDRef.current && inboundMediaSessionIDRef.current) {
                try {
                    // Subtract 2 seconds from the event timestamp
                    const seekTime = subSeconds(new Date(eventTimestamp), 2).toISOString();

                    LOG.info('CALCULATED SEEK TIME:', {
                        originalTime: eventTimestamp,
                        seekTime: seekTime,
                        timeDifference: '2 seconds before',
                    });

                    // Make POST request to the seek API
                    const endpoint = `${config.replayStreamEndpoint}/api/v1/replay/stream/seek`;
                    const payload = {
                        action: 'seekForward',
                        mediaSessionId: inboundMediaSessionIDRef.current,
                        peerId: inboundPeerIDRef.current,
                        value: seekTime,
                    };

                    LOG.info('SENDING SEEK API REQUEST:', {
                        endpoint,
                        payload,
                    });

                    const response = await nvAxios.post(endpoint, payload, {
                        ...(sensor && { headers: { streamId: sensor.streamId || sensor.sensorId } }),
                    });

                    LOG.info('SEEK API RESPONSE:', {
                        status: response.status,
                        data: response.data,
                        success: response.status === 200,
                    });

                    if (response.status === 200) {
                        LOG.info('EVENT MARKER SEEK SUCCESS:', {
                            seekTime,
                            formattedTime: format(new Date(seekTime), 'HH:mm:ss'),
                        });
                        LOG.info('Successfully seeked to event time: ', seekTime);
                        enqueueSnackbar(`Seeked to ${format(new Date(seekTime), 'HH:mm:ss')}`, {
                            variant: 'success',
                            autoHideDuration: 3000,
                        });
                    } else {
                        LOG.error('EVENT MARKER SEEK FAILED:', {
                            status: response.status,
                            seekTime,
                        });
                        LOG.error('Failed to seek to event time: ', seekTime);
                        enqueueSnackbar('Failed to seek to event time', { variant: 'error' });
                    }
                } catch (error) {
                    LOG.error('EVENT MARKER SEEK ERROR:', {
                        eventTimestamp,
                        error: error,
                        message: error instanceof Error ? error.message : 'Unknown error',
                    });
                    LOG.error('Error seeking to event time:', error);
                    enqueueSnackbar('Error seeking to event time', { variant: 'error' });
                }
            } else {
                LOG.warn('EVENT MARKER SEEK SKIPPED:', {
                    reason: 'Invalid stream type or missing connection data',
                    streamType,
                    hasInboundPeerId: !!inboundPeerIDRef.current,
                    hasMediaSessionId: !!inboundMediaSessionIDRef.current,
                });
            }
        }, 300); // 300ms delay to detect double-click
    };

    // Portal target for menus/dialogs. In fullscreen, MUI portals default to document.body which
    // sits outside the fullscreen element and is therefore not rendered; anchoring them to the
    // fullscreen wrapper keeps them visible.
    const fullscreenContainer = isActuallyFullScreen ? videoWrapperRef.current : undefined;

    // Shared control cluster (transport controls + analytics/quality settings). Rendered in the
    // card footer while windowed and inside the fullscreen overlay while fullscreen, so exactly
    // one instance exists at a time (avoids duplicate element IDs used by automated tests).
    const controlsCluster = (
        <>
            {(streamType === StreamType.Live || streamType === StreamType.Replay) && (
                <Box sx={{ display: 'flex', gap: 0.5, mr: 1 }} aria-label='Delivery protocol'>
                    <Button
                        size='small'
                        variant={deliveryProtocol === 'webrtc' ? 'contained' : 'outlined'}
                        onClick={() => setDeliveryProtocol('webrtc')}
                    >
                        WebRTC
                    </Button>
                    <Button
                        size='small'
                        variant={deliveryProtocol === 'dash' ? 'contained' : 'outlined'}
                        onClick={() => setDeliveryProtocol('dash')}
                    >
                        DASH
                    </Button>
                </Box>
            )}
            <VideoControls
                playbackStatus={playbackStatus}
                playbackSpeed={playbackSpeed}
                volume={volume}
                isMuted={isMuted}
                streamType={streamType}
                isAudioTrackPresent={isAudioTrackPresent}
                onPlayPause={handlePlayPause}
                onFastForward={() => handleFastForwardAndRewind('fastForward')}
                onRewind={() => handleFastForwardAndRewind('rewind')}
                onSeekForward={handleSeekForward}
                onSeekBackward={handleSeekBackward}
                onVolumeChange={handleVolumeChange}
                onToggleMute={handleMute}
                onScreenshot={handleScreenshot}
                onFullscreen={handleFullScreen}
                onCalendarClick={() => setIsRangeDialogOpen(true)}
                onSyncClick={() => setIsSyncDialogOpen(true)}
                onReplay={handleReplay}
                isFullscreen={isActuallyFullScreen}
            />

            <Box sx={{ display: 'flex', alignItems: 'center' }}>
                <Tooltip title='Analytics Overlay Settings' placement='top'>
                    <IconButton onClick={handleAnalyticsOverlayToggle}>
                        <Settings />
                    </IconButton>
                </Tooltip>
                <QualityMenu
                    onSettingChange={handleQualitySettingChange}
                    currentSetting={quality}
                    show={streamType !== StreamType.VideoWall && deliveryProtocol === 'webrtc'}
                    container={fullscreenContainer}
                    onOpenChange={setIsQualityMenuOpen}
                />
            </Box>
        </>
    );

    // Replay timeline (zoom/pan controls + seek slider). Like controlsCluster it is rendered in the
    // card body while windowed and inside the fullscreen overlay while fullscreen (single instance).
    const showTimeline = streamType === StreamType.Replay && timelines.length > 0 && !isLoadingTimelines;
    // Guard the whole element behind showTimeline: it dereferences timelines[0], which is empty
    // for live streams, so it must not be constructed unless there are timelines.
    const timelineBlock = showTimeline ? (
        <Box className='timeline-container' sx={{ mt: 2 }}>
            {/* Zoom Controls */}
            <Box
                sx={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    mb: 1,
                    px: 1,
                }}
            >
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    {/* Pan Controls - only show when zoomed */}
                    {zoomLevel > 1 && (
                        <Tooltip title='Pan Left (A)' placement='top'>
                            <IconButton
                                size='small'
                                onClick={() => handlePan('left')}
                                sx={{
                                    fontSize: '0.7rem',
                                    minWidth: '24px',
                                    height: '24px',
                                    border: '1px solid',
                                    borderColor: 'divider',
                                    color: 'primary.main',
                                }}
                            >
                                ◀
                            </IconButton>
                        </Tooltip>
                    )}

                    {/* Zoom Controls */}
                    <Tooltip title='Zoom In (+)' placement='top'>
                        <IconButton
                            size='small'
                            onClick={handleZoomIn}
                            disabled={zoomLevel >= 16}
                            sx={{
                                fontSize: '0.8rem',
                                minWidth: '28px',
                                height: '28px',
                                border: '1px solid',
                                borderColor: 'divider',
                            }}
                        >
                            +
                        </IconButton>
                    </Tooltip>
                    <Typography
                        variant='body2'
                        sx={{
                            minWidth: '40px',
                            textAlign: 'center',
                            fontSize: '0.75rem',
                            color: 'text.secondary',
                        }}
                    >
                        {zoomLevel.toFixed(1)}x
                    </Typography>
                    <Tooltip title='Zoom Out (-)' placement='top'>
                        <IconButton
                            size='small'
                            onClick={handleZoomOut}
                            disabled={zoomLevel <= 1}
                            sx={{
                                fontSize: '0.8rem',
                                minWidth: '28px',
                                height: '28px',
                                border: '1px solid',
                                borderColor: 'divider',
                            }}
                        >
                            −
                        </IconButton>
                    </Tooltip>

                    {/* Pan Controls - only show when zoomed */}
                    {zoomLevel > 1 && (
                        <Tooltip title='Pan Right (D)' placement='top'>
                            <IconButton
                                size='small'
                                onClick={() => handlePan('right')}
                                sx={{
                                    fontSize: '0.7rem',
                                    minWidth: '24px',
                                    height: '24px',
                                    border: '1px solid',
                                    borderColor: 'divider',
                                    color: 'primary.main',
                                }}
                            >
                                ▶
                            </IconButton>
                        </Tooltip>
                    )}

                    <Tooltip title='Reset Zoom (R)' placement='top'>
                        <Button
                            size='small'
                            onClick={handleZoomReset}
                            disabled={zoomLevel === 1}
                            sx={{
                                fontSize: '0.65rem',
                                minWidth: '40px',
                                height: '28px',
                                px: 1,
                            }}
                        >
                            Reset
                        </Button>
                    </Tooltip>

                    {/* Help Button */}
                    <Tooltip
                        title={
                            <Box sx={{ p: 0.5 }}>
                                <Typography variant='caption' sx={{ fontWeight: 'bold', display: 'block' }}>
                                    Zoom & Pan Controls:
                                </Typography>
                                <Typography variant='caption' sx={{ display: 'block', mt: 0.5 }}>
                                    • Mouse wheel: Zoom in/out
                                </Typography>
                                <Typography variant='caption' sx={{ display: 'block' }}>
                                    • + / - : Zoom in/out
                                </Typography>
                                <Typography variant='caption' sx={{ display: 'block' }}>
                                    • A / ← : Pan left
                                </Typography>
                                <Typography variant='caption' sx={{ display: 'block' }}>
                                    • D / → : Pan right
                                </Typography>
                                <Typography variant='caption' sx={{ display: 'block' }}>
                                    • R : Reset to full view
                                </Typography>
                                <Typography variant='caption' sx={{ display: 'block', mt: 0.5 }}>
                                    • Double-click event: Zoom to event
                                </Typography>
                            </Box>
                        }
                        placement='left'
                    >
                        <IconButton
                            size='small'
                            sx={{
                                fontSize: '0.7rem',
                                minWidth: '24px',
                                height: '24px',
                                border: '1px solid',
                                borderColor: 'divider',
                                color: 'text.secondary',
                            }}
                        >
                            ?
                        </IconButton>
                    </Tooltip>
                </Box>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                    {/* Zoom Feedback */}
                    {zoomFeedback && (
                        <Box
                            sx={{
                                backgroundColor: 'primary.main',
                                color: 'primary.contrastText',
                                px: 1.5,
                                py: 0.5,
                                borderRadius: 1,
                                fontSize: '0.7rem',
                                fontWeight: 'medium',
                                animation: 'fadeInOut 1.5s ease-in-out',
                                '@keyframes fadeInOut': {
                                    '0%': { opacity: 0, transform: 'scale(0.8)' },
                                    '20%': { opacity: 1, transform: 'scale(1)' },
                                    '80%': { opacity: 1, transform: 'scale(1)' },
                                    '100%': { opacity: 0, transform: 'scale(0.8)' },
                                },
                            }}
                        >
                            {zoomFeedback}
                        </Box>
                    )}

                    {/* Time Range Display */}
                    {visibleTimeRange && (
                        <Typography
                            variant='caption'
                            sx={{
                                color: 'text.secondary',
                                fontSize: '0.65rem',
                            }}
                        >
                            {format(new Date(visibleTimeRange.start), 'HH:mm:ss')} - {format(new Date(visibleTimeRange.end), 'HH:mm:ss')}
                        </Typography>
                    )}
                </Box>
            </Box>

            <TimeRangeSlider
                min={visibleTimeRange?.start || calenderStartTime || timelines[0].startTime}
                max={visibleTimeRange?.end || calenderEndTime || timelines[timelines.length - 1].endTime}
                onSingleTimeSelect={handleTimeRangeSelect}
                singleSelectMode={true}
                disabledRange={disabledIntervals}
                actualRecordingBounds={{
                    start: timelines[0].startTime,
                    end: timelines[timelines.length - 1].endTime,
                }}
                eventMarkers={
                    visibleTimeRange ? filterEventsInVisibleRange(eventMarkers, visibleTimeRange.start, visibleTimeRange.end) : eventMarkers
                }
                onEventMarkerClick={handleEventMarkerClick}
                onFetchEventImage={fetchEventImage}
                getEventImage={getEventImage}
                loadingImages={loadingImages}
                sensorId={sensor?.sensorId}
                onChange={() => {}}
                container={fullscreenContainer}
            />
        </Box>
    ) : null;

    return (
        <Card
            sx={{
                // Subtle entrance: gentle fade + rise when the player mounts.
                animation: 'vstPlayerEnter 280ms ease-out both',
                '@keyframes vstPlayerEnter': {
                    from: { opacity: 0, transform: 'translateY(6px) scale(0.995)' },
                    to: { opacity: 1, transform: 'translateY(0) scale(1)' },
                },
                '@media (prefers-reduced-motion: reduce)': {
                    animation: 'none',
                },
            }}
        >
            <Box
                sx={{
                    display: 'flex',
                    flexDirection: 'column',
                    padding: '16px',
                    minHeight: '72px',
                    borderBottom: '1px solid',
                    borderColor: 'divider',
                }}
            >
                <Box
                    sx={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        minWidth: 0,
                        marginBottom: '4px',
                        width: '100%',
                    }}
                >
                    <Tooltip
                        title={streamType === StreamType.VideoWall ? `Streaming ${sensors?.length} sensors` : sensor?.name || ''}
                        placement='top'
                        arrow
                    >
                        <Typography
                            variant='h6'
                            component='div'
                            sx={{
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                                whiteSpace: 'nowrap',
                                minWidth: 0,
                                maxWidth: '250px',
                                flex: '0 0 auto',
                            }}
                        >
                            {streamType === StreamType.VideoWall ? `Streaming ${sensors?.length} sensors` : sensor?.name}
                        </Typography>
                    </Tooltip>
                    <Box
                        sx={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 1,
                            flexShrink: 0,
                            minWidth: 'fit-content',
                        }}
                    >
                        {streamType === StreamType.Replay && (
                            <Button
                                size='small'
                                variant='outlined'
                                onClick={handleShowEventsClick}
                                sx={{
                                    mr: 1,
                                    fontSize: '0.75rem',
                                    height: '28px',
                                    color: isFetchingEvents ? 'error.main' : 'primary.main',
                                    borderColor: isFetchingEvents ? 'error.main' : 'primary.main',
                                    '&:hover': {
                                        borderColor: isFetchingEvents ? 'error.dark' : 'primary.dark',
                                        backgroundColor: isFetchingEvents ? 'error.light' : 'action.hover',
                                    },
                                }}
                            >
                                {isFetchingEvents ? 'Stop Loading' : 'Show Events'}
                            </Button>
                        )}
                        {deliveryProtocol === 'webrtc' && <BitrateSparkline bitrate={bitrate} />}
                        {onClose && (
                            <IconButton
                                onClick={onClose}
                                aria-label='close'
                                size='small'
                                sx={{
                                    backgroundColor: 'background.paper',
                                    '&:hover': {
                                        backgroundColor: 'action.hover',
                                    },
                                }}
                            >
                                <Close fontSize='small' />
                            </IconButton>
                        )}
                    </Box>
                </Box>
                <Tooltip
                    title={
                        streamType === StreamType.VideoWall ? `Type: ${streamType}` : `Type: ${streamType} | Sensor ID: ${sensor?.sensorId}`
                    }
                    placement='bottom'
                    arrow
                >
                    <Typography
                        variant='body2'
                        color='text.secondary'
                        sx={{
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                            maxWidth: '350px',
                        }}
                    >
                        {streamType === StreamType.VideoWall
                            ? `Type: ${streamType}`
                            : `Type: ${streamType} | Sensor ID: ${sensor?.sensorId}`}
                    </Typography>
                </Tooltip>
            </Box>
            <CardContent>
                {/* Analytics Drawing Components */}
                <Analytics
                    sensor={sensor}
                    streamType={streamType}
                    videoRef={videoRef}
                    videoWidth={videoWidth}
                    videoHeight={videoHeight}
                    enqueueSnackbar={enqueueSnackbar}
                >
                    {({ canvasOverlay }) => (
                        <>
                            <Box sx={muiStyles.videoContainer}>
                                <Box
                                    ref={videoWrapperRef}
                                    sx={muiStyles.videoWrapper}
                                    onMouseMove={showControls}
                                    onTouchStart={showControls}
                                    onMouseLeave={() => {
                                        // Only auto-hide on leave when nothing is open; never hide the
                                        // cursor/controls while a dialog or menu is in use.
                                        if (isActuallyFullScreen && !isAnyOverlayDialogOpen) {
                                            setControlsVisible(false);
                                        }
                                    }}
                                >
                                    <video
                                        ref={videoRef}
                                        id={videoElementId}
                                        className='vst-video-player'
                                        onLoadedMetadata={handleVideoMetadata}
                                        style={htmlStyles.video}
                                    >
                                        Your browser does not support the video tag.
                                    </video>

                                    {/* Drawing Canvas Overlay - positioned directly over video */}
                                    {canvasOverlay}

                                    {/* Fullscreen overlay controls - mounted only while actually in
                                        fullscreen, auto-hiding after mouse inactivity (YouTube/VLC style). */}
                                    {isActuallyFullScreen && (
                                        <Box
                                            data-testid='fullscreen-controls-overlay'
                                            onMouseMove={showControls}
                                            onMouseEnter={showControls}
                                            onTouchStart={showControls}
                                            sx={{
                                                position: 'absolute',
                                                bottom: 0,
                                                left: 0,
                                                right: 0,
                                                // Below MUI's menu/dialog layer (1300) so the quality menu
                                                // and dialogs render above the controls/timeline, but above
                                                // the video, canvas, and network widget (<=1001).
                                                zIndex: 1100,
                                                display: 'flex',
                                                flexDirection: 'column',
                                                width: '100%',
                                                px: 2,
                                                pt: 4,
                                                pb: 1,
                                                background: 'linear-gradient(to top, rgba(0, 0, 0, 0.85) 0%, rgba(0, 0, 0, 0) 100%)',
                                                color: 'common.white',
                                                opacity: controlsVisible ? 1 : 0,
                                                transform: controlsVisible ? 'translateY(0)' : 'translateY(100%)',
                                                transition: 'opacity 0.3s ease, transform 0.3s ease',
                                                pointerEvents: controlsVisible ? 'auto' : 'none',
                                            }}
                                        >
                                            {/* Replay timeline above the transport controls (single instance: see timelineBlock) */}
                                            {showTimeline && (
                                                <Box
                                                    sx={{
                                                        width: '100%',
                                                        mb: 1,
                                                        p: 1,
                                                        borderRadius: 1,
                                                        backgroundColor: 'rgba(0, 0, 0, 0.45)',
                                                    }}
                                                >
                                                    {timelineBlock}
                                                </Box>
                                            )}
                                            <Box sx={{ display: 'flex', alignItems: 'center', width: '100%' }}>{controlsCluster}</Box>
                                        </Box>
                                    )}

                                    <VideoWallPlaybackHandler
                                        videoRef={videoRef}
                                        streamType={streamType}
                                        setPlaybackStatus={setPlaybackStatus}
                                    />
                                    {deliveryProtocol === 'webrtc' && webRTCStats && (
                                        <NetworkQualityWidget
                                            stats={webRTCStats}
                                            sensorName={sensor?.name}
                                            playbackStatus={playbackStatus}
                                        />
                                    )}
                                    {(isLoading || hasError) &&
                                        (hasError ? (
                                            <Box sx={muiStyles.errorOverlay}>
                                                <ErrorOutlineIcon sx={{ fontSize: 60, mb: 1 }} />
                                                <Typography variant='h6'>Playback Error</Typography>
                                                {errorDetails && (
                                                    <Typography variant='body1'>
                                                        Error {errorDetails.code}: {errorDetails.message}
                                                    </Typography>
                                                )}
                                            </Box>
                                        ) : (
                                            <Box sx={muiStyles.loadingOverlay}>
                                                {!(
                                                    startTimeMs.current &&
                                                    endTimeMs.current &&
                                                    playbackStatus === StreamState.NOT_PLAYING
                                                ) && <CircularProgress size={60} thickness={4} sx={{ color: 'white', mb: 2 }} />}
                                                <Typography variant='h6' sx={{ color: 'white' }}>
                                                    {connectionPhase === 'connecting'
                                                        ? 'WebRTC Connecting...'
                                                        : startTimeMs.current &&
                                                            endTimeMs.current &&
                                                            playbackStatus === StreamState.NOT_PLAYING
                                                          ? 'Stream Ended'
                                                          : 'Waiting For Data...'}
                                                </Typography>
                                            </Box>
                                        ))}
                                </Box>
                            </Box>
                        </>
                    )}
                </Analytics>

                {calenderStartTime && calenderEndTime && <LinearProgress variant='determinate' value={percentagePlayback} />}

                {/* Loading indicator for MMS timeline fetch */}
                {isLoadingTimelines && streamType === StreamType.Replay && (
                    <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', mt: 2, mb: 2 }}>
                        <CircularProgress size={40} />
                        <Typography variant='body2' sx={{ mt: 1, color: 'text.secondary' }}>
                            Loading timelines...
                        </Typography>
                    </Box>
                )}

                {showTimeline && !isActuallyFullScreen && timelineBlock}
            </CardContent>
            <Divider orientation='horizontal' flexItem />
            {/* Footer controls: rendered while windowed. In fullscreen the same cluster is rendered
                as an auto-hiding overlay inside the video wrapper, so only one instance exists. */}
            {!isActuallyFullScreen && (
                <CardActions disableSpacing sx={{ display: 'flex', justifyContent: 'space-between' }}>
                    <Box sx={muiStyles.controls}>{controlsCluster}</Box>
                </CardActions>
            )}
            <Dialog open={isSyncDialogOpen} onClose={() => setIsSyncDialogOpen(false)} container={fullscreenContainer}>
                <DialogTitle>Sync to Time</DialogTitle>
                <DialogContent>
                    <TextField
                        fullWidth
                        label='Time(seconds)'
                        value={delayBySeconds}
                        onChange={e => setDelayBySeconds(e.target.value)}
                        helperText='The playback will be started from end of recording with above user provided buffer'
                        margin='normal'
                    />
                </DialogContent>
                <DialogActions>
                    <Button onClick={handleDelayedPlaybackByNSeconds}>Submit</Button>
                    <Button onClick={() => setIsSyncDialogOpen(false)}>Close</Button>
                </DialogActions>
            </Dialog>

            <RangePickerDialog
                open={isRangeDialogOpen}
                onClose={handleCalenderDatePickerClose}
                onSubmit={handleCalenderRangePlayback}
                container={fullscreenContainer}
            />

            {streamType !== StreamType.Streambridge && (
                <AnalyticsOverlayDialog
                    open={openAnalyticsOverlay}
                    onClose={handleAnalyticsOverlayToggle}
                    onSave={handleAnalyticsOverlaySave}
                    sensors={sensors}
                    streamType={streamType}
                    container={fullscreenContainer}
                />
            )}
        </Card>
    );
};

export default React.memo(VideoPlayer);

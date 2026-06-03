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
import React from 'react';
import { IconButton, Slider, Box, useMediaQuery, useTheme, Typography, Tooltip } from '@mui/material';
import {
    PlayArrow,
    Pause,
    FastRewind,
    FastForward,
    Forward10,
    Replay10,
    VolumeUp,
    VolumeOff,
    PhotoCamera,
    Fullscreen,
    DateRange,
    AccessTime,
    Replay,
} from '@mui/icons-material';
import { StreamState, StreamType } from 'vst-streaming-lib';

interface VideoControlsProps {
    playbackStatus: StreamState;
    playbackSpeed: number;
    volume: number;
    isMuted: boolean;
    streamType: string;
    isAudioTrackPresent: boolean;
    onPlayPause: () => void;
    onFastForward: () => void;
    onRewind: () => void;
    onSeekForward: () => void;
    onSeekBackward: () => void;
    onVolumeChange: (event: Event, newValue: number | number[]) => void;
    onToggleMute: () => void;
    onScreenshot: () => void;
    onFullscreen: () => void;
    onCalendarClick: () => void;
    onSyncClick: () => void;
    onReplay: () => void;
}

const VideoControls: React.FC<VideoControlsProps> = ({
    playbackStatus,
    playbackSpeed,
    volume,
    isMuted,
    streamType,
    isAudioTrackPresent,
    onPlayPause,
    onFastForward,
    onRewind,
    onSeekForward,
    onSeekBackward,
    onVolumeChange,
    onToggleMute,
    onScreenshot,
    onFullscreen,
    onCalendarClick,
    onSyncClick,
    onReplay,
}) => {
    const theme = useTheme();
    const isXsScreen = useMediaQuery(theme.breakpoints.down('sm'));
    const isSmScreen = useMediaQuery(theme.breakpoints.down('md'));
    const isMdScreen = useMediaQuery(theme.breakpoints.down('lg'));

    return (
        <Box
            sx={{
                display: 'flex',
                alignItems: 'center',
                width: '100%',
                justifyContent: 'space-between',
                overflow: 'hidden',
            }}
        >
            {/* Left side controls */}
            <Box sx={{ display: 'flex', alignItems: 'center' }}>
                <Tooltip title={playbackStatus === StreamState.PLAYING ? 'Pause' : 'Play'}>
                    <IconButton onClick={onPlayPause} size='small' id='play-pause-control-btn'>
                        {playbackStatus === StreamState.PLAYING ? <Pause /> : <PlayArrow />}
                    </IconButton>
                </Tooltip>

                {isAudioTrackPresent && !isSmScreen && (
                    <Box
                        sx={{
                            display: 'flex',
                            alignItems: 'center',
                            width: isMdScreen ? '80px' : '100px',
                            mx: 1,
                        }}
                    >
                        <Tooltip title={isMuted ? 'Unmute' : 'Mute'}>
                            <IconButton onClick={onToggleMute} size='small' id='mute-control-btn'>
                                {isMuted ? <VolumeOff /> : <VolumeUp />}
                            </IconButton>
                        </Tooltip>
                        <Slider
                            value={volume}
                            onChange={onVolumeChange}
                            aria-labelledby='volume-slider'
                            min={0}
                            max={100}
                            size='small'
                            sx={{
                                color: '#888888',
                                mx: 1,
                                width: isMdScreen ? '50px' : '70px',
                            }}
                        />
                    </Box>
                )}
            </Box>

            {/* Right side controls */}
            <Box
                sx={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 0.5,
                }}
            >
                {streamType === StreamType.Replay && (
                    <>
                        <Tooltip title='Replay'>
                            <IconButton onClick={onReplay} size='small' id='replay-control-btn'>
                                <Replay />
                            </IconButton>
                        </Tooltip>
                        <Tooltip title='Rewind'>
                            <IconButton
                                onClick={onRewind}
                                disabled={playbackStatus === StreamState.NOT_PLAYING}
                                size='small'
                                sx={{ position: 'relative' }}
                                id='rewind-control-btn'
                            >
                                <Box sx={{ display: 'flex', alignItems: 'baseline' }}>
                                    <FastRewind />
                                    {playbackSpeed < 0 && (
                                        <Typography
                                            variant='caption'
                                            sx={{
                                                fontSize: '0.7rem',
                                                lineHeight: 1,
                                                marginLeft: '-4px',
                                                marginBottom: '-4px',
                                            }}
                                        >
                                            {Math.abs(playbackSpeed)}x
                                        </Typography>
                                    )}
                                </Box>
                            </IconButton>
                        </Tooltip>
                        <Tooltip title='Fast Forward'>
                            <IconButton
                                onClick={onFastForward}
                                disabled={playbackStatus === StreamState.NOT_PLAYING}
                                size='small'
                                sx={{ position: 'relative' }}
                                id='fast-forward-control-btn'
                            >
                                <Box sx={{ display: 'flex', alignItems: 'baseline' }}>
                                    <FastForward />
                                    {playbackSpeed > 1 && (
                                        <Typography
                                            variant='caption'
                                            sx={{
                                                fontSize: '0.7rem',
                                                lineHeight: 1,
                                                marginLeft: '-4px',
                                                marginBottom: '-4px',
                                            }}
                                        >
                                            {playbackSpeed}x
                                        </Typography>
                                    )}
                                </Box>
                            </IconButton>
                        </Tooltip>
                        {!isXsScreen && (
                            <>
                                <Tooltip title='Seek Backward 10s'>
                                    <IconButton
                                        onClick={onSeekBackward}
                                        disabled={playbackStatus === StreamState.NOT_PLAYING}
                                        size='small'
                                        id='seek-backward-control-btn'
                                    >
                                        <Replay10 />
                                    </IconButton>
                                </Tooltip>
                                <Tooltip title='Seek Forward 10s'>
                                    <IconButton
                                        onClick={onSeekForward}
                                        disabled={playbackStatus === StreamState.NOT_PLAYING}
                                        size='small'
                                        id='seek-forward-control-btn'
                                    >
                                        <Forward10 />
                                    </IconButton>
                                </Tooltip>
                            </>
                        )}
                    </>
                )}

                {!isXsScreen && streamType !== StreamType.VideoWall && (
                    <Tooltip title='Take Screenshot'>
                        <IconButton onClick={onScreenshot} size='small' id='screenshot-control-btn'>
                            <PhotoCamera />
                        </IconButton>
                    </Tooltip>
                )}

                {streamType === StreamType.Replay && !isSmScreen && (
                    <>
                        <Tooltip title='Calendar'>
                            <IconButton onClick={onCalendarClick} size='small' id='calendar-control-btn'>
                                <DateRange />
                            </IconButton>
                        </Tooltip>
                        <Tooltip title='Sync'>
                            <IconButton onClick={onSyncClick} size='small' id='sync-control-btn'>
                                <AccessTime />
                            </IconButton>
                        </Tooltip>
                    </>
                )}

                <Tooltip title='Fullscreen'>
                    <IconButton onClick={onFullscreen} size='small' id='fullscreen-control-btn'>
                        <Fullscreen />
                    </IconButton>
                </Tooltip>
            </Box>
        </Box>
    );
};

export default VideoControls;

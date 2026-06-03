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
import React, { useEffect } from 'react';
import { StreamState } from 'vst-streaming-lib';

interface VideoWallPlaybackHandlerProps {
    videoRef: React.RefObject<HTMLVideoElement>;
    streamType: string;
    setPlaybackStatus: (status: StreamState) => void;
}

const VideoWallPlaybackHandler: React.FC<VideoWallPlaybackHandlerProps> = ({ videoRef, streamType, setPlaybackStatus }) => {
    useEffect(() => {
        if (streamType === 'VideoWall' && videoRef.current) {
            const handleVideoPlay = () => {
                setPlaybackStatus(StreamState.PLAYING);
            };
            const handleVideoPause = () => {
                setPlaybackStatus(StreamState.PAUSED);
            };

            videoRef.current.addEventListener('play', handleVideoPlay);
            videoRef.current.addEventListener('pause', handleVideoPause);

            return () => {
                if (videoRef.current) {
                    videoRef.current.removeEventListener('play', handleVideoPlay);
                    videoRef.current.removeEventListener('pause', handleVideoPause);
                }
            };
        }
    }, [streamType, videoRef, setPlaybackStatus]);

    return null;
};

export default VideoWallPlaybackHandler;

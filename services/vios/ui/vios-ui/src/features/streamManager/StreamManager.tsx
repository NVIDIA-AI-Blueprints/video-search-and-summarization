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
import { StreamType } from 'vst-streaming-lib';
import VideoPlayer from '../../components/videoPlayer/VideoPlayer';
import { Sensor } from '../../interfaces/interfaces';

const VSTStreamManager: React.FC<{
    sensor?: Sensor;
    streamType: StreamType;
    onWebRTCStatsUpdate?: (stats: RTCStatsReport) => void;
    sensors?: Sensor[];
    onClose?: () => void;
}> = ({ sensor, streamType, onWebRTCStatsUpdate, sensors, onClose }) => {
    const videoElementId = streamType !== StreamType.VideoWall ? `video-${streamType}-${sensor?.streamId}` : 'video-wall-streaming';

    return (
        <div>
            {streamType === StreamType.VideoWall && sensors && sensors.length > 0 && (
                <VideoPlayer
                    sensors={sensors}
                    streamType={streamType}
                    videoElementId={videoElementId}
                    onWebRTCStatsUpdate={onWebRTCStatsUpdate}
                    onClose={onClose}
                />
            )}
            {streamType !== StreamType.VideoWall && sensor && (
                <VideoPlayer
                    sensor={sensor}
                    streamType={streamType}
                    videoElementId={videoElementId}
                    onWebRTCStatsUpdate={onWebRTCStatsUpdate}
                    onClose={onClose}
                />
            )}
        </div>
    );
};

export default VSTStreamManager;

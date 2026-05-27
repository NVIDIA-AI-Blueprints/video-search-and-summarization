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
import { useState, useEffect, useRef } from 'react';

interface BitrateStats {
    lastBytesReceived: number;
    lastTimestamp: number;
}

export const useBitrate = (fullStats?: RTCStatsReport) => {
    const [bitrate, setBitrate] = useState<number>(0);
    const statsRef = useRef<BitrateStats>({
        lastBytesReceived: 0,
        lastTimestamp: 0,
    });

    useEffect(() => {
        if (!fullStats) {
            console.log('useBitrate: No stats available');
            return;
        }

        const checkBitrate = () => {
            try {
                let foundInboundRtp = false;

                fullStats.forEach(report => {
                    // Look for inbound-rtp stats for video
                    if (report.type === 'inbound-rtp' && report.kind === 'video') {
                        foundInboundRtp = true;
                        const { lastBytesReceived, lastTimestamp } = statsRef.current;
                        if (lastTimestamp) {
                            const currentBitrate =
                                (8 * (report.bytesReceived - lastBytesReceived)) / ((report.timestamp - lastTimestamp) / 1000); // bits per second
                            setBitrate(Math.round(currentBitrate));
                        } else {
                            console.log('useBitrate: First measurement, waiting for next sample');
                        }
                        statsRef.current = {
                            lastBytesReceived: report.bytesReceived,
                            lastTimestamp: report.timestamp,
                        };
                    }
                });

                if (!foundInboundRtp) {
                    console.log('useBitrate: No inbound-rtp video stats found');
                }
            } catch (error) {
                console.error('useBitrate: Error calculating bitrate:', error);
            }
        };

        // Check bitrate whenever stats update
        checkBitrate();
    }, [fullStats]);

    return bitrate;
};

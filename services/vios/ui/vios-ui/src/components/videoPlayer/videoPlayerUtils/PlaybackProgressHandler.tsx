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
import { useCallback, useEffect, useRef } from 'react';
import debounce from 'lodash/debounce';
import { Timeline } from '../../../interfaces/interfaces';

interface UsePlaybackProgressProps {
    startTimeMs: React.MutableRefObject<number | null>;
    endTimeMs: React.MutableRefObject<number | null>;
    setPercentagePlayback: (percentage: number) => void;
    timelines?: Timeline[]; // Add timeline data to calculate accurate progress
}

// Helper function to calculate total playable duration within a time range
const calculatePlayableDuration = (timelines: Timeline[], startTimeMs: number, endTimeMs: number): number => {
    if (!timelines || timelines.length === 0) {
        return endTimeMs - startTimeMs; // Fallback to original behavior
    }

    let totalPlayableDuration = 0;

    for (const timeline of timelines) {
        const timelineStartMs = new Date(timeline.startTime).getTime();
        const timelineEndMs = new Date(timeline.endTime).getTime();

        // Find intersection between timeline segment and selected range
        const intersectionStart = Math.max(timelineStartMs, startTimeMs);
        const intersectionEnd = Math.min(timelineEndMs, endTimeMs);

        // If there's a valid intersection, add its duration
        if (intersectionStart < intersectionEnd) {
            totalPlayableDuration += intersectionEnd - intersectionStart;
        }
    }

    return totalPlayableDuration;
};

// Helper function to calculate how much playable content has been consumed up to current timestamp
const calculatePlayedDuration = (timelines: Timeline[], startTimeMs: number, currentTimeMs: number): number => {
    if (!timelines || timelines.length === 0) {
        return Math.max(0, currentTimeMs - startTimeMs); // Fallback to original behavior
    }

    let totalPlayedDuration = 0;

    for (const timeline of timelines) {
        const timelineStartMs = new Date(timeline.startTime).getTime();
        const timelineEndMs = new Date(timeline.endTime).getTime();

        // Find intersection between timeline segment and range from start to current time
        const intersectionStart = Math.max(timelineStartMs, startTimeMs);
        const intersectionEnd = Math.min(timelineEndMs, Math.max(currentTimeMs, startTimeMs));

        // If there's a valid intersection, add its duration
        if (intersectionStart < intersectionEnd) {
            totalPlayedDuration += intersectionEnd - intersectionStart;
        }
    }

    return totalPlayedDuration;
};

const usePlaybackProgress = ({ startTimeMs, endTimeMs, setPercentagePlayback, timelines }: UsePlaybackProgressProps) => {
    // Create a ref to store the debounced callback
    const onPlaybackTimeUpdateRef = useRef<ReturnType<typeof debounce> | null>(null);

    // Create the debounced callback
    useEffect(() => {
        onPlaybackTimeUpdateRef.current = debounce((ts: number) => {
            if (startTimeMs.current && endTimeMs.current) {
                const startMs = startTimeMs.current;
                const endMs = endTimeMs.current;

                // Calculate total playable duration (excluding gaps)
                const totalPlayableDuration = calculatePlayableDuration(timelines || [], startMs, endMs);

                // Calculate how much playable content has been consumed
                const playedDuration = calculatePlayedDuration(timelines || [], startMs, ts);

                // Calculate percentage based on playable content
                const percentage = totalPlayableDuration > 0 ? (playedDuration / totalPlayableDuration) * 100 : 0;

                // Ensure percentage is within valid bounds
                setPercentagePlayback(Math.min(Math.max(percentage, 0), 100));
            }
        }, 100);

        // Cleanup function
        return () => {
            if (onPlaybackTimeUpdateRef.current) {
                onPlaybackTimeUpdateRef.current.cancel();
            }
        };
    }, [startTimeMs, endTimeMs, setPercentagePlayback, timelines]);

    // Expose the callback to parent component
    const onPlaybackTimeUpdate = useCallback((ts: number) => {
        if (onPlaybackTimeUpdateRef.current) {
            onPlaybackTimeUpdateRef.current(ts);
        }
    }, []);

    return { onPlaybackTimeUpdate };
};

export default usePlaybackProgress;

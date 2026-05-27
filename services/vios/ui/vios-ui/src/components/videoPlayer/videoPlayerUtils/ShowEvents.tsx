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
import { useRef, useCallback, useEffect } from 'react';
import { format } from 'date-fns';
import { useSnackbar } from 'notistack';
import nvAxios from '../../../services/Axios';
import config from '../../../config';
import { EventMarker } from '../../../features/timeSlider/TimeSlider';
import { Timeline } from '../../../interfaces/interfaces';

export interface ShowEventsProps {
    sensorId?: string;
    sensorName?: string;
    timelines: Timeline[];
    calenderStartTime?: string;
    calenderEndTime?: string;
    onEventsUpdate: (events: EventMarker[]) => void;
    onFetchingStateChange: (isFetching: boolean) => void;
}

export interface EventImagesState {
    loadingImages: Record<string, boolean>;
    eventImagesRef: React.MutableRefObject<Record<string, string>>;
}

// Utility function to break a single time range into chunks
const createTimeChunks = (startTime: string, endTime: string, chunkMinutes: number = 5): Array<{ start: string; end: string }> => {
    const chunks = [];
    const startMs = new Date(startTime).getTime();
    const endMs = new Date(endTime).getTime();
    const chunkMs = chunkMinutes * 60 * 1000; // Convert minutes to milliseconds

    // Handle edge case: if the segment is smaller than chunk size, return it as a single chunk
    if (endMs - startMs <= chunkMs) {
        chunks.push({
            start: startTime,
            end: endTime,
        });
        return chunks;
    }

    let currentStart = startMs;

    while (currentStart < endMs) {
        const currentEnd = Math.min(currentStart + chunkMs, endMs);
        chunks.push({
            start: new Date(currentStart).toISOString(),
            end: new Date(currentEnd).toISOString(),
        });
        currentStart = currentEnd;
    }

    return chunks;
};

// Utility function to create chunks from multiple timeline segments
// Only creates chunks for the last 4 hours
const createChunksFromTimelines = (
    timelines: Timeline[],
    chunkMinutes: number = 2
): Array<{ start: string; end: string; segmentIndex: number }> => {
    const allChunks: Array<{ start: string; end: string; segmentIndex: number }> = [];

    // Calculate 4-hour cutoff time from current time
    // This ensures we only fetch events from the last 4 hours (events API limitation)
    const now = new Date();
    const fourHoursAgo = new Date(now.getTime() - 4 * 60 * 60 * 1000);
    const cutoffTimeMs = fourHoursAgo.getTime();

    console.log(`Current time: ${now.toISOString()}`);
    console.log(`4-hour cutoff (from now): ${fourHoursAgo.toISOString()}`);

    timelines.forEach((timeline, segmentIndex) => {
        const timelineStartMs = new Date(timeline.startTime).getTime();
        const timelineEndMs = new Date(timeline.endTime).getTime();

        // Skip entire timeline if it ends before the 4-hour cutoff
        if (timelineEndMs < cutoffTimeMs) {
            console.log(
                `Skipping timeline segment ${segmentIndex + 1} (${timeline.startTime} to ${timeline.endTime}) - older than 4 hours`
            );
            return;
        }

        // Trim timeline start time if it begins before the 4-hour cutoff
        let adjustedStartTime = timeline.startTime;
        if (timelineStartMs < cutoffTimeMs) {
            adjustedStartTime = fourHoursAgo.toISOString();
            console.log(`Trimming timeline segment ${segmentIndex + 1} start time from ${timeline.startTime} to ${adjustedStartTime}`);
        }

        const segmentChunks = createTimeChunks(adjustedStartTime, timeline.endTime, chunkMinutes);

        // Add segment index to each chunk for tracking
        segmentChunks.forEach(chunk => {
            allChunks.push({
                ...chunk,
                segmentIndex,
            });
        });
    });

    return allChunks;
};

// Helper function to safely extract string values from unknown types
const getStringValue = (obj: Record<string, unknown>, key: string, defaultValue: string): string => {
    const value = obj[key];
    return typeof value === 'string' ? value : defaultValue;
};

// Process events from API response into EventMarkers
const processEventsToMarkers = (
    tripwireEvents: unknown[],
    roiEvents: unknown[],
    chunkStartMs: number,
    chunkEndMs: number
): EventMarker[] => {
    // Process tripwire events
    const tripwireMarkers = tripwireEvents
        .map((event: unknown, idx: number) => {
            const eventData = event as Record<string, unknown>;
            const eventTimestamp = eventData.timestamp;
            if (!eventTimestamp || typeof eventTimestamp !== 'string') {
                console.warn(`Tripwire event ${idx} missing or invalid timestamp:`, event);
                return null;
            }

            const epoch = new Date(eventTimestamp).getTime();
            if (isNaN(epoch)) {
                console.warn(`Invalid tripwire timestamp for event ${idx}:`, eventTimestamp);
                return null;
            }

            if (epoch < chunkStartMs || epoch > chunkEndMs) {
                return null;
            }

            const objectData = (eventData.object as Record<string, unknown>) || {};
            const eventInfo = (eventData.event as Record<string, unknown>) || {};

            return {
                value: epoch,
                label: format(new Date(epoch), 'HH:mm'),
                timestamp: eventTimestamp,
                color: '#f44336', // Red for tripwire
                description: `${getStringValue(objectData, 'type', 'object')} ${getStringValue(eventInfo, 'type', 'UNKNOWN')} ${getStringValue(eventInfo, 'id', 'unknown')}`,
            };
        })
        .filter(Boolean);

    // Process ROI events
    const roiMarkers = roiEvents
        .map((event: unknown, idx: number) => {
            const eventData = event as Record<string, unknown>;
            const eventTimestamp = eventData.timestamp;
            if (!eventTimestamp || typeof eventTimestamp !== 'string') {
                console.warn(`ROI event ${idx} missing or invalid timestamp:`, event);
                return null;
            }

            const epoch = new Date(eventTimestamp).getTime();
            if (isNaN(epoch)) {
                console.warn(`Invalid ROI timestamp for event ${idx}:`, eventTimestamp);
                return null;
            }

            if (epoch < chunkStartMs || epoch > chunkEndMs) {
                return null;
            }

            const objectData = (eventData.object as Record<string, unknown>) || {};
            const eventInfo = (eventData.event as Record<string, unknown>) || {};

            return {
                value: epoch,
                label: format(new Date(epoch), 'HH:mm'),
                timestamp: eventTimestamp,
                color: '#f44336', // Red for ROI
                description: `${getStringValue(objectData, 'type', 'object')} ${getStringValue(eventInfo, 'type', 'UNKNOWN')} ${getStringValue(eventInfo, 'id', 'unknown')}`,
            };
        })
        .filter(Boolean);

    return [...tripwireMarkers, ...roiMarkers] as EventMarker[];
};

// Fetch events for a single time chunk
const fetchEventsForChunk = async (
    chunkStart: string,
    chunkEnd: string,
    sensorName: string,
    abortController: AbortController
): Promise<EventMarker[]> => {
    const tripwireEndpoint = `${config.mdatWebApiEndpoint}/events/tripwire?sensorId=${sensorName}&fromTimestamp=${chunkStart}&toTimestamp=${chunkEnd}`;
    const roiEndpoint = `${config.mdatWebApiEndpoint}/events/roi?sensorId=${sensorName}&fromTimestamp=${chunkStart}&toTimestamp=${chunkEnd}`;

    // Fetch both tripwire and ROI events in parallel for this chunk
    const [tripwireResult, roiResult] = await Promise.allSettled([
        nvAxios.get(tripwireEndpoint, { signal: abortController.signal, headers: { streamId: sensorName } }),
        nvAxios.get(roiEndpoint, { signal: abortController.signal, headers: { streamId: sensorName } }),
    ]);

    // Extract events, handling failures gracefully
    const tripwireEvents = tripwireResult.status === 'fulfilled' ? tripwireResult.value.data?.tripwireEvents || [] : [];

    const roiEvents = roiResult.status === 'fulfilled' ? roiResult.value.data?.roiEvents || [] : [];

    // Log any failures
    if (tripwireResult.status === 'rejected') {
        console.error(`Tripwire API failed for chunk ${chunkStart}-${chunkEnd}:`, tripwireResult.reason);
    }
    if (roiResult.status === 'rejected') {
        console.error(`ROI API failed for chunk ${chunkStart}-${chunkEnd}:`, roiResult.reason);
    }

    const chunkStartMs = new Date(chunkStart).getTime();
    const chunkEndMs = new Date(chunkEnd).getTime();

    return processEventsToMarkers(tripwireEvents, roiEvents, chunkStartMs, chunkEndMs);
};

// Custom hook for managing events
export const useShowEvents = (props: ShowEventsProps) => {
    const { sensorName, timelines, calenderStartTime, calenderEndTime, onEventsUpdate, onFetchingStateChange } = props;
    const { enqueueSnackbar } = useSnackbar();
    const eventFetchAbortControllerRef = useRef<AbortController | null>(null);

    // Main function to fetch events with sequential chunked approach for multiple timeline segments
    const fetchTripwireEvents = useCallback(async () => {
        if (!sensorName) {
            console.log('No sensor selected for events');
            enqueueSnackbar('No sensor selected for events', { variant: 'warning' });
            return;
        }

        // Check if we have timeline segments available
        if (!timelines || timelines.length === 0) {
            console.log('No timeline segments available for events');
            enqueueSnackbar('No timeline segments available for events', { variant: 'warning' });
            return;
        }

        // If calendar range is set, filter timelines to only those within the range
        let activeTimelines = timelines;
        if (calenderStartTime && calenderEndTime) {
            const calStartMs = new Date(calenderStartTime).getTime();
            const calEndMs = new Date(calenderEndTime).getTime();

            activeTimelines = timelines
                .filter(timeline => {
                    const timelineStartMs = new Date(timeline.startTime).getTime();
                    const timelineEndMs = new Date(timeline.endTime).getTime();

                    // Include timeline if it overlaps with calendar range
                    return timelineStartMs < calEndMs && timelineEndMs > calStartMs;
                })
                .map(timeline => ({
                    ...timeline,
                    // Trim timeline to calendar range if needed
                    startTime: new Date(Math.max(new Date(timeline.startTime).getTime(), calStartMs)).toISOString(),
                    endTime: new Date(Math.min(new Date(timeline.endTime).getTime(), calEndMs)).toISOString(),
                }));
        }

        if (activeTimelines.length === 0) {
            console.log('No timeline segments in the specified range');
            enqueueSnackbar('No timeline segments in the specified range', { variant: 'warning' });
            return;
        }

        try {
            onFetchingStateChange(true);

            // Cancel any existing fetch operation
            if (eventFetchAbortControllerRef.current) {
                eventFetchAbortControllerRef.current.abort();
            }

            // Create new abort controller for this fetch operation
            const abortController = new AbortController();
            eventFetchAbortControllerRef.current = abortController;

            // Clear existing event markers
            onEventsUpdate([]);

            // Create chunks from all timeline segments
            const timeChunks = createChunksFromTimelines(activeTimelines, 2);

            console.log(`Processing ${activeTimelines.length} timeline segments:`);
            activeTimelines.forEach((timeline, idx) => {
                const duration = (new Date(timeline.endTime).getTime() - new Date(timeline.startTime).getTime()) / (1000 * 60);
                console.log(`  Segment ${idx + 1}: ${timeline.startTime} to ${timeline.endTime} (${duration.toFixed(1)} minutes)`);
            });
            console.log(`Total chunks to process: ${timeChunks.length} (${2} minutes each max, last 4 hours only)`);

            // Check if there are no chunks in the last 4 hours
            if (timeChunks.length === 0) {
                console.log('No events available in the last 4 hours');
                enqueueSnackbar('No events available in the last 4 hours', {
                    variant: 'warning',
                    autoHideDuration: 4000,
                });
                onFetchingStateChange(false);
                return;
            }

            // Track progress
            let totalEventsLoaded = 0;
            let failedChunks = 0;
            let accumulatedEvents: EventMarker[] = [];

            // Process chunks from newest to oldest (reverse order)
            for (let chunkIndex = timeChunks.length - 1; chunkIndex >= 0; chunkIndex--) {
                try {
                    // Check if operation was cancelled
                    if (abortController.signal.aborted) {
                        console.log('Events fetch was cancelled');
                        return;
                    }

                    const chunk = timeChunks[chunkIndex];
                    const chunkDuration = (new Date(chunk.end).getTime() - new Date(chunk.start).getTime()) / (1000 * 60);

                    console.log(
                        `Processing chunk ${timeChunks.length - chunkIndex}/${timeChunks.length} (Segment ${chunk.segmentIndex + 1}): ${chunk.start} to ${chunk.end} (${chunkDuration.toFixed(1)}min)`
                    );

                    // Fetch events for this chunk
                    const chunkMarkers = await fetchEventsForChunk(chunk.start, chunk.end, sensorName, abortController);

                    // Immediately plot the events from this chunk
                    if (chunkMarkers.length > 0) {
                        accumulatedEvents = [...accumulatedEvents, ...chunkMarkers];
                        // Sort by timestamp to maintain chronological order
                        accumulatedEvents.sort((a, b) => a.value - b.value);
                        onEventsUpdate([...accumulatedEvents]);

                        console.log(`Chunk ${timeChunks.length - chunkIndex} plotted: ${chunkMarkers.length} events added to timeline`);

                        // Force a brief pause to ensure the UI updates immediately
                        await new Promise(resolve => setTimeout(resolve, 10));
                    } else {
                        console.log(`Chunk ${timeChunks.length - chunkIndex} completed: No events found in this time segment`);
                    }

                    totalEventsLoaded += chunkMarkers.length;

                    // Show progress update for every few chunks or at segment boundaries
                    const isLastChunkInSegment =
                        chunkIndex === 0 || (chunkIndex > 0 && timeChunks[chunkIndex - 1].segmentIndex !== chunk.segmentIndex);

                    const chunksProcessed = timeChunks.length - chunkIndex;
                    if (chunksProcessed % 3 === 0 || chunkIndex === 0 || isLastChunkInSegment) {
                        const segmentText = isLastChunkInSegment ? ` (Segment ${chunk.segmentIndex + 1} complete)` : '';
                        enqueueSnackbar(
                            `Progress: ${chunksProcessed}/${timeChunks.length} chunks processed (${totalEventsLoaded} events)${segmentText}`,
                            {
                                variant: 'info',
                                autoHideDuration: 2000,
                            }
                        );
                    }
                } catch (error) {
                    failedChunks++;

                    // Don't show error if operation was cancelled
                    if (error instanceof Error && error.message === 'Operation cancelled') {
                        console.log('Events fetch was cancelled');
                        return;
                    }

                    console.error(`Failed to fetch events for chunk ${timeChunks.length - chunkIndex}:`, error);
                }
            }

            // Show final summary
            if (failedChunks === 0) {
                enqueueSnackbar(
                    `Successfully loaded ${totalEventsLoaded} events from ${activeTimelines.length} recording segments (${timeChunks.length} chunks)`,
                    {
                        variant: 'success',
                        autoHideDuration: 4000,
                    }
                );
            } else {
                enqueueSnackbar(
                    `Loaded ${totalEventsLoaded} events from ${activeTimelines.length} segments (${failedChunks}/${timeChunks.length} chunks failed)`,
                    {
                        variant: 'warning',
                        autoHideDuration: 4000,
                    }
                );
            }
        } catch (error) {
            // Don't show error if operation was cancelled
            if (error instanceof Error && error.message === 'Operation cancelled') {
                console.log('Events fetch was cancelled');
                return;
            }
            console.error('Events fetch error:', error);
            enqueueSnackbar('Failed to load events', { variant: 'error' });
        } finally {
            onFetchingStateChange(false);
            // Clear the abort controller reference
            if (eventFetchAbortControllerRef.current) {
                eventFetchAbortControllerRef.current = null;
            }
        }
    }, [sensorName, timelines, calenderStartTime, calenderEndTime, onEventsUpdate, onFetchingStateChange, enqueueSnackbar]);

    // Function to cancel ongoing event fetch
    const cancelEventsFetch = useCallback(() => {
        if (eventFetchAbortControllerRef.current) {
            console.log('Cancelling events fetch...');
            eventFetchAbortControllerRef.current.abort();
            eventFetchAbortControllerRef.current = null;
            onFetchingStateChange(false);
            enqueueSnackbar('Events fetch cancelled', {
                variant: 'info',
                autoHideDuration: 2000,
            });
        }
    }, [onFetchingStateChange, enqueueSnackbar]);

    // Cleanup on unmount
    useEffect(() => {
        return () => {
            // Cancel any ongoing event fetch operations
            if (eventFetchAbortControllerRef.current) {
                eventFetchAbortControllerRef.current.abort();
                eventFetchAbortControllerRef.current = null;
            }
        };
    }, []);

    return {
        fetchTripwireEvents,
        cancelEventsFetch,
    };
};

// Custom hook for managing event images
export const useEventImages = (sensorId?: string) => {
    const eventImagesRef = useRef<Record<string, string>>({});
    const loadingImagesRef = useRef<Record<string, boolean>>({});

    // Fetch event image from replay stream API on hover
    const fetchEventImage = useCallback(
        async (eventTimestamp: string, onLoadingChange: (key: string, loading: boolean) => void) => {
            if (!sensorId) return;

            const imageKey = `${sensorId}-${eventTimestamp}`;

            // Don't fetch if already loading or already loaded
            if (loadingImagesRef.current[imageKey] || eventImagesRef.current[imageKey]) {
                return;
            }

            loadingImagesRef.current[imageKey] = true;
            onLoadingChange(imageKey, true);

            try {
                const endpoint = `${config.streamRecorderEndpoint}/api/v1/replay/stream/${sensorId}/picture?startTime=${eventTimestamp}&height=240&width=320`;
                const response = await nvAxios.get(endpoint, {
                    responseType: 'blob',
                    headers: { streamId: sensorId },
                });

                // Create blob URL and store in ref (avoids re-renders that disrupt stream)
                const imageUrl = window.URL.createObjectURL(new Blob([response.data], { type: 'image/jpeg' }));
                eventImagesRef.current[imageKey] = imageUrl;
            } catch (error) {
                console.error(`Failed to fetch event image for ${eventTimestamp}:`, error);
            } finally {
                loadingImagesRef.current[imageKey] = false;
                onLoadingChange(imageKey, false);
            }
        },
        [sensorId]
    );

    // Function to get event image from ref (for TimeRangeSlider)
    const getEventImage = useCallback((imageKey: string) => {
        return eventImagesRef.current[imageKey];
    }, []);

    // Cleanup on unmount
    useEffect(() => {
        return () => {
            // Cleanup object URLs to prevent memory leaks
            Object.values(eventImagesRef.current).forEach(url => {
                if (url.startsWith('blob:')) {
                    window.URL.revokeObjectURL(url);
                }
            });
        };
    }, []);

    return {
        fetchEventImage,
        getEventImage,
        eventImagesRef,
    };
};

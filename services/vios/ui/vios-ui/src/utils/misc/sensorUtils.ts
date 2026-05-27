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
import { Sensor } from '../../interfaces/interfaces';
import nvAxios from '../../services/Axios';
import config from '../../config';
import useVSTUIStore from '../../services/StateManagement';

/**
 * Interface for timeline API response
 */
interface TimelineEntry {
    endTime: string;
    sizeInMegabytes: number;
    startTime: string;
}

interface StreamTimeline {
    sizeInMegabytes: number;
    state: string;
    timelines: TimelineEntry[];
}

interface TimelineResponse {
    [streamId: string]:
        | StreamTimeline
        | {
              remainingStorageDays: number;
              sizeInMegabytes: number;
          };
}

/**
 * Utility function to get the appropriate sensors for replay functionality.
 * Prioritizes replaySensors when available, falls back to regular sensors.
 *
 * @param sensors - Regular sensors from sensor management service
 * @param replaySensors - Sensors from replay service (when sensor management is unavailable)
 * @returns The appropriate sensor array to use for replay functionality
 */
export const getReplaySensors = (sensors: Sensor[], replaySensors: Sensor[]): Sensor[] => {
    return replaySensors.length > 0 ? replaySensors : sensors;
};

/**
 * Utility function to get the appropriate sensors for live streaming functionality.
 * Prioritizes liveSensors when available, falls back to regular sensors.
 *
 * @param sensors - Regular sensors from sensor management service
 * @param liveSensors - Sensors from live stream service
 * @returns The appropriate sensor array to use for live streaming functionality
 */
export const getLiveSensors = (sensors: Sensor[], liveSensors: Sensor[]): Sensor[] => {
    return liveSensors.length > 0 ? liveSensors : sensors;
};

/**
 * Utility function to get sensors with timeline data for replay functionality.
 * Calls the storage timeline API and filters sensors based on actual timeline presence.
 *
 * @param sensors - Regular sensors from sensor management service
 * @param replaySensors - Sensors from replay service (when sensor management is unavailable)
 * @returns Promise that resolves to sensors that have timeline data available
 */
export const getSensorsWithTimeline = async (sensors: Sensor[], replaySensors: Sensor[]): Promise<Sensor[]> => {
    try {
        const availableSensors = getReplaySensors(sensors, replaySensors);
        const vstAdaptorType = useVSTUIStore.getState().vstAdaptorType;

        // Call the appropriate timeline API based on adaptor type
        const endpoint =
            vstAdaptorType === 'mms'
                ? `${config.storageManagementEndpoint}/api/v1/storage/timelines`
                : `${config.storageManagementEndpoint}/api/v1/storage/size?timelines=true`;

        console.log('getSensorsWithTimeline - Adaptor Type:', vstAdaptorType);
        console.log('getSensorsWithTimeline - Calling endpoint:', endpoint);

        const response = await nvAxios.get<TimelineResponse>(endpoint);
        const timelineData = response.data;

        console.log('getSensorsWithTimeline - Response received:', Object.keys(timelineData).length, 'streams');

        // Filter sensors that have timelines in the API response
        return availableSensors.filter(sensor => {
            const idToCheck = sensor.streamId || sensor.sensorId;
            const streamTimeline = timelineData[idToCheck];

            // For MMS, the response is an array of timelines directly
            if (vstAdaptorType === 'mms') {
                return Array.isArray(streamTimeline) && streamTimeline.length > 0;
            }

            // For VST, check if it's a StreamTimeline (not the total object) and has timelines
            return streamTimeline && 'timelines' in streamTimeline && streamTimeline.timelines && streamTimeline.timelines.length > 0;
        });
    } catch (error) {
        console.error('Error fetching timeline data:', error);
        // Fallback to returning all sensors if API call fails
        const availableSensors = getReplaySensors(sensors, replaySensors);
        return availableSensors;
    }
};

/**
 * Utility function to get authorized sensors for live streaming functionality.
 * Filters sensors to only include those that are authorized.
 *
 * @param sensors - Regular sensors from sensor management service
 * @param liveSensors - Sensors from live stream service
 * @returns Sensors that are authorized for live streaming
 */
export const getAuthorizedLiveSensors = (sensors: Sensor[], liveSensors: Sensor[]): Sensor[] => {
    const availableSensors = getLiveSensors(sensors, liveSensors);
    return availableSensors.filter(sensor => sensor.isAuthorized);
};

/**
 * Utility function to get authorized main streams for video wall functionality.
 * Filters sensors to only include main streams that are authorized.
 *
 * @param sensors - Regular sensors from sensor management service
 * @param liveSensors - Sensors from live stream service
 * @returns Main stream sensors that are authorized for video wall
 */
export const getAuthorizedMainStreams = (sensors: Sensor[], liveSensors: Sensor[]): Sensor[] => {
    const availableSensors = getLiveSensors(sensors, liveSensors);
    return availableSensors.filter(sensor => sensor.isAuthorized && sensor.isMain);
};

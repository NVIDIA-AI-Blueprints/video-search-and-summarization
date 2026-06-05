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
import nvAxios from '../../services/Axios';
import config from '../../config';
import useVSTUIStore from '../../services/StateManagement';
import { logError, logInfo } from './Logs';
import { isNil } from 'lodash';
import streamsToJSONConvertor from './streamsJSONConvertor';
import { Sensor, SensorStatus, StorageSizes, SensorStorageSize } from '../../interfaces/interfaces';

// Interface for stream data (used by both replay and live)
interface StreamData {
    isMain: boolean;
    metadata: {
        bitrate: string;
        codec: string;
        framerate: string;
        govlength: string;
        resolution: string;
    };
    name: string;
    streamId: string;
    tags?: string;
    url: string;
    vodUrl: string;
}

const CAMERA_UNAUTHORIZED_ERROR = 'CameraUnauthorizedError';
const NO_ERROR = 'NoError';

// Helper function to create API error object
const createApiError = (error: unknown): { timestamp: number; error: string; hasError: boolean } => ({
    timestamp: Date.now(),
    error: error instanceof Error ? error.message : String(error),
    hasError: true,
});

// Helper function to clear API error (success state)
const clearApiError = (): null => null;

// Helper function to create sensor from stream data (unified for replay and live)
const createSensorFromStream = (
    sensorId: string,
    stream: StreamData,
    sensorStatus: Record<string, SensorStatus>,
    serviceAvailability: { sensorManagement: boolean },
    sensorTagsById: Record<string, string> = {}
): Sensor => {
    // Determine authorization and error status
    let isAuthorized = true;
    let isError = false;

    if (serviceAvailability.sensorManagement && sensorStatus) {
        const status = sensorStatus[sensorId];
        if (status) {
            isAuthorized = status.errorCode !== CAMERA_UNAUTHORIZED_ERROR;
            isError = status.errorCode !== NO_ERROR;
        }
    }

    return {
        sensorId: sensorId,
        streamId: stream.streamId,
        name: stream.name || 'Unknown Sensor',
        manufacturer: 'Unknown',
        hardware: 'Unknown',
        hardwareId: sensorId,
        firmwareVersion: 'Unknown',
        serialNumber: 'Unknown',
        sensorIp: 'Unknown',
        location: 'Unknown',
        isRemoteSensor: false,
        remoteDeviceId: '',
        remoteDeviceLocation: '',
        remoteDeviceName: '',
        state: 'active',
        tags: stream.tags || sensorTagsById[sensorId] || '',
        position: {
            coordinates: { x: '0', y: '0' },
            geoLocation: { latitude: '0', longitude: '0' },
            origin: { latitude: '0', longitude: '0' },
            depth: '0',
            direction: '0',
            fieldOfView: '0',
        },
        isAuthorized: isAuthorized,
        isError: isError,
        resolution: stream.metadata.resolution || '1920x1080',
        isMain: stream.isMain,
    };
};

// Function to update sensors
export const updateSensorsAndStreams = async () => {
    try {
        const vstAdaptorType = useVSTUIStore.getState().vstAdaptorType;
        console.log('vstAdaptorType', vstAdaptorType);

        // Check all services availability
        const serviceAvailability = await useVSTUIStore.getState().checkAllServicesAvailability();
        console.log('Services Available:', serviceAvailability);

        // Set loading state for MMS timeline fetch
        if (vstAdaptorType === 'mms') {
            useVSTUIStore.getState().setIsLoadingTimelines(true);
        }

        // Create array of API call promises
        const apiCalls = [
            nvAxios.get(`${config.sensorManagementEndpoint}/api/v1/sensor/list`),
            nvAxios.get(`${config.sensorManagementEndpoint}/api/v1/sensor/status`),
            vstAdaptorType !== 'streamer'
                ? nvAxios.get(`${config.streamRecorderEndpoint}/api/v1/record/status`)
                : Promise.resolve({ data: {} }),
            nvAxios.get(`${config.sensorManagementEndpoint}/api/v1/sensor/streams`),
            vstAdaptorType === 'mms'
                ? nvAxios.get(`${config.storageManagementEndpoint}/api/v1/storage/timelines`)
                : nvAxios.get(`${config.sensorManagementEndpoint}/api/v1/storage/size?timelines=true`),
        ];

        // Use Promise.allSettled instead of Promise.all to handle partial failures
        const results = await Promise.allSettled(apiCalls);

        // Extract results with error handling for each API call
        const [sensorListResult, sensorStatusResult, recordStatusResult, streamsResult, storageSizeResult] = results;

        // Process sensor status - this is critical for other operations
        let currentSensorStatus: Record<string, SensorStatus> = {};
        if (sensorStatusResult.status === 'fulfilled') {
            currentSensorStatus = sensorStatusResult.value.data;
            useVSTUIStore.setState({ sensorStatus: currentSensorStatus });
            useVSTUIStore.getState().setApiError('sensorStatus', clearApiError());
            logInfo('Successfully updated sensor status');
        } else {
            const error = createApiError(sensorStatusResult.reason);
            useVSTUIStore.getState().setApiError('sensorStatus', error);
            logError('Failed to fetch sensor status:', sensorStatusResult.reason);
        }

        // Process recording status only if adapter type is 'vst'
        if (vstAdaptorType !== 'streamer') {
            if (recordStatusResult.status === 'fulfilled') {
                const recordStatus = recordStatusResult.value.data || {};
                useVSTUIStore.setState({ recordingStatus: recordStatus });
                useVSTUIStore.getState().setApiError('recordingStatus', clearApiError());
                logInfo('Successfully updated recording status');
            } else {
                const error = createApiError(recordStatusResult.reason);
                useVSTUIStore.getState().setApiError('recordingStatus', error);
                logError('Failed to fetch recording status:', recordStatusResult.reason);
                useVSTUIStore.setState({ recordingStatus: {} });
            }
        } else {
            useVSTUIStore.getState().setApiError('recordingStatus', clearApiError());
        }

        // ===================================================================
        // 1. SENSOR SERVICE SENSORS (from /api/v1/sensor/list)
        // ===================================================================
        const sensorTagsById: Record<string, string> = {};
        if (sensorListResult.status === 'fulfilled') {
            const sensorListData = sensorListResult.value.data;
            if (!isNil(sensorListData) && sensorListData.length > 0) {
                sensorListData.forEach((device: Sensor) => {
                    if (device.sensorId && device.tags) {
                        sensorTagsById[device.sensorId] = device.tags;
                    }
                });
                // Filter out removed sensors
                const activeSensors = sensorListData.filter(
                    (device: Sensor) => !(Object.prototype.hasOwnProperty.call(device, 'state') && device.state === 'removed')
                );

                const removedSensors = sensorListData.filter(
                    (device: Sensor) => Object.prototype.hasOwnProperty.call(device, 'state') && device.state === 'removed'
                );
                useVSTUIStore.setState({ removedSensors });

                // Create sensor service sensors with streamId = sensorId (main stream)
                const sensorServiceSensors = activeSensors.map((device: Sensor) => {
                    const sensorStatus = currentSensorStatus[device.sensorId];

                    let isAuthorized = false;
                    let isError = true;

                    if (sensorStatus) {
                        isAuthorized = sensorStatus.errorCode !== CAMERA_UNAUTHORIZED_ERROR;
                        isError = sensorStatus.errorCode !== NO_ERROR;
                    }

                    // For main stream, streamId equals sensorId
                    return {
                        ...device,
                        isAuthorized,
                        isError,
                        streamId: device.sensorId,
                        isMain: true,
                    };
                });

                useVSTUIStore.getState().setSensorServiceSensors(sensorServiceSensors);
                useVSTUIStore.getState().setApiError('sensorList', clearApiError());
                logInfo(`Successfully updated ${sensorServiceSensors.length} sensor service sensors`);
            }
        } else {
            const error = createApiError(sensorListResult.reason);
            useVSTUIStore.getState().setApiError('sensorList', error);
            logError('Failed to fetch sensor list:', sensorListResult.reason);
        }

        // ===================================================================
        // 2. REPLAY SERVICE SENSORS (from /api/v1/replay/streams)
        // ===================================================================
        if (serviceAvailability.replay) {
            try {
                logInfo('Replay service is available. Fetching replay streams.');
                const replayStreamsResponse = await nvAxios.get(`${config.replayStreamEndpoint}/api/v1/replay/streams`);
                const replayStreamsData = replayStreamsResponse.data;

                if (!isNil(replayStreamsData) && replayStreamsData.length > 0) {
                    const replayServiceSensors: Sensor[] = [];

                    replayStreamsData.forEach((sensorStreams: Record<string, StreamData[]>) => {
                        const sensorId = Object.keys(sensorStreams)[0];
                        const streams = sensorStreams[sensorId];

                        // Create one sensor object for each stream (main + sub-streams)
                        streams.forEach((stream: StreamData) => {
                            const sensor = createSensorFromStream(
                                sensorId,
                                stream,
                                currentSensorStatus,
                                serviceAvailability,
                                sensorTagsById
                            );
                            replayServiceSensors.push(sensor);
                        });
                    });

                    useVSTUIStore.getState().setReplayServiceSensors(replayServiceSensors);
                    logInfo(`Successfully created ${replayServiceSensors.length} replay service sensors`);
                } else {
                    useVSTUIStore.getState().setReplayServiceSensors([]);
                    logInfo('No replay streams available');
                }
            } catch (replayError) {
                logError('Failed to fetch replay streams:', replayError);
                useVSTUIStore.getState().setReplayServiceSensors([]);
            }
        } else {
            useVSTUIStore.getState().setReplayServiceSensors([]);
            logInfo('Replay service not available');
        }

        // ===================================================================
        // 3. LIVE SERVICE SENSORS (from /api/v1/live/streams)
        // ===================================================================
        if (serviceAvailability.liveStream) {
            try {
                logInfo('Live stream service is available. Fetching live streams.');
                const liveStreamsResponse = await nvAxios.get(`${config.liveStreamEndpoint}/api/v1/live/streams`);
                const liveStreamsData = liveStreamsResponse.data;

                if (!isNil(liveStreamsData) && liveStreamsData.length > 0) {
                    const liveServiceSensors: Sensor[] = [];

                    liveStreamsData.forEach((sensorStreams: Record<string, StreamData[]>) => {
                        const sensorId = Object.keys(sensorStreams)[0];
                        const streams = sensorStreams[sensorId];

                        // Create one sensor object for each stream (main + sub-streams)
                        streams.forEach((stream: StreamData) => {
                            const sensor = createSensorFromStream(
                                sensorId,
                                stream,
                                currentSensorStatus,
                                serviceAvailability,
                                sensorTagsById
                            );
                            liveServiceSensors.push(sensor);
                        });
                    });

                    useVSTUIStore.getState().setLiveServiceSensors(liveServiceSensors);
                    logInfo(`Successfully created ${liveServiceSensors.length} live service sensors`);
                } else {
                    useVSTUIStore.getState().setLiveServiceSensors([]);
                    logInfo('No live streams available');
                }
            } catch (liveError) {
                logError('Failed to fetch live streams:', liveError);
                useVSTUIStore.getState().setLiveServiceSensors([]);
            }
        } else {
            useVSTUIStore.getState().setLiveServiceSensors([]);
            logInfo('Live stream service not available');
        }

        // ===================================================================
        // OTHER API RESULTS (streams, storage size)
        // ===================================================================

        // Process streams
        if (streamsResult.status === 'fulfilled') {
            const streamsData = streamsResult.value.data;
            if (!isNil(streamsData) && streamsData.length > 0) {
                logInfo('sensor/streams', streamsData);
                const parsedStreams = streamsToJSONConvertor(streamsData);
                useVSTUIStore.setState({ streams: parsedStreams.sensors });
                useVSTUIStore.getState().setApiError('streams', clearApiError());
                logInfo('Successfully updated sensor streams');
            }
        } else {
            const error = createApiError(streamsResult.reason);
            useVSTUIStore.getState().setApiError('streams', error);
            logError('Failed to fetch sensor streams:', streamsResult.reason);
        }

        // Process storage size
        if (storageSizeResult.status === 'fulfilled') {
            const storageSizeData = storageSizeResult.value.data;
            if (!isNil(storageSizeData)) {
                logInfo('storage/size', storageSizeData);

                // Transform MMS response format to match VST format
                if (vstAdaptorType === 'mms') {
                    const transformedData: StorageSizes = {
                        total: {
                            remainingStorageDays: 0,
                            sizeInMegabytes: 0,
                            totalAvailableStorageSize: 0,
                            totalDiskCapacity: 0,
                        },
                    };

                    Object.entries(storageSizeData).forEach(([streamId, timelines]) => {
                        if (Array.isArray(timelines)) {
                            const sensorData: SensorStorageSize = {
                                sizeInMegabytes: 0,
                                state: 'active',
                                timelines: timelines.map((timeline: { endTime: string; startTime: string }) => ({
                                    endTime: timeline.endTime,
                                    startTime: timeline.startTime,
                                    sizeInMegabytes: 0,
                                })),
                            };
                            transformedData[streamId] = sensorData;
                        }
                    });

                    useVSTUIStore.setState({ storageSizes: transformedData });
                    useVSTUIStore.getState().setIsLoadingTimelines(false);
                } else {
                    useVSTUIStore.setState({ storageSizes: storageSizeData });
                }

                useVSTUIStore.getState().setApiError('storageSize', clearApiError());
                logInfo('Successfully updated storage sizes');
            }
        } else {
            const error = createApiError(storageSizeResult.reason);
            useVSTUIStore.getState().setApiError('storageSize', error);
            logError('Failed to fetch storage sizes:', storageSizeResult.reason);

            // Clear loading state even on error for MMS
            if (vstAdaptorType === 'mms') {
                useVSTUIStore.getState().setIsLoadingTimelines(false);
            }
        }

        // Log summary
        const successCount = results.filter(result => result.status === 'fulfilled').length;
        const failureCount = results.filter(result => result.status === 'rejected').length;

        if (failureCount > 0) {
            logError(`Dashboard update completed with ${successCount} successful and ${failureCount} failed API calls`);
        } else {
            logInfo(`Dashboard update completed successfully with all ${successCount} API calls`);
        }
    } catch (error) {
        logError('Unexpected error in updateSensorsAndStreams:', error);
    }
};

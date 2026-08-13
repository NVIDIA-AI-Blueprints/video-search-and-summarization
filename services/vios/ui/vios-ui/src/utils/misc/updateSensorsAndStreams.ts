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
import LOG from './Logger';
import nvAxios from '../../services/Axios';
import config from '../../config';
import useVSTUIStore from '../../services/StateManagement';
import { logError, logInfo } from './Logs';
import { isNil } from 'lodash';
import streamsToJSONConvertor from './streamsJSONConvertor';
import { ApiErrors, RecordStatus, Sensor, SensorStatus, StorageSizes, SensorStorageSize } from '../../interfaces/interfaces';

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

// Settled result of one of the dashboard API calls
type SettledApiResult = PromiseSettledResult<{ data: unknown }>;

// Description of a stream service (replay or live) whose sensors have to be refreshed
interface StreamService {
    isAvailable: boolean;
    name: string;
    streamName: string;
    url: string;
    setSensors: (sensors: Sensor[]) => void;
}

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

// Helper function to record a failed API call
const reportApiFailure = (endpoint: keyof ApiErrors, message: string, reason: unknown): void => {
    useVSTUIStore.getState().setApiError(endpoint, createApiError(reason));
    logError(message, reason);
};

// Helper function to process sensor status - this is critical for other operations
const processSensorStatus = (result: SettledApiResult): Record<string, SensorStatus> => {
    if (result.status === 'rejected') {
        reportApiFailure('sensorStatus', 'Failed to fetch sensor status:', result.reason);
        return {};
    }

    const sensorStatus = result.value.data as Record<string, SensorStatus>;
    useVSTUIStore.setState({ sensorStatus });
    useVSTUIStore.getState().setApiError('sensorStatus', clearApiError());
    logInfo('Successfully updated sensor status');
    return sensorStatus;
};

// Helper function to process recording status only if adapter type is 'vst'
const processRecordingStatus = (result: SettledApiResult, vstAdaptorType: string): void => {
    if (vstAdaptorType === 'streamer') {
        useVSTUIStore.getState().setApiError('recordingStatus', clearApiError());
        return;
    }

    if (result.status === 'rejected') {
        reportApiFailure('recordingStatus', 'Failed to fetch recording status:', result.reason);
        useVSTUIStore.setState({ recordingStatus: {} });
        return;
    }

    useVSTUIStore.setState({ recordingStatus: (result.value.data || {}) as Record<string, RecordStatus> });
    useVSTUIStore.getState().setApiError('recordingStatus', clearApiError());
    logInfo('Successfully updated recording status');
};

const isRemovedSensor = (device: Sensor): boolean => Object.prototype.hasOwnProperty.call(device, 'state') && device.state === 'removed';

// Helper function to process the sensor service sensors, returning the tags of every listed sensor
const processSensorList = (result: SettledApiResult, currentSensorStatus: Record<string, SensorStatus>): Record<string, string> => {
    const sensorTagsById: Record<string, string> = {};

    if (result.status === 'rejected') {
        reportApiFailure('sensorList', 'Failed to fetch sensor list:', result.reason);
        return sensorTagsById;
    }

    const sensorListData = result.value.data as Sensor[] | null | undefined;
    if (isNil(sensorListData) || sensorListData.length === 0) {
        return sensorTagsById;
    }

    sensorListData.forEach((device: Sensor) => {
        if (device.sensorId && device.tags) {
            sensorTagsById[device.sensorId] = device.tags;
        }
    });

    // Filter out removed sensors
    const activeSensors = sensorListData.filter((device: Sensor) => !isRemovedSensor(device));

    const removedSensors = sensorListData.filter((device: Sensor) => isRemovedSensor(device));
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

    return sensorTagsById;
};

// Helper function to create one sensor object for each stream (main + sub-streams)
const buildSensorsFromStreams = (
    streamsData: Record<string, StreamData[]>[],
    currentSensorStatus: Record<string, SensorStatus>,
    serviceAvailability: { sensorManagement: boolean },
    sensorTagsById: Record<string, string>
): Sensor[] => {
    const sensors: Sensor[] = [];

    streamsData.forEach((sensorStreams: Record<string, StreamData[]>) => {
        const sensorId = Object.keys(sensorStreams)[0];

        sensorStreams[sensorId].forEach((stream: StreamData) => {
            sensors.push(createSensorFromStream(sensorId, stream, currentSensorStatus, serviceAvailability, sensorTagsById));
        });
    });

    return sensors;
};

// Helper function to refresh the sensors of a stream service (replay or live)
const updateStreamServiceSensors = async (
    service: StreamService,
    currentSensorStatus: Record<string, SensorStatus>,
    serviceAvailability: { sensorManagement: boolean },
    sensorTagsById: Record<string, string>
): Promise<void> => {
    if (!service.isAvailable) {
        service.setSensors([]);
        logInfo(`${service.name} service not available`);
        return;
    }

    try {
        logInfo(`${service.name} service is available. Fetching ${service.streamName} streams.`);
        const response = await nvAxios.get(service.url);
        const streamsData = response.data as Record<string, StreamData[]>[] | null | undefined;

        if (isNil(streamsData) || streamsData.length === 0) {
            service.setSensors([]);
            logInfo(`No ${service.streamName} streams available`);
            return;
        }

        const sensors = buildSensorsFromStreams(streamsData, currentSensorStatus, serviceAvailability, sensorTagsById);
        service.setSensors(sensors);
        logInfo(`Successfully created ${sensors.length} ${service.streamName} service sensors`);
    } catch (error) {
        logError(`Failed to fetch ${service.streamName} streams:`, error);
        service.setSensors([]);
    }
};

// Helper function to process streams
const processStreams = (result: SettledApiResult): void => {
    if (result.status === 'rejected') {
        reportApiFailure('streams', 'Failed to fetch sensor streams:', result.reason);
        return;
    }

    const streamsData = result.value.data as Record<string, StreamData[]>[] | null | undefined;
    if (isNil(streamsData) || streamsData.length === 0) {
        return;
    }

    logInfo('sensor/streams', streamsData);
    const parsedStreams = streamsToJSONConvertor(streamsData);
    useVSTUIStore.setState({ streams: parsedStreams.sensors });
    useVSTUIStore.getState().setApiError('streams', clearApiError());
    logInfo('Successfully updated sensor streams');
};

// Helper function to transform MMS response format to match VST format
const transformMmsStorageSizes = (storageSizeData: Record<string, unknown>): StorageSizes => {
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

    return transformedData;
};

// Helper function to process storage size
const processStorageSize = (result: SettledApiResult, vstAdaptorType: string): void => {
    if (result.status === 'rejected') {
        reportApiFailure('storageSize', 'Failed to fetch storage sizes:', result.reason);

        // Clear loading state even on error for MMS
        if (vstAdaptorType === 'mms') {
            useVSTUIStore.getState().setIsLoadingTimelines(false);
        }
        return;
    }

    const storageSizeData = result.value.data;
    if (isNil(storageSizeData)) {
        return;
    }

    logInfo('storage/size', storageSizeData);

    if (vstAdaptorType === 'mms') {
        useVSTUIStore.setState({ storageSizes: transformMmsStorageSizes(storageSizeData as Record<string, unknown>) });
        useVSTUIStore.getState().setIsLoadingTimelines(false);
    } else {
        useVSTUIStore.setState({ storageSizes: storageSizeData as StorageSizes });
    }

    useVSTUIStore.getState().setApiError('storageSize', clearApiError());
    logInfo('Successfully updated storage sizes');
};

// Function to update sensors
export const updateSensorsAndStreams = async () => {
    try {
        const vstAdaptorType = useVSTUIStore.getState().vstAdaptorType;
        LOG.info('vstAdaptorType', vstAdaptorType);

        // Check all services availability
        const serviceAvailability = await useVSTUIStore.getState().checkAllServicesAvailability();
        LOG.info('Services Available:', serviceAvailability);

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

        const currentSensorStatus = processSensorStatus(sensorStatusResult);
        processRecordingStatus(recordStatusResult, vstAdaptorType);

        // ===================================================================
        // 1. SENSOR SERVICE SENSORS (from /api/v1/sensor/list)
        // ===================================================================
        const sensorTagsById = processSensorList(sensorListResult, currentSensorStatus);

        // ===================================================================
        // 2. REPLAY SERVICE SENSORS (from /api/v1/replay/streams)
        // ===================================================================
        await updateStreamServiceSensors(
            {
                isAvailable: serviceAvailability.replay,
                name: 'Replay',
                streamName: 'replay',
                url: `${config.replayStreamEndpoint}/api/v1/replay/streams`,
                setSensors: useVSTUIStore.getState().setReplayServiceSensors,
            },
            currentSensorStatus,
            serviceAvailability,
            sensorTagsById
        );

        // ===================================================================
        // 3. LIVE SERVICE SENSORS (from /api/v1/live/streams)
        // ===================================================================
        await updateStreamServiceSensors(
            {
                isAvailable: serviceAvailability.liveStream,
                name: 'Live stream',
                streamName: 'live',
                url: `${config.liveStreamEndpoint}/api/v1/live/streams`,
                setSensors: useVSTUIStore.getState().setLiveServiceSensors,
            },
            currentSensorStatus,
            serviceAvailability,
            sensorTagsById
        );

        // ===================================================================
        // OTHER API RESULTS (streams, storage size)
        // ===================================================================
        processStreams(streamsResult);
        processStorageSize(storageSizeResult, vstAdaptorType);

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

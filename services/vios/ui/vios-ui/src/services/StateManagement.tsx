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
import { create } from 'zustand';
import { VSTUIState, StorageSizes, SensorStatus, RecordStatus, Sensor, ApiErrors, ApiError } from '../interfaces/interfaces';
import config from '../config';
import LOG from '../utils/misc/Logger';
import { checkServiceAvailability } from '../utils/misc/utils';

const useVSTUIStore = create<VSTUIState>(set => ({
    emdxEndpoint: undefined,
    sensorServiceSensors: [],
    replayServiceSensors: [],
    liveServiceSensors: [],
    streams: [],
    vstAdaptorType: undefined,
    vstVersion: undefined,
    storageSizes: undefined,
    sensorStatus: {},
    recordingStatus: {},
    removedSensors: [],
    apiErrors: {
        sensorList: null,
        sensorStatus: null,
        recordingStatus: null,
        streams: null,
        storageSize: null,
    },
    isLoadingTimelines: false,

    // Service availability flags
    isSensormanagementServiceAvailable: true,
    isStoragemanagementServiceAvailable: true,
    isRecorderServiceAvailable: true,
    isLiveStreamServiceAvailable: true,
    isStreamBridgeServiceAvailable: true,
    isReplayServiceAvailable: true,

    // Service availability setters
    setSensormanagementServiceAvailable: (available: boolean) => set({ isSensormanagementServiceAvailable: available }),
    setStoragemanagementServiceAvailable: (available: boolean) => set({ isStoragemanagementServiceAvailable: available }),
    setRecorderServiceAvailable: (available: boolean) => set({ isRecorderServiceAvailable: available }),
    setLiveStreamServiceAvailable: (available: boolean) => set({ isLiveStreamServiceAvailable: available }),
    setStreamBridgeServiceAvailable: (available: boolean) => set({ isStreamBridgeServiceAvailable: available }),
    setReplayServiceAvailable: (available: boolean) => set({ isReplayServiceAvailable: available }),

    // Check all services availability
    checkAllServicesAvailability: async () => {
        const [sensorManagementResult, storageManagementResult, recorderResult, liveStreamResult, streamBridgeResult, replayResult] =
            await Promise.allSettled([
                checkServiceAvailability(config.sensorManagementEndpoint, 'sensor'),
                checkServiceAvailability(config.storageManagementEndpoint, 'storage'),
                checkServiceAvailability(config.streamRecorderEndpoint, 'record'),
                checkServiceAvailability(config.liveStreamEndpoint, 'live'),
                checkServiceAvailability(config.streambridgeEndpoint, 'streambridge'),
                checkServiceAvailability(config.replayStreamEndpoint, 'replay'),
            ]);

        const sensorManagement = sensorManagementResult.status === 'fulfilled' ? sensorManagementResult.value : false;
        const storageManagement = storageManagementResult.status === 'fulfilled' ? storageManagementResult.value : false;
        const recorder = recorderResult.status === 'fulfilled' ? recorderResult.value : false;
        const liveStream = liveStreamResult.status === 'fulfilled' ? liveStreamResult.value : false;
        const streamBridge = streamBridgeResult.status === 'fulfilled' ? streamBridgeResult.value : false;
        const replay = replayResult.status === 'fulfilled' ? replayResult.value : false;

        set({
            isSensormanagementServiceAvailable: sensorManagement,
            isStoragemanagementServiceAvailable: storageManagement,
            isRecorderServiceAvailable: recorder,
            isLiveStreamServiceAvailable: liveStream,
            isStreamBridgeServiceAvailable: streamBridge,
            isReplayServiceAvailable: replay,
        });

        const serviceStatus = {
            sensorManagement,
            storageManagement,
            recorder,
            liveStream,
            streamBridge,
            replay,
        };

        LOG.info('Service availability check completed:', serviceStatus);
        return serviceStatus;
    },

    setSensorServiceSensors: (sensors: Sensor[]) => set({ sensorServiceSensors: sensors }),

    addStreams: sensorStreams =>
        set(state => {
            const newSensorStreams = Array.isArray(sensorStreams) ? sensorStreams : [sensorStreams];
            const updatedStreams = [...state.streams];

            newSensorStreams.forEach(newSensorStream => {
                const existingIndex = updatedStreams.findIndex(s => s.id === newSensorStream.id);
                if (existingIndex === -1) {
                    updatedStreams.push(newSensorStream);
                } else {
                    updatedStreams[existingIndex] = {
                        ...updatedStreams[existingIndex],
                        ...newSensorStream,
                    };
                }
            });

            return { streams: updatedStreams };
        }),

    setEmdxEndpoint: (endpoint: string) => set({ emdxEndpoint: endpoint }),

    setVstAdaptorType: (adaptorType: string) => set({ vstAdaptorType: adaptorType }),

    setVstVersion: (version: string) => set({ vstVersion: version }),

    setStorageSizes: (storageSizes: StorageSizes) => set({ storageSizes }),

    setSensorStatus: (sensorStatus: Record<string, SensorStatus>) => set({ sensorStatus }),

    setRecordingStatus: (recordingStatus: Record<string, RecordStatus>) => set({ recordingStatus }),

    setRemovedSensors: (removedSensors: Sensor[]) => set({ removedSensors }),

    setReplayServiceSensors: (sensors: Sensor[]) => set({ replayServiceSensors: sensors }),

    setLiveServiceSensors: (sensors: Sensor[]) => set({ liveServiceSensors: sensors }),

    setApiError: (endpoint: keyof ApiErrors, error: ApiError | null) =>
        set(state => ({
            apiErrors: {
                ...state.apiErrors,
                [endpoint]: error,
            },
        })),

    setIsLoadingTimelines: (isLoading: boolean) => set({ isLoadingTimelines: isLoading }),
}));

export default useVSTUIStore;

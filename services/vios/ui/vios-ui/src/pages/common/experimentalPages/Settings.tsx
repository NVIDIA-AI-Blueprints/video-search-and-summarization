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
import { Box, Tabs, Tab } from '@mui/material';
import SensorManagementSettings from '../../../features/microserviceSettings/sensorManagementSettings/SensorManagementSettings';
import SensorReplaySettings from '../../../features/microserviceSettings/sensorReplaySettings/SensorReplaySettings';
import SensorLiveSettings from '../../../features/microserviceSettings/sensorLiveSettings/SensorLiveSettings';
import SensorProxySettings from '../../../features/microserviceSettings/sensorProxySettings/SensorProxySettings';
import SensorRecordSettings from '../../../features/microserviceSettings/sensorRecordSettings/SensorRecordSettings';
import useVSTUIStore from '../../../services/StateManagement';
import SensorStorageSettings from '../../../features/microserviceSettings/sensorStorageSettings/SensorStorageSettings';

const App: React.FC = () => {
    const vstAdaptorType = useVSTUIStore(state => state.vstAdaptorType);
    const isSensormanagementServiceAvailable = useVSTUIStore(state => state.isSensormanagementServiceAvailable);
    const isReplayServiceAvailable = useVSTUIStore(state => state.isReplayServiceAvailable);
    const isLiveStreamServiceAvailable = useVSTUIStore(state => state.isLiveStreamServiceAvailable);
    const isStreamBridgeServiceAvailable = useVSTUIStore(state => state.isStreamBridgeServiceAvailable);
    const isRecorderServiceAvailable = useVSTUIStore(state => state.isRecorderServiceAvailable);
    const isStoragemanagementServiceAvailable = useVSTUIStore(state => state.isStoragemanagementServiceAvailable);

    // Find the first available tab
    const getFirstAvailableTab = () => {
        if (isSensormanagementServiceAvailable) return 0;
        if (isReplayServiceAvailable) return 1;
        if (isLiveStreamServiceAvailable) return 2;
        if (isStreamBridgeServiceAvailable) return 3;
        if (vstAdaptorType !== 'streamer' && isRecorderServiceAvailable) return 4;
        if (vstAdaptorType !== 'streamer' && isStoragemanagementServiceAvailable) return 5;
        return 0; // fallback to first tab
    };

    const [activeTab, setActiveTab] = React.useState(getFirstAvailableTab());

    // Update active tab if current tab becomes unavailable
    React.useEffect(() => {
        const currentTabAvailable = (() => {
            switch (activeTab) {
                case 0:
                    return isSensormanagementServiceAvailable;
                case 1:
                    return isReplayServiceAvailable;
                case 2:
                    return isLiveStreamServiceAvailable;
                case 3:
                    return isStreamBridgeServiceAvailable;
                case 4:
                    return vstAdaptorType !== 'streamer' && isRecorderServiceAvailable;
                case 5:
                    return vstAdaptorType !== 'streamer' && isStoragemanagementServiceAvailable;
                default:
                    return false;
            }
        })();

        if (!currentTabAvailable) {
            setActiveTab(getFirstAvailableTab());
        }
    }, [
        activeTab,
        isSensormanagementServiceAvailable,
        isReplayServiceAvailable,
        isLiveStreamServiceAvailable,
        isStreamBridgeServiceAvailable,
        isRecorderServiceAvailable,
        isStoragemanagementServiceAvailable,
        vstAdaptorType,
    ]);

    const handleTabChange = (_event: React.SyntheticEvent, newValue: number) => {
        setActiveTab(newValue);
    };

    return (
        <Box sx={{ width: '100%' }}>
            <Tabs value={activeTab} onChange={handleTabChange} variant='scrollable' scrollButtons='auto'>
                <Tab label='Sensor Management' disabled={!isSensormanagementServiceAvailable} />
                <Tab label='Replay Stream Management' disabled={!isReplayServiceAvailable} />
                <Tab label='Live Stream Management' disabled={!isLiveStreamServiceAvailable} />
                <Tab label='Sensor Proxy Management' disabled={!isStreamBridgeServiceAvailable} />
                {vstAdaptorType !== 'streamer' && <Tab label='Sensor Record Management' disabled={!isRecorderServiceAvailable} />}
                {vstAdaptorType !== 'streamer' && <Tab label='Sensor Storage Management' disabled={!isStoragemanagementServiceAvailable} />}
            </Tabs>
            <Box sx={{ padding: 2 }}>
                {activeTab === 0 && isSensormanagementServiceAvailable && <SensorManagementSettings />}
                {activeTab === 1 && isReplayServiceAvailable && <SensorReplaySettings />}
                {activeTab === 2 && isLiveStreamServiceAvailable && <SensorLiveSettings />}
                {activeTab === 3 && isStreamBridgeServiceAvailable && <SensorProxySettings />}
                {activeTab === 4 && vstAdaptorType !== 'streamer' && isRecorderServiceAvailable && <SensorRecordSettings />}
                {activeTab === 5 && vstAdaptorType !== 'streamer' && isStoragemanagementServiceAvailable && <SensorStorageSettings />}
            </Box>
        </Box>
    );
};

export default App;

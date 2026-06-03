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
import { Box, Chip, Tooltip } from '@mui/material';
import WarningIcon from '@mui/icons-material/Warning';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import useVSTUIStore from '../../services/StateManagement';

interface ServiceStatus {
    name: string;
    available: boolean;
}

const DashboardStatusIndicator: React.FC = () => {
    const {
        isSensormanagementServiceAvailable,
        isStoragemanagementServiceAvailable,
        isRecorderServiceAvailable,
        isLiveStreamServiceAvailable,
        isReplayServiceAvailable,
    } = useVSTUIStore(state => ({
        isSensormanagementServiceAvailable: state.isSensormanagementServiceAvailable,
        isStoragemanagementServiceAvailable: state.isStoragemanagementServiceAvailable,
        isRecorderServiceAvailable: state.isRecorderServiceAvailable,
        isLiveStreamServiceAvailable: state.isLiveStreamServiceAvailable,
        isReplayServiceAvailable: state.isReplayServiceAvailable,
    }));

    const getServiceStatuses = (): ServiceStatus[] => {
        return [
            { name: 'Sensor Management', available: isSensormanagementServiceAvailable },
            { name: 'Storage Management', available: isStoragemanagementServiceAvailable },
            { name: 'Recorder', available: isRecorderServiceAvailable },
            { name: 'Live Stream', available: isLiveStreamServiceAvailable },
            { name: 'Replay', available: isReplayServiceAvailable },
        ];
    };

    const serviceStatuses = getServiceStatuses();
    const unavailableServices = serviceStatuses.filter(service => !service.available);
    const availableServices = serviceStatuses.filter(service => service.available);
    const hasUnavailableServices = unavailableServices.length > 0;

    return (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Tooltip
                title={
                    <Box>
                        <div style={{ fontWeight: 'bold', marginBottom: '8px' }}>
                            Service Status ({availableServices.length}/{serviceStatuses.length} available)
                        </div>
                        {availableServices.length > 0 && (
                            <div style={{ marginBottom: '8px' }}>
                                <div style={{ fontWeight: 'bold', color: '#4caf50', marginBottom: '4px' }}>Available:</div>
                                {availableServices.map((service, index) => (
                                    <div key={index} style={{ marginBottom: '2px', paddingLeft: '8px' }}>
                                        • {service.name}
                                    </div>
                                ))}
                            </div>
                        )}
                        {unavailableServices.length > 0 && (
                            <div>
                                <div style={{ fontWeight: 'bold', color: '#f44336', marginBottom: '4px' }}>Unavailable:</div>
                                {unavailableServices.map((service, index) => (
                                    <div key={index} style={{ marginBottom: '2px', paddingLeft: '8px' }}>
                                        • {service.name}
                                    </div>
                                ))}
                            </div>
                        )}
                    </Box>
                }
                placement='bottom-end'
            >
                <Chip
                    icon={hasUnavailableServices ? <WarningIcon /> : <CheckCircleIcon />}
                    label={
                        hasUnavailableServices
                            ? `${unavailableServices.length} service${unavailableServices.length > 1 ? 's' : ''} unavailable`
                            : 'All services available'
                    }
                    color={hasUnavailableServices ? 'warning' : 'success'}
                    variant='outlined'
                    size='small'
                    sx={{
                        fontSize: '0.75rem',
                        height: '24px',
                        '& .MuiChip-icon': {
                            fontSize: '0.875rem',
                        },
                    }}
                />
            </Tooltip>
        </Box>
    );
};

export default DashboardStatusIndicator;

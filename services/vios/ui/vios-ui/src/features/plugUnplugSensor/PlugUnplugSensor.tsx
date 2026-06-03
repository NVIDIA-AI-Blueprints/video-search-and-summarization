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
import React, { useCallback, useState } from 'react';
import { Card, CardHeader, CardActions, CardContent, Typography, Stack, Divider, Box } from '@mui/material';
import { LoadingButton } from '@mui/lab';
import nvAxios from '../../services/Axios';
import { useNotifications } from '@toolpad/core/useNotifications';
import useVSTUIStore from '../../services/StateManagement';
import { Sensor } from '../../interfaces/interfaces';
import MultipleSensorSelector from '../../components/sensorSelector/MultipleSensorSelector';
import config from '../../config';
import PowerSettingsNewIcon from '@mui/icons-material/PowerSettingsNew';
import PowerOffIcon from '@mui/icons-material/PowerOff';

const PlugUnplugSensor: React.FC = () => {
    const sensors = useVSTUIStore(state => state.sensorServiceSensors);
    const [selectedSensors, setSelectedSensors] = useState<Sensor[] | undefined>();
    const handleSensorSelection = useCallback((selection: Sensor[] | undefined) => {
        setSelectedSensors(selection || []);
    }, []);

    const [isLoading, setIsLoading] = useState<boolean>(false);
    const notifications = useNotifications();

    const handleSensorPlug = async (): Promise<void> => {
        if (!selectedSensors) {
            return;
        }
        setIsLoading(true);
        let successCount = 0;
        let errorCount = 0;
        for (let i = 0; i < selectedSensors.length; i += 1) {
            try {
                await nvAxios.post(
                    `${config.sensorManagementEndpoint}/api/v1/sensor/debug/plug`,
                    {
                        ip: selectedSensors[i].sensorIp,
                        action: 'plug',
                    },
                    { headers: { streamId: selectedSensors[i].sensorId } }
                );
                successCount++;
            } catch (error) {
                console.log('Failed to plug sensor', error);
                errorCount++;
            }
        }
        setIsLoading(false);

        if (successCount > 0) {
            notifications.show(`Successfully plugged ${successCount} sensor(s)`, {
                severity: 'success',
                autoHideDuration: 3000,
            });
        }
        if (errorCount > 0) {
            notifications.show(`Failed to plug ${errorCount} sensor(s)`, {
                severity: 'error',
                autoHideDuration: 3000,
            });
        }
    };

    const handleSensorUnplug = async (): Promise<void> => {
        if (!selectedSensors) {
            return;
        }
        setIsLoading(true);
        let successCount = 0;
        let errorCount = 0;
        for (let i = 0; i < selectedSensors.length; i += 1) {
            try {
                await nvAxios.post(
                    `${config.sensorManagementEndpoint}/api/v1/sensor/debug/unplug`,
                    {
                        ip: selectedSensors[i].sensorIp,
                        action: 'unplug',
                    },
                    { headers: { streamId: selectedSensors[i].sensorId } }
                );
                successCount++;
            } catch (error) {
                console.log('Failed to unplug sensor', error);
                errorCount++;
            }
        }
        setIsLoading(false);

        if (successCount > 0) {
            notifications.show(`Successfully unplugged ${successCount} sensor(s)`, {
                severity: 'success',
                autoHideDuration: 3000,
            });
        }
        if (errorCount > 0) {
            notifications.show(`Failed to unplug ${errorCount} sensor(s)`, {
                severity: 'error',
                autoHideDuration: 3000,
            });
        }
    };

    return (
        <Card sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
            <CardHeader
                title={
                    <Typography variant='h6' sx={{ fontWeight: 500 }}>
                        Plug/Unplug Sensors
                    </Typography>
                }
                subheader={
                    <Typography variant='body2' color='text.secondary'>
                        Debug option to simulate plugging and unplugging sensors
                    </Typography>
                }
            />
            <Divider />
            <CardContent sx={{ flexGrow: 1 }}>
                <Stack spacing={3}>
                    <Box>
                        <Typography variant='subtitle2' sx={{ mb: 1 }}>
                            Select Sensors
                        </Typography>
                        <MultipleSensorSelector
                            multiple
                            sensors={sensors}
                            onChange={selection => {
                                handleSensorSelection(selection);
                            }}
                            selectedSensors={selectedSensors}
                        />
                    </Box>
                </Stack>
            </CardContent>
            <Divider />
            <CardActions sx={{ p: 2, justifyContent: 'flex-start', gap: 2 }}>
                <LoadingButton
                    size='large'
                    type='submit'
                    variant='contained'
                    onClick={handleSensorPlug}
                    loading={isLoading}
                    startIcon={<PowerSettingsNewIcon />}
                    sx={{
                        minWidth: 120,
                        '&:hover': {
                            transform: 'translateY(-1px)',
                        },
                    }}
                >
                    Plug
                </LoadingButton>
                <LoadingButton
                    size='large'
                    type='submit'
                    variant='contained'
                    color='error'
                    onClick={handleSensorUnplug}
                    loading={isLoading}
                    startIcon={<PowerOffIcon />}
                    sx={{
                        minWidth: 120,
                        '&:hover': {
                            transform: 'translateY(-1px)',
                        },
                    }}
                >
                    Unplug
                </LoadingButton>
            </CardActions>
        </Card>
    );
};

export default PlugUnplugSensor;

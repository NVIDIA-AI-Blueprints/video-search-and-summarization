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
import { Card, CardContent, CardActions, Button, Grid2 as Grid, TextField, CardHeader } from '@mui/material';
import VpnKeyIcon from '@mui/icons-material/VpnKey';
import { Sensor } from '../../../interfaces/interfaces';
import LOG from '../../../utils/misc/Logger';
import nvAxios from '../../../services/Axios';
import config from '../../../config';
import MultipleSensorSelector from '../../../components/sensorSelector/MultipleSensorSelector';
import useVSTUIStore from '../../../services/StateManagement';
import { useSnackbar } from 'notistack';
import { updateSensorsAndStreams } from '../../../utils/misc/updateSensorsAndStreams';

const SensorCredentialCard: React.FC = () => {
    const { enqueueSnackbar } = useSnackbar();
    const [username, setUsername] = useState<string>('');
    const [password, setPassword] = useState<string>('');
    const [isUpdating, setIsUpdating] = useState<boolean>(false);
    const sensors = useVSTUIStore(state => state.sensorServiceSensors);
    const [selectedSensors, setSelectedSensors] = useState<Sensor[]>([]);
    const handleSensorSelection = useCallback((selection: Sensor[] | undefined) => {
        setSelectedSensors(selection || []);
    }, []);

    // Filter unauthorized sensors
    const unauthorizedSensors = sensors.filter(sensor => !sensor.isAuthorized);

    const handleCredentialUpdate = async () => {
        setIsUpdating(true);

        try {
            // Update credentials for all selected sensors
            const promises = selectedSensors.map(sensor =>
                nvAxios.post(
                    `${config.sensorManagementEndpoint}/api/v1/sensor/${sensor.sensorId}/credentials`,
                    {
                        username,
                        password,
                    },
                    { headers: { streamId: sensor.sensorId } }
                )
            );

            await Promise.all(promises);

            // Refresh sensor list to update authorization status
            await updateSensorsAndStreams();

            const sensorNames = selectedSensors.map(s => s.name).join(', ');
            enqueueSnackbar(`Sensor credentials set for: ${sensorNames}`, {
                variant: 'success',
            });

            // Clear the form
            setUsername('');
            setPassword('');
            setSelectedSensors([]);
        } catch (error) {
            const sensorNames = selectedSensors.map(s => s.name).join(', ');
            LOG.error(`Failed to update credentials for: ${sensorNames}`);
            enqueueSnackbar(`Failed to update credentials for: ${sensorNames}`, {
                variant: 'error',
            });
        } finally {
            setIsUpdating(false);
        }
    };

    return (
        <Card>
            <CardHeader title={'Sensor Credentials'} subheader={`Verify sensor credentials to authorize multiple sensors`} />
            <CardContent>
                <Grid container spacing={2}>
                    <Grid size={{ xs: 12 }}>
                        <MultipleSensorSelector
                            sensors={unauthorizedSensors}
                            onChange={handleSensorSelection}
                            selectedSensors={selectedSensors}
                            label='Select Sensors'
                        />
                    </Grid>
                    <Grid size={{ xs: 12 }}>
                        <TextField fullWidth label='Username' value={username} onChange={e => setUsername(e.target.value)} />
                    </Grid>
                    <Grid size={{ xs: 12 }}>
                        <TextField
                            fullWidth
                            label='Password'
                            type='password'
                            value={password}
                            onChange={e => setPassword(e.target.value)}
                        />
                    </Grid>
                </Grid>
            </CardContent>
            <CardActions>
                <Button
                    variant='contained'
                    color='primary'
                    startIcon={<VpnKeyIcon />}
                    onClick={handleCredentialUpdate}
                    disabled={isUpdating || !username || selectedSensors.length === 0}
                >
                    Update Credentials
                </Button>
            </CardActions>
        </Card>
    );
};

export default SensorCredentialCard;

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
import { Card, CardContent, CardActions, Button, Typography, Grid2 as Grid, CardHeader } from '@mui/material';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import { Sensor } from '../../../interfaces/interfaces';
import LOG from '../../../utils/misc/Logger';
import nvAxios from '../../../services/Axios';
import config from '../../../config';
import useVSTUIStore from '../../../services/StateManagement';
import SingleSensorSelector from '../../../components/sensorSelector/SingleSensorSelector';
import { useSnackbar } from 'notistack';

const SensorRebootCard: React.FC = () => {
    const { enqueueSnackbar } = useSnackbar();
    const [isRebooting, setIsRebooting] = useState<boolean>(false);
    const sensors = useVSTUIStore(state => state.sensorServiceSensors);
    const [selectedSensor, setSelectedSensor] = useState<Sensor | null>(null);
    const handleSensorSelection = useCallback((selection: Sensor | null) => {
        setSelectedSensor(selection);
    }, []);

    // Filter authorized sensors
    const authorizedSensors = sensors.filter(sensor => sensor.isAuthorized);

    const handleReboot = async () => {
        setIsRebooting(true);
        nvAxios
            .post(`${config.sensorManagementEndpoint}/api/v1/sensor/${selectedSensor?.sensorId}/reboot`, {
                headers: { streamId: selectedSensor?.sensorId },
            })
            .then(() => {
                setIsRebooting(false);
                enqueueSnackbar(`Rebooted ${selectedSensor?.name}`, {
                    variant: 'success',
                });
            })
            .catch(() => {
                LOG.error(`Failed to reboot ${selectedSensor?.name}`);
                enqueueSnackbar(`Failed to reboot ${selectedSensor?.name}`, {
                    variant: 'error',
                });
                setIsRebooting(false);
            });
    };

    return (
        <Card>
            <CardHeader title={'Reboot Sensor'} subheader={`Reboot sensor by making a network call`} />
            <CardContent>
                <Grid container spacing={2} alignItems='center'>
                    <Grid size={{ xs: 12 }}>
                        <SingleSensorSelector
                            sensors={authorizedSensors}
                            onChange={selection => {
                                handleSensorSelection(selection);
                            }}
                            selectedSensors={selectedSensor}
                        />
                    </Grid>
                    <Grid size={{ xs: 12 }}>
                        <Typography>Use the button below to reboot sensor.</Typography>
                    </Grid>
                </Grid>
            </CardContent>
            <CardActions>
                <Button
                    variant='contained'
                    color='primary'
                    startIcon={<RestartAltIcon />}
                    onClick={handleReboot}
                    disabled={isRebooting || !selectedSensor}
                >
                    Reboot Sensor
                </Button>
            </CardActions>
        </Card>
    );
};

export default SensorRebootCard;

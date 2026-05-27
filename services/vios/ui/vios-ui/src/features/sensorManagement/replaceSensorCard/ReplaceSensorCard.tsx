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
import DeleteIcon from '@mui/icons-material/Delete';
import LOG from '../../../utils/misc/Logger';
import nvAxios from '../../../services/Axios';
import config from '../../../config';
import SingleSensorSelector from '../../../components/sensorSelector/SingleSensorSelector';
import useVSTUIStore from '../../../services/StateManagement';
import { ReplaceSensorPayload, Sensor } from '../../../interfaces/interfaces';
import { useSnackbar } from 'notistack';
import { updateSensorsAndStreams } from '../../../utils/misc/updateSensorsAndStreams';

const SensorRemoveCard: React.FC = () => {
    const { enqueueSnackbar } = useSnackbar();
    const [isRemoving, setIsRemoving] = useState<boolean>(false);
    const sensors = useVSTUIStore(state => state.sensorServiceSensors);
    const [selectedOldSensor, setSelectedOldSensor] = useState<Sensor | null>(null);
    const [selectedNewSensor, setSelectedNewSensor] = useState<Sensor | null>(null);

    // Filter authorized sensors
    const authorizedSensors = sensors.filter(sensor => sensor.isAuthorized);

    const handleOldSensorSelection = useCallback((selection: Sensor | null) => {
        setSelectedOldSensor(selection);
    }, []);
    const handleNewSensorSelection = useCallback((selection: Sensor | null) => {
        setSelectedNewSensor(selection);
    }, []);

    const handleReplaceSensor = async () => {
        if (selectedNewSensor && selectedOldSensor) {
            const payload: ReplaceSensorPayload = {
                sensorId: selectedNewSensor?.sensorId,
            };
            setIsRemoving(true);
            nvAxios
                .post(`${config.sensorManagementEndpoint}/api/v1/sensor/${selectedOldSensor?.sensorId}/replace`, payload, {
                    headers: { streamId: selectedOldSensor?.sensorId },
                })
                .then(() => {
                    LOG.info(`Success - replace sensor ${selectedOldSensor?.name} by  ${selectedNewSensor?.name}`);
                    enqueueSnackbar(`Replaced sensor ${selectedOldSensor?.name} by  ${selectedNewSensor?.name}`, {
                        variant: 'success',
                    });
                    setIsRemoving(false);
                    updateSensorsAndStreams();
                })
                .catch(() => {
                    LOG.error(`Failed to replace ${selectedOldSensor?.name}`);
                    enqueueSnackbar(`Failed to replace ${selectedOldSensor?.name}`, {
                        variant: 'error',
                    });
                    setIsRemoving(false);
                });
        }
    };

    return (
        <Card>
            <CardHeader
                title={'Replace Sensor'}
                subheader={`Replace inactive or malfunctioning sensor with another active or working sensor.`}
            />
            <CardContent>
                <Grid container spacing={2} alignItems='center'>
                    <Grid size={{ xs: 12 }}>
                        <SingleSensorSelector
                            sensors={authorizedSensors}
                            onChange={selection => {
                                handleOldSensorSelection(selection);
                            }}
                            selectedSensors={selectedOldSensor}
                        />
                    </Grid>
                    <Grid size={{ xs: 12 }}>
                        <SingleSensorSelector
                            sensors={authorizedSensors}
                            onChange={selection => {
                                handleNewSensorSelection(selection);
                            }}
                            selectedSensors={selectedNewSensor}
                        />
                    </Grid>
                    <Grid size={{ xs: 12 }}>
                        <Typography>Use the button below to replace one sensor with another.</Typography>
                    </Grid>
                </Grid>
            </CardContent>
            <CardActions>
                <Button
                    variant='contained'
                    color='error'
                    startIcon={<DeleteIcon />}
                    onClick={handleReplaceSensor}
                    disabled={isRemoving || !selectedOldSensor || !selectedNewSensor}
                >
                    Replace Sensor
                </Button>
            </CardActions>
        </Card>
    );
};

export default SensorRemoveCard;

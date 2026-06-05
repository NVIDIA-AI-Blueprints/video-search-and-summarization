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
import MultipleSensorSelector from '../../../components/sensorSelector/MultipleSensorSelector';
import useVSTUIStore from '../../../services/StateManagement';
import { Sensor } from '../../../interfaces/interfaces';
import { useSnackbar } from 'notistack';
import { updateSensorsAndStreams } from '../../../utils/misc/updateSensorsAndStreams';

const SensorRemoveCard: React.FC = () => {
    const { enqueueSnackbar } = useSnackbar();
    const [isRemoving, setIsRemoving] = useState<boolean>(false);
    const sensors = useVSTUIStore(state => state.sensorServiceSensors);
    const [selectedSensors, setSelectedSensors] = useState<Sensor[]>([]);

    const handleSensorSelection = useCallback((selection: Sensor[] | undefined) => {
        setSelectedSensors(selection || []);
    }, []);

    const handleRemove = async () => {
        if (!selectedSensors.length) return;

        setIsRemoving(true);

        try {
            // Remove all sensors in parallel
            const removePromises = selectedSensors.map(sensor =>
                nvAxios.delete(`${config.sensorManagementEndpoint}/api/v1/sensor/${sensor.sensorId}`, {
                    headers: { streamId: sensor.sensorId },
                })
            );

            await Promise.all(removePromises);

            // Update UI state
            setIsRemoving(false);
            setSelectedSensors([]);
            await updateSensorsAndStreams();

            // Show success message
            const sensorNames = selectedSensors.map(s => s.name).join(', ');
            const message =
                selectedSensors.length === 1 ? `Removed ${sensorNames}` : `Removed ${selectedSensors.length} sensors: ${sensorNames}`;

            enqueueSnackbar(message, {
                variant: 'success',
            });
        } catch (error) {
            LOG.error(`Failed to remove sensors: ${selectedSensors.map(s => s.name).join(', ')}`);

            const sensorNames = selectedSensors.map(s => s.name).join(', ');
            const message =
                selectedSensors.length === 1 ? `Failed to remove ${sensorNames}` : `Failed to remove some sensors: ${sensorNames}`;

            enqueueSnackbar(message, {
                variant: 'error',
            });
            setIsRemoving(false);
        }
    };

    const buttonText =
        selectedSensors.length === 0
            ? 'Remove Sensors'
            : selectedSensors.length === 1
              ? 'Remove Sensor'
              : `Remove ${selectedSensors.length} Sensors`;

    return (
        <Card>
            <CardHeader
                title={'Remove Sensors'}
                subheader={`Remove sensors from VST. Removing sensors does not delete their video recordings from the disk.`}
            />
            <CardContent>
                <Grid container spacing={2} alignItems='center'>
                    <Grid size={{ xs: 12 }}>
                        <MultipleSensorSelector
                            sensors={sensors}
                            onChange={handleSensorSelection}
                            selectedSensors={selectedSensors}
                            label='Select Sensors to Remove'
                        />
                    </Grid>
                    <Grid size={{ xs: 12 }}>
                        <Typography>
                            {selectedSensors.length === 0
                                ? 'Select one or more sensors to remove from the system.'
                                : selectedSensors.length === 1
                                  ? 'Use the button below to remove this sensor from the system.'
                                  : `Use the button below to remove these ${selectedSensors.length} sensors from the system.`}
                        </Typography>
                    </Grid>
                </Grid>
            </CardContent>
            <CardActions>
                <Button
                    variant='contained'
                    color='error'
                    startIcon={<DeleteIcon />}
                    onClick={handleRemove}
                    disabled={isRemoving || selectedSensors.length === 0}
                >
                    {buttonText}
                </Button>
            </CardActions>
        </Card>
    );
};

export default SensorRemoveCard;

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
import useVSTUIStore from '../../services/StateManagement';
import SingleSensorSelector from '../../components/sensorSelector/SingleSensorSelector';
import React, { useCallback, useState, useMemo } from 'react';
import { Grid2 as Grid } from '@mui/material';
import { Sensor } from '../../interfaces/interfaces';
import CameraSettingsForm from '../../components/sensorSettingsForm/SensorSettingsForm';
import VSTStreamManager from '../../features/streamManager/StreamManager';
import { StreamType } from 'vst-streaming-lib';

const SensorConfiguration = () => {
    const sensors = useVSTUIStore(state => state.sensorServiceSensors);
    const isSensormanagementServiceAvailable = useVSTUIStore(state => state.isSensormanagementServiceAvailable);
    const [selectedSensor, setSelectedSensor] = useState<Sensor | null>(null);

    // Filter authorized sensors
    const authorizedSensors = useMemo(() => sensors.filter(sensor => sensor.isAuthorized), [sensors]);

    const handleSensorSelection = useCallback((selection: Sensor | null) => {
        setSelectedSensor(selection);
    }, []);

    return (
        <Grid container spacing={2}>
            <Grid size={{ xs: 12 }}>
                <h1>Sensor Configuration</h1>
            </Grid>
            <Grid size={{ xs: 12 }}>
                <div
                    style={{
                        pointerEvents: !isSensormanagementServiceAvailable ? 'none' : 'auto',
                        opacity: !isSensormanagementServiceAvailable ? 0.5 : 1,
                    }}
                >
                    <SingleSensorSelector
                        sensors={authorizedSensors}
                        onChange={selection => {
                            handleSensorSelection(selection);
                        }}
                        selectedSensors={selectedSensor}
                    />
                </div>
            </Grid>
            {selectedSensor && (
                <Grid
                    size={{ xs: 12 }}
                    sx={{
                        display: 'block',
                        marginLeft: 'auto',
                        marginRight: 'auto',
                        width: { xs: '100%', sm: '100%', md: 600, lg: 700 },
                        pointerEvents: !isSensormanagementServiceAvailable ? 'none' : 'auto',
                        opacity: !isSensormanagementServiceAvailable ? 0.5 : 1,
                    }}
                >
                    <VSTStreamManager key={selectedSensor.sensorId} sensor={selectedSensor} streamType={StreamType.Live} />
                </Grid>
            )}
            {selectedSensor && (
                <Grid
                    size={{ xs: 12 }}
                    sx={{
                        pointerEvents: !isSensormanagementServiceAvailable ? 'none' : 'auto',
                        opacity: !isSensormanagementServiceAvailable ? 0.5 : 1,
                    }}
                >
                    <CameraSettingsForm sensor={selectedSensor} />
                </Grid>
            )}
        </Grid>
    );
};

export default SensorConfiguration;

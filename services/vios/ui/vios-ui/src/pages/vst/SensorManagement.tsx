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
import useVSTUIStore from '../../services/StateManagement';
import { Grid2 as Grid } from '@mui/material';
import SensorRebootCard from '../../features/sensorManagement/sensorRebootCard/SensorRebootCard';
import SensorRemoveCard from '../../features/sensorManagement/sensorRemoveCard/SensorRemoveCard';
import SensorScanCard from '../../features/sensorManagement/sensorScanCard/SensorScanCard';
import SensorCredentialCard from '../../features/sensorManagement/sensorCredentialCard/SensorCredentialCard';
import AddNewSensorCard from '../../features/sensorManagement/addNewSensorCard/AddNewSensorCard';
import ReplaceSensorCard from '../../features/sensorManagement/replaceSensorCard/ReplaceSensorCard';

const SensorConfiguration = () => {
    const isSensormanagementServiceAvailable = useVSTUIStore(state => state.isSensormanagementServiceAvailable);
    const cardProps = !isSensormanagementServiceAvailable ? { sx: { pointerEvents: 'none', opacity: 0.5 } } : {};

    return (
        <Grid container spacing={2}>
            <Grid size={{ xs: 12 }}>
                <h1>Sensor Management</h1>
            </Grid>
            <Grid size={{ xs: 6 }} {...cardProps}>
                <SensorRebootCard />
            </Grid>
            <Grid size={{ xs: 6 }} {...cardProps}>
                <SensorRemoveCard />
            </Grid>
            <Grid size={{ xs: 6 }} {...cardProps}>
                <SensorCredentialCard />
            </Grid>
            <Grid size={{ xs: 6 }} {...cardProps}>
                <ReplaceSensorCard />
            </Grid>
            <Grid size={{ xs: 6 }} {...cardProps}>
                <SensorScanCard />
            </Grid>
            <Grid size={{ xs: 6 }} {...cardProps}>
                <AddNewSensorCard />
            </Grid>
        </Grid>
    );
};

export default SensorConfiguration;

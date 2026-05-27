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
import React, { useCallback, useState } from 'react';
import { Grid2 as Grid, Typography } from '@mui/material';
import { Sensor } from '../../interfaces/interfaces';
import MediaConfigurationForm from '../../components/mediaConfigurationForm/MediaConfigurationForm';
import MetaDataForm from '../../components/metadataForm/MetadataForm';

const MediaConfiguration = () => {
    const sensors = useVSTUIStore(state => state.sensorServiceSensors);
    const [selectedSensor, setSelectedSensor] = useState<Sensor | null>(null);
    const handleSensorSelection = useCallback((selection: Sensor | null) => {
        setSelectedSensor(selection);
    }, []);

    return (
        <Grid container spacing={2}>
            <Grid size={{ xs: 12 }}>
                <Typography variant='h4' gutterBottom>
                    Media Configuration
                </Typography>
            </Grid>
            <Grid size={{ xs: 12 }}>
                <SingleSensorSelector
                    sensors={sensors}
                    onChange={selection => {
                        handleSensorSelection(selection);
                    }}
                    selectedSensors={selectedSensor}
                />
            </Grid>
            {selectedSensor && (
                <>
                    <Grid size={{ xs: 12, md: 6 }}>
                        <MediaConfigurationForm key={selectedSensor.sensorId} initialData={selectedSensor} />
                    </Grid>
                    <Grid size={{ xs: 12, md: 6 }}>
                        <MetaDataForm key={selectedSensor.sensorId} streamId={selectedSensor.sensorId} />
                    </Grid>
                </>
            )}
        </Grid>
    );
};

export default MediaConfiguration;

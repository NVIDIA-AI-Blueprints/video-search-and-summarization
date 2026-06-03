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
import { Card, CardContent, CardHeader, Button, CircularProgress, Box } from '@mui/material';
import SingleSensorSelector from '../sensorSelector/SingleSensorSelector';
import { Sensor } from '../../interfaces/interfaces';

interface SensorSelectionCardProps {
    authorizedSensors: Sensor[];
    selectedSensor: Sensor | null;
    onSensorSelection: (sensor: Sensor | null) => void;
    onLoadCalibrationData: () => void;
    isLoadingCalibration: boolean;
}

const SensorSelectionCard: React.FC<SensorSelectionCardProps> = ({
    authorizedSensors,
    selectedSensor,
    onSensorSelection,
    onLoadCalibrationData,
    isLoadingCalibration,
}) => {
    return (
        <Card>
            <CardHeader title='Sensor Selection' subheader='Select a sensor to load calibration data' />
            <CardContent>
                <SingleSensorSelector sensors={authorizedSensors} onChange={onSensorSelection} selectedSensors={selectedSensor} />

                {selectedSensor && (
                    <Box sx={{ mt: 2 }}>
                        <Button
                            variant='contained'
                            onClick={onLoadCalibrationData}
                            disabled={isLoadingCalibration}
                            startIcon={isLoadingCalibration ? <CircularProgress size={20} /> : null}
                        >
                            {isLoadingCalibration ? 'Loading...' : 'Load Calibration Data'}
                        </Button>
                    </Box>
                )}
            </CardContent>
        </Card>
    );
};

export default SensorSelectionCard;

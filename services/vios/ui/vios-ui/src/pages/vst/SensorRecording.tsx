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
import React, { useCallback, useEffect, useState } from 'react';
import { Grid2 as Grid, Typography } from '@mui/material';
import { Sensor, CronScheduleArray } from '../../interfaces/interfaces';
import nvAxios from '../../services/Axios';
import config from '../../config';
import LOG from '../../utils/misc/Logger';
import RecordingStatusCard from '../../components/sensorRecording/RecordingStatusCard';
import RecordingScheduleCard from '../../components/sensorRecording/RecordingScheduleCard';
import RecordingScheduleTable from '../../components/sensorRecording/RecordingScheduleTable';

const SensorRecording = () => {
    const sensors = useVSTUIStore(state => state.sensorServiceSensors);
    const isRecorderServiceAvailable = useVSTUIStore(state => state.isRecorderServiceAvailable);
    const [selectedSensor, setSelectedSensor] = useState<Sensor | null>(null);
    const [recordingStatus, setRecordingStatus] = useState<string>('off');
    const [recordSchedules, setRecordSchedules] = useState<CronScheduleArray>([]);

    // Filter authorized sensors
    const authorizedSensors = sensors.filter(sensor => sensor.isAuthorized);

    const handleSensorSelection = useCallback((selection: Sensor | null) => {
        setSelectedSensor(selection);
    }, []);

    const updateRecordingSchedule = useCallback(() => {
        nvAxios
            .get(`${config.streamRecorderEndpoint}/api/v1/record/${selectedSensor?.sensorId}/schedule`, {
                headers: { streamId: selectedSensor?.streamId || selectedSensor?.sensorId },
            })
            .then(response => {
                setRecordSchedules(response.data || []);
            })
            .catch(() => {
                LOG.error(`Failed to set recording for ${selectedSensor?.name}`);
            });
    }, [selectedSensor?.name, selectedSensor?.sensorId]);

    useEffect(() => {
        const updateRecordingStatus = () => {
            nvAxios
                .get(`${config.streamRecorderEndpoint}/api/v1/record/${selectedSensor?.sensorId}/status`, {
                    headers: { streamId: selectedSensor?.streamId || selectedSensor?.sensorId },
                })
                .then(response => {
                    if (response.data) {
                        setRecordingStatus(response.data.recordingStatus);
                    }
                })
                .catch(() => {
                    LOG.error(`Failed to set recording for ${selectedSensor?.name}`);
                });
        };

        if (selectedSensor) {
            updateRecordingStatus();
            updateRecordingSchedule();
        } else {
            setRecordingStatus('off');
            setRecordSchedules([]);
        }
    }, [selectedSensor, updateRecordingSchedule]);

    return (
        <Grid container spacing={3}>
            <Grid size={{ xs: 12 }}>
                <Typography variant='h4' gutterBottom>
                    Record Settings
                </Typography>
            </Grid>
            <Grid size={{ xs: 12 }}>
                <div
                    style={{
                        pointerEvents: !isRecorderServiceAvailable ? 'none' : 'auto',
                        opacity: !isRecorderServiceAvailable ? 0.5 : 1,
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
            <Grid
                size={{ xs: 12, sm: 6 }}
                sx={{
                    pointerEvents: !isRecorderServiceAvailable ? 'none' : 'auto',
                    opacity: !isRecorderServiceAvailable ? 0.5 : 1,
                }}
            >
                <RecordingStatusCard
                    selectedSensor={selectedSensor}
                    recordingStatus={recordingStatus}
                    onStatusChange={setRecordingStatus}
                />
            </Grid>
            <Grid
                size={{ xs: 12, sm: 6 }}
                sx={{
                    pointerEvents: !isRecorderServiceAvailable ? 'none' : 'auto',
                    opacity: !isRecorderServiceAvailable ? 0.5 : 1,
                }}
            >
                <RecordingScheduleCard selectedSensor={selectedSensor} onScheduleUpdate={updateRecordingSchedule} />
            </Grid>
            <Grid
                size={{ xs: 12 }}
                sx={{
                    pointerEvents: !isRecorderServiceAvailable ? 'none' : 'auto',
                    opacity: !isRecorderServiceAvailable ? 0.5 : 1,
                }}
            >
                <RecordingScheduleTable
                    selectedSensor={selectedSensor}
                    recordSchedules={recordSchedules}
                    onScheduleUpdate={updateRecordingSchedule}
                />
            </Grid>
        </Grid>
    );
};

export default SensorRecording;

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
import { Alert, Card, Grid2 as Grid, Skeleton, CardHeader, CardContent, CardActions, Divider, FormControlLabel } from '@mui/material';
import { NetworkConfig, Sensor } from '../../interfaces/interfaces';
import SensorInformationForm from '../../components/sensorInformationForm/SensorInformationForm';
import nvAxios from '../../services/Axios';
import config from '../../config';
import LOG from '../../utils/misc/Logger';
import NetworkInformationForm from '../../components/networkInformationForm/NetworkInformationForm';

const LiveStream = () => {
    const sensors = useVSTUIStore(state => state.sensorServiceSensors);
    const isSensormanagementServiceAvailable = useVSTUIStore(state => state.isSensormanagementServiceAvailable);
    const [selectedSensor, setSelectedSensor] = useState<Sensor | null>(null);
    const [sensorInfo, setSensorInfo] = useState<Sensor>();
    const [sensorNetworkInfo, setSensorNetworkInfo] = useState<NetworkConfig>();
    const handleSensorSelection = useCallback((selection: Sensor | null) => {
        setSelectedSensor(selection);
    }, []);
    const [loading, setLoading] = useState<boolean>(false);

    // Filter authorized sensors
    const authorizedSensors = sensors.filter(sensor => sensor.isAuthorized);

    const fetchSensorDetails = useCallback(async () => {
        setLoading(true);
        await nvAxios
            .get(`${config.sensorManagementEndpoint}/api/v1/sensor/${selectedSensor?.sensorId}/info`, {
                headers: { streamId: selectedSensor?.streamId || selectedSensor?.sensorId },
            })
            .then(response => {
                if (response.data) {
                    setSensorInfo(response.data as Sensor);
                }
            })
            .catch(() => {
                LOG.error(`Failed to get sensor information for ${selectedSensor?.name}`);
            });
        await nvAxios
            .get(`${config.sensorManagementEndpoint}/api/v1/sensor/${selectedSensor?.sensorId}/network`, {
                headers: { streamId: selectedSensor?.streamId || selectedSensor?.sensorId },
            })
            .then(response => {
                if (response.data) {
                    setSensorNetworkInfo(response.data as NetworkConfig);
                }
            })
            .catch(() => {
                LOG.error(`Failed to get sensor network information for ${selectedSensor?.name}`);
            });
        setLoading(false);
    }, [selectedSensor?.name, selectedSensor?.sensorId]);

    useEffect(() => {
        setSensorNetworkInfo(undefined);
        setSensorInfo(undefined);
        if (selectedSensor) {
            fetchSensorDetails();
        }
    }, [fetchSensorDetails, selectedSensor]);

    return (
        <Grid container spacing={2}>
            <Grid size={{ xs: 12 }}>
                <h1>Sensor Details</h1>
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
            {loading && (
                <>
                    <Grid size={{ xs: 12, sm: 6 }}>
                        <Card>
                            <CardHeader title={<Skeleton width='40%' />} subheader={<Skeleton width='60%' />} />
                            <CardContent>
                                <Grid container spacing={2}>
                                    {/* Name field */}
                                    <Grid size={{ xs: 12 }}>
                                        <Skeleton height={56} />
                                    </Grid>
                                    {/* Tags field */}
                                    <Grid size={{ xs: 12 }}>
                                        <Skeleton height={56} />
                                    </Grid>
                                    {/* Divider */}
                                    <Grid size={{ xs: 12 }}>
                                        <Divider>
                                            <Skeleton width='30%' />
                                        </Divider>
                                    </Grid>
                                    {/* Position fields - 2 columns */}
                                    <Grid size={{ xs: 6 }}>
                                        <Skeleton height={56} />
                                    </Grid>
                                    <Grid size={{ xs: 6 }}>
                                        <Skeleton height={56} />
                                    </Grid>
                                    <Grid size={{ xs: 6 }}>
                                        <Skeleton height={56} />
                                    </Grid>
                                    {/* Read-only divider */}
                                    <Grid size={{ xs: 12 }}>
                                        <Divider>
                                            <Skeleton width='30%' />
                                        </Divider>
                                    </Grid>
                                    {/* Read-only fields - 2 columns, 9 fields */}
                                    {[...Array(9)].map((_, index) => (
                                        <Grid size={{ xs: 6 }} key={index}>
                                            <Skeleton height={56} />
                                        </Grid>
                                    ))}
                                </Grid>
                            </CardContent>
                            <CardActions>
                                <Skeleton width={100} height={36} />
                            </CardActions>
                        </Card>
                    </Grid>
                    <Grid size={{ xs: 12, sm: 6 }}>
                        <Card>
                            <CardHeader title={<Skeleton width='40%' />} subheader={<Skeleton width='60%' />} />
                            <CardContent>
                                <Grid container spacing={2}>
                                    {/* Network fields - 2 columns */}
                                    {[...Array(6)].map((_, index) => (
                                        <Grid size={{ xs: 6 }} key={index}>
                                            <Skeleton height={56} />
                                        </Grid>
                                    ))}
                                    {/* Switch controls */}
                                    <Grid size={{ xs: 6 }}>
                                        <FormControlLabel
                                            control={<Skeleton variant='rectangular' width={58} height={38} />}
                                            label={<Skeleton width='80px' />}
                                        />
                                    </Grid>
                                    <Grid size={{ xs: 6 }}>
                                        <FormControlLabel
                                            control={<Skeleton variant='rectangular' width={58} height={38} />}
                                            label={<Skeleton width='80px' />}
                                        />
                                    </Grid>
                                </Grid>
                            </CardContent>
                            <CardActions>
                                <Skeleton width={100} height={36} />
                            </CardActions>
                        </Card>
                    </Grid>
                </>
            )}

            {sensorInfo && selectedSensor && !loading && (
                <Grid
                    size={{ xs: 12, sm: 6 }}
                    sx={{
                        pointerEvents: !isSensormanagementServiceAvailable ? 'none' : 'auto',
                        opacity: !isSensormanagementServiceAvailable ? 0.5 : 1,
                    }}
                >
                    <SensorInformationForm initialData={sensorInfo} />
                </Grid>
            )}
            {sensorNetworkInfo && selectedSensor && !loading && (
                <Grid
                    size={{ xs: 12, sm: 6 }}
                    sx={{
                        pointerEvents: !isSensormanagementServiceAvailable ? 'none' : 'auto',
                        opacity: !isSensormanagementServiceAvailable ? 0.5 : 1,
                    }}
                >
                    <NetworkInformationForm initialData={sensorNetworkInfo} sensor={selectedSensor} />
                </Grid>
            )}
            {!sensorInfo && selectedSensor && !loading && (
                <Grid size={{ xs: 12, sm: 6 }}>
                    <Card>
                        <Alert severity='warning'>Sensor information is unavailable for the selected sensor</Alert>
                    </Card>
                </Grid>
            )}
            {!sensorNetworkInfo && selectedSensor && !loading && (
                <Grid size={{ xs: 12, sm: 6 }}>
                    <Card>
                        <Alert severity='warning'>Network information is unavailable for the selected sensor</Alert>
                    </Card>
                </Grid>
            )}
        </Grid>
    );
};

export default LiveStream;

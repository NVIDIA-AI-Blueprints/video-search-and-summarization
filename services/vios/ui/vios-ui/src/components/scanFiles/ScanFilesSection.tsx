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
import React, { useState } from 'react';
import { Stack, Box, Typography } from '@mui/material';
import { LoadingButton } from '@mui/lab';
import { useSnackbar } from 'notistack';
import SingleSensorSelector from '../sensorSelector/SingleSensorSelector';
import useVSTUIStore from '../../services/StateManagement';
import { Sensor } from '../../interfaces/interfaces';
import nvAxios from '../../services/Axios';
import LOG from '../../utils/misc/Logger';
import { updateSensorsAndStreams } from '../../utils/misc/updateSensorsAndStreams';
import config from '../../config';

const ScanFilesSection = () => {
    const { enqueueSnackbar } = useSnackbar();
    const sensors = useVSTUIStore(state => state.sensorServiceSensors);
    const [selectedSensor, setSelectedSensor] = useState<Sensor | null>(null);
    const [isScanning, setIsScanning] = useState(false);

    const handleSubmit = async () => {
        if (!selectedSensor) {
            enqueueSnackbar(`Error - Sensor not selected`, {
                variant: 'error',
                anchorOrigin: { horizontal: 'right', vertical: 'bottom' },
            });
            return;
        }

        enqueueSnackbar(`Pending - scanning sensors`, {
            variant: 'info',
            anchorOrigin: { horizontal: 'right', vertical: 'bottom' },
        });

        try {
            setIsScanning(true);
            await nvAxios.post(
                `${config.sensorManagementEndpoint}/api/v1/sensor/scan`,
                {},
                {
                    headers: { streamId: selectedSensor.sensorId },
                }
            );
            enqueueSnackbar(`Success - scan sensors completed`, {
                variant: 'success',
                anchorOrigin: { horizontal: 'right', vertical: 'bottom' },
            });

            // Update sensor and stream lists after successful scan
            await updateSensorsAndStreams();
        } catch (error: unknown) {
            LOG.error('Failed to scan sensors');
            if (error && typeof error === 'object' && 'response' in error) {
                const axiosError = error as {
                    response?: {
                        status?: number;
                        data?: { error_message?: string };
                    };
                };
                if (axiosError.response?.data == null) {
                    enqueueSnackbar(`Error - ${axiosError.response?.status}`, {
                        variant: 'error',
                        anchorOrigin: {
                            horizontal: 'right',
                            vertical: 'bottom',
                        },
                    });
                } else {
                    enqueueSnackbar(`Error - ${axiosError.response?.data?.error_message || 'Unknown error'}`, {
                        variant: 'error',
                        anchorOrigin: {
                            horizontal: 'right',
                            vertical: 'bottom',
                        },
                    });
                }
            } else {
                enqueueSnackbar(`Error - Unknown error occurred`, {
                    variant: 'error',
                    anchorOrigin: { horizontal: 'right', vertical: 'bottom' },
                });
            }
        } finally {
            setIsScanning(false);
        }
    };

    return (
        <>
            <Typography variant='h6' gutterBottom>
                Scan Files
            </Typography>
            <Stack spacing={3} sx={{ mb: 2 }}>
                <SingleSensorSelector sensors={sensors} onChange={setSelectedSensor} selectedSensors={selectedSensor} />
            </Stack>
            <Box m={1} display='flex' justifyContent='flex-end' alignItems='flex-end'>
                <LoadingButton size='large' type='submit' variant='contained' onClick={handleSubmit} loading={isScanning}>
                    Submit
                </LoadingButton>
            </Box>
        </>
    );
};

export default ScanFilesSection;

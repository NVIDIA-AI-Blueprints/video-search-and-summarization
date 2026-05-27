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
import { Box, Card, CardContent, CardHeader, Divider, FormControlLabel, Radio, RadioGroup, Typography } from '@mui/material';
import { useSnackbar } from 'notistack';
import { ChangeEvent, useState } from 'react';
import { Sensor } from '../../interfaces/interfaces';
import nvAxios from '../../services/Axios';
import config from '../../config';
import LOG from '../../utils/misc/Logger';
import React from 'react';

interface RecordingStatusCardProps {
    selectedSensor: Sensor | null;
    recordingStatus: string;
    onStatusChange: (status: string) => void;
}

const RecordingStatusCard = ({ selectedSensor, recordingStatus, onStatusChange }: RecordingStatusCardProps) => {
    const { enqueueSnackbar } = useSnackbar();
    const [isLoading, setIsLoading] = useState(false);

    const handleRecordingChange = (_event: ChangeEvent<HTMLInputElement>, value: string) => {
        setIsLoading(true);
        nvAxios
            .post(
                `${config.streamRecorderEndpoint}/api/v1/record/${selectedSensor?.sensorId}/${value === 'user' ? 'start' : 'stop'}`,
                {},
                {
                    headers: {
                        streamId: selectedSensor?.sensorId,
                    },
                }
            )
            .then(() => {
                onStatusChange(value);
                setIsLoading(false);
                enqueueSnackbar(`Recording ${value === 'user' ? 'started' : 'stopped'} successfully`, {
                    variant: 'success',
                    autoHideDuration: 3000,
                });
            })
            .catch(() => {
                LOG.error(`Failed to set recording for ${selectedSensor?.name}`);
                setIsLoading(false);
                enqueueSnackbar(`Failed to ${value === 'user' ? 'start' : 'stop'} recording`, {
                    variant: 'error',
                    autoHideDuration: 3000,
                });
            });
    };

    return (
        <Card elevation={2}>
            <CardHeader title='Recording Status' subheader='Set recording status' sx={{ borderBottom: 1, borderColor: 'divider' }} />
            <CardContent>
                <Box>
                    <Box sx={{ mb: 2 }}>
                        <Box
                            sx={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: 4,
                            }}
                        >
                            <Box
                                sx={{
                                    display: 'flex',
                                    alignItems: 'center',
                                    gap: 1,
                                }}
                            >
                                <Typography variant='body2' color='text.secondary'>
                                    Recording Status:
                                </Typography>
                                <Typography variant='body2'>
                                    <b>
                                        {selectedSensor && recordingStatus
                                            ? recordingStatus !== 'off' &&
                                              recordingStatus !== 'error' &&
                                              recordingStatus !== 'statusUnknown'
                                                ? 'Recording Active'
                                                : 'Recording Inactive'
                                            : 'Unavailable'}
                                    </b>
                                </Typography>
                            </Box>

                            <Box
                                sx={{
                                    display: 'flex',
                                    alignItems: 'center',
                                    gap: 1,
                                }}
                            >
                                <Typography variant='body2' color='text.secondary'>
                                    Status:
                                </Typography>
                                <Typography variant='body2'>
                                    <b>{selectedSensor && recordingStatus ? recordingStatus.replace(/_/g, ' ') : 'Unavailable'}</b>
                                </Typography>
                            </Box>
                        </Box>
                    </Box>
                    <Divider sx={{ my: 2 }} />
                    <Box>
                        <Typography variant='subtitle2' color='text.secondary' gutterBottom>
                            Change Status
                        </Typography>
                        <RadioGroup value={recordingStatus} onChange={handleRecordingChange} row sx={{ gap: 2 }}>
                            <FormControlLabel value='off' control={<Radio />} label='Off' disabled={selectedSensor == null || isLoading} />
                            <FormControlLabel value='user' control={<Radio />} label='On' disabled={selectedSensor == null || isLoading} />
                        </RadioGroup>
                    </Box>
                </Box>
            </CardContent>
        </Card>
    );
};

export default RecordingStatusCard;

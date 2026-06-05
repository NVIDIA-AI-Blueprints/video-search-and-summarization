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
import {
    Card,
    CardHeader,
    FormGroup,
    FormControlLabel,
    Switch,
    CardActions,
    CardContent,
    Box,
    Typography,
    Stack,
    Divider,
} from '@mui/material';
import { LoadingButton } from '@mui/lab';
import nvAxios from '../../services/Axios';
import { useNotifications } from '@toolpad/core/useNotifications';
import config from '../../config';

interface LoggingStatus {
    live_stream: boolean;
    vod_stream: boolean;
}

const TimestampLogging: React.FC = () => {
    const [liveStreamLogging, setLiveStreamLogging] = useState<boolean>(false);
    const [vodStreamLogging, setVodStreamLogging] = useState<boolean>(false);
    const [isLoading, setIsLoading] = useState<boolean>(false);
    const notifications = useNotifications();

    const getLoggingStatus = async (): Promise<void> => {
        setIsLoading(true);
        try {
            const response = await nvAxios.get<LoggingStatus>(`${config.sensorManagementEndpoint}/api/v1/sensor/debug/logging`);
            const { data } = response;
            console.log('Debug Logging status: ', data);
            if (data) {
                if ('live_stream' in data) {
                    setLiveStreamLogging(data.live_stream);
                }
                if ('vod_stream' in data) {
                    setVodStreamLogging(data.vod_stream);
                }
            }
        } catch (error) {
            console.log('Failed to fetch logging status', error);
            notifications.show('Error - Could not Get logging data', {
                severity: 'error',
                autoHideDuration: 3000,
            });
        } finally {
            setIsLoading(false);
        }
    };

    const setLoggingStatus = async (): Promise<void> => {
        setIsLoading(true);
        try {
            await nvAxios.post(`${config.sensorManagementEndpoint}/api/v1/sensor/debug/logging`, {
                live_stream: liveStreamLogging,
                vod_stream: vodStreamLogging,
            });
            notifications.show('Logging status updated successfully', {
                severity: 'success',
                autoHideDuration: 3000,
            });
        } catch (error) {
            console.log('Failed to set logging status', error);
            notifications.show('Error - Could not set logging status', {
                severity: 'error',
                autoHideDuration: 3000,
            });
        } finally {
            setIsLoading(false);
        }
    };

    const handleLiveStreamLoggingChange = (event: React.ChangeEvent<HTMLInputElement>): void => {
        setLiveStreamLogging(event.target.checked);
    };

    const handleVodStreamLoggingChange = (event: React.ChangeEvent<HTMLInputElement>): void => {
        setVodStreamLogging(event.target.checked);
    };

    return (
        <Card sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
            <CardHeader
                title={
                    <Typography variant='h6' sx={{ fontWeight: 500 }}>
                        Stream Timestamp Logging
                    </Typography>
                }
                subheader={
                    <Typography variant='body2' color='text.secondary'>
                        Enable or disable timestamp logging for different stream types
                    </Typography>
                }
            />
            <Divider />
            <CardContent sx={{ flexGrow: 1 }}>
                <Stack spacing={3}>
                    <Box>
                        <LoadingButton
                            size='small'
                            type='submit'
                            variant='outlined'
                            onClick={getLoggingStatus}
                            loading={isLoading}
                            sx={{ mb: 2 }}
                        >
                            Get Current Status
                        </LoadingButton>
                        <FormGroup>
                            <FormControlLabel
                                control={<Switch checked={liveStreamLogging} onChange={handleLiveStreamLoggingChange} color='primary' />}
                                label={<Typography variant='body1'>Live Stream</Typography>}
                            />
                            <FormControlLabel
                                control={<Switch checked={vodStreamLogging} onChange={handleVodStreamLoggingChange} color='primary' />}
                                label={<Typography variant='body1'>VOD Stream</Typography>}
                            />
                        </FormGroup>
                    </Box>
                </Stack>
            </CardContent>
            <Divider />
            <CardActions sx={{ p: 2, justifyContent: 'flex-start' }}>
                <LoadingButton
                    size='large'
                    type='submit'
                    variant='contained'
                    onClick={setLoggingStatus}
                    loading={isLoading}
                    sx={{
                        minWidth: 120,
                        '&:hover': {
                            transform: 'translateY(-1px)',
                        },
                    }}
                >
                    Set Status
                </LoadingButton>
            </CardActions>
        </Card>
    );
};

export default TimestampLogging;

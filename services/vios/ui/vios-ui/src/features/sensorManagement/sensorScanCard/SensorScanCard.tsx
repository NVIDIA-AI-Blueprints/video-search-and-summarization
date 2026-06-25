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
import { Card, CardContent, CardActions, Button, Typography, Grid2 as Grid, CardHeader } from '@mui/material';
import CameraAltIcon from '@mui/icons-material/CameraAlt';
import LOG from '../../../utils/misc/Logger';
import nvAxios from '../../../services/Axios';
import config from '../../../config';
import { useSnackbar } from 'notistack';
import useVSTUIStore from '../../../services/StateManagement';

const SensorScanCard: React.FC = () => {
    const { enqueueSnackbar } = useSnackbar();
    const [isScanning, setIsScanning] = useState<boolean>(false);
    const vstAdaptorType = useVSTUIStore(state => state.vstAdaptorType);

    const handleScan = async () => {
        setIsScanning(true);
        enqueueSnackbar(`Initiating scan`, {
            variant: 'info',
        });
        nvAxios
            .post(`${config.sensorManagementEndpoint}/api/v1/sensor/scan`)
            .then(() => {
                setIsScanning(false);
                enqueueSnackbar(`Scan success`, {
                    variant: 'success',
                });
            })
            .catch(() => {
                LOG.error('Failed to scan for sensors');
                enqueueSnackbar('Failed to scan for sensors', {
                    variant: 'error',
                });
                setIsScanning(false);
            });
    };

    return (
        <Card>
            <CardHeader
                title={vstAdaptorType === 'streamer' ? 'Scan Files' : 'Scan Sensors'}
                subheader={`Scan for ${vstAdaptorType === 'streamer' ? 'files' : 'sensors'}`}
            />
            <CardContent>
                <Grid container spacing={2} alignItems='center'>
                    <Grid size={{ xs: 12 }}>
                        <Typography variant='body1' sx={{ mb: 2 }}>
                            Click the button below to scan for new {vstAdaptorType === 'streamer' ? 'files' : 'sensors'}
                        </Typography>
                        <Typography variant='body2' color='text.secondary'></Typography>
                    </Grid>
                </Grid>
            </CardContent>
            <CardActions>
                <Button variant='contained' color='primary' startIcon={<CameraAltIcon />} onClick={handleScan} disabled={isScanning}>
                    {vstAdaptorType === 'streamer' ? 'Scan for Files' : 'Scan for Sensors'}
                </Button>
            </CardActions>
        </Card>
    );
};

export default SensorScanCard;

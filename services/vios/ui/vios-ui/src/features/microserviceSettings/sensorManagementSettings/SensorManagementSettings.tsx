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
import React, { useState, useEffect } from 'react';
import {
    Box,
    TextField,
    Button,
    Table,
    TableBody,
    TableCell,
    TableContainer,
    TableRow,
    Card,
    CardContent,
    CardActions,
    CardHeader,
    Skeleton,
} from '@mui/material';
import Grid from '@mui/material/Grid2';
import nvAxios from '../../../services/Axios';
import config from '../../../config';
import LOG from '../../../utils/misc/Logger';
import { SensorManagementConfig } from '../../../interfaces/interfaces';

const SensorManagementSettings: React.FC = () => {
    const [configData, setConfigData] = useState<SensorManagementConfig | null>(null);
    const [deviceDiscoveryInterfaces, setDeviceDiscoveryInterfaces] = useState<string>('');
    const [ntpServers, setNtpServers] = useState<string>('');

    useEffect(() => {
        const fetchData = async () => {
            try {
                const response = await nvAxios.get(`${config.sensorManagementEndpoint}/api/v1/sensor/configuration`);
                const data: SensorManagementConfig = response.data;
                setConfigData(data);
                setDeviceDiscoveryInterfaces(data.deviceDiscoveryInterfaces || '');
                setNtpServers(data.ntpServers || '');
            } catch (error) {
                LOG.error(`Failed to get sensor configuration`);
            }
        };

        fetchData();
    }, []);

    const handleSubmit = async () => {
        if (!configData) return;

        const updatedConfig = {
            ...configData,
            deviceDiscoveryInterfaces,
            ntpServers,
        };

        try {
            await nvAxios.post(`${config.sensorManagementEndpoint}/api/v1/sensor/configuration`, updatedConfig);
            setConfigData(updatedConfig);
        } catch (error) {
            LOG.error(`Failed to update sensor configuration`);
        }
    };

    if (!configData) {
        return (
            <Box sx={{ flexGrow: 1, padding: 2 }}>
                <Grid container spacing={3}>
                    <Grid size={{ xs: 12 }}>
                        <Card elevation={2}>
                            <CardHeader
                                title={<Skeleton width='60%' />}
                                subheader={<Skeleton width='40%' />}
                                sx={{ borderBottom: 1, borderColor: 'divider' }}
                            />
                            <CardContent sx={{ pt: 3 }}>
                                <Skeleton height={56} sx={{ mb: 3 }} />
                                <Skeleton height={56} />
                            </CardContent>
                            <CardActions sx={{ px: 2, pb: 2 }}>
                                <Skeleton width={120} height={36} />
                            </CardActions>
                        </Card>
                    </Grid>
                    <Grid size={{ xs: 12 }}>
                        <Card elevation={2}>
                            <CardHeader
                                title={<Skeleton width='60%' />}
                                subheader={<Skeleton width='40%' />}
                                sx={{ borderBottom: 1, borderColor: 'divider' }}
                            />
                            <CardContent>
                                <TableContainer>
                                    <Table size='small' sx={{ '& td, & th': { px: 3, py: 1.5 } }}>
                                        <TableBody>
                                            {[...Array(6)].map((_, index) => (
                                                <TableRow
                                                    key={index}
                                                    sx={{
                                                        '&:nth-of-type(odd)': {
                                                            backgroundColor: 'action.hover',
                                                        },
                                                    }}
                                                >
                                                    <TableCell sx={{ width: '40%' }}>
                                                        <Skeleton width='80%' />
                                                    </TableCell>
                                                    <TableCell>
                                                        <Skeleton width='60%' />
                                                    </TableCell>
                                                </TableRow>
                                            ))}
                                        </TableBody>
                                    </Table>
                                </TableContainer>
                            </CardContent>
                        </Card>
                    </Grid>
                </Grid>
            </Box>
        );
    }

    return (
        <Box sx={{ flexGrow: 1, padding: 2 }}>
            <Grid container spacing={3}>
                <Grid size={{ xs: 12 }}>
                    <Card elevation={2}>
                        <CardHeader
                            title='Sensor Management Configuration'
                            subheader='Editable settings'
                            sx={{ borderBottom: 1, borderColor: 'divider' }}
                        />
                        <CardContent sx={{ pt: 3 }}>
                            <TextField
                                fullWidth
                                label='Device Discovery Interfaces'
                                value={deviceDiscoveryInterfaces}
                                onChange={e => setDeviceDiscoveryInterfaces(e.target.value)}
                                helperText='Enter comma-separated values'
                                margin='normal'
                                sx={{ mb: 3 }}
                            />
                            <TextField
                                fullWidth
                                label='NTP Servers'
                                value={ntpServers}
                                onChange={e => setNtpServers(e.target.value)}
                                helperText='Enter comma-separated values'
                                margin='normal'
                            />
                        </CardContent>
                        <CardActions sx={{ px: 2, pb: 2 }}>
                            <Button variant='contained' color='primary' onClick={handleSubmit} sx={{ px: 4 }}>
                                Save Changes
                            </Button>
                        </CardActions>
                    </Card>
                </Grid>
                <Grid size={{ xs: 12 }}>
                    <Card elevation={2}>
                        <CardHeader
                            title='Sensor Management Configuration'
                            subheader='Read-only settings'
                            sx={{ borderBottom: 1, borderColor: 'divider' }}
                        />
                        <CardContent>
                            <TableContainer>
                                <Table size='small' sx={{ '& td, & th': { px: 3, py: 1.5 } }}>
                                    <TableBody>
                                        {Object.entries(configData).map(([key, value]) => (
                                            <TableRow
                                                key={key}
                                                sx={{
                                                    '&:nth-of-type(odd)': {
                                                        backgroundColor: 'action.hover',
                                                    },
                                                }}
                                            >
                                                <TableCell
                                                    component='th'
                                                    scope='row'
                                                    sx={{
                                                        fontWeight: 'medium',
                                                        width: '40%',
                                                    }}
                                                >
                                                    {key}
                                                </TableCell>
                                                <TableCell
                                                    sx={{
                                                        fontFamily: 'monospace',
                                                    }}
                                                >
                                                    {typeof value === 'boolean' ? value.toString() : JSON.stringify(value, null, 2)}
                                                </TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            </TableContainer>
                        </CardContent>
                    </Card>
                </Grid>
            </Grid>
        </Box>
    );
};

export default SensorManagementSettings;

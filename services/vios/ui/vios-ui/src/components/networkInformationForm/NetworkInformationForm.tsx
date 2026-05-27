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
import { Grid2 as Grid, Card, CardContent, CardActions, TextField, Button, Switch, FormControlLabel, CardHeader } from '@mui/material';
import { useSnackbar } from 'notistack';
import { NetworkConfig, Sensor } from '../../interfaces/interfaces';
import LOG from '../../utils/misc/Logger';
import nvAxios from '../../services/Axios';
import config from '../../config';

const NetworkInformationForm: React.FC<{
    initialData: NetworkConfig;
    sensor: Sensor;
}> = ({ initialData, sensor }) => {
    const [formData, setFormData] = useState<NetworkConfig>(initialData);
    const { enqueueSnackbar } = useSnackbar();

    const handleTextChange = (field: keyof NetworkConfig, value: string) => {
        setFormData({ ...formData, [field]: value });
    };

    const handleSwitchChange = (field: keyof NetworkConfig) => {
        setFormData({
            ...formData,
            [field]: !formData[field as keyof NetworkConfig],
        });
    };

    const handleSubmit = () => {
        LOG.verbose('Submitting data:', formData);
        nvAxios
            .post(`${config.sensorManagementEndpoint}/api/v1/sensor/${sensor?.sensorId}/network`, formData, {
                headers: { streamId: sensor?.sensorId },
            })
            .then(() => {
                enqueueSnackbar('Network settings updated successfully', { variant: 'success' });
            })
            .catch(() => {
                LOG.error(`Failed to post sensor information for ${sensor?.name}`);
                enqueueSnackbar('Failed to update network settings', { variant: 'error' });
            });
    };

    return (
        <Card>
            <CardHeader title={'Network Details'} subheader={`General information about the network`} />
            <CardContent>
                <Grid container spacing={2}>
                    <Grid size={{ xs: 6 }}>
                        <TextField
                            fullWidth
                            label='DHCP V4'
                            value={formData.dhcpV4}
                            onChange={e => handleTextChange('dhcpV4', e.target.value)}
                        />
                    </Grid>
                    <Grid size={{ xs: 6 }}>
                        <TextField
                            fullWidth
                            label='DHCP V6'
                            value={formData.dhcpV6}
                            onChange={e => handleTextChange('dhcpV6', e.target.value)}
                        />
                    </Grid>
                    <Grid size={{ xs: 6 }}>
                        <TextField
                            fullWidth
                            label='IP Address V4'
                            value={formData.ipAddressV4}
                            onChange={e => handleTextChange('ipAddressV4', e.target.value)}
                        />
                    </Grid>
                    <Grid size={{ xs: 6 }}>
                        <TextField
                            fullWidth
                            label='IP Address V6'
                            value={formData.ipAddressV6}
                            onChange={e => handleTextChange('ipAddressV6', e.target.value)}
                        />
                    </Grid>
                    <Grid size={{ xs: 6 }}>
                        <FormControlLabel
                            control={<Switch checked={formData.isIpv4Enabled} onChange={() => handleSwitchChange('isIpv4Enabled')} />}
                            label='IPv4 Enabled'
                        />
                    </Grid>
                    <Grid size={{ xs: 6 }}>
                        <FormControlLabel
                            control={<Switch checked={formData.isIpv6Enabled} onChange={() => handleSwitchChange('isIpv6Enabled')} />}
                            label='IPv6 Enabled'
                        />
                    </Grid>
                    <Grid size={{ xs: 6 }}>
                        <TextField
                            fullWidth
                            label='Subnet Mask V4'
                            value={formData.subnetMaskV4}
                            onChange={e => handleTextChange('subnetMaskV4', e.target.value)}
                        />
                    </Grid>
                    <Grid size={{ xs: 6 }}>
                        <TextField
                            fullWidth
                            label='Subnet Mask V6'
                            value={formData.subnetMaskV6}
                            onChange={e => handleTextChange('subnetMaskV6', e.target.value)}
                        />
                    </Grid>
                </Grid>
            </CardContent>
            <CardActions>
                <Button variant='contained' color='primary' onClick={handleSubmit}>
                    Submit
                </Button>
            </CardActions>
        </Card>
    );
};

export default NetworkInformationForm;

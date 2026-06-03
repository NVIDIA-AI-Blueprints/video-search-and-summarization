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
import React, { /*useCallback,*/ useState } from 'react';
import { Card, CardContent, CardActions, Button, Grid2 as Grid, TextField, CardHeader, Switch, FormControlLabel } from '@mui/material';
import AddAPhotoIcon from '@mui/icons-material/AddAPhoto';
import { AddNewSensorPayload /*Sensor*/ } from '../../../interfaces/interfaces';
import LOG from '../../../utils/misc/Logger';
import nvAxios from '../../../services/Axios';
import config from '../../../config';
import { useSnackbar } from 'notistack';
// import useVSTUIStore from '../../../services/nvStateManagement';
import { updateSensorsAndStreams } from '../../../utils/misc/updateSensorsAndStreams';

const AddNewSensorCard: React.FC = () => {
    const { enqueueSnackbar } = useSnackbar();
    // const addSensor = useVSTUIStore((state) => state.addSensor);
    const [username, setUsername] = useState<string>('');
    const [password, setPassword] = useState<string>('');
    const [ipAddress, setIPAddress] = useState<string>('');
    const [rtspURL, setRTSPURL] = useState<string>('');
    const [name, setName] = useState<string>('');
    const [location, setLocation] = useState<string>('');
    const [tags, setTags] = useState<string>('');
    const [isUpdating, setIsUpdating] = useState<boolean>(false);
    const [isRTSP, setIsRTSP] = useState(true);
    const [verifyRtsp, setVerifyRtsp] = useState<boolean>(false);

    const handleNewSensorUpdate = async () => {
        setIsUpdating(true);
        const payload: AddNewSensorPayload = {
            username,
            password,
            name,
            location,
            tags,
        };
        if (isRTSP) {
            payload.sensorUrl = rtspURL;
            payload.verifyRtsp = verifyRtsp;
        } else {
            payload.sensorIp = ipAddress;
        }
        enqueueSnackbar(`Initiating sensor add`, {
            variant: 'info',
        });
        nvAxios
            .post(`${config.sensorManagementEndpoint}/api/v1/sensor/add`, payload)
            .then(async () => {
                setIsUpdating(false);
                // Update the sensor list from the backend
                await updateSensorsAndStreams();
                enqueueSnackbar(`Sensor added`, {
                    variant: 'success',
                });
                // Clear the form
                setUsername('');
                setPassword('');
                setIPAddress('');
                setRTSPURL('');
                setName('');
                setLocation('');
                setTags('');
                setVerifyRtsp(false);
            })
            .catch(error => {
                const errorMessage = error.response?.data?.error_message || 'Failed to add sensor';
                LOG.error(`Failed to add sensor: ${errorMessage}`);
                enqueueSnackbar(errorMessage, {
                    variant: 'error',
                });
                setIsUpdating(false);
            });
    };

    return (
        <Card>
            <CardHeader title={'Add new sensors'} subheader={`Add new sensors to VST using either RTSP URL or IP Address`} />
            <CardContent>
                <Grid container spacing={2}>
                    <Grid size={{ xs: 12 }}>
                        <FormControlLabel
                            control={<Switch checked={isRTSP} onChange={() => setIsRTSP(!isRTSP)} />}
                            label={isRTSP ? 'RTSP URL' : 'IP Address'}
                        />
                        <FormControlLabel
                            control={<Switch checked={verifyRtsp} onChange={() => setVerifyRtsp(!verifyRtsp)} disabled={!isRTSP} />}
                            label='Verify RTSP'
                        />
                    </Grid>
                    {!isRTSP && (
                        <Grid size={{ xs: 12 }}>
                            <TextField fullWidth label='IP address' value={ipAddress} onChange={e => setIPAddress(e.target.value)} />
                        </Grid>
                    )}
                    {isRTSP && (
                        <Grid size={{ xs: 12 }}>
                            <TextField fullWidth label='RTSP URL' value={rtspURL} onChange={e => setRTSPURL(e.target.value)} />
                        </Grid>
                    )}
                    <Grid size={{ xs: 12 }}>
                        <TextField fullWidth label='Location' value={location} onChange={e => setLocation(e.target.value)} />
                    </Grid>
                    <Grid size={{ xs: 12 }}>
                        <TextField fullWidth label='Name' value={name} onChange={e => setName(e.target.value)} />
                    </Grid>
                    <Grid size={{ xs: 12 }}>
                        <TextField
                            fullWidth
                            label='Tags'
                            value={tags}
                            onChange={e => setTags(e.target.value)}
                            placeholder='tag1, tag2... tagN'
                        />
                    </Grid>
                    <Grid size={{ xs: 12 }}>
                        <TextField fullWidth label='Username' value={username} onChange={e => setUsername(e.target.value)} />
                    </Grid>
                    <Grid size={{ xs: 12 }}>
                        <TextField
                            fullWidth
                            label='Password'
                            type='password'
                            value={password}
                            onChange={e => setPassword(e.target.value)}
                        />
                    </Grid>
                </Grid>
            </CardContent>
            <CardActions>
                <Button
                    variant='contained'
                    color='primary'
                    startIcon={<AddAPhotoIcon />}
                    onClick={handleNewSensorUpdate}
                    disabled={isUpdating || (isRTSP ? !rtspURL : !ipAddress)}
                >
                    Add Sensor
                </Button>
            </CardActions>
        </Card>
    );
};

export default AddNewSensorCard;

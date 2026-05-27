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
import { Grid2 as Grid, Box, Alert, Typography, Card, CardHeader, CardContent } from '@mui/material';
import SensorVideoDownloadCard from '../../features/sensorManagement/sensorVideoDownload/SensorVideoDownload';
import SensorVideoDelete from '../../features/sensorManagement/sensorVideoDelete/SensorVideoDelete';
import MediaUpload from '../nvstreamer/MediaUpload';
import useVSTUIStore from '../../services/StateManagement';

const MediaManagement: React.FC = () => {
    const isStorageServiceAvailable = useVSTUIStore(state => state.isStoragemanagementServiceAvailable);

    const containerStyle = {
        opacity: isStorageServiceAvailable ? 1 : 0.5,
        pointerEvents: isStorageServiceAvailable ? 'auto' : 'none',
        position: 'relative' as const,
    };

    return (
        <Grid container spacing={3}>
            <Grid size={{ xs: 12 }}>
                <Typography variant='h4' gutterBottom>
                    Media Management
                </Typography>
                {!isStorageServiceAvailable && (
                    <Box mb={2}>
                        <Alert severity='error'>Storage service is not available. Storage management features are disabled.</Alert>
                    </Box>
                )}
            </Grid>

            <Box sx={containerStyle} width='100%'>
                <Grid container spacing={3}>
                    {/* Media Management Actions */}
                    <Grid size={{ xs: 12, md: 6 }}>
                        <SensorVideoDownloadCard />
                    </Grid>
                    <Grid size={{ xs: 12, md: 6 }}>
                        <SensorVideoDelete />
                    </Grid>

                    {/* Media Upload Section */}
                    <Grid size={{ xs: 12 }}>
                        <Card>
                            <CardHeader
                                title='Upload Media'
                                subheader='Upload video files with metadata information. Single or bulk upload supported.'
                            />
                            <CardContent>
                                <MediaUpload />
                            </CardContent>
                        </Card>
                    </Grid>
                </Grid>
            </Box>
        </Grid>
    );
};

export default MediaManagement;

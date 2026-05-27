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
import { Grid2 as Grid, Typography } from '@mui/material';
import DeleteFilesSection from '../../components/deleteFiles/DeleteFilesSection';
import SensorScanCard from '../../features/sensorManagement/sensorScanCard/SensorScanCard';

const MediaManagement = () => {
    return (
        <Grid container spacing={3}>
            <Grid size={{ xs: 12 }}>
                <Typography variant='h4' gutterBottom>
                    Media Management
                </Typography>
            </Grid>
            <Grid size={{ xs: 12, md: 6 }}>
                <SensorScanCard />
            </Grid>
            <Grid size={{ xs: 12, md: 6 }}>
                <DeleteFilesSection />
            </Grid>
        </Grid>
    );
};

export default MediaManagement;

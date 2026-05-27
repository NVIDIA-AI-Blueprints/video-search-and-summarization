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
import { Typography, Box, Grid } from '@mui/material';
import RTSPTable from '../../features/dashboardTables/RTSPTable';
import VideoFilesWidget from '../../features/dashboardWidgets/videoFilesWidget';
import SensorsWithErrorWidget from '../../features/dashboardWidgets/SensorsWithErrorWidget';
import DashboardWidgetErrorBoundary from '../../components/errorBoundary/DashboardWidgetErrorBoundary';

const StreamerDashboard = () => {
    return (
        <>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 5 }}>
                <Typography variant='h4'>Dashboard</Typography>
            </Box>
            <Grid container spacing={3} justifyContent='center'>
                <Grid item xs={12} sm={6} md={6}>
                    <Box sx={{ width: '100%' }}>
                        <DashboardWidgetErrorBoundary widgetName='Video Files'>
                            <VideoFilesWidget />
                        </DashboardWidgetErrorBoundary>
                    </Box>
                </Grid>
                <Grid item xs={12} sm={6} md={6}>
                    <Box sx={{ width: '100%' }}>
                        <DashboardWidgetErrorBoundary widgetName='Media Files with Error'>
                            <SensorsWithErrorWidget />
                        </DashboardWidgetErrorBoundary>
                    </Box>
                </Grid>
                <Grid item xs={12}>
                    <DashboardWidgetErrorBoundary widgetName='RTSP Table'>
                        <Box>
                            <RTSPTable />
                        </Box>
                    </DashboardWidgetErrorBoundary>
                </Grid>
            </Grid>
        </>
    );
};

export default StreamerDashboard;

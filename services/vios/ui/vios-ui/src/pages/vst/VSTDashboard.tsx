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
import React, { useEffect } from 'react';
import { Box, Grid2 as Grid } from '@mui/material';
import SensorInformationTable from '../../features/dashboardTables/SensorInformationTable';
import OfflineSensorsWidget from '../../features/dashboardWidgets/OfflineSensorsWidget';
import SensorsWithErrorWidget from '../../features/dashboardWidgets/SensorsWithErrorWidget';
import TotalSensorsWidget from '../../features/dashboardWidgets/TotalSensorsWidget';
import TotalRecordSize from '../../features/dashboardWidgets/TotalRecordSizeWidget';
import RecordingGapsWidget from '../../features/dashboardWidgets/RecordingGapsWidget';
import TimelinesGapTable from '../../features/dashboardTables/TimelinesGapTable';
import RecordingSensorsWidget from '../../features/dashboardWidgets/RecordingSensorsWidget';
import NonRecordingSensorsWidget from '../../features/dashboardWidgets/NonRecordingSensorsWidget';
import DisconnectedSensorsRecordSizeWidget from '../../features/dashboardWidgets/DisconnectedSensorsRecordSizeWidget';
import DashboardWidgetErrorBoundary from '../../components/errorBoundary/DashboardWidgetErrorBoundary';
import DashboardStatusIndicator from '../../components/dashboardStatus/DashboardStatusIndicator';
import { updateSensorsAndStreams } from '../../utils/misc/updateSensorsAndStreams';
import useVSTUIStore from '../../services/StateManagement';

const VSTDashboard: React.FC = () => {
    const isSensorServiceAvailable = useVSTUIStore(state => state.isSensormanagementServiceAvailable);
    const isRecorderServiceAvailable = useVSTUIStore(state => state.isRecorderServiceAvailable);

    useEffect(() => {
        // Initial data fetch
        updateSensorsAndStreams();
    }, []);

    return (
        <>
            <Grid container spacing={3}>
                <Grid size={{ xs: 12 }}>
                    <Box
                        sx={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
                            mb: 2,
                        }}
                    >
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                            <h1>Dashboard</h1>
                            <DashboardStatusIndicator />
                        </Box>
                    </Box>
                </Grid>
                <Grid size={{ xs: 12, sm: 6, md: 3 }}>
                    <DashboardWidgetErrorBoundary widgetName='Total Sensors'>
                        <TotalSensorsWidget />
                    </DashboardWidgetErrorBoundary>
                </Grid>
                <Grid size={{ xs: 12, sm: 6, md: 3 }}>
                    <Box
                        sx={{
                            opacity: isSensorServiceAvailable ? 1 : 0.5,
                            pointerEvents: isSensorServiceAvailable ? 'auto' : 'none',
                            filter: isSensorServiceAvailable ? 'none' : 'grayscale(1)',
                            transition: 'all 0.3s ease-in-out',
                        }}
                    >
                        <DashboardWidgetErrorBoundary widgetName='Offline Sensors'>
                            <OfflineSensorsWidget />
                        </DashboardWidgetErrorBoundary>
                    </Box>
                </Grid>
                <Grid size={{ xs: 12, sm: 6, md: 3 }}>
                    <Box
                        sx={{
                            opacity: isSensorServiceAvailable ? 1 : 0.5,
                            pointerEvents: isSensorServiceAvailable ? 'auto' : 'none',
                            filter: isSensorServiceAvailable ? 'none' : 'grayscale(1)',
                            transition: 'all 0.3s ease-in-out',
                        }}
                    >
                        <DashboardWidgetErrorBoundary widgetName='Sensors with Error'>
                            <SensorsWithErrorWidget />
                        </DashboardWidgetErrorBoundary>
                    </Box>
                </Grid>
                <Grid size={{ xs: 12, sm: 6, md: 3 }}>
                    <DashboardWidgetErrorBoundary widgetName='Total Record Size'>
                        <TotalRecordSize />
                    </DashboardWidgetErrorBoundary>
                </Grid>
                <Grid size={{ xs: 12, sm: 6, md: 3 }}>
                    <DashboardWidgetErrorBoundary widgetName='Recording Gaps'>
                        <RecordingGapsWidget />
                    </DashboardWidgetErrorBoundary>
                </Grid>
                <Grid size={{ xs: 12, sm: 6, md: 3 }}>
                    <Box
                        sx={{
                            opacity: isRecorderServiceAvailable ? 1 : 0.5,
                            pointerEvents: isRecorderServiceAvailable ? 'auto' : 'none',
                            filter: isRecorderServiceAvailable ? 'none' : 'grayscale(1)',
                            transition: 'all 0.3s ease-in-out',
                        }}
                    >
                        <DashboardWidgetErrorBoundary widgetName='Recording Sensors'>
                            <RecordingSensorsWidget />
                        </DashboardWidgetErrorBoundary>
                    </Box>
                </Grid>
                <Grid size={{ xs: 12, sm: 6, md: 3 }}>
                    <Box
                        sx={{
                            opacity: isRecorderServiceAvailable ? 1 : 0.5,
                            pointerEvents: isRecorderServiceAvailable ? 'auto' : 'none',
                            filter: isRecorderServiceAvailable ? 'none' : 'grayscale(1)',
                            transition: 'all 0.3s ease-in-out',
                        }}
                    >
                        <DashboardWidgetErrorBoundary widgetName='Non-Recording Sensors'>
                            <NonRecordingSensorsWidget />
                        </DashboardWidgetErrorBoundary>
                    </Box>
                </Grid>
                <Grid size={{ xs: 12, sm: 6, md: 3 }}>
                    <DashboardWidgetErrorBoundary widgetName='Disconnected Sensors Record Size'>
                        <DisconnectedSensorsRecordSizeWidget />
                    </DashboardWidgetErrorBoundary>
                </Grid>
                <Grid size={{ xs: 12 }}>
                    <DashboardWidgetErrorBoundary widgetName='Sensor Information Table'>
                        <Box
                            sx={{
                                opacity: isRecorderServiceAvailable ? 1 : 0.5,
                                pointerEvents: isRecorderServiceAvailable ? 'auto' : 'none',
                                filter: isRecorderServiceAvailable ? 'none' : 'grayscale(1)',
                                transition: 'all 0.3s ease-in-out',
                            }}
                        >
                            <SensorInformationTable />
                        </Box>
                    </DashboardWidgetErrorBoundary>
                </Grid>
                <Grid size={{ xs: 12 }}>
                    <DashboardWidgetErrorBoundary widgetName='Timelines Gap Table'>
                        <TimelinesGapTable />
                    </DashboardWidgetErrorBoundary>
                </Grid>
            </Grid>
        </>
    );
};

export default VSTDashboard;

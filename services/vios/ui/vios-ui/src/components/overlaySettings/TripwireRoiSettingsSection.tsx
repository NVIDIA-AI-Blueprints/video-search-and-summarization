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
import { Box, Grid, Typography, FormControlLabel, Switch } from '@mui/material';

interface TripwireRoiSettingsSectionProps {
    overlayTripwire: boolean;
    setOverlayTripwire: (value: boolean) => void;
    overlayRoi: boolean;
    setOverlayRoi: (value: boolean) => void;
}

const TripwireRoiSettingsSection: React.FC<TripwireRoiSettingsSectionProps> = ({
    overlayTripwire,
    setOverlayTripwire,
    overlayRoi,
    setOverlayRoi,
}) => {
    return (
        <Box
            sx={{
                bgcolor: 'rgba(0, 0, 0, 0.02)',
                borderRadius: 2,
                p: 2,
                mb: 2,
                border: '1px solid rgba(0, 0, 0, 0.08)',
            }}
        >
            <Typography variant='h6' sx={{ mb: 1.5, fontWeight: 600, color: 'primary.main' }}>
                Tripwire and ROI
            </Typography>
            <Grid container spacing={2}>
                <Grid item xs={12} sm={6}>
                    <FormControlLabel
                        control={<Switch checked={overlayTripwire} onChange={e => setOverlayTripwire(e.target.checked)} />}
                        label='Show Tripwires'
                        sx={{ '& .MuiFormControlLabel-label': { fontWeight: 500 } }}
                    />
                </Grid>
                <Grid item xs={12} sm={6}>
                    <FormControlLabel
                        control={<Switch checked={overlayRoi} onChange={e => setOverlayRoi(e.target.checked)} />}
                        label='Show ROI'
                        sx={{ '& .MuiFormControlLabel-label': { fontWeight: 500 } }}
                    />
                </Grid>
            </Grid>
        </Box>
    );
};

export default TripwireRoiSettingsSection;

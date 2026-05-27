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
import { Dialog, DialogTitle, DialogContent, DialogActions, Button, TextField, Box } from '@mui/material';
import { CoordinatePoint, TripwireCoordinates } from './AnalyticsTypes';

interface AnalyticsNameDialogProps {
    open: boolean;
    roiPoints: CoordinatePoint[];
    tripwirePoints: TripwireCoordinates | null;
    directionPoints: TripwireCoordinates | null;
    roiName: string;
    tripwireName: string;
    onRoiNameChange: (name: string) => void;
    onTripwireNameChange: (name: string) => void;
    onSubmit: () => void;
    onCancel: () => void;
}

const AnalyticsNameDialog: React.FC<AnalyticsNameDialogProps> = ({
    open,
    roiPoints,
    tripwirePoints,
    directionPoints,
    roiName,
    tripwireName,
    onRoiNameChange,
    onTripwireNameChange,
    onSubmit,
    onCancel,
}) => {
    const hasROI = roiPoints.length >= 3;
    const hasTripwire = tripwirePoints && directionPoints;

    const getDialogTitle = () => {
        if (hasROI && hasTripwire) {
            return 'Enter Names for ROI and Tripwire';
        } else if (hasROI) {
            return 'Enter Names for ROI';
        } else {
            return 'Enter Names for Tripwire';
        }
    };

    const isSubmitDisabled = () => {
        if (hasROI && !roiName.trim()) return true;
        if (hasTripwire && !tripwireName.trim()) return true;
        return false;
    };

    return (
        <Dialog open={open} onClose={onCancel} maxWidth='sm' fullWidth>
            <DialogTitle>{getDialogTitle()}</DialogTitle>
            <DialogContent>
                <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
                    {hasROI && (
                        <TextField
                            label='ROI Name'
                            value={roiName}
                            onChange={e => onRoiNameChange(e.target.value)}
                            fullWidth
                            required
                            placeholder='Enter a name for the ROI'
                        />
                    )}
                    {hasTripwire && (
                        <TextField
                            label='Tripwire Name'
                            value={tripwireName}
                            onChange={e => onTripwireNameChange(e.target.value)}
                            fullWidth
                            required
                            placeholder='Enter a name for the Tripwire'
                        />
                    )}
                </Box>
            </DialogContent>
            <DialogActions>
                <Button onClick={onCancel} color='secondary'>
                    Cancel
                </Button>
                <Button onClick={onSubmit} color='primary' variant='contained' disabled={isSubmitDisabled()}>
                    Submit
                </Button>
            </DialogActions>
        </Dialog>
    );
};

export default AnalyticsNameDialog;

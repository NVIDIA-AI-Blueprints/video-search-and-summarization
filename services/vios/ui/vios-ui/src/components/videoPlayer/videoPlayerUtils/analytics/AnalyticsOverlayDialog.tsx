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
import React, { useRef } from 'react';
import { Dialog, DialogTitle, DialogContent, DialogActions, Button } from '@mui/material';
import { StreamOverlayOptions, StreamCompositeOptions, StreamType } from 'vst-streaming-lib';
import { Sensor } from '../../../../interfaces/interfaces';
import { OverlaySettingsPanel } from '../../../overlaySettings';
import type { OverlaySettingsPanelHandle } from '../../../overlaySettings';

interface AnalyticsOverlayDialogProps {
    open: boolean;
    onClose: () => void;
    onSave: (settings: { overlay: StreamOverlayOptions; composite?: StreamCompositeOptions; framerate?: number }, tag?: string) => void;
    sensors?: Sensor[];
    streamType?: StreamType;
}

const AnalyticsOverlayDialog: React.FC<AnalyticsOverlayDialogProps> = ({ open, onClose, onSave, sensors, streamType }) => {
    const panelRef = useRef<OverlaySettingsPanelHandle>(null);

    const handleSave = () => {
        panelRef.current?.save();
        onClose();
    };

    return (
        <Dialog open={open} onClose={onClose} maxWidth='md' fullWidth>
            <DialogTitle
                sx={{
                    borderBottom: '1px solid rgba(0, 0, 0, 0.08)',
                    fontWeight: 600,
                    fontSize: '1.25rem',
                }}
            >
                Analytics Overlay Settings
            </DialogTitle>
            <DialogContent sx={{ p: 0 }}>
                <OverlaySettingsPanel ref={panelRef} onSettingsChange={onSave} sensors={sensors} streamType={streamType} />
            </DialogContent>
            <DialogActions
                sx={{
                    borderTop: '1px solid rgba(0, 0, 0, 0.08)',
                    p: 2,
                    gap: 2,
                    bgcolor: 'rgba(0, 0, 0, 0.01)',
                }}
            >
                <Button onClick={onClose} color='primary' sx={{ fontWeight: 500, px: 3, py: 1, borderRadius: 2 }}>
                    Cancel
                </Button>
                <Button
                    onClick={handleSave}
                    color='primary'
                    variant='contained'
                    sx={{
                        fontWeight: 600,
                        px: 4,
                        py: 1,
                        borderRadius: 2,
                        boxShadow: 2,
                        '&:hover': { boxShadow: 4 },
                    }}
                >
                    Save Settings
                </Button>
            </DialogActions>
        </Dialog>
    );
};

export default AnalyticsOverlayDialog;

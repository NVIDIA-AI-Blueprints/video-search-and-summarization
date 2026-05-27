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
import {
    IconButton,
    Menu,
    MenuItem,
    ListItemText,
    Tooltip,
    Dialog,
    DialogTitle,
    DialogContent,
    DialogActions,
    Button,
    TextField,
    Typography,
    Box,
    Tooltip as MuiTooltip,
    Switch,
    FormControlLabel,
} from '@mui/material';
import { Settings as SettingsIcon, Info as InfoIcon } from '@mui/icons-material';
import useSettings from '../../hooks/useSettings';
import { DEFAULT_NETWORK_QUALITY_SETTINGS } from '../../theme/defaultSettings';

const DEFAULT_SETTINGS = {
    networkQualityWidget: DEFAULT_NETWORK_QUALITY_SETTINGS,
};

const SettingsDropdown: React.FC = () => {
    const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null);
    const [showNetworkQualitySettings, setShowNetworkQualitySettings] = useState(false);
    const { settings, updateSettings } = useSettings();
    const open = Boolean(anchorEl);

    const handleClick = (event: React.MouseEvent<HTMLElement>) => {
        setAnchorEl(event.currentTarget);
    };

    const handleClose = () => {
        setAnchorEl(null);
    };

    const handleThemeToggle = () => {
        updateSettings({
            theme: settings.theme === 'dark' ? 'light' : 'dark',
        });
        handleClose();
    };

    const handleNetworkQualitySettings = () => {
        setShowNetworkQualitySettings(true);
        handleClose();
    };

    const handleNetworkQualitySettingsClose = () => {
        setShowNetworkQualitySettings(false);
    };

    const handleNetworkQualitySettingsSave = (event: React.FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        const formData = new FormData(event.currentTarget);
        const newSettings = {
            networkQualityWidget: {
                initialDelayMs: Number(formData.get('initialDelayMs')),
                consecutiveIssuesThreshold: Number(formData.get('consecutiveIssuesThreshold')),
                widgetDisplayDurationMs: Number(formData.get('widgetDisplayDurationMs')),
                userHideDurationMs: Number(formData.get('userHideDurationMs')),
                maxGraphPoints: Number(formData.get('maxGraphPoints')),
                thresholds: {
                    severePacketLoss: Number(formData.get('severePacketLoss')),
                    severeJitterMs: Number(formData.get('severeJitterMs')),
                    lowFps: Number(formData.get('lowFps')),
                    highPli: Number(formData.get('highPli')),
                    highNack: Number(formData.get('highNack')),
                    highFir: Number(formData.get('highFir')),
                    highJitterMs: Number(formData.get('highJitterMs')),
                    moderatePacketLoss: Number(formData.get('moderatePacketLoss')),
                    moderateNack: Number(formData.get('moderateNack')),
                    moderatePli: Number(formData.get('moderatePli')),
                    moderateFir: Number(formData.get('moderateFir')),
                    highLatencyMs: Number(formData.get('highLatencyMs')),
                },
            },
        };
        updateSettings(newSettings);
        handleNetworkQualitySettingsClose();
    };

    const renderSettingField = (name: string, label: string, defaultValue: number, currentValue?: number, tooltip?: string) => {
        const isDefault = currentValue === undefined || currentValue === defaultValue;
        return (
            <Box sx={{ position: 'relative' }}>
                <TextField
                    name={name}
                    label={label}
                    type='number'
                    defaultValue={currentValue ?? defaultValue}
                    fullWidth
                    InputProps={{
                        endAdornment: (
                            <MuiTooltip title={tooltip || `Default: ${defaultValue}`}>
                                <InfoIcon fontSize='small' color={isDefault ? 'disabled' : 'primary'} sx={{ ml: 1 }} />
                            </MuiTooltip>
                        ),
                    }}
                />
                {!isDefault && (
                    <Typography
                        variant='caption'
                        color='primary'
                        sx={{
                            position: 'absolute',
                            right: 0,
                            top: -20,
                        }}
                    >
                        Customized
                    </Typography>
                )}
            </Box>
        );
    };

    return (
        <>
            <Tooltip title='UI Settings'>
                <IconButton
                    color='inherit'
                    onClick={handleClick}
                    aria-controls={open ? 'settings-menu' : undefined}
                    aria-haspopup='true'
                    aria-expanded={open ? 'true' : undefined}
                >
                    <SettingsIcon />
                </IconButton>
            </Tooltip>
            <Menu
                anchorEl={anchorEl}
                id='settings-menu'
                open={open}
                onClose={handleClose}
                onClick={handleClose}
                transformOrigin={{ horizontal: 'right', vertical: 'top' }}
                anchorOrigin={{ horizontal: 'right', vertical: 'bottom' }}
            >
                <MenuItem sx={{ minWidth: '200px' }}>
                    <FormControlLabel
                        control={<Switch checked={settings.theme === 'dark'} onChange={handleThemeToggle} color='primary' />}
                        label={`${settings.theme === 'dark' ? 'Dark' : 'Light'} Mode`}
                        sx={{ width: '100%' }}
                    />
                </MenuItem>
                <MenuItem onClick={handleNetworkQualitySettings}>
                    <ListItemText>Network Quality Widget</ListItemText>
                </MenuItem>
            </Menu>

            <Dialog open={showNetworkQualitySettings} onClose={handleNetworkQualitySettingsClose} maxWidth='md' fullWidth>
                <form onSubmit={handleNetworkQualitySettingsSave}>
                    <DialogTitle>Network Quality Widget Settings</DialogTitle>
                    <DialogContent>
                        <Box
                            sx={{
                                display: 'grid',
                                gridTemplateColumns: 'repeat(2, 1fr)',
                                gap: 2,
                                mt: 2,
                            }}
                        >
                            <Typography variant='h6' gridColumn='span 2'>
                                Display Settings
                            </Typography>
                            {renderSettingField(
                                'initialDelayMs',
                                'Initial Delay (ms)',
                                DEFAULT_SETTINGS.networkQualityWidget.initialDelayMs,
                                settings.networkQualityWidget?.initialDelayMs,
                                'Delay before starting to monitor stats'
                            )}
                            {renderSettingField(
                                'consecutiveIssuesThreshold',
                                'Consecutive Issues Threshold',
                                DEFAULT_SETTINGS.networkQualityWidget.consecutiveIssuesThreshold,
                                settings.networkQualityWidget?.consecutiveIssuesThreshold,
                                'Number of consecutive issues before showing widget'
                            )}
                            {renderSettingField(
                                'widgetDisplayDurationMs',
                                'Widget Display Duration (ms)',
                                DEFAULT_SETTINGS.networkQualityWidget.widgetDisplayDurationMs,
                                settings.networkQualityWidget?.widgetDisplayDurationMs,
                                'How long to show the widget'
                            )}
                            {renderSettingField(
                                'userHideDurationMs',
                                'User Hide Duration (ms)',
                                DEFAULT_SETTINGS.networkQualityWidget.userHideDurationMs,
                                settings.networkQualityWidget?.userHideDurationMs,
                                'How long to stay hidden after user dismisses (5 minutes)'
                            )}
                            {renderSettingField(
                                'maxGraphPoints',
                                'Max Graph Points',
                                DEFAULT_SETTINGS.networkQualityWidget.maxGraphPoints,
                                settings.networkQualityWidget?.maxGraphPoints,
                                'Number of points to show in graphs'
                            )}

                            <Typography variant='h6' gridColumn='span 2' sx={{ mt: 2 }}>
                                Critical Issues (Red)
                            </Typography>
                            {renderSettingField(
                                'severePacketLoss',
                                'Severe Packet Loss',
                                DEFAULT_SETTINGS.networkQualityWidget.thresholds.severePacketLoss,
                                settings.networkQualityWidget?.thresholds.severePacketLoss,
                                'packetsLost threshold for critical issues'
                            )}
                            {renderSettingField(
                                'severeJitterMs',
                                'Severe Jitter (ms)',
                                DEFAULT_SETTINGS.networkQualityWidget.thresholds.severeJitterMs,
                                settings.networkQualityWidget?.thresholds.severeJitterMs,
                                'jitter threshold for critical issues'
                            )}
                            {renderSettingField(
                                'lowFps',
                                'Low FPS',
                                DEFAULT_SETTINGS.networkQualityWidget.thresholds.lowFps,
                                settings.networkQualityWidget?.thresholds.lowFps,
                                'fps threshold for critical issues'
                            )}

                            <Typography variant='h6' gridColumn='span 2' sx={{ mt: 2 }}>
                                Warning Issues (Orange)
                            </Typography>
                            {renderSettingField(
                                'highPli',
                                'High PLI',
                                DEFAULT_SETTINGS.networkQualityWidget.thresholds.highPli,
                                settings.networkQualityWidget?.thresholds.highPli,
                                'PLI threshold for warnings'
                            )}
                            {renderSettingField(
                                'highNack',
                                'High NACK',
                                DEFAULT_SETTINGS.networkQualityWidget.thresholds.highNack,
                                settings.networkQualityWidget?.thresholds.highNack,
                                'NACK threshold for warnings'
                            )}
                            {renderSettingField(
                                'highFir',
                                'High FIR',
                                DEFAULT_SETTINGS.networkQualityWidget.thresholds.highFir,
                                settings.networkQualityWidget?.thresholds.highFir,
                                'FIR threshold for warnings'
                            )}
                            {renderSettingField(
                                'highJitterMs',
                                'High Jitter (ms)',
                                DEFAULT_SETTINGS.networkQualityWidget.thresholds.highJitterMs,
                                settings.networkQualityWidget?.thresholds.highJitterMs,
                                'jitter threshold for congestion warnings'
                            )}
                            {renderSettingField(
                                'moderatePacketLoss',
                                'Moderate Packet Loss',
                                DEFAULT_SETTINGS.networkQualityWidget.thresholds.moderatePacketLoss,
                                settings.networkQualityWidget?.thresholds.moderatePacketLoss,
                                'packetsLost threshold for warnings'
                            )}
                            {renderSettingField(
                                'moderateNack',
                                'Moderate NACK',
                                DEFAULT_SETTINGS.networkQualityWidget.thresholds.moderateNack,
                                settings.networkQualityWidget?.thresholds.moderateNack,
                                'NACK threshold for warnings'
                            )}
                            {renderSettingField(
                                'moderatePli',
                                'Moderate PLI',
                                DEFAULT_SETTINGS.networkQualityWidget.thresholds.moderatePli,
                                settings.networkQualityWidget?.thresholds.moderatePli,
                                'PLI threshold for warnings'
                            )}
                            {renderSettingField(
                                'moderateFir',
                                'Moderate FIR',
                                DEFAULT_SETTINGS.networkQualityWidget.thresholds.moderateFir,
                                settings.networkQualityWidget?.thresholds.moderateFir,
                                'FIR threshold for warnings'
                            )}
                            {renderSettingField(
                                'highLatencyMs',
                                'High Latency (ms)',
                                DEFAULT_SETTINGS.networkQualityWidget.thresholds.highLatencyMs,
                                settings.networkQualityWidget?.thresholds.highLatencyMs,
                                'RTT threshold for warnings'
                            )}
                        </Box>
                    </DialogContent>
                    <DialogActions>
                        <Button onClick={handleNetworkQualitySettingsClose}>Cancel</Button>
                        <Button type='submit' variant='contained'>
                            Save
                        </Button>
                    </DialogActions>
                </form>
            </Dialog>
        </>
    );
};

export default SettingsDropdown;

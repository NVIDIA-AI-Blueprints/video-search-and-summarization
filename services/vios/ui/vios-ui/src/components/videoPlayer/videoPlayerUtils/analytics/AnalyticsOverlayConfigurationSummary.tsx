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
import {
    Table,
    TableBody,
    TableCell,
    TableContainer,
    TableHead,
    TableRow,
    Paper,
    Chip,
    Box,
    Typography,
    useTheme,
    alpha,
} from '@mui/material';
import { StreamOverlayOptions } from 'vst-streaming-lib';

interface AnalyticsOverlayConfigurationSummaryProps {
    overlaySettings: StreamOverlayOptions;
    compact?: boolean;
    title?: string;
}

const AnalyticsOverlayConfigurationSummary: React.FC<AnalyticsOverlayConfigurationSummaryProps> = ({
    overlaySettings,
    compact = false,
    title = 'Current Analytics Overlay Configuration',
}) => {
    const theme = useTheme();

    const getPositionLabel = (position: number) => {
        switch (position) {
            case 0:
                return 'Middle';
            case 1:
                return 'Top Left';
            case 2:
                return 'Top Right';
            case 3:
                return 'Bottom Left';
            case 4:
                return 'Bottom Right';
            default:
                return 'Unknown';
        }
    };

    const renderValue = (
        value: boolean | string | number | string[] | number[] | undefined | null,
        type: 'boolean' | 'array' | 'string' | 'number' = 'string'
    ) => {
        if (value === undefined || value === null) {
            return (
                <Typography variant='caption' color='text.secondary'>
                    Not set
                </Typography>
            );
        }

        switch (type) {
            case 'boolean':
                return <Chip label={value ? 'Enabled' : 'Disabled'} color={value ? 'success' : 'default'} size='small' />;
            case 'array':
                if (!Array.isArray(value) || value.length === 0) {
                    return (
                        <Typography variant='caption' color='text.secondary'>
                            None
                        </Typography>
                    );
                }
                return (
                    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                        {value.map((item, index) => (
                            <Chip key={index} label={item} size='small' variant='outlined' />
                        ))}
                    </Box>
                );
            case 'number':
                return <Typography variant='body2'>{value}</Typography>;
            default:
                return <Typography variant='body2'>{value}</Typography>;
        }
    };

    const configRows = [
        { label: 'Bounding Boxes', value: overlaySettings.bbox?.showAll, type: 'boolean' as const },
        { label: 'Tripwires', value: overlaySettings.tripwire?.showAll, type: 'boolean' as const },
        { label: 'ROI', value: overlaySettings.roi?.showAll, type: 'boolean' as const },
        { label: 'Debug Mode', value: overlaySettings.debug, type: 'boolean' as const },
        { label: 'Show Pose', value: overlaySettings.pose, type: 'boolean' as const },
        { label: 'Show Halo', value: overlaySettings.needHalo, type: 'boolean' as const },
        { label: 'Color', value: overlaySettings.color, type: 'string' as const },
        { label: 'Thickness', value: overlaySettings.thickness, type: 'number' as const },
        { label: 'Opacity', value: overlaySettings.opacity, type: 'number' as const },
        { label: 'Object IDs', value: overlaySettings.bbox?.objectId, type: 'array' as const },
        { label: 'Class Types', value: overlaySettings.bbox?.classType, type: 'array' as const },
        { label: 'Proximity Classes', value: overlaySettings.proximityClass, type: 'array' as const },
        { label: 'Entrant Classes', value: overlaySettings.entrantClass, type: 'array' as const },
        { label: 'Show Object ID', value: overlaySettings.bbox?.showObjId, type: 'boolean' as const },
        {
            label: 'Object ID Position',
            value: overlaySettings.bbox?.objIdPosition !== undefined ? getPositionLabel(overlaySettings.bbox.objIdPosition) : undefined,
            type: 'string' as const,
        },
        { label: 'Object ID Text Color', value: overlaySettings.bbox?.objIdTextColor, type: 'string' as const },
        { label: 'Object ID Background Color', value: overlaySettings.bbox?.objIdTextBGColor, type: 'string' as const },
        { label: 'Proximity Area Factor', value: overlaySettings.proximityAreaFactor, type: 'number' as const },
        { label: 'Proximity Animation', value: overlaySettings.proximityAnimation, type: 'string' as const },
    ];

    const filteredRows = compact
        ? configRows.filter(
              row => row.value !== undefined && row.value !== null && (Array.isArray(row.value) ? row.value.length > 0 : true)
          )
        : configRows;

    return (
        <Box sx={{ mt: 2 }}>
            <Typography variant='subtitle2' gutterBottom sx={{ fontWeight: 'medium' }}>
                {title}
            </Typography>
            <TableContainer
                component={Paper}
                sx={{
                    maxHeight: compact ? 300 : 400,
                    bgcolor: theme.palette.background.paper,
                    border: `1px solid ${alpha(theme.palette.divider, 0.12)}`,
                    '& .MuiTableCell-root': {
                        py: compact ? 0.5 : 1,
                        borderBottom: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
                    },
                }}
            >
                <Table size={compact ? 'small' : 'medium'}>
                    <TableHead>
                        <TableRow>
                            <TableCell
                                sx={{
                                    fontWeight: 'bold',
                                    bgcolor: theme.palette.background.paper,
                                    borderBottom: `2px solid ${theme.palette.primary.main}`,
                                    zIndex: 10,
                                    position: 'sticky',
                                    top: 0,
                                    boxShadow: '0 1px 2px rgba(0, 0, 0, 0.1)',
                                }}
                            >
                                Setting
                            </TableCell>
                            <TableCell
                                sx={{
                                    fontWeight: 'bold',
                                    bgcolor: theme.palette.background.paper,
                                    borderBottom: `2px solid ${theme.palette.primary.main}`,
                                    zIndex: 10,
                                    position: 'sticky',
                                    top: 0,
                                    boxShadow: '0 1px 2px rgba(0, 0, 0, 0.1)',
                                }}
                            >
                                Value
                            </TableCell>
                        </TableRow>
                    </TableHead>
                    <TableBody>
                        {filteredRows.map((row, index) => (
                            <TableRow key={index}>
                                <TableCell sx={{ fontWeight: 'medium', minWidth: 180 }}>{row.label}</TableCell>
                                <TableCell>{renderValue(row.value, row.type)}</TableCell>
                            </TableRow>
                        ))}
                        {filteredRows.length === 0 && (
                            <TableRow>
                                <TableCell colSpan={2} align='center' sx={{ py: 3 }}>
                                    <Typography variant='body2' color='text.secondary'>
                                        No configuration set
                                    </Typography>
                                </TableCell>
                            </TableRow>
                        )}
                    </TableBody>
                </Table>
            </TableContainer>
        </Box>
    );
};

export default AnalyticsOverlayConfigurationSummary;

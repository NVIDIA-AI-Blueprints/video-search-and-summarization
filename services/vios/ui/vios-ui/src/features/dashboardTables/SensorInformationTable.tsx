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
import React, { ReactElement, useState } from 'react';
import { TableContainer, Paper, Table, TableHead, TableRow, TableCell, TableBody, Typography, TablePagination, Alert } from '@mui/material';
import Tooltip from '@mui/material/Tooltip';
import VideocamIcon from '@mui/icons-material/Videocam';
import VideocamOffIcon from '@mui/icons-material/VideocamOff';
import WarningIcon from '@mui/icons-material/Warning';
import CalendarMonthIcon from '@mui/icons-material/CalendarMonth';
import ErrorIcon from '@mui/icons-material/Error';
import CheckIcon from '@mui/icons-material/Check';
import CloseIcon from '@mui/icons-material/Close';
import { motion } from 'framer-motion';
import { SensorInformationTableData } from '../../interfaces/interfaces';
import useVSTUIStore from '../../services/StateManagement';

const SensorInformationTable: React.FC = () => {
    const [page, setPage] = useState(0);
    const [rowsPerPage, setRowsPerPage] = useState(10);

    const sensors = useVSTUIStore(state => state.sensorServiceSensors);
    const sensorStatus = useVSTUIStore(state => state.sensorStatus);
    const recordingStatus = useVSTUIStore(state => state.recordingStatus);

    const mergedData: SensorInformationTableData[] = sensors.map(sensor => ({
        name: sensor.name,
        sensorId: sensor.sensorId,
        remoteDeviceName: sensor.remoteDeviceName,
        remoteDeviceLocation: sensor.remoteDeviceLocation,
        recording_status: recordingStatus[sensor.sensorId]?.recording_status || 'unknown',
        state: sensorStatus[sensor.sensorId]?.state || 'unknown',
        errorMessage: sensorStatus[sensor.sensorId]?.errorMessage || 'unknown',
    }));

    // Handle page change
    const handleChangePage = (_event: React.MouseEvent | null, newPage: number) => {
        setPage(newPage);
    };

    // Handle rows per page change
    const handleChangeRowsPerPage = (event: React.ChangeEvent<HTMLInputElement>) => {
        setRowsPerPage(parseInt(event.target.value, 10));
        setPage(0);
    };

    const getNameStyle = () => {
        return {
            color: 'text.primary',
            fontWeight: 500,
            backgroundColor: 'action.hover',
            padding: '4px 8px',
            borderRadius: '4px',
            display: 'inline-block',
        };
    };

    const getRecordingStatusIcon = (recordingStatus: string) => {
        let iconComponent: ReactElement;
        switch (recordingStatus) {
            case 'off':
                iconComponent = <VideocamOffIcon color='warning' />;
                break;
            case 'schedule':
                iconComponent = <CalendarMonthIcon color='primary' />;
                break;
            case 'user':
                iconComponent = <VideocamIcon color='primary' />;
                break;
            case 'event':
                iconComponent = <VideocamIcon color='primary' />;
                break;
            case 'alwaysOn':
                iconComponent = <VideocamIcon color='primary' />;
                break;
            case 'error':
                iconComponent = <ErrorIcon color='error' />;
                break;
            case 'statusUnknown':
                iconComponent = <WarningIcon color='warning' />;
                break;
            default:
                iconComponent = <WarningIcon color='warning' />;
                break;
        }
        return <Tooltip title={recordingStatus}>{iconComponent}</Tooltip>;
    };

    const getSensorStateIcon = (sensorState: string) => {
        let iconComponent: ReactElement;
        switch (sensorState) {
            case 'online':
                iconComponent = <CheckIcon color='primary' />;
                break;
            case 'offline':
                iconComponent = <CloseIcon color='error' />;
                break;
            default:
                iconComponent = <WarningIcon color='warning' />;
                break;
        }
        return <Tooltip title={sensorState}>{iconComponent}</Tooltip>;
    };

    const getErrorMessageComponent = (errorMessage: string) => {
        if (errorMessage === 'No Error') {
            return (
                <Alert
                    severity='info'
                    sx={{
                        py: 0,
                        backgroundColor: 'rgba(2, 136, 209, 0.1)',
                        '& .MuiAlert-icon': {
                            color: 'info.main',
                        },
                    }}
                >
                    {errorMessage}
                </Alert>
            );
        }
        return (
            <Alert
                severity='error'
                sx={{
                    py: 0,
                    backgroundColor: 'rgba(211, 47, 47, 0.1)',
                    '& .MuiAlert-icon': {
                        color: 'error.main',
                    },
                }}
            >
                {errorMessage}
            </Alert>
        );
    };

    return (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 1 }}>
            <TableContainer component={Paper} style={{ overflowX: 'auto' }}>
                <Table>
                    <TableHead>
                        <TableRow>
                            <TableCell sx={{ cursor: 'default' }}>
                                <Typography variant='button'>
                                    <b>Name</b>
                                </Typography>
                            </TableCell>
                            <TableCell sx={{ cursor: 'default' }}>
                                <Typography variant='button'>
                                    <b>Recording Status</b>
                                </Typography>
                            </TableCell>
                            <TableCell sx={{ cursor: 'default' }}>
                                <Typography variant='button'>
                                    <b>State</b>
                                </Typography>
                            </TableCell>
                            <TableCell sx={{ cursor: 'default' }}>
                                <Typography variant='button'>
                                    <b>Error Message</b>
                                </Typography>
                            </TableCell>
                        </TableRow>
                    </TableHead>
                    <TableBody>
                        {mergedData.slice(page * rowsPerPage, page * rowsPerPage + rowsPerPage).map((row, index) => (
                            <TableRow key={index}>
                                <TableCell>
                                    <Typography variant='body2' sx={getNameStyle()}>
                                        <Tooltip
                                            title={
                                                <div>
                                                    <div>
                                                        <strong>Sensor ID:</strong> {row.sensorId}
                                                    </div>
                                                </div>
                                            }
                                            arrow
                                        >
                                            <span>{row.name}</span>
                                        </Tooltip>
                                    </Typography>
                                </TableCell>
                                <TableCell sx={{ cursor: 'default' }}>
                                    <Typography variant='body2'>{getRecordingStatusIcon(row.recording_status)}</Typography>
                                </TableCell>
                                <TableCell sx={{ cursor: 'default' }}>
                                    <Typography variant='body2'>{getSensorStateIcon(row.state)}</Typography>
                                </TableCell>
                                <TableCell>{getErrorMessageComponent(row.errorMessage)}</TableCell>
                            </TableRow>
                        ))}
                    </TableBody>
                </Table>
                <TablePagination
                    rowsPerPageOptions={[10, 20, 50]}
                    component='div'
                    count={mergedData.length}
                    rowsPerPage={rowsPerPage}
                    page={page}
                    onPageChange={handleChangePage}
                    onRowsPerPageChange={handleChangeRowsPerPage}
                />
            </TableContainer>
        </motion.div>
    );
};

export default SensorInformationTable;

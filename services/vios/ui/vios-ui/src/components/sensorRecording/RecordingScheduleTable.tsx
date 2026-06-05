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
import {
    Button,
    Card,
    CardContent,
    CardHeader,
    Paper,
    Table,
    TableBody,
    TableCell,
    TableContainer,
    TableHead,
    TableRow,
} from '@mui/material';
import { useSnackbar } from 'notistack';
import { Sensor } from '../../interfaces/interfaces';
import nvAxios from '../../services/Axios';
import config from '../../config';
import LOG from '../../utils/misc/Logger';
import cronstrue from 'cronstrue';
import React from 'react';

interface RecordingScheduleTableProps {
    selectedSensor: Sensor | null;
    recordSchedules: Array<{
        startTime: string;
        endTime: string;
    }>;
    onScheduleUpdate: () => void;
}

const RecordingScheduleTable = ({ selectedSensor, recordSchedules, onScheduleUpdate }: RecordingScheduleTableProps) => {
    const { enqueueSnackbar } = useSnackbar();

    const handleDeleteSchedule = (schedule: { startTime: string; endTime: string }) => {
        nvAxios
            .delete(
                `${config.streamRecorderEndpoint}/api/v1/record/${selectedSensor?.sensorId}/schedule?startTime=${schedule.startTime}&endTime=${schedule.endTime}`,
                {
                    headers: {
                        streamId: selectedSensor?.sensorId,
                    },
                }
            )
            .then(() => {
                onScheduleUpdate();
                enqueueSnackbar('Schedule deleted successfully', {
                    variant: 'success',
                    autoHideDuration: 3000,
                });
            })
            .catch(() => {
                LOG.error(`Failed to delete recording schedule for ${selectedSensor?.name}`);
                enqueueSnackbar('Failed to delete schedule', {
                    variant: 'error',
                    autoHideDuration: 3000,
                });
            });
    };

    return (
        <Card elevation={2}>
            <CardHeader
                title='Recording Schedule'
                subheader='Recording schedules of sensor'
                sx={{ borderBottom: 1, borderColor: 'divider' }}
            />
            <CardContent>
                <TableContainer component={Paper} elevation={0}>
                    <Table size='small' sx={{ '& td, & th': { px: 3, py: 1.5 } }}>
                        <TableHead>
                            <TableRow>
                                <TableCell>
                                    <b>Schedule #</b>
                                </TableCell>
                                <TableCell>
                                    <b>Start Time</b>
                                </TableCell>
                                <TableCell>
                                    <b>End Time</b>
                                </TableCell>
                                <TableCell align='right'>
                                    <b>Actions</b>
                                </TableCell>
                            </TableRow>
                        </TableHead>
                        <TableBody>
                            {recordSchedules.length === 0 ? (
                                <TableRow>
                                    <TableCell colSpan={4} align='center'>
                                        No recording schedules found
                                    </TableCell>
                                </TableRow>
                            ) : (
                                recordSchedules.map((schedule, index) => (
                                    <TableRow
                                        key={index}
                                        sx={{
                                            '&:last-child td, &:last-child th': { border: 0 },
                                            '&:nth-of-type(odd)': {
                                                backgroundColor: 'action.hover',
                                            },
                                        }}
                                    >
                                        <TableCell component='th' scope='row'>
                                            {index + 1}
                                        </TableCell>
                                        <TableCell>
                                            {schedule.startTime +
                                                ' (' +
                                                cronstrue.toString(schedule.startTime, {
                                                    verbose: true,
                                                }) +
                                                ')'}
                                        </TableCell>
                                        <TableCell>
                                            {schedule.endTime +
                                                ' (' +
                                                cronstrue.toString(schedule.endTime, {
                                                    verbose: true,
                                                }) +
                                                ')'}
                                        </TableCell>
                                        <TableCell align='right'>
                                            <Button
                                                variant='contained'
                                                color='error'
                                                size='small'
                                                onClick={() => handleDeleteSchedule(schedule)}
                                            >
                                                Delete
                                            </Button>
                                        </TableCell>
                                    </TableRow>
                                ))
                            )}
                        </TableBody>
                    </Table>
                </TableContainer>
            </CardContent>
        </Card>
    );
};

export default RecordingScheduleTable;

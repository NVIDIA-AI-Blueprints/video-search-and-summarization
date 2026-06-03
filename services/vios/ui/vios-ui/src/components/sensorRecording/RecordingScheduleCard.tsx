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
    Alert,
    Box,
    Button,
    Card,
    CardActions,
    CardContent,
    CardHeader,
    Chip,
    FormControl,
    Grid2 as Grid,
    InputLabel,
    MenuItem,
    Select,
    Typography,
} from '@mui/material';
import { useSnackbar } from 'notistack';
import { useCallback, useEffect, useState } from 'react';
import { Sensor } from '../../interfaces/interfaces';
import nvAxios from '../../services/Axios';
import config from '../../config';
import LOG from '../../utils/misc/Logger';
import cron from 'cron-validate';
import cronstrue from 'cronstrue';
import React from 'react';

const BOSMA_SCHEDULER_PRESET = {
    presetId: 'cpp-cron',
    useSeconds: false,
    useYears: false,
    useAliases: false,
    useBlankDay: false,
    allowOnlyOneBlankDayField: false,
    mustHaveBlankDayField: false,
    useLastDayOfMonth: false,
    useLastDayOfWeek: false,
    useNearestWeekday: false,
    useNthWeekdayOfMonth: false,
    minutes: {
        lowerLimit: 0,
        upperLimit: 59,
    },
    hours: {
        lowerLimit: 0,
        upperLimit: 23,
    },
    daysOfMonth: {
        lowerLimit: 1,
        upperLimit: 31,
    },
    months: {
        lowerLimit: 1,
        upperLimit: 12,
    },
    daysOfWeek: {
        lowerLimit: 0,
        upperLimit: 6,
    },
};

interface RecordingScheduleCardProps {
    selectedSensor: Sensor | null;
    onScheduleUpdate: () => void;
}

const RecordingScheduleCard = ({ selectedSensor, onScheduleUpdate }: RecordingScheduleCardProps) => {
    const { enqueueSnackbar } = useSnackbar();
    const [startTime, setStartTime] = useState('');
    const [endTime, setEndTime] = useState('');
    const [startTimeEng, setStartTimeEng] = useState('');
    const [endTimeEng, setEndTimeEng] = useState('');
    const [error, setError] = useState('');
    const [startDay, setStartDay] = useState('');
    const [startHour, setStartHour] = useState('');
    const [startMinute, setStartMinute] = useState('');
    const [endDay, setEndDay] = useState('');
    const [endHour, setEndHour] = useState('');
    const [endMinute, setEndMinute] = useState('');

    const validateCron = useCallback((cronStr: string) => {
        if (containsSpecialCharacters(cronStr)) {
            return false;
        }
        if (containsWildCardMinuteHourDay(cronStr)) {
            return false;
        }
        const cronResult = cron(cronStr, BOSMA_SCHEDULER_PRESET);
        if (cronResult.isValid()) {
            LOG.verbose(`Valid cron string provided, processing`);
            return true;
        } else {
            const errorValue = cronResult.getError();
            LOG.error('Invalid cron string provided');
            LOG.error(errorValue.join(', '));
            return false;
        }
    }, []);

    useEffect(() => {
        if (validateCron(startTime)) setStartTimeEng(cronstrue.toString(startTime, { verbose: true }));
        if (validateCron(endTime)) setEndTimeEng(cronstrue.toString(endTime, { verbose: true }));
    }, [startTime, endTime, validateCron]);

    const submitRecordSchedules = () => {
        const payload = [
            {
                startTime: startTime,
                endTime: endTime,
            },
        ];
        nvAxios
            .post(`${config.streamRecorderEndpoint}/api/v1/record/${selectedSensor?.sensorId}/schedule`, payload, {
                headers: {
                    streamId: selectedSensor?.sensorId,
                },
            })
            .then(() => {
                onScheduleUpdate();
                enqueueSnackbar('Schedule added successfully', {
                    variant: 'success',
                    autoHideDuration: 3000,
                });
            })
            .catch(() => {
                LOG.error(`Failed to set recording for ${selectedSensor?.name}`);
                enqueueSnackbar('Failed to add schedule', {
                    variant: 'error',
                    autoHideDuration: 3000,
                });
            });
    };

    const handleSubmit = () => {
        if (!validateCron(startTime) || !validateCron(endTime)) {
            setStartTimeEng('');
            setEndTimeEng('');
            setError(`Invalid cron format. Please use format: minute hour day month day-of-week (e.g., 30 14 * * 5)`);
            return;
        } else {
            setError('');
            setStartTimeEng(cronstrue.toString(startTime, { verbose: true }));
            setEndTimeEng(cronstrue.toString(endTime, { verbose: true }));
            submitRecordSchedules();
        }
    };

    const containsWildCardMinuteHourDay = (cronExpression: string) => {
        const fields = cronExpression.split(' ');

        if (fields.length !== 5) {
            return false;
        }

        const [minute, hour, , , dayOfWeek] = fields;

        if (minute === '*' || hour === '*' || dayOfWeek === '*') {
            return true;
        }
        return false;
    };

    const containsSpecialCharacters = (str: string) => {
        const specialChars = [',', '-', '/'];
        return specialChars.some(char => str.includes(char));
    };

    const daysOfWeek = [
        { value: '0', label: 'Sunday' },
        { value: '1', label: 'Monday' },
        { value: '2', label: 'Tuesday' },
        { value: '3', label: 'Wednesday' },
        { value: '4', label: 'Thursday' },
        { value: '5', label: 'Friday' },
        { value: '6', label: 'Saturday' },
    ];

    const hours = Array.from({ length: 24 }, (_, i) => ({
        value: i.toString(),
        label: i.toString().padStart(2, '0'),
    }));

    const minutes = Array.from({ length: 60 }, (_, i) => ({
        value: i.toString(),
        label: i.toString().padStart(2, '0'),
    }));

    const updateCronExpression = (day: string, hour: string, minute: string, isStart: boolean) => {
        if (day && hour && minute) {
            const cronExpression = `${minute} ${hour} * * ${day}`;
            if (isStart) {
                setStartTime(cronExpression);
            } else {
                setEndTime(cronExpression);
            }
        } else {
            if (isStart) {
                setStartTime('');
            } else {
                setEndTime('');
            }
        }
    };

    useEffect(() => {
        updateCronExpression(startDay, startHour, startMinute, true);
    }, [startDay, startHour, startMinute]);

    useEffect(() => {
        updateCronExpression(endDay, endHour, endMinute, false);
    }, [endDay, endHour, endMinute]);

    const areAllSelectionsMade = useCallback(() => {
        return startDay !== '' && startHour !== '' && startMinute !== '' && endDay !== '' && endHour !== '' && endMinute !== '';
    }, [startDay, startHour, startMinute, endDay, endHour, endMinute]);

    return (
        <Card elevation={2}>
            <CardHeader
                title='Recording Schedule'
                subheader='Set recording schedule using dropdowns'
                sx={{ borderBottom: 1, borderColor: 'divider' }}
            />
            <CardContent>
                <Box component='form' noValidate autoComplete='off'>
                    <Grid container spacing={1}>
                        <Grid size={{ xs: 12 }}>
                            <Typography variant='subtitle2' sx={{ color: 'text.secondary', mb: 1 }}>
                                Start Time
                            </Typography>
                        </Grid>
                        <Grid size={{ xs: 4 }}>
                            <FormControl fullWidth size='small'>
                                <InputLabel>Day</InputLabel>
                                <Select
                                    value={startDay}
                                    label='Day'
                                    onChange={e => setStartDay(e.target.value)}
                                    disabled={selectedSensor == null}
                                >
                                    <MenuItem value=''>
                                        <em>Select a day</em>
                                    </MenuItem>
                                    {daysOfWeek.map(day => (
                                        <MenuItem key={day.value} value={day.value}>
                                            {day.label}
                                        </MenuItem>
                                    ))}
                                </Select>
                            </FormControl>
                        </Grid>
                        <Grid size={{ xs: 4 }}>
                            <FormControl fullWidth size='small'>
                                <InputLabel>Hour</InputLabel>
                                <Select
                                    value={startHour}
                                    label='Hour'
                                    onChange={e => setStartHour(e.target.value)}
                                    disabled={selectedSensor == null}
                                >
                                    <MenuItem value=''>
                                        <em>Select an hour</em>
                                    </MenuItem>
                                    {hours.map(hour => (
                                        <MenuItem key={hour.value} value={hour.value}>
                                            {hour.label}
                                        </MenuItem>
                                    ))}
                                </Select>
                            </FormControl>
                        </Grid>
                        <Grid size={{ xs: 4 }}>
                            <FormControl fullWidth size='small'>
                                <InputLabel>Minute</InputLabel>
                                <Select
                                    value={startMinute}
                                    label='Minute'
                                    onChange={e => setStartMinute(e.target.value)}
                                    disabled={selectedSensor == null}
                                >
                                    <MenuItem value=''>
                                        <em>Select a minute</em>
                                    </MenuItem>
                                    {minutes.map(minute => (
                                        <MenuItem key={minute.value} value={minute.value}>
                                            {minute.label}
                                        </MenuItem>
                                    ))}
                                </Select>
                            </FormControl>
                        </Grid>
                        {startTimeEng != '' && (
                            <Grid size={{ xs: 12 }}>
                                <Chip label={startTimeEng} size='small' sx={{ mt: 1 }} />
                            </Grid>
                        )}
                    </Grid>

                    <Grid container spacing={1} sx={{ mt: 1 }}>
                        <Grid size={{ xs: 12 }}>
                            <Typography variant='subtitle2' sx={{ color: 'text.secondary', mb: 1 }}>
                                End Time
                            </Typography>
                        </Grid>
                        <Grid size={{ xs: 4 }}>
                            <FormControl fullWidth size='small'>
                                <InputLabel>Day</InputLabel>
                                <Select
                                    value={endDay}
                                    label='Day'
                                    onChange={e => setEndDay(e.target.value)}
                                    disabled={selectedSensor == null}
                                >
                                    <MenuItem value=''>
                                        <em>Select a day</em>
                                    </MenuItem>
                                    {daysOfWeek.map(day => (
                                        <MenuItem key={day.value} value={day.value}>
                                            {day.label}
                                        </MenuItem>
                                    ))}
                                </Select>
                            </FormControl>
                        </Grid>
                        <Grid size={{ xs: 4 }}>
                            <FormControl fullWidth size='small'>
                                <InputLabel>Hour</InputLabel>
                                <Select
                                    value={endHour}
                                    label='Hour'
                                    onChange={e => setEndHour(e.target.value)}
                                    disabled={selectedSensor == null}
                                >
                                    <MenuItem value=''>
                                        <em>Select an hour</em>
                                    </MenuItem>
                                    {hours.map(hour => (
                                        <MenuItem key={hour.value} value={hour.value}>
                                            {hour.label}
                                        </MenuItem>
                                    ))}
                                </Select>
                            </FormControl>
                        </Grid>
                        <Grid size={{ xs: 4 }}>
                            <FormControl fullWidth size='small'>
                                <InputLabel>Minute</InputLabel>
                                <Select
                                    value={endMinute}
                                    label='Minute'
                                    onChange={e => setEndMinute(e.target.value)}
                                    disabled={selectedSensor == null}
                                >
                                    <MenuItem value=''>
                                        <em>Select a minute</em>
                                    </MenuItem>
                                    {minutes.map(minute => (
                                        <MenuItem key={minute.value} value={minute.value}>
                                            {minute.label}
                                        </MenuItem>
                                    ))}
                                </Select>
                            </FormControl>
                        </Grid>
                        {endTimeEng != '' && (
                            <Grid size={{ xs: 12 }}>
                                <Chip label={endTimeEng} size='small' sx={{ mt: 1 }} />
                            </Grid>
                        )}
                    </Grid>
                </Box>
            </CardContent>
            <CardActions sx={{ px: 2, pb: 2 }}>
                <Button
                    variant='contained'
                    onClick={handleSubmit}
                    disabled={selectedSensor == null || !areAllSelectionsMade()}
                    sx={{ px: 4 }}
                >
                    Submit
                </Button>
            </CardActions>
            {error && (
                <Box sx={{ px: 2, pb: 2 }}>
                    <Alert severity='error'>{error}</Alert>
                </Box>
            )}
        </Card>
    );
};

export default RecordingScheduleCard;

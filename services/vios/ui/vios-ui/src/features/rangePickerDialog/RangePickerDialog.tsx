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
import { Dialog, DialogTitle, DialogContent, DialogActions, Button, TextField, Checkbox, FormControlLabel, Box } from '@mui/material';
import { DateTimePicker } from '@mui/x-date-pickers/DateTimePicker';
import { LocalizationProvider } from '@mui/x-date-pickers/LocalizationProvider';
import { AdapterDateFns } from '@mui/x-date-pickers/AdapterDateFns';
import { addMilliseconds } from 'date-fns';
import LOG from '../../utils/misc/Logger';

interface RangePickerDialogProps {
    open: boolean;
    onClose: () => void;
    onSubmit: (startTime: string, endTime: string) => void;
}

const RangePickerDialog: React.FC<RangePickerDialogProps> = ({ open, onClose, onSubmit }) => {
    const [startDate, setStartDate] = useState<Date | null>(new Date());
    const [endDate, setEndDate] = useState<Date | null>(new Date());
    const [startMicroseconds, setStartMicroseconds] = useState<number>(0);
    const [endMicroseconds, setEndMicroseconds] = useState<number>(0);
    const [addStartMicroseconds, setAddStartMicroseconds] = useState<boolean>(false);
    const [addEndMicroseconds, setAddEndMicroseconds] = useState<boolean>(false);

    const handleSubmit = () => {
        if (startDate && endDate) {
            let finalStartDate = startDate;
            let finalEndDate = endDate;

            if (addStartMicroseconds) {
                finalStartDate = addMilliseconds(finalStartDate, startMicroseconds / 1000);
            }

            if (addEndMicroseconds) {
                finalEndDate = addMilliseconds(finalEndDate, endMicroseconds / 1000);
            }

            const startTimeUTC = finalStartDate?.toISOString();
            const endTimeUTC = finalEndDate?.toISOString();

            onSubmit(startTimeUTC, endTimeUTC);
            onClose();
        } else {
            LOG.info('Either start date or end date not provided', startDate, endDate);
        }
    };

    return (
        <Dialog open={open} onClose={onClose}>
            <DialogTitle>Select Date and Time Range</DialogTitle>
            <DialogContent>
                <LocalizationProvider dateAdapter={AdapterDateFns}>
                    <Box display='flex' flexDirection='column' gap={2} mt={2}>
                        <DateTimePicker
                            label='Start Date and Time'
                            value={startDate}
                            onChange={newValue => setStartDate(newValue)}
                            views={['year', 'month', 'day', 'hours', 'minutes', 'seconds']}
                            defaultValue={new Date()}
                            timeSteps={{ hours: 1, minutes: 1, seconds: 1 }}
                        />
                        <DateTimePicker
                            label='End Date and Time'
                            value={endDate}
                            onChange={newValue => setEndDate(newValue)}
                            views={['year', 'month', 'day', 'hours', 'minutes', 'seconds']}
                            defaultValue={new Date()}
                            timeSteps={{ hours: 1, minutes: 1, seconds: 1 }}
                        />
                    </Box>
                </LocalizationProvider>
                <FormControlLabel
                    control={<Checkbox checked={addStartMicroseconds} onChange={e => setAddStartMicroseconds(e.target.checked)} />}
                    label='Add microseconds to start time'
                />
                {addStartMicroseconds && (
                    <TextField
                        type='number'
                        label='Start Microseconds'
                        value={startMicroseconds}
                        onChange={e => setStartMicroseconds(Number(e.target.value))}
                        fullWidth
                        margin='normal'
                    />
                )}
                <FormControlLabel
                    control={<Checkbox checked={addEndMicroseconds} onChange={e => setAddEndMicroseconds(e.target.checked)} />}
                    label='Add microseconds to end time'
                />
                {addEndMicroseconds && (
                    <TextField
                        type='number'
                        label='End Microseconds'
                        value={endMicroseconds}
                        onChange={e => setEndMicroseconds(Number(e.target.value))}
                        fullWidth
                        margin='normal'
                    />
                )}
            </DialogContent>
            <DialogActions>
                <Button onClick={onClose}>Cancel</Button>
                <Button onClick={handleSubmit} color='primary'>
                    Submit
                </Button>
            </DialogActions>
        </Dialog>
    );
};

export default RangePickerDialog;

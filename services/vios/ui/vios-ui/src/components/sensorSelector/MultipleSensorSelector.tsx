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
import { Autocomplete, TextField, Chip } from '@mui/material';
import { Sensor, MultipleSensorSelectorProps } from '../../interfaces/interfaces';

const MultipleSensorSelector: React.FC<MultipleSensorSelectorProps> = ({
    sensors,
    selectedSensors,
    onChange,
    label = 'Select Sensors',
}) => {
    const handleChange = (_event: React.SyntheticEvent, value: Sensor[] | undefined) => {
        onChange(value);
    };

    const getSensorLabel = (option: Sensor): string => option.name || option.streamId || option.sensorId || '';

    return (
        <Autocomplete
            multiple
            options={sensors}
            getOptionLabel={option => (option ? getSensorLabel(option) : '')}
            getOptionKey={option => option.streamId || option.sensorId}
            isOptionEqualToValue={(option, value) => {
                const optionId = option.streamId || option.sensorId;
                const valueId = value.streamId || value.sensorId;
                return optionId === valueId;
            }}
            value={selectedSensors || []}
            onChange={handleChange}
            renderTags={(tagValue, getTagProps) =>
                tagValue.map((option, index) => {
                    const { key, ...tagProps } = getTagProps({ index });
                    const uniqueKey = option.streamId || option.sensorId || key;
                    return <Chip key={uniqueKey} label={getSensorLabel(option)} {...tagProps} />;
                })
            }
            renderInput={params => <TextField {...params} variant='outlined' label={label} />}
        />
    );
};

export default MultipleSensorSelector;

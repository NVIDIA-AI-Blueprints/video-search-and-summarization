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
import { TextField, Select, MenuItem, FormControl, InputLabel } from '@mui/material';
import { RangeField, EnumField, Resolution } from '../../interfaces/interfaces';

interface RangeFieldProps {
    field: RangeField;
    label: string;
    disabled?: boolean;
    onChange: (value: string) => void;
}

export const RangeFieldComponent: React.FC<RangeFieldProps> = ({ field, label, disabled = false, onChange }) => (
    <TextField
        disabled={disabled}
        label={label}
        type='number'
        value={field.Value}
        onChange={e => onChange(e.target.value)}
        inputProps={{
            min: field.Min,
            max: field.Max,
        }}
        fullWidth
        margin='normal'
    />
);

interface EnumFieldProps {
    field: EnumField;
    label: string;
    disabled?: boolean;
    onChange: (value: string) => void;
}

export const EnumFieldComponent: React.FC<EnumFieldProps> = ({ field, label, disabled = false, onChange }) => (
    <FormControl fullWidth margin='normal'>
        <InputLabel>{label}</InputLabel>
        <Select disabled={disabled} value={field.Value} onChange={e => onChange(e.target.value)} label={label}>
            {field.AllowedValues.map(value => (
                <MenuItem key={value} value={value}>
                    {value}
                </MenuItem>
            ))}
        </Select>
    </FormControl>
);

interface ResolutionFieldProps {
    field: {
        AllowedValues: Resolution[];
        Value: Resolution;
    };
    disabled?: boolean;
    onChange: (value: Resolution) => void;
}

export const ResolutionFieldComponent: React.FC<ResolutionFieldProps> = ({ field, disabled = false, onChange }) => (
    <FormControl fullWidth margin='normal'>
        <InputLabel>Resolution</InputLabel>
        <Select
            disabled={disabled}
            value={`${field.Value.Width}x${field.Value.Height}`}
            onChange={e => {
                const [width, height] = e.target.value.split('x');
                onChange({ Width: width, Height: height });
            }}
            label='Resolution'
        >
            {field.AllowedValues.map(resolution => (
                <MenuItem key={`${resolution.Width}x${resolution.Height}`} value={`${resolution.Width}x${resolution.Height}`}>
                    {`${resolution.Width}x${resolution.Height}`}
                </MenuItem>
            ))}
        </Select>
    </FormControl>
);

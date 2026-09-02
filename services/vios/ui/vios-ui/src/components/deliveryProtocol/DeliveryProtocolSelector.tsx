// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

import React, { useId } from 'react';
import { FormControl, InputLabel, MenuItem, Select, SelectChangeEvent } from '@mui/material';

import { LiveDeliveryProtocol } from '../../interfaces/interfaces';

// Chosen alongside the sensors, before anything plays, because switching it
// afterwards tears the stream down and builds a new one.
const DeliveryProtocolSelector: React.FC<{
    value: LiveDeliveryProtocol;
    onChange: (protocol: LiveDeliveryProtocol) => void;
    disabled?: boolean;
}> = ({ value, onChange, disabled }) => {
    // Every stream page stays mounted, so a fixed id would appear several
    // times in one document.
    const id = useId();
    return (
        <FormControl size='small' sx={{ width: 170, flexShrink: 0 }} disabled={disabled}>
            <InputLabel id={`${id}-label`}>Streaming Protocol</InputLabel>
            <Select
                labelId={`${id}-label`}
                id={id}
                data-testid='delivery-protocol'
                value={value}
                label='Streaming Protocol'
                onChange={(event: SelectChangeEvent) => onChange(event.target.value as LiveDeliveryProtocol)}
            >
                <MenuItem value='webrtc'>WebRTC</MenuItem>
                <MenuItem value='dash'>DASH</MenuItem>
            </Select>
        </FormControl>
    );
};

export default DeliveryProtocolSelector;

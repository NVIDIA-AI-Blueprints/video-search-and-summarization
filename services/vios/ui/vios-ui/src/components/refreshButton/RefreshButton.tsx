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
import { IconButton, Tooltip } from '@mui/material';
import { Refresh as RefreshIcon } from '@mui/icons-material';
import { updateSensorsAndStreams } from '../../utils/misc/updateSensorsAndStreams';
import { useSnackbar } from 'notistack';

const RefreshButton: React.FC = () => {
    const { enqueueSnackbar } = useSnackbar();
    const [isRefreshing, setIsRefreshing] = useState(false);

    const handleRefresh = async () => {
        try {
            setIsRefreshing(true);
            await updateSensorsAndStreams();
            enqueueSnackbar('Sensors and streams updated successfully', {
                variant: 'success',
                autoHideDuration: 3000,
            });
        } catch (error) {
            enqueueSnackbar('Failed to update sensors and streams', {
                variant: 'error',
                autoHideDuration: 3000,
            });
        } finally {
            setIsRefreshing(false);
        }
    };

    return (
        <Tooltip title='Refresh sensors and streams data'>
            <IconButton color='inherit' onClick={handleRefresh} disabled={isRefreshing}>
                <RefreshIcon
                    sx={{
                        animation: isRefreshing ? 'spin 1s linear infinite' : 'none',
                        '@keyframes spin': {
                            '0%': {
                                transform: 'rotate(0deg)',
                            },
                            '100%': {
                                transform: 'rotate(360deg)',
                            },
                        },
                    }}
                />
            </IconButton>
        </Tooltip>
    );
};

export default RefreshButton;

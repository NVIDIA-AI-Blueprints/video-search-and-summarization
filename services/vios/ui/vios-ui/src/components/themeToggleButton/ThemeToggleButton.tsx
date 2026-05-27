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
import { Switch, Tooltip, FormControlLabel } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import useThemeContext from '../../hooks/useThemeContext';

const ThemeToggleButton: React.FC = () => {
    const { toggleColorMode } = useThemeContext();
    const theme = useTheme();
    const isDarkMode = theme.palette.mode === 'dark';

    return (
        <Tooltip title={`Switch to ${isDarkMode ? 'light' : 'dark'} mode`}>
            <FormControlLabel
                control={
                    <Switch
                        checked={isDarkMode}
                        onChange={toggleColorMode}
                        color='default'
                        sx={{
                            '& .MuiSwitch-switchBase.Mui-checked': {
                                color: theme.palette.primary.main,
                                '& + .MuiSwitch-track': {
                                    backgroundColor: theme.palette.primary.main,
                                },
                            },
                        }}
                    />
                }
                label={isDarkMode ? 'Dark Mode' : 'Light Mode'}
                sx={{ ml: 1 }}
            />
        </Tooltip>
    );
};

export default ThemeToggleButton;

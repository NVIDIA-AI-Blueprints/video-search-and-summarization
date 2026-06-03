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
import { Menu, MenuItem, IconButton, Tooltip } from '@mui/material';
import { Tune } from '@mui/icons-material';

interface QualityMenuProps {
    onSettingChange: (setting: string) => void;
    currentSetting: string;
    show?: boolean;
}

const QualityMenu: React.FC<QualityMenuProps> = ({ onSettingChange, currentSetting, show = true }) => {
    const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null);
    const open = Boolean(anchorEl);

    if (!show) return null;

    const handleClick = (event: React.MouseEvent<HTMLButtonElement>) => {
        setAnchorEl(event.currentTarget);
    };

    const handleClose = () => {
        setAnchorEl(null);
    };

    const handleSettingSelect = (setting: string) => {
        onSettingChange(setting);
        handleClose();
    };

    const settings = ['low', 'medium', 'high', 'auto', 'pass_through'];

    return (
        <>
            <Tooltip title='Quality Settings' placement='top'>
                <IconButton onClick={handleClick}>
                    <Tune />
                </IconButton>
            </Tooltip>
            <Menu
                id='settings-menu'
                anchorEl={anchorEl}
                open={open}
                onClose={handleClose}
                anchorOrigin={{
                    vertical: 'top',
                    horizontal: 'center',
                }}
                transformOrigin={{
                    vertical: 'bottom',
                    horizontal: 'center',
                }}
            >
                {settings.map(setting => (
                    <MenuItem
                        key={setting}
                        onClick={() => handleSettingSelect(setting)}
                        selected={currentSetting === setting}
                        sx={{
                            '&.Mui-selected': {
                                backgroundColor: 'primary.main',
                                color: 'primary.contrastText',
                                '&:hover': {
                                    backgroundColor: 'primary.dark',
                                },
                            },
                        }}
                    >
                        {setting.charAt(0).toUpperCase() + setting.slice(1).replace('_', ' ')}
                    </MenuItem>
                ))}
            </Menu>
        </>
    );
};

export default QualityMenu;

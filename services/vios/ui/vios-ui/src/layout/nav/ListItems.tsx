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
import * as React from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import ListItemButton from '@mui/material/ListItemButton';
import ListItemIcon from '@mui/material/ListItemIcon';
import ListItemText from '@mui/material/ListItemText';
import DashboardIcon from '@mui/icons-material/Dashboard';
import SettingsIcon from '@mui/icons-material/Settings';
import UploadIcon from '@mui/icons-material/Upload';
import StorageIcon from '@mui/icons-material/Storage';
import StreamIcon from '@mui/icons-material/Stream';
import BuildIcon from '@mui/icons-material/Build';
import ExperimentIcon from '@mui/icons-material/Science';
import GridOnIcon from '@mui/icons-material/GridOn';
import TuneIcon from '@mui/icons-material/Tune';
import VideocamIcon from '@mui/icons-material/Videocam';
import VideoFileIcon from '@mui/icons-material/VideoFile';
import VideoSettingsIcon from '@mui/icons-material/VideoSettings';
import RadioButtonCheckedIcon from '@mui/icons-material/RadioButtonChecked';
import VideoLibraryIcon from '@mui/icons-material/VideoLibrary';
import AssessmentIcon from '@mui/icons-material/Assessment';

// Custom styles for the NavLink component
const navLinkStyles = {
    color: 'inherit',
    textDecoration: 'none',
};

const StreamerListItems = () => {
    const location = useLocation();

    return (
        <React.Fragment>
            <NavLink to='/dashboard' style={navLinkStyles}>
                <ListItemButton selected={location.pathname === '/dashboard'}>
                    <ListItemIcon>
                        <DashboardIcon />
                    </ListItemIcon>
                    <ListItemText primary='Dashboard' />
                </ListItemButton>
            </NavLink>
            <NavLink to='/media-configuration' style={navLinkStyles}>
                <ListItemButton selected={location.pathname === '/media-configuration'}>
                    <ListItemIcon>
                        <VideoSettingsIcon />
                    </ListItemIcon>
                    <ListItemText primary='Media Configuration' />
                </ListItemButton>
            </NavLink>
            <NavLink to='/media-management' style={navLinkStyles}>
                <ListItemButton selected={location.pathname === '/media-management'}>
                    <ListItemIcon>
                        <BuildIcon />
                    </ListItemIcon>
                    <ListItemText primary='Media Management' />
                </ListItemButton>
            </NavLink>
            <NavLink to='/media-streams' style={navLinkStyles}>
                <ListItemButton selected={location.pathname === '/media-streams'}>
                    <ListItemIcon>
                        <StreamIcon />
                    </ListItemIcon>
                    <ListItemText primary='Media Streams' />
                </ListItemButton>
            </NavLink>
            <NavLink to='/media-upload' style={navLinkStyles}>
                <ListItemButton selected={location.pathname === '/media-upload'}>
                    <ListItemIcon>
                        <UploadIcon />
                    </ListItemIcon>
                    <ListItemText primary='Media Upload' />
                </ListItemButton>
            </NavLink>
            <NavLink to='/settings' style={navLinkStyles}>
                <ListItemButton selected={location.pathname === '/settings'}>
                    <ListItemIcon>
                        <SettingsIcon />
                    </ListItemIcon>
                    <ListItemText primary='Settings' />
                </ListItemButton>
            </NavLink>
            <NavLink to='/experimental' style={navLinkStyles}>
                <ListItemButton selected={location.pathname === '/experimental'}>
                    <ListItemIcon>
                        <ExperimentIcon />
                    </ListItemIcon>
                    <ListItemText primary='Experimental' />
                </ListItemButton>
            </NavLink>
        </React.Fragment>
    );
};

const VSTListItems = () => {
    const location = useLocation();

    return (
        <React.Fragment>
            <NavLink to='/dashboard' style={navLinkStyles}>
                <ListItemButton selected={location.pathname === '/dashboard'}>
                    <ListItemIcon>
                        <DashboardIcon />
                    </ListItemIcon>
                    <ListItemText primary='Dashboard' />
                </ListItemButton>
            </NavLink>
            <NavLink to='/sensor-management' style={navLinkStyles}>
                <ListItemButton selected={location.pathname === '/sensor-management'}>
                    <ListItemIcon>
                        <BuildIcon />
                    </ListItemIcon>
                    <ListItemText primary='Sensor Management' />
                </ListItemButton>
            </NavLink>
            <NavLink to='/sensor-configuration' style={navLinkStyles}>
                <ListItemButton selected={location.pathname === '/sensor-configuration'}>
                    <ListItemIcon>
                        <TuneIcon />
                    </ListItemIcon>
                    <ListItemText primary='Sensor Configuration' />
                </ListItemButton>
            </NavLink>
            <NavLink to='/record-settings' style={navLinkStyles}>
                <ListItemButton selected={location.pathname === '/record-settings'}>
                    <ListItemIcon>
                        <RadioButtonCheckedIcon />
                    </ListItemIcon>
                    <ListItemText primary='Record Settings' />
                </ListItemButton>
            </NavLink>
            <NavLink to='/sensor-details' style={navLinkStyles}>
                <ListItemButton selected={location.pathname === '/sensor-details'}>
                    <ListItemIcon>
                        <StorageIcon />
                    </ListItemIcon>
                    <ListItemText primary='Sensor Details' />
                </ListItemButton>
            </NavLink>
            <NavLink to='/stream-details' style={navLinkStyles}>
                <ListItemButton selected={location.pathname === '/stream-details'}>
                    <ListItemIcon>
                        <AssessmentIcon />
                    </ListItemIcon>
                    <ListItemText primary='Stream Details' />
                </ListItemButton>
            </NavLink>
            <NavLink to='/media-management' style={navLinkStyles}>
                <ListItemButton selected={location.pathname === '/media-management'}>
                    <ListItemIcon>
                        <VideoLibraryIcon />
                    </ListItemIcon>
                    <ListItemText primary='Media Management' />
                </ListItemButton>
            </NavLink>
            <NavLink to='/live-streams' style={navLinkStyles}>
                <ListItemButton selected={location.pathname === '/live-streams'}>
                    <ListItemIcon>
                        <VideocamIcon />
                    </ListItemIcon>
                    <ListItemText primary='Live Streams' />
                </ListItemButton>
            </NavLink>
            <NavLink to='/recorded-streams' style={navLinkStyles}>
                <ListItemButton selected={location.pathname === '/recorded-streams'}>
                    <ListItemIcon>
                        <VideoFileIcon />
                    </ListItemIcon>
                    <ListItemText primary='Recorded Streams' />
                </ListItemButton>
            </NavLink>
            <NavLink to='/video-wall' style={navLinkStyles}>
                <ListItemButton selected={location.pathname === '/video-wall'}>
                    <ListItemIcon>
                        <GridOnIcon />
                    </ListItemIcon>
                    <ListItemText primary='Video Wall' />
                </ListItemButton>
            </NavLink>
            <NavLink to='/settings' style={navLinkStyles}>
                <ListItemButton selected={location.pathname === '/settings'}>
                    <ListItemIcon>
                        <SettingsIcon />
                    </ListItemIcon>
                    <ListItemText primary='Settings' />
                </ListItemButton>
            </NavLink>
            <NavLink to='/experimental' style={navLinkStyles}>
                <ListItemButton selected={location.pathname === '/experimental'}>
                    <ListItemIcon>
                        <ExperimentIcon />
                    </ListItemIcon>
                    <ListItemText primary='Experimental' />
                </ListItemButton>
            </NavLink>
        </React.Fragment>
    );
};

export { StreamerListItems, VSTListItems };

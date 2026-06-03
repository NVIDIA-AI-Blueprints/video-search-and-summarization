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
import React, { useEffect, useState } from 'react';
import { styled, useTheme } from '@mui/material/styles';
import { Outlet, useLocation } from 'react-router-dom';
import MuiDrawer from '@mui/material/Drawer';
import { Box, Stack, Toolbar, useMediaQuery } from '@mui/material';
import MuiAppBar from '@mui/material/AppBar';
import List from '@mui/material/List';
import Typography from '@mui/material/Typography';
import Divider from '@mui/material/Divider';
import IconButton from '@mui/material/IconButton';
import Container from '@mui/material/Container';
import MenuIcon from '@mui/icons-material/Menu';
import ChevronLeftIcon from '@mui/icons-material/ChevronLeft';
import { StreamerListItems, VSTListItems } from './nav/ListItems';
import useVSTUIStore from '../services/StateManagement';
import LocalDateTime from '../features/clock/LocalDateTime';
import UTCDateTime from '../features/clock/UTCDateTime';
import RefreshButton from '../components/refreshButton/RefreshButton';
import SettingsDropdown from '../components/settingsDropdown/SettingsDropdown';
import LiveStream from '../pages/vst/LiveStream';
import ReplayStream from '../pages/vst/ReplayStream';
import VideoWall from '../pages/vst/VideoWall';
import Experimental from '../pages/common/Experimental';
import MediaStreams from '../pages/nvstreamer/MediaStreams';
import { AppBarProps } from '../interfaces/interfaces';
import Footer from './Footer';
import MediaUpload from '../pages/nvstreamer/MediaUpload';

const VST_ADAPTOR = 'vst';
const MMS_ADAPTOR = 'mms';

const drawerWidth: number = 240;

const AppBar = styled(MuiAppBar, {
    shouldForwardProp: prop => prop !== 'open',
})<AppBarProps>(({ theme, open }) => ({
    zIndex: theme.zIndex.drawer + 1,
    transition: theme.transitions.create(['width', 'margin'], {
        easing: theme.transitions.easing.sharp,
        duration: theme.transitions.duration.leavingScreen,
    }),
    backgroundColor: theme.palette.background.paper,
    color: theme.palette.text.primary,
    boxShadow: '0 1px 3px 0 rgba(0, 0, 0, 0.1), 0 1px 2px 0 rgba(0, 0, 0, 0.06)',
    borderRadius: 0,
    ...(open && {
        marginLeft: drawerWidth,
        width: `calc(100% - ${drawerWidth}px)`,
        transition: theme.transitions.create(['width', 'margin'], {
            easing: theme.transitions.easing.sharp,
            duration: theme.transitions.duration.enteringScreen,
        }),
    }),
}));

const Drawer = styled(MuiDrawer, {
    shouldForwardProp: prop => prop !== 'open',
})(({ theme, open }) => ({
    '& .MuiDrawer-paper': {
        position: 'relative',
        whiteSpace: 'nowrap',
        width: drawerWidth,
        transition: theme.transitions.create('width', {
            easing: theme.transitions.easing.sharp,
            duration: theme.transitions.duration.enteringScreen,
        }),
        boxSizing: 'border-box',
        borderRight: `1px solid ${theme.palette.divider}`,
        backgroundColor: theme.palette.background.paper,
        ...(!open && {
            overflowX: 'hidden',
            transition: theme.transitions.create('width', {
                easing: theme.transitions.easing.sharp,
                duration: theme.transitions.duration.leavingScreen,
            }),
            width: theme.spacing(7),
            [theme.breakpoints.up('sm')]: {
                width: theme.spacing(9),
            },
        }),
    },
}));

export default function UILayout() {
    const [open, setOpen] = useState(true);
    const location = useLocation();
    const adaptorType = useVSTUIStore(state => state.vstAdaptorType);
    const version = useVSTUIStore(state => state.vstVersion);
    const listItems = adaptorType === VST_ADAPTOR || adaptorType === MMS_ADAPTOR ? <VSTListItems /> : <StreamerListItems />;
    const toggleDrawer = () => {
        setOpen(!open);
    };

    const theme = useTheme();
    const isMobile = useMediaQuery(theme.breakpoints.down('md'));

    useEffect(() => {
        if (isMobile) {
            setOpen(false);
        }
    }, [isMobile]);

    const isLiveStreamVisible = location.pathname === '/live-streams';
    const isReplayStreamVisible = location.pathname === '/recorded-streams';
    const isVideoWallVisible = location.pathname === '/video-wall';
    const isExperimentalVisible = location.pathname === '/experimental';
    const isMediaStreamsVisible = location.pathname === '/media-streams';
    const isMediaUploadVisible = location.pathname === '/media-upload';

    return (
        <Box sx={{ display: 'flex', overflowX: 'hidden', minHeight: '100vh', position: 'relative' }}>
            <AppBar position='fixed' open={open} enableColorOnDark>
                <Toolbar
                    sx={{
                        pr: '24px',
                        minHeight: '64px',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                    }}
                >
                    <Box sx={{ display: 'flex', alignItems: 'center' }}>
                        <IconButton
                            edge='start'
                            color='inherit'
                            aria-label='open drawer'
                            onClick={toggleDrawer}
                            sx={{
                                display: isMobile ? 'none' : 'default',
                                marginRight: '24px',
                                ...(open && { display: 'none' }),
                            }}
                        >
                            <MenuIcon />
                        </IconButton>
                        <Typography
                            variant='h5'
                            sx={{
                                fontWeight: 600,
                                letterSpacing: '0.5px',
                            }}
                        >
                            {adaptorType === VST_ADAPTOR ? 'VSS VIOS' : adaptorType ? adaptorType.toUpperCase() : ''}{' '}
                            <sup style={{ opacity: 0.8, fontSize: '0.7em' }}>{version}</sup>
                        </Typography>
                    </Box>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        <Stack
                            direction='row'
                            spacing={2}
                            sx={{
                                display: {
                                    xs: 'none',
                                    sm: 'none',
                                    md: 'inherit',
                                },
                            }}
                        >
                            <UTCDateTime />
                            <LocalDateTime />
                        </Stack>
                        <Stack direction='row' spacing={0.5}>
                            <RefreshButton />
                            <SettingsDropdown />
                        </Stack>
                    </Box>
                </Toolbar>
            </AppBar>
            <Drawer variant='permanent' open={open}>
                <Toolbar
                    sx={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'flex-end',
                        px: [1],
                        minHeight: '64px',
                    }}
                >
                    <IconButton onClick={toggleDrawer} size='small'>
                        <ChevronLeftIcon />
                    </IconButton>
                </Toolbar>
                <Divider />
                <List component='nav'>{listItems}</List>
            </Drawer>
            <Box
                component='main'
                sx={{
                    flexGrow: 1,
                    height: '100vh',
                    overflow: 'auto',
                    display: 'flex',
                    flexDirection: 'column',
                    backgroundColor: theme => theme.palette.background.default,
                    '&::-webkit-scrollbar': {
                        width: '8px',
                        height: '8px',
                    },
                    '&::-webkit-scrollbar-track': {
                        background: theme => theme.palette.background.paper,
                    },
                    '&::-webkit-scrollbar-thumb': {
                        background: theme => theme.palette.divider,
                        borderRadius: '4px',
                    },
                }}
            >
                <Toolbar />
                <Container
                    maxWidth={false}
                    sx={{
                        mt: 4,
                        mb: 4,
                        flex: 1,
                        px: { xs: 2, sm: 3, md: 4 },
                    }}
                >
                    <div
                        style={{
                            display: isLiveStreamVisible ? 'block' : 'none',
                        }}
                    >
                        <LiveStream />
                    </div>
                    <div
                        style={{
                            display: isReplayStreamVisible ? 'block' : 'none',
                        }}
                    >
                        <ReplayStream />
                    </div>
                    <div
                        style={{
                            display: isVideoWallVisible ? 'block' : 'none',
                        }}
                    >
                        <VideoWall />
                    </div>
                    <div
                        style={{
                            display: isExperimentalVisible ? 'block' : 'none',
                        }}
                    >
                        <Experimental />
                    </div>
                    <div
                        style={{
                            display: isMediaStreamsVisible ? 'block' : 'none',
                        }}
                    >
                        <MediaStreams />
                    </div>
                    <div
                        style={{
                            display: isMediaUploadVisible ? 'block' : 'none',
                        }}
                    >
                        <MediaUpload />
                    </div>
                    <Outlet />
                </Container>
                <Footer />
            </Box>
        </Box>
    );
}

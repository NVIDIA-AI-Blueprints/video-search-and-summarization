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
import { SxProps, Theme } from '@mui/material';
import { CSSProperties } from 'react';

type MuiStyles = {
    menuItem: SxProps<Theme>;
    videoContainer: SxProps<Theme>;
    videoWrapper: SxProps<Theme>;
    errorOverlay: SxProps<Theme>;
    loadingOverlay: SxProps<Theme>;
    networkQualityWidget: SxProps<Theme>;
    controls: SxProps<Theme>;
};

type HtmlStyles = {
    video: CSSProperties;
};

export const muiStyles: MuiStyles = {
    menuItem: {
        '&.Mui-selected': {
            backgroundColor: 'primary.main',
            color: 'primary.contrastText',
            '&:hover': {
                backgroundColor: 'primary.dark',
            },
        },
    },
    videoContainer: {
        position: 'relative',
        paddingTop: '56.25%', // 16:9 aspect ratio
        width: '100%',
        backgroundColor: 'black',
        overflow: 'visible',
        transform: 'translateZ(0)',
        willChange: 'transform',
        isolation: 'isolate',
        minHeight: '300px', // Add minimum height to prevent collapse
        height: '100%', // Ensure full height
        zIndex: 1,
    },
    videoWrapper: {
        position: 'absolute',
        top: 0,
        left: 0,
        width: '100%',
        height: '100%',
        transform: 'translateZ(0)',
        willChange: 'transform',
        zIndex: 1,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
    },
    errorOverlay: {
        position: 'absolute',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: 'rgba(0, 0, 0, 0.5)',
        zIndex: 3,
        color: 'white',
    },
    loadingOverlay: {
        position: 'absolute',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: 'rgba(0, 0, 0, 0.5)',
        zIndex: 3,
    },
    networkQualityWidget: {
        position: 'absolute',
        bottom: 10,
        right: 10,
        zIndex: 1001,
        pointerEvents: 'none',
    },
    controls: {
        display: 'flex',
        alignItems: 'center',
        width: '100%',
        position: 'relative',
        zIndex: 3,
    },
};

export const htmlStyles: HtmlStyles = {
    video: {
        position: 'absolute',
        width: '100%',
        height: '100%',
        objectFit: 'contain',
        transform: 'translateZ(0)',
        willChange: 'transform',
        zIndex: 2,
    },
};

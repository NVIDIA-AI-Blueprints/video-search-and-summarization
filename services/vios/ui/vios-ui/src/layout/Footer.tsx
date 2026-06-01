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
import { Box, Typography } from '@mui/material';
import { getUIVersion } from '../utils/misc/versionUtils';

const Footer = () => {
    const uiVersion = getUIVersion();

    return (
        <Box
            component='footer'
            sx={{
                py: 1.5,
                px: 2,
                mt: 'auto',
                backgroundColor: 'transparent',
                borderTop: 1,
                borderColor: 'divider',
                display: 'flex',
                justifyContent: 'center',
                alignItems: 'center',
                gap: 2,
                opacity: 0.8,
            }}
        >
            <Typography variant='body2' color='text.secondary' sx={{ fontWeight: 400 }}>
                VSS VIOS UI v{uiVersion}
            </Typography>
        </Box>
    );
};

export default Footer;

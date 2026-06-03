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
import React, { Component, ErrorInfo, ReactNode } from 'react';
import { Card, CardContent, Typography, Box, IconButton } from '@mui/material';
import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline';
import RefreshIcon from '@mui/icons-material/Refresh';

interface Props {
    children: ReactNode;
    widgetName?: string;
}

interface State {
    hasError: boolean;
    error: Error | null;
}

class DashboardWidgetErrorBoundary extends Component<Props, State> {
    public state: State = {
        hasError: false,
        error: null,
    };

    public static getDerivedStateFromError(error: Error): State {
        return { hasError: true, error };
    }

    public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
        console.error(`Dashboard widget error in ${this.props.widgetName || 'Unknown'}:`, error, errorInfo);
    }

    private handleRetry = () => {
        this.setState({ hasError: false, error: null });
    };

    public render() {
        if (this.state.hasError) {
            return (
                <Card
                    sx={{
                        minHeight: 120,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        bgcolor: 'action.hover',
                        border: '1px dashed',
                        borderColor: 'error.main',
                    }}
                >
                    <CardContent sx={{ textAlign: 'center', py: 2 }}>
                        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', mb: 1 }}>
                            <ErrorOutlineIcon color='error' sx={{ mr: 1 }} />
                            <Typography variant='body2' color='error'>
                                Widget Error
                            </Typography>
                        </Box>
                        <Typography variant='caption' color='text.secondary' sx={{ mb: 1, display: 'block' }}>
                            {this.props.widgetName || 'Component'} failed to load
                        </Typography>
                        <IconButton size='small' onClick={this.handleRetry} sx={{ mt: 1 }} title='Retry loading widget'>
                            <RefreshIcon fontSize='small' />
                        </IconButton>
                    </CardContent>
                </Card>
            );
        }

        return this.props.children;
    }
}

export default DashboardWidgetErrorBoundary;

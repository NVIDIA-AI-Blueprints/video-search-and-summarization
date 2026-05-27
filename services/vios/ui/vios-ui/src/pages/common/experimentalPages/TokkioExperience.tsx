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
import { FC, useState, useRef, useCallback } from 'react';
import {
    Grid2 as Grid,
    Card,
    Container,
    Box,
    CardHeader,
    Button,
    ButtonGroup,
    Switch,
    Typography,
    Stack,
    styled,
    Paper,
} from '@mui/material';
import StreamManager, { ErrorType } from 'vst-streaming-lib';
import React from 'react';
import { StreamType } from 'vst-streaming-lib';
import appConfig from '../../../config';

// Updated styled components
const VideoContainer = styled(Box)(({ theme }) => ({
    padding: theme.spacing(3),
    '& video': {
        width: '100%',
        height: 'auto',
        borderRadius: theme.shape.borderRadius,
        backgroundColor: theme.palette.grey[900],
    },
}));

const ControlsSection = styled(Paper)(({ theme }) => ({
    padding: theme.spacing(2),
    marginBottom: theme.spacing(2),
    borderRadius: theme.shape.borderRadius,
    boxShadow: theme.shadows[2],
}));

const OptionsStack = styled(Stack)(({ theme }) => ({
    padding: theme.spacing(2),
    '& .MuiSwitch-root': {
        marginLeft: 'auto',
    },
}));

interface StreamConfig {
    enableCamera: boolean;
    enableMicrophone: boolean;
    enableDummyUDPCall: boolean;
}

const Webrtc: FC = () => {
    const streamManager = useRef<StreamManager | null>(null);
    const [isStreaming, setIsStreaming] = useState(false);
    const [config, setConfig] = useState<StreamConfig>({
        enableDummyUDPCall: false,
        enableCamera: true,
        enableMicrophone: true,
    });

    const onErrorCallback = useCallback(async (error: ErrorType) => {
        console.error('on Error: ', error);
        await stopStreaming();
    }, []);

    const handleConfigChange = (key: keyof StreamConfig) => (event: React.ChangeEvent<HTMLInputElement>) => {
        setConfig(prev => ({ ...prev, [key]: event.target.checked }));
        console.log(`${key}:`, event.target.checked);
    };

    const startStreaming = async () => {
        const { enableCamera, enableMicrophone } = config;

        if (isStreaming) {
            alert('Please stop the existing stream first');
            return;
        }

        if (!enableCamera && !enableMicrophone) {
            alert('Enable either camera or microphone or both');
            return;
        }

        streamManager.current = new StreamManager();
        if (streamManager.current) {
            const streambridgeEndpoint = appConfig.streambridgeEndpoint;
            let wsEndpoint = streambridgeEndpoint.startsWith('https')
                ? streambridgeEndpoint.replace('https', 'wss')
                : streambridgeEndpoint.replace('http', 'ws');

            let proxy = window.location.pathname;
            if (proxy !== '/' && proxy.length > 0) {
                if (proxy[proxy.length - 1] === '/') {
                    proxy = proxy.slice(0, -1);
                }
                wsEndpoint = `${wsEndpoint}${wsEndpoint.endsWith('/') ? '' : '/'}${proxy}`;
            }

            streamManager.current.updateConfig({
                inboundStreamVideoElementId: 'tokkio-avatar-stream',
                outboundStreamVideoElementId: 'webcam-stream',
                vstWebsocketEndpoint: wsEndpoint,
                ...config,
                streamType: StreamType.Streambridge,
                enableWebsocketPing: true,
                enableLogs: true,
                errorCallback: onErrorCallback,
            });

            console.log('streamManager.current', streamManager.current.getConfig());

            streamManager.current.startStreaming({
                options: {
                    rtptransport: 'udp',
                    timeout: 60,
                    quality: 'auto',
                },
            });
            setIsStreaming(true);
        }
    };

    const stopStreaming = async () => {
        if (streamManager.current) {
            try {
                await streamManager.current.stopStreaming();
                streamManager.current = null;
                setIsStreaming(false);
            } catch (error) {
                console.error('Error stopping stream:', error);
            }
        }
        setIsStreaming(false);
    };

    return (
        <Container maxWidth='lg' sx={{ py: 4 }}>
            <Typography variant='h4' gutterBottom sx={{ mb: 3 }}>
                Tokkio Experience
            </Typography>
            <Grid container spacing={3}>
                {/* Video Streams */}
                {[
                    {
                        title: 'Tokkio Avatar Stream',
                        id: 'tokkio-avatar-stream',
                    },
                    { title: 'Webcam Stream', id: 'webcam-stream' },
                ].map(({ title, id }) => (
                    <Grid key={id} size={{ xs: 12, sm: 6 }}>
                        <Card elevation={3}>
                            <CardHeader title={title} />
                            <VideoContainer>
                                <video autoPlay controls muted id={id} />
                            </VideoContainer>
                        </Card>
                    </Grid>
                ))}

                {/* Controls */}
                <Grid size={{ xs: 12 }}>
                    <ControlsSection elevation={3}>
                        <Typography variant='h6' gutterBottom>
                            Stream Controls
                        </Typography>
                        <Box sx={{ p: 2 }}>
                            <ButtonGroup variant='contained' fullWidth size='large'>
                                <Button onClick={startStreaming} disabled={isStreaming} color='primary' sx={{ py: 1.5 }}>
                                    Start Stream
                                </Button>
                                <Button onClick={stopStreaming} disabled={!isStreaming} color='error' sx={{ py: 1.5 }}>
                                    Stop Stream
                                </Button>
                            </ButtonGroup>
                        </Box>
                    </ControlsSection>
                </Grid>

                {/* Options */}
                <Grid size={{ xs: 12 }}>
                    <ControlsSection elevation={3}>
                        <Typography variant='h6' gutterBottom sx={{ px: 2, pt: 1 }}>
                            Stream Options
                        </Typography>
                        {[
                            {
                                label: 'Camera',
                                key: 'enableCamera',
                                description: 'Enable/disable camera stream',
                            },
                            {
                                label: 'Microphone',
                                key: 'enableMicrophone',
                                description: 'Enable/disable microphone input',
                            },
                            {
                                label: 'Dummy UDP Stream',
                                key: 'enableDummyUDPCall',
                                description: 'Enable/disable UDP stream simulation',
                            },
                        ].map(({ label, key, description }) => (
                            <OptionsStack key={key} direction='row' spacing={1} alignItems='center'>
                                <Box>
                                    <Typography variant='subtitle1'>{label}</Typography>
                                    <Typography variant='body2' color='text.secondary'>
                                        {description}
                                    </Typography>
                                </Box>
                                <Switch
                                    checked={config[key as keyof StreamConfig]}
                                    onChange={handleConfigChange(key as keyof StreamConfig)}
                                />
                            </OptionsStack>
                        ))}
                    </ControlsSection>
                </Grid>
            </Grid>
        </Container>
    );
};

export default Webrtc;

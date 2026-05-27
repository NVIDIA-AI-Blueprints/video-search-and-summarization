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
import React, { useState, useEffect, useRef } from 'react';
import store from 'store2';
import {
    Box,
    TextField,
    IconButton,
    Paper,
    Typography,
    Avatar,
    Card,
    CardContent,
    CardMedia,
    InputAdornment,
    CircularProgress,
    Alert,
    Stack,
} from '@mui/material';
import SendIcon from '@mui/icons-material/Send';
import DeleteIcon from '@mui/icons-material/Delete';
import UserAvatar from '@mui/icons-material/AccountCircle';
import SupportAgentIcon from '@mui/icons-material/SupportAgent';
import HttpIcon from '@mui/icons-material/Http';
import { v4 as generateUUID } from 'uuid';
import nvAxios from '../../services/Axios';

interface Message {
    type: 'user' | 'bot';
    content: string;
    mediaType?: 'image' | 'video' | 'text' | null;
    mediaUrl?: string | null;
}

interface QueryItem {
    type: string;
    content: string;
    ref_id: string;
    metadata: Record<string, unknown>;
}

interface ResponseItem {
    type: string;
    content: string;
}

const ChatBot: React.FC = () => {
    const [messages, setMessages] = useState<Message[]>([]);
    const [input, setInput] = useState<string>('');
    const [endpoint, setEndpoint] = useState<string>(store.get('chatbotEndpoint') || '');
    const [isLoading, setIsLoading] = useState<boolean>(false);
    const [error, setError] = useState<string | null>(null);
    const messagesEndRef = useRef<HTMLDivElement>(null);

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    };

    useEffect(scrollToBottom, [messages]);

    useEffect(() => {
        store.set('chatbotEndpoint', endpoint);
    }, [endpoint]);

    const handleSend = async () => {
        if (input.trim() === '' || endpoint.trim() === '') return;

        const userMessage: Message = { type: 'user', content: input };
        setMessages(prev => [...prev, userMessage]);
        setInput('');
        setIsLoading(true);
        setError(null);

        const userQuery: QueryItem[] = [
            {
                type: 'text/plain',
                content: input,
                ref_id: generateUUID(),
                metadata: {},
            },
        ];

        try {
            const response = await nvAxios.post(`${endpoint}/api/query`, userQuery, { headers: { 'Content-Type': 'application/json' } });
            const data = response.data;

            const botMessage: Message = {
                type: 'bot',
                content: '',
                mediaType: null,
                mediaUrl: null,
            };

            data.response.forEach((item: ResponseItem) => {
                if (item.type === 'text/plain') {
                    botMessage.content += `${item.content}\n`;
                } else if (item.type === 'image/jpeg') {
                    botMessage.mediaType = 'image';
                    botMessage.mediaUrl = `data:image/jpeg;base64,${item.content}`;
                } else if (item.type === 'video/mp4' || item.type === 'video/mp2t') {
                    botMessage.mediaType = 'video';
                    botMessage.mediaUrl = `data:${item.type};base64,${item.content}`;
                }
            });

            botMessage.content = botMessage.content.trim();
            setMessages(prev => [...prev, botMessage]);
        } catch (error) {
            console.error('Error fetching response:', error);
            setError('An error occurred while fetching the response. Please try again.');
        } finally {
            setIsLoading(false);
        }
    };

    const handleDelete = async () => {
        if (endpoint.trim() === '') return;

        setIsLoading(true);
        setError(null);

        try {
            await nvAxios.delete(`${endpoint}/api/memory`, {
                headers: { 'Content-Type': 'application/json' },
            });
            setMessages([]);
            setInput('');
            const botMessage: Message = {
                type: 'bot',
                content: 'Memory deleted successfully',
                mediaType: 'text',
                mediaUrl: null,
            };
            setMessages([botMessage]);
        } catch (error) {
            console.error('Error deleting memory:', error);
            setError('An error occurred while deleting the memory. Please try again.');
        } finally {
            setIsLoading(false);
        }
    };

    const renderMessage = (message: Message, index: number) => {
        const isBot = message.type === 'bot';
        return (
            <Box
                key={index}
                sx={{
                    display: 'flex',
                    justifyContent: isBot ? 'flex-start' : 'flex-end',
                    mb: 2,
                    px: 2,
                }}
            >
                <Box
                    sx={{
                        display: 'flex',
                        flexDirection: isBot ? 'row' : 'row-reverse',
                        alignItems: 'flex-start',
                        maxWidth: '80%',
                    }}
                >
                    <Avatar
                        sx={{
                            bgcolor: isBot ? 'primary.main' : 'secondary.main',
                            mr: isBot ? 1 : 0,
                            ml: isBot ? 0 : 1,
                            width: 32,
                            height: 32,
                        }}
                    >
                        {isBot ? <SupportAgentIcon sx={{ fontSize: 20 }} /> : <UserAvatar sx={{ fontSize: 20 }} />}
                    </Avatar>
                    <Card
                        variant='outlined'
                        sx={{
                            borderRadius: 2,
                            bgcolor: isBot ? 'background.paper' : 'primary.main',
                            borderColor: isBot ? 'divider' : 'primary.main',
                            boxShadow: 1,
                        }}
                    >
                        <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
                            <Typography
                                variant='body2'
                                component='div'
                                sx={{
                                    color: isBot ? 'text.primary' : 'primary.contrastText',
                                }}
                            >
                                {message.content.split('\n').map((line, i) => (
                                    <React.Fragment key={i}>
                                        {line}
                                        <br />
                                    </React.Fragment>
                                ))}
                                {message.mediaType === 'image' && (
                                    <CardMedia
                                        component='img'
                                        image={message.mediaUrl || ''}
                                        alt='Image response'
                                        sx={{
                                            borderRadius: 1,
                                            mt: 1,
                                            maxHeight: 300,
                                            objectFit: 'contain',
                                        }}
                                    />
                                )}
                                {message.mediaType === 'video' && (
                                    <CardMedia
                                        component='video'
                                        src={message.mediaUrl || ''}
                                        controls
                                        sx={{
                                            borderRadius: 1,
                                            mt: 1,
                                            maxHeight: 300,
                                        }}
                                    />
                                )}
                            </Typography>
                        </CardContent>
                    </Card>
                </Box>
            </Box>
        );
    };

    return (
        <Box
            sx={{
                height: '75vh',
                display: 'flex',
                flexDirection: 'column',
                bgcolor: 'background.paper',
                borderRadius: 2,
                overflow: 'hidden',
                boxShadow: 3,
            }}
        >
            <Box
                sx={{
                    p: 2,
                    bgcolor: 'background.paper',
                    borderBottom: 1,
                    borderColor: 'divider',
                }}
            >
                <TextField
                    fullWidth
                    variant='outlined'
                    placeholder='Enter API endpoint (e.g., http://ip:port)'
                    value={endpoint}
                    onChange={e => setEndpoint(e.target.value)}
                    InputProps={{
                        startAdornment: (
                            <InputAdornment position='start'>
                                <HttpIcon color='primary' />
                            </InputAdornment>
                        ),
                    }}
                    size='small'
                />
            </Box>
            <Paper
                elevation={0}
                sx={{
                    flex: 1,
                    overflow: 'auto',
                    bgcolor: 'background.default',
                    backgroundImage: theme =>
                        `linear-gradient(${theme.palette.mode === 'dark' ? 'rgba(0, 0, 0, 0.2)' : 'rgba(255, 255, 255, 0.9)'}, 
                        ${theme.palette.mode === 'dark' ? 'rgba(0, 0, 0, 0.2)' : 'rgba(255, 255, 255, 0.9)'})`,
                }}
            >
                {messages.map(renderMessage)}
                {error && (
                    <Alert severity='error' sx={{ mx: 2, mt: 2 }}>
                        {error}
                    </Alert>
                )}
                <div ref={messagesEndRef} />
            </Paper>
            <Box
                sx={{
                    p: 2,
                    bgcolor: 'background.paper',
                    borderTop: 1,
                    borderColor: 'divider',
                }}
            >
                <TextField
                    disabled={isLoading}
                    fullWidth
                    variant='outlined'
                    placeholder='Type your message...'
                    value={input}
                    onChange={e => setInput(e.target.value)}
                    onKeyPress={e => e.key === 'Enter' && handleSend()}
                    size='small'
                    InputProps={{
                        endAdornment: (
                            <Stack direction='row' spacing={1}>
                                <IconButton color='error' onClick={handleDelete} disabled={isLoading} size='small'>
                                    <DeleteIcon />
                                </IconButton>
                                <IconButton color='primary' onClick={handleSend} disabled={isLoading} size='small'>
                                    {isLoading ? <CircularProgress size={20} /> : <SendIcon />}
                                </IconButton>
                            </Stack>
                        ),
                    }}
                />
            </Box>
        </Box>
    );
};

export default ChatBot;

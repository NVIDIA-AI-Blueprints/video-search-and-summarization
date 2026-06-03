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
import React, { useState, ChangeEvent } from 'react';
import {
    Card,
    CardHeader,
    CardContent,
    CardActions,
    TextField,
    List,
    ListItem,
    ListItemText,
    Checkbox,
    FormControlLabel,
    Chip,
    Box,
    Typography,
    Stack,
    Divider,
    IconButton,
    Tooltip,
} from '@mui/material';
import nvAxios from '../../services/Axios';
import config from '../../config';
import { StreamList } from '../../interfaces/interfaces';
import { LoadingButton } from '@mui/lab';
import AddIcon from '@mui/icons-material/Add';
import RefreshIcon from '@mui/icons-material/Refresh';
import HelpOutlineIcon from '@mui/icons-material/HelpOutline';

interface Stream {
    name: string;
    url: string;
    status?: 'success' | 'error';
}

const AddNvStreamerStreams: React.FC = () => {
    const [apiEndpoint, setApiEndpoint] = useState<string>('');
    const [streams, setStreams] = useState<Stream[]>([]);
    const [selectedStreams, setSelectedStreams] = useState<string[]>([]);
    const [summary, setSummary] = useState<{
        success: number;
        error: number;
    } | null>(null);
    const [isLoading, setIsLoading] = useState<boolean>(false);

    const handleSubmit = async (): Promise<void> => {
        setIsLoading(true);
        try {
            const response = await nvAxios.get(apiEndpoint);
            const data: StreamList[] = response.data;
            const filteredStreams: Stream[] = data.flatMap(item => {
                const [, value] = Object.entries(item)[0];
                return value
                    .filter(stream => stream.isMain && stream.url)
                    .map(stream => ({
                        name: stream.name,
                        url: stream.url,
                    }));
            });
            setStreams(filteredStreams);
            setSelectedStreams([]);
            setSummary(null);
        } catch (error) {
            console.error('Error fetching streams:', error);
        } finally {
            setIsLoading(false);
        }
    };

    const handleSelectAll = (event: ChangeEvent<HTMLInputElement>): void => {
        if (event.target.checked) {
            setSelectedStreams(streams.map(stream => stream.name));
        } else {
            setSelectedStreams([]);
        }
    };

    const handleSelectStream = (name: string): void => {
        setSelectedStreams(prev => (prev.includes(name) ? prev.filter(item => item !== name) : [...prev, name]));
    };

    const handleAddSelectedStreams = async (): Promise<void> => {
        setIsLoading(true);
        let successCount = 0;
        let errorCount = 0;
        const updatedStreams = [...streams];
        for (const streamName of selectedStreams) {
            const streamIndex = updatedStreams.findIndex(s => s.name === streamName);
            if (streamIndex !== -1) {
                const stream = updatedStreams[streamIndex];
                const jsonData = { name: stream.name, sensorUrl: stream.url };
                try {
                    await nvAxios.post(`${config.sensorManagementEndpoint}/api/v1/sensor/add`, jsonData);
                    updatedStreams[streamIndex] = {
                        ...stream,
                        status: 'success',
                    };
                    successCount++;
                } catch (error) {
                    console.error(`Error adding stream ${streamName}:`, error);
                    updatedStreams[streamIndex] = {
                        ...stream,
                        status: 'error',
                    };
                    errorCount++;
                }
            }
        }
        setStreams(updatedStreams);
        setSummary({ success: successCount, error: errorCount });
        setIsLoading(false);
    };

    return (
        <Card sx={{ display: 'flex', flexDirection: 'column' }}>
            <CardHeader
                title={
                    <Typography variant='h6' sx={{ fontWeight: 500 }}>
                        Add NVStreamer Streams
                    </Typography>
                }
                subheader={
                    <Typography variant='body2' color='text.secondary'>
                        Add streams from an NVStreamer instance
                    </Typography>
                }
                action={
                    <Tooltip title='Enter the NVStreamer API endpoint to fetch available streams'>
                        <IconButton>
                            <HelpOutlineIcon />
                        </IconButton>
                    </Tooltip>
                }
            />
            <Divider />
            <CardContent sx={{ flexGrow: 1 }}>
                <Stack spacing={3}>
                    <Box>
                        <Stack direction='row' spacing={2} alignItems='center'>
                            <TextField
                                fullWidth
                                label='API Endpoint'
                                value={apiEndpoint}
                                onChange={(e: ChangeEvent<HTMLInputElement>) => setApiEndpoint(e.target.value)}
                                placeholder='http://nvstreamer-ip:port/api/v1/sensor/streams'
                                size='small'
                            />
                            <LoadingButton
                                onClick={handleSubmit}
                                variant='contained'
                                loading={isLoading}
                                startIcon={<RefreshIcon />}
                                sx={{
                                    minWidth: 120,
                                    whiteSpace: 'nowrap',
                                    '&:hover': {
                                        transform: 'translateY(-1px)',
                                    },
                                }}
                            >
                                Fetch Streams
                            </LoadingButton>
                        </Stack>
                    </Box>

                    {streams.length > 0 && (
                        <Box>
                            <FormControlLabel
                                control={<Checkbox checked={selectedStreams.length === streams.length} onChange={handleSelectAll} />}
                                label={<Typography variant='subtitle2'>Select All</Typography>}
                            />
                            <List
                                sx={{
                                    maxHeight: 300,
                                    overflow: 'auto',
                                    bgcolor: 'background.paper',
                                    borderRadius: 1,
                                    border: 1,
                                    borderColor: 'divider',
                                }}
                            >
                                {streams.map(stream => (
                                    <ListItem
                                        key={stream.name}
                                        divider
                                        sx={{
                                            '&:last-child': {
                                                borderBottom: 'none',
                                            },
                                        }}
                                    >
                                        <Checkbox
                                            checked={selectedStreams.includes(stream.name)}
                                            onChange={() => handleSelectStream(stream.name)}
                                        />
                                        <ListItemText
                                            primary={<Typography variant='body1'>{stream.name}</Typography>}
                                            secondary={
                                                <Typography
                                                    variant='body2'
                                                    color='text.secondary'
                                                    sx={{
                                                        wordBreak: 'break-all',
                                                    }}
                                                >
                                                    {stream.url}
                                                </Typography>
                                            }
                                        />
                                        {stream.status && (
                                            <Chip
                                                label={stream.status === 'success' ? 'Added' : 'Failed'}
                                                color={stream.status === 'success' ? 'success' : 'error'}
                                                size='small'
                                                sx={{ ml: 1 }}
                                            />
                                        )}
                                    </ListItem>
                                ))}
                            </List>
                            {summary && (
                                <Box mt={2}>
                                    <Typography variant='subtitle1' sx={{ mb: 1, fontWeight: 500 }}>
                                        Summary
                                    </Typography>
                                    <Stack direction='row' spacing={2}>
                                        <Chip label={`${summary.success} Added`} color='success' variant='outlined' />
                                        <Chip label={`${summary.error} Failed`} color='error' variant='outlined' />
                                    </Stack>
                                </Box>
                            )}
                        </Box>
                    )}
                </Stack>
            </CardContent>
            <Divider />
            <CardActions sx={{ p: 2, justifyContent: 'flex-end' }}>
                <LoadingButton
                    onClick={handleAddSelectedStreams}
                    variant='contained'
                    disabled={selectedStreams.length === 0}
                    loading={isLoading}
                    startIcon={<AddIcon />}
                    sx={{
                        minWidth: 180,
                        '&:hover': {
                            transform: 'translateY(-1px)',
                        },
                    }}
                >
                    Add Selected Streams
                </LoadingButton>
            </CardActions>
        </Card>
    );
};

export default AddNvStreamerStreams;

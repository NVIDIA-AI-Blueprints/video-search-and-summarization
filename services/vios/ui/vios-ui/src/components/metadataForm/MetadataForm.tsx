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
import React, { useState, useEffect } from 'react';
import {
    Box,
    Card,
    CardContent,
    Typography,
    Table,
    TableBody,
    TableCell,
    TableContainer,
    TableHead,
    TableRow,
    Paper,
    Skeleton,
    TablePagination,
} from '@mui/material';
import nvAxios from '../../services/Axios';
import config from '../../config';
import LOG from '../../utils/misc/Logger';

interface MetadataFormProps {
    streamId: string;
}

interface Metadata {
    AudioCaps: string;
    AudioCodec: string;
    Bitrate: number;
    Channels: number;
    Codec: string;
    Container: string;
    ContainerCaps: string;
    Depth: number;
    Duration: number;
    FrameCount: number;
    Framerate: number;
    FramerateDenom: number;
    FramerateNum: number;
    Height: number;
    SampleRate: number;
    ScanType: string;
    VideoCaps: string;
    Width: number;
}

const MetadataForm: React.FC<MetadataFormProps> = ({ streamId }) => {
    const [metadata, setMetadata] = useState<Metadata | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [page, setPage] = useState(0);
    const [rowsPerPage] = useState(12);

    useEffect(() => {
        const fetchMetadata = async () => {
            try {
                setLoading(true);
                setError(null);
                const response = await nvAxios.get(
                    `${config.storageManagementEndpoint}/api/v1/storage/file/mediainfo?sensorId=${streamId}`,
                    { headers: { streamId: streamId } }
                );
                setMetadata(response.data);
            } catch (error) {
                LOG.error(`Failed to fetch metadata for stream ${streamId}`);
                setError('Failed to fetch metadata');
            } finally {
                setLoading(false);
            }
        };

        if (streamId) {
            fetchMetadata();
        }
    }, [streamId]);

    const handleChangePage = (_event: unknown, newPage: number) => {
        setPage(newPage);
    };

    if (loading) {
        return (
            <Card>
                <CardContent>
                    <Typography variant='h6' gutterBottom>
                        <Skeleton width='40%' />
                    </Typography>
                    <Paper elevation={1}>
                        <TableContainer>
                            <Table>
                                <TableHead>
                                    <TableRow>
                                        <TableCell>
                                            <Skeleton width='80%' />
                                        </TableCell>
                                        <TableCell>
                                            <Skeleton width='60%' />
                                        </TableCell>
                                    </TableRow>
                                </TableHead>
                                <TableBody>
                                    {[...Array(8)].map((_, index) => (
                                        <TableRow key={index}>
                                            <TableCell>
                                                <Skeleton width='90%' />
                                            </TableCell>
                                            <TableCell>
                                                <Skeleton width='70%' />
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </TableContainer>
                        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', p: 2 }}>
                            <Skeleton width='30%' />
                            <Skeleton width='20%' />
                        </Box>
                    </Paper>
                </CardContent>
            </Card>
        );
    }

    if (error) {
        return (
            <Card>
                <CardContent>
                    <Typography color='error'>{error}</Typography>
                </CardContent>
            </Card>
        );
    }

    if (!metadata) {
        return null;
    }

    const metadataRows = [
        { label: 'Audio Codec', value: metadata.AudioCodec },
        { label: 'Audio Caps', value: metadata.AudioCaps },
        {
            label: 'Bitrate',
            value: metadata.Bitrate ? `${metadata.Bitrate.toLocaleString()} bps` : 'N/A',
        },
        { label: 'Channels', value: metadata.Channels },
        { label: 'Codec', value: metadata.Codec },
        { label: 'Container', value: metadata.Container },
        { label: 'Container Caps', value: metadata.ContainerCaps },
        { label: 'Depth', value: metadata.Depth },
        { label: 'Duration', value: `${metadata.Duration} seconds` },
        { label: 'Frame Count', value: metadata.FrameCount },
        { label: 'Framerate', value: `${metadata.Framerate} fps` },
        { label: 'Framerate Denominator', value: metadata.FramerateDenom },
        { label: 'Framerate Numerator', value: metadata.FramerateNum },
        { label: 'Height', value: metadata.Height },
        {
            label: 'Sample Rate',
            value: metadata.SampleRate ? `${metadata.SampleRate.toLocaleString()} Hz` : 'N/A',
        },
        { label: 'Scan Type', value: metadata.ScanType },
        { label: 'Video Caps', value: metadata.VideoCaps },
        { label: 'Width', value: metadata.Width },
    ];

    // Calculate the starting index for the current page
    const startIndex = page * rowsPerPage;
    const paginatedRows = metadataRows.slice(startIndex, startIndex + rowsPerPage);

    return (
        <Card sx={{ height: '100%' }}>
            <CardContent>
                <Typography variant='h4' gutterBottom>
                    Stream Metadata
                </Typography>
                <TableContainer
                    component={Paper}
                    sx={{
                        boxShadow: 'none',
                        border: '1px solid',
                        borderColor: 'divider',
                    }}
                >
                    <Table size='small'>
                        <TableHead>
                            <TableRow sx={{ backgroundColor: 'background.paper' }}>
                                <TableCell
                                    sx={{
                                        fontWeight: 600,
                                        width: '40%',
                                        borderBottom: '2px solid',
                                        borderColor: 'divider',
                                    }}
                                >
                                    Property
                                </TableCell>
                                <TableCell
                                    sx={{
                                        fontWeight: 600,
                                        borderBottom: '2px solid',
                                        borderColor: 'divider',
                                    }}
                                >
                                    Value
                                </TableCell>
                            </TableRow>
                        </TableHead>
                        <TableBody>
                            {paginatedRows.map((row, index) => (
                                <TableRow
                                    key={row.label}
                                    sx={{
                                        backgroundColor: index % 2 === 0 ? 'action.hover' : 'background.paper',
                                        '&:hover': {
                                            backgroundColor: 'action.selected',
                                        },
                                    }}
                                >
                                    <TableCell
                                        component='th'
                                        scope='row'
                                        sx={{
                                            fontWeight: 500,
                                            color: 'text.secondary',
                                        }}
                                    >
                                        {row.label}
                                    </TableCell>
                                    <TableCell sx={{ fontFamily: 'monospace' }}>{row.value}</TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                    <TablePagination
                        component='div'
                        count={metadataRows.length}
                        page={page}
                        onPageChange={handleChangePage}
                        rowsPerPage={rowsPerPage}
                        rowsPerPageOptions={[12]}
                        sx={{ borderTop: '1px solid', borderColor: 'divider' }}
                    />
                </TableContainer>
            </CardContent>
        </Card>
    );
};

export default MetadataForm;

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
import {
    Table,
    TableBody,
    TableCell,
    TableContainer,
    TableHead,
    TableRow,
    Paper,
    TablePagination,
    Typography,
    IconButton,
    Tooltip,
} from '@mui/material';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import { motion } from 'framer-motion';
import { useSnackbar } from 'notistack';
import useVSTUIStore from '../../services/StateManagement';
import { RTSPTableData } from '../../interfaces/interfaces';
import { copyToClipboard } from '../../utils/misc/utils';

const RTSPTable: React.FC = () => {
    const streams = useVSTUIStore(state => state.streams);
    const [page, setPage] = useState(0);
    const [rowsPerPage, setRowsPerPage] = useState(10);
    const { enqueueSnackbar } = useSnackbar();

    // Transform streams data into RTSPTableData format
    const data: RTSPTableData[] = streams.flatMap(sensorStream =>
        sensorStream.streams.map(stream => ({
            name: stream.name,
            url: stream.url,
            type: stream.isMain ? 'Main Stream' : 'Sub Stream',
            resolution: stream.metadata.resolution,
        }))
    );

    const handleChangePage = (_event: React.MouseEvent | null, newPage: number) => {
        setPage(newPage);
    };

    const handleChangeRowsPerPage = (event: React.ChangeEvent<HTMLInputElement>) => {
        setRowsPerPage(parseInt(event.target.value, 10));
        setPage(0);
    };

    const handleCopyUrl = async (url: string) => {
        try {
            await copyToClipboard(url);
            enqueueSnackbar('URL copied to clipboard', {
                variant: 'success',
                autoHideDuration: 2000,
            });
        } catch (error) {
            console.error('Failed to copy URL:', error);
            enqueueSnackbar('Failed to copy URL. Please try selecting and copying manually.', {
                variant: 'error',
                autoHideDuration: 3000,
            });
        }
    };

    const formatResolution = (resolution: string) => {
        // Handle common resolution formats
        const res = resolution.toLowerCase();
        if (res.includes('4k') || res.includes('uhd')) return '4K (3840×2160)';
        if (res.includes('1080p') || res.includes('full hd')) return '1080p (1920×1080)';
        if (res.includes('720p') || res.includes('hd')) return '720p (1280×720)';
        if (res.includes('480p')) return '480p (854×480)';

        // If it's already in format like "1920x1080", just make it consistent
        if (res.match(/\d+x\d+/)) {
            return res.replace('x', '×').toUpperCase();
        }

        return resolution;
    };

    const getStreamTypeStyle = (type: string) => {
        if (type === 'Main Stream') {
            return {
                color: 'primary.main',
                fontWeight: 600,
                backgroundColor: 'rgba(118, 185, 0, 0.1)', // Using NVIDIA green with opacity
                padding: '4px 8px',
                borderRadius: '4px',
                display: 'inline-block',
            };
        }
        return {
            color: 'text.secondary',
            fontWeight: 500,
            backgroundColor: 'action.hover',
            padding: '4px 8px',
            borderRadius: '4px',
            display: 'inline-block',
        };
    };

    const getResolutionStyle = (resolution: string) => {
        const res = resolution.toLowerCase();
        let color = 'text.secondary';
        let bgColor = 'action.hover';

        if (res.includes('4k') || res.includes('uhd') || res.includes('3840')) {
            color = 'info.main';
            bgColor = 'rgba(2, 136, 209, 0.1)'; // Using theme's info color with opacity
        } else if (res.includes('1080p') || res.includes('full hd') || res.includes('1920')) {
            color = 'success.main';
            bgColor = 'rgba(118, 185, 0, 0.1)'; // Using NVIDIA green with opacity
        } else if (res.includes('720p') || res.includes('hd') || res.includes('1280')) {
            color = 'info.main';
            bgColor = 'rgba(2, 136, 209, 0.1)'; // Using theme's info color with opacity
        }

        return {
            color,
            fontWeight: 500,
            backgroundColor: bgColor,
            padding: '4px 8px',
            borderRadius: '4px',
            display: 'inline-block',
        };
    };

    const getNameStyle = () => {
        return {
            color: 'text.primary',
            fontWeight: 500,
            backgroundColor: 'action.hover',
            padding: '4px 8px',
            borderRadius: '4px',
            display: 'inline-block',
        };
    };

    const getUrlStyle = () => {
        return {
            color: 'primary.main',
            fontWeight: 500,
            backgroundColor: 'rgba(118, 185, 0, 0.1)', // Using NVIDIA green with opacity
            padding: '4px 8px',
            borderRadius: '4px',
            display: 'inline-block',
            cursor: 'pointer',
            '&:hover': {
                backgroundColor: 'primary.main',
                color: 'primary.contrastText',
            },
        };
    };

    return (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 1 }}>
            <TableContainer component={Paper} style={{ overflowX: 'auto' }}>
                <Table>
                    <TableHead>
                        <TableRow>
                            <TableCell sx={{ cursor: 'default' }}>
                                <Typography variant='button'>
                                    <b>Name</b>
                                </Typography>
                            </TableCell>
                            <TableCell sx={{ cursor: 'default' }}>
                                <Typography variant='button'>
                                    <b>RTSP URL</b>
                                </Typography>
                            </TableCell>
                            <TableCell sx={{ cursor: 'default' }}>
                                <Typography variant='button'>
                                    <b>Type</b>
                                </Typography>
                            </TableCell>
                            <TableCell sx={{ cursor: 'default' }}>
                                <Typography variant='button'>
                                    <b>Resolution</b>
                                </Typography>
                            </TableCell>
                        </TableRow>
                    </TableHead>
                    <TableBody>
                        {data.slice(page * rowsPerPage, page * rowsPerPage + rowsPerPage).map((row, index) => (
                            <TableRow key={index}>
                                <TableCell>
                                    <Typography variant='body2' sx={getNameStyle()}>
                                        {row.name}
                                    </Typography>
                                </TableCell>
                                <TableCell>
                                    <div
                                        style={{
                                            display: 'flex',
                                            alignItems: 'center',
                                            gap: '8px',
                                        }}
                                    >
                                        <Typography variant='body2' sx={getUrlStyle()} onClick={() => handleCopyUrl(row.url)}>
                                            {row.url}
                                        </Typography>
                                        <Tooltip title='Copy URL'>
                                            <IconButton
                                                size='small'
                                                onClick={() => handleCopyUrl(row.url)}
                                                sx={{
                                                    '&:hover': {
                                                        backgroundColor: 'rgba(0, 0, 0, 0.04)',
                                                    },
                                                }}
                                            >
                                                <ContentCopyIcon fontSize='small' />
                                            </IconButton>
                                        </Tooltip>
                                    </div>
                                </TableCell>
                                <TableCell>
                                    <Typography variant='body2' sx={getStreamTypeStyle(row.type)}>
                                        {row.type}
                                    </Typography>
                                </TableCell>
                                <TableCell>
                                    <Typography variant='body2' sx={getResolutionStyle(row.resolution)}>
                                        {formatResolution(row.resolution)}
                                    </Typography>
                                </TableCell>
                            </TableRow>
                        ))}
                    </TableBody>
                </Table>
                <TablePagination
                    rowsPerPageOptions={[10, 25, 50]}
                    component='div'
                    count={data.length}
                    rowsPerPage={rowsPerPage}
                    page={page}
                    onPageChange={handleChangePage}
                    onRowsPerPageChange={handleChangeRowsPerPage}
                />
            </TableContainer>
        </motion.div>
    );
};

export default RTSPTable;

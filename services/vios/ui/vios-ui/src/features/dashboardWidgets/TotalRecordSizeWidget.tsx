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
import { isNil } from 'lodash';
import { Box, Card, Typography, alpha, styled } from '@mui/material';
import StorageIcon from '@mui/icons-material/Storage';
import { motion } from 'framer-motion';
import useVSTUIStore from '../../services/StateManagement';

const StyledCard = styled(Card)(({ theme }) => ({
    padding: theme.spacing(4),
    boxShadow: `0 8px 24px ${alpha(theme.palette.primary.main, 0.15)}`,
    textAlign: 'center',
    color: theme.palette.text.primary,
    backgroundColor: alpha(theme.palette.primary.light, 0.9),
    borderRadius: theme.spacing(2),
    transition: 'all 0.3s ease-in-out',
    height: '220px',
    width: '100%',
    maxWidth: '400px',
    margin: '0 auto',
    position: 'relative',
    overflow: 'hidden',
    '&::before': {
        content: '""',
        position: 'absolute',
        top: 0,
        left: 0,
        right: 0,
        height: '4px',
        background: `linear-gradient(90deg, 
            ${alpha(theme.palette.primary.main, 0.8)} 0%, 
            ${alpha(theme.palette.primary.light, 0.8)} 100%)`,
    },
    '&:hover': {
        transform: 'translateY(-4px)',
        boxShadow: `0 12px 28px ${alpha(theme.palette.primary.main, 0.25)}`,
    },
}));

const TotalRecordSize: React.FC = () => {
    const [recordSize, setRecordSize] = useState<{ value: number; unit: string }>({ value: 0, unit: 'MB' });
    const [diskCapacity, setDiskCapacity] = useState<{ value: number; unit: string }>({ value: 0, unit: 'MB' });
    const [diskUsed, setDiskUsed] = useState<{ value: number; unit: string }>({ value: 0, unit: 'MB' });
    const [percentage, setPercentage] = useState<number>(0);
    const storageSizes = useVSTUIStore(state => state.storageSizes);

    useEffect(() => {
        const formatStorageSize = (sizeInMb: number): { value: number; unit: string } => {
            if (sizeInMb >= 1024 * 1024) {
                // >= 1TB
                return {
                    value: Number((sizeInMb / (1024 * 1024)).toFixed(2)),
                    unit: 'TB',
                };
            }
            if (sizeInMb >= 1024) {
                // >= 1GB
                return {
                    value: Number((sizeInMb / 1024).toFixed(2)),
                    unit: 'GB',
                };
            }
            return { value: Number(sizeInMb.toFixed(2)), unit: 'MB' };
        };

        if (!isNil(storageSizes) && !isNil(storageSizes.total)) {
            const recordSizeMb = storageSizes.total.sizeInMegabytes || 0;
            const availableStorageMb = storageSizes.total.totalAvailableStorageSize || 0;
            const diskCapacityMb = storageSizes.total.totalDiskCapacity || 0;
            const diskUsedMb = diskCapacityMb - availableStorageMb; // Disk usage = capacity - available

            setRecordSize(formatStorageSize(recordSizeMb));
            setDiskCapacity(formatStorageSize(diskCapacityMb));
            setDiskUsed(formatStorageSize(diskUsedMb));

            if (diskCapacityMb > 0) {
                setPercentage(Number(((diskUsedMb / diskCapacityMb) * 100).toFixed(1)));
            } else {
                setPercentage(0);
            }
        }
    }, [storageSizes]);

    const size = 160;
    const strokeWidth = 12;
    const radius = (size - strokeWidth) / 2;
    const circumference = Math.PI * radius; // Half circle circumference
    const offset = circumference - (percentage / 100) * circumference;

    const getProgressColor = (percent: number): string => {
        if (percent < 60) return '#1976d2'; // Blue
        if (percent <= 90) return '#CA6924'; // Yellow/Orange
        return '#d32f2f'; // Red
    };

    return (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 1 }}>
            <StyledCard>
                <Box
                    sx={{
                        display: 'flex',
                        flexDirection: 'column',
                        alignItems: 'center',
                        justifyContent: 'center',
                        height: '100%',
                        pt: 2,
                    }}
                >
                    {/* Semi-Circle Progress */}
                    <Box sx={{ position: 'relative', display: 'inline-flex', mb: 1 }}>
                        <svg width={size} height={size / 2 + 20} style={{ overflow: 'visible' }}>
                            {/* Background Semi-Circle */}
                            <path
                                d={`M ${strokeWidth / 2},${size / 2} A ${radius},${radius} 0 0,1 ${size - strokeWidth / 2},${size / 2}`}
                                fill='none'
                                stroke={alpha('#000', 0.1)}
                                strokeWidth={strokeWidth}
                                strokeLinecap='round'
                            />
                            {/* Progress Semi-Circle */}
                            <path
                                d={`M ${strokeWidth / 2},${size / 2} A ${radius},${radius} 0 0,1 ${size - strokeWidth / 2},${size / 2}`}
                                fill='none'
                                stroke={getProgressColor(percentage)}
                                strokeWidth={strokeWidth}
                                strokeLinecap='round'
                                strokeDasharray={circumference}
                                strokeDashoffset={offset}
                                style={{
                                    transition: 'stroke-dashoffset 0.5s ease',
                                }}
                            />
                        </svg>
                        {/* Center Content */}
                        <Box
                            sx={{
                                position: 'absolute',
                                bottom: -10,
                                left: '50%',
                                transform: 'translateX(-50%)',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                flexDirection: 'column',
                            }}
                        >
                            <StorageIcon sx={{ fontSize: 28, mb: 0.5, color: 'common.black' }} />
                            <Typography variant='caption' color='common.black' sx={{ opacity: 0.7, whiteSpace: 'nowrap' }}>
                                VST Recordings
                            </Typography>
                            <Typography variant='h5' component='div' fontWeight='bold' color='common.black'>
                                {recordSize.value} {recordSize.unit}
                            </Typography>
                        </Box>
                    </Box>

                    {/* Storage Info */}
                    <Typography variant='body2' color='common.black' fontWeight={500} sx={{ opacity: 0.9, mb: 0.5, mt: 1 }}>
                        {diskUsed.value} {diskUsed.unit} / {diskCapacity.value} {diskCapacity.unit}
                    </Typography>
                    <Typography
                        variant='caption'
                        color='common.black'
                        textTransform='uppercase'
                        letterSpacing='0.5px'
                        sx={{ opacity: 0.7 }}
                    >
                        Total Disk Usage ({percentage}%)
                    </Typography>
                </Box>
            </StyledCard>
        </motion.div>
    );
};

export default TotalRecordSize;

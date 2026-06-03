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
import SdCardAlertIcon from '@mui/icons-material/SdCardAlert';
import Widget from '../../components/widget/Widget';
import { motion } from 'framer-motion';
import { Box, Typography } from '@mui/material';
import useVSTUIStore from '../../services/StateManagement';
import { SensorStorageSize } from '../../interfaces/interfaces';

interface SensorInfo {
    name: string;
    size: number;
    unit: string;
}

const DisconnectedSensorsRecordSizeWidget: React.FC = () => {
    const [disconnectedSensors, setDisconnectedSensors] = useState<SensorInfo[]>([]);
    const [totalSize, setTotalSize] = useState<number>(0);
    const [totalUnit, setTotalUnit] = useState<string>('MB');
    const storageSizes = useVSTUIStore(state => state.storageSizes);
    const removedSensors = useVSTUIStore(state => state.removedSensors);

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

    useEffect(() => {
        if (isNil(storageSizes)) {
            setDisconnectedSensors([]);
            setTotalSize(0);
            setTotalUnit('MB');
            return;
        }

        // Get list of removed sensor IDs
        const removedSensorIds = removedSensors.map(sensor => sensor.sensorId);

        // Filter out removed sensors and get their storage data
        const sensorData = Object.entries(storageSizes)
            .filter(([key]) => key !== 'total' && removedSensorIds.includes(key))
            .map(([key, value]) => {
                const storageSize = value as SensorStorageSize;
                const { value: size, unit } = formatStorageSize(storageSize.sizeInMegabytes);
                return {
                    name: key,
                    size,
                    unit,
                };
            });

        setDisconnectedSensors(sensorData);

        // Calculate total size in MB for conversion
        const totalSizeInMb = sensorData.reduce((sum, sensor) => {
            const multiplier = sensor.unit === 'TB' ? 1024 * 1024 : sensor.unit === 'GB' ? 1024 : 1;
            return sum + sensor.size * multiplier;
        }, 0);

        const { value, unit } = formatStorageSize(totalSizeInMb);
        setTotalSize(value);
        setTotalUnit(unit);
    }, [storageSizes, removedSensors]);

    return (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 1 }}>
            <Widget
                title={`${totalUnit} disconnected record size`}
                total={totalSize}
                color={disconnectedSensors.length > 0 ? 'warning' : 'success'}
                icon={<SdCardAlertIcon />}
            >
                <Box sx={{ mt: 1 }}>
                    {disconnectedSensors.map(sensor => (
                        <Typography
                            key={sensor.name}
                            variant='caption'
                            display='block'
                            sx={{
                                color: 'text.secondary',
                                fontSize: '0.75rem',
                                lineHeight: 1.2,
                            }}
                        >
                            {`${sensor.name}: ${sensor.size} ${sensor.unit}`}
                        </Typography>
                    ))}
                </Box>
            </Widget>
        </motion.div>
    );
};

export default DisconnectedSensorsRecordSizeWidget;

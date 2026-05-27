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
import { isNil } from 'lodash';
import TimelineIcon from '@mui/icons-material/Timeline';
import Widget from '../../components/widget/Widget';
import { motion } from 'framer-motion';
import useVSTUIStore from '../../services/StateManagement';

interface TimelineData {
    endTime: string;
    sizeInMegabytes: number;
    startTime: string;
}

interface SensorData {
    sizeInMegabytes: number;
    state: string;
    timelines: TimelineData[];
}

interface StorageResponse {
    [key: string]: SensorData | { remainingStorageDays: number; sizeInMegabytes: number };
}

const RecordingGapsWidget: React.FC = () => {
    const storageSizes = useVSTUIStore(state => state.storageSizes);
    const vstAdaptorType = useVSTUIStore(state => state.vstAdaptorType);

    const countSensorsWithGaps = (data: StorageResponse): number => {
        if (isNil(data)) {
            return 0;
        }

        let count = 0;
        Object.entries(data).forEach(([key, value]) => {
            if (key !== 'total' && 'timelines' in value) {
                // If there's more than one timeline entry, there are gaps
                if (value.timelines.length > 1) {
                    count++;
                }
            }
        });
        return count;
    };

    const sensorsWithGaps = storageSizes ? countSensorsWithGaps(storageSizes) : 0;
    const title = vstAdaptorType === 'streamer' ? 'Media files with gaps' : 'Sensors with recording gaps';

    return (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 1 }}>
            <Widget title={title} total={sensorsWithGaps} color={sensorsWithGaps > 0 ? 'warning' : 'success'} icon={<TimelineIcon />} />
        </motion.div>
    );
};

export default RecordingGapsWidget;

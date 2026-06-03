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
import { isNil, filter } from 'lodash';
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord';
import Widget from '../../components/widget/Widget';
import { motion } from 'framer-motion';
import useVSTUIStore from '../../services/StateManagement';

const RecordingSensorsWidget: React.FC = () => {
    const recordingStatus = useVSTUIStore(state => state.recordingStatus);

    const recordingSensors = React.useMemo(() => {
        if (isNil(recordingStatus)) {
            return 0;
        }
        const sensorArray = Object.values(recordingStatus);
        const validRecordingStatuses = ['schedule', 'user', 'event', 'alwaysOn'];
        const recordingSensors = filter(sensorArray, sensor => validRecordingStatuses.includes(sensor.recording_status));
        return recordingSensors.length;
    }, [recordingStatus]);

    return (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 1 }}>
            <Widget
                title='Recording Sensors'
                total={recordingSensors}
                color={recordingSensors == 0 ? 'warning' : 'success'}
                icon={<FiberManualRecordIcon />}
            />
        </motion.div>
    );
};

export default RecordingSensorsWidget;

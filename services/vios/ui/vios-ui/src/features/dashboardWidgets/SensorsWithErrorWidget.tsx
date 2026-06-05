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
import { filter } from 'lodash';
import WarningIcon from '@mui/icons-material/Warning';
import Widget from '../../components/widget/Widget';
import { motion } from 'framer-motion';
import useVSTUIStore from '../../services/StateManagement';

const SensorsWithErrorWidget: React.FC = () => {
    const [sensorsWithErrors, setSensorsWithErrors] = useState<number>(0);
    const vstAdaptorType = useVSTUIStore(state => state.vstAdaptorType);
    const sensors = useVSTUIStore(state => state.sensorServiceSensors);

    useEffect(() => {
        const countSensorsWithErrors = (): number => {
            return filter(sensors, sensor => sensor.isError).length;
        };
        setSensorsWithErrors(countSensorsWithErrors());
    }, [sensors]);

    const title = vstAdaptorType === 'streamer' ? 'Media files in bad state' : 'Sensors in bad state';

    return (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 1 }}>
            <Widget title={title} total={sensorsWithErrors} color={sensorsWithErrors > 0 ? 'warning' : 'success'} icon={<WarningIcon />} />
        </motion.div>
    );
};

export default SensorsWithErrorWidget;

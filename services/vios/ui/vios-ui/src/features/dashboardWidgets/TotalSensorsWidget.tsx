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
import VideocamIcon from '@mui/icons-material/Videocam';
import Widget from '../../components/widget/Widget';
import { motion } from 'framer-motion';
import useVSTUIStore from '../../services/StateManagement';

const TotalSensorsWidget: React.FC = () => {
    const sensors = useVSTUIStore(state => state.sensorServiceSensors);
    const sensorCount = isNil(sensors) ? 0 : sensors.length;

    return (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 1 }}>
            <Widget title='Total sensors' total={sensorCount} color='primary' icon={<VideocamIcon />} />
        </motion.div>
    );
};

export default TotalSensorsWidget;

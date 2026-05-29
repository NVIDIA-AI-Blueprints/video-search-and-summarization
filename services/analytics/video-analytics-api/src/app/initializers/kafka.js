/*
 * SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

'use strict';

const mdx = require("@nvidia-mdx/web-api-core");
const cache = require('./cache');
const config = cache.get("bootstrap-config");

let kafka = null;

if(config.kafka.brokers!=null && config.kafka.brokers.length!=0){
    let kafkaConfigMap = new Map();

    // Custom log creator for KafkaJS to format logs with timestamp first
    const logCreator = () => ({ namespace, level, label, log }) => {
        const { timestamp, message, ...extra } = log;
        console.log(JSON.stringify({ timestamp, level: label, message, logger: namespace, ...extra }));
    };

    const clientOptions = {
        brokers: config.kafka.brokers,
        logCreator,
        ...(config.kafka.retries == null ? {} : {
            retry: { retries: config.kafka.retries },
        }),
    };

    kafka = new mdx.Utils.Kafka(clientOptions, kafkaConfigMap);
}
module.exports = kafka;

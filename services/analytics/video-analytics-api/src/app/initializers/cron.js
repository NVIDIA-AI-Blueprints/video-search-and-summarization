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

const cache = require('./cache');
const mdx = require("@nvidia-mdx/web-api-core");
const winston = require('winston');
const logger = winston.createLogger({
    format: winston.format.combine(
        winston.format.timestamp(),
        winston.format.printf(({ timestamp, level, message, ...meta }) => {
            return JSON.stringify({ timestamp, level, message, ...meta });
        })
    ),
    transports: [
        new winston.transports.Console()
    ],
    exitOnError: false
});

const DEFAULT_CONFIG_STATUS_TIMEOUT_MS = 15000;

module.exports = {
    startConfigStatusTimeoutCheck: (elastic) => {
        const bootstrapConfig = cache.get("bootstrap-config");
        if(bootstrapConfig == null || bootstrapConfig.server == null || bootstrapConfig.server.configs == null){
            logger.error("[CONFIG STATUS ERROR] Missing bootstrap-config in cache.");
            return;
        }

        const configStatusTimeoutMsRaw = bootstrapConfig.server.configs.get("configStatusTimeoutMs");
        let configStatusTimeoutMs = DEFAULT_CONFIG_STATUS_TIMEOUT_MS;
        if(configStatusTimeoutMsRaw != null){
            const configStatusTimeoutMsString = String(configStatusTimeoutMsRaw).trim();
            configStatusTimeoutMs = Number(configStatusTimeoutMsString);
            if(configStatusTimeoutMsString === "" || !Number.isInteger(configStatusTimeoutMs) || configStatusTimeoutMs < 0){
                logger.error(`[CONFIG STATUS ERROR] Invalid configStatusTimeoutMs=${configStatusTimeoutMsRaw}. Expected a non-negative integer.`);
                process.exit(1);
            }
        }
        cache.set("configStatusTimeoutMs", configStatusTimeoutMs);

        const configStatusTimeoutCheckFrequencyMsRaw = bootstrapConfig.server.configs.get("configStatusTimeoutCheckFrequencyMs");
        let configStatusTimeoutCheckFrequencyMs;
        if(configStatusTimeoutCheckFrequencyMsRaw == null){
            logger.error(`[CONFIG STATUS ERROR] Invalid config timeout settings: configStatusTimeoutMs=${configStatusTimeoutMs}, configStatusTimeoutCheckFrequencyMs=${configStatusTimeoutCheckFrequencyMs}.`);
            return;
        }

        const configStatusTimeoutCheckFrequencyMsString = String(configStatusTimeoutCheckFrequencyMsRaw).trim();
        configStatusTimeoutCheckFrequencyMs = Number(configStatusTimeoutCheckFrequencyMsString);
        if(configStatusTimeoutCheckFrequencyMsString === "" || !Number.isInteger(configStatusTimeoutCheckFrequencyMs) || configStatusTimeoutCheckFrequencyMs <= 0){
            logger.error(`[CONFIG STATUS ERROR] Invalid config timeout settings: configStatusTimeoutMs=${configStatusTimeoutMs}, configStatusTimeoutCheckFrequencyMs=${configStatusTimeoutCheckFrequencyMs}.`);
            return;
        }

        cache.set("configStatusTimeoutCheckFrequencyMs", configStatusTimeoutCheckFrequencyMs);

        let configManagerObject = new mdx.Services.ConfigManager();
        const configStatusTimeoutInterval = setInterval(() => {
            configManagerObject.runConfigStatusTimeoutCheck(elastic, configStatusTimeoutMs);
        }, configStatusTimeoutCheckFrequencyMs);
        configStatusTimeoutInterval.unref?.();
    }
};

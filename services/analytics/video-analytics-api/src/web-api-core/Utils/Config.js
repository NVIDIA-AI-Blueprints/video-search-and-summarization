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

const InvalidInputError = require('../Errors/InvalidInputError');

/** 
 * Class containing Config utilities.
 * @memberof mdxWebApiCore.Utils
 * */

class Config {

    #bootstrapObjectMap = null;

    /** 
     * Constructor is passed a destructured object as param.
     * @param {Object} [input={}] - Input object.
     * @param {?Object} [input.bootstrap=null] - Bootstrap config of web-api
     */
    constructor({ bootstrap=null } = {}) {

        if(bootstrap == null){
            throw (new InvalidInputError("bootstrap is a required param."));
        }

        this.#bootstrapObjectMap = this.#initOrOverrideBootstrap(bootstrap);
    }

    #initOrOverrideBootstrap(bootstrap) {
        let bootstrapObjectMap = null;
        if (this.#bootstrapObjectMap == null) {
            bootstrapObjectMap = {
                server: {
                    port: null,
                    configs: new Map()
                },
                kafka: {
                    brokers: null,
                    retries: null
                },
                elasticsearch: {
                    node: null,
                    indexPrefix: null,
                    rawIndex: null,
                    retries: null
                }
            };
        } else {
            bootstrapObjectMap = this.#bootstrapObjectMap;
        }
        if (bootstrap.hasOwnProperty("server")) {
            if (bootstrap.server.hasOwnProperty("port")) {
                bootstrapObjectMap.server.port = bootstrap.server.port;
            }
            if (bootstrap.server.hasOwnProperty("configs")) {
                for (let config of bootstrap.server.configs) {
                    bootstrapObjectMap.server.configs.set(config.name, config.value);
                }
            }
        }

        if (bootstrap.hasOwnProperty("kafka")) {
            if (bootstrap.kafka.hasOwnProperty("brokers")) {
                bootstrapObjectMap.kafka.brokers = bootstrap.kafka.brokers;
            }
            if (bootstrap.kafka.hasOwnProperty("retries")) {
                const retries = bootstrap.kafka.retries;
                if (retries != null && (!Number.isInteger(retries) || retries < 0)) {
                    throw (new InvalidInputError(`Invalid kafka.retries: must be a non-negative integer, got ${retries}`));
                }
                bootstrapObjectMap.kafka.retries = retries;
            }

            const kafkaBrokers = bootstrapObjectMap.kafka.brokers;
            const hasKafkaBrokers = Array.isArray(kafkaBrokers) ? kafkaBrokers.length > 0 : kafkaBrokers != null;
            if (!hasKafkaBrokers && bootstrapObjectMap.kafka.retries != null && bootstrapObjectMap.kafka.retries !== 0) {
                console.log("[CONFIG] kafka.brokers is not configured; setting kafka.retries to null.");
                bootstrapObjectMap.kafka.retries = null;
            }
        }

        if (bootstrap.hasOwnProperty("elasticsearch")) {
            if (bootstrap.elasticsearch.hasOwnProperty("node")) {
                bootstrapObjectMap.elasticsearch.node = bootstrap.elasticsearch.node;
            }
            if (bootstrap.elasticsearch.hasOwnProperty("indexPrefix")) {
                bootstrapObjectMap.elasticsearch.indexPrefix = bootstrap.elasticsearch.indexPrefix;
            }
            if(bootstrap.elasticsearch.hasOwnProperty("rawIndex")){
                bootstrapObjectMap.elasticsearch.rawIndex = bootstrap.elasticsearch.rawIndex;
            }
            if (bootstrap.elasticsearch.hasOwnProperty("retries")) {
                const retries = bootstrap.elasticsearch.retries;
                if (retries != null && (!Number.isInteger(retries) || retries < 0)) {
                    throw (new InvalidInputError(`Invalid elasticsearch.retries: must be a non-negative integer, got ${retries}`));
                }
                bootstrapObjectMap.elasticsearch.retries = retries;
            }
        }

        return bootstrapObjectMap;
    }

    /**
     * Returns Bootstrap Config Object Map
     * @public
     * @returns {Object} Returns an object containing bootstrap config in maps.
     * @example
     * let ConfigObject = new mdx.Utils.Config({bootstrap:defaultBootstrapConfig});
     * let bootstrapObjectMap = ConfigObject.getBootstrapObjectMap();
     */
    getBootstrapObjectMap() {
        return this.#bootstrapObjectMap;
    }

    /**
     * Overrides current Bootstrap Object Map
     * @public
     * @param {Object} bootstrapConfig - Bootstrap configuration object.
     * @example
     * let ConfigObject = new mdx.Utils.Config({bootstrap:defaultBootstrapConfig});
     * const bootstrapConfig = require(bootstrapConfigPath); // bootstrapConfigPath is the path to file which has the values provided by user.
     * ConfigObject.overrideBootstrapConfig(bootstrapConfig);
     */
    overrideBootstrapConfig(bootstrapConfig) {
        this.#bootstrapObjectMap = this.#initOrOverrideBootstrap(bootstrapConfig);
    }

}

module.exports = Config;

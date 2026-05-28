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

/** 
 * Class which defines Database
 * @memberof mdxWebApiCore.Utils
 * */

class Database {
    #name;
    #client;
    #configs;

    /** 
     * Constructor is passed a destructured object as param.
     * @param {Object} input - Input object.
     * @param {string} input.name
     * @param {Object} input.client - Database client
     * @param {Map} input.configs
     */
    constructor({ name, client, configs} = {}) {
        this.#name = name;
        this.#client = client;
        this.#configs = configs;
    }

    /** 
     * returns the database name.
     * @public
     * @returns {string}
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let databaseName = elastic.getName();
     */
    getName(){
        return this.#name;
    }

    /** 
     * returns the database configs.
     * @public
     * @returns {Map}
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let databaseConfigs = elastic.getConfigs();
     */
    getConfigs(){
        return this.#configs;
    }

    /** 
     * returns the database client.
     * @public
     * @returns {Object}
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let databaseClient = elastic.getClient();
     */
    getClient(){
        return this.#client;
    }

}

module.exports = Database;

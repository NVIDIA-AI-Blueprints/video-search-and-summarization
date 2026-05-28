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

const Database = require('./Database');
const IndexNotFoundError = require('../Errors/IndexNotFoundError');
const InvalidInputError = require('../Errors/InvalidInputError');
const InternalServerError = require('../Errors/InternalServerError');
const {Client} = require('@elastic/elasticsearch');
const { errors: elasticErrors } = require('@elastic/elasticsearch');

/** 
 * Class containing Elasticsearch Utils
 * @memberof mdxWebApiCore.Utils
 * @extends Database
 * */

class Elasticsearch extends Database{

    /**
     * Constructor
     * @param {Object} connectionObject
     * @param {Map} configs
     */
    constructor(connectionObject,configs){
        let esClient = new Client(connectionObject);
        super({name: "Elasticsearch", client: esClient, configs});
    }

    static #indices = this.#initIndices();

    static #initIndices() {
        let indices = new Map();
        indices.set("calibration", "calibration");
        indices.set("calibrationAudit", "calibration-audit");
        indices.set("behavior", "behavior-*");
        indices.set("clusterLabels", "cluster-labels");
        indices.set("alerts", "alerts-*");
        indices.set("vlmAlerts", "vlm-alerts-*");
        indices.set("incidents", "incidents-*");
        indices.set("vlmIncidents", "vlm-incidents-*");
        indices.set("usdAssets", "usd-assets");
        indices.set("roadNetwork", "road-network");
        indices.set("events", "events-*");
        indices.set("bev", "bev-*");
        indices.set("frames", "frames-*");
        indices.set("mtmc", "mtmc-*");
        indices.set("rtls", "rtls-*");
        indices.set("spaceUtilization", "space-utilization-*");
        indices.set("amrLocations", "amr-locations-*");
        indices.set("amrEvents", "amr-events-*");
        indices.set("occupancyReset", "occupancy-reset");
        indices.set("calibrationImages", "calibration-images");
        indices.set("configs", "configs");
        indices.set("configsAudit", "configs-audit");
        indices.set("sensorLookup", "sensor-lookup");
        return indices;
    }

    /**
      * return elasticsearch errors
      * @public
      * @static
      * @returns {Object} Elasticsearch error is returned
      * @example
      * const mdx = require("@nvidia-mdx/web-api-core");
      * let elasticErrors = mdx.Utils.Elasticsearch.getElasticErrors();
     */
    static getElasticErrors(){
        return elasticErrors;
    }

    /**
     * Used to format elasticsearch result object
     * @public
     * @static
     * @param {Object} esResult - Elasticsearch result object.
     * @returns {Array<Object>} Array of documents obtained by searching Elasticsearch
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * let resultList = mdx.Utils.Elasticsearch.searchResultFormatter(esResult);
     */
    static searchResultFormatter(esResult) {
        let resultList = esResult.hits.hits;
        resultList = resultList.map(result => result["_source"]);
        return resultList;
    }

    /**
     * Returns index.
     * @public
     * @static
     * @param {string} indexType - Index type used to retrieve the configured index.
     * @returns {string|undefined} Returns index
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * let indexType = "behavior";
     * let index = mdx.Utils.Elasticsearch.getIndex(indexType);
     */
    static getIndex(key) {
        if (!this.#indices.has(key)) {
            throw new InvalidInputError(`Invalid index key: "${key}". Valid keys are: ${Array.from(Elasticsearch.#indices.keys()).join(', ')}`);
        }
        return Elasticsearch.#indices.get(key);
    }

    /**
     * returns Elasticsearch query result
     * @public
     * @static
     * @async
     * @param {Object} client - Elasticsearch client object.
     * @param {Object} queryObject - queryObject
     * @param {boolean} [indexAbsentErr=true] - Throw an error when index doesn't exist
     * @returns {Promise<{body:?Object,indexAbsent:boolean}>} Elasticsearch result object
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let result = await mdx.Utils.Elasticsearch.getSearchResults(elastic.getClient(),queryObject,false);
     */
    static async getSearchResults(client, queryObject, indexAbsentErr = true) {
        let indexExist = await client.indices.exists({ index: queryObject.index, allow_no_indices: false });
        if (indexExist) {
            let result = await client.search(queryObject);
            return { body: result, indexAbsent: false };
        } else {
            if (!indexAbsentErr) {
                return { body: null, indexAbsent: true };
            } else {
                let errorMessage = `Index: ${queryObject.index} doesn't exist.`;
                let error = new IndexNotFoundError(errorMessage);
                throw (error);
            }
        }
    }

    /**
     * Executes a scrolling Elasticsearch search and returns all hit sources.
     * @public
     * @static
     * @async
     * @param {Object} client - Elasticsearch client.
     * @param {Object} queryObject - Elasticsearch search request object.
     * @param {boolean} [indexAbsentErr=true] - Whether to throw when the target index does not exist.
     * @returns {Promise<Object>} Aggregated hit sources and index-presence metadata.
     */
    static async getScrollSearchResults(client, queryObject, indexAbsentErr = true) {
        let indexExist = await client.indices.exists({ index: queryObject.index, allow_no_indices: false });
        if (indexExist) {
            queryObject.scroll = "1m";
            let resultList = new Array();
            let searchResult = await client.search(queryObject);
            let scrollId = searchResult._scroll_id;
            let formattedResult= this.searchResultFormatter(searchResult);
            resultList.push(...formattedResult);
            while(formattedResult.length>0){
                const scrollResult = await client.scroll({
                    scroll_id: scrollId,
                    scroll: '1m'
                });
                scrollId = scrollResult._scroll_id;
                formattedResult = this.searchResultFormatter(scrollResult);
                resultList.push(...formattedResult);
            }
            await client.clearScroll({ scroll_id: scrollId });
            return { hitSources: resultList, indexAbsent: false };
        } else {
            if (!indexAbsentErr) {
                return { hitSources: null, indexAbsent: true };
            } else {
                let errorMessage = `Index: ${queryObject.index} doesn't exist.`;
                let error = new IndexNotFoundError(errorMessage);
                throw (error);
            }
        }
    }

    /**
     * returns Elasticsearch doc count result
     * @public
     * @static
     * @async
     * @param {Object} client - Elasticsearch client object.
     * @param {Object} queryObject - queryObject
     * @param {boolean} [indexAbsentErr=true] - Throw an error when index doesn't exist
     * @returns {Promise<{count:number,indexAbsent:boolean}>} Elasticsearch result object
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let result = mdx.Utils.Elasticsearch.getDocCount(elastic.getClient(),queryObject,false);
     */
    static async getDocCount(client, queryObject, indexAbsentErr = true) {
        let indexExist = await client.indices.exists({ index: queryObject.index, allow_no_indices: false });
        if (indexExist) {
            let result = await client.count(queryObject);
            return { count: result.count, indexAbsent: false };
        } else {
            if (!indexAbsentErr) {
                return { count: 0, indexAbsent: true };
            } else {
                let errorMessage = `Index: ${queryObject.index} doesn't exist.`;
                let error = new IndexNotFoundError(errorMessage);
                throw (error);
            }
        }
    }

    /**
     * Checks if an Elasticsearch ingest pipeline exists
     * @public
     * @static
     * @async
     * @param {Object} client - Elasticsearch client
     * @param {string} pipelineId - The ID of the pipeline to check
     * @throws {InternalServerError} Throws an error if the pipeline doesn't exist
     * @returns {Promise<boolean>} Returns true if pipeline exists
     * @example
     * const mdx = require('@nvidia-mdx/web-api-core');
     * const elastic = new mdx.Utils.Elasticsearch({node: 'elasticsearch-url'},databaseConfigMap);
     * await mdx.Utils.Elasticsearch.checkIngestPipelineExists(elastic.getClient(), 'insertion-timestamp-pipeline');
     */
    static async checkIngestPipelineExists(client, pipelineId) {
        try {
            await client.ingest.getPipeline({ id: pipelineId });
            return true;
        } catch (error) {
            let errorMessage = "";
            if (error instanceof elasticErrors.ResponseError && error.statusCode === 404) {
                errorMessage = `Ingest pipeline '${pipelineId}' does not exist in Elasticsearch.`;
            }
            else {
                errorMessage = `Error checking ingest pipeline '${pipelineId}': ${error.toString()}`;
            }
            throw new InternalServerError(errorMessage);
        }
    }
}

module.exports = Elasticsearch;

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

const deepcopy = require("deepcopy");
const Database = require("../Utils/Database");
const filterTemplate = require("../queryTemplates/filter.json");
const randomlySampledClustersTemplate = require("../queryTemplates/randomlySampledClusters.json");
const Elasticsearch = require("../Utils/Elasticsearch");
const Validator = require("../Utils/Validator");
const InternalServerError = require('../Errors/InternalServerError');
const InvalidInputError = require('../Errors/InvalidInputError');
const BadRequestError = require('../Errors/BadRequestError');

/** 
 * Class which defines Clustering
 * @memberof mdxWebApiCore.Services
 * */

class Clustering {

    static #defaultMinBehaviorDistance = 30;
    static #defaultMaxClusterSampleSize = 100;

    async #getLatestModelVersionFromEs(elasticDb,{sensorId, fromTimestamp,toTimestamp}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("behavior")}`;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ range: { timestamp: { lte: toTimestamp } } });
        queryBody.query.bool.must.push({ range: { end: { gte: fromTimestamp } } });
        queryBody.query.bool.must.push({ term: { "sensor.id.keyword": sensorId } });
        queryBody.query.bool.must.push({ exists: { field: "info.cluster.index" } });
        queryBody.query.bool.must_not = [{ term: { "info.cluster.index.keyword": "-1" } }];
        let queryObject = {
            index,
            body: queryBody,
            sort: 'end:desc',
            size: 1,
            _source_includes: ["info.cluster.modelVersion"]
        };
        let results = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return results;
    }

    async #getLatestModelVersion(documentDb,input){
        let modelVersion = null;
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let results = await this.#getLatestModelVersionFromEs(documentDb,input);
                if (!results.indexAbsent) {
                    results = Elasticsearch.searchResultFormatter(results.body);
                    if (results.length > 0) {
                        modelVersion = results[0].info["cluster.modelVersion"];
                    }
                }
                return modelVersion;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #getClusterLabelsFromEs(elasticDb,{sensorId,modelVersion,clusterIndex}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("clusterLabels")}`;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ term: { "sensorId.keyword": sensorId } });
        queryBody.query.bool.must.push({ term: { "modelVersion.keyword": modelVersion } });
        if (clusterIndex != null) {
            queryBody.query.bool.must.push({ term: { "clusterIndex.keyword": clusterIndex } });
        }
        queryBody.aggs = {
            clusterIndices: {
                terms: {
                    field: "clusterIndex.keyword",
                    size: 1000
                },
                aggs: {
                    label: {
                        top_hits: {
                            size: 1,
                            sort: [
                                {
                                    "timestamp": {
                                        order: "desc"
                                    }
                                }
                            ],
                            _source: {
                                includes: ["label"]
                            }
                        }
                    }
                }
            }
        };
        let queryObject = {
            index,
            body: queryBody,
            size: 0
        };
        let results = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return results;
    }

    /** 
     * returns a map containing clusterIndex and the latest cluster label associated to it.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.sensorId - Sensor ID used to query cluster labels.
     * @param {string} input.modelVersion - Model version used to query cluster labels.
     * @param {?string} [input.clusterIndex=null] - Cluster index used to query a specific cluster label.
     * @returns {Promise<Map<string,string>>} A map containing clusterIndex and the latest cluster label associated to it is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorId: "abc", modelVersion: "2"};
     * let clusteringObject = new mdx.Services.Clustering();
     * let labelMap = await clusteringObject.getClusterLabels(elastic,input);
     */
    async getClusterLabels(documentDb,input){
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                sensorId:{
                    type: "string",
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "sensorId should have atleast 1 character.",
                        maxLength: "sensorId should have atmost 10000 characters."
                    }
                },
                modelVersion: {
                    type: "string",
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "modelVersion should have atleast 1 character.",
                        maxLength: "modelVersion should have atmost 10000 characters."
                    }
                },
                clusterIndex:{
                    type: ["string", "null"],
                    default: null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "clusterIndex should have atleast 1 character.",
                        maxLength: "clusterIndex should have atmost 10000 characters."
                    }
                }
            },
            required:["sensorId", "modelVersion"],
            errorMessage:{
                required: "Input should have required properties 'sensorId' and 'modelVersion'.",
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema, false);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        let labelMap = new Map();
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let results = await this.#getClusterLabelsFromEs(documentDb,input);
                if (!results.indexAbsent) {
                    for (let clusterObject of results.body.aggregations.clusterIndices.buckets) {
                        let clusterIndex = clusterObject.key;
                        let latestLabel = Elasticsearch.searchResultFormatter(clusterObject.label);
                        latestLabel = latestLabel[0].label;
                        labelMap.set(clusterIndex, latestLabel);
                    }
                }
                return labelMap;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #getSampledBehaviorClustersFromEs(elasticDb, {sensorId,fromTimestamp,toTimestamp, modelVersion,maxClusterSampleSize,clusterIndex,minBehaviorDistance}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("behavior")}`;
        let queryBody = deepcopy(randomlySampledClustersTemplate);
        queryBody.query.function_score.query.bool.must.push({ exists: { field: "info.cluster.index" } });
        queryBody.query.function_score.query.bool.must.push({ term: { "sensor.id.keyword": sensorId } });
        queryBody.query.function_score.query.bool.must.push({ range: { timestamp: { lte: toTimestamp } } });
        queryBody.query.function_score.query.bool.must.push({ range: { end: { gte: fromTimestamp } } });
        queryBody.query.function_score.query.bool.must.push({ term: { "info.cluster.modelVersion.keyword": modelVersion } });
        queryBody.query.function_score.query.bool.must.push({ range: { distance: { gte: minBehaviorDistance } } });
        if (clusterIndex != null) {
            queryBody.query.function_score.query.bool.must.push({ term: { "info.cluster.index.keyword": clusterIndex } });
        } else {
            queryBody.query.function_score.query.bool.must_not = [{ term: { "info.cluster.index.keyword": "-1" } }];
        }
        queryBody.aggs.clusterIndices.aggs.randomBehaviorSamples.top_hits.size=maxClusterSampleSize;
        queryBody.aggs.clusterIndices.aggs.randomBehaviorSamples.top_hits._source.includes = [
            'Id', 'id', 'timestamp', 'end', 'locations', 'smoothLocations', 'timeInterval', 'length', 'speedOverTime',
            'sensor', 'place.name', 'object', 'edges', 'info.cluster.index', 'info.cluster.modelVersion', 'direction',
            'speed', 'distance', 'bearing', 'videoPath'
        ];
        let queryObject = {
            index,
            body: queryBody,
            size: 0
        };
        let results = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return results;
    }

    /** 
     * returns sampled behaviors for each cluster.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.sensorId - Sensor ID used to filter sampled behavior clusters.
     * @param {string} input.fromTimestamp - fromTimestamp for the query in ISO 8601 format.
     * @param {string} input.toTimestamp - toTimestamp for the query in ISO 8601 format.
     * @param {?string} [input.clusterIndex=null] - Cluster index used to filter sampled behavior clusters.
     * @param {number} [input.maxClusterSampleSize=100] - Maximum number of sampled behaviors returned for each cluster.
     * @param {number} [input.minBehaviorDistance=30] - Minimum behavior distance used to filter sampled behavior clusters.
     * @returns {Promise<Object>} An object containing sampled behaviors for each cluster is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorId: "abc", fromTimestamp: "2023-01-12T11:20:10.000Z", toTimestamp: "2023-01-12T14:20:10.000Z"};
     * let clusteringObject = new mdx.Services.Clustering();
     * let clusters = await clusteringObject.getSampledBehaviorClusters(elastic,input);
     */
    async getSampledBehaviorClusters(documentDb, input){
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                sensorId:{
                    type: "string",
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "sensorId should have atleast 1 character.",
                        maxLength: "sensorId should have atmost 10000 characters."
                    }
                },
                fromTimestamp: {
                    type: "string"
                },
                toTimestamp: {
                    type: "string"
                },
                clusterIndex:{
                    type: ["string", "null"],
                    default: null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "clusterIndex should have atleast 1 character.",
                        maxLength: "clusterIndex should have atmost 10000 characters."
                    }
                },
                maxClusterSampleSize:{
                    type: "integer",
                    minimum: 1,
                    maximum: 100,
                    default: Clustering.#defaultMaxClusterSampleSize,
                    errorMessage: {
                        minimum: "maxClusterSampleSize can have a minimum value of 1.",
                        maximum: "maxClusterSampleSize can have a maximum value of 100."
                    }
                },
                minBehaviorDistance:{
                    type: "number",
                    minimum: 0,
                    default: Clustering.#defaultMinBehaviorDistance,
                    errorMessage: {
                        minimum: "minBehaviorDistance can have a minimum value of 0.",
                    }
                }
            },
            required:["sensorId", "fromTimestamp", "toTimestamp"],
            errorMessage:{
                required: "Input should have required properties 'sensorId', 'fromTimestamp' and 'toTimestamp'.",
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        let timeRangeValidationResult = Validator.isValidTimeRange(input.fromTimestamp, input.toTimestamp);
        if (!timeRangeValidationResult.valid) {
            throw (new InvalidInputError(timeRangeValidationResult.reason));
        }
        if("maxClusterSampleSize" in input && !Number.isFinite(input.maxClusterSampleSize)) {
            throw (new InvalidInputError("maxClusterSampleSize is not a finite integer."));
        }
        if ("minBehaviorDistance" in input && !Number.isFinite(input.minBehaviorDistance)) {
            throw (new InvalidInputError("minBehaviorDistance is not a finite number."));
        }
        let clusters = new Array();
        let modelVersion = await this.#getLatestModelVersion(documentDb,input);
        if(modelVersion == null){
            return {clusters};
        }
        input.modelVersion = modelVersion;
        let labelMap = await this.getClusterLabels(documentDb,{
            sensorId:input.sensorId,
            modelVersion:input.modelVersion,
            clusterIndex: input.clusterIndex
        });
        switch(documentDb.getName()){
            case "Elasticsearch": {
                let behaviorClusterResults = await this.#getSampledBehaviorClustersFromEs(documentDb, input);
                if (!behaviorClusterResults.indexAbsent) {
                    for (let clusterObject of behaviorClusterResults.body.aggregations.clusterIndices.buckets) {
                        let clusterIndex = clusterObject.key;
                        let count = clusterObject.doc_count;
                        let sampledBehaviors = Elasticsearch.searchResultFormatter(clusterObject.randomBehaviorSamples);
                        let label = (labelMap.has(clusterIndex) ? labelMap.get(clusterIndex) : null);
                        clusters.push({
                            clusterIndex,
                            modelVersion,
                            label,
                            count,
                            sampledBehaviors
                        });
                    }
                }
                return {clusters};
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #checkIsValidClusterInEs(elasticDb,{sensorId,modelVersion,clusterIndex}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("behavior")}`;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ exists: { field: "info.cluster.index" } });
        queryBody.query.bool.must.push({ term: { "sensor.id.keyword": sensorId } });
        queryBody.query.bool.must.push({ term: { "info.cluster.modelVersion.keyword": modelVersion } });
        queryBody.query.bool.must.push({ term: { "info.cluster.index.keyword": clusterIndex } });
        let queryObject = {
            index,
            body: queryBody,
            size: 1,
            _source_includes: ["sensor.id", "info.cluster.modelVersion", "info.cluster.index"]
        };
        let results = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return results;
    }

    /** 
     * returns if a cluster is valid.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.sensorId - Sensor ID associated with the cluster.
     * @param {string} input.modelVersion - Model version associated with the cluster.
     * @param {string} input.clusterIndex - Cluster index to validate.
     * @returns {Promise<boolean>} Validity of the cluster is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorId: "abc", modelVersion: "2", clusterIndex: "1"};
     * let clusteringObject = new mdx.Services.Clustering();
     * let validCluster = await clusteringObject.isValidCluster(elastic,input);
     */
    async isValidCluster(documentDb, input){
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                sensorId:{
                    type: "string",
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "sensorId should have atleast 1 character.",
                        maxLength: "sensorId should have atmost 10000 characters."
                    }
                },
                modelVersion: {
                    type: "string",
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "modelVersion should have atleast 1 character.",
                        maxLength: "modelVersion should have atmost 10000 characters."
                    }
                },
                clusterIndex: {
                    type: "string",
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "clusterIndex should have atleast 1 character.",
                        maxLength: "clusterIndex should have atmost 10000 characters."
                    }
                }
            },
            required:["sensorId", "modelVersion", "clusterIndex"],
            errorMessage:{
                required: "Input should have required properties 'sensorId', 'modelVersion' and 'clusterIndex'.",
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema, false);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        let validCluster = false;
        switch(documentDb.getName()){
            case "Elasticsearch": {
                let results = await this.#checkIsValidClusterInEs(documentDb,input);
                if (!results.indexAbsent) {
                    results = Elasticsearch.searchResultFormatter(results.body);
                    if (results.length > 0) {
                        validCluster = true;
                    }
                }
                return validCluster;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    /** 
     * returns if a cluster label already exists.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.sensorId - Sensor ID associated with the cluster label.
     * @param {string} input.modelVersion - Model version associated with the cluster label.
     * @param {string} input.label - Cluster label to check.
     * @returns {Promise<boolean>} A boolean is returned which signifies whether the cluster label already exists
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorId: "abc", modelVersion: "2", label: "left"};
     * let clusteringObject = new mdx.Services.Clustering();
     * let clusterLabelExists = await clusteringObject.doesClusterLabelExist(elastic,input);
     */
    async doesClusterLabelExist(documentDb, input){
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                sensorId:{
                    type: "string",
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "sensorId should have atleast 1 character.",
                        maxLength: "sensorId should have atmost 10000 characters."
                    }
                },
                modelVersion: {
                    type: "string",
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "modelVersion should have atleast 1 character.",
                        maxLength: "modelVersion should have atmost 10000 characters."
                    }
                },
                label:{
                    type: "string",
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "label should have atleast 1 character.",
                        maxLength: "label should have atmost 10000 characters."
                    }
                }
            },
            required:["sensorId", "modelVersion", "label"],
            errorMessage:{
                required: "Input should have required properties 'sensorId', 'modelVersion' and 'label'.",
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema, false);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        let formattedLabel = input.label.trim().replace(/\s\s+/g, " ");
        formattedLabel = formattedLabel.replace(/\s/g, "-").toLowerCase();
        let clusterLabelExists = false;
        let labelMap = await this.getClusterLabels(documentDb,{sensorId:input.sensorId,modelVersion:input.modelVersion});
        for (const [clusterIndex, clusterLabel] of labelMap) {
            if (clusterLabel === formattedLabel) {
                clusterLabelExists = true;
                break;
            }
        }
        return clusterLabelExists;
    }

    async #insertClusterLabelInEs(elasticDb,{sensorId,modelVersion,clusterIndex,label}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("clusterLabels")}`;
        const esClient = elasticDb.getClient();
        const ingestPipelineId = "insertion-timestamp-pipeline";
        
        await Elasticsearch.checkIngestPipelineExists(esClient, ingestPipelineId);
        
        const targetTimestampField = "timestamp";
        
        let queryObject = {
            index,
            pipeline: ingestPipelineId,
            body: {
                sensorId,
                modelVersion,
                clusterIndex,
                label,
                targetFieldName: targetTimestampField
            }
        };
        let result = await esClient.index(queryObject);
        return result;
    }

    /** 
     * returns a success message if the cluster label was inserted successfully.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.sensorId - Sensor ID associated with the cluster.
     * @param {string} input.modelVersion - Model version associated with the cluster.
     * @param {string} input.clusterIndex - Cluster index to label.
     * @param {string} input.label - Cluster label to add.
     * @returns {Promise<Object>} A success message is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorId: "abc", modelVersion: "2", clusterIndex: "1", label: "left"};
     * let clusteringObject = new mdx.Services.Clustering();
     * let result = await clusteringObject.addClusterLabel(elastic,input);
     */
    async addClusterLabel(documentDb, input){
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                sensorId:{
                    type: "string",
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "sensorId should have atleast 1 character.",
                        maxLength: "sensorId should have atmost 10000 characters."
                    }
                },
                modelVersion: {
                    type: "string",
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "modelVersion should have atleast 1 character.",
                        maxLength: "modelVersion should have atmost 10000 characters."
                    }
                },
                clusterIndex: {
                    type: "string",
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "clusterIndex should have atleast 1 character.",
                        maxLength: "clusterIndex should have atmost 10000 characters."
                    }
                },
                label:{
                    type: "string",
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "label should have atleast 1 character.",
                        maxLength: "label should have atmost 10000 characters."
                    }
                }
            },
            required:["sensorId", "modelVersion", "clusterIndex", "label"],
            errorMessage:{
                required: "Input should have required properties 'sensorId', 'modelVersion', 'clusterIndex' and 'label'.",
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema, false);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        if(input.clusterIndex === "-1"){
            throw (new InvalidInputError("Cannot assign label to clusterIndex -1."));
        }
        input.label = input.label.trim().replace(/\s\s+/g, " ");
        input.label = input.label.replace(/\s/g, "-").toLowerCase();
        let [validCluster, clusterLabelExists] = await Promise.all([
            this.isValidCluster(documentDb, {sensorId:input.sensorId, modelVersion:input.modelVersion, clusterIndex:input.clusterIndex}),
            this.doesClusterLabelExist(documentDb, {sensorId:input.sensorId, modelVersion:input.modelVersion, label:input.label})
        ]);
        if (!validCluster) {
            let errorMessage = `clusterIndex: ${input.clusterIndex} belonging to model version: ${input.modelVersion} of sensorId: ${input.sensorId} doesn't exist.`;
            throw (new InvalidInputError(errorMessage));
        }
        if (clusterLabelExists) {
            let errorMessage = `modelVersion: ${input.modelVersion} of sensorId: ${input.sensorId} already has label: ${input.label}.`;
            throw (new InvalidInputError(errorMessage));
        }
        switch(documentDb.getName()){
            case "Elasticsearch": {
                await this.#insertClusterLabelInEs(documentDb,input);
                return ({ success: true });
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    } 
}

module.exports = Clustering;
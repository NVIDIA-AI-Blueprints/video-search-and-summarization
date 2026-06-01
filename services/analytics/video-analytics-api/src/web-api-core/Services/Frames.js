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
const Elasticsearch = require("../Utils/Elasticsearch");
const Validator = require("../Utils/Validator");
const InternalServerError = require('../Errors/InternalServerError');
const InvalidInputError = require('../Errors/InvalidInputError');
const BadRequestError = require('../Errors/BadRequestError');
const Calibration = require("./Calibration");
const Utils = require("../Utils/Utils");
const winston = require('winston');
const logger = winston.createLogger({
    format: winston.format.combine(
        winston.format.timestamp(),
        winston.format.printf(({ timestamp, level, message, ...meta }) => {
            return JSON.stringify({ timestamp, level, message, ...meta });
        })
    ),
    transports: [
        new winston.transports.Console({ level: 'info' })
    ],
    exitOnError: false
});

/** 
 * Class which defines Frames
 * @memberof mdxWebApiCore.Services
 * */

class Frames {

    static #defaultFramesResultSize = 25;
    static #maxFramesResultSize = 1000;
    static #defaultAlertResultSize = 25;
    static #maxAlertResultSize = 1000;
    static #maxHighConfidenceFrameObjectsInResult = 100;
    static #minObjectConfidence = 0.80;

    async #getElasticDocIdObjectDetailsMap(elasticDb, { fromTimestamp, toTimestamp, sensorId, objectId, maxResultSize, minConfidence }) {
        const index = elasticDb.getConfigs().get("rawIndex");
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ term: { "sensorId.keyword": sensorId } });
        queryBody.query.bool.must.push({ range: { timestamp: { gte: fromTimestamp, lte: toTimestamp } } });
        if (objectId != null) {
            queryBody.query.bool.must.push({
                nested: {
                    path: "objects",
                    query: {
                        bool: {
                            must: [
                                { term: { "objects.id.keyword": objectId } }
                            ]
                        }
                    }
                }
            });
        }
        queryBody.aggs = {
            objects: {
                nested: {
                    path: "objects"
                },
                aggs: {
                    objectsFilter: {
                        filter: {
                            bool: {
                                filter: [
                                    {
                                        range: {
                                            "objects.confidence": {
                                                gte: minConfidence
                                            }
                                        }
                                    }
                                ]
                            }
                        },
                        aggs: {
                            objectIds: {
                                terms: {
                                    field: "objects.id.keyword",
                                    size: (objectId != null) ? 1 : maxResultSize
                                },
                                aggs: {
                                    maxConfidenceDetection: {
                                        top_hits: {
                                            sort: [
                                                {
                                                    "objects.confidence": {
                                                        order: "desc"
                                                    }
                                                }
                                            ],
                                            _source: {
                                                includes: ["objects.id", "objects.bbox", "objects.confidence", "objects.embedding.vector"]
                                            },
                                            size: 1
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        if (objectId != null) {
            queryBody.aggs.objects.aggs.objectsFilter.filter.bool.filter.push({
                term: { "objects.id.keyword": objectId }
            });
        }
        let queryObject = { index, body: queryBody, size: 0 };
        let aggregatedResult = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        let docIdObjectDetailsMap = new Map();
        if (!aggregatedResult.indexAbsent) {
            for (let bucket of aggregatedResult.body.aggregations.objects.objectsFilter.objectIds.buckets) {
                let docId = bucket.maxConfidenceDetection.hits.hits[0]["_id"];
                let doc = bucket.maxConfidenceDetection.hits.hits[0]["_source"];
                if (!docIdObjectDetailsMap.has(docId)) {
                    docIdObjectDetailsMap.set(docId,[doc]);
                }else{
                    let objectList =  docIdObjectDetailsMap.get(docId);
                    objectList.push(doc);
                    docIdObjectDetailsMap.set(docId,objectList);
                }
            }
        }
        return docIdObjectDetailsMap;
    }

    async #getElasticDocIdFramesMap(elasticDb, docIds) {
        if (docIds.length == 0) {
            return new Map();
        }
        const index = elasticDb.getConfigs().get("rawIndex");
        let queryObject = {
            index,
            body: {
                query: {
                    ids: {
                        values: docIds
                    }
                }
            },
            size: docIds.length
        };
        let frameResult = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        let docIdFrameMap = new Map();
        if (!frameResult.indexAbsent) {
            for (let hit of frameResult.body.hits.hits) {
                docIdFrameMap.set(hit["_id"], hit["_source"]);
            }
        }
        return docIdFrameMap;
    }

    async #getLatestTimestampOfFrameWithObjectFromES(elasticDb, { sensorId, objectId, frameId }) {
        const index = elasticDb.getConfigs().get("rawIndex");
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ term: { "sensorId.keyword": sensorId } });
        queryBody.query.bool.must.push({ term: { "id.keyword": frameId } });
        queryBody.query.bool.must.push({
            nested: {
                path: "objects",
                query: {
                    bool: {
                        must: [
                            { term: { "objects.id.keyword": objectId } }
                        ]
                    }
                }
            }
        });
        let queryObject = {
            index,
            body: queryBody,
            sort: "timestamp:desc",
            _source_includes: ["timestamp"],
            size: 1
        };
        let timestampOfFrameResult = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        if (!timestampOfFrameResult.indexAbsent) {
            timestampOfFrameResult = Elasticsearch.searchResultFormatter(timestampOfFrameResult.body);
            if (timestampOfFrameResult.length == 0) {
                return null;
            }
            return timestampOfFrameResult[0].timestamp;
        }
    }

    async #getEmbeddingFromEs(elasticDb, { sensorId, objectId, timestamp }) {
        const index = elasticDb.getConfigs().get("rawIndex");
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ term: { "sensorId.keyword": sensorId } });
        queryBody.query.bool.must.push({ term: { "timestamp": timestamp } });
        queryBody.aggs = {
            objects: {
                nested: {
                    path: "objects"
                },
                aggs: {
                    objectsFilter: {
                        filter: {
                            bool: {
                                filter: [
                                    { term: { "objects.id.keyword": objectId } }
                                ]
                            }
                        },
                        aggs: {
                            objectDoc: {
                                top_hits: {
                                    _source: {
                                        includes: ["objects.embedding.vector"]
                                    },
                                    size: 1
                                }
                            }
                        }
                    }
                }
            }
        }
        let queryObject = { index, body: queryBody, size: 0 };
        let embeddingResult = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        let embedding = new Array();
        if (!embeddingResult.indexAbsent) {
            embeddingResult = Elasticsearch.searchResultFormatter(embeddingResult.body.aggregations.objects.objectsFilter.objectDoc);
            if (embeddingResult.length > 0) {
                embedding = (embeddingResult[0].embedding!=null)?embeddingResult[0].embedding.vector:null;
            }
        }
        return embedding;
    }

    /** 
     * returns an object containing an array of detected objects. Only the data point corresponding to the max confidence of each object id will be present in the array.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.sensorId - Sensor ID used to filter detected objects.
     * @param {?string} [input.objectId=null] - objectId and maxResultSize can't occur together.
     * @param {string} input.fromTimestamp - fromTimestamp for the query in ISO 8601 format.
     * @param {string} input.toTimestamp - toTimestamp for the query in ISO 8601 format.
     * @param {number} [input.maxResultSize=100] - Maximum number of detected objects returned. objectId and maxResultSize can't occur together.
     * @param {number} [input.minConfidence=0.80] - Minimum confidence used to filter detected objects.
     * @returns {Promise<Object>} An object containing an array of detected objects is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorId: "abc", fromTimestamp: "2023-01-12T11:20:10.000Z", toTimestamp: "2023-01-12T14:20:10.000Z"};
     * let framesMetadata = new mdx.Services.Frames();
     * let objects = await framesMetadata.getMaxConfidenceDetectionOfObjects(elastic, input);
     */
    async getMaxConfidenceDetectionOfObjects(documentDb, input) {
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                sensorId: {
                    type: "string",
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "sensorId should have atleast 1 character.",
                        maxLength: "sensorId should have atmost 10000 characters."
                    }
                },
                objectId: {
                    type: ["string", "null"],
                    default: null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "objectId should have atleast 1 character.",
                        maxLength: "objectId should have atmost 10000 characters."
                    }
                },
                fromTimestamp: {
                    type: "string"
                },
                toTimestamp: {
                    type: "string"
                },
                maxResultSize: {
                    type: "integer",
                    minimum: 1,
                    maximum: Frames.#maxHighConfidenceFrameObjectsInResult,
                    default: Frames.#maxHighConfidenceFrameObjectsInResult,
                    errorMessage: {
                        type: "maxResultSize is not an integer.",
                        minimum: "maxResultSize can have a minimum value of 1.",
                        maximum: `maxResultSize can have a maximum value of ${Frames.#maxHighConfidenceFrameObjectsInResult}.`
                    }
                },
                minConfidence: {
                    type: "number",
                    minimum: 0,
                    maximum: 1,
                    default: Frames.#minObjectConfidence,
                    errorMessage: {
                        type: "minConfidence is not a number.",
                        minimum: "minConfidence can have a minimum value of 0.",
                        maximum: "minConfidence can have a maximum value of 1."
                    }
                }
            },
            required: ["sensorId", "fromTimestamp", "toTimestamp"],
            not: {
                required: ["objectId", "maxResultSize"]
            },
            errorMessage: {
                required: "Input should have required properties 'sensorId', 'fromTimestamp' and 'toTimestamp'.",
                not: "Input cannot have both 'objectId' and 'maxResultSize'."
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
        if ("maxResultSize" in input && !Number.isFinite(input.maxResultSize)) {
            throw (new InvalidInputError("maxResultSize is not a finite integer."));
        }
        if ("minConfidence" in input && !Number.isFinite(input.minConfidence)) {
            throw (new InvalidInputError("minConfidence is not a finite number."));
        }
        let objects = new Array();
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let docIdObjectDetailsMap = await this.#getElasticDocIdObjectDetailsMap(documentDb, input);
                let docIdFrameMap = await this.#getElasticDocIdFramesMap(documentDb, Array.from(docIdObjectDetailsMap.keys()));
                for (let [docId, frame] of docIdFrameMap.entries()) {
                    let objectsInResult = docIdObjectDetailsMap.get(docId);
                    for (let object of objectsInResult) {
                        objects.push({
                            frameId: frame.id,
                            sensorId: frame.sensorId,
                            timestamp: frame.timestamp,
                            objectId: object.id,
                            bbox: object.bbox,
                            confidence: object.confidence,
                            embedding: (object.embedding!=null)?object.embedding.vector:null
                        });
                    }
                }
                return {objects};
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    /** 
     * returns the timestamp of the latest occurance of objectId in a frameId of a sensor.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.sensorId - Sensor ID used to filter the latest timestamp of frame with object.
     * @param {string} input.objectId - Object ID used to filter the latest timestamp of frame with object.
     * @param {string} input.frameId - Frame ID used to filter the latest timestamp of frame with object.
     * @returns {Promise<?string>} Timestamp of the frame is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorId: "abc", objectId: "120", frameId: "2200"};
     * let framesMetadata = new mdx.Services.Frames();
     * let timestampOfFrame = await framesMetadata.getLatestTimestampOfFrameWithObject(elastic, input);
     */
    async getLatestTimestampOfFrameWithObject(documentDb, input) {
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                sensorId: {
                    type: "string",
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "sensorId should have atleast 1 character.",
                        maxLength: "sensorId should have atmost 10000 characters."
                    }
                },
                objectId: {
                    type: "string",
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "objectId should have atleast 1 character.",
                        maxLength: "objectId should have atmost 10000 characters."
                    }
                },
                frameId: {
                    type: "string",
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "frameId should have atleast 1 character.",
                        maxLength: "frameId should have atmost 10000 characters."
                    }
                }
            },
            required: ["sensorId", "objectId", "frameId"],
            errorMessage:{
                required:"Input should have required properties 'sensorId', 'objectId' and 'frameId'."
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        let timestampOfFrame = null;
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                timestampOfFrame = await this.#getLatestTimestampOfFrameWithObjectFromES(documentDb, input);
                return timestampOfFrame;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    /** 
     * returns embedding of an objectId.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.sensorId - Sensor ID used to filter embedding.
     * @param {string} input.objectId - Object ID used to filter embedding.
     * @param {string} input.timestamp - Timestamp for the query in ISO 8601 format.
     * @returns {Promise<?Array<number>>} Embedding of an objectId is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorId: "abc", objectId: "120", timestamp: "2023-01-12T11:20:10.000Z"};
     * let framesMetadata = new mdx.Services.Frames();
     * let embedding = await framesMetadata.getEmbedding(elastic, input);
     */
    async getEmbedding(documentDb, input) {
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                sensorId: {
                    type: "string",
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "sensorId should have atleast 1 character.",
                        maxLength: "sensorId should have atmost 10000 characters."
                    }
                },
                objectId: {
                    type: "string",
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "objectId should have atleast 1 character.",
                        maxLength: "objectId should have atmost 10000 characters."
                    }
                },
                timestamp: {
                    type: "string"
                }
            },
            required: ["sensorId", "objectId", "timestamp"],
            errorMessage:{
                required: "Input should have required properties 'sensorId', 'objectId' and 'timestamp'."
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        if (!Validator.isValidISOTimestamp(input.timestamp)) {
            throw (new InvalidInputError("Invalid timestamp."));
        }
        let embedding = new Array();
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                embedding = this.#getEmbeddingFromEs(documentDb, input);
                return embedding;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #queryEsProximityClusters(elasticDb, { sensorId, fromTimestamp, toTimestamp }) {
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("frames")}`;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ range: { "timestamp": { lte: toTimestamp, gte: fromTimestamp } } });
        queryBody.query.bool.must.push({ term: { "sensorId.keyword": sensorId } });
        let queryObject = {
            index,
            body: queryBody,
            size: 1,
            sort: "timestamp:desc",
            _source: ["id", "timestamp", "socialDistancing.clusters"]
        };
        let clusters = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return clusters;
    }

    /** 
     * returns details of proximity clusters.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.sensorId - Sensor ID used to filter proximity clusters.
     * @param {string} input.fromTimestamp - fromTimestamp for the query in ISO 8601 format.
     * @param {string} input.toTimestamp - toTimestamp for the query in ISO 8601 format.
     * @returns {Promise<Object>} An object containing details of proximity clusters is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorId: "abc", fromTimestamp: "2023-01-12T11:20:10.000Z", toTimestamp: "2023-01-12T14:20:10.000Z"};
     * let framesMetadata = new mdx.Services.Frames();
     * let proximityClusterResult = await framesMetadata.getProximityClusters(elastic, input);
     */
    async getProximityClusters(documentDb, input) {
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                sensorId: {
                    type: "string",
                    minLength: 1,
                    errorMessage: {
                        minLength: "sensorId should have atleast 1 character."
                    }
                },
                fromTimestamp: {
                    type: "string"
                },
                toTimestamp: {
                    type: "string"
                }
            },
            required: ["sensorId", "fromTimestamp", "toTimestamp"],
            errorMessage:{
                required: "Input should have required properties 'sensorId', 'fromTimestamp' and 'toTimestamp'."
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
        let clusterDetails = { id: null, timestamp: null, clusters: new Array() };
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let results = await this.#queryEsProximityClusters(documentDb, input);
                if (!results.indexAbsent) {
                    results = Elasticsearch.searchResultFormatter(results.body);
                    if (results.length > 0) {
                        clusterDetails.id = results[0].id;
                        clusterDetails.timestamp = results[0].timestamp;
                        clusterDetails.clusters = results[0].socialDistancing.clusters;
                    }
                }
                return clusterDetails;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #getFrameEarlierToInputTimestamp(documentDb, { sensorId, timestamp }) {
        let frameEarlierToInputTimestamp = null;
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                const index = documentDb.getConfigs().get("rawIndex");
                let queryBody = deepcopy(filterTemplate);
                queryBody.query.bool.must.push({ term: { "sensorId.keyword": sensorId } });
                queryBody.query.bool.must.push({ range: { "timestamp": { lte: timestamp } } });
                let queryObject = {
                    index,
                    body: queryBody,
                    size: 1,
                    sort: "timestamp:desc",
                    _source_includes: ["id", "timestamp"]
                };
                let results = await Elasticsearch.getSearchResults(documentDb.getClient(), queryObject, false);
                if (results.indexAbsent) {
                    logger.info("[DATA] Raw index is not present.");
                } else {
                    results = Elasticsearch.searchResultFormatter(results.body);
                    if (results.length == 0) {
                        logger.info(`[DATA] Raw data is not present for sensor: ${sensorId}.`);
                    } else {
                        frameEarlierToInputTimestamp = results[0];
                    }
                }
                return frameEarlierToInputTimestamp;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    /** 
     * returns an object containing pts.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.sensorId - Sensor ID used to query pts.
     * @param {string} [input.timestamp] - Either timestamp or frameId should be present.
     * @param {number} [input.frameId] - Frame ID used to query pts. Either timestamp or frameId should be present.
     * @returns {Promise<Object>} An object containing pts is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorId: "abc", frameId: 2200};
     * let framesMetadata = new mdx.Services.Frames();
     * let pts = await framesMetadata.getPts(elastic, input);
     */
    async getPts(documentDb, input) {
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                sensorId: {
                    type: "string",
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "sensorId should have atleast 1 character.",
                        maxLength: "sensorId should have atmost 10000 characters."
                    }
                },
                timestamp: {
                    type: "string"
                },
                frameId: {
                    type: "integer"
                }
            },
            required: ["sensorId"],
            oneOf: [
                { required: ["timestamp"] },
                { required: ["frameId"] }
            ],
            errorMessage:{
                required:"Input should have required property 'sensorId'.",
                oneOf: "Input should have either 'timestamp' or 'frameId'."
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        if ("timestamp" in input) {
            if (!Validator.isValidISOTimestamp(input.timestamp)) {
                throw (new InvalidInputError("Invalid timestamp."));
            }
        } else {
            if (!Number.isFinite(input.frameId)) {
                throw (new InvalidInputError("frameId is not a finite integer."));
            }
        }
        let calibrationObject = new Calibration();
        let { calibration } = await calibrationObject.getCalibration(documentDb, { sensorId: input.sensorId });
        let sensorCalibration = calibration.sensors[0];
        let fps = null;
        let source = null;
        for (let attribute of sensorCalibration.attributes) {
            if (attribute.name === "fps") {
                if (attribute.value !== "") {
                    fps = parseInt(attribute.value, 10);
                }
            } else if (attribute.name === "source") {
                if (attribute.value !== "") {
                    source = attribute.value;
                }
            }
        }
        if (source == null) {
            throw (new InvalidInputError(`source of the sensor is not defined in calibration. Update the calibration of sensorId: ${input.sensorId}.`));
        } else if (source !== "nvstreamer") {
            throw (new InvalidInputError("Pts can be calculated only for 'nvstreamer' source."));
        }
        if (fps == null) {
            throw (new InvalidInputError(`fps of the sensor is not defined in calibration. Update the calibration of sensorId: ${input.sensorId}.`));
        }
        if ("frameId" in input) {
            return {pts: Math.floor((input.frameId / fps) * 1000)};
        } else {
            let pts = null;
            let frameResult = await this.#getFrameEarlierToInputTimestamp(documentDb, input);
            if (frameResult != null) {
                let dataFrameId = parseInt(frameResult.id, 10);
                let dataPts = Math.floor((dataFrameId / fps) * 1000);
                let tsEqualComparison = Utils.tsCompare(input.timestamp, "==", frameResult.timestamp);
                let tsLesserThanComparison = Utils.tsCompare(input.timestamp, "<", frameResult.timestamp);
                let tsGreaterThanComparison = Utils.tsCompare(input.timestamp, ">", frameResult.timestamp);
                if (tsEqualComparison) {
                    pts = dataPts;
                } else if (tsLesserThanComparison) {
                    pts = dataPts - (new Date(frameResult.timestamp) - new Date(input.timestamp));
                } else if (tsGreaterThanComparison) {
                    pts = dataPts + (new Date(input.timestamp) - new Date(frameResult.timestamp));
                }
            }
            return {pts};
        }
    }

    async #getFramesFromES(elasticDb, { sensorId, frameId, timestamp, fromTimestamp, toTimestamp, maxResultSize, type }) {
        let index;
        switch(type){
            case "raw":
                index = elasticDb.getConfigs().get("rawIndex");
                break;
            case "enhanced":
                index = `${elasticDb.getConfigs().get("indexPrefix")}${Elasticsearch.getIndex("frames")}`;
                break;
            case "bev":
                index = `${elasticDb.getConfigs().get("indexPrefix")}${Elasticsearch.getIndex("bev")}`;
                break;
            default:
                new InternalServerError(`Invalid type: ${type}. Valid values are: 'raw', 'enhanced', 'bev'.`)
        }   
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({term :{"sensorId.keyword":sensorId}});
        if(frameId!=null){
            queryBody.query.bool.must.push({term :{"id.keyword":frameId}});
        }else if(timestamp!=null){
            queryBody.query.bool.must.push({term:{"timestamp":timestamp}});
        }else{
            queryBody.query.bool.must.push({range:{"timestamp":{lte:toTimestamp,gte:fromTimestamp}}});
        }
        let queryObject = { 
            index, 
            body: queryBody,
            sort: "timestamp:desc",
            size: maxResultSize
        }
        if(type === "raw"){
            queryObject["_source_includes"] = ["version", "id", "timestamp", "sensorId", "objects"];
        }else{
            queryObject["_source_excludes"] = ["type"]
        }
        let results = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return results;
    }

    /** 
     * returns an object containing an array of frames.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.sensorId - Sensor ID used to filter frames.
     * @param {string} [input.frameId] - Either frameId or timestamp or (fromTimestamp and toTimestamp) should be present. frameId can't occur together with maxResultSize.
     * @param {string} [input.timestamp] - Either frameId or timestamp or (fromTimestamp and toTimestamp) should be present. timestamp can't occur together with maxResultSize.
     * @param {string} [input.fromTimestamp] - Either frameId or timestamp or (fromTimestamp and toTimestamp) should be present.
     * @param {string} [input.toTimestamp] - Either frameId or timestamp or (fromTimestamp and toTimestamp) should be present.
     * @param {number} [input.maxResultSize=25] - Maximum number of frames returned. maxResultSize can't occur together with either frameId or timestamp.
     * @returns {Promise<Object>} An object containing an array of frames is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorId: "abc", timestamp: "2023-01-12T11:20:10.000Z"};
     * let framesMetadata = new mdx.Services.Frames();
     * let frames = await framesMetadata.getFrames(elastic,input);
     */
    async getFrames(documentDb, input) {
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                sensorId: {
                    type: "string",
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "sensorId should have atleast 1 character.",
                        maxLength: "sensorId should have atmost 10000 characters."
                    }
                },
                frameId:{
                    type: ["string","null"],
                    default: null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "frameId should have atleast 1 character.",
                        maxLength: "frameId should have atmost 10000 characters."
                    }
                },
                timestamp:{
                    type: ["string","null"],
                    default: null,
                },
                fromTimestamp: {
                    type: ["string","null"],
                    default: null,
                },
                toTimestamp: {
                    type: ["string","null"],
                    default: null,
                },
                maxResultSize: {
                    type: "integer",
                    minimum: 1,
                    maximum: Frames.#maxFramesResultSize,
                    default: Frames.#defaultFramesResultSize,
                    errorMessage: {
                        type: "maxResultSize is not an integer.",
                        minimum: "maxResultSize can have a minimum value of 1.",
                        maximum: `maxResultSize can have a maximum value of ${Frames.#maxFramesResultSize}.`
                    }
                }
            },
            required: ["sensorId"],
            oneOf:[
                {
                    required:["fromTimestamp", "toTimestamp"]
                },
                {
                    required:["timestamp"]
                },
                {
                    required:["frameId"]
                }
            ],
            not: {
                anyOf: [
                    { required: ["timestamp","maxResultSize"] },
                    { required: ["frameId","maxResultSize"] }
                ]
            },
            errorMessage:{
                required: "Input should have required property 'sensorId'.",
                oneOf: "Input can either have 'frameId', 'timestamp' or a time range i.e. 'fromTimestamp' and 'toTimestamp'.",
                not: "Input cannot have 'maxResultSize' when it has either 'frameId' or 'timestamp'."
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        if (!Number.isFinite(input.maxResultSize)) {
            throw (new InvalidInputError("maxResultSize is not a finite integer."));
        }
        if(input.timestamp!=null || input.frameId !=null){
            input.maxResultSize = 1;
            if(input.timestamp!=null){
                if(!Validator.isValidISOTimestamp(input.timestamp)){
                    throw (new InvalidInputError("Invalid timestamp."));
                }
            }
        }else{
            let timeRangeValidationResult = Validator.isValidTimeRange(input.fromTimestamp, input.toTimestamp);
            if (!timeRangeValidationResult.valid) {
                throw (new InvalidInputError(timeRangeValidationResult.reason));
            }
        }
        input.type = "raw";
        let frames = new Array();
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let results = await this.#getFramesFromES(documentDb, input);
                if (!results.indexAbsent) {
                    frames = Elasticsearch.searchResultFormatter(results.body);
                }
                return {frames};
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    /** 
     * returns an object containing an array of enhanced frames.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.sensorId - Sensor ID used to filter enhanced frames.
     * @param {string} [input.frameId] - Either frameId or timestamp or (fromTimestamp and toTimestamp) should be present. frameId can't occur together with maxResultSize.
     * @param {string} [input.timestamp] - Either frameId or timestamp or (fromTimestamp and toTimestamp) should be present. timestamp can't occur together with maxResultSize.
     * @param {string} [input.fromTimestamp] - Either frameId or timestamp or (fromTimestamp and toTimestamp) should be present.
     * @param {string} [input.toTimestamp] - Either frameId or timestamp or (fromTimestamp and toTimestamp) should be present.
     * @param {number} [input.maxResultSize=25] - Maximum number of enhanced frames returned. maxResultSize can't occur together with either frameId or timestamp.
     * @returns {Promise<Object>} An object containing an array of enhanced frames is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorId: "abc", timestamp: "2023-01-12T11:20:10.000Z"};
     * let framesMetadata = new mdx.Services.Frames();
     * let enhancedFrames = await framesMetadata.getEnhancedFrames(elastic,input);
     */
    async getEnhancedFrames(documentDb, input) {
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                sensorId: {
                    type: "string",
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "sensorId should have atleast 1 character.",
                        maxLength: "sensorId should have atmost 10000 characters."
                    }
                },
                frameId:{
                    type: ["string","null"],
                    default: null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "frameId should have atleast 1 character.",
                        maxLength: "frameId should have atmost 10000 characters."
                    }
                },
                timestamp:{
                    type: ["string","null"],
                    default: null,
                },
                fromTimestamp: {
                    type: ["string","null"],
                    default: null,
                },
                toTimestamp: {
                    type: ["string","null"],
                    default: null,
                },
                maxResultSize: {
                    type: "integer",
                    minimum: 1,
                    maximum: Frames.#maxFramesResultSize,
                    default: Frames.#defaultFramesResultSize,
                    errorMessage: {
                        type: "maxResultSize is not an integer.",
                        minimum: "maxResultSize can have a minimum value of 1.",
                        maximum: `maxResultSize can have a maximum value of ${Frames.#maxFramesResultSize}.`
                    }
                }
            },
            required: ["sensorId"],
            oneOf:[
                {
                    required:["fromTimestamp", "toTimestamp"]
                },
                {
                    required:["timestamp"]
                },
                {
                    required:["frameId"]
                }
            ],
            not: {
                anyOf: [
                    { required: ["timestamp","maxResultSize"] },
                    { required: ["frameId","maxResultSize"] }
                ]
            },
            errorMessage:{
                required: "Input should have required property 'sensorId'.",
                oneOf: "Input can either have 'frameId', 'timestamp' or a time range i.e. 'fromTimestamp' and 'toTimestamp'.",
                not: "Input cannot have 'maxResultSize' when it has either 'frameId' or 'timestamp'."
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        if (!Number.isFinite(input.maxResultSize)) {
            throw (new InvalidInputError("maxResultSize is not a finite integer."));
        }
        if(input.timestamp!=null || input.frameId !=null){
            input.maxResultSize = 1;
            if(input.timestamp!=null){
                if(!Validator.isValidISOTimestamp(input.timestamp)){
                    throw (new InvalidInputError("Invalid timestamp."));
                }
            }
        }else{
            let timeRangeValidationResult = Validator.isValidTimeRange(input.fromTimestamp, input.toTimestamp);
            if (!timeRangeValidationResult.valid) {
                throw (new InvalidInputError(timeRangeValidationResult.reason));
            }
        }
        input.type = "enhanced";
        let enhancedFrames = new Array();
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let results = await this.#getFramesFromES(documentDb, input);
                if (!results.indexAbsent) {
                    enhancedFrames = Elasticsearch.searchResultFormatter(results.body);
                }
                return {enhancedFrames};
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    /** 
     * returns an object containing an array of bev frames.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.sensorId - Sensor ID used to filter bev frames.
     * @param {string} [input.frameId] - Either frameId or timestamp or (fromTimestamp and toTimestamp) should be present. frameId can't occur together with maxResultSize.
     * @param {string} [input.timestamp] - Either frameId or timestamp or (fromTimestamp and toTimestamp) should be present. timestamp can't occur together with maxResultSize.
     * @param {string} [input.fromTimestamp] - Either frameId or timestamp or (fromTimestamp and toTimestamp) should be present.
     * @param {string} [input.toTimestamp] - Either frameId or timestamp or (fromTimestamp and toTimestamp) should be present.
     * @param {number} [input.maxResultSize=25] - Maximum number of bev frames returned. maxResultSize can't occur together with either frameId or timestamp.
     * @returns {Promise<Object>} An object containing an array of bev frames is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorId: "abc", timestamp: "2023-01-12T11:20:10.000Z"};
     * let framesMetadata = new mdx.Services.Frames();
     * let bevFrames = await framesMetadata.getBevFrames(elastic,input);
     */
    async getBevFrames(documentDb, input) {
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                sensorId: {
                    type: "string",
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "sensorId should have atleast 1 character.",
                        maxLength: "sensorId should have atmost 10000 characters."
                    }
                },
                frameId:{
                    type: ["string","null"],
                    default: null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "frameId should have atleast 1 character.",
                        maxLength: "frameId should have atmost 10000 characters."
                    }
                },
                timestamp:{
                    type: ["string","null"],
                    default: null,
                },
                fromTimestamp: {
                    type: ["string","null"],
                    default: null,
                },
                toTimestamp: {
                    type: ["string","null"],
                    default: null,
                },
                maxResultSize: {
                    type: "integer",
                    minimum: 1,
                    maximum: Frames.#maxFramesResultSize,
                    default: Frames.#defaultFramesResultSize,
                    errorMessage: {
                        type: "maxResultSize is not an integer.",
                        minimum: "maxResultSize can have a minimum value of 1.",
                        maximum: `maxResultSize can have a maximum value of ${Frames.#maxFramesResultSize}.`
                    }
                }
            },
            required: ["sensorId"],
            oneOf:[
                {
                    required:["fromTimestamp", "toTimestamp"]
                },
                {
                    required:["timestamp"]
                },
                {
                    required:["frameId"]
                }
            ],
            not: {
                anyOf: [
                    { required: ["timestamp","maxResultSize"] },
                    { required: ["frameId","maxResultSize"] }
                ]
            },
            errorMessage:{
                required: "Input should have required property 'sensorId'.",
                oneOf: "Input can either have 'frameId', 'timestamp' or a time range i.e. 'fromTimestamp' and 'toTimestamp'.",
                not: "Input cannot have 'maxResultSize' when it has either 'frameId' or 'timestamp'."
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        if (!Number.isFinite(input.maxResultSize)) {
            throw (new InvalidInputError("maxResultSize is not a finite integer."));
        }
        if(input.timestamp!=null || input.frameId !=null){
            input.maxResultSize = 1;
            if(input.timestamp!=null){
                if(!Validator.isValidISOTimestamp(input.timestamp)){
                    throw (new InvalidInputError("Invalid timestamp."));
                }
            }
        }else{
            let timeRangeValidationResult = Validator.isValidTimeRange(input.fromTimestamp, input.toTimestamp);
            if (!timeRangeValidationResult.valid) {
                throw (new InvalidInputError(timeRangeValidationResult.reason));
            }
        }
        input.type = "bev";
        let bevFrames = new Array();
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let results = await this.#getFramesFromES(documentDb, input);
                if (!results.indexAbsent) {
                    bevFrames = Elasticsearch.searchResultFormatter(results.body);
                }
                return {bevFrames};
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #getFramesWithAlertsFromES(elasticDb, {fromTimestamp, toTimestamp, sensorId, type, maxResultSize}) {
        let index = `${elasticDb.getConfigs().get("indexPrefix")}${Elasticsearch.getIndex("frames")}`;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({range:{"timestamp":{lte:toTimestamp,gte:fromTimestamp}}});
        if(sensorId!=null){
            queryBody.query.bool.must.push({term :{"sensorId.keyword":sensorId}});
        }
        if(type==null){
            let violationClauses = new Array();
            violationClauses.push({ term: { "info.confinedAreaViolation.keyword": "true" } });
            violationClauses.push({ term: { "socialDistancing.info.proximityViolation.keyword": "true" } });
            violationClauses.push({
                nested: {
                    path: "rois",
                    query: {
                        term: { "rois.info.restrictedAreaViolation.keyword": "true" }
                    }
                }
            });
            queryBody.query.bool.must.push({ bool: { should: violationClauses, minimum_should_match: 1 } });
        }else{
            switch(type) {
                case "proximity":
                    queryBody.query.bool.must.push({ term: { "socialDistancing.info.proximityViolation.keyword": "true" } });
                    break;
                case "restricted-area":
                    queryBody.query.bool.must.push({
                        nested: {
                            path: "rois",
                            query: {
                                term: { "rois.info.restrictedAreaViolation.keyword": "true" }
                            }
                        }
                    });
                    break;
                case "confined-area":
                    queryBody.query.bool.must.push({ term: { "info.confinedAreaViolation.keyword": "true" } });
                    break;
            }
        }
        let queryObject = { 
            index, 
            body: queryBody,
            sort: "timestamp:desc",
            size: maxResultSize,
            _source_excludes: ["type"]
        }
        let results = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return results;
    }

    /**
     * Retrieves frame timestamps and sensor IDs for frames that contain matching alerts.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.fromTimestamp - fromTimestamp for the query in ISO 8601 format.
     * @param {string} input.toTimestamp - toTimestamp for the query in ISO 8601 format.
     * @param {?string} [input.sensorId=null] - Sensor ID used to filter frame alerts.
     * @param {?string} [input.type=null] - Alert type used to filter frame alerts. type should be one of 'proximity', 'restricted-area' or 'confined-area'.
     * @param {number} [input.maxResultSize=25] - Maximum number of alerts returned.
     * @returns {Promise<Object>} An object containing an array of alerts is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {fromTimestamp: "2023-01-12T11:20:10.000Z", toTimestamp: "2023-01-12T14:20:10.000Z"};
     * let framesObject = new mdx.Services.Frames();
     * let alerts = await framesObject.getAlerts(elastic,input);
     */
    async getAlerts(documentDb, input) {
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                fromTimestamp: {
                    type: "string"
                },
                toTimestamp: {
                    type: "string"
                },
                sensorId: {
                    type: ["string", "null"],
                    default: null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "sensorId should have atleast 1 character.",
                        maxLength: "sensorId should have atmost 10000 characters."
                    }
                },
                type: {
                    type: ["string","null"],
                    enum:[
                        null,
                        "proximity",
                        "restricted-area",
                        "confined-area"
                    ],
                    default:null,
                    errorMessage:{
                        enum: "type is an optional input, but when present it must have one of the following values: 'proximity', 'restricted-area' or 'confined-area'."
                    }
                },
                maxResultSize: {
                    type: "integer",
                    minimum: 1,
                    maximum: Frames.#maxAlertResultSize,
                    default: Frames.#defaultAlertResultSize,
                    errorMessage: {
                        type: "maxResultSize is not an integer.",
                        minimum: "maxResultSize can have a minimum value of 1.",
                        maximum: `maxResultSize can have a maximum value of ${Frames.#maxAlertResultSize}.`
                    }
                }
            },
            required: ["fromTimestamp", "toTimestamp"],
            errorMessage:{
                required: "Input should have required properties 'fromTimestamp' and 'toTimestamp'."
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        if (!Number.isFinite(input.maxResultSize)) {
            throw (new InvalidInputError("maxResultSize is not a finite integer."));
        }
        let timeRangeValidationResult = Validator.isValidTimeRange(input.fromTimestamp, input.toTimestamp);
        if (!timeRangeValidationResult.valid) {
            throw (new InvalidInputError(timeRangeValidationResult.reason));
        }
        let alerts = new Array();
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let results = await this.#getFramesWithAlertsFromES(documentDb, input);
                if (!results.indexAbsent) {
                    results = Elasticsearch.searchResultFormatter(results.body);
                    for (let result of results) {
                        let alert = {
                            sensorId: result.sensorId,
                            timestamp: result.timestamp
                        }
                        let objectIdsSet = new Set();
                        if((input.type==null || input.type==="confined-area") && result.info.confinedAreaViolation==="true"){
                            let objectIds = new Array();
                            let objectTypeIdLists = result.info.confinedAreaViolationObjects.split("|");
                            for(let objectTypeIdList of objectTypeIdLists){
                                objectIds.push(...objectTypeIdList.split(",")) ;
                            }
                            alert.confinedArea = { objectIds }
                            for (let objectId of objectIds) {
                                objectIdsSet.add(objectId);
                            }
                        }
                        if((input.type==null || input.type==="proximity") && result.socialDistancing.info.proximityViolation==="true"){
                            alert.proximity = result.socialDistancing;
                            for (let stringifiedObjectList of alert.proximity.info.proximityViolationObjects.split("|")) {
                                for (let objectId of stringifiedObjectList.split(",")) {
                                    objectIdsSet.add(objectId);
                                }
                            }
                        }
                        if((input.type==null || input.type==="restricted-area")){
                            let roiObjectIdsMap = new Map();
                            for(let roi of result.rois){
                                if(roi.info.restrictedAreaViolation==="true"){
                                    if(!roiObjectIdsMap.has(roi.id)){
                                        roiObjectIdsMap.set(roi.id, new Array());
                                    }
                                    let objectIdList = roiObjectIdsMap.get(roi.id);
                                    for (let objectId of roi.objectIds) {
                                        objectIdList.push(objectId);
                                    }
                                    roiObjectIdsMap.set(roi.id, objectIdList);
                                }
                            }
                            if(roiObjectIdsMap.size>0){
                                alert.restrictedArea = new Array();
                                for(let [roiId, objectIds] of roiObjectIdsMap){
                                    alert.restrictedArea.push({
                                        roiId,
                                        objectIds
                                    });
                                    for (let objectId of objectIds) {
                                        objectIdsSet.add(objectId);
                                    }
                                }
                            }
                        }
                        alert.objects = new Array();
                        for(let object of result.objects){
                            if(objectIdsSet.has(object.id)){
                                let objectDetails = {
                                    id: object.id,
                                    type: object.type
                                }
                                if (object.hasOwnProperty("bbox3d") && object.bbox3d.hasOwnProperty("coordinates")) {
                                    objectDetails.bbox3d = {
                                        coordinates: object.bbox3d.coordinates
                                    }
                                }
                                if (object.hasOwnProperty("bbox")) {
                                    objectDetails.bbox = object.bbox
                                }
                                alert.objects.push(objectDetails);
                            }
                        }
                        alerts.push(alert);
                    }
                }
                return {alerts};
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }
}

module.exports = Frames;

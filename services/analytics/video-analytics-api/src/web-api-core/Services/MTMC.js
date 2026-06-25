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
const filterTemplate = require("../queryTemplates/filter.json");
const Database = require("../Utils/Database");
const Frames = require("./Frames");
const Behavior = require("./Behavior");
const serializer = require('proto3-json-serializer');
const protobuf = require('protobufjs');
const path = require('path');
const PROTO_PATH = path.resolve(__dirname, '../schemas/proto/schema.proto');
const nvSchemaRoot = protobuf.loadSync(PROTO_PATH);
const nvFrame = nvSchemaRoot.lookupType("nv.Frame");
const Elasticsearch = require("../Utils/Elasticsearch");
const Validator = require("../Utils/Validator");
const InternalServerError = require('../Errors/InternalServerError');
const InvalidInputError = require('../Errors/InvalidInputError');
const BadRequestError = require('../Errors/BadRequestError');
const Kafka = require("../Utils/Kafka");
const Utils = require("../Utils/Utils");
const winston = require('winston');
const NodeCache = require("node-cache");
let objectCountWithLocationsCache = new NodeCache();
let simulatedTimestampCache = new NodeCache();
let amrCountWithLocationsCache = new NodeCache();
let amrRoutes = new NodeCache();
let amrRouteTimestampCache = new NodeCache();
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
 * Class used for obtaining MTMC related information.
 * @memberof mdxWebApiCore.Services
 * */

class MTMC {

    static #defaultUniqueObjectCountTimeWindowInMs = 100;
    static #defaultUniqueObjectsResultSize = 25;
    static #defaultAMREventsObjectsResultSize = 25;
    static #maxUniqueObjectsResultSize = 100;
    static #maxAMREventsObjectsResultSize = 100;
    static #maxSensorIdsInQuery = 20;
    static #defaultHoursAgoQuery = 48;
    static #maxHoursAgoQbeQuery = 72;
    static #defaultTopKQbe = 50;
    static #defaultQbeMatchScoreThreshold = 0.65;
    static #defaultTimeWindowInMsForUniqueObjectCountWithLocations = 3000;
    static #defaultAmrTimestampWindowInMs = 200;
    static #defaultAmrRouteChangeWindowInMs = 1000;
    static #maxTimeWindowInMs = 5000;
    static #defaultTimeWindowInMs = 100;

    async #getUniqueObjectCountFromES(elasticDb, { timestamp, timeWindowInMs, place, sensorIds, objectId }) {
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        let queryBody = deepcopy(filterTemplate);
        let fromTimestamp = new Date(new Date(timestamp) - timeWindowInMs).toISOString();
        queryBody.query.bool.must.push({ range: { timestamp: { lte: timestamp } } });
        queryBody.query.bool.must.push({ range: { end: { gte: fromTimestamp } } });
        if (place != null) {
            queryBody.query.bool.must.push({
                nested: {
                    path: "matched",
                    query: {
                        bool: {
                            must: [
                                { prefix: { "matched.place.keyword": place } },
                                { range: { "matched.timestamp": { lte: timestamp } } },
                                { range: { "matched.end": { gte: fromTimestamp } } }
                            ]
                        }
                    }
                }
            });
        } else if (objectId != null) {
            queryBody.query.bool.must.push({
                nested: {
                    path: "matched",
                    query: {
                        bool: {
                            must: [
                                { term: { "matched.sensorId.keyword": sensorIds[0] } },
                                { term: { "matched.objectId.keyword": objectId } },
                                { range: { "matched.timestamp": { lte: timestamp } } },
                                { range: { "matched.end": { gte: fromTimestamp } } }
                            ]
                        }
                    }
                }
            });
        } else if (sensorIds != null) {
            queryBody.query.bool.must.push({
                nested: {
                    path: "matched",
                    query: {
                        bool: {
                            must: [
                                { terms: { "matched.sensorId.keyword": sensorIds } },
                                { range: { "matched.timestamp": { lte: timestamp } } },
                                { range: { "matched.end": { gte: fromTimestamp } } }
                            ]
                        }
                    }
                }
            });
        }
        let queryObject = {
            index: `${indexPrefix}${Elasticsearch.getIndex("mtmc")}`,
            body: queryBody
        }
        let uniqueObjectCountResult = await Elasticsearch.getDocCount(elasticDb.getClient(), queryObject, false);
        return uniqueObjectCountResult;
    }

    async #getUniqueObjectsFromES(elasticDb, { fromTimestamp, toTimestamp, place, sensorIds, objectId, globalId, maxResultSize }) {
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ range: { timestamp: { lte: toTimestamp } } });
        queryBody.query.bool.must.push({ range: { end: { gte: fromTimestamp } } });
        if (place != null) {
            queryBody.query.bool.must.push({
                nested: {
                    path: "matched",
                    query: {
                        bool: {
                            must: [
                                { prefix: { "matched.place.keyword": place } },
                                { range: { "matched.timestamp": { lte: toTimestamp } } },
                                { range: { "matched.end": { gte: fromTimestamp } } }
                            ]
                        }
                    }
                }
            });
        } else if (globalId != null) {
            queryBody.query.bool.must.push({ term: { "globalId.keyword": globalId } });
        } else if (objectId != null) {
            queryBody.query.bool.must.push({
                nested: {
                    path: "matched",
                    query: {
                        bool: {
                            must: [
                                { term: { "matched.sensorId.keyword": sensorIds[0] } },
                                { term: { "matched.objectId.keyword": objectId } },
                                { range: { "matched.timestamp": { lte: toTimestamp } } },
                                { range: { "matched.end": { gte: fromTimestamp } } }
                            ]
                        }
                    }
                }
            });
        } else if (sensorIds != null) {
            queryBody.query.bool.must.push({
                nested: {
                    path: "matched",
                    query: {
                        bool: {
                            must: [
                                { terms: { "matched.sensorId.keyword": sensorIds } },
                                { range: { "matched.timestamp": { lte: toTimestamp } } },
                                { range: { "matched.end": { gte: fromTimestamp } } }
                            ]
                        }
                    }
                }
            });
        }
        let queryObject = {
            index: `${indexPrefix}${Elasticsearch.getIndex("mtmc")}`,
            body: queryBody,
            sort: "end:desc",
            size: (globalId != null) ? 1 : maxResultSize
        }
        let uniqueObjectsResult = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return uniqueObjectsResult;
    }

    /** 
     * returns unique object count.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.timestamp - Timestamp for the query in ISO 8601 format.
     * @param {number} input.timeWindowInMs - Time window in milliseconds used to query unique object count.
     * @param {Array<string>} [input.sensorIds] - Either sensorIds or place can be present. They are mutually exclusive. Exactly one sensorId should be in the array when objectId is present.
     * @param {?string} [input.objectId=null] - Object ID used to filter unique objects.
     * @param {string} [input.place] - Either sensorIds or place can be present. They are mutually exclusive.
     * @returns {Promise<Object>} An object containing unique object count is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorIds: ["abc"], timestamp: "2023-01-12T11:20:10.000Z", timeWindowInMs: 100};
     * let mtmc = new mdx.Services.MTMC();
     * let uniqueObjectCount = await mtmc.getUniqueObjectCount(elastic, input);
     */
    async getUniqueObjectCount(documentDb, input) {
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                timestamp: {
                    type: "string"
                },
                timeWindowInMs: {
                    type: "integer",
                    minimum: 1,
                    maximum: 86400000,
                    default: MTMC.#defaultUniqueObjectCountTimeWindowInMs,
                    errorMessage: {
                        type: "timeWindowInMs is not an integer.",
                        minimum: "timeWindowInMs can have a minimum value of 1.",
                        maximum: "timeWindowInMs can have a maximum value of 500."
                    }
                },
                sensorIds: {
                    type: ["array", "null"],
                    items: {
                        type: "string",
                        minLength: 1,
                        maxLength: 10000,
                        errorMessage: {
                            minLength: "Elements of sensorIds array should have atleast 1 character.",
                            maxLength: "Elements of sensorIds array should have atmost 10000 characters."
                        }
                    },
                    minItems: 1,
                    maxItems: MTMC.#maxSensorIdsInQuery,
                    default: null,
                    errorMessage: {
                        minItems: "sensorIds should have atleast 1 item.",
                        maxItems: `sensorIds can have atmost ${MTMC.#maxSensorIdsInQuery} items.`
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
                place: {
                    type: ["string", "null"],
                    default: null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "place should have atleast 1 character.",
                        maxLength: "place should have atmost 10000 characters."
                    }
                }
            },
            required: ["timestamp"],
            not: {
                required: ["sensorIds", "place"]
            },
            if: {
                not: {
                    properties: {
                        objectId: {
                            const: null
                        }
                    }
                }
            },
            then: {
                properties: {
                    sensorIds: {
                        type: "array",
                        maxItems: 1,
                        errorMessage: {
                            maxItems: "Input should have exactly 1 'sensorId' when 'objectId' is present."
                        }
                    }
                },
                required: ["sensorIds"],
                errorMessage: {
                    required: "'sensorIds' is required in input when 'objectId' is present."
                }
            },
            errorMessage: {
                required: "Input should have required properties 'timestamp'.",
                not: "Input cannot have both 'sensorIds' and 'place'.",
                if: "Input should have exactly 1 'sensorId' when 'objectId' is present."
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        let timestampValidationResult = Validator.isValidISOTimestamp(input.timestamp);
        if (!timestampValidationResult) {
            throw (new InvalidInputError("Invalid timestamp."));
        }
        let uniqueObjectCount = 0;
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let results = await this.#getUniqueObjectCountFromES(documentDb, input);
                if (!results.indexAbsent) {
                    uniqueObjectCount = results.count;
                }
                return { uniqueObjectCount };
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    /** 
     * returns unique objects.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.fromTimestamp - fromTimestamp for the query in ISO 8601 format.
     * @param {string} input.toTimestamp - toTimestamp for the query in ISO 8601 format.
     * @param {Array<string>} [input.sensorIds] - Either sensorIds, globalId or place can be present. They are mutually exclusive. Exactly one sensorId should be in the array when objectId is present.
     * @param {?string} [input.objectId=null] - Object ID used to filter unique objects.
     * @param {string} [input.place] - Either sensorIds, globalId or place can be present. They are mutually exclusive.
     * @param {string} [input.globalId] - Global ID used to filter unique objects. Either sensorIds, globalId or place can be present. They are mutually exclusive.
     * @param {number} [input.maxResultSize=25] - Maximum number of unique objects returned. globalId and maxResultSize can't occur together.
     * @returns {Promise<Object>} Unique objects are returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorIds: ["abc"], fromTimestamp: "2023-01-12T11:20:10.000Z", toTimestamp: "2023-01-12T14:20:10.000Z"};
     * let mtmc = new mdx.Services.MTMC();
     * let uniqueObjects = await mtmc.getUniqueObjects(elastic, input);
     */
    async getUniqueObjects(documentDb, input) {
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
                sensorIds: {
                    type: ["array", "null"],
                    items: {
                        type: "string",
                        minLength: 1,
                        maxLength: 10000,
                        errorMessage: {
                            minLength: "Element of sensorIds array should have atleast 1 character.",
                            maxLength: "Element of sensorIds array should have atmost 10000 characters."
                        }
                    },
                    minItems: 1,
                    maxItems: MTMC.#maxSensorIdsInQuery,
                    default: null,
                    errorMessage: {
                        minItems: "sensorIds should have atleast 1 item",
                        maxItems: `sensorIds can have atmost ${MTMC.#maxSensorIdsInQuery} items.`
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
                place: {
                    type: ["string", "null"],
                    default: null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "place should have atleast 1 character.",
                        maxLength: "place should have atmost 10000 characters."
                    }
                },
                globalId: {
                    type: ["string", "null"],
                    default: null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "globalId should have atleast 1 character.",
                        maxLength: "globalId should have atmost 10000 characters."
                    }
                },
                maxResultSize: {
                    type: "integer",
                    minimum: 1,
                    maximum: MTMC.#maxUniqueObjectsResultSize,
                    default: MTMC.#defaultUniqueObjectsResultSize,
                    errorMessage: {
                        type: "maxResultSize is not an integer.",
                        minimum: "maxResultSize can have a minimum value of 1.",
                        maximum: `maxResultSize can have a maximum value of ${MTMC.#maxUniqueObjectsResultSize}.`
                    }
                }
            },
            required: ["fromTimestamp", "toTimestamp"],
            oneOf: [
                { required: ["sensorIds"] },
                { required: ["place"] },
                { required: ["globalId"] },
                {
                    not: {
                        anyOf: [
                            { required: ["sensorIds"] },
                            { required: ["place"] },
                            { required: ["globalId"] }
                        ]
                    }
                }
            ],
            not: {
                required: ["globalId", "maxResultSize"]
            },
            if: {
                not: {
                    properties: {
                        objectId: {
                            const: null
                        }
                    }
                }
            },
            then: {
                properties: {
                    sensorIds: {
                        type: "array",
                        maxItems: 1,
                        errorMessage: {
                            maxItems: "Input should have exactly 1 'sensorId' when 'objectId' is present."
                        }
                    }
                },
                required: ["sensorIds"],
                errorMessage: {
                    required: "'sensorIds' is required in input when 'objectId' is present."
                }
            },
            errorMessage: {
                required: "Input should have required properties 'fromTimestamp' and 'toTimestamp'.",
                not: "Input cannot have both 'globalId' and 'maxResultSize'.",
                oneOf: "Input cannot have a combination of 'sensorIds', 'place' and 'globalId' as they are mutually exclusive.",
                if: "Input should have exactly 1 'sensorId' when 'objectId' is present."
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
        if (!Number.isFinite(input.maxResultSize)) {
            throw (new InvalidInputError("maxResultSize is not a finite integer."));
        }
        let uniqueObjects = new Array();
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let results = await this.#getUniqueObjectsFromES(documentDb, input);
                if (!results.indexAbsent) {
                    uniqueObjects = Elasticsearch.searchResultFormatter(results.body);
                }
                return { uniqueObjects };
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    /** 
     * returns locations of matched objects.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.fromTimestamp - fromTimestamp for the query in ISO 8601 format.
     * @param {string} input.toTimestamp - toTimestamp for the query in ISO 8601 format.
     * @param {Array<string>} [input.behaviorIds] - Either behaviorIds or globalId should be present.
     * @param {string} [input.globalId] - Either behaviorIds or globalId should be present.
     * @returns {Promise<Object>} Locations of matched objects are returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {globalId: "12", fromTimestamp: "2023-01-12T11:20:10.000Z", toTimestamp: "2023-01-12T14:20:10.000Z"};
     * let mtmc = new mdx.Services.MTMC();
     * let behaviors = await mtmc.getLocationsOfMatchedBehaviors(elastic, input);
     */
    async getLocationsOfMatchedBehaviors(documentDb, input) {
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
                behaviorIds: {
                    type: "array",
                    items: {
                        type: "string",
                        minLength: 1,
                        maxLength: 10000,
                        errorMessage: {
                            minLength: "Element of behaviorIds array should have atleast 1 character.",
                            maxLength: "Element of behaviorIds array should have atmost 10000 characters."
                        }
                    },
                    minItems: 1,
                    maxItems: Behavior.getMaxBehaviorsInLocationQuery(),
                    errorMessage: {
                        minItems: "behaviorIds should have atleast 1 item",
                        maxItems: `behaviorIds can have atmost ${Behavior.getMaxBehaviorsInLocationQuery()} items.`
                    }
                },
                globalId: {
                    type: "string",
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "globalId should have atleast 1 character.",
                        maxLength: "globalId should have atmost 10000 characters."
                    }
                }
            },
            required: ["fromTimestamp", "toTimestamp"],
            oneOf: [
                { required: ["behaviorIds"] },
                { required: ["globalId"] }
            ],
            errorMessage: {
                required: "Input should have required properties 'fromTimestamp' and 'toTimestamp'.",
                oneOf: "Exactly one of the following has to be present in the input: 'behaviorIds' or 'globalId'."
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
        if ("globalId" in input) {
            let { uniqueObjects } = await this.getUniqueObjects(documentDb, { fromTimestamp: input.fromTimestamp, toTimestamp: input.toTimestamp, globalId: input.globalId });
            if (uniqueObjects.length == 0) {
                return { behaviors: new Array() };
            } else {
                input.behaviorIds = uniqueObjects[0].matched.map(behavior => behavior.id);
            }
        }
        let behaviorMetadata = new Behavior();
        if (input.behaviorIds.length > Behavior.getMaxBehaviorsInLocationQuery()) {
            let splitListCount = Math.ceil(input.behaviorIds.length / Behavior.getMaxBehaviorsInLocationQuery());
            let behaviorIdLists = new Array();
            for (let i = 0; i < splitListCount; i++) {
                if (i === splitListCount - 1) {
                    behaviorIdLists.push(input.behaviorIds.slice(i * Behavior.getMaxBehaviorsInLocationQuery(), input.behaviorIds.length));
                } else {
                    behaviorIdLists.push(input.behaviorIds.slice(i * Behavior.getMaxBehaviorsInLocationQuery(), (i + 1) * Behavior.getMaxBehaviorsInLocationQuery()));
                }
            }
            let behaviorLocationPromises = behaviorIdLists.map(behaviorIdList => behaviorMetadata.getLocationsOfBehaviors(documentDb, {
                fromTimestamp: input.fromTimestamp,
                toTimestamp: input.toTimestamp,
                behaviorIds: behaviorIdList
            }));
            let behaviorLocationResponses = await Promise.all(behaviorLocationPromises);
            let behaviors = behaviorLocationResponses.map(behaviorLocationResponse => behaviorLocationResponse.behaviors).flat();
            return { behaviors };
        } else {
            let behaviors = await behaviorMetadata.getLocationsOfBehaviors(documentDb, {
                fromTimestamp: input.fromTimestamp,
                toTimestamp: input.toTimestamp,
                behaviorIds: input.behaviorIds
            });
            return behaviors;
        }
    }

    /** 
     * returns normalized embedding.
     * @public
     * @param {Array<number>} embedding - Embedding vector to normalize.
     * @returns {Array<number>} Normalized embedding is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * let mtmc = new mdx.Services.MTMC();
     * let normalizedEmbedding = mtmc.getNormalizedEmbedding(embedding);
     */
    getNormalizedEmbedding(embedding) {
        let sumOfSquares = 0;
        for (let element of embedding) {
            sumOfSquares += Math.pow(element, 2);
        }
        let denominator = Math.sqrt(sumOfSquares);
        let normalizedEmbedding = new Array();
        for (let element of embedding) {
            normalizedEmbedding.push((element / denominator));
        }
        return normalizedEmbedding;
    }

    /** 
     * consumes incoming rtls messages.
     * @public
     * @async
     * @param {MessageBroker} messageBroker - MessageBroker Object
     * @param {Object} rtlsConfig - RTLS config object.
     * @returns {Promise<void>}
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const kafka = new mdx.Utils.Kafka({brokers: ["kafka-broker-url"]}, kafkaConfigMap);
     * let rtlsConfig = {inSimulationMode: false};
     * let mtmcObject = new mdx.Services.MTMC();
     * await mtmcObject.consumeRTLSMessages(kafka,rtlsConfig);
     */
    async consumeRTLSMessages(messageBroker, rtlsConfig) {
        if (messageBroker == null) {
            throw (new InvalidInputError("A message broker like 'kafka' is required to consume RTLS messages."));
        }
        switch (messageBroker.getName()) {
            case "Kafka": {
                await this.#consumeRTLSKafkaMessages(messageBroker, rtlsConfig);
                break;
            }
            default:
                throw (new InternalServerError(`Invalid message broker: ${messageBroker.getName()}.`));
        }
    }

    async #consumeRTLSKafkaMessages(kafka, rtlsConfig) {
        const processRTLSKafkaMessage = ({ topic, partition, message }) => {
            const decodedFrameProto = nvFrame.decode(message.value);
            let value = serializer.toProto3JSON(decodedFrameProto);
            value.timestamp = new Date(value.timestamp).toISOString();
            if(objectCountWithLocationsCache.has(value.info.place)){
                let latestMessageInCache = objectCountWithLocationsCache.get(value.info.place);
                if(Utils.tsCompare(value.timestamp, "<", latestMessageInCache.timestamp)){
                    logger.warn(`[RTLS UNORDERED DATA] The current datapoint has timestamp: ${value.timestamp}. Previous datapoint's timestamp: ${latestMessageInCache.timestamp}.`);
                }else if(Utils.tsCompare(value.timestamp, "==", latestMessageInCache.timestamp)){
                    logger.warn(`[RTLS DATA ISSUE] The current and previous datapoint have the same timestamp: ${value.timestamp}.`);
                }
            }
            objectCountWithLocationsCache.set(value.info.place, value);
            if (rtlsConfig.inSimulationMode) {
                simulatedTimestampCache.set(value.info.place, value.timestamp);
            }
        }
        const kafkaClient = kafka.getClient();
        const adminClient = kafka.getAdminClient();
        const topicPattern = Kafka.getTopicPattern("rtls");
        const topicRegex = new RegExp(topicPattern);
        const consumerGroup = "mdx-rtls-web-api";
        await Kafka.initializeConsumer(({topic, partition, message})=>processRTLSKafkaMessage({topic, partition, message}), kafkaClient, adminClient, consumerGroup, topicRegex, {isTopicPattern: true});
    }

    /** 
     * consumes incoming AMR messages.
     * @public
     * @async
     * @param {MessageBroker} messageBroker - MessageBroker Object
     * @param {Object} amrConfig - AMR config object.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const kafka = new mdx.Utils.Kafka({brokers: ["kafka-broker-url"]}, kafkaConfigMap);
     * let amrConfig = {inSimulationMode: false, amrRetentionInSec: 3600};
     * let mtmcObject = new mdx.Services.MTMC();
     * await mtmcObject.consumeAMRMessages(kafka,amrConfig);
     */
    async consumeAMRMessages(messageBroker, amrConfig) {
        if (messageBroker == null) {
            throw (new InvalidInputError("A message broker like 'kafka' is required to consume AMR messages."));
        }
        switch (messageBroker.getName()) {
            case "Kafka": {
                await this.#consumeAMRKafkaMessages(messageBroker, amrConfig);
                break;
            }
            default:
                throw (new InternalServerError(`Invalid message broker: ${messageBroker.getName()}.`));
        }
    }

    #updateAmrRouteCache(message, amrConfig) {
        let value = JSON.parse(message.value.toString());
        if(amrRouteTimestampCache.has(value.place)){
            let timestampFromCache = amrRouteTimestampCache.get(value.place);
            if(Utils.tsCompare(value.timestamp, "<", timestampFromCache)){
                logger.warn(`[AMR EVENTS UNORDERED DATA] The current datapoint has timestamp: ${value.timestamp}. Previous datapoint's timestamp: ${timestampFromCache}.`);
            }else if(Utils.tsCompare(value.timestamp, "==", timestampFromCache)){
                logger.warn(`[AMR EVENTS DATA ISSUE] The current and previous datapoint have the same timestamp: ${value.timestamp}.`);
            }
        }
        amrRouteTimestampCache.set(value.place, value.timestamp);
        let routeMap = amrRoutes.get(value.place) || new Map();
        for (let event of value.events) {
            routeMap.set(event.objectId, { timestamp: value.timestamp, event });
        }
        let currentTimestamp = (!amrConfig.inSimulationMode) ? new Date() : (simulatedTimestampCache.get(value.place) || value.timestamp);
        let minRouteTimestamp = new Date(new Date(currentTimestamp) - (amrConfig.amrRetentionInSec * 1000)).toISOString();
        let amrIdsToBeDeleted = new Set();
        for (let [amrId, routeEventObject] of routeMap) {
            if (Utils.tsCompare(routeEventObject.timestamp, "<", minRouteTimestamp)) {
                amrIdsToBeDeleted.add(amrId);
            }
        }
        for (let amrId of amrIdsToBeDeleted) {
            routeMap.delete(amrId);
        }
        amrRoutes.set(value.place, routeMap);
    }

    #updateAmrLocationCacheList(message, amrConfig) {
        let value = JSON.parse(message.value.toString());
        let currentList = amrCountWithLocationsCache.get(value.place) || new Array();
        if(currentList.length>0){
            let previousRecord = currentList[currentList.length-1];
            if(Utils.tsCompare(value.timestamp, "<", previousRecord.timestamp)){
                logger.warn(`[AMR UNORDERED DATA] The current datapoint has timestamp: ${value.timestamp}. Previous datapoint's timestamp: ${previousRecord.timestamp}.`);
            }else if(Utils.tsCompare(value.timestamp, "==", previousRecord.timestamp)){
                logger.warn(`[AMR DATA ISSUE] The current and previous datapoint have the same timestamp: ${value.timestamp}.`);
            }
        }
        currentList.push(value);
        let currentTimestamp = (!amrConfig.inSimulationMode) ? new Date() : (simulatedTimestampCache.get(value.place) || value.timestamp);
        let minTimestampOfNewList = new Date(new Date(currentTimestamp) - (amrConfig.amrRetentionInSec * 1000)).toISOString();
        let newStartIndex = 0;
        for (let amrResult of currentList) {
            if (Utils.tsCompare(amrResult.timestamp, "<", minTimestampOfNewList)) {
                newStartIndex++;
            } else {
                break;
            }
        }
        if (newStartIndex > 0) {
            if (newStartIndex == currentList.length) {
                currentList = new Array();
            } else {
                currentList = currentList.slice(newStartIndex);
            }
        }
        amrCountWithLocationsCache.set(value.place, currentList);
    }

    async #consumeAMRKafkaMessages(kafka, amrConfig) {
        const processAMRKafkaMessage = ({topic, partition, message}) => {
            let messageType = message.headers.type;
            if (messageType === undefined) {
                logger.warn("[AMR DATA ISSUE] Header 'type' has to be present for AMR data.");
            } else if (messageType.toString() === "mdx-amr-locations") {
                this.#updateAmrLocationCacheList(message, amrConfig);
            } else if (messageType.toString() === "mdx-amr-events") {
                this.#updateAmrRouteCache(message, amrConfig);
            } else {
                logger.warn(`[AMR DATA ISSUE] Invalid value: ${messageType.toString()} for header 'type'.`);
            }
        }
        const kafkaClient = kafka.getClient();
        const adminClient = kafka.getAdminClient();
        const topic = Kafka.getTopic("amr");
        const consumerGroup = `${topic}-web-api`;
        await Kafka.initializeConsumer(({topic, partition, message})=>processAMRKafkaMessage({topic, partition, message}), kafkaClient, adminClient, consumerGroup, topic);
    }

    async #getUniqueObjectCountWithLocationsFromEs(elasticDb, { place, timestamp, timeWindowInMs }) {
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("rtls")}`;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ range: { "timestamp": { lte: timestamp, gte: new Date(new Date(timestamp) - timeWindowInMs).toISOString() } } });
        queryBody.query.bool.must.push({ term: { "place.keyword": place } });
        let queryObject = {
            index,
            body: queryBody,
            sort: "timestamp:desc",
            size: 1,
            _source_excludes: ["Id", "type"]
        };
        let results = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return results;
    }

    async #getAMRRecordFromES(elasticDb, { place, amrTimestampWindowInMs, timestampOfRTLSRecord }) {
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("amrLocations")}`;
        let pastQueryBody = deepcopy(filterTemplate);
        pastQueryBody.query.bool.must.push({ range: { "timestamp": { lte: timestampOfRTLSRecord, gte: new Date(new Date(timestampOfRTLSRecord) - amrTimestampWindowInMs).toISOString() } } });
        pastQueryBody.query.bool.must.push({ term: { "place.keyword": place } });
        let pastQueryObject = {
            index,
            body: pastQueryBody,
            sort: "timestamp:desc",
            size: 1,
            _source_excludes: ["Id", "type"]
        };
        let futureQueryBody = deepcopy(filterTemplate);
        futureQueryBody.query.bool.must.push({ term: { "place.keyword": place } });
        futureQueryBody.query.bool.must.push({ range: { "timestamp": { lte: new Date(new Date(timestampOfRTLSRecord).getTime() + amrTimestampWindowInMs).toISOString(), gt: timestampOfRTLSRecord } } });
        let futureQueryObject = {
            index,
            body: futureQueryBody,
            sort: "timestamp:asc",
            size: 1,
            _source_excludes: ["Id", "type"]
        };
        let chosenAmrRecord = null;
        let [pastResult, futureResult] = await Promise.all([
            Elasticsearch.getSearchResults(elasticDb.getClient(), pastQueryObject, false),
            Elasticsearch.getSearchResults(elasticDb.getClient(), futureQueryObject, false)
        ]);
        if (!pastResult.indexAbsent) {
            pastResult = Elasticsearch.searchResultFormatter(pastResult.body);
            if (pastResult.length > 0) {
                chosenAmrRecord = pastResult[0];
            }
        }
        if (!futureResult.indexAbsent) {
            futureResult = Elasticsearch.searchResultFormatter(futureResult.body);
            if (futureResult.length > 0) {
                if (chosenAmrRecord == null) {
                    chosenAmrRecord = futureResult[0];
                } else {
                    let pastDiff = new Date(timestampOfRTLSRecord) - new Date(chosenAmrRecord.timestamp);
                    let futureDiff = new Date(futureResult[0].timestamp) - new Date(timestampOfRTLSRecord);
                    if (futureDiff <= pastDiff) {
                        chosenAmrRecord = futureResult[0];
                    }
                }
            }
        }
        return chosenAmrRecord;
    }

    async #getAMRRecordWithoutRtlsFromES(elasticDb, { place, timestamp, timeWindowInMs }) {
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("amrLocations")}`;
        let minPossibleTimestampOfAmr = new Date(new Date(timestamp) - timeWindowInMs).toISOString();
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ range: { "timestamp": { lte: timestamp, gte: minPossibleTimestampOfAmr } } });
        queryBody.query.bool.must.push({ term: { "place.keyword": place } });
        let queryObject = {
            index,
            body: queryBody,
            sort: "timestamp:desc",
            size: 1,
            _source_excludes: ["Id", "type"]
        };
        let chosenAmrRecord = null;
        let result = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        if (!result.indexAbsent) {
            result = Elasticsearch.searchResultFormatter(result.body);
            if (result.length > 0) {
                chosenAmrRecord = result[0];
            }
        }
        return chosenAmrRecord;
    }

    async #getAMRRouteEventsFromES(elasticDb, { place, amrRouteChangeWindowInMs, timestamp, timeWindowInMs, timestampOfRTLSRecord = null }) {
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("amrEvents")}`;
        let currentTimestamp = (timestampOfRTLSRecord != null) ? timestampOfRTLSRecord : timestamp;
        let minRouteTimestamp = (timestampOfRTLSRecord != null) ? new Date(new Date(currentTimestamp) - (amrRouteChangeWindowInMs)).toISOString() :
            new Date(new Date(currentTimestamp) - (timeWindowInMs)).toISOString();
        let maxRouteTimestamp = (timestampOfRTLSRecord != null) ? timestampOfRTLSRecord : timestamp;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ range: { "timestamp": { lte: maxRouteTimestamp, gte: minRouteTimestamp } } });
        queryBody.query.bool.must.push({ term: { "place.keyword": place } });
        let queryObject = {
            index,
            body: queryBody,
            sort: "timestamp:asc",
            size: 10000,
            _source_excludes: ["Id", "type"]
        };
        let events = new Array();
        let result = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        if (!result.indexAbsent) {
            result = Elasticsearch.searchResultFormatter(result.body);
            if (result.length > 0) {
                let amrRouteMap = new Map();
                for (let eventMessage of result) {
                    for (let event of eventMessage.events) {
                        amrRouteMap.set(event.objectId, { timestamp: eventMessage.timestamp, event });
                    }
                }
                for (let [amrId, value] of amrRouteMap) {
                    events.push(value.event);
                }
            }
        }
        return events;
    }

    #getLatestUniqueObjectCountWithLocationsFromCache({ place, timestamp, timeWindowInMs }, inSimulationMode) {
        let latestMessageInCache = objectCountWithLocationsCache.get(place);
        if (latestMessageInCache == undefined || (!inSimulationMode && Utils.tsCompare(latestMessageInCache.timestamp, "<", new Date(new Date(timestamp) - timeWindowInMs).toISOString()))) {
            return null;
        } else {
            return latestMessageInCache;
        }
    }

    async #getAMREventsFromES(elasticDb, { fromTimestamp, toTimestamp, place, objectType, maxResultSize }) {
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("amrEvents")}`;

        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ range: { "timestamp": { lte: toTimestamp, gte: fromTimestamp } } });
        if (place != null) {
            queryBody.query.bool.must.push({ term: { "place.keyword": place } });
        }

        if (objectType != null) {
            queryBody.query.bool.must.push({
                nested: {
                    path: "events",
                    query: {
                        bool: {
                            must: [
                                { term: { "events.objectType.keyword": objectType } }
                            ]
                        }
                    }
                }
            });
        }

        let queryObject = {
            index,
            body: queryBody,
            sort: "timestamp:desc",
            size: maxResultSize
        };
        let amrEvents = new Array();
        let result = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        if (!result.indexAbsent) {
            result = Elasticsearch.searchResultFormatter(result.body);
            if (result.length > 0) {
                amrEvents = result.map(eventMessage => ({
                    timestamp: eventMessage.timestamp,
                    place: eventMessage.place,
                    events: eventMessage.events
                }));
                if (objectType != null) {
                    amrEvents.forEach(eventMessage => {
                        eventMessage.events = eventMessage.events.filter(event => event.objectType === objectType);
                    });
                }
            }
        }
        return amrEvents;
    }


    #getAMRRecordFromCache({ place, amrTimestampWindowInMs, timestampOfRTLSRecord }) {
        let amrMessages = amrCountWithLocationsCache.get(place) || new Array();
        let minPossibleTimestampOfAmr = new Date(new Date(timestampOfRTLSRecord) - amrTimestampWindowInMs).toISOString();
        let chosenAmrRecord = null;
        for (let amrMessage of amrMessages) {
            if (Utils.tsCompare(amrMessage.timestamp, "<", minPossibleTimestampOfAmr)) {
                continue;
            } else if (Utils.tsCompare(amrMessage.timestamp, "<=", timestampOfRTLSRecord)) {
                if (chosenAmrRecord == null) {
                    chosenAmrRecord = amrMessage;
                } else {
                    if (Utils.tsCompare(amrMessage.timestamp, ">=", chosenAmrRecord.timestamp)) {
                        chosenAmrRecord = amrMessage;
                    }
                }
            } else {
                let maxFutureTimestamp = new Date(new Date(timestampOfRTLSRecord).getTime() + amrTimestampWindowInMs).toISOString();
                if (Utils.tsCompare(amrMessage.timestamp, "<=", maxFutureTimestamp)) {
                    if (chosenAmrRecord == null) {
                        chosenAmrRecord = amrMessage;
                    } else {
                        let pastDiff = new Date(timestampOfRTLSRecord) - new Date(chosenAmrRecord.timestamp);
                        let futureDiff = new Date(amrMessage.timestamp) - new Date(timestampOfRTLSRecord);
                        if (futureDiff <= pastDiff) {
                            chosenAmrRecord = amrMessage;
                        }
                    }
                }
                break;
            }
        }
        return chosenAmrRecord;
    }

    #getAMRRouteEventsFromCache({ place, timeWindowInMs, timestamp, amrRouteChangeWindowInMs, timestampOfRTLSRecord = null }) {
        let amrRouteMap = amrRoutes.get(place) || new Map();
        let events = new Array();
        let currentTimestamp = (timestampOfRTLSRecord != null) ? timestampOfRTLSRecord : timestamp;
        let minRouteTimestamp = (timestampOfRTLSRecord != null) ? new Date(new Date(currentTimestamp) - (amrRouteChangeWindowInMs)).toISOString() :
            new Date(new Date(currentTimestamp) - (timeWindowInMs)).toISOString();
        let maxRouteTimestamp = (timestampOfRTLSRecord != null) ? timestampOfRTLSRecord : timestamp;
        for (let [amrId, value] of amrRouteMap) {
            if (Utils.tsCompare(value.timestamp, ">=", minRouteTimestamp) && Utils.tsCompare(value.timestamp, "<=", maxRouteTimestamp)) {
                events.push(value.event);
            }
        }
        return events;
    }

    #getAMRRecordWithoutRtlsFromCache({ place, timestamp, timeWindowInMs }) {
        let amrMessages = amrCountWithLocationsCache.get(place) || new Array();
        let minPossibleTimestampOfAmr = new Date(new Date(timestamp) - timeWindowInMs).toISOString();
        let chosenAmrRecord = null;
        for (let amrMessage of amrMessages) {
            if (Utils.tsCompare(amrMessage.timestamp, "<", minPossibleTimestampOfAmr)) {
                continue;
            } else if (Utils.tsCompare(amrMessage.timestamp, "<=", timestamp)) {
                if (chosenAmrRecord == null) {
                    chosenAmrRecord = amrMessage;
                } else {
                    if (Utils.tsCompare(amrMessage.timestamp, ">=", chosenAmrRecord.timestamp)) {
                        chosenAmrRecord = amrMessage;
                    }
                }
            } else {
                break;
            }
        }
        return chosenAmrRecord;
    }

    /** 
     * returns an object containing unique object count of a place with object locations.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {MessageBroker} messageBroker - MessageBroker Object
     * @param {Object} input - Input object.
     * @param {string} input.place - Place used to filter unique object count with locations.
     * @param {?string} [input.timestamp=null] - Timestamp for the query in ISO 8601 format.
     * @param {number} [input.timeWindowInMs=3000] - Time window in milliseconds used to query object locations.
     * @param {number} [input.amrTimestampWindowInMs=200] - AMR timestamp window in milliseconds used to match records.
     * @param {number} [input.amrRouteChangeWindowInMs=1000] - AMR route change window in milliseconds used to match route events.
     * @param {boolean} [inSimulationMode=false] - Whether to use simulation mode.
     * @returns {Promise<Object>} Unique object count of a place along with object locations is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * const kafka = new mdx.Utils.Kafka({brokers: ["kafka-broker-url"]}, kafkaConfigMap);
     * let input = {place: "building=abc/room=xyz"};
     * let mtmc = new mdx.Services.MTMC();
     * let uniqueObjectCountWithLocations = await mtmc.getUniqueObjectCountWithLocations(elastic,kafka,input);
     */
    async getUniqueObjectCountWithLocations(documentDb, messageBroker, input, inSimulationMode = false) {
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                place: {
                    type: "string",
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "place should have atleast 1 character.",
                        maxLength: "place should have atmost 10000 characters."
                    }
                },
                timestamp: {
                    type: ["string", "null"],
                    default: null
                },
                timeWindowInMs: {
                    type: "integer",
                    minimum: 1,
                    default: MTMC.#defaultTimeWindowInMsForUniqueObjectCountWithLocations,
                    errorMessage: {
                        type: "timeWindowInMs is not an integer.",
                        minimum: "timeWindowInMs can have a minimum value of 1."
                    }
                },
                amrTimestampWindowInMs: {
                    type: "integer",
                    minimum: 0,
                    default: MTMC.#defaultAmrTimestampWindowInMs,
                    errorMessage: {
                        type: "amrTimestampWindowInMs is not an integer.",
                        minimum: "amrTimestampWindowInMs can have a minimum value of 0."
                    }
                },
                amrRouteChangeWindowInMs: {
                    type: "integer",
                    minimum: 0,
                    default: MTMC.#defaultAmrRouteChangeWindowInMs,
                    errorMessage: {
                        type: "amrRouteChangeWindowInMs is not an integer.",
                        minimum: "amrRouteChangeWindowInMs can have a minimum value of 0."
                    }
                }
            },
            required: ["place"],
            errorMessage: {
                required: "Input should have required properties 'place'.",
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        if (!Number.isFinite(input.timeWindowInMs)) {
            throw (new InvalidInputError("timeWindowInMs is not a finite integer."));
        }
        let uniqueObjectCountWithLocations = { place: input.place, timestamp: null, objectCounts: new Array(), locationsOfObjects: new Array() };
        if (input.timestamp != null) {
            if (!Validator.isValidISOTimestamp(input.timestamp)) {
                throw (new InvalidInputError("Invalid timestamp."));
            }
            uniqueObjectCountWithLocations.timestamp = input.timestamp;
            switch (documentDb.getName()) {
                case "Elasticsearch": {
                    let rtlsAbsent = false;
                    let results = await this.#getUniqueObjectCountWithLocationsFromEs(documentDb, input);
                    if (!results.indexAbsent) {
                        results = Elasticsearch.searchResultFormatter(results.body);
                        if (results.length > 0) {
                            uniqueObjectCountWithLocations = results[0];
                            let [amrRecord, events] = await Promise.all([
                                this.#getAMRRecordFromES(documentDb, {
                                    place: input.place,
                                    timestampOfRTLSRecord: uniqueObjectCountWithLocations.timestamp,
                                    amrTimestampWindowInMs: input.amrTimestampWindowInMs
                                }),
                                this.#getAMRRouteEventsFromES(documentDb, {
                                    place: input.place,
                                    amrRouteChangeWindowInMs: input.amrRouteChangeWindowInMs,
                                    timestampOfRTLSRecord: uniqueObjectCountWithLocations.timestamp,
                                    timestamp: input.timestamp,
                                    timeWindowInMs: input.timeWindowInMs
                                })
                            ]);
                            if (amrRecord != null) {
                                for (let objectCount of amrRecord.objectCounts) {
                                    uniqueObjectCountWithLocations.objectCounts.push(objectCount);
                                }
                                for (let locationsOfObject of amrRecord.locationsOfObjects) {
                                    uniqueObjectCountWithLocations.locationsOfObjects.push(locationsOfObject);
                                }
                            }
                            if (events.length > 0) {
                                uniqueObjectCountWithLocations.events = events;
                            }
                        } else {
                            rtlsAbsent = true;
                        }
                    } else {
                        rtlsAbsent = true;
                    }
                    if (rtlsAbsent) {
                        let [amrRecord, events] = await Promise.all([
                            this.#getAMRRecordWithoutRtlsFromES(documentDb, {
                                place: input.place,
                                timestamp: input.timestamp,
                                timeWindowInMs: input.timeWindowInMs
                            }),
                            this.#getAMRRouteEventsFromES(documentDb, {
                                place: input.place,
                                amrRouteChangeWindowInMs: input.amrRouteChangeWindowInMs,
                                timestamp: input.timestamp,
                                timeWindowInMs: input.timeWindowInMs
                            })
                        ]);
                        if (amrRecord != null) {
                            uniqueObjectCountWithLocations.timestamp = amrRecord.timestamp;
                            for (let objectCount of amrRecord.objectCounts) {
                                uniqueObjectCountWithLocations.objectCounts.push(objectCount);
                            }
                            for (let locationsOfObject of amrRecord.locationsOfObjects) {
                                uniqueObjectCountWithLocations.locationsOfObjects.push(locationsOfObject);
                            }
                        }
                        if (events.length > 0) {
                            uniqueObjectCountWithLocations.events = events;
                        }
                    }
                    return uniqueObjectCountWithLocations;
                }
                default:
                    throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
            }
        } else {
            if (messageBroker == null) {
                throw (new InvalidInputError("A message broker like 'kafka' is required to consume messages which provide real time locations and object count."));
            }
            if (!inSimulationMode) {
                let currentEpochTimeInSec = (new Date().getTime()) / 1000;
                let roundedTimestamp = new Date(Math.ceil(currentEpochTimeInSec) * 1000);
                input.timestamp = roundedTimestamp.toISOString();
                uniqueObjectCountWithLocations.timestamp = input.timestamp;
            }
            let latestRtlsRecord = this.#getLatestUniqueObjectCountWithLocationsFromCache(input, inSimulationMode);
            if (latestRtlsRecord != null) {
                uniqueObjectCountWithLocations.timestamp = latestRtlsRecord.timestamp;
                if("fov" in latestRtlsRecord){
                    for(let typeMetrics of latestRtlsRecord.fov){
                        uniqueObjectCountWithLocations.objectCounts.push({
                            type : typeMetrics.type,
                            count : typeMetrics.count
                        });
                    }
                }
                if("objects" in latestRtlsRecord){
                    for(let object of latestRtlsRecord.objects){
                        let locations=[object.coordinate.x,object.coordinate.y];
                        if("z" in object.coordinate){
                            locations.push(object.coordinate.z);
                        }
                        uniqueObjectCountWithLocations.locationsOfObjects.push({
                            id: object.id,
                            locations:[locations],
                            type:object.type
                        });
                    }
                }
                let amrRecord = this.#getAMRRecordFromCache({
                    place: input.place,
                    timestampOfRTLSRecord: uniqueObjectCountWithLocations.timestamp,
                    amrTimestampWindowInMs: input.amrTimestampWindowInMs
                });
                if (amrRecord != null) {
                    for (let objectCount of amrRecord.objectCounts) {
                        uniqueObjectCountWithLocations.objectCounts.push(objectCount);
                    }
                    for (let locationsOfObject of amrRecord.locationsOfObjects) {
                        uniqueObjectCountWithLocations.locationsOfObjects.push(locationsOfObject);
                    }
                }
                let events = this.#getAMRRouteEventsFromCache({
                    place: input.place,
                    amrRouteChangeWindowInMs: input.amrRouteChangeWindowInMs,
                    timestampOfRTLSRecord: uniqueObjectCountWithLocations.timestamp,
                    timestamp: input.timestamp,
                    timeWindowInMs: input.timeWindowInMs
                });
                if (events.length > 0) {
                    uniqueObjectCountWithLocations.events = events;
                }
            } else {
                if (!inSimulationMode) {
                    let amrRecord = this.#getAMRRecordWithoutRtlsFromCache({
                        place: input.place,
                        timestamp: input.timestamp,
                        timeWindowInMs: input.timeWindowInMs
                    });
                    if (amrRecord != null) {
                        uniqueObjectCountWithLocations.timestamp = amrRecord.timestamp;
                        for (let objectCount of amrRecord.objectCounts) {
                            uniqueObjectCountWithLocations.objectCounts.push(objectCount);
                        }
                        for (let locationsOfObject of amrRecord.locationsOfObjects) {
                            uniqueObjectCountWithLocations.locationsOfObjects.push(locationsOfObject);
                        }
                    }
                    let events = this.#getAMRRouteEventsFromCache({
                        place: input.place,
                        amrRouteChangeWindowInMs: input.amrRouteChangeWindowInMs,
                        timestamp: input.timestamp,
                        timeWindowInMs: input.timeWindowInMs
                    });
                    if (events.length > 0) {
                        uniqueObjectCountWithLocations.events = events;
                    }
                }
            }
            return uniqueObjectCountWithLocations;
        }
    }

    /** 
     * returns AMR events for the requested place, object type, and time range.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.fromTimestamp - fromTimestamp for the query in ISO 8601 format.
     * @param {string} input.toTimestamp - toTimestamp for the query in ISO 8601 format.
     * @param {string} input.place - Place used to filter AMR events.
     * @param {string} [input.objectType="AMR"] - Object type used to filter AMR events.
     * @param {number} [input.maxResultSize=25] - Maximum number of AMR events returned.
     * @returns {Promise<Object>} An object containing an array of AMR events is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"}, databaseConfigMap);
     * let input = {place: "building=abc/room=xyz", objectType: "AMR", fromTimestamp: "2023-01-12T11:20:10.000Z", toTimestamp: "2023-01-12T14:20:10.000Z"};
     * let mtmc = new mdx.Services.MTMC();
     * let amrEvents = await mtmc.getAMREvents(elastic, input);
     */
    async getAMREvents(documentDb, input) {
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
                place: {
                    type: "string",
                    minLength: 1,
                    errorMessage: {
                        minLength: "place should have atleast 1 character.",
                        maxLength: "place should have atmost 10000 characters."
                    }
                },
                maxResultSize: {
                    type: "integer",
                    minimum: 1,
                    maximum: MTMC.#maxAMREventsObjectsResultSize,
                    default: MTMC.#defaultAMREventsObjectsResultSize,
                    errorMessage: {
                        type: "maxResultSize is not an integer.",
                        minimum: "maxResultSize can have a minimum value of 1.",
                        maximum: `maxResultSize can have a maximum value of ${MTMC.#maxAMREventsObjectsResultSize}.`
                    }
                },
                objectType: {
                    type: "string",
                    minLength: 1,
                    default: "AMR",
                    errorMessage: {
                        minLength: "objectType should have atleast 1 character.",
                        maxLength: "objectType should have atmost 10000 characters."
                    }
                }
            },
            required: ["fromTimestamp", "toTimestamp", "place", "objectType"],
            errorMessage: {
                required: "Input should have required properties 'fromTimestamp', 'toTimestamp', 'place' and 'objectType'."
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

        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let results = await this.#getAMREventsFromES(documentDb, input);
                return { amrEvents: results };
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    /**
     * Retrieves tracker occupancy counts for a place at a given timestamp.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.timestamp - Timestamp in ISO 8601 format.
     * @param {string} input.place - Place used to filter tracker occupancy.
     * @param {number} [input.timeWindowInMs=100] - Time window in milliseconds used to query tracker occupancy.
     * @returns {Promise<Object>} An object containing an array of tracker occupancy counts is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"}, databaseConfigMap);
     * let input = {place: "building=abc/room=xyz", timestamp: "2023-01-12T11:20:10.000Z", timeWindowInMs: 100};
     * let mtmc = new mdx.Services.MTMC();
     * let trackerOccupancy = await mtmc.getOccupancyTracker(elastic, input);
     */
    async getOccupancyTracker(documentDb, input) {
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                timestamp: {
                    type: "string"
                },
                place: {
                    type: "string",
                    minLength: 1,
                    errorMessage: {
                        minLength: "place should have atleast 1 character.",
                        maxLength: "place should have atmost 10000 characters."
                    }
                },
                timeWindowInMs: {
                    type: "integer",
                    minimum: 0,
                    maximum: MTMC.#maxTimeWindowInMs,
                    default: MTMC.#defaultTimeWindowInMs,
                    errorMessage: {
                        type: "timeWindowInMs is not an integer.",
                        minimum: "timeWindowInMs can have a minimum value of 0.",
                        maximum: `timeWindowInMs can have a maximum value of ${MTMC.#maxTimeWindowInMs}.`
                    }
                }
            },
            required: ["timestamp", "place"],
            errorMessage: {
                required: "Input should have required properties 'timestamp', 'place'."
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }

        if (!Validator.isValidISOTimestamp(input.timestamp)) {
            throw (new InvalidInputError("Invalid timestamp."));
        }

        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let rtlsAbsent = false;
                let objectCounts = Array();
                let results = await this.#getUniqueObjectCountWithLocationsFromEs(documentDb,  { place: input.place, timestamp : input.timestamp, timeWindowInMs: input.timeWindowInMs });
                
                if (!results.indexAbsent) {
                    results = Elasticsearch.searchResultFormatter(results.body);
                    if (results.length > 0) {
                            for (let objectCount of results[0].objectCounts) {
                                objectCounts.push(objectCount);
                            }
                            let amrRecord = await this.#getAMRRecordFromES(documentDb, {
                                    place: input.place,
                                    timestampOfRTLSRecord: results[0].timestamp,
                                    amrTimestampWindowInMs: input.timeWindowInMs
                                });

                                if (amrRecord) {
                                    objectCounts.push(...amrRecord.objectCounts);
                                }
                    } else {
                        rtlsAbsent = true;
                    }
                } else {
                    rtlsAbsent = true;
                }
                if (rtlsAbsent) {
                    let amrRecord = await this.#getAMRRecordWithoutRtlsFromES(documentDb, {
                            place: input.place,
                            timestamp: input.timestamp,
                            timeWindowInMs: input.timeWindowInMs
                        });
                    
                        if (amrRecord) {
                            objectCounts.push(...amrRecord.objectCounts);
                        }
                }
                return { "trackerOccupancy": objectCounts };
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }
    
    async #getLastRecordFromEs(elasticDb, {place, source, objectType}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        let indexSuffix=null;
        if(source==="RTLS"){
            indexSuffix=Elasticsearch.getIndex("rtls");
        }else if(source==="AMR"){
            indexSuffix=Elasticsearch.getIndex("amrLocations");
        }
        const index = `${indexPrefix}${indexSuffix}`;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ prefix: { "place.keyword": place } });
        if (objectType != null) {
            queryBody.query.bool.must.push({
                nested: {
                    path: "objectCounts",
                    query: {
                        bool: {
                            must: [
                                { term: { "objectCounts.type.keyword": objectType } },
                                { range: { "objectCounts.count": { gt: 0 } } }
                            ]
                        }
                    }
                }
            });
        }
        let queryObject = {
            index,
            body: queryBody,
            sort: "timestamp:desc",
            size: 1,
            _source_excludes: ["Id", "type"]
        };
        let searchResults = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return searchResults;
    }

    /**
     * Retrieves the most recent RTLS or AMR record for a place.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.place - Place used to filter the latest record.
     * @param {string} [input.source="RTLS"] - Source used to select the latest record. source should be one of 'RTLS' or 'AMR'.
     * @param {?string} [input.objectType=null] - Object type used to filter the latest record with non-zero counts.
     * @returns {Promise<Object>} An object containing last record is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"}, databaseConfigMap);
     * let input = {place: "building=abc/room=xyz", source: "RTLS"};
     * let mtmc = new mdx.Services.MTMC();
     * let lastRecord = await mtmc.getLastRecord(elastic, input);
     */
    async getLastRecord(documentDb, input) {
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                place: {
                    type: "string",
                    minLength: 1,
                    errorMessage: {
                        minLength: "place should have atleast 1 character.",
                        maxLength: "place should have atmost 10000 characters."
                    }
                },
                source: {
                    type: "string",
                    enum: [
                        "RTLS",
                        "AMR"
                    ],
                    default: "RTLS",
                    errorMessage: {
                        "enum": "source must be one of the following values: 'RTLS' or 'AMR'."
                    }
                },
                objectType:{
                    type: ["string", "null"],
                    default: null,
                    minLength: 1,
                    errorMessage: {
                        minLength: "objectType should have atleast 1 character."
                    }
                }
            },
            required: ["place"],
            errorMessage: {
                required: "Input should have required property 'place'."
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        let lastRecord = null;
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let result = await this.#getLastRecordFromEs(documentDb, input);
                if (!result.indexAbsent) {
                    result = Elasticsearch.searchResultFormatter(result.body);
                    if (result.length>0){
                        lastRecord=result[0];
                    }
                }
                return { lastRecord };
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

}

module.exports = MTMC;

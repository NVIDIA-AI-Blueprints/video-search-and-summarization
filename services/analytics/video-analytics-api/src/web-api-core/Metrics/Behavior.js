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
const Elasticsearch = require("../Utils/Elasticsearch");
const Validator = require("../Utils/Validator");
const filterTemplate = require("../queryTemplates/filter.json");
const Calibration = require("../Services/Calibration");
const RoadNetwork = require("../Services/RoadNetwork");
const averageSpeedPerDirectionTemplate = require("../queryTemplates/averageSpeedPerDirection.json");
const flowratePerDirectionTemplate = require("../queryTemplates/flowratePerDirection.json");
const Utils = require("../Utils/Utils");
const InternalServerError = require('../Errors/InternalServerError');
const InvalidInputError = require('../Errors/InvalidInputError');
const BadRequestError = require('../Errors/BadRequestError');
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
const NodeCache = require("node-cache");
let cache = new NodeCache();

/** 
 * Class which helps calculating AverageSpeed 
 * @memberof mdxWebApiCore.Metrics
 * */

class Behavior {
    static #stationaryObjectDefaultTimeIntervalThresholdInSec = 300;
    static #stationaryObjectMaxTimeIntervalThresholdInSec = 900
    static #speedOverTimeStationaryObjectMaxTimeIntervalThresholdInSec = 500;

    static #stationaryObjectDefaultDistanceThresholdInMeters = 5;
    static #stationaryObjectMinDistanceThresholdInMeters = 10;
    static #speedOverTimeStationaryObjectMinDistanceThresholdInMeters = 5;

    static #shortLivedBehaviorDefaultTimeIntervalInSec = 3;
    static #shortLivedBehaviorMinTimeIntervalInSec = 10;
    static #speedOverTimeShortLivedBehaviorMinTimeIntervalInSec = 3;

    static #speedOverTimeMaxBehaviorResultSize = 10000;

    static #defaultFlowRateunit = "/min";
    static #defaultCorridorTravelTimeUnit = "min";
    static #maxTimeWindowSizeInSec = 900;
    static #defaultTimeWindowSizeInSec = 5;

    static #avgSpeedunit = new Map([["cartesian", "mph"], ["geo", "mph"], ["image", "pixels/sec"]]);

    /** 
      * Returns unit of AverageSpeed.
      * @public
      * @static
      * @param {string} calibrationType - Type of calibration used by the application.
      * @returns {string} Unit of Average Speed is returned
      * @example
      * let calibrationType = "image";
      * let unit = mdx.Metrics.AverageSpeed.getSpeedUnit(calibrationType);
     */
    static getSpeedUnit(calibrationType) {
        if (this.#avgSpeedunit.has(calibrationType)) {
            return this.#avgSpeedunit.get(calibrationType);
        } else {
            throw (new InvalidInputError(`Invalid calibration type: ${calibrationType}.`));
        }
    }

    async #getAverageSpeedOfEachDirectionFromEs(elasticDb, { fromTimestamp, toTimestamp, sensorId, place, sensorList, stationaryObjectMaxTimeIntervalThresholdInSec, stationaryObjectMinDistanceThresholdInMeters, shortLivedBehaviorMinTimeIntervalInSec }) {
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("behavior")}`;

        let queryBody = deepcopy(averageSpeedPerDirectionTemplate);

        queryBody.query.bool.must[0].range.timestamp.lte = toTimestamp;
        queryBody.query.bool.must[1].range.end.gte = fromTimestamp;
        queryBody.query.bool.must[2].range.timeInterval.gte = shortLivedBehaviorMinTimeIntervalInSec;
        queryBody.query.bool.must[2].range.timeInterval.lte = stationaryObjectMaxTimeIntervalThresholdInSec;
        queryBody.query.bool.must[3].range.distance.gte = stationaryObjectMinDistanceThresholdInMeters;
        if (place != null) {
            queryBody.query.bool.must.push({ prefix: { "place.name.keyword": place } });
        } else if (sensorId != null) {
            queryBody.query.bool.must.push({ term: { "sensor.id.keyword": sensorId } });
        } else if (sensorList != null) {
            let sensorIdClauses = new Array();
            for (let sensorId of sensorList) {
                sensorIdClauses.push({ term: { "sensor.id.keyword": sensorId } });
            }
            queryBody.query.bool.must.push({ bool: { should: sensorIdClauses, minimum_should_match: 1 } });
        }
        let queryObject = { index, body: queryBody, size: 0 };
        let results = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        let avgSpeedPerDirection = new Array();
        if (!results.indexAbsent) {
            results = results.body;
            if (results.hasOwnProperty("aggregations")) {
                for (let directionObject of results.aggregations.directions.buckets) {
                    let direction = directionObject.key;
                    let avgSpeedDetails = {
                        averageSpeed: directionObject.averageSpeed.value,
                        objectCount: directionObject.doc_count
                    }
                    avgSpeedPerDirection.push({ direction, avgSpeedDetails });
                }
            }
        }
        return avgSpeedPerDirection;
    }

    /**
     * Retrieves the average speed metrics per direction.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input parameters for the query.
     * @param {string} input.fromTimestamp - fromTimestamp for the query in ISO 8601 format.
     * @param {string} input.toTimestamp - toTimestamp for the query in ISO 8601 format.
     * @param {string} [input.sensorId] - Either sensorId or place should be present.
     * @param {string} [input.place] - Either sensorId or place should be present.
     * @param {number} [input.stationaryObjectMaxTimeIntervalThresholdInSec=300] - Maximum time interval threshold for stationary objects (in seconds).
     * @param {number} [input.stationaryObjectMinDistanceThresholdInMeters=5] - Minimum distance threshold for stationary objects (in meters).
     * @param {number} [input.shortLivedBehaviorMinTimeIntervalInSec=3] - Minimum time interval for short-lived behavior (in seconds).
     * @returns {Promise<Object>} Average speed metrics per direction are returned.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {place: "city=abc/intersection=xyz", fromTimestamp: "2025-01-10T11:05:10.000Z", toTimestamp: "2025-01-10T11:10:10.000Z"};
     * let behaviorMetrics = new mdx.Metrics.Behavior();
     * let result = await behaviorMetrics.getAverageSpeedPerDirection(elastic,input);
     */
    async getAverageSpeedPerDirection(documentDb, input) {
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
                    type: "string",
                    minLength: 1,
                    errorMessage: {
                        minLength: "sensorId should have atleast 1 character."
                    }
                },
                place: {
                    type: "string",
                    minLength: 1,
                    errorMessage: {
                        minLength: "place should have atleast 1 character."
                    }
                },
                stationaryObjectMaxTimeIntervalThresholdInSec: {
                    type: "integer",
                    minimum: 0,
                    maximum: Behavior.#stationaryObjectMaxTimeIntervalThresholdInSec,
                    default: Behavior.#stationaryObjectDefaultTimeIntervalThresholdInSec,
                    errorMessage: {
                        type: "stationaryObjectMaxTimeIntervalThresholdInSec is not an integer.",
                        minimum: "stationaryObjectMaxTimeIntervalThresholdInSec can have a minimum value of 0.",
                        maximum: `stationaryObjectMaxTimeIntervalThresholdInSec can have a maximum value of ${Behavior.#stationaryObjectMaxTimeIntervalThresholdInSec}.`
                    }
                },
                stationaryObjectMinDistanceThresholdInMeters: {
                    type: "integer",
                    minimum: 0,
                    maximum: Behavior.#stationaryObjectMinDistanceThresholdInMeters,
                    default: Behavior.#stationaryObjectDefaultDistanceThresholdInMeters,
                    errorMessage: {
                        type: "stationaryObjectMinDistanceThresholdInMeters is not an integer.",
                        minimum: "stationaryObjectMinDistanceThresholdInMeters can have a minimum value of 0.",
                        maximum: `stationaryObjectMinDistanceThresholdInMeters can have a maximum value of ${Behavior.#stationaryObjectMinDistanceThresholdInMeters}.`
                    }
                },
                shortLivedBehaviorMinTimeIntervalInSec: {
                    type: "integer",
                    minimum: 0,
                    maximum: Behavior.#shortLivedBehaviorMinTimeIntervalInSec,
                    default: Behavior.#shortLivedBehaviorDefaultTimeIntervalInSec,
                    errorMessage: {
                        type: "shortLivedBehaviorMinTimeIntervalInSec is not an integer.",
                        minimum: "shortLivedBehaviorMinTimeIntervalInSec can have a minimum value of 0.",
                        maximum: `shortLivedBehaviorMinTimeIntervalInSec can have a maximum value of ${Behavior.#shortLivedBehaviorMinTimeIntervalInSec}.`
                    }
                }
            },
            required: ["fromTimestamp", "toTimestamp"],
            oneOf: [
                { required: ["sensorId"] },
                { required: ["place"] }
            ],
            errorMessage: {
                required: "Input should have required properties 'fromTimestamp' and 'toTimestamp'.",
                oneOf: "Input should have either 'sensorId' or 'place'."
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
        if ("stationaryObjectMaxTimeIntervalThresholdInSec" in input && !Number.isFinite(input.stationaryObjectMaxTimeIntervalThresholdInSec)) {
            throw (new InvalidInputError("stationaryObjectMaxTimeIntervalThresholdInSec is not a finite integer."));
        }
        if ("stationaryObjectMinDistanceThresholdInMeters" in input && !Number.isFinite(input.stationaryObjectMinDistanceThresholdInMeters)) {
            throw (new InvalidInputError("stationaryObjectMinDistanceThresholdInMeters is not a finite integer."));
        }
        if ("shortLivedBehaviorMinTimeIntervalInSec" in input && !Number.isFinite(input.shortLivedBehaviorMinTimeIntervalInSec)) {
            throw (new InvalidInputError("shortLivedBehaviorMinTimeIntervalInSec is not a finite integer."));
        }

        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let calibrationObject = new Calibration();
                let [avgSpeedPerDirection, calibrationType] = await Promise.all([
                    this.#getAverageSpeedOfEachDirectionFromEs(documentDb, input),
                    calibrationObject.getCalibrationType(documentDb)
                ]);

                calibrationType = calibrationType ? calibrationType : "cartesian";
                let averageSpeedUnit = Behavior.getSpeedUnit(calibrationType);

                for (let result of avgSpeedPerDirection) {
                    result.averageSpeed = `${Math.floor(result.avgSpeedDetails.averageSpeed)} ${averageSpeedUnit}`;
                    delete result.avgSpeedDetails;
                }
                return { metrics: avgSpeedPerDirection };
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #getFlowrateOfEachDirectionFromEs(elasticDb, { toTimestamp, sensorId, place, timeWindowSizeInSec, flowrateUnit, stationaryObjectMaxTimeIntervalThresholdInSec, stationaryObjectMinDistanceThresholdInMeters, shortLivedBehaviorMinTimeIntervalInSec }) {
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("behavior")}`;
        
        let fromTimestamp = new Date(new Date(toTimestamp) - (timeWindowSizeInSec * 1000)).toISOString();

        let queryBody = deepcopy(flowratePerDirectionTemplate);

        queryBody.query.bool.must[0].range.timestamp.lte = toTimestamp;
        queryBody.query.bool.must[1].range.end.gte = fromTimestamp;
        queryBody.query.bool.must[2].range.timeInterval.gte = shortLivedBehaviorMinTimeIntervalInSec;
        queryBody.query.bool.must[2].range.timeInterval.lte = stationaryObjectMaxTimeIntervalThresholdInSec;
        queryBody.query.bool.must[3].range.distance.gte = stationaryObjectMinDistanceThresholdInMeters;

        if (place != null) {
            queryBody.query.bool.must.push({ prefix: { "place.name.keyword": place } });
        } else if (sensorId != null) {
            queryBody.query.bool.must.push({ term: { "sensor.id.keyword": sensorId } });
        }

        let queryObject = { index, body: queryBody, size: 0 };
        let results = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        let flowratePerDirection = new Array();

        if (!results.indexAbsent) {
            results = results.body;
            if (results.hasOwnProperty("aggregations")) {
                let directionCountMap = new Map();
                for (let sensorObject of results.aggregations.sensors.buckets) {
                    for (let directionObject of sensorObject.directions.buckets) {
                        if (!directionCountMap.has(directionObject.key)) {
                            directionCountMap.set(directionObject.key, new Array());
                        }
                        directionCountMap.get(directionObject.key).push(directionObject.uniqueObjectCount.value);
                    }
                }

                for (let direction of directionCountMap.keys()) {
                    let totalCount = directionCountMap.get(direction).reduce((x, y) => x + y, 0);
                    let averageCount = Math.floor(totalCount / (directionCountMap.get(direction).length));
                    let flowrate = null;
                    if (flowrateUnit === "/sec") {
                        flowrate = averageCount / timeWindowSizeInSec;
                    } else if (flowrateUnit === "/min") {
                        flowrate = averageCount * (60 / timeWindowSizeInSec);
                    } else if (flowrateUnit === "/hr") {
                        flowrate = averageCount * (60 * 60 / timeWindowSizeInSec);
                    }
                    flowratePerDirection.push({ direction, flowrate });
                }
            }
        }
        return flowratePerDirection;
    }

    /**
     * Retrieves the flowrate metrics per direction.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input parameters for the query.
     * @param {string} input.toTimestamp - toTimestamp for the query in ISO 8601 format.
     * @param {string} [input.sensorId] - SensorID for filtering data. Required if `place` is not provided.
     * @param {string} [input.place] - Place for filtering data. Required if `sensorId` is not provided.
     * @param {number} [input.timeWindowSizeInSec=5] - The size of the time window (in seconds) for calculating flowrate.
     * @param {string} [input.flowrateUnit="/min"] - flowrateUnit should be one of '/sec', '/min' or '/hr'.
     * @param {number} [input.stationaryObjectMaxTimeIntervalThresholdInSec=300] - Maximum time interval threshold for stationary objects (in seconds).
     * @param {number} [input.stationaryObjectMinDistanceThresholdInMeters=5] - Minimum distance threshold for stationary objects (in meters).
     * @param {number} [input.shortLivedBehaviorMinTimeIntervalInSec=3] - Minimum time interval for short-lived behavior (in seconds).
     * @returns {Promise<Object>} Flowrate metrics per direction are returned.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {place: "city=abc/intersection=xyz", toTimestamp: "2025-01-10T11:10:05.000Z"};
     * let behaviorMetrics = new mdx.Metrics.Behavior();
     * let result = await behaviorMetrics.getFlowratePerDirection(elastic,input);
     */
    async getFlowratePerDirection(documentDb, input) {
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                toTimestamp: {
                    type: "string"
                },
                sensorId: {
                    type: "string",
                    minLength: 1,
                    errorMessage: {
                        minLength: "sensorId should have atleast 1 character.",
                        maxLength: "sensorId should have atmost 10000 characters."
                    }
                },
                place: {
                    type: "string",
                    minLength: 1,
                    errorMessage: {
                        minLength: "place should have atleast 1 character.",
                        maxLength: "place should have atmost 10000 characters."
                    }
                },
                timeWindowSizeInSec: {
                    type: "integer",
                    minimum: 0,
                    maximum: Behavior.#maxTimeWindowSizeInSec,
                    default: Behavior.#defaultTimeWindowSizeInSec,
                    errorMessage: {
                        type: "timeWindowSizeInSec is not an integer.",
                        minimum: "timeWindowSizeInSec can have a minimum value of 0.",
                        maximum: `timeWindowSizeInSec can have a maximum value of ${Behavior.#maxTimeWindowSizeInSec}.`
                    }
                },
                flowrateUnit: {
                    type: "string",
                    enum: [
                        "/sec", 
                        "/min", 
                        "/hr"
                    ],
                    default: Behavior.#defaultFlowRateunit,
                    errorMessage: {
                        enum: "flowrateUnit must be one of the following values: '/sec', '/min', '/hr'."
                    }
                },
                shortLivedBehaviorMinTimeIntervalInSec: {
                    type: "integer",
                    minimum: 0,
                    maximum: Behavior.#shortLivedBehaviorMinTimeIntervalInSec,
                    default: Behavior.#shortLivedBehaviorDefaultTimeIntervalInSec,
                    errorMessage: {
                        type: "shortLivedBehaviorMinTimeIntervalInSec is not an integer.",
                        minimum: "shortLivedBehaviorMinTimeIntervalInSec can have a minimum value of 0.",
                        maximum: `shortLivedBehaviorMinTimeIntervalInSec can have a maximum value of ${Behavior.#shortLivedBehaviorMinTimeIntervalInSec}.`
                    }
                },
                stationaryObjectMaxTimeIntervalThresholdInSec: {
                    type: "integer",
                    minimum: 0,
                    maximum: Behavior.#stationaryObjectMaxTimeIntervalThresholdInSec,
                    default: Behavior.#stationaryObjectDefaultTimeIntervalThresholdInSec,
                    errorMessage: {
                        type: "stationaryObjectMaxTimeIntervalThresholdInSec is not an integer.",
                        minimum: "stationaryObjectMaxTimeIntervalThresholdInSec can have a minimum value of 0.",
                        maximum: `stationaryObjectMaxTimeIntervalThresholdInSec can have a maximum value of ${Behavior.#stationaryObjectMaxTimeIntervalThresholdInSec}.`
                    }
                },
                stationaryObjectMinDistanceThresholdInMeters: {
                    type: "integer",
                    minimum: 0,
                    maximum: Behavior.#stationaryObjectMinDistanceThresholdInMeters,
                    default: Behavior.#stationaryObjectDefaultDistanceThresholdInMeters,
                    errorMessage: {
                        type: "stationaryObjectMinDistanceThresholdInMeters is not an integer.",
                        minimum: "stationaryObjectMinDistanceThresholdInMeters can have a minimum value of 0.",
                        maximum: `stationaryObjectMinDistanceThresholdInMeters can have a maximum value of ${Behavior.#stationaryObjectMinDistanceThresholdInMeters}.`
                    }
                }
            },
            required: ["toTimestamp"],
            oneOf: [
                { required: ["sensorId"] },
                { required: ["place"] }
            ],
            errorMessage: {
                required: "Input should have required properties 'toTimestamp'.",
                oneOf: "Input should have either 'sensorId' or 'place'."
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        if (!Validator.isValidISOTimestamp(input.toTimestamp)) {
            throw (new InvalidInputError("Invalid toTimestamp."));
        }
        if ("timeWindowSizeInSec" in input && !Number.isFinite(input.timeWindowSizeInSec)) {
            throw (new InvalidInputError("timeWindowSizeInSec is not a finite integer."));
        }
        if ("stationaryObjectMaxTimeIntervalThresholdInSec" in input && !Number.isFinite(input.stationaryObjectMaxTimeIntervalThresholdInSec)) {
            throw (new InvalidInputError("stationaryObjectMaxTimeIntervalThresholdInSec is not a finite integer."));
        }
        if ("stationaryObjectMinDistanceThresholdInMeters" in input && !Number.isFinite(input.stationaryObjectMinDistanceThresholdInMeters)) {
            throw (new InvalidInputError("stationaryObjectMinDistanceThresholdInMeters is not a finite integer."));
        }
        if ("shortLivedBehaviorMinTimeIntervalInSec" in input && !Number.isFinite(input.shortLivedBehaviorMinTimeIntervalInSec)) {
            throw (new InvalidInputError("shortLivedBehaviorMinTimeIntervalInSec is not a finite integer."));
        }

        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let flowratePerDirection = await this.#getFlowrateOfEachDirectionFromEs(documentDb, input);

                for (let result of flowratePerDirection) {
                    result.flowrate = `${result.flowrate} ${input.flowrateUnit}`;
                }

                return { metrics: flowratePerDirection };
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    /**
     * Retrieves the combined average speed and flowrate metrics per direction.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input parameters for the query.
     * @param {string} input.fromTimestamp - fromTimestamp for the query in ISO 8601 format.
     * @param {string} input.toTimestamp - toTimestamp for the query in ISO 8601 format.
     * @param {string} [input.sensorId] - SensorID for filtering data. Required if `place` is not provided.
     * @param {string} [input.place] - Place for filtering data. Required if `sensorId` is not provided.
     * @param {number} [input.timeWindowSizeInSec=5] - The size of the time window (in seconds) for calculating flowrate.
     * @param {string} [input.flowrateUnit="/min"] - The unit of the flowrate, e.g., "/sec", "/min", "/hr".
     * @param {number} [input.stationaryObjectMaxTimeIntervalThresholdInSec=300] - Maximum time interval threshold for stationary objects (in seconds).
     * @param {number} [input.stationaryObjectMinDistanceThresholdInMeters=5] - Minimum distance threshold for stationary objects (in meters).
     * @param {number} [input.shortLivedBehaviorMinTimeIntervalInSec=3] - Minimum time interval for short-lived behavior (in seconds).
     * @returns {Promise<Object>} Combined average speed and flowrate metrics per direction are returned.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {place: "city=abc/intersection=xyz", fromTimestamp: "2025-01-10T10:00:00.000Z", toTimestamp: "2025-01-10T11:10:05.000Z"};
     * let behaviorMetrics = new mdx.Metrics.Behavior();
     * let result = await behaviorMetrics.getAverageSpeedWithFlowrate(elastic,input);
     */
    async getAverageSpeedWithFlowrate(documentDb, input) {
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
                    type: "string",
                    minLength: 1,
                    errorMessage: {
                        minLength: "sensorId should have atleast 1 character."
                    }
                },
                place: {
                    type: "string",
                    minLength: 1,
                    errorMessage: {
                        minLength: "place should have atleast 1 character."
                    }
                },
                timeWindowSizeInSec: {
                    type: "integer",
                    minimum: 0,
                    maximum: Behavior.#maxTimeWindowSizeInSec,
                    default: Behavior.#defaultTimeWindowSizeInSec,
                    errorMessage: {
                        type: "timeWindowSizeInSec is not an integer.",
                        minimum: "timeWindowSizeInSec can have a minimum value of 0.",
                        maximum: `timeWindowSizeInSec can have a maximum value of ${Behavior.#maxTimeWindowSizeInSec}.`
                    }
                },
                flowrateUnit: {
                    type: "string",
                    enum: [
                        "/sec", 
                        "/min", 
                        "/hr"
                    ],
                    default: Behavior.#defaultFlowRateunit,
                    errorMessage: {
                        enum: "flowrateUnit must be one of the following values: '/sec', '/min', '/hr'."
                    }
                },
                stationaryObjectMaxTimeIntervalThresholdInSec: {
                    type: "integer",
                    minimum: 0,
                    maximum: Behavior.#stationaryObjectMaxTimeIntervalThresholdInSec,
                    default: Behavior.#stationaryObjectDefaultTimeIntervalThresholdInSec,
                    errorMessage: {
                        type: "stationaryObjectMaxTimeIntervalThresholdInSec is not an integer.",
                        minimum: "stationaryObjectMaxTimeIntervalThresholdInSec can have a minimum value of 0.",
                        maximum: `stationaryObjectMaxTimeIntervalThresholdInSec can have a maximum value of ${Behavior.#stationaryObjectMaxTimeIntervalThresholdInSec}.`
                    }
                },
                stationaryObjectMinDistanceThresholdInMeters: {
                    type: "integer",
                    minimum: 0,
                    maximum: Behavior.#stationaryObjectMinDistanceThresholdInMeters,
                    default: Behavior.#stationaryObjectDefaultDistanceThresholdInMeters,
                    errorMessage: {
                        type: "stationaryObjectMinDistanceThresholdInMeters is not an integer.",
                        minimum: "stationaryObjectMinDistanceThresholdInMeters can have a minimum value of 0.",
                        maximum: `stationaryObjectMinDistanceThresholdInMeters can have a maximum value of ${Behavior.#stationaryObjectMinDistanceThresholdInMeters}.`
                    }
                },
                shortLivedBehaviorMinTimeIntervalInSec: {
                    type: "integer",
                    minimum: 0,
                    maximum: Behavior.#shortLivedBehaviorMinTimeIntervalInSec,
                    default: Behavior.#shortLivedBehaviorDefaultTimeIntervalInSec,
                    errorMessage: {
                        type: "shortLivedBehaviorMinTimeIntervalInSec is not an integer.",
                        minimum: "shortLivedBehaviorMinTimeIntervalInSec can have a minimum value of 0.",
                        maximum: `shortLivedBehaviorMinTimeIntervalInSec can have a maximum value of ${Behavior.#shortLivedBehaviorMinTimeIntervalInSec}.`
                    }
                }
            },
            required: ["fromTimestamp", "toTimestamp"],
            oneOf: [
                { required: ["sensorId"] },
                { required: ["place"] }
            ],
            errorMessage: {
                required: "Input should have required properties 'fromTimestamp' and 'toTimestamp'.",
                oneOf: "Input should have either 'sensorId' or 'place'."
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
        if ("timeWindowSizeInSec" in input && !Number.isFinite(input.timeWindowSizeInSec)) {
            throw (new InvalidInputError("timeWindowSizeInSec is not a finite integer."));
        }
        if ("stationaryObjectMaxTimeIntervalThresholdInSec" in input && !Number.isFinite(input.stationaryObjectMaxTimeIntervalThresholdInSec)) {
            throw (new InvalidInputError("stationaryObjectMaxTimeIntervalThresholdInSec is not a finite integer."));
        }
        if ("stationaryObjectMinDistanceThresholdInMeters" in input && !Number.isFinite(input.stationaryObjectMinDistanceThresholdInMeters)) {
            throw (new InvalidInputError("stationaryObjectMinDistanceThresholdInMeters is not a finite integer."));
        }
        if ("shortLivedBehaviorMinTimeIntervalInSec" in input && !Number.isFinite(input.shortLivedBehaviorMinTimeIntervalInSec)) {
            throw (new InvalidInputError("shortLivedBehaviorMinTimeIntervalInSec is not a finite integer."));
        }

        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let [avgSpeedPerDirection, flowratePerDirection, calibrationType] = await Promise.all([
                    this.#getAverageSpeedOfEachDirectionFromEs(documentDb, { fromTimestamp: input.fromTimestamp, toTimestamp: input.toTimestamp, sensorId: input.sensorId, place: input.place, stationaryObjectMaxTimeIntervalThresholdInSec: input.stationaryObjectMaxTimeIntervalThresholdInSec, stationaryObjectMinDistanceThresholdInMeters: input.stationaryObjectMinDistanceThresholdInMeters, shortLivedBehaviorMinTimeIntervalInSec: input.shortLivedBehaviorMinTimeIntervalInSec }),
                    this.#getFlowrateOfEachDirectionFromEs(documentDb, { toTimestamp: input.toTimestamp, sensorId: input.sensorId, place: input.place, timeWindowSizeInSec: input.timeWindowSizeInSec, flowrateUnit: input.flowrateUnit, stationaryObjectMaxTimeIntervalThresholdInSec: input.stationaryObjectMaxTimeIntervalThresholdInSec, stationaryObjectMinDistanceThresholdInMeters: input.stationaryObjectMinDistanceThresholdInMeters, shortLivedBehaviorMinTimeIntervalInSec: input.shortLivedBehaviorMinTimeIntervalInSec }),
                    new Calibration().getCalibrationType(documentDb)
                ]);
                let avgSpeedMap = new Map(avgSpeedPerDirection.map(item => [item.direction, item]));
                let flowrateMap = new Map(flowratePerDirection.map(item => [item.direction, item]));
                
                let avgSpeedDirections = new Set(avgSpeedMap.keys());
                let flowrateDirections = new Set(flowrateMap.keys());

                let commonDirections = new Set([...avgSpeedDirections].filter(i => flowrateDirections.has(i)));
                let avgSpeedExclusiveDir = Utils.setDifference(avgSpeedDirections, flowrateDirections);
                let flowrateExclusiveDir = Utils.setDifference(flowrateDirections, avgSpeedDirections);

                let resultList = [];

                for (let direction of commonDirections) {
                    let avgSpeedResult = avgSpeedMap.get(direction);
                    let flowrateResult = flowrateMap.get(direction);
                    
                    let result = { direction, ...avgSpeedResult.avgSpeedDetails, flowrate: flowrateResult.flowrate };
                    resultList.push(result);
                }

                for (let direction of avgSpeedExclusiveDir) {
                    let avgSpeedResult = avgSpeedMap.get(direction);
                    let result = { direction, ...avgSpeedResult.avgSpeedDetails, flowrate: 0 };
                    resultList.push(result);
                }

                for (let direction of flowrateExclusiveDir) {
                    let flowrateResult = flowrateMap.get(direction);
                    let result = { direction, averageSpeed: 0, objectCount: 0, flowrate: flowrateResult.flowrate };
                    resultList.push(result);
                }

                resultList.sort((obj1, obj2) => obj2.objectCount - obj1.objectCount);
                
                calibrationType = calibrationType ? calibrationType : "cartesian";

                let averageSpeedUnit = Behavior.getSpeedUnit(calibrationType);
                let flowrateUnit = input.flowrateUnit;
                for (let result of resultList) {
                    delete result.objectCount;
                    result.flowrate = `${result.flowrate} ${flowrateUnit}`;
                    result.averageSpeed = `${Math.floor(result.averageSpeed)} ${averageSpeedUnit}`;
                }
                return { metrics: resultList };
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }
    
    /**
     * Retrieves the combined average speed and travel time metrics for corridor directions.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input parameters for the query.
     * @param {string} input.fromTimestamp - fromTimestamp for the query in ISO 8601 format.
     * @param {string} input.toTimestamp - toTimestamp for the query in ISO 8601 format.
     * @param {string} input.place - Place used to filter corridor metrics. Supported hierarchy is city/corridor.
     * @param {string} [input.corridorTravelTimeUnit="min"] - The unit of the travel time, e.g., "sec", "min", "hr".
     * @param {number} [input.stationaryObjectMaxTimeIntervalThresholdInSec=300] - Maximum time interval threshold for stationary objects (in seconds).
     * @param {number} [input.stationaryObjectMinDistanceThresholdInMeters=5] - Minimum distance threshold for stationary objects (in meters).
     * @param {number} [input.shortLivedBehaviorMinTimeIntervalInSec=3] - Minimum time interval for short-lived behavior (in seconds).
     * @returns {Promise<Object>} Combined average speed and travel time metrics for corridor directions are returned.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {place: "city=abc/corridor=xyz", fromTimestamp: "2023-01-10T12:00:00.000Z", toTimestamp: "2023-01-10T12:30:00.000Z"};
     * let behaviorMetrics = new mdx.Metrics.Behavior();
     * let result = await behaviorMetrics.getAverageSpeedWithTravelTime(elastic,input);
     */
    async getAverageSpeedWithTravelTime(documentDb, input) {
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
                        minLength: "place should have atleast 1 character."
                    }
                },
                corridorTravelTimeUnit: {
                    type: "string",
                    enum: [
                        "sec", 
                        "min", 
                        "hr"
                    ],
                    default: Behavior.#defaultCorridorTravelTimeUnit,
                    errorMessage: {
                        enum: "corridorTravelTimeUnit must be one of the following values: '/sec', '/min', '/hr'."
                    }
                },
                stationaryObjectMaxTimeIntervalThresholdInSec: {
                    type: "integer",
                    minimum: 0,
                    maximum: Behavior.#stationaryObjectMaxTimeIntervalThresholdInSec,
                    default: Behavior.#stationaryObjectDefaultTimeIntervalThresholdInSec,
                    errorMessage: {
                        type: "stationaryObjectMaxTimeIntervalThresholdInSec is not an integer.",
                        minimum: "stationaryObjectMaxTimeIntervalThresholdInSec can have a minimum value of 0.",
                        maximum: `stationaryObjectMaxTimeIntervalThresholdInSec can have a maximum value of ${Behavior.#stationaryObjectMaxTimeIntervalThresholdInSec}.`
                    }
                },
                stationaryObjectMinDistanceThresholdInMeters: {
                    type: "integer",
                    minimum: 0,
                    maximum: Behavior.#stationaryObjectMinDistanceThresholdInMeters,
                    default: Behavior.#stationaryObjectDefaultDistanceThresholdInMeters,
                    errorMessage: {
                        type: "stationaryObjectMinDistanceThresholdInMeters is not an integer.",
                        minimum: "stationaryObjectMinDistanceThresholdInMeters can have a minimum value of 0.",
                        maximum: `stationaryObjectMinDistanceThresholdInMeters can have a maximum value of ${Behavior.#stationaryObjectMinDistanceThresholdInMeters}.`
                    }
                },
                shortLivedBehaviorMinTimeIntervalInSec: {
                    type: "integer",
                    minimum: 0,
                    maximum: Behavior.#shortLivedBehaviorMinTimeIntervalInSec,
                    default: Behavior.#shortLivedBehaviorDefaultTimeIntervalInSec,
                    errorMessage: {
                        type: "shortLivedBehaviorMinTimeIntervalInSec is not an integer.",
                        minimum: "shortLivedBehaviorMinTimeIntervalInSec can have a minimum value of 0.",
                        maximum: `shortLivedBehaviorMinTimeIntervalInSec can have a maximum value of ${Behavior.#shortLivedBehaviorMinTimeIntervalInSec}.`
                    }
                }
            },
            required: ["fromTimestamp", "toTimestamp", "place"],
            errorMessage: {
                required: "Input should have required properties 'fromTimestamp' and 'toTimestamp' and 'place'."
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
        if ("stationaryObjectMaxTimeIntervalThresholdInSec" in input && !Number.isFinite(input.stationaryObjectMaxTimeIntervalThresholdInSec)) {
            throw (new InvalidInputError("stationaryObjectMaxTimeIntervalThresholdInSec is not a finite integer."));
        }
        if ("stationaryObjectMinDistanceThresholdInMeters" in input && !Number.isFinite(input.stationaryObjectMinDistanceThresholdInMeters)) {
            throw (new InvalidInputError("stationaryObjectMinDistanceThresholdInMeters is not a finite integer."));
        }
        if ("shortLivedBehaviorMinTimeIntervalInSec" in input && !Number.isFinite(input.shortLivedBehaviorMinTimeIntervalInSec)) {
            throw (new InvalidInputError("shortLivedBehaviorMinTimeIntervalInSec is not a finite integer."));
        }

        let calibrationObject = new Calibration();
        let { timestamp, calibration } = await calibrationObject.getCalibration(documentDb);
        let cachedCalibrationTimestamp = cache.get("calibration-timestamp");
        
        let inputPlaceHierarchy = Utils.getPlaceHierarchy(input.place);
        if (inputPlaceHierarchy !== "city/corridor") {
            throw (new InvalidInputError(`Unsupported place hierarchy: ${inputPlaceHierarchy}.`));
        }

        if (cachedCalibrationTimestamp == undefined || Utils.tsCompare(timestamp, ">", cachedCalibrationTimestamp)) {
            let calibrationMaps = calibrationObject.getCalibrationMaps(calibration);
            cache.set("placeHierarchyMap",calibrationMaps.placeHierarchyMap);
            cache.set("sensorPlaceMap",calibrationMaps.sensorPlaceMap);
            cache.set("corridorInfoMap", calibrationMaps.corridorInfoMap);
            cache.set("calibration-timestamp", timestamp);
        }

        let cachedCorridorInfoMap = cache.get("corridorInfoMap");
        if (cachedCorridorInfoMap == null) {
            return { metrics: new Array() };
        }
        let corridorInfo = cachedCorridorInfoMap.get(input.place);
        if (corridorInfo == null) {
            return { metrics: new Array() };
        }

        let [avgSpeedPerDirection, calibrationType] = await Promise.all([
            this.#getAverageSpeedOfEachDirectionFromEs(documentDb, { fromTimestamp: input.fromTimestamp, toTimestamp: input.toTimestamp, sensorList: corridorInfo.sensors, stationaryObjectMaxTimeIntervalThresholdInSec: input.stationaryObjectMaxTimeIntervalThresholdInSec, stationaryObjectMinDistanceThresholdInMeters: input.stationaryObjectMinDistanceThresholdInMeters, shortLivedBehaviorMinTimeIntervalInSec: input.shortLivedBehaviorMinTimeIntervalInSec }),
            calibrationObject.getCalibrationType(documentDb)
        ]);

        let avgSpeedPerDirectionMap = new Map(avgSpeedPerDirection.map(item => [item.direction, item]));

        let corridorDirections = new Set(corridorInfo.directions);
        let resultList = new Array();
        for (let direction of corridorDirections) {
            if (avgSpeedPerDirectionMap.has(direction)) {
                let avgSpeedResult = avgSpeedPerDirectionMap.get(direction);
                let result = { direction: direction,  ...avgSpeedResult.avgSpeedDetails };
                resultList.push(result);
            }
        }
        resultList.sort((obj1, obj2) => obj2.objectCount - obj1.objectCount);

        let corridorTravelTimeUnit = input.corridorTravelTimeUnit;
        calibrationType = calibrationType ? calibrationType : "image";

        let averageSpeedUnit = Behavior.getSpeedUnit(calibrationType);
        for (let result of resultList) {
            delete result.objectCount;
            if (corridorTravelTimeUnit === "hr") {
                result.corridorTravelTime = `${Math.ceil(((corridorInfo.length) / result.averageSpeed))} ${corridorTravelTimeUnit}`;
            } else if (corridorTravelTimeUnit === "min") {
                result.corridorTravelTime = `${Math.ceil(((corridorInfo.length) / result.averageSpeed) * 60)} ${corridorTravelTimeUnit}`;
            } else if (corridorTravelTimeUnit === "sec") {
                result.corridorTravelTime = `${Math.ceil(((corridorInfo.length) / result.averageSpeed) * 60 * 60)} ${corridorTravelTimeUnit}`;
            }
            result.averageSpeed = `${Math.floor(result.averageSpeed)} ${averageSpeedUnit}`;
        }
        return { metrics: resultList };
    }

    async #getBehaviorsToCalculateSpeedOverTimeFromES(elasticDb, {place, fromTimestamp, toTimestamp, objectTypes, requireBehaviorsToHaveEdges, shortLivedBehaviorMinTimeIntervalInSec, stationaryObjectMaxTimeIntervalThresholdInSec, stationaryObjectMinDistanceThresholdInMeters, maxResultSize}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("behavior")}`;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ prefix: { "place.name.keyword": place } });
        queryBody.query.bool.must.push({ range: { timestamp: { lte: toTimestamp } } });
        queryBody.query.bool.must.push({ range: { end: { gte: fromTimestamp } } });
        queryBody.query.bool.must.push({
            range: {
                timeInterval: {
                    gte: shortLivedBehaviorMinTimeIntervalInSec,
                    lte: stationaryObjectMaxTimeIntervalThresholdInSec
                }
            }
        });
        queryBody.query.bool.must.push({ range: { distance: { gte: stationaryObjectMinDistanceThresholdInMeters } } });
        let objectTypeClauses = new Array();
        for (let objectType of objectTypes) {
            objectTypeClauses.push({ term: { "object.type.keyword": objectType } });
        }
        queryBody.query.bool.must.push({ bool: { should: objectTypeClauses, minimum_should_match: 1 } });
        if(requireBehaviorsToHaveEdges){
            queryBody.query.bool.filter={
                script:{
                    script: {
                        source: "doc.containsKey('edges') ? doc['edges.keyword'].length > 0 : false",
                        lang: "painless"
                    }
                }
            };
        } 
        let queryObject = {
            index, 
            body: queryBody,
            size: maxResultSize,
            _source_includes: [
                'Id', 'timeInterval', 'timestamp', 'end', 'speedOverTime',
                'sensor.id', 'place.name', 'edges', 'direction', 'speed', "object.id", "object.type"
            ]
        }
        let results = await Elasticsearch.getSearchResults(elasticDb.getClient(),queryObject,false);
        return results;
    }

    /**
     * Retrieves the behaviors with current average speed.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input parameters for the query.
     * @param {string} input.place - Place for filtering data.
     * @param {string} input.fromTimestamp - fromTimestamp for the query in ISO 8601 format.
     * @param {string} input.toTimestamp - toTimestamp for the query in ISO 8601 format.
     * @param {Array<string>} [input.objectTypes=["Vehicle"]] - Object types to filter data.
     * @param {boolean} [input.requireBehaviorsToHaveEdges=false] - Whether to require behaviors to have edges.
     * @param {number} [input.expectedAvgSpeedOfObjects=45] - Expected average speed of objects.
     * @param {number} [input.stationaryObjectMaxTimeIntervalThresholdInSec=500] - Maximum time interval threshold for stationary objects (in seconds).
     * @param {number} [input.stationaryObjectMinDistanceThresholdInMeters=5] - Minimum distance threshold for stationary objects (in meters).
     * @param {number} [input.shortLivedBehaviorMinTimeIntervalInSec=3] - Minimum time interval for short-lived behavior (in seconds).
     * @param {number} [input.maxResultSize=10000] - Maximum number of behaviors returned.
     * @returns {Promise<Object>} Behaviors with current average speed are returned.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {place: "city=abc/intersection=xyz", fromTimestamp: "2025-01-10T11:05:10.000Z", toTimestamp: "2025-01-10T11:10:10.000Z"};
     * let behaviorMetrics = new mdx.Metrics.Behavior();
     * let result = await behaviorMetrics.getCurrentBehaviorSpeed(elastic,input);
     */
    async getCurrentBehaviorSpeed(documentDb, input){
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
                        minLength: "place should have atleast 1 character."
                    }
                },
                fromTimestamp: {
                    type: "string"
                },
                toTimestamp: {
                    type: "string"
                },
                objectTypes: {
                    type : "array",
                    default: ["Vehicle"],
                    items: {
                        type: "string",
                        minLength: 1,
                        maxLength: 10000,
                        errorMessage: {
                            minLength: "Element of objectTypes array should have atleast 1 character.",
                            maxLength: "Element of objectTypes array should have atmost 10000 characters."                            
                        }
                    },
                    minItems: 1,
                    errorMessage: {
                        minItems: "objectTypes should have atleast 1 item"
                    }
                },
                requireBehaviorsToHaveEdges: {
                    type: "boolean",
                    default: false,
                    errorMessage: {
                        type: "requireBehaviorsToHaveEdges doesn't have a boolean value.",
                    }
                },
                expectedAvgSpeedOfObjects:{
                    type: "number",
                    default: 45
                },
                stationaryObjectMaxTimeIntervalThresholdInSec: {
                    type: "integer",
                    minimum: 0,
                    maximum: Behavior.#speedOverTimeStationaryObjectMaxTimeIntervalThresholdInSec,
                    default: Behavior.#speedOverTimeStationaryObjectMaxTimeIntervalThresholdInSec,
                    errorMessage: {
                        type: "stationaryObjectMaxTimeIntervalThresholdInSec is not an integer.",
                        minimum: "stationaryObjectMaxTimeIntervalThresholdInSec can have a minimum value of 0.",
                        maximum: `stationaryObjectMaxTimeIntervalThresholdInSec can have a maximum value of ${Behavior.#speedOverTimeStationaryObjectMaxTimeIntervalThresholdInSec}.`
                    }
                },
                stationaryObjectMinDistanceThresholdInMeters: {
                    type: "integer",
                    minimum: 0,
                    maximum: Behavior.#speedOverTimeStationaryObjectMinDistanceThresholdInMeters,
                    default: Behavior.#speedOverTimeStationaryObjectMinDistanceThresholdInMeters,
                    errorMessage: {
                        type: "stationaryObjectMinDistanceThresholdInMeters is not an integer.",
                        minimum: "stationaryObjectMinDistanceThresholdInMeters can have a minimum value of 0.",
                        maximum: `stationaryObjectMinDistanceThresholdInMeters can have a maximum value of ${Behavior.#speedOverTimeStationaryObjectMinDistanceThresholdInMeters}.`
                    }
                },
                shortLivedBehaviorMinTimeIntervalInSec: {
                    type: "integer",
                    minimum: 0,
                    maximum: Behavior.#speedOverTimeShortLivedBehaviorMinTimeIntervalInSec,
                    default: Behavior.#speedOverTimeShortLivedBehaviorMinTimeIntervalInSec,
                    errorMessage: {
                        type: "shortLivedBehaviorMinTimeIntervalInSec is not an integer.",
                        minimum: "shortLivedBehaviorMinTimeIntervalInSec can have a minimum value of 0.",
                        maximum: `shortLivedBehaviorMinTimeIntervalInSec can have a maximum value of ${Behavior.#speedOverTimeShortLivedBehaviorMinTimeIntervalInSec}.`
                    }
                },
                maxResultSize: {
                    type: "integer",
                    minimum: 1,
                    maximum: Behavior.#speedOverTimeMaxBehaviorResultSize,
                    default: Behavior.#speedOverTimeMaxBehaviorResultSize,
                    errorMessage: {
                        type: "maxResultSize is not an integer.",
                        minimum: "maxResultSize can have a minimum value of 1.",
                        maximum: `maxResultSize can have a maximum value of ${Behavior.#speedOverTimeMaxBehaviorResultSize}.`
                    }
                }
            },
            required: ["place", "fromTimestamp", "toTimestamp"],
            errorMessage: {
                required: "Input should have required properties 'place', 'fromTimestamp' and 'toTimestamp'."
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
        if (!Number.isFinite(input.expectedAvgSpeedOfObjects)) {
            throw (new InvalidInputError("'expectedAvgSpeedOfObjects' is not a finite number."));
        }
        if (!Number.isFinite(input.maxResultSize)) {
            throw (new InvalidInputError("maxResultSize is not a finite integer."));
        }
        if ("stationaryObjectMaxTimeIntervalThresholdInSec" in input && !Number.isFinite(input.stationaryObjectMaxTimeIntervalThresholdInSec)) {
            throw (new InvalidInputError("stationaryObjectMaxTimeIntervalThresholdInSec is not a finite integer."));
        }
        if ("stationaryObjectMinDistanceThresholdInMeters" in input && !Number.isFinite(input.stationaryObjectMinDistanceThresholdInMeters)) {
            throw (new InvalidInputError("stationaryObjectMinDistanceThresholdInMeters is not a finite integer."));
        }
        if ("shortLivedBehaviorMinTimeIntervalInSec" in input && !Number.isFinite(input.shortLivedBehaviorMinTimeIntervalInSec)) {
            throw (new InvalidInputError("shortLivedBehaviorMinTimeIntervalInSec is not a finite integer."));
        }

        let behaviors = new Array();
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let results = await this.#getBehaviorsToCalculateSpeedOverTimeFromES(documentDb, input);
                if (!results.indexAbsent) {
                    behaviors = Elasticsearch.searchResultFormatter(results.body);
                    for (let behavior of behaviors) {
                        let currentAvgSpeed = null;
                        let tsComparisonResult = Utils.tsCompare(input.toTimestamp, ">" ,behavior.end);
                        let behaviorLength = behavior.speedOverTime.length;
                        if (tsComparisonResult) {
                            currentAvgSpeed = behavior.speedOverTime[behaviorLength - 1];
                        } else {
                            let currentBehaviorTimeInterval = (new Date(input.toTimestamp) - new Date(behavior.timestamp)) / 1000;
                            let speedIndex = Math.ceil((behaviorLength * currentBehaviorTimeInterval) / behavior.timeInterval) - 1;
                            currentAvgSpeed = behavior.speedOverTime[speedIndex];
                        }
                        if (behavior.speed > input.expectedAvgSpeedOfObjects) {
                            if (currentAvgSpeed >= 2 * behavior.speed) {
                                currentAvgSpeed = behavior.speed
                            }
                        }
                        behavior.currentAvgSpeed = currentAvgSpeed;
                    }
                }
                return behaviors;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    /**
     * Returns road segment speeds for a city or intersection place.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input parameters for the query.
     * @param {string} input.place - Supported hierarchies are city and city/intersection.
     * @param {string} input.fromTimestamp - fromTimestamp for the query in ISO 8601 format.
     * @param {string} input.toTimestamp - toTimestamp for the query in ISO 8601 format.
     * @param {Array<string>} [input.objectTypes=["Vehicle"]] - Object types to filter data.
     * @param {boolean} [input.segmentInfo=false] - Whether to return segment information.
     * @returns {Promise<Object>} Road segment speeds are returned.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {place: "city=abc/intersection=xyz", fromTimestamp: "2025-01-10T11:05:10.000Z", toTimestamp: "2025-01-10T11:10:10.000Z"};
     * let behaviorMetrics = new mdx.Metrics.Behavior();
     * let result = await behaviorMetrics.getRoadSegmentSpeed(elastic,input);
     */
    async getRoadSegmentSpeed(documentDb, input){
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
                        minLength: "place should have atleast 1 character."
                    }
                },
                fromTimestamp: {
                    type: "string"
                },
                toTimestamp: {
                    type: "string"
                },
                objectTypes: {
                    type: "array",
                    default: ["Vehicle"],
                    items: {
                        type: "string",
                        minLength: 1,
                        maxLength: 10000,
                        errorMessage: {
                            minLength: "Element of objectTypes array should have atleast 1 character.",
                            maxLength: "Element of objectTypes array should have atmost 10000 characters."                            
                        }
                    },
                    minItems: 1,
                    errorMessage: {
                        minItems: "objectTypes should have atleast 1 item"
                    }
                },
                segmentInfo: {
                    type: "boolean",
                    default: false,
                    errorMessage: {
                        type: "segmentInfo doesn't have a boolean value.",
                    }
                }
            },
            required: ["place", "fromTimestamp", "toTimestamp"],
            errorMessage: {
                required: "Input should have required properties 'place', 'fromTimestamp' and 'toTimestamp'."
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

        const supportedPlaces = new Set(["city","city/intersection"]);
        let inputPlaceHierarchy = Utils.getPlaceHierarchy(input.place);
        if (!supportedPlaces.has(inputPlaceHierarchy)) {
            throw(new InvalidInputError(`Unsupported place hierarchy: ${inputPlaceHierarchy}.`));
        }
        let calibrationObject = new Calibration();
        let roadNetworkObject = new RoadNetwork();
        let [roadNetworkResponse, calibrationResponse, behaviorsWithCurrentSpeed] = await Promise.all([
            roadNetworkObject.getRoadNetwork(documentDb),
            calibrationObject.getCalibration(documentDb),
            this.getCurrentBehaviorSpeed(documentDb, {place: input.place,fromTimestamp: input.fromTimestamp,toTimestamp: input.toTimestamp, objectTypes: input.objectTypes, requireBehaviorsToHaveEdges: true})
        ]);

        let {timestamp,calibration} = calibrationResponse;
        let cachedCalibrationTimestamp = cache.get("calibration-timestamp");
        if(cachedCalibrationTimestamp == undefined || Utils.tsCompare(timestamp,">",cachedCalibrationTimestamp)){
            let calibrationMaps = calibrationObject.getCalibrationMaps(calibration);
            cache.set("placeHierarchyMap",calibrationMaps.placeHierarchyMap);
            cache.set("sensorPlaceMap",calibrationMaps.sensorPlaceMap);
            cache.set("corridorInfoMap", calibrationMaps.corridorInfoMap);
            cache.set("calibration-timestamp",timestamp);
        }

        let cachedPlaceHierarchyMap = cache.get("placeHierarchyMap");
        if(!cachedPlaceHierarchyMap.has(input.place)){
            return { roadSegments: new Array() };
        }
        
        let roadNetwork = roadNetworkResponse.roadNetwork;
        let roadNetworkTimestamp = roadNetworkResponse.timestamp;
        let cachedRoadNetworkTimestamp = cache.get("road-network-timestamp");
        if (cachedRoadNetworkTimestamp == undefined ||
            Utils.tsCompare(roadNetworkTimestamp, ">", cachedRoadNetworkTimestamp)
        ) {
            let intersectionInfoMap = roadNetworkObject.getIntersectionInfoMap(roadNetwork);
            let citySegmentMap = roadNetworkObject.getSegmentMap(roadNetwork.intersections);
            let minimalCitySegmentMap = roadNetworkObject.getMinimalSegmentMap(citySegmentMap);
            cache.set("road-network-timestamp", roadNetworkTimestamp);
            cache.set("roadNetworkIntersectionInfoMap",intersectionInfoMap);
            cache.set("roadNetworkCitySegmentMaps", {citySegmentMap,minimalCitySegmentMap});
            let cacheKeys = cache.keys();
            let keysToBeDeleted = new Set();
            for(let key of cacheKeys){
                if(key.startsWith("intersectionSegmentMap-")){
                    keysToBeDeleted.add(key);
                }
            }
            cache.del( Array.from(keysToBeDeleted) );
        }

        let segmentMap = null;
        if(inputPlaceHierarchy==="city"){
            let cachedRoadNetworkCitySegmentMaps = cache.get("roadNetworkCitySegmentMaps");
            if(cachedRoadNetworkCitySegmentMaps == null){
                return { roadSegments: new Array() };
            }
            segmentMap = (input.segmentInfo) ? cachedRoadNetworkCitySegmentMaps.citySegmentMap : cachedRoadNetworkCitySegmentMaps.minimalCitySegmentMap;
        }else if (inputPlaceHierarchy==="city/intersection"){
            let intersectionSegmentMaps = null;
            if(cache.has(`intersectionSegmentMap-${input.place}`)){
                intersectionSegmentMaps = cache.get(`intersectionSegmentMap-${input.place}`);
                if (intersectionSegmentMaps == null) {
                    return { roadSegments: new Array() };
                }
            }else{
                let roadNetworkIntersectionInfoMap = cache.get("roadNetworkIntersectionInfoMap");
                if (roadNetworkIntersectionInfoMap == null) {
                    return { roadSegments: new Array() };
                }
                let roadNetworkOfIntersection = roadNetworkIntersectionInfoMap.get(input.place);
                if (roadNetworkOfIntersection == null) {
                    return { roadSegments: new Array() };
                }
                let intersectionSegmentMap = roadNetworkObject.getSegmentMap([roadNetworkOfIntersection]);
                let minimalIntersectionSegmentMap = roadNetworkObject.getMinimalSegmentMap(intersectionSegmentMap);
                intersectionSegmentMaps = {
                    segmentMap:intersectionSegmentMap, 
                    minimalSegmentMap: minimalIntersectionSegmentMap
                }
                cache.set(`intersectionSegmentMap-${input.place}`, intersectionSegmentMaps);
            }
            segmentMap = (input.segmentInfo) ? intersectionSegmentMaps.segmentMap : intersectionSegmentMaps.minimalSegmentMap;
        }
        let placeEdgeMap = new Map();
        for(let behavior of behaviorsWithCurrentSpeed){
            let behaviorPlace = behavior.place.name;
            if(!placeEdgeMap.has(behaviorPlace)){
                placeEdgeMap.set(behaviorPlace, new Map());
            }
            for (let edge of behavior.edges) {
                if (segmentMap.has(edge)) {
                    if(!placeEdgeMap.get(behaviorPlace).has(edge)){
                        placeEdgeMap.get(behaviorPlace).set(edge,{
                            speedSum: 0,
                            objectCount: 0
                        });
                    }
                    let speedDetails = placeEdgeMap.get(behaviorPlace).get(edge);
                    speedDetails.speedSum+=behavior.currentAvgSpeed;
                    speedDetails.objectCount+=1;
                    placeEdgeMap.get(behaviorPlace).set(edge,speedDetails);
                }
            }
        }
        for (const [place, edgeSpeedInfoMap] of placeEdgeMap) {
            for(const [edgeId, speedDetails] of edgeSpeedInfoMap){
                let avgSpeed = Math.floor(speedDetails.speedSum / speedDetails.objectCount);
                if (speedDetails.objectCount > segmentMap.get(edgeId).objectCount) {
                    segmentMap.get(edgeId).speed = avgSpeed;
                    segmentMap.get(edgeId).objectCount = speedDetails.objectCount;
                }
            }
        }

        let roadSegments = new Array();
        for (let [segmentId, segmentDetails] of segmentMap) {
            segmentDetails.id = segmentId;
            roadSegments.push(segmentDetails);
        }
            
        return { roadSegments } ;
    }
}

module.exports = Behavior;
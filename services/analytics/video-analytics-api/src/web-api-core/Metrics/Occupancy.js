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
const Events = require("../Services/Events");
const Validator = require("../Utils/Validator");
const Histogram = require("../Utils/Histogram");
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

/** 
 * Class which defines Occupancy
 * @memberof mdxWebApiCore.Metrics
 * */

class Occupancy {
    
    static #defaultTimeWindowInMsForMutuallyExclusiveRoiOccupancy = 2000;
    static #defaultTimestampDelayInSecForMutuallyExclusiveRoiOccupancy = 3;

    async #getOccupancyResetDetailsFromEs(elasticDb, {place,timestamp}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("occupancyReset")}`;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ range: { "timestamp": { lte: timestamp } } });
        queryBody.query.bool.must.push({ term: { "place.keyword": place } });
        queryBody.aggs = {
            objectTypes: {
                terms: {
                    field: "objectType.keyword",
                    size: 10000
                },
                aggs: {
                    latestReset: {
                        top_hits: {
                            size: 1,
                            sort: [{ "timestamp": { order: "desc" } }]
                        }
                    }
                }
            }
        };
        let queryObject = {
            index,
            body: queryBody,
            size: 0,
            sort: "timestamp:desc"
        };
        let results = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return results;
    }

    async #getOccupancyResetDetails(documentDb,input){
        let occupancyResetDetails = { place: input.place, occupancyResets: new Array() };
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let results = await this.#getOccupancyResetDetailsFromEs(documentDb, input);
                if (!results.indexAbsent) {
                    if (results.body.hasOwnProperty("aggregations")) {
                        for (let objectTypeResetObject of results.body.aggregations.objectTypes.buckets) {
                            let objectType = objectTypeResetObject.key;
                            let resetObject = objectTypeResetObject.latestReset.hits.hits[0]["_source"];
                            occupancyResetDetails.occupancyResets.push({
                                objectType,
                                occupancyReset: resetObject.occupancyReset,
                                timestamp: resetObject.timestamp
                            })
                        }
                    }
                }
                return occupancyResetDetails;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    #formatObjectTypeKey(objectType) {
        const formatted = objectType
            .trim()
            .toLowerCase()
            .replace(/[\s_]+/g, '_')  // Replace whitespace and underscores with single underscore
            .replace(/^_+|_+$/g, '');  // Remove leading/trailing underscores
        
        // Return null if normalization results in empty string
        return formatted === '' ? null : formatted;
    }

    async #getOccupancyResetMap(documentDb, input) {
        const resetDetails = await this.#getOccupancyResetDetails(documentDb, input);
        const resetMap = new Map();
        
        for (let resetDetail of resetDetails.occupancyResets) {
            const formattedKey = this.#formatObjectTypeKey(resetDetail.objectType);
            
            // Skip if objectType is malformed (normalizes to empty)
            if (formattedKey === null) {
                logger.warn(`[DATA] Skipping reset detail with invalid objectType: '${resetDetail.objectType}'`);
                continue;
            }
            
            // Check if key already exists
            if (resetMap.has(formattedKey)) {
                const existingDetail = resetMap.get(formattedKey);
                // Compare timestamps and keep the newest one
                if (Utils.tsCompare(resetDetail.timestamp, ">", existingDetail.timestamp)) {
                    resetMap.set(formattedKey, resetDetail);
                }
            } else {
                resetMap.set(formattedKey, resetDetail);
            }
        }
        
        return resetMap;
    }

    async #getOccupancyFromTripwireEventsEs(elasticDb,{place,objectType,timestamp}, objectTypeResetDetails){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("events")}`;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ term: { "event.info.class.keyword": "tripwire" } });
        queryBody.query.bool.must.push({ term: { "place.name.keyword": place } });
        queryBody.query.bool.must.push({ range: { timestamp: { lte: timestamp } } });
        
        if (objectType != null) {
            // Single objectType filter
            queryBody.query.bool.must.push({ term: { "object.type.keyword": objectType } });
            // Check if reset details exist for this objectType
            if (objectTypeResetDetails.has(objectType)) {
                let resetDetail = objectTypeResetDetails.get(objectType);
                if (resetDetail != null) {
                    queryBody.query.bool.must.push({ range: { end: { gte: resetDetail.timestamp } } });
                }
            }
        } else {
            // Multiple objectTypes - create should clauses for each objectType and reset timestamp combo
            let objectTypeClauses = new Array();
            for (let [objType, resetDetail] of objectTypeResetDetails) {
                let clause = {
                    bool: {
                        must: [
                            { term: { "object.type.keyword": objType } }
                        ]
                    }
                };
                if (resetDetail != null) {
                    clause.bool.must.push({ range: { end: { gte: resetDetail.timestamp } } });
                }
                objectTypeClauses.push(clause);
            }
            if (objectTypeClauses.length > 0) {
                queryBody.query.bool.must.push({bool: {should: objectTypeClauses, minimum_should_match: 1}});
            }
        }
            
        queryBody.aggs = {
            objectTypes: {
                terms: {
                    field: "object.type.keyword",
                    size: 10000
                },
                aggs: {
                    eventTypes: {
                        terms: {
                            field: "event.type.keyword"
                        }
                    }
                }
            }
        }
        let queryObject = { index, body: queryBody, size: 0 };
        let results = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return results;
    }

    async #getOccupancyFromTripwireEvents(documentDb,input, objectTypeResetDetails){
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let objectCountMap = new Map();
                let results = await this.#getOccupancyFromTripwireEventsEs(documentDb,input, objectTypeResetDetails);
                if (!results.indexAbsent) {
                    if (results.body.hasOwnProperty("aggregations")) {
                        for (let objectTypeBucket of results.body.aggregations.objectTypes.buckets) {
                            let tripEventCountMap = new Map([["IN",0],["OUT",0]]);
                            for (let countObject of objectTypeBucket.eventTypes.buckets) {
                                tripEventCountMap.set(countObject.key, countObject.doc_count);
                            }
                            objectCountMap.set(objectTypeBucket.key, tripEventCountMap);
                        }
                    }
                }
                
                // Calculate occupancy for each objectType
                let objectTypeOccupancyMap = new Map();
                for (let [objectType, tripEventCountMap] of objectCountMap) {
                    // Initialize with reset value if it exists
                    let occupancy = 0;
                    let resetDetails = null;
                    if (objectTypeResetDetails.has(objectType)) {
                        resetDetails = objectTypeResetDetails.get(objectType);
                        if (resetDetails != null) {
                            occupancy = resetDetails.occupancyReset;
                        }
                    }
                    
                    // Add event-based occupancy
                    let eventOccupancy = tripEventCountMap.get("IN") - tripEventCountMap.get("OUT");
                    occupancy += eventOccupancy;
                    
                    if (occupancy < 0) {
                        logger.warn(`[DATA] Occupancy based on events and reset value for objectType '${objectType}' is less than 0. Resetting occupancy to 0.`);
                        occupancy = 0;
                    }
                    
                    let tripEventCounts = new Array();
                    for (let [eventType, count] of tripEventCountMap) {
                        tripEventCounts.push({ type: eventType, count });
                    }
                    objectTypeOccupancyMap.set(objectType, {
                        occupancy: occupancy,
                        events: tripEventCounts,
                        resetDetails: resetDetails
                    });
                }
                
                return objectTypeOccupancyMap;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    /** 
     * returns an object containing occupancy based on tripwire events.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.place - Place used to query occupancy.
     * @param {string} input.timestamp - Timestamp for the query in ISO 8601 format.
     * @param {?string} [input.objectType] - If null, returns occupancy for all object types. If specified, returns occupancy for that specific object type.
     * @returns {Promise<Object>} Occupancy based on tripwire events is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {place: "building=abc/room=xyz", timestamp: "2023-01-12T11:20:10.000Z"};
     * let occupancyObject = new mdx.Metrics.Occupancy();
     * let occupancyResult = await occupancyObject.getOccupancyBasedOnTripwireEvents(elastic,input);
     */
    async getOccupancyBasedOnTripwireEvents(documentDb,input){
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                place:{
                    type: "string",
                    minLength: 1,
                    errorMessage: {
                        minLength: "place should have atleast 1 character."
                    }
                },
                timestamp: {
                    type: "string"
                },
                objectType:{
                    type: ["string", "null"],
                    minLength: 1,
                    default: null,
                    errorMessage: {
                        minLength: "objectType should have atleast 1 character."
                    }
                }
            },
            required: ["place", "timestamp"],
            errorMessage:{
                required: "Input should have required properties 'place' and 'timestamp'.",
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        if (!Validator.isValidISOTimestamp(input.timestamp)) {
            throw (new InvalidInputError("Invalid timestamp."));
        }
        
        let occupancyResult = {place: input.place, timestamp: input.timestamp, occupancy: new Array()};
        
        let occupancyResetMap = null;
        let uniqueObjectTypes = null;
        let objectTypeResetDetails = new Map();
        let resetOnlyObjectTypes = new Set();

        if (input.objectType != null) {

            let formattedKey = this.#formatObjectTypeKey(input.objectType);
            if (formattedKey === null) {
                throw (new InvalidInputError("Invalid objectType."));
            }
            // Optimized path: specific objectType provided
            // Only fetch reset details for that specific objectType
            occupancyResetMap = await this.#getOccupancyResetMap(documentDb, input);
            
            // Check if reset exists for this objectType
            if (occupancyResetMap.has(formattedKey)) {
                let resetDetail = occupancyResetMap.get(formattedKey);
                objectTypeResetDetails.set(input.objectType, resetDetail);
            } else {
                objectTypeResetDetails.set(input.objectType, null);
            }
            // No resetOnlyObjectTypes to process (we're only looking at one type)
        } else {
            // Fetch all object types
            let eventsService = new Events();
            [occupancyResetMap, uniqueObjectTypes] = await Promise.all([
                this.#getOccupancyResetMap(documentDb, input),
                eventsService.getObjectTypesOfTripwireEvents(documentDb, {place: input.place, toTimestamp: input.timestamp})
            ]);
            
            // Process object types and their reset details
            // For each object type from events, check if it exists in reset map
            for (let objectType of uniqueObjectTypes) {
                let formattedKey = this.#formatObjectTypeKey(objectType);
                if (formattedKey === null) {
                    // ObjectType is malformed, treat as no reset
                    logger.warn(`[DATA] Event has invalid objectType format: '${objectType}'. It won't have a reset value.`);
                    objectTypeResetDetails.set(objectType, null);
                } else if (occupancyResetMap.has(formattedKey)) {
                    let resetDetail = occupancyResetMap.get(formattedKey);
                    objectTypeResetDetails.set(objectType, resetDetail);
                } else {
                    // No reset found for this object type, use null
                    objectTypeResetDetails.set(objectType, null);
                }
            }
            
            // Find object types that are only in reset map but not in events
            let formattedUniqueObjectTypes = new Set();
            for (let objectType of uniqueObjectTypes) {
                let formattedKey = this.#formatObjectTypeKey(objectType);
                if (formattedKey !== null) {
                    formattedUniqueObjectTypes.add(formattedKey);
                }
            }
            
            // Get object types only in reset map
            resetOnlyObjectTypes = Utils.setDifference(new Set(occupancyResetMap.keys()), formattedUniqueObjectTypes);
        }
        
        let occupancyMap = await this.#getOccupancyFromTripwireEvents(documentDb, input, objectTypeResetDetails);
        
        // Add reset-only object types (those with resets but no events)
        if (input.objectType != null) {
            // Optimized path: check if the specified objectType was processed
            // If not, it might have only reset data with no events, or no data at all
            if (!occupancyMap.has(input.objectType) && objectTypeResetDetails.has(input.objectType)) {
                let resetDetails = objectTypeResetDetails.get(input.objectType);
                let occupancy = 0;
                if (resetDetails != null) {
                    occupancy = resetDetails.occupancyReset;
                }
                occupancyMap.set(input.objectType, {
                    occupancy: occupancy,
                    events: [{ type: "IN", count: 0 }, { type: "OUT", count: 0 }],
                    resetDetails: resetDetails
                });
            }
        } else {
            // Full path: add all reset-only object types
            for (let formattedKey of resetOnlyObjectTypes) {
                let resetDetails = occupancyResetMap.get(formattedKey);
                if (resetDetails != null) {
                    let objectType = resetDetails.objectType;
                    let occupancy = resetDetails.occupancyReset;
                    occupancyMap.set(objectType, {
                        occupancy: occupancy,
                        events: [{ type: "IN", count: 0 }, { type: "OUT", count: 0 }],
                        resetDetails: resetDetails
                    });
                }
            }
        }

        for (let [objectType, occupancyDetails] of occupancyMap) {
            occupancyResult.occupancy.push({
                objectType: objectType,
                count: occupancyDetails.occupancy,
                details:{
                    events: occupancyDetails.events,
                    occupancyReset: (occupancyDetails.resetDetails != null) ? {
                        resetValue: occupancyDetails.resetDetails.occupancyReset, 
                        resetTimestamp: occupancyDetails.resetDetails.timestamp
                    } : null
                }
            });
        }

        return occupancyResult;
    }

    async #insertResetOccupancyInEs(elasticDb,{place, timestamp, occupancyReset, objectType}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("occupancyReset")}`;
        let queryObject = {
            index,
            body: {
                place,
                objectType,
                timestamp,
                occupancyReset
            }
        };
        let result = await elasticDb.getClient().index(queryObject);
        return result;
    }

    /** 
     * returns a success message if reset value was inserted successfully.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.place - Place used to insert the occupancy reset.
     * @param {string} input.timestamp - Timestamp for the occupancy reset in ISO 8601 format.
     * @param {number} input.occupancyReset - Occupancy reset value to insert.
     * @param {string} [input.objectType="Person"] - Object type used to insert the occupancy reset.
     * @returns {Promise<Object>} A success message is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {place: "building=abc/room=xyz", timestamp: "2023-01-12T11:20:10.000Z", occupancyReset: 4};
     * let occupancyObject = new mdx.Metrics.Occupancy();
     * let result = await occupancyObject.resetOccupancy(elastic,input);
     */
    async resetOccupancy(documentDb,input){
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                place:{
                    type: "string",
                    minLength: 1,
                    errorMessage: {
                        minLength: "place should have atleast 1 character."
                    }
                },
                timestamp: {
                    type: "string"
                },
                occupancyReset:{
                    type: "integer",
                    minimum: 0,
                    errorMessage: {
                        type: "occupancyReset is not an integer.",
                        minimum: "occupancyReset can have a minimum value of 0."
                    }
                },
                objectType:{
                    type: "string",
                    minLength: 1,
                    errorMessage: {
                        minLength: "objectType should have atleast 1 character."
                    }
                }
            },
            required: ["place", "timestamp", "occupancyReset", "objectType"],
            errorMessage:{
                required: "Input should have required properties 'place', 'timestamp', 'occupancyReset' and 'objectType'.",
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema, false);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        if (!Validator.isValidISOTimestamp(input.timestamp)) {
            throw (new InvalidInputError("Invalid timestamp."));
        }
        if (!Number.isFinite(input.occupancyReset)) {
            throw (new InvalidInputError("occupancyReset is not a finite integer."));
        }
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                await this.#insertResetOccupancyInEs(documentDb,input);
                return ({ success: true });
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #getAverageFovOccupancyFromEs(elasticDb,{sensorId,fromTimestamp,toTimestamp,objectType}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("frames")}`;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ range: { "timestamp": { lte: toTimestamp, gte: fromTimestamp } } });
        queryBody.query.bool.must.push({ term: { "sensorId.keyword": sensorId } });
        queryBody.aggs = {
            fov: {
                nested: {
                    path: "fov"
                },
                aggs: {
                    searchAggFilter: {
                        filter: {
                            bool: {
                                filter: []
                            }
                        },
                        aggs:{
                            objectType: {
                                terms: {
                                    field: "fov.type.keyword",
                                    size: 1000
                                },
                                aggs: {
                                    avgCount: {
                                        avg: {
                                            field: "fov.count"
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        if (objectType != null) {
            queryBody.aggs.fov.aggs.searchAggFilter.filter.bool.filter.push({
                term: { "fov.type.keyword": objectType }
            });
        }
        let queryObject = { index, body: queryBody, size: 0 };
        let results = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return results;
    }

    /** 
     * returns an object containing average fov occupancy.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.sensorId - Sensor ID used to query average fov occupancy.
     * @param {string} input.fromTimestamp - fromTimestamp for the query in ISO 8601 format.
     * @param {string} input.toTimestamp - toTimestamp for the query in ISO 8601 format.
     * @param {?string} [input.objectType=null] - Object type used to filter average fov occupancy.
     * @returns {Promise<Object>} Average Fov Occupancy is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorId: "abc", fromTimestamp: "2023-01-12T11:20:10.000Z", toTimestamp: "2023-01-12T14:20:10.000Z"};
     * let occupancyObject = new mdx.Metrics.Occupancy();
     * let occupancyResult = await occupancyObject.getAverageFovOccupancy(elastic,input);
     */
    async getAverageFovOccupancy(documentDb,input){
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
                fromTimestamp:{
                    type: "string"
                },
                toTimestamp:{
                    type: "string"
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
        let fovOccupancy = new Array();
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let results = await this.#getAverageFovOccupancyFromEs(documentDb,input);
                if (!results.indexAbsent) {
                    for (let aggCountObject of results.body.aggregations.fov.searchAggFilter.objectType.buckets) {
                        fovOccupancy.push({
                            type:aggCountObject.key,
                            averageCount: Math.round(aggCountObject.avgCount.value)
                        });
                    }
                }
                return {fovOccupancy};
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #getHistogramOfAverageFovOccupancyFromEs(elasticDb,{sensorId,fromTimestamp,toTimestamp,bucketSizeInSec,objectType}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("frames")}`;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ range: { "timestamp": { gte: fromTimestamp, lte: toTimestamp } } });
        queryBody.query.bool.must.push({ term: { "sensorId.keyword": sensorId } });
        queryBody.aggs = {
            eventsOverTime: {
                date_histogram: {
                    field: "timestamp",
                    fixed_interval: `${bucketSizeInSec}s`
                },
                aggs: {
                    fov: {
                        nested: {
                            path: "fov"
                        },
                        aggs: {
                            searchAggFilter: {
                                filter: {
                                    bool: {
                                        filter: []
                                    }
                                },
                                aggs:{
                                    objectType: {
                                        terms: {
                                            field: "fov.type.keyword",
                                            size: 1000
                                        },
                                        aggs: {
                                            avgCount: {
                                                avg: {
                                                    field: "fov.count"
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        };
        if (objectType != null) {
            queryBody.aggs.eventsOverTime.aggs.fov.aggs.searchAggFilter.filter.bool.filter.push({
                term: { "fov.type.keyword": objectType }
            });
        }
        let queryObject = { index, body: queryBody, size: 0 };
        let results = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return results;
    }

    #addMissingObjectsInHistogramBucket(histogramBucket, objectTypes) {
        let bucketObjectTypes = new Set();
        for (let bucketObject of histogramBucket.objects) {
            bucketObjectTypes.add(bucketObject.type);
        }
        let missingObjects = Utils.setDifference(objectTypes, bucketObjectTypes);
        for (let missingObject of missingObjects) {
            histogramBucket.objects.push({ type: missingObject, averageCount: 0 });
        }
        return histogramBucket;
    }
    
    #addMissingObjectsInRoiHistogramBucket(histogramBucket, objectTypes) {
        let bucketObjectTypes = new Set();
        for (let bucketObject of histogramBucket.objects) {
            bucketObjectTypes.add(bucketObject.type);
        }
        let missingObjects = Utils.setDifference(objectTypes, bucketObjectTypes);
        for (let missingObject of missingObjects) {
            histogramBucket.objects.push({ type: missingObject, averageCount: 0, uniqueObjectCount: 0 });
        }
        return histogramBucket;
    }

    /** 
     * returns an object containing histogram of average fov occupancy.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.sensorId - Sensor ID used to query the occupancy histogram.
     * @param {string} [input.fromTimestamp] - Either fromTimestamp and toTimestamp should be present together or minutesAgo should be present.
     * @param {string} [input.toTimestamp] - Either fromTimestamp and toTimestamp should be present together or minutesAgo should be present.
     * @param {number} [input.minutesAgo] - Time window in minutes before now. Either fromTimestamp and toTimestamp should be present together or minutesAgo should be present.
     * @param {number} [input.bucketCount=20] - Number of histogram buckets returned.
     * @param {?string} [input.objectType=null] - Object type used to filter the histogram.
     * @returns {Promise<Object>} Histogram of Average Fov Occupancy is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorId: "abc", fromTimestamp: "2023-01-12T11:20:10.000Z", toTimestamp: "2023-01-12T14:20:10.000Z",bucketCount:24};
     * let occupancyObject = new mdx.Metrics.Occupancy();
     * let fovHistogramResult = await occupancyObject.getHistogramOfAverageFovOccupancy(elastic,input);
     */
    async getHistogramOfAverageFovOccupancy(documentDb,input){
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
                    type: ["string","null"],
                    default: null
                },
                toTimestamp: {
                    type: ["string","null"],
                    default: null
                },
                minutesAgo: {
                    type: ["integer","null"],
                    minimum: 1,
                    default: null,
                    errorMessage: {
                        type: "minutesAgo is not an integer.",
                        minimum: "minutesAgo can have a minimum value of 1."
                    }
                },
                bucketCount:{
                    type: "integer",
                    minimum: 1,
                    default: Histogram.getDefaultHistogramBucketCount(),
                    maximum: Histogram.getMaxHistogramBucketCount(),
                    errorMessage: {
                        type: "bucketCount is not an integer.",
                        minimum: "bucketCount can have a minimum value of 1.",
                        maximum: `bucketCount can have a maximum value of ${Histogram.getMaxHistogramBucketCount()}.`
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
            required:["sensorId"],
            oneOf:[
                {
                    required:["minutesAgo"],
                    not: {
                        anyOf: [
                            { required: ["fromTimestamp", "minutesAgo"] },
                            { required: ["toTimestamp", "minutesAgo"] }
                        ]
                    }
                },
                {
                    required:["fromTimestamp", "toTimestamp"],
                    not: {
                        required: ["minutesAgo"]
                    }
                }
            ],
            errorMessage:{
                required: "Input should have required properties 'sensorId'.",
                oneOf: "One of the following combinations should be present in input: ('fromTimestamp', 'toTimestamp') or ('minutesAgo')."
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        if(input.fromTimestamp!=null && input.toTimestamp!=null){
            let timeRangeValidationResult = Validator.isValidTimeRange(input.fromTimestamp, input.toTimestamp);
            if (!timeRangeValidationResult.valid) {
                throw (new InvalidInputError(timeRangeValidationResult.reason));
            }
        }else if(input.minutesAgo!=null){
            if(!Number.isFinite(input.minutesAgo)){
                throw (new InvalidInputError("minutesAgo is not a finite integer."));
            }
            input.toTimestamp = new Date().toISOString();
            input.fromTimestamp = new Date(new Date(input.toTimestamp) - (input.minutesAgo * 60 * 1000)).toISOString();
        }
        if(!Number.isFinite(input.bucketCount)){
            throw (new InvalidInputError("bucketCount is not a finite integer."));
        }
        input.bucketSizeInSec = Histogram.computeBucketSizeInSec({
            bucketCount: input.bucketCount,
            fromTimestamp:input.fromTimestamp,
            toTimestamp:input.toTimestamp
        });
        let fovHistogram = new Array();
        switch(documentDb.getName()){
            case "Elasticsearch": {
                let results = await this.#getHistogramOfAverageFovOccupancyFromEs(documentDb,input);
                let objectTypes = new Set();
                let bucketMap = new Map();
                if (!results.indexAbsent) {
                    for (let bucket of results.body.aggregations.eventsOverTime.buckets) {
                        let start = bucket["key_as_string"];
                        let end = new Date(new Date(start).valueOf() + (input.bucketSizeInSec * 1000)).toISOString();
                        let formattedHistogramBucket = { start, end, objects: new Array() };
                        for (let aggCountObject of bucket.fov.searchAggFilter.objectType.buckets) {
                            formattedHistogramBucket.objects.push({
                                type: aggCountObject.key,
                                averageCount: Math.round(aggCountObject.avgCount.value)
                            });
                            objectTypes.add(aggCountObject.key);
                        }
                        bucketMap.set(start,formattedHistogramBucket);
                    }
                }
                let emptyHistogram = Histogram.getEmptyHistogram({
                    bucketSizeInSec:input.bucketSizeInSec,
                    fromTimestamp:input.fromTimestamp,
                    toTimestamp:input.toTimestamp
                });
                if (bucketMap.size === 0) {
                    fovHistogram = emptyHistogram;
                    for(let bucket of fovHistogram){
                        bucket.objects = new Array();
                    }
                }else{
                    for(let bucket of emptyHistogram){
                        if(bucketMap.has(bucket.start)){
                            bucket = bucketMap.get(bucket.start);
                        }else{
                            bucket.objects = new Array();
                        }
                        bucket = this.#addMissingObjectsInHistogramBucket(bucket, objectTypes);
                        fovHistogram.push(bucket);
                    }
                }
                if(Utils.tsCompare(fovHistogram[0].start,"<",input.fromTimestamp)){
                    fovHistogram[0].start = input.fromTimestamp;
                }
                if(Utils.tsCompare(fovHistogram[fovHistogram.length-1].end,">",input.toTimestamp)){
                    fovHistogram[fovHistogram.length-1].end = input.toTimestamp;
                }
                return { bucketSizeInSec: input.bucketSizeInSec, histogram: fovHistogram };
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #getRoiOccupancyFromEs(elasticDb,{sensorId,roiId,fromTimestamp,toTimestamp, objectType}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("frames")}`;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ range: { "timestamp": { lte: toTimestamp, gte: fromTimestamp } } });
        queryBody.query.bool.must.push({ term: { "sensorId.keyword": sensorId } });
        if (roiId != null) {
            queryBody.query.bool.must.push({
                nested: {
                    path: "rois",
                    query: {
                        bool: {
                            must: [{ term: { "rois.id.keyword": roiId } }]
                        }
                    }
                }
            });
        }
        queryBody.aggs = {
            rois: {
                nested: {
                    path: "rois"
                },
                aggs: {
                    searchAggFilter: {
                        filter: {
                            bool: {
                                filter: []
                            }
                        },
                        aggs: {
                            roiIds: {
                                terms: {
                                    field: "rois.id.keyword",
                                    size: 40
                                },
                                aggs: {
                                    objectType: {
                                        terms: {
                                            field: "rois.type.keyword",
                                            size: 1000
                                        },
                                        aggs: {
                                            avgCount: {
                                                avg: {
                                                    field: "rois.count"
                                                }
                                            },
                                            uniqueObjectCount: {
                                                cardinality: {
                                                    field: "rois.objectIds.keyword"
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        if (roiId != null) {
            queryBody.aggs.rois.aggs.searchAggFilter.filter.bool.filter.push({
                term: { "rois.id.keyword": roiId }
            });
        }
        if (objectType != null) {
            queryBody.aggs.rois.aggs.searchAggFilter.filter.bool.filter.push({
                term: { "rois.type.keyword": objectType }
            });
        }
        let queryObject = { index, body: queryBody, size: 0 };
        let results = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return results;
    }

    /** 
     * returns an object containing average and unique object roi occupancy.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.sensorId - Sensor ID used to query roi occupancy.
     * @param {?string} [input.roiId=null] - ROI ID used to filter roi occupancy.
     * @param {string} input.fromTimestamp - fromTimestamp for the query in ISO 8601 format.
     * @param {string} input.toTimestamp - toTimestamp for the query in ISO 8601 format.
     * @param {?string} [input.objectType=null] - Object type used to filter the roi occupancy.
     * @returns {Promise<Object>} Roi Occupancy is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorId: "abc", fromTimestamp: "2023-01-12T11:20:10.000Z", toTimestamp: "2023-01-12T14:20:10.000Z"};
     * let occupancyObject = new mdx.Metrics.Occupancy();
     * let occupancyResult = await occupancyObject.getRoiOccupancy(elastic,input);
     */
    async getRoiOccupancy(documentDb, input){
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
                roiId: {
                    type: ["string", "null"],
                    default: null,
                    minLength: 1,
                    errorMessage: {
                        minLength: "roiId should have atleast 1 character."
                    }
                },
                fromTimestamp:{
                    type: "string"
                },
                toTimestamp:{
                    type: "string"
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
        let roiOccupancy = new Array();
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let results = await this.#getRoiOccupancyFromEs(documentDb,input);
                if (!results.indexAbsent) {
                    for (let roiObject of results.body.aggregations.rois.searchAggFilter.roiIds.buckets) {
                        let roiId = roiObject.key;
                        let roiCount = {roiId, objects:new Array()};
                        for (let aggCountObject of roiObject.objectType.buckets) {
                            roiCount.objects.push({
                                type:aggCountObject.key,
                                averageCount:Math.round(aggCountObject.avgCount.value),
                                uniqueObjectCount: aggCountObject.uniqueObjectCount.value
                            });
                        }
                        roiOccupancy.push(roiCount);
                    }
                }
                return {roiOccupancy};
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #getHistogramOfRoiOccupancyFromEs(elasticDb,{sensorId,roiId,fromTimestamp,toTimestamp,bucketSizeInSec,objectType}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("frames")}`;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ range: { "timestamp": { lte: toTimestamp, gte: fromTimestamp } } });
        queryBody.query.bool.must.push({ term: { "sensorId.keyword": sensorId } });
        if (roiId != null) {
            queryBody.query.bool.must.push({
                nested: {
                    path: "rois",
                    query: {
                        bool: {
                            must: [{ term: { "rois.id.keyword": roiId } }]
                        }
                    }
                }
            });
        }
        queryBody.aggs = {
            eventsOverTime: {
                date_histogram: {
                    field: "timestamp",
                    fixed_interval: `${bucketSizeInSec}s`
                },
                aggs: {
                    rois: {
                        nested: {
                            path: "rois"
                        },
                        aggs: {
                            searchAggFilter: {
                                filter: {
                                    bool: {
                                        filter: []
                                    }
                                },
                                aggs: {
                                    roiIds: {
                                        terms: {
                                            field: "rois.id.keyword",
                                            size: 40
                                        },
                                        aggs: {
                                            objectType: {
                                                terms: {
                                                    field: "rois.type.keyword",
                                                    size: 1000
                                                },
                                                aggs: {
                                                    avgCount: {
                                                        avg: {
                                                            field: "rois.count"
                                                        }
                                                    },
                                                    uniqueObjectCount: {
                                                        cardinality: {
                                                            field: "rois.objectIds.keyword"
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        if (roiId != null) {
            queryBody.aggs.eventsOverTime.aggs.rois.aggs.searchAggFilter.filter.bool.filter.push({
                term: { "rois.id.keyword": roiId }
            });
        }
        if (objectType != null) {
            queryBody.aggs.eventsOverTime.aggs.rois.aggs.searchAggFilter.filter.bool.filter.push({
                term: { "rois.type.keyword": objectType }
            });
        }
        let queryObject = { index, body: queryBody, size: 0 };
        let results = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return results;
    }

    /** 
     * returns an object containing histogram of roi occupancy.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.sensorId - Sensor ID used to query the roi occupancy histogram.
     * @param {?string} [input.roiId=null] - ROI ID used to filter the roi occupancy histogram.
     * @param {string} [input.fromTimestamp] - Either fromTimestamp and toTimestamp should be present together or minutesAgo should be present.
     * @param {string} [input.toTimestamp] - Either fromTimestamp and toTimestamp should be present together or minutesAgo should be present.
     * @param {number} [input.minutesAgo] - Time window in minutes before now. Either fromTimestamp and toTimestamp should be present together or minutesAgo should be present.
     * @param {number} [input.bucketCount=20] - Number of histogram buckets returned.
     * @param {?string} [input.objectType=null] - Object type used to filter the roi occupancy histogram.
     * @returns {Promise<Object>} Histogram of Roi Occupancy is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorId: "abc", fromTimestamp: "2023-01-12T11:20:10.000Z", toTimestamp: "2023-01-12T14:20:10.000Z",bucketCount:24};
     * let occupancyObject = new mdx.Metrics.Occupancy();
     * let roiHistogramResult = await occupancyObject.getHistogramOfRoiOccupancy(elastic,input);
     */
    async getHistogramOfRoiOccupancy(documentDb,input){
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
                roiId: {
                    type: ["string","null"],
                    default: null,
                    minLength: 1,
                    errorMessage: {
                        minLength: "roiId should have atleast 1 character."
                    }
                },
                fromTimestamp: {
                    type: ["string","null"],
                    default: null
                },
                toTimestamp: {
                    type: ["string","null"],
                    default: null
                },
                minutesAgo: {
                    type: ["integer","null"],
                    minimum: 1,
                    default: null,
                    errorMessage: {
                        type: "minutesAgo is not an integer.",
                        minimum: "minutesAgo can have a minimum value of 1."
                    }
                },
                bucketCount:{
                    type: "integer",
                    minimum: 1,
                    default: Histogram.getDefaultHistogramBucketCount(),
                    maximum: Histogram.getMaxHistogramBucketCount(),
                    errorMessage: {
                        type: "bucketCount is not an integer.",
                        minimum: "bucketCount can have a minimum value of 1.",
                        maximum: `bucketCount can have a maximum value of ${Histogram.getMaxHistogramBucketCount()}.`
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
            required:["sensorId"],
            oneOf:[
                {
                    required:["minutesAgo"],
                    not: {
                        anyOf: [
                            { required: ["fromTimestamp", "minutesAgo"] },
                            { required: ["toTimestamp", "minutesAgo"] }
                        ]
                    }
                },
                {
                    required:["fromTimestamp", "toTimestamp"],
                    not: {
                        required: ["minutesAgo"]
                    }
                }
            ],
            errorMessage:{
                required: "Input should have required properties 'sensorId'.",
                oneOf: "One of the following combinations should be present in input: ('fromTimestamp', 'toTimestamp') or ('minutesAgo')."
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        if(input.fromTimestamp!=null && input.toTimestamp!=null){
            let timeRangeValidationResult = Validator.isValidTimeRange(input.fromTimestamp, input.toTimestamp);
            if (!timeRangeValidationResult.valid) {
                throw (new InvalidInputError(timeRangeValidationResult.reason));
            }
        }else if(input.minutesAgo!=null){
            if(!Number.isFinite(input.minutesAgo)){
                throw (new InvalidInputError("minutesAgo is not a finite integer."));
            }
            input.toTimestamp = new Date().toISOString();
            input.fromTimestamp = new Date(new Date(input.toTimestamp) - (input.minutesAgo * 60 * 1000)).toISOString();
        }
        if(!Number.isFinite(input.bucketCount)){
            throw (new InvalidInputError("bucketCount is not a finite integer."));
        }
        input.bucketSizeInSec = Histogram.computeBucketSizeInSec({
            bucketCount: input.bucketCount,
            fromTimestamp:input.fromTimestamp,
            toTimestamp:input.toTimestamp
        });
        let roiHistogramResult = { bucketSizeInSec: input.bucketSizeInSec, rois: new Array() };
        switch(documentDb.getName()){
            case "Elasticsearch": {
                let results = await this.#getHistogramOfRoiOccupancyFromEs(documentDb, input);
                if (!results.indexAbsent) {
                    let roiIds = new Set();
                    for (let bucket of results.body.aggregations.eventsOverTime.buckets) {
                        for (let roiObject of bucket.rois.searchAggFilter.roiIds.buckets) {
                            roiIds.add(roiObject.key);
                        }
                    }
                    let roiHistogramMap = new Map();
                    let objectTypes = new Set();
                    for (let bucket of results.body.aggregations.eventsOverTime.buckets) {
                        let start = bucket["key_as_string"];
                        let end = new Date(new Date(start).valueOf() + (input.bucketSizeInSec * 1000)).toISOString();
                        let roisInBucket = new Set();
                        for (let roiObject of bucket.rois.searchAggFilter.roiIds.buckets) {
                            let roiId = roiObject.key;
                            let formattedHistogramBucket = { start, end, objects: new Array() };
                            for (let aggCountObject of roiObject.objectType.buckets) {
                                formattedHistogramBucket.objects.push({
                                    type: aggCountObject.key,
                                    averageCount: Math.round(aggCountObject.avgCount.value),
                                    uniqueObjectCount: aggCountObject.uniqueObjectCount.value
                                });
                                objectTypes.add(aggCountObject.key);
                            }
                            let bucketMap = new Map();
                            if (roiHistogramMap.has(roiId)) {
                                bucketMap = roiHistogramMap.get(roiId);
                            }
                            bucketMap.set(start,formattedHistogramBucket);
                            roiHistogramMap.set(roiId, bucketMap);
                            roisInBucket.add(roiId);
                        }
                        let missingRois = Utils.setDifference(roiIds, roisInBucket);
                        for (let roiId of missingRois) {
                            let bucketMap = new Map();
                            if (roiHistogramMap.has(roiId)) {
                                bucketMap = roiHistogramMap.get(roiId);
                            }
                            bucketMap.set(start,{ start, end, objects: new Array() });
                            roiHistogramMap.set(roiId, bucketMap);
                        }
                    }
                    let emptyHistogram = Histogram.getEmptyHistogram({
                        bucketSizeInSec:input.bucketSizeInSec,
                        fromTimestamp:input.fromTimestamp,
                        toTimestamp:input.toTimestamp
                    });
                    for (let [roiId, bucketMap] of roiHistogramMap) {
                        let histogram = new Array();
                        for(let bucket of emptyHistogram){
                            if(bucketMap.has(bucket.start)){
                                bucket = bucketMap.get(bucket.start);
                            }else{
                                bucket.objects = new Array();
                            }
                            bucket = this.#addMissingObjectsInRoiHistogramBucket(bucket, objectTypes);
                            histogram.push(bucket);
                        }
                        if(Utils.tsCompare(histogram[0].start,"<",input.fromTimestamp)){
                            histogram[0].start = input.fromTimestamp;
                        }
                        if(Utils.tsCompare(histogram[histogram.length-1].end,">",input.toTimestamp)){
                            histogram[histogram.length-1].end = input.toTimestamp;
                        }
                        roiHistogramResult.rois.push({ id: roiId, histogram });
                    }
                }
                return roiHistogramResult;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #getUniqueObjectCountInMutuallyExclusiveRoisFromEs(elasticDb, {place, timestamp, timeWindowInMs,objectType,objectDetails}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("frames")}`;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ range: { "timestamp": { lte: timestamp, gte: new Date(new Date(timestamp)-timeWindowInMs).toISOString() } } });
        queryBody.query.bool.must.push({ exists: { field: "info.place" } });
        queryBody.query.bool.must.push({ term: { "info.place.keyword": place } });
        queryBody.query.bool.must.push({
            nested: {
                path: "rois",
                query: {
                    bool: {
                        must: [{ term: { "rois.type.keyword": objectType } }]
                    }
                }
            }
        });
        queryBody.aggs = {
            sensorIds: {
                terms: {
                    field: "sensorId.keyword",
                    size: 10000
                },
                aggs: {
                    lastProcessedRecord: {
                        top_hits: {
                            size: 1,
                            sort: [
                                {
                                    timestamp: {
                                        order: "desc"
                                    }
                                }
                            ],
                            _source: {
                                includes: ["rois"],
                                excludes: (!objectDetails)?["rois.coordinates","rois.id"]:[]
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
     * returns an object containing occupancy of a place which is calculated based on mutually exclusive rois.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.place - Place used to query occupancy.
     * @param {?string} [input.timestamp=null] - Timestamp for the query in ISO 8601 format.
     * @param {string} [input.objectType="Person"] - Object type used to filter occupancy.
     * @param {boolean} [input.objectDetails=false] - Whether to include object details in the response.
     * @param {number} [input.timestampDelayInSec=3] - Timestamp delay in seconds used to query recent occupancy.
     * @param {number} [input.timeWindowInMs=2000] - Time window in milliseconds used to query occupancy.
     * @returns {Promise<Object>} Occupancy of a place which is calculated based on mutually exclusive rois is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {place: "building=abc/room=xyz"};
     * let occupancyObject = new mdx.Metrics.Occupancy();
     * let placeOccupancy = await occupancyObject.getUniqueObjectCountInMutuallyExclusiveRois(elastic,input);
     */
    async getUniqueObjectCountInMutuallyExclusiveRois(documentDb,input){
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
                timestamp: {
                    type: ["string","null"],
                    default: null
                },
                objectType:{
                    type: "string",
                    default: "Person",
                    minLength: 1,
                    errorMessage: {
                        minLength: "objectType should have atleast 1 character."
                    }
                },
                objectDetails:{
                    type: "boolean",
                    default: false,
                    errorMessage: {
                        type: "objectDetails doesn't have a boolean value.",
                    }
                },
                timestampDelayInSec:{
                    type: "integer",
                    minimum: 1,
                    default: Occupancy.#defaultTimestampDelayInSecForMutuallyExclusiveRoiOccupancy,
                    errorMessage: {
                        type: "timestampDelayInSec is not an integer.",
                        minimum: "timestampDelayInSec can have a minimum value of 1."
                    }
                },
                timeWindowInMs:{
                    type: "integer",
                    minimum: 0,
                    default: Occupancy.#defaultTimeWindowInMsForMutuallyExclusiveRoiOccupancy,
                    errorMessage: {
                        type: "timeWindowInMs is not an integer.",
                        minimum: "timeWindowInMs can have a minimum value of 0."
                    }
                }
            },
            required:["place"],
            errorMessage:{
                required: "Input should have required properties 'place'.",
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        if(input.timestamp!=null){
            if (!Validator.isValidISOTimestamp(input.timestamp)) {
                throw (new InvalidInputError("Invalid timestamp."));
            }
            input.timestampDelayInSec = null;
        }else{
            if(!Number.isFinite(input.timestampDelayInSec)){
                throw (new InvalidInputError("timestampDelayInSec is not a finite integer."));
            }
            let currentEpochTimeInSec = (new Date().getTime())/1000;
            let roundedTimestamp = new Date(Math.ceil(currentEpochTimeInSec)*1000);
            input.timestamp = new Date(roundedTimestamp - input.timestampDelayInSec*1000).toISOString();
        }
        if(!Number.isFinite(input.timeWindowInMs)){
            throw (new InvalidInputError("timeWindowInMs is not a finite integer."));
        }
        let occupancyResult = {place: input.place, timestamp: input.timestamp, occupancy: 0, objectType: input.objectType};
        if(input.objectDetails){
            occupancyResult.objectDetails = new Array();
        }
        switch(documentDb.getName()){
            case "Elasticsearch": {
                let results = await this.#getUniqueObjectCountInMutuallyExclusiveRoisFromEs(documentDb,input);
                if (!results.indexAbsent) {
                    for(let bucket of results.body.aggregations.sensorIds.buckets){
                        let sensorId = bucket.key;
                        let record = bucket.lastProcessedRecord.hits.hits[0]["_source"];
                        for(let roi of record.rois){
                            if(roi.type === input.objectType){
                                occupancyResult.occupancy+=roi.count;
                                if(input.objectDetails){
                                    occupancyResult.objectDetails.push({
                                        sensorId,
                                        roiId:roi.id,
                                        objects: roi.objectIds.map(function(id, i) {
                                            return {id, coordinates:roi.coordinates[i]};
                                        })
                                    });
                                }
                            }
                        }
                    }
                }
                return occupancyResult;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #getRTLSHistogramOfAverageOccupancyOfAPlaceFromEs(elasticDb,{place,fromTimestamp,toTimestamp,bucketSizeInSec}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("rtls")}`;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ range: { "timestamp": { gte: fromTimestamp, lte: toTimestamp } } });
        queryBody.query.bool.must.push({ prefix: { "place.keyword": place } });
        queryBody.aggs = {
            eventsOverTime: {
                date_histogram: {
                    field: "timestamp",
                    fixed_interval: `${bucketSizeInSec}s`,
                    min_doc_count: 0,
                    extended_bounds: {
                        min: fromTimestamp,
                        max: toTimestamp
                    }
                },
                aggs: {
                    objectCounts: {
                        nested: {
                            path: "objectCounts"
                        },
                        aggs: {
                            objectType: {
                                terms: {
                                    field: "objectCounts.type.keyword",
                                    size: 10000
                                },
                                aggs: {
                                    avgCount: {
                                        avg: {
                                            field: "objectCounts.count"
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        let queryObject = { index, body: queryBody, size: 0 };
        let results = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return results;
    }

    async #getAMRHistogramOfAverageOccupancyOfAPlaceFromEs(elasticDb,{place,fromTimestamp,toTimestamp,bucketSizeInSec}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("amrLocations")}`;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ range: { "timestamp": { gte: fromTimestamp, lte: toTimestamp } } });
        queryBody.query.bool.must.push({ prefix: { "place.keyword": place } });
        queryBody.aggs = {
            eventsOverTime: {
                date_histogram: {
                    field: "timestamp",
                    fixed_interval: `${bucketSizeInSec}s`,
                    min_doc_count: 0,
                    extended_bounds: {
                        min: fromTimestamp,
                        max: toTimestamp
                    }
                },
                aggs: {
                    objectCounts: {
                        nested: {
                            path: "objectCounts"
                        },
                        aggs: {
                            objectType: {
                                terms: {
                                    field: "objectCounts.type.keyword",
                                    size: 10000
                                },
                                aggs: {
                                    avgCount: {
                                        avg: {
                                            field: "objectCounts.count"
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        let queryObject = { index, body: queryBody, size: 0 };
        let results = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return results;
    }

    /** 
     * returns an object containing histogram of average occupancy of a place.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.place - Place used to query the occupancy histogram.
     * @param {string} [input.fromTimestamp] - Either fromTimestamp and toTimestamp should be present together or minutesAgo should be present.
     * @param {string} [input.toTimestamp] - Either fromTimestamp and toTimestamp should be present together or minutesAgo should be present.
     * @param {number} [input.minutesAgo] - Time window in minutes before now. Either fromTimestamp and toTimestamp should be present together or minutesAgo should be present.
     * @param {number} [input.bucketCount=20] - Number of histogram buckets returned.
     * @returns {Promise<Object>} Histogram of Average Occupancy of a place is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {place: "building=abc/room=xyz", fromTimestamp: "2023-01-12T11:20:10.000Z", toTimestamp: "2023-01-12T14:20:10.000Z",bucketCount:24};
     * let occupancyObject = new mdx.Metrics.Occupancy();
     * let histogramResult = await occupancyObject.getHistogramOfAverageOccupancyOfAPlace(elastic,input);
     */
    async getHistogramOfAverageOccupancyOfAPlace(documentDb, input){
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
                    type: ["string","null"],
                    default: null
                },
                toTimestamp: {
                    type: ["string","null"],
                    default: null
                },
                minutesAgo: {
                    type: ["integer","null"],
                    minimum: 1,
                    default: null,
                    errorMessage: {
                        type: "minutesAgo is not an integer.",
                        minimum: "minutesAgo can have a minimum value of 1."
                    }
                },
                bucketCount:{
                    type: "integer",
                    minimum: 1,
                    default: Histogram.getDefaultHistogramBucketCount(),
                    maximum: Histogram.getMaxHistogramBucketCount(),
                    errorMessage: {
                        type: "bucketCount is not an integer.",
                        minimum: "bucketCount can have a minimum value of 1.",
                        maximum: `bucketCount can have a maximum value of ${Histogram.getMaxHistogramBucketCount()}.`
                    }
                }
            },
            required:["place"],
            oneOf:[
                {
                    required:["minutesAgo"],
                    not: {
                        anyOf: [
                            { required: ["fromTimestamp", "minutesAgo"] },
                            { required: ["toTimestamp", "minutesAgo"] }
                        ]
                    }
                },
                {
                    required:["fromTimestamp", "toTimestamp"],
                    not: {
                        required: ["minutesAgo"]
                    }
                }
            ],
            errorMessage:{
                required: "Input should have required properties 'place'.",
                oneOf: "One of the following combinations should be present in input: ('fromTimestamp', 'toTimestamp') or ('minutesAgo')."
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        if(input.fromTimestamp!=null && input.toTimestamp!=null){
            let timeRangeValidationResult = Validator.isValidTimeRange(input.fromTimestamp, input.toTimestamp);
            if (!timeRangeValidationResult.valid) {
                throw (new InvalidInputError(timeRangeValidationResult.reason));
            }
        }else if(input.minutesAgo!=null){
            if(!Number.isFinite(input.minutesAgo)){
                throw (new InvalidInputError("minutesAgo is not a finite integer."));
            }
            input.toTimestamp = new Date().toISOString();
            input.fromTimestamp = new Date(new Date(input.toTimestamp) - (input.minutesAgo * 60 * 1000)).toISOString();
        }
        if(!Number.isFinite(input.bucketCount)){
            throw (new InvalidInputError("bucketCount is not a finite integer."));
        }
        input.bucketSizeInSec = Histogram.computeBucketSizeInSec({
            bucketCount: input.bucketCount,
            fromTimestamp:input.fromTimestamp,
            toTimestamp:input.toTimestamp
        });
        let occupancyHistogramOfAPlace = new Array();
        switch(documentDb.getName()){
            case "Elasticsearch": {
                let [rtlsResults, amrResults] = await Promise.all([
                    this.#getRTLSHistogramOfAverageOccupancyOfAPlaceFromEs(documentDb,input),
                    this.#getAMRHistogramOfAverageOccupancyOfAPlaceFromEs(documentDb,input)
                ]);
                let objectTypes = new Set();
                let bucketMap = new Map();
                if(!rtlsResults.indexAbsent){
                    for (let bucket of rtlsResults.body.aggregations.eventsOverTime.buckets) {
                        let start = bucket["key_as_string"];
                        let end = new Date(new Date(start).valueOf() + (input.bucketSizeInSec * 1000)).toISOString();
                        let formattedHistogramBucket = { start, end, objects: new Array() };
                        for (let aggCountObject of bucket.objectCounts.objectType.buckets) {
                            formattedHistogramBucket.objects.push({
                                type: aggCountObject.key,
                                averageCount: Math.round(aggCountObject.avgCount.value)
                            });
                            objectTypes.add(aggCountObject.key);
                        }
                        bucketMap.set(start,formattedHistogramBucket);
                    }
                }
                if(!amrResults.indexAbsent){
                    for (let bucket of amrResults.body.aggregations.eventsOverTime.buckets) {
                        let start = bucket["key_as_string"];
                        let end = new Date(new Date(start).valueOf() + (input.bucketSizeInSec * 1000)).toISOString();
                        let formattedHistogramBucket = (bucketMap.has(start))?bucketMap.get(start):{ start, end, objects: new Array() };
                        for (let aggCountObject of bucket.objectCounts.objectType.buckets) {
                            formattedHistogramBucket.objects.push({
                                type: aggCountObject.key,
                                averageCount: Math.round(aggCountObject.avgCount.value)
                            });
                            objectTypes.add(aggCountObject.key);
                        }
                        bucketMap.set(start,formattedHistogramBucket);
                    }
                }
                if (bucketMap.size === 0) {
                    occupancyHistogramOfAPlace = Histogram.getEmptyHistogram({
                        bucketSizeInSec:input.bucketSizeInSec,
                        fromTimestamp:input.fromTimestamp,
                        toTimestamp:input.toTimestamp
                    });
                    for(let bucket of occupancyHistogramOfAPlace){
                        bucket.objects = new Array();
                    }
                }else{
                    for (let [start, bucket] of bucketMap) {
                        bucket = this.#addMissingObjectsInHistogramBucket(bucket, objectTypes);
                        occupancyHistogramOfAPlace.push(bucket);
                    }
                }
                if(Utils.tsCompare(occupancyHistogramOfAPlace[0].start,"<",input.fromTimestamp)){
                    occupancyHistogramOfAPlace[0].start = input.fromTimestamp;
                }
                if(Utils.tsCompare(occupancyHistogramOfAPlace[occupancyHistogramOfAPlace.length-1].end,">",input.toTimestamp)){
                    occupancyHistogramOfAPlace[occupancyHistogramOfAPlace.length-1].end = input.toTimestamp;
                }
                return { bucketSizeInSec: input.bucketSizeInSec, histogram: occupancyHistogramOfAPlace };
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }
}

module.exports = Occupancy;

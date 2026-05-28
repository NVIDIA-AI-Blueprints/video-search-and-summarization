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
const Histogram = require("../Utils/Histogram");
const Validator = require("../Utils/Validator");
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
 * Class which defines TripwireEvent
 * @memberof mdxWebApiCore.Metrics
 * */

class TripwireEvent {
    
    async #getDetailedTripwireCountsFromEs(elasticDb, {sensorId, fromTimestamp, toTimestamp, tripwireId, objectType}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("events")}`;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ term: { "sensor.id.keyword": sensorId } });
        queryBody.query.bool.must.push({ term: { "event.info.class.keyword": "tripwire" } });
        queryBody.query.bool.must.push({ range: { timestamp: { lte: toTimestamp } } });
        queryBody.query.bool.must.push({ range: { end: { gte: fromTimestamp } } });
        if(tripwireId!=null){
            queryBody.query.bool.must.push({ term: { "event.id.keyword": tripwireId } });
        }
        if (objectType!=null){
            queryBody.query.bool.must.push({ term: { "object.type.keyword": objectType } });
        }
        queryBody.aggs = {
            eventIds: {
                terms: {
                    field: "event.id.keyword",
                    size: 50
                },
                aggs: {
                    objectTypes: {
                        terms: {
                            field: "object.type.keyword",
                            size: 20
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
            }
        }
        let queryObject = { index, body: queryBody, size: 0 };
        let results = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return results;
    }

    async #getDetailedTripwireCounts(documentDb, input){
        let detailedTripEventCounts = new Map();
        switch(documentDb.getName()){
            case "Elasticsearch": {
                let results = await this.#getDetailedTripwireCountsFromEs(documentDb, input);
                if (!results.indexAbsent) {
                    for (let tripwireObject of results.body.aggregations.eventIds.buckets) {
                        let tripwireId = tripwireObject.key;
                        let objectTypeMap = new Map();
                        
                        for (let objectTypeBucket of tripwireObject.objectTypes.buckets) {
                            let objectType = objectTypeBucket.key;
                            let countMap = new Map();
                            
                            for (let countObject of objectTypeBucket.eventTypes.buckets) {
                                countMap.set(countObject.key, countObject.doc_count);
                            }
                            if (!countMap.has("IN")) {
                                countMap.set("IN", 0);
                            }
                            if (!countMap.has("OUT")) {
                                countMap.set("OUT", 0);
                            }
                            objectTypeMap.set(objectType, countMap);
                        }
                        detailedTripEventCounts.set(tripwireId, objectTypeMap);
                    }
                }
                return detailedTripEventCounts;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }
    
    async #getTripwireCountsOfObjectsFromEs(elasticDb, {sensorId, fromTimestamp, toTimestamp, tripwireId, objectType}) {
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("events")}`;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ term: { "sensor.id.keyword": sensorId } });
        queryBody.query.bool.must.push({ term: { "event.info.class.keyword": "tripwire" } });
        queryBody.query.bool.must.push({ range: { timestamp: { lte: toTimestamp } } });
        queryBody.query.bool.must.push({ range: { end: { gte: fromTimestamp } } });
        if(tripwireId!=null){
            queryBody.query.bool.must.push({ term: { "event.id.keyword": tripwireId } });
        }
        if(objectType!=null){
            queryBody.query.bool.must.push({ term: { "object.type.keyword": objectType } });
        }
        queryBody.aggs = {
            groupedBuckets: {
                composite: {
                    size: 100,
                    sources: [
                        {
                            eventId: {
                                terms: {
                                    field: "event.id.keyword"
                                }
                            }
                        },
                        {
                            objectType: {
                                terms: {
                                    field: "object.type.keyword"
                                }
                            }
                        },
                        {
                            objectId: {
                                terms: {
                                    field: "id.keyword"
                                }
                            }
                        }
                    ]
                },
                aggs: {
                    eventTypes: {
                        terms: {
                            field: "event.type.keyword"
                        }
                    }
                }
            }
        };
        let resultList = new Array();
        let queryObject = { index, body: queryBody, size: 0 };
        while (true) {
            let results = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
            if (!results.indexAbsent) {
                resultList.push(...results.body.aggregations.groupedBuckets.buckets);
                const after = results.body.aggregations.groupedBuckets.after_key;
                if (after) {
                    queryObject.body.aggs.groupedBuckets.composite.after = after;
                } else {
                    break;
                }
            }else{
                break;
            }
        }
        return resultList;
    }

    async #getEffectiveTripwireCounts(documentDb,input){
        let effectiveTripEventCounts = new Map();
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let resultList = await this.#getTripwireCountsOfObjectsFromEs(documentDb,input);
                for (let bucket of resultList) {
                    let tripwireId = bucket.key.eventId;
                    let objectType = bucket.key.objectType;
                    
                    // Get or create tripwire map
                    if(!effectiveTripEventCounts.has(tripwireId)){
                        effectiveTripEventCounts.set(tripwireId, new Map());
                    }
                    let objectTypeMap = effectiveTripEventCounts.get(tripwireId);
                    
                    // Get or create object type map
                    if(!objectTypeMap.has(objectType)){
                        objectTypeMap.set(objectType, new Map([["IN",0],["OUT",0]]));
                    }
                    let countMap = objectTypeMap.get(objectType);
                    
                    let inCount = 0;
                    let outCount = 0;
                    for(let eventType of bucket.eventTypes.buckets){
                        if(eventType.key==="IN"){
                            inCount=eventType.doc_count;
                        }else if(eventType.key==="OUT"){
                            outCount=eventType.doc_count;
                        }
                    }
                    let netCount = inCount-outCount;
                    if(netCount>0){
                        let currentInCount = countMap.get("IN");
                        countMap.set("IN",currentInCount+1);
                        if(netCount>1){
                            logger.warn(`[DATA] object id: ${bucket.key.objectId} (type: ${objectType}) has IN count greater than OUT count by ${netCount}.`);
                        }
                    }else if(netCount<0){
                        let currentOutCount = countMap.get("OUT");
                        countMap.set("OUT",currentOutCount+1);
                        if(netCount < -1){
                            logger.warn(`[DATA] object id: ${bucket.key.objectId} (type: ${objectType}) has OUT count greater than IN count by ${Math.abs(netCount)}.`);
                        }
                    }
                }
                return effectiveTripEventCounts;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    } 

    /** 
     * Retrieves an object containing effective and actual tripwire counts.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.sensorId - Sensor ID used to query tripwire counts.
     * @param {?string} [input.tripwireId=null] - Tripwire ID used to filter tripwire counts.
     * @param {string} input.fromTimestamp - fromTimestamp for the query in ISO 8601 format.
     * @param {string} input.toTimestamp - toTimestamp for the query in ISO 8601 format.
     * @param {?string} [input.objectType=null] - Object type used to filter tripwire counts.
     * @returns {Promise<Object>} Effective and actual tripwire counts are returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorId: "abc", fromTimestamp: "2023-01-12T11:20:10.000Z", toTimestamp: "2023-01-12T14:20:10.000Z"};
     * let tripwireMetricObject = new mdx.Metrics.TripwireEvent();
     * let tripwireCounts = await tripwireMetricObject.getTripwireCounts(elastic,input);
     */
    async getTripwireCounts(documentDb, input){
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
                    errorMessage: {
                        minLength: "sensorId should have atleast 1 character."
                    }
                },
                fromTimestamp: {
                    type: "string"
                },
                toTimestamp: {
                    type: "string"
                },
                tripwireId:{
                    type: ["string", "null"],
                    default:null,
                    minLength: 1,
                    errorMessage: {
                        minLength: "tripwireId should have atleast 1 character."
                    }
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
            required: ["sensorId", "fromTimestamp", "toTimestamp"],
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
        let [detailedCountMap, effectiveCountMap] = await Promise.all([
            this.#getDetailedTripwireCounts(documentDb,input),
            this.#getEffectiveTripwireCounts(documentDb,input)
        ]);
        let tripwireMetrics = new Array();
        let aggregatedCountMap = new Map();
        let aggregatedMetrics = { events: new Array() };
        
        for (let [tripwireId, objectTypeMap] of detailedCountMap) {
            let effectiveObjectTypeMap = effectiveCountMap.get(tripwireId);
            let events = new Array();
            
            for (let [objectType, eventTypeMap] of objectTypeMap) {
                let effectiveEventTypeMap = effectiveObjectTypeMap.get(objectType);
                
                for (let [eventType, actualCount] of eventTypeMap) {
                    let effectiveCount = effectiveEventTypeMap.get(eventType);
                    events.push({ type: eventType, count: effectiveCount, actualCount, objectType });
                    
                    // Aggregate by objectType and eventType
                    let aggregateKey = `${objectType}::${eventType}`;
                    if (!aggregatedCountMap.has(aggregateKey)) {
                        aggregatedCountMap.set(aggregateKey, {
                            type: eventType,
                            count: effectiveCount,
                            actualCount: actualCount,
                            objectType: objectType
                        });
                    } else {
                        let currentAggregateCounts = aggregatedCountMap.get(aggregateKey);
                        aggregatedCountMap.set(aggregateKey, {
                            type: eventType,
                            count: currentAggregateCounts.count + effectiveCount,
                            actualCount: currentAggregateCounts.actualCount + actualCount,
                            objectType: objectType
                        });
                    }
                }
            }
            tripwireMetrics.push({ id: tripwireId, events });
        }
        
        for (let [aggregateKey, countObject] of aggregatedCountMap) {
            aggregatedMetrics.events.push(countObject);
        }
        
        return ({ tripwireMetrics, aggregatedMetrics });
    }

    async #tripwireHistogramOfObjectsFromEs(elasticDb, {sensorId,tripwireId,fromTimestamp,toTimestamp,bucketSizeInSec,objectType}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("events")}`;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ term: { "event.info.class.keyword": "tripwire" } });
        queryBody.query.bool.must.push({ range: { end: { gte: fromTimestamp, lte: toTimestamp } } });
        queryBody.query.bool.must.push({ term: { "sensor.id.keyword": sensorId } });
        if(objectType!=null){
            queryBody.query.bool.must.push({ term: { "object.type.keyword": objectType } });
        }
        if(tripwireId!=null){
            queryBody.query.bool.must.push({ term: { "event.id.keyword": tripwireId } });
        }
        queryBody.aggs = {
            groupedBuckets: {
                composite: {
                    size: 100,
                    sources: [
                        {
                            eventId: {
                                terms: {
                                    field: "event.id.keyword"
                                }
                            }
                        },
                        {
                            objectType: {
                                terms: {
                                    field: "object.type.keyword"
                                }
                            }
                        },
                        {
                            bucketStartTime: {
                                date_histogram: {
                                    field: "end",
                                    fixed_interval: `${bucketSizeInSec}s`
                                }
                            }
                        },
                        {
                            objectId: {
                                terms: {
                                    field: "id.keyword"
                                }
                            }
                        }
                    ]
                },
                aggs: {
                    eventTypes: {
                        terms: {
                            field: "event.type.keyword"
                        }
                    }
                }
            }
        };
        let resultList = new Array();
        let queryObject = { index, body: queryBody, size: 0 };
        while (true) {
            let results = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
            if (!results.indexAbsent) {
                resultList.push(...results.body.aggregations.groupedBuckets.buckets);
                const after = results.body.aggregations.groupedBuckets.after_key;
                if (after) {
                    queryObject.body.aggs.groupedBuckets.composite.after = after;
                } else {
                    break;
                }
            }else{
                break;
            }
        }
        return resultList;
    }

    /** 
     * Retrieves an object containing histogram of actual and effective tripwire counts.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.sensorId - Sensor ID used to query the tripwire histogram.
     * @param {?string} [input.tripwireId=null] - Tripwire ID used to filter the tripwire histogram.
     * @param {string} [input.fromTimestamp] - Either fromTimestamp and toTimestamp should be present together or minutesAgo should be present.
     * @param {string} [input.toTimestamp] - Either fromTimestamp and toTimestamp should be present together or minutesAgo should be present.
     * @param {number} [input.minutesAgo] - Time window in minutes before now. Either fromTimestamp and toTimestamp should be present together or minutesAgo should be present.
     * @param {number} [input.bucketCount=20] - Number of histogram buckets returned.
     * @param {?string} [input.objectType=null] - Object type used to filter the tripwire histogram.
     * @returns {Promise<Object>} Histogram of actual and effective tripwire counts is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorId: "abc", fromTimestamp: "2023-01-12T11:20:10.000Z", toTimestamp: "2023-01-12T14:20:10.000Z",bucketCount:24};
     * let tripwireMetricObject = new mdx.Metrics.TripwireEvent();
     * let histogramResult = await tripwireMetricObject.getTripwireHistogram(elastic,input);
     */
    async getTripwireHistogram(documentDb,input){
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
                tripwireId:{
                    type: ["string", "null"],
                    default:null,
                    minLength: 1,
                    errorMessage: {
                        minLength: "tripwireId should have atleast 1 character."
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
                    minLength: 1,
                    default: null,
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
        let formattedResult = { bucketSizeInSec: input.bucketSizeInSec, tripwires: new Array() };
        switch(documentDb.getName()){
            case "Elasticsearch": {
                let uniqueObjectTypes = new Set();

                let histogramResultList = await this.#tripwireHistogramOfObjectsFromEs(documentDb,input);
                    
                if (input.objectType!=null){
                    uniqueObjectTypes.add(input.objectType);
                }else{
                    for (let bucket of histogramResultList) {
                        uniqueObjectTypes.add(bucket.key.objectType);
                    }
                }
                
                let emptyHistogram = Histogram.getEmptyHistogram({
                    bucketSizeInSec:input.bucketSizeInSec,
                    fromTimestamp:input.fromTimestamp,
                    toTimestamp:input.toTimestamp
                });

                let formattedEmptyHistogram = new Array();
                for(let emptyBucket of emptyHistogram){
                    let formattedEmptyBucket = {
                        start: emptyBucket.start,
                        end: emptyBucket.end,
                        objectTypeCountMaps: new Map()
                    };
                    for(let uniqueObjectType of uniqueObjectTypes){
                        formattedEmptyBucket.objectTypeCountMaps.set(uniqueObjectType, {
                            count: new Map([["IN",0],["OUT",0]]),
                            actualCount: new Map([["IN",0],["OUT",0]])
                        });
                    }
                    formattedEmptyHistogram.push(formattedEmptyBucket);
                }

                let tripwireHistogramMap = new Map();
                for (let bucket of histogramResultList) {
                    let tripwireId = bucket.key.eventId;
                    let objectType = bucket.key.objectType;
                    
                    if(!tripwireHistogramMap.has(tripwireId)){
                        let histogramBucketMap = new Map();
                        for(let formattedBucket of formattedEmptyHistogram){
                            let formattedEmptyBucket = deepcopy(formattedBucket);
                            histogramBucketMap.set(new Date(formattedBucket.start).valueOf(),formattedEmptyBucket);
                        }
                        tripwireHistogramMap.set(tripwireId,histogramBucketMap);
                    }
                    
                    let bucketStartTime = bucket.key.bucketStartTime;
                    let histogramBucketMap = tripwireHistogramMap.get(tripwireId);
                    let bucketObject = histogramBucketMap.get(bucketStartTime);
                    
                    let countMaps = bucketObject.objectTypeCountMaps.get(objectType);
                    let inCount = 0;
                    let outCount = 0;
                    for(let eventType of bucket.eventTypes.buckets){
                        if(eventType.key==="IN"){
                            inCount=eventType.doc_count;
                        }else if(eventType.key==="OUT"){
                            outCount=eventType.doc_count;
                        }
                    }
                    
                    let currentActualInCount = countMaps.actualCount.get("IN");
                    let currentActualOutCount = countMaps.actualCount.get("OUT");
                    countMaps.actualCount.set("IN",currentActualInCount+inCount);
                    countMaps.actualCount.set("OUT",currentActualOutCount+outCount);
                    
                    let netCount = inCount-outCount;
                    if(netCount>0){
                        let currentInCount = countMaps.count.get("IN");
                        countMaps.count.set("IN",currentInCount+1);
                        if(netCount>1){
                            logger.warn(`[DATA] id: ${bucket.key.objectId} (type: ${objectType}) has IN count greater than OUT count by ${netCount}.`)
                        }
                    }else if(netCount<0){
                        let currentOutCount = countMaps.count.get("OUT");
                        countMaps.count.set("OUT",currentOutCount+1);
                        if(netCount < -1){
                            logger.warn(`[DATA] id: ${bucket.key.objectId} (type: ${objectType}) has OUT count greater than IN count by ${Math.abs(netCount)}.`)
                        }
                    }
                }
                
                for (let [tripwireId, histogramBucketMap] of tripwireHistogramMap) {
                    let histogram = new Array();
                    
                    for (let [bucketStartTime, bucketObject] of histogramBucketMap) {
                        let formattedBucket = {
                            start: bucketObject.start,
                            end: bucketObject.end,
                            events: new Array()
                        };
                        
                        for(let [objectType, countMaps] of bucketObject.objectTypeCountMaps){
                            for(let [eventType, count] of countMaps.count){
                                let actualCount = countMaps.actualCount.get(eventType);
                                formattedBucket.events.push({
                                    type: eventType,
                                    count,
                                    actualCount,
                                    objectType
                                });
                            }
                        }
                        histogram.push(formattedBucket);
                    }
                    
                    if(histogram.length>0){
                        if(Utils.tsCompare(histogram[0].start,"<",input.fromTimestamp)){
                            histogram[0].start = input.fromTimestamp;
                        }
                        if(Utils.tsCompare(histogram[histogram.length-1].end,">",input.toTimestamp)){
                            histogram[histogram.length-1].end = input.toTimestamp;
                        }
                    }
                    
                    formattedResult.tripwires.push({
                        id: tripwireId,
                        histogram
                    });
                }
                return formattedResult;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }
}

module.exports = TripwireEvent;
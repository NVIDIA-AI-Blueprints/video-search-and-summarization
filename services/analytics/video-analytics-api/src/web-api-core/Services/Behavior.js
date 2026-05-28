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
const Elasticsearch = require("../Utils/Elasticsearch");
const Validator = require("../Utils/Validator");
const InternalServerError = require('../Errors/InternalServerError');
const InvalidInputError = require('../Errors/InvalidInputError');
const BadRequestError = require('../Errors/BadRequestError');
const Frames = require("./Frames");
const Utils = require("../Utils/Utils");

/** 
 * Class which defines Behavior
 * @memberof mdxWebApiCore.Services
 * */

class Behavior {

    static #defaultBehaviorResultSize = 25;
    static #maxBehaviorResultSize = 10000;
    static #maxBehaviorsInLocationQuery = 100;

    /**
     * returns the max behaviors that can be present in location query.
     * @public
     * @static
     * @returns {number} Max behaviors that can be present in location query is returned. The returned value is an integer.
     * @example
     * let result = mdx.Services.Behavior.getMaxBehaviorsInLocationQuery();
     */
    static getMaxBehaviorsInLocationQuery(){
        return this.#maxBehaviorsInLocationQuery;
    }

    async #getTimestampOfBehaviorFromEs(elasticDb,{sensorId,objectId,timestampWithinBehavior}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ term: { "sensor.id.keyword": sensorId } });
        queryBody.query.bool.must.push({ term: { "object.id.keyword": objectId } });
        queryBody.query.bool.must.push({ range: { timestamp: { lte: timestampWithinBehavior } } });
        queryBody.query.bool.must.push({ range: { end: { gte: timestampWithinBehavior } } });
        let queryObject = {
            index: `${indexPrefix}${Elasticsearch.getIndex("behavior")}`,
            body: queryBody,
            sort: "end:desc",
            _source_includes: ["timestamp"],
            size: 1
        }
        let behaviorTimestamp = null;
        let result =  await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        if(!result.indexAbsent) {
            result = Elasticsearch.searchResultFormatter(result.body);
            if(result.length>0){
                behaviorTimestamp = result[0].timestamp;
            }
        }
        return behaviorTimestamp;
    }

    /** 
     * returns the start timestamp of a behavior.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.sensorId - Sensor ID used to query the behavior.
     * @param {string} input.objectId - Object ID used to query the behavior.
     * @param {string} input.timestampWithinBehavior - Timestamp within the behavior in ISO 8601 format.
     * @returns {Promise<?string>} Start timestamp of a behavior is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorId: "abc", objectId: "200", timestampWithinBehavior: "2023-01-12T14:20:10.000Z"};
     * let behaviorObject = new mdx.Services.Behavior();
     * let timestamp = await behaviorObject.getTimestampOfBehavior(elastic,input);
     */
    async getTimestampOfBehavior(documentDb, input) {
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
                timestampWithinBehavior: {
                    type: "string"
                }
            },
            required: ["sensorId", "objectId", "timestampWithinBehavior"],
            errorMessage:{
                required: "Input should have required properties 'sensorId', 'objectId' and 'timestampWithinBehavior'."
            }
        }
        let validationResult = Validator.validateJsonSchema(input,schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        if(!Validator.isValidISOTimestamp(input.timestampWithinBehavior)){
            throw (new InvalidInputError("Invalid timestampWithinBehavior."));
        }
        let timestamp=null;
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                timestamp = await this.#getTimestampOfBehaviorFromEs(documentDb,input);
                return timestamp;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #getBehaviorsFromEs(elasticDb, {sensorId, place, objectId, objectType, fromTimestamp,toTimestamp,queryString,maxResultSize}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("behavior")}`;
        let queryBody = deepcopy(filterTemplate);
        if(fromTimestamp!=null && toTimestamp!=null){
            queryBody.query.bool.must.push({ range: { timestamp: { lte: toTimestamp } } });
            queryBody.query.bool.must.push({ range: { end: { gte: fromTimestamp } } });
        }
        if(place!=null){
            queryBody.query.bool.must.push({ prefix: { "place.name.keyword": place } });
        }else{
            queryBody.query.bool.must.push({ term: { "sensor.id.keyword": sensorId } });
            if(objectId!=null){
                queryBody.query.bool.must.push({ term: { "object.id.keyword": objectId } });
            }
        }
        if(objectType!=null){
            queryBody.query.bool.must.push({ term: { "object.type.keyword": objectType } });
        }
        if(queryString!=null){
            queryBody.query.bool.must.push({ query_string: { query: queryString } });
        }
        let queryObject = {
            index,
            body: queryBody,
            sort: "end:desc",
            size: (objectId != null) ? 1 : maxResultSize
        };
        let searchResults = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return searchResults;
    }

    /** 
     * Returns an object containing an array of behaviors.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} [input.sensorId] - Either sensorId or place should be present.
     * @param {string} [input.place] - Either sensorId or place should be present.
     * @param {?string} [input.objectId=null] - objectId can be present only if (sensorId, fromTimestamp and toTimestamp) are present. objectId can't be used together with either queryString or maxResultSize.
     * @param {string} [input.fromTimestamp] - Either queryString or (fromTimestamp and toTimestamp) or (queryString, fromTimestamp and toTimestamp) should be present.
     * @param {string} [input.toTimestamp] - Either queryString or (fromTimestamp and toTimestamp) or (queryString, fromTimestamp and toTimestamp) should be present.
     * @param {string} [input.queryString] - queryString follows lucene syntax. Either queryString or (fromTimestamp and toTimestamp) or (queryString, fromTimestamp and toTimestamp) should be present. queryString and objectId can't occur together.
     * @param {string} [input.objectType] - Object type used to filter behaviors.
     * @param {number} [input.maxResultSize=25] - Maximum number of behaviors returned. objectId and maxResultSize can't occur together. If objectId is not null, then maxResultSize is set to 1.
     * @returns {Promise<Object>} An object containing an array of behaviors is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorId: "abc", fromTimestamp: "2023-01-12T11:20:10.000Z", toTimestamp: "2023-01-12T14:20:10.000Z"};
     * let behaviorMetadata = new mdx.Services.Behavior();
     * let behaviorResult = await behaviorMetadata.getBehaviors(elastic,input);
     */
    async getBehaviors(documentDb, input){
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                sensorId: {
                    type: ["string","null"],
                    default: null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "sensorId should have atleast 1 character.",
                        maxLength: "sensorId should have atmost 10000 characters."
                    }
                },
                place: {
                    type: ["string","null"],
                    default: null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "place should have atleast 1 character.",
                        maxLength: "place should have atmost 10000 characters."
                    }
                },
                objectId: {
                    type: ["string","null"],
                    default: null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "objectId should have atleast 1 character.",
                        maxLength: "objectId should have atmost 10000 characters."
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
                objectType: {
                    type: ["string","null"],
                    default: null
                },
                queryString: {
                    type: ["string","null"],
                    default: null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "queryString should have atleast 1 character.",
                        maxLength: "queryString should have atmost 10000 characters."
                    }
                },
                maxResultSize: {
                    type: "integer",
                    minimum: 1,
                    maximum: Behavior.#maxBehaviorResultSize,
                    default: Behavior.#defaultBehaviorResultSize,
                    errorMessage: {
                        type: "maxResultSize is not an integer.",
                        minimum: "maxResultSize can have a minimum value of 1.",
                        maximum: `maxResultSize can have a maximum value of ${Behavior.#maxBehaviorResultSize}.`
                    }
                }
            },
            oneOf: [
                {
                    required: ["sensorId"]
                },
                {
                    required: ["place"]
                }
            ],
            dependentRequired: {
                fromTimestamp: ["toTimestamp"],
                toTimestamp: ["fromTimestamp"],
                objectId: ["sensorId", "fromTimestamp", "toTimestamp"]
            },
            not: {
                anyOf: [
                    { required: ["queryString","objectId"] },
                    { required: ["objectId", "maxResultSize"] }
                ]
            },
            errorMessage:{
                oneOf: "Only one of 'sensorId' or 'place' should exist in the query.",
                dependentRequired: "Input should either have both of 'fromTimestamp' and 'toTimestamp' or can't have any of them. 'objectId' requires 'sensorId', 'fromTimestamp' and 'toTimestamp'.",
                not: "'objectId' can't be used together with either 'queryString' or 'maxResultSize'."
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
        }
        if ("maxResultSize" in input && !Number.isFinite(input.maxResultSize)) {
            throw (new InvalidInputError("maxResultSize is not a finite integer."));
        }
        let behaviors = new Array();
        switch(documentDb.getName()){
            case "Elasticsearch": {
                let results = await this.#getBehaviorsFromEs(documentDb,input);
                if (!results.indexAbsent) {
                    behaviors = Elasticsearch.searchResultFormatter(results.body);
                }
                return {behaviors};
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        } 
    }

    /**
     * Returns sliced location data for a behavior based on the specified timestamp range.
     * @public
     * @static
     * @param {Object} behavior - Behavior object containing locations, smoothLocations, timeInterval, timestamp, and end timestamp
     * @param {string} fromTimestamp - Start timestamp in ISO format
     * @param {string} toTimestamp - End timestamp in ISO format
     * @returns {Object} Object containing sliced locations and smoothLocations
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const slicedData = mdx.Services.Behavior.getLocationsBasedOnTimestampRange(behavior, "2023-01-01T10:00:00.000Z", "2023-01-01T10:00:01.234Z");
     */
    static getLocationsBasedOnTimestampRange(behavior, fromTimestamp, toTimestamp){
        let timeRangeValidationResult = Validator.isValidTimeRange(fromTimestamp, toTimestamp);
        if (!timeRangeValidationResult.valid) {
            throw (new InvalidInputError(timeRangeValidationResult.reason));
        }
        if(Utils.tsCompare(fromTimestamp, ">", behavior.end)){
            throw (new InvalidInputError("fromTimestamp is later than Behavior's end timestamp."));
        }
        if(Utils.tsCompare(toTimestamp, "<", behavior.timestamp)){
            throw (new InvalidInputError("toTimestamp is earlier than Behavior's start timestamp."));
        }
        if(behavior.timeInterval === 0){
            return {locations: behavior.locations, smoothLocations: behavior.smoothLocations};
        }
        let startLocationIndex = null;
        let endLocationIndex = null;
        if(Utils.tsCompare(behavior.timestamp, ">=", fromTimestamp)){
            startLocationIndex = 0;
        }else{
            let startRatio = Utils.getTimeInterval(behavior.timestamp, fromTimestamp) / behavior.timeInterval;
            startLocationIndex = Math.floor(startRatio * behavior.locations.coordinates.length);
            if(startLocationIndex >= behavior.locations.coordinates.length){
                startLocationIndex = behavior.locations.coordinates.length - 1;
            }
        }
        if(Utils.tsCompare(behavior.end, "<=", toTimestamp)){
            endLocationIndex = behavior.locations.coordinates.length - 1;
        }else{
            let endRatio = Utils.getTimeInterval(behavior.timestamp, toTimestamp) / behavior.timeInterval;
            endLocationIndex = Math.ceil(endRatio * behavior.locations.coordinates.length);
            if(endLocationIndex >= behavior.locations.coordinates.length){
                endLocationIndex = behavior.locations.coordinates.length - 1;
            }
        }
        if(startLocationIndex == null || endLocationIndex == null){
            return {locations: behavior.locations, smoothLocations: behavior.smoothLocations};
        }else{
            const slicedLocations = deepcopy(behavior.locations);
            slicedLocations.coordinates = slicedLocations.coordinates.slice(startLocationIndex, endLocationIndex + 1);
            const slicedSmoothLocations = deepcopy(behavior.smoothLocations);
            slicedSmoothLocations.coordinates = slicedSmoothLocations.coordinates.slice(startLocationIndex, endLocationIndex + 1);
            return {locations: slicedLocations, smoothLocations: slicedSmoothLocations};
        }
    }
    
    /** 
     * returns the start and end pts of a behavior.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.sensorId - Sensor ID used to query behavior pts.
     * @param {number} input.endFrameId - End frame ID used to query behavior pts.
     * @param {number} input.behaviorTimeInterval - Behavior time interval used to query behavior pts.
     * @returns {Promise<Object>} Start and end pts of a behavior is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorId: "abc", endFrameId: 200, behaviorTimeInterval: 20};
     * let behaviorMetadata = new mdx.Services.Behavior();
     * let behaviorPts = await behaviorMetadata.getPts(elastic,input);
     */
    async getPts(documentDb, input){
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
                endFrameId: {
                    type: "integer"
                },
                behaviorTimeInterval: {
                    type: "number"
                }
            },
            required: ["sensorId", "endFrameId", "behaviorTimeInterval"],
            errorMessage:{
                required: "Input should have required properties 'sensorId', 'endFrameId' and 'behaviorTimeInterval'."
            }
        }
        let validationResult = Validator.validateJsonSchema(input,schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        if (!Number.isFinite(input.endFrameId)) {
            throw (new InvalidInputError("endFrameId is not a finite integer."));
        }
        if (!Number.isFinite(input.behaviorTimeInterval)) {
            throw (new InvalidInputError("behaviorTimeInterval is not a finite integer."));
        }
        let frameObject = new Frames();
        let endPts = await frameObject.getPts(documentDb,{sensorId:input.sensorId,frameId:input.endFrameId});
        endPts=endPts.pts;
        let behaviorTimeIntervalInMs=Math.floor(input.behaviorTimeInterval*1000);
        if(behaviorTimeIntervalInMs>endPts){
            throw(new InternalServerError("Invalid behaviorTimeInterval. behaviorTimeInterval should be smaller than endPts."));
        }
        let startPts = endPts - behaviorTimeIntervalInMs;
        return {startPts,endPts};
    }

    async #getLocationsOfBehaviorsFromES(elasticDb, {fromTimestamp, toTimestamp, behaviorIds}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ range: { timestamp: { lte: toTimestamp } } });
        queryBody.query.bool.must.push({ range: { end: { gte: fromTimestamp } } });
        let behaviorIdClauses = new Array();
        for (let behaviorId of behaviorIds) {
            behaviorIdClauses.push({ term: { "id.keyword": behaviorId } });
        }
        queryBody.query.bool.must.push({bool: {should: behaviorIdClauses, minimum_should_match: 1}});
        let queryObject = {
            index: `${indexPrefix}${Elasticsearch.getIndex("behavior")}`,
            body: queryBody,
            sort: "end:desc",
            size: 10000,
            _source_includes: [
                'Id', 'id', 'locations', 'smoothLocations', 'speedOverTime', 
                'speed', 'timeInterval', 'length', 'distance', 'bearing', 'timestamp', 'end'
            ],
        }
        let behaviors = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return behaviors;
    }

    /** 
     * returns the locations of a given set of input behaviors.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.fromTimestamp - fromTimestamp for the query in ISO 8601 format.
     * @param {string} input.toTimestamp - toTimestamp for the query in ISO 8601 format.
     * @param {Array<string>} input.behaviorIds - Behavior IDs used to query locations.
     * @returns {Promise<Object>} An object containing locations of the input behaviors is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {fromTimestamp: "2023-01-12T11:20:10.000Z", toTimestamp: "2023-01-12T14:20:10.000Z", behaviorIds: ["abc #-# 272","def #-# 2152"]};
     * let behaviorMetadata = new mdx.Services.Behavior();
     * let behaviorPts = await behaviorMetadata.getLocationsOfBehaviors(elastic,input);
     */
    async getLocationsOfBehaviors(documentDb, input){
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
                        errorMessage: {
                            minLength: "Element of behaviorIds array should have atleast 1 character."
                        }
                    },
                    minItems: 1,
                    maxItems: Behavior.#maxBehaviorsInLocationQuery,
                    errorMessage: {
                        minItems: "behaviorIds should have atleast 1 item",
                        maxItems: `behaviorIds can have atmost ${Behavior.#maxBehaviorsInLocationQuery} items.`
                    }
                }
            },
            required: ["fromTimestamp", "toTimestamp", "behaviorIds"],
            errorMessage: {
                required: "Input should have required properties 'behaviorIds', 'fromTimestamp' and 'toTimestamp'.",
            }
        }
        let validationResult = Validator.validateJsonSchema(input,schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        let timeRangeValidationResult = Validator.isValidTimeRange(input.fromTimestamp, input.toTimestamp);
        if (!timeRangeValidationResult.valid) {
            throw (new InvalidInputError(timeRangeValidationResult.reason));
        }
        let behaviors = new Array();
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let results = await this.#getLocationsOfBehaviorsFromES(documentDb, input);
                if (!results.indexAbsent) {
                    behaviors = Elasticsearch.searchResultFormatter(results.body);
                }
                return {behaviors};
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #getBehaviorsUsingIdsAndTimestampFromES(elasticDb, {behaviorInfo} ){
        if(behaviorInfo.length==0){
            return new Array();
        }
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        let queryBody = deepcopy(filterTemplate);
        let behaviorClauses = new Array();
        for (let behavior of behaviorInfo) {
            let conditions = {bool:{must:new Array()}};
            conditions.bool.must.push({term:{"sensor.id.keyword":behavior.sensorId}});
            conditions.bool.must.push({term:{"object.id.keyword":behavior.objectId}});
            conditions.bool.must.push({term:{"timestamp":behavior.timestamp}});
            behaviorClauses.push(conditions);
        }
        queryBody.query.bool.must.push({bool: {should: behaviorClauses, minimum_should_match: 1}});
        let queryObject = {
            index: `${indexPrefix}${Elasticsearch.getIndex("behavior")}`,
            body: queryBody,
            sort: "end:desc",
            size: behaviorInfo.length
        }
        let behaviors = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        if(!behaviors.indexAbsent) {
            behaviors = Elasticsearch.searchResultFormatter(behaviors.body);
        }
        return behaviors;
    }

    /** 
     * returns an object containing an array of behaviors.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {Array<{sensorId:string,objectId:string,timestamp:string}>} input.behaviorInfo - Behavior identifiers and timestamps used to query behaviors.
     * @returns {Promise<Object>} An object containing an array of behaviors is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {behaviorInfo:[{sensorId: "abc", objectId: "200", timestamp: "2023-01-12T14:20:10.000Z"},{sensorId: "def", objectId: "210", timestamp: "2023-01-12T14:24:10.000Z"}]};
     * let behaviorMetadata = new mdx.Services.Behavior();
     * let behaviorResult = await behaviorMetadata.getBehaviorsUsingIdsAndTimestamp(elastic,input);
     */ 
    async getBehaviorsUsingIdsAndTimestamp(documentDb,input){
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                behaviorInfo:{
                    type: "array",
                    items: {
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
                        required: ["sensorId","objectId","timestamp"],
                        errorMessage: {
                            required: "Object in behaviorInfo array should have required properties 'sensorId', 'objectId' and 'timestamp'.",
                        }
                    }
                }    
            },
            required: ["behaviorInfo"],
            errorMessage:{
                required: "Input should have required property 'behaviorInfo'."
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema, false);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        let invalidTimestampIndices = new Array();
        for(let i=0;i<input.behaviorInfo.length;i++){
            if (!Validator.isValidISOTimestamp(input.behaviorInfo[i].timestamp)) {
                invalidTimestampIndices.push(i);
            }
        }
        if(invalidTimestampIndices.length>0){
            throw (new InvalidInputError(`behaviorInfo has invalid timestamp in the following indices: ${invalidTimestampIndices.join(", ")}.`));
        }
        let behaviors = new Array();
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                behaviors = await this.#getBehaviorsUsingIdsAndTimestampFromES(documentDb,input);
                return {behaviors};
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }
}

module.exports = Behavior;
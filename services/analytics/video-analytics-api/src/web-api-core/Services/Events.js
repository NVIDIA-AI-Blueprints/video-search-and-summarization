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

/** 
 * Class which defines Events
 * @memberof mdxWebApiCore.Services
 * */

class Events {

    static #defaultTripwireEventsResultSize = 25;
    static #maxTripwireEventsResultSize = 500;
    static #defaultRoiEventsResultSize = 25;
    static #maxRoiEventsResultSize = 500;

    #getTripwireEventEsQueryBody({sensorId, place,tripwireId,fromTimestamp,toTimestamp,objectType}){
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ term: { "event.info.class.keyword": "tripwire" } });
        if(sensorId!=null){
            queryBody.query.bool.must.push({ term: { "sensor.id.keyword": sensorId } });
            if(tripwireId!=null){
                queryBody.query.bool.must.push({ term: { "event.id.keyword": tripwireId } });
            }
        }else{
            queryBody.query.bool.must.push({ term: { "place.name.keyword": place } });
        }
        queryBody.query.bool.must.push({ range: { timestamp: { lte: toTimestamp } } });
        queryBody.query.bool.must.push({ range: { end: { gte: fromTimestamp } } });
        if(objectType!=null){
            queryBody.query.bool.must.push({ term: { "object.type.keyword": objectType } });
        }
        return queryBody;
    }

    async #getTripwireEventsFromEs(elasticDb, input){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("events")}`;
        let queryBody = this.#getTripwireEventEsQueryBody(input);
        let queryObject = { 
            index, 
            body: queryBody,
            sort: "end:desc",
            size: input.maxResultSize,
            _source_includes:[
                'Id', 'id', 'timestamp', 'end', 'timeInterval', 'locations', 'smoothLocations', 'length', 'speedOverTime',  
                'sensor', 'event', 'place.name', 'analyticsModule', 'object', 'direction', 'speed', 'distance', 
                'bearing', 'videoPath'
            ]
        }
        let tripwireEvents = new Array();
        let results = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        if (!results.indexAbsent) {
            tripwireEvents = Elasticsearch.searchResultFormatter(results.body);
        }
        return tripwireEvents;
    }

    async #getEffectiveTripwireEventsFromEs(elasticDb,input){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("events")}`;
        let queryBody = this.#getTripwireEventEsQueryBody(input);
        let queryObject = { 
            index, 
            body: queryBody,
            sort: "end:desc",
            _source_includes:[
                'Id', 'id', 'timestamp', 'end', 'timeInterval', 'locations', 'smoothLocations', 'length', 'speedOverTime',  
                'sensor', 'event', 'place.name', 'analyticsModule', 'object', 'direction', 'speed', 'distance', 
                'bearing', 'videoPath'
            ]
        }
        let behaviorIdsOfTripwireEvents = new Set();
        let effectiveTripwireEvents = new Array();
        let scrollOutput = await Elasticsearch.getScrollSearchResults(elasticDb.getClient(), queryObject, false);
        if(!scrollOutput.indexAbsent){
            for(let tripwireEvent of scrollOutput.hitSources){
                if(!behaviorIdsOfTripwireEvents.has(tripwireEvent.id)){
                    effectiveTripwireEvents.push(tripwireEvent);
                    behaviorIdsOfTripwireEvents.add(tripwireEvent.id);
                    if (behaviorIdsOfTripwireEvents.size===input.maxResultSize) {
                        break;
                    }
                }
            }
        }
        return effectiveTripwireEvents;
    }

    /** 
     * returns an object containing an array of tripwire events.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} [input.sensorId] - Either sensorId or place should be present.
     * @param {string} [input.place] - Either sensorId or place should be present.
     * @param {?string} [input.tripwireId=null] - Tripwire ID can be present only if sensorId is present in the input.
     * @param {string} input.fromTimestamp - fromTimestamp for the query in ISO 8601 format.
     * @param {string} input.toTimestamp - toTimestamp for the query in ISO 8601 format.
     * @param {number} [input.maxResultSize=25] - Maximum number of tripwire events returned.
     * @param {string} [input.objectType=null] - Object type to filter data.
     * @param {boolean} [input.effective=true] - If effective is true, then the result returned will contain only the latest tripwire events of each object.
     * @returns {Promise<Object>} An object containing an array of tripwire events is returned.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorId: "abc", fromTimestamp: "2023-01-12T11:20:10.000Z", toTimestamp: "2023-01-12T14:20:10.000Z"};
     * let tripwireMetadata = new mdx.Services.Events();
     * let tripwireEvents = await tripwireMetadata.getTripwireEvents(elastic,input);
     */
    async getTripwireEvents(documentDb,input){
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                sensorId: {
                    type: ["string", "null"],
                    default:null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "sensorId should have atleast 1 character.",
                        maxLength: "sensorId should have atmost 10000 characters."
                    }
                },
                place: {
                    type: ["string", "null"],
                    default:null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "place should have atleast 1 character.",
                        maxLength: "place should have atmost 10000 characters."
                    }
                },
                tripwireId:{
                    type: ["string", "null"],
                    default:null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "tripwireId should have atleast 1 character.",
                        maxLength: "tripwireId should have atmost 10000 characters."
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
                    maximum: Events.#maxTripwireEventsResultSize,
                    default: Events.#defaultTripwireEventsResultSize,
                    errorMessage: {
                        type: "maxResultSize is not an integer.",
                        minimum: "maxResultSize can have a minimum value of 1.",
                        maximum: `maxResultSize can have a maximum value of ${Events.#maxTripwireEventsResultSize}.`
                    }
                },
                objectType:{
                    type: ["string","null"],
                    minLength: 1,
                    maxLength: 10000,
                    default: null,
                    errorMessage: {
                        minLength: "objectType should have atleast 1 character.",
                        maxLength: "objectType should have atmost 10000 characters."
                    }
                },
                effective:{
                    type: "boolean",
                    default: true,
                    errorMessage: {
                        type: "effective doesn't have a boolean value.",
                    }
                }
            },
            required: ["fromTimestamp", "toTimestamp"],
            oneOf:[
                {
                    required: ["sensorId"]
                },
                {
                    required: ["place"]
                }
            ],
            dependentRequired: {
                tripwireId: ["sensorId"]
            },
            errorMessage:{
                required: "Input should have required properties 'fromTimestamp' and 'toTimestamp'.",
                dependentRequired: "If input has 'tripwireId' then it should have 'sensorId'.",
                oneOf:"Input should have either 'sensorId' or 'place'."
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
        let tripwireEvents = new Array();
        switch(documentDb.getName()) {
            case "Elasticsearch": {
                if(input.effective){
                    tripwireEvents = await this.#getEffectiveTripwireEventsFromEs(documentDb,input);
                }else{
                    tripwireEvents = await this.#getTripwireEventsFromEs(documentDb,input);
                }
                return {tripwireEvents};
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #getObjectTypesOfTripwireEventsFromEs(elasticDb, {sensorId, place, tripwireId, fromTimestamp, toTimestamp}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("events")}`;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ term: { "event.info.class.keyword": "tripwire" } });
        if(sensorId!=null){
            queryBody.query.bool.must.push({ term: { "sensor.id.keyword": sensorId } });
            if(tripwireId!=null){
                queryBody.query.bool.must.push({ term: { "event.id.keyword": tripwireId } });
            }
        }else{
            queryBody.query.bool.must.push({ term: { "place.name.keyword": place } });
        }
        if(toTimestamp!=null){
            queryBody.query.bool.must.push({ range: { timestamp: { lte: toTimestamp } } });
        }
        if(fromTimestamp!=null){
            queryBody.query.bool.must.push({ range: { end: { gte: fromTimestamp } } });
        }
        queryBody.aggs = {
            objectTypes: {
                terms: {
                    field: "object.type.keyword",
                    size: 10000
                }
            }
        };
        let queryObject = { index, body: queryBody, size: 0 };
        let results = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return results;
    }

    /**
     * Returns unique object types observed in matching tripwire events.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} [input.sensorId] - Either sensorId or place should be present.
     * @param {string} [input.place] - Either sensorId or place should be present.
     * @param {?string} [input.tripwireId=null] - tripwireId can be present only if sensorId is present in the input.
     * @param {string} [input.fromTimestamp] - Optional start timestamp used with toTimestamp to filter tripwire events.
     * @param {string} input.toTimestamp - toTimestamp for the query in ISO 8601 format.
     * @returns {Promise<Set<string>>} Unique object types observed in matching tripwire events are returned.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorId: "abc", toTimestamp: "2023-01-12T14:20:10.000Z"};
     * let eventsObject = new mdx.Services.Events();
     * let objectTypes = await eventsObject.getObjectTypesOfTripwireEvents(elastic,input);
     */
    async getObjectTypesOfTripwireEvents(documentDb, input){
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                sensorId: {
                    type: ["string", "null"],
                    default:null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "sensorId should have atleast 1 character.",
                        maxLength: "sensorId should have atmost 10000 characters."
                    }
                },
                place: {
                    type: ["string", "null"],
                    default:null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "place should have atleast 1 character.",
                        maxLength: "place should have atmost 10000 characters."
                    }
                },
                tripwireId:{
                    type: ["string", "null"],
                    default:null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "tripwireId should have atleast 1 character.",
                        maxLength: "tripwireId should have atmost 10000 characters."
                    }
                },
                fromTimestamp: {
                    type: ["string","null"],
                    default: null
                },
                toTimestamp: {
                    type: ["string","null"],
                    default: null
                }
            },
            oneOf:[
                {
                    required: ["sensorId"]
                },
                {
                    required: ["place"]
                }
            ],
            dependentRequired: {
                tripwireId: ["sensorId"]
            },
            errorMessage: {
                dependentRequired: "If input has 'tripwireId' then it should have 'sensorId'.",
                oneOf:"Input should have either 'sensorId' or 'place'."
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        if(input.fromTimestamp!=null){
            let timeRangeValidationResult = Validator.isValidTimeRange(input.fromTimestamp, input.toTimestamp);
            if (!timeRangeValidationResult.valid) {
                throw (new InvalidInputError(timeRangeValidationResult.reason));
            }
        }else{
            if (!Validator.isValidISOTimestamp(input.toTimestamp)) {
                throw (new InvalidInputError("Invalid toTimestamp."));
            }
        }
        let uniqueObjectTypes = new Set();
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let results = await this.#getObjectTypesOfTripwireEventsFromEs(documentDb,input);
                if (!results.indexAbsent) {
                    if (results.body.hasOwnProperty("aggregations")) {
                        for (let objectTypeBucket of results.body.aggregations.objectTypes.buckets) {
                            uniqueObjectTypes.add(objectTypeBucket.key);
                        }
                    }
                }
                return uniqueObjectTypes;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }
    
    async #getRoiEventsFromEs(elasticDb, {sensorId, place,roiId,fromTimestamp,toTimestamp,objectType,maxResultSize}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("events")}`;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ term: { "event.info.class.keyword": "roi" } });
        if(sensorId!=null){
            queryBody.query.bool.must.push({ term: { "sensor.id.keyword": sensorId } });
            if(roiId!=null){
                queryBody.query.bool.must.push({ term: { "event.id.keyword": roiId } });
            }
        }else{
            queryBody.query.bool.must.push({ term: { "place.name.keyword": place } });
        }
        queryBody.query.bool.must.push({ range: { timestamp: { lte: toTimestamp } } });
        queryBody.query.bool.must.push({ range: { end: { gte: fromTimestamp } } });
        if(objectType!=null){
            queryBody.query.bool.must.push({ term: { "object.type.keyword": objectType } });
        }
        let queryObject = { 
            index, 
            body: queryBody,
            sort: "end:desc",
            size: maxResultSize,
            _source_includes:[
                'Id', 'id', 'timestamp', 'end', 'timeInterval', 'locations', 'smoothLocations', 'length', 'speedOverTime',  
                'sensor', 'event', 'place.name', 'analyticsModule', 'object', 'direction', 'speed', 'distance', 
                'bearing', 'videoPath'
            ]
        }
        let roiEvents = new Array();
        let results = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        if (!results.indexAbsent) {
            roiEvents = Elasticsearch.searchResultFormatter(results.body);
        }
        return roiEvents;
    }

    /**
     * Retrieves an object containing an array of ROI events.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} [input.sensorId] - Either sensorId or place should be present.
     * @param {string} [input.place] - Either sensorId or place should be present.
     * @param {?string} [input.roiId=null] - roiId can be present only if sensorId is present in the input.
     * @param {string} input.fromTimestamp - Start timestamp in ISO 8601 format.
     * @param {string} input.toTimestamp - End timestamp in ISO 8601 format.
     * @param {number} [input.maxResultSize=25] - Maximum number of ROI events returned.
     * @param {?string} [input.objectType=null] - Object type used to filter ROI events.
     * @returns {Promise<Object>} An object containing an array of ROI events is returned.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorId: "abc", fromTimestamp: "2023-01-12T11:20:10.000Z", toTimestamp: "2023-01-12T14:20:10.000Z"};
     * let eventsObject = new mdx.Services.Events();
     * let roiEvents = await eventsObject.getRoiEvents(elastic,input);
     */
    async getRoiEvents(documentDb, input){
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                sensorId: {
                    type: ["string", "null"],
                    default:null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "sensorId should have atleast 1 character.",
                        maxLength: "sensorId should have atmost 10000 characters."
                    }
                },
                place: {
                    type: ["string", "null"],
                    default:null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "place should have atleast 1 character.",
                        maxLength: "place should have atmost 10000 characters."
                    }
                },
                roiId:{
                    type: ["string", "null"],
                    default:null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "roiId should have atleast 1 character.",
                        maxLength: "roiId should have atmost 10000 characters."
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
                    maximum: Events.#maxRoiEventsResultSize,
                    default: Events.#defaultRoiEventsResultSize,
                    errorMessage: {
                        type: "maxResultSize is not an integer.",
                        minimum: "maxResultSize can have a minimum value of 1.",
                        maximum: `maxResultSize can have a maximum value of ${Events.#maxRoiEventsResultSize}.`
                    }
                },
                objectType:{
                    type: ["string","null"],
                    minLength: 1,
                    maxLength: 10000,
                    default: null,
                    errorMessage: {
                        minLength: "objectType should have atleast 1 character.",
                        maxLength: "objectType should have atmost 10000 characters."
                    }
                }
            },
            required: ["fromTimestamp", "toTimestamp"],
            oneOf:[
                {
                    required: ["sensorId"]
                },
                {
                    required: ["place"]
                }
            ],
            dependentRequired: {
                roiId: ["sensorId"]
            },
            errorMessage:{
                required: "Input should have required properties 'fromTimestamp' and 'toTimestamp'.",
                dependentRequired: "If input has 'roiId' then it should have 'sensorId'.",
                oneOf:"Input should have either 'sensorId' or 'place'."
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
        let roiEvents = new Array();
        switch(documentDb.getName()) {
            case "Elasticsearch": {
                roiEvents = await this.#getRoiEventsFromEs(documentDb,input);
                return {roiEvents};
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }
}

module.exports = Events;

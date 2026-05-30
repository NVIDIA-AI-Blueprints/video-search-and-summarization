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
const Calibration = require("./Calibration");
const Place = require("./Place");
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
const Utils = require("../Utils/Utils");
const NodeCache = require( "node-cache" );
let cache = new NodeCache();

/** 
 * Class used for obtaining incident related information.
 * @memberof mdxWebApiCore.Services
 * */

class Incident {
    
    static #defaultIncidentResultSize = 25;
    static #maxIncidentResultSize = 10000;
    static #defaultSevereIncidentTypes = ["Collision Detection"];

    async #getIncidentsFromEs(elasticDb, {sensorId, place, objectId, category, fromTimestamp, toTimestamp, queryString, maxResultSize, vlmVerified, vlmVerdict}) {
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        let index = indexPrefix;
        if (vlmVerified){
            index += Elasticsearch.getIndex("vlmIncidents");
        }else{
            index += Elasticsearch.getIndex("incidents");
        }
        let queryBody = deepcopy(filterTemplate);
        if(fromTimestamp != null && toTimestamp != null){
            queryBody.query.bool.must.push({ range: { timestamp: { lte: toTimestamp } } });
            queryBody.query.bool.must.push({ range: { end: { gte: fromTimestamp } } });
        }
        if(place != null){
            queryBody.query.bool.must.push({ prefix: { "place.name.keyword": place } });
        } else if(sensorId != null){
            queryBody.query.bool.must.push({ term: { "sensorId.keyword": sensorId } });
            if(objectId != null){
                queryBody.query.bool.must.push({ terms: { "objectIds": objectId } });
            }
        }
        if(category != null){
            queryBody.query.bool.must.push({ term: { "category.keyword": category } });
        }
        if(queryString != null){
            queryBody.query.bool.must.push({ query_string: { query: queryString } });
        }
        if(vlmVerified){
            if(vlmVerdict === "all"){
                // do nothing
            }else if(vlmVerdict === "not-confirmed"){
                queryBody.query.bool.must.push({ terms: { "info.verdict.keyword": ["rejected", "verification-failed"] } });
            }else{
                queryBody.query.bool.must.push({ term: { "info.verdict.keyword": vlmVerdict } });
            }
        }
        let queryObject = {
            index,
            body: queryBody,
            sort: "end:desc",
            size: maxResultSize
        }
        let searchResults = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return searchResults;
    }

    /** 
     * returns incident records that match the supplied filters.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {?string} [input.sensorId=null] - Sensor ID used to filter incidents. sensorId and place are mutually exclusive.
     * @param {?string} [input.place=null] - Place used to filter incidents. sensorId and place are mutually exclusive.
     * @param {?string} [input.objectId=null] - Object ID used to filter incidents. objectId requires sensorId, fromTimestamp and toTimestamp.
     * @param {?string} [input.category=null] - Incident category used to filter incidents.
     * @param {string} [input.fromTimestamp] - Either fromTimestamp and toTimestamp should be present together or neither should be present.
     * @param {string} [input.toTimestamp] - Either fromTimestamp and toTimestamp should be present together or neither should be present.
     * @param {?string} [input.queryString=null] - Query string used to filter incidents. queryString and objectId are mutually exclusive.
     * @param {boolean} [input.vlmVerified=false] - Whether to query VLM-verified incidents.
     * @param {string} [input.vlmVerdict] - VLM verdict filter. vlmVerdict can only be provided when vlmVerified is true and should be one of 'all', 'confirmed', 'rejected', 'verification-failed' or 'not-confirmed'.
     * @param {number} [input.maxResultSize=25] - Maximum number of incidents returned.
     * @returns {Promise<Object>} An object containing an array of incidents is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"}, databaseConfigMap);
     * let input = {sensorId: "sensor-1", fromTimestamp: "2023-01-12T11:20:10.000Z", toTimestamp: "2023-01-12T14:20:10.000Z"};
     * let incidentObject = new mdx.Services.Incidents();
     * let incidents = await incidentObject.getIncidents(elastic, input);
     */
    async getIncidents(documentDb, input) {
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
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
                category: {
                    type: ["string", "null"],
                    default: null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "category should have atleast 1 character.",
                        maxLength: "category should have atmost 10000 characters."
                    }
                },
                fromTimestamp: {
                    type: ["string", "null"],
                    default: null
                },
                toTimestamp: {
                    type: ["string", "null"],
                    default: null
                },
                queryString: {
                    type: ["string", "null"],
                    default: null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "queryString should have atleast 1 character.",
                        maxLength: "queryString should have atmost 10000 characters."
                    }
                },
                vlmVerified:{
                    type: "boolean",
                    default: false,
                    errorMessage: {
                        type: "vlmVerified doesn't have a boolean value.",
                    }
                },
                vlmVerdict:{
                    type: "string",
                    enum: ["all", "confirmed", "rejected", "verification-failed", "not-confirmed"],
                    errorMessage: {
                        enum: "vlmVerdict must be one of the following values: 'all', 'confirmed', 'rejected', 'verification-failed', 'not-confirmed'."
                    }
                },
                maxResultSize: {
                    type: "integer",
                    minimum: 1,
                    maximum: Incident.#maxIncidentResultSize,
                    default: Incident.#defaultIncidentResultSize,
                    errorMessage: {
                        type: "maxResultSize is not an integer.",
                        minimum: "maxResultSize can have a minimum value of 1.",
                        maximum: `maxResultSize can have a maximum value of ${Incident.#maxIncidentResultSize}.`
                    }
                }
            },
            dependentRequired: {
                fromTimestamp: ["toTimestamp"],
                toTimestamp: ["fromTimestamp"],
                objectId: ["sensorId", "fromTimestamp", "toTimestamp"]
            },
            dependentSchemas: {
                vlmVerdict: {
                    properties: {
                        vlmVerified: { const: true }
                    },
                    required: ["vlmVerified"],
                    errorMessage: "vlmVerdict can only be provided when vlmVerified is true."
                }
            },
            not: {
                anyOf: [
                    { required: ["queryString","objectId"] },
                    { required: ["sensorId","place"] }
                ]
            },
            if: {
                anyOf: [
                    { not: { required: ["vlmVerified"] } },
                    { properties: { vlmVerified: { enum: ['false', false] } } }
                ]
            },
            else: {
                if: {
                    properties: { vlmVerified: { enum: ['true', true] } }
                },
                then: {
                    properties: {
                        vlmVerdict: { default: "all" }
                    }
                }
            },
            errorMessage:{
                dependentRequired: "Input should either have both of 'fromTimestamp' and 'toTimestamp' or can't have any of them. 'objectId' requires 'sensorId', 'fromTimestamp' and 'toTimestamp'.",
                not: "'queryString' can't be used together with 'objectId'. Also, both 'sensorId' and 'place' cannot be provided together."
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        if(input.fromTimestamp != null && input.toTimestamp != null){
            let timeRangeValidationResult = Validator.isValidTimeRange(input.fromTimestamp, input.toTimestamp);
            if (!timeRangeValidationResult.valid) {
                throw (new InvalidInputError(timeRangeValidationResult.reason));
            }
        }
        if ("maxResultSize" in input && !Number.isInteger(input.maxResultSize)) {
            throw (new InvalidInputError("maxResultSize must be an integer."));
        }
        if (!("vlmVerdict" in input)) {
            input.vlmVerdict = null;
        }
        let incidents = new Array();
        switch (documentDb.getName()) {
            case "Elasticsearch":{
                let results = await this.#getIncidentsFromEs(documentDb,input);
                if(!results.indexAbsent){
                    incidents = Elasticsearch.searchResultFormatter(results.body);
                }
                return { incidents };
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #checkEsIfSevereIncidentSensor(elasticDb, { sensorId, fromTimestamp, toTimestamp, severeIncidentTypes, vlmVerified, vlmVerdict }){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        let index = indexPrefix;
        if (vlmVerified){
            index += Elasticsearch.getIndex("vlmIncidents");
        }else{
            index += Elasticsearch.getIndex("incidents");
        }
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ range: { timestamp: { lte: toTimestamp } } });
        queryBody.query.bool.must.push({ range: { end: { gte: fromTimestamp } } });
        queryBody.query.bool.must.push({ term: { "sensorId.keyword": sensorId } });
        if(severeIncidentTypes.length > 0){
            let severeIncidentClauses=new Array();
            for(let severeIncident of severeIncidentTypes){
                severeIncidentClauses.push({prefix: {"analyticsModule.id.keyword": severeIncident}});
            }
            queryBody.query.bool.must.push({bool: {should: severeIncidentClauses, minimum_should_match: 1}});
        }
        if(vlmVerified){
            if(vlmVerdict === "all"){
                // do nothing
            }else if(vlmVerdict === "not-confirmed"){
                queryBody.query.bool.must.push({ terms: { "info.verdict.keyword": ["rejected", "verification-failed"] } });
            }else{
                queryBody.query.bool.must.push({ term: { "info.verdict.keyword": vlmVerdict } });
            }
        }
        let queryObject = {
            index,
            body: queryBody,
            size: 1
        };
        let severeIncidents = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return severeIncidents;
    }

    async #isSevereIncidentSensor(documentDb, input) {
        let severeIncidentSensor = false;
        switch (documentDb.getName()) {
            case "Elasticsearch":{
                let severeIncidentsResult = await this.#checkEsIfSevereIncidentSensor(documentDb, input);
                if(!severeIncidentsResult.indexAbsent){
                    severeIncidentsResult = Elasticsearch.searchResultFormatter(severeIncidentsResult.body);
                    if(severeIncidentsResult.length > 0){
                        severeIncidentSensor = true;
                    }
                }
                return severeIncidentSensor;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #queryEsSevereIncidentsForNonLeafPlace(elasticDb, { place, fromTimestamp, toTimestamp, severeIncidentTypes, vlmVerified, vlmVerdict }){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        let index = indexPrefix;
        if (vlmVerified){
            index += Elasticsearch.getIndex("vlmIncidents");
        }else{
            index += Elasticsearch.getIndex("incidents");
        }
        let queryBody = Place.buildEsPlaceSuccessorAggQueryForNonLeafPlace({place});
        queryBody.query.bool.must.push({ range: { timestamp: { lte: toTimestamp } } });
        queryBody.query.bool.must.push({ range: { end: { gte: fromTimestamp } } });
        if(severeIncidentTypes.length > 0){
            let severeIncidentClauses=new Array();
            for(let severeIncident of severeIncidentTypes){
                severeIncidentClauses.push({prefix: {"analyticsModule.id.keyword": severeIncident}});
            }
            queryBody.query.bool.must.push({bool: {should: severeIncidentClauses, minimum_should_match: 1}});
        }
        if(vlmVerified){
            if(vlmVerdict === "all"){
                // do nothing
            }else if(vlmVerdict === "not-confirmed"){
                queryBody.query.bool.must.push({ terms: { "info.verdict.keyword": ["rejected", "verification-failed"] } });
            }else{
                queryBody.query.bool.must.push({ term: { "info.verdict.keyword": vlmVerdict } });
            }
        }
        let queryObject = {
            index,
            body: queryBody,
            size: 0
        };
        let severeIncidents = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return severeIncidents;
    }

    async #queryEsSevereIncidentsForPlaceWithSensor(elasticDb, { place, fromTimestamp, toTimestamp, severeIncidentTypes, vlmVerified, vlmVerdict }){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        let index = indexPrefix;
        if (vlmVerified){
            index += Elasticsearch.getIndex("vlmIncidents");
        }else{
            index += Elasticsearch.getIndex("incidents");
        }
        let queryBody = Place.buildEsSensorIdAggQueryForLeafPlace({place, sensorIdField: "sensorId"});
        queryBody.query.bool.must.push({ range: { timestamp: { lte: toTimestamp } } });
        queryBody.query.bool.must.push({ range: { end: { gte: fromTimestamp } } });
        if(severeIncidentTypes.length > 0){
            let severeIncidentClauses=new Array();
            for(let severeIncident of severeIncidentTypes){
                severeIncidentClauses.push({prefix: {"analyticsModule.id.keyword": severeIncident}});
            }
            queryBody.query.bool.must.push({bool: {should: severeIncidentClauses, minimum_should_match: 1}});
        }
        if(vlmVerified){
            if(vlmVerdict === "all"){
                // do nothing
            }else if(vlmVerdict === "not-confirmed"){
                queryBody.query.bool.must.push({ terms: { "info.verdict.keyword": ["rejected", "verification-failed"] } });
            }else{
                queryBody.query.bool.must.push({ term: { "info.verdict.keyword": vlmVerdict } });
            }
        }
        let queryObject = {
            index,
            body: queryBody,
            size: 0
        };
        let severeIncidents = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return severeIncidents;
    }

    async #getSevereIncidentsOfPlaceWithSensor(documentDb, input){
        let sensors = new Set();
        switch(documentDb.getName()){
            case "Elasticsearch":{
                let severeIncidentsResult = await this.#queryEsSevereIncidentsForPlaceWithSensor(documentDb, input);
                if(!severeIncidentsResult.indexAbsent){
                    severeIncidentsResult = severeIncidentsResult.body;
                    if (severeIncidentsResult.hasOwnProperty("aggregations")) {
                        for (let sensorObject of severeIncidentsResult.aggregations.sensorIds.buckets) {
                            sensors.add(sensorObject.key);
                        }
                    }
                }
                return sensors;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }        
    }

    async #getSevereIncidentsOfNonLeafPlace(documentDb, input){
        let places = new Set();
        switch(documentDb.getName()){
            case "Elasticsearch":{
                let severeIncidentsResult = await this.#queryEsSevereIncidentsForNonLeafPlace(documentDb, input);
                if(!severeIncidentsResult.indexAbsent){
                    severeIncidentsResult=severeIncidentsResult.body;
                    if (severeIncidentsResult.hasOwnProperty("aggregations")) {
                        for (let placeObject of severeIncidentsResult.aggregations.placeSuccessor.buckets) {
                            places.add(placeObject.key);
                        }
                    }
                }
                return places;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }        
    }

    /** 
     * returns severe-incident coverage for a sensor or place over a time range.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {?string} [input.sensorId=null] - Sensor ID used to check severe incidents. Exactly one of sensorId or place should be present.
     * @param {?string} [input.place=null] - Place used to check severe incidents. Exactly one of sensorId or place should be present.
     * @param {string} input.fromTimestamp - fromTimestamp for the query in ISO 8601 format.
     * @param {string} input.toTimestamp - toTimestamp for the query in ISO 8601 format.
     * @param {Array<string>} [input.severeIncidentTypes=["Collision Detection"]] - Severe incident types used to filter incidents.
     * @param {boolean} [input.vlmVerified=false] - Whether to query VLM-verified incidents.
     * @param {string} [input.vlmVerdict] - VLM verdict filter. vlmVerdict can only be provided when vlmVerified is true and should be one of 'all', 'confirmed', 'rejected', 'verification-failed' or 'not-confirmed'.
     * @returns {Promise<Object>} Severe-incident matches grouped by sensors and/or places are returned.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"}, databaseConfigMap);
     * let input = {place: "building=abc/room=xyz", fromTimestamp: "2023-01-12T11:20:10.000Z", toTimestamp: "2023-01-12T14:20:10.000Z"};
     * let incidentObject = new mdx.Services.Incidents();
     * let severeIncidents = await incidentObject.getSevereIncidentsResult(elastic, input);
     */
    async getSevereIncidentsResult(documentDb, input){
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
                fromTimestamp: {
                    type: ["string","null"],
                    default: null
                },
                toTimestamp: {
                    type: ["string","null"],
                    default: null
                },
                severeIncidentTypes:{
                    type: "array",
                    default: Incident.#defaultSevereIncidentTypes,
                    items: {
                        type: "string",
                        minLength: 1,
                        maxLength: 10000,
                        errorMessage: {
                            minLength: "Element of severeIncidentTypes array should have atleast 1 character.",
                            maxLength: "Element of severeIncidentTypes array should have atmost 10000 characters."                            
                        }
                    },
                    minItems: 1,
                    errorMessage: {
                        minItems: "severeIncidentTypes should have atleast 1 item"
                    }
                },
                vlmVerified:{
                    type: "boolean",
                    default: false,
                    errorMessage: {
                        type: "vlmVerified doesn't have a boolean value.",
                    }
                },
                vlmVerdict:{
                    type: "string",
                    enum: ["all", "confirmed", "rejected", "verification-failed", "not-confirmed"],
                    errorMessage: {
                        enum: "vlmVerdict must be one of the following values: 'all', 'confirmed', 'rejected', 'verification-failed', 'not-confirmed'."
                    }
                }
            },
            required: ["fromTimestamp", "toTimestamp"],
            oneOf: [
                {
                    required: ["sensorId"]
                },
                {
                    required: ["place"]
                }
            ],
            dependentSchemas: {
                vlmVerdict: {
                    properties: {
                        vlmVerified: { const: true }
                    },
                    required: ["vlmVerified"],
                    errorMessage: "vlmVerdict can only be provided when vlmVerified is true."
                }
            },
            if: {
                anyOf: [
                    { not: { required: ["vlmVerified"] } },
                    { properties: { vlmVerified: { enum: ['false', false] } } }
                ]
            },
            else: {
                if: {
                    properties: { vlmVerified: { enum: ['true', true] } }
                },
                then: {
                    properties: {
                        vlmVerdict: { default: "all" }
                    }
                }
            },
            errorMessage:{
                oneOf: "Only one of 'sensorId' or 'place' should exist in the query.",
                required: "Input should have required properties 'fromTimestamp' and 'toTimestamp'."
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
        if (!("vlmVerdict" in input)) {
            input.vlmVerdict = null;
        }
        if(input.sensorId != null){
            let severeIncidentSensor = await this.#isSevereIncidentSensor(documentDb,input);
            let severeIncidents = {sensors: new Array()};
            if(severeIncidentSensor){
                severeIncidents.sensors.push(input.sensorId);
            }
            return { severeIncidents };
        } else {
            let calibrationObject = new Calibration();
            let {timestamp, calibration} = await calibrationObject.getCalibration(documentDb);
            if(calibration.calibrationType === "" && calibration.sensors.length == 0){
                logger.warn("[CALIBRATION] Calibration file is not present.");
            }
            let cachedCalibrationTimestamp = cache.get("calibration-timestamp");
            if(cachedCalibrationTimestamp == undefined || Utils.tsCompare(timestamp,">",cachedCalibrationTimestamp)){
                let calibrationMaps = calibrationObject.getCalibrationMaps(calibration);
                cache.set("placeHierarchyMap", calibrationMaps.placeHierarchyMap);
                cache.set("sensorPlaceMap", calibrationMaps.sensorPlaceMap);
                cache.set("calibration-timestamp", timestamp);
            }
            let cachedPlaceHierarchyMap = cache.get("placeHierarchyMap");
            if(!cachedPlaceHierarchyMap.has(input.place)){
                return { severeIncidents: null };
            }
            let placeDetails = cachedPlaceHierarchyMap.get(input.place);
            let placeMetadata = {
                leafPlace: (placeDetails.places != null) ? false : true,
                sensor: (placeDetails.sensors != null) ? true : false
            };
            if (!placeMetadata.leafPlace && placeMetadata.sensor) {
                let [places,sensors] = await Promise.all([
                    this.#getSevereIncidentsOfNonLeafPlace(documentDb, input),
                    this.#getSevereIncidentsOfPlaceWithSensor(documentDb, input)
                ]);
                return { severeIncidents: {places: Array.from(places), sensors: Array.from(sensors)} };
            } else if (!placeMetadata.leafPlace) {
                let places = await this.#getSevereIncidentsOfNonLeafPlace(documentDb, input);
                return { severeIncidents: {places: Array.from(places)} };
            } else if (placeMetadata.leafPlace) {
                let sensors = await this.#getSevereIncidentsOfPlaceWithSensor(documentDb, input);
                return { severeIncidents: {sensors: Array.from(sensors)} };
            }
        }
    }
}

module.exports = Incident;

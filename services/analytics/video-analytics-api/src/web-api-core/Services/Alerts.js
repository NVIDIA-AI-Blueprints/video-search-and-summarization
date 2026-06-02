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
 * Class used for obtaining alerts related information.
 * @memberof mdxWebApiCore.Services
 * */

class Alerts {
    
    static #defaultAlertResultSize = 25;
    static #maxAlertResultSize = 10000;
    static #defaultSevereAlertTypes = ["Abnormal Movement"];

    async #getAlertsFromEs(elasticDb, {sensorId, place, objectId, objectType, fromTimestamp, toTimestamp, queryString, maxResultSize, vlmVerified, vlmVerdict}) {
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        let index = indexPrefix;
        if (vlmVerified){
            index += Elasticsearch.getIndex("vlmAlerts");
        }else{
            index += Elasticsearch.getIndex("alerts");
        }
        let queryBody = deepcopy(filterTemplate);
        if(fromTimestamp!=null && toTimestamp!=null){
            queryBody.query.bool.must.push({ range: { timestamp: { lte: toTimestamp } } });
            queryBody.query.bool.must.push({ range: { end: { gte: fromTimestamp } } });
        }
        if(place!=null){
            queryBody.query.bool.must.push({ prefix: { "place.name.keyword": place } });
        }else if(sensorId != null){
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
     * Retrieves alert records that match the supplied filters.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {?string} [input.sensorId=null] - Sensor ID used to filter alerts. sensorId and place are mutually exclusive.
     * @param {?string} [input.place=null] - Place used to filter alerts. sensorId and place are mutually exclusive.
     * @param {?string} [input.objectId=null] - Object ID used to filter alerts. objectId requires sensorId, fromTimestamp and toTimestamp.
     * @param {string} [input.fromTimestamp] - Either fromTimestamp and toTimestamp should be present together or neither should be present.
     * @param {string} [input.toTimestamp] - Either fromTimestamp and toTimestamp should be present together or neither should be present.
     * @param {?string} [input.objectType=null] - Object type used to filter alerts.
     * @param {?string} [input.queryString=null] - Query string used to filter alerts. queryString and objectId are mutually exclusive.
     * @param {boolean} [input.vlmVerified=false] - Whether to query VLM-verified alerts.
     * @param {string} [input.vlmVerdict] - VLM verdict filter. vlmVerdict can only be provided when vlmVerified is true and should be one of 'all', 'confirmed', 'rejected', 'verification-failed' or 'not-confirmed'.
     * @param {number} [input.maxResultSize=25] - Maximum number of alerts returned.
     * @returns {Promise<Object>} An object containing an array of alerts is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"}, databaseConfigMap);
     * let input = {sensorId: "sensor-1", fromTimestamp: "2023-01-12T11:20:10.000Z", toTimestamp: "2023-01-12T14:20:10.000Z"};
     * let alertsObject = new mdx.Services.Alerts();
     * let alerts = await alertsObject.getAlerts(elastic, input);
     */
    async getAlerts(documentDb, input) {
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
                    maximum: Alerts.#maxAlertResultSize,
                    default: Alerts.#defaultAlertResultSize,
                    errorMessage: {
                        type: "maxResultSize is not an integer.",
                        minimum: "maxResultSize can have a minimum value of 1.",
                        maximum: `maxResultSize can have a maximum value of ${Alerts.#maxAlertResultSize}.`
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
        if(input.fromTimestamp!=null && input.toTimestamp!=null){
            let timeRangeValidationResult = Validator.isValidTimeRange(input.fromTimestamp, input.toTimestamp);
            if (!timeRangeValidationResult.valid) {
                throw (new InvalidInputError(timeRangeValidationResult.reason));
            }
        }
        if ("maxResultSize" in input && !Number.isFinite(input.maxResultSize)) {
            throw (new InvalidInputError("maxResultSize is not a finite integer."));
        }
        if (!("vlmVerdict" in input)) {
            input.vlmVerdict = null;
        }
        let alerts = new Array();
        switch (documentDb.getName()) {
            case "Elasticsearch":{
                let results = await this.#getAlertsFromEs(documentDb,input);
                if(!results.indexAbsent){
                    alerts = Elasticsearch.searchResultFormatter(results.body);
                }
                return {alerts};
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #checkEsIfSevereAlertSensor(elasticDb, {sensorId, fromTimestamp, toTimestamp, severeAlertTypes, vlmVerified, vlmVerdict}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        let index = indexPrefix;
        if (vlmVerified){
            index += Elasticsearch.getIndex("vlmAlerts");
        }else{
            index += Elasticsearch.getIndex("alerts");
        }
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ range: { timestamp: { lte: toTimestamp } } });
        queryBody.query.bool.must.push({ range: { end: { gte: fromTimestamp } } });
        queryBody.query.bool.must.push({ term: { "sensor.id.keyword": sensorId } });
        let severeAlertClauses=new Array();
        for(let severeAlert of severeAlertTypes){
            severeAlertClauses.push({prefix: {"analyticsModule.description.keyword": severeAlert}});
        }
        queryBody.query.bool.must.push({bool:{should:severeAlertClauses, minimum_should_match: 1}});
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
        let severeAlerts = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return severeAlerts;
    }

    async #isSevereAlertSensor(documentDb, input) {
        let severeAlertSensor = false;
        switch (documentDb.getName()) {
            case "Elasticsearch":{
                let severeAlertsResult = await this.#checkEsIfSevereAlertSensor(documentDb, input);
                if(!severeAlertsResult.indexAbsent){
                    severeAlertsResult=Elasticsearch.searchResultFormatter(severeAlertsResult.body);
                    if(severeAlertsResult.length>0){
                        severeAlertSensor=true;
                    }
                }
                return severeAlertSensor;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #queryEsSevereAlertsForPlaceWithSensor(elasticDb, {place, fromTimestamp, toTimestamp, severeAlertTypes, vlmVerified, vlmVerdict}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        let index = indexPrefix;
        if (vlmVerified){
            index += Elasticsearch.getIndex("vlmAlerts");
        }else{
            index += Elasticsearch.getIndex("alerts");
        }
        let queryBody = Place.buildEsSensorIdAggQueryForLeafPlace({place, sensorIdField: "sensor.id"});
        queryBody.query.bool.must.push({ range: { timestamp: { lte: toTimestamp } } });
        queryBody.query.bool.must.push({ range: { end: { gte: fromTimestamp } } });
        let severeAlertClauses=new Array();
        for(let severeAlert of severeAlertTypes){
            severeAlertClauses.push({prefix: {"analyticsModule.description.keyword": severeAlert}});
        }
        queryBody.query.bool.must.push({bool:{should:severeAlertClauses, minimum_should_match: 1}});
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
        let severeAlerts = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return severeAlerts;
    }

    async #getSevereAlertsOfPlaceWithSensor(documentDb, input){
        let sensors = new Set();
        switch(documentDb.getName()){
            case "Elasticsearch":{
                let severeAlertsResult = await this.#queryEsSevereAlertsForPlaceWithSensor(documentDb, input);
                if(!severeAlertsResult.indexAbsent){
                    severeAlertsResult=severeAlertsResult.body;
                    if (severeAlertsResult.hasOwnProperty("aggregations")) {
                        for (let sensorObject of severeAlertsResult.aggregations.sensorIds.buckets) {
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

    async #queryEsSevereAlertsForNonLeafPlace(elasticDb, {place, fromTimestamp, toTimestamp, severeAlertTypes, vlmVerified, vlmVerdict}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        let index = indexPrefix;
        if (vlmVerified){
            index += Elasticsearch.getIndex("vlmAlerts");
        }else{
            index += Elasticsearch.getIndex("alerts");
        }
        let queryBody = Place.buildEsPlaceSuccessorAggQueryForNonLeafPlace({place});
        queryBody.query.bool.must.push({ range: { timestamp: { lte: toTimestamp } } });
        queryBody.query.bool.must.push({ range: { end: { gte: fromTimestamp } } });
        let severeAlertClauses=new Array();
        for(let severeAlert of severeAlertTypes){
            severeAlertClauses.push({prefix: {"analyticsModule.description.keyword": severeAlert}});
        }
        queryBody.query.bool.must.push({bool:{should:severeAlertClauses, minimum_should_match: 1}});
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
        let severeAlerts = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return severeAlerts;
    }

    async #getSevereAlertsOfNonLeafPlace(documentDb, input){
        let places = new Set();
        switch(documentDb.getName()){
            case "Elasticsearch":{
                let severeAlertsResult = await this.#queryEsSevereAlertsForNonLeafPlace(documentDb, input);
                if(!severeAlertsResult.indexAbsent){
                    severeAlertsResult=severeAlertsResult.body;
                    if (severeAlertsResult.hasOwnProperty("aggregations")) {
                        for (let placeObject of severeAlertsResult.aggregations.placeSuccessor.buckets) {
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
     * Retrieves severe-alert coverage for a sensor or place over a time range.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {?string} [input.sensorId=null] - Sensor ID used to check severe alerts. Exactly one of sensorId or place should be present.
     * @param {?string} [input.place=null] - Place used to check severe alerts. Exactly one of sensorId or place should be present.
     * @param {string} input.fromTimestamp - fromTimestamp for the query in ISO 8601 format.
     * @param {string} input.toTimestamp - toTimestamp for the query in ISO 8601 format.
     * @param {Array<string>} [input.severeAlertTypes=["Abnormal Movement"]] - Severe alert types used to filter alerts.
     * @param {boolean} [input.vlmVerified=false] - Whether to query VLM-verified alerts.
     * @param {string} [input.vlmVerdict] - VLM verdict filter. vlmVerdict can only be provided when vlmVerified is true and should be one of 'all', 'confirmed', 'rejected', 'verification-failed' or 'not-confirmed'.
     * @returns {Promise<Object>} Severe-alert matches grouped by sensors and/or places are returned.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"}, databaseConfigMap);
     * let input = {place: "building=abc/room=xyz", fromTimestamp: "2023-01-12T11:20:10.000Z", toTimestamp: "2023-01-12T14:20:10.000Z"};
     * let alertsObject = new mdx.Services.Alerts();
     * let severeAlerts = await alertsObject.getSevereAlertsResult(elastic, input);
     */
    async getSevereAlertsResult(documentDb, input){
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
                severeAlertTypes:{
                    type: "array",
                    default: Alerts.#defaultSevereAlertTypes,
                    items: {
                        type: "string",
                        minLength: 1,
                        maxLength: 10000,
                        errorMessage: {
                            minLength: "Element of severeAlertTypes array should have atleast 1 character.",
                            maxLength: "Element of severeAlertTypes array should have atmost 10000 characters."                            
                        }
                    },
                    minItems: 1,
                    errorMessage: {
                        minItems: "severeAlertTypes should have atleast 1 item"
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
        if(input.sensorId!=null){
            let severeAlertSensor = await this.#isSevereAlertSensor(documentDb,input);
            let severeAlerts = {sensors: new Array()};
            if(severeAlertSensor){
                severeAlerts.sensors.push(input.sensorId);
            }
            return { severeAlerts };
        }else{
            let calibrationObject = new Calibration();
            let {timestamp,calibration} = await calibrationObject.getCalibration(documentDb);
            if(calibration.calibrationType === "" && calibration.sensors.length == 0){
                logger.warn("[CALIBRATION] Calibration file is not present.");
            }
            let cachedCalibrationTimestamp = cache.get("calibration-timestamp");
            if(cachedCalibrationTimestamp == undefined || Utils.tsCompare(timestamp,">",cachedCalibrationTimestamp)){
                let calibrationMaps = calibrationObject.getCalibrationMaps(calibration);
                cache.set("placeHierarchyMap",calibrationMaps.placeHierarchyMap);
                cache.set("sensorPlaceMap",calibrationMaps.sensorPlaceMap);
                cache.set("calibration-timestamp",timestamp);
            }
            let cachedPlaceHierarchyMap = cache.get("placeHierarchyMap");
            if(!cachedPlaceHierarchyMap.has(input.place)){
                return { severeAlerts: null };
            }
            let placeDetails = cachedPlaceHierarchyMap.get(input.place);
            let placeMetadata = {
                leafPlace: (placeDetails.places != null) ? false : true,
                sensor: (placeDetails.sensors != null) ? true : false
            };
            if (!placeMetadata.leafPlace && placeMetadata.sensor) {
                let [places,sensors] = await Promise.all([
                    this.#getSevereAlertsOfNonLeafPlace(documentDb, input),
                    this.#getSevereAlertsOfPlaceWithSensor(documentDb, input)
                ]);
                return { severeAlerts: {places: Array.from(places),sensors: Array.from(sensors)} };
            } else if (!placeMetadata.leafPlace) {
                let places = await this.#getSevereAlertsOfNonLeafPlace(documentDb, input);
                return { severeAlerts: {places: Array.from(places)} };
            } else if (placeMetadata.leafPlace) {
                let sensors = await this.#getSevereAlertsOfPlaceWithSensor(documentDb, input);
                return { severeAlerts: {sensors: Array.from(sensors)} };
            }
        }
    }
}

module.exports = Alerts;

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
const BadRequestError = require('../Errors/BadRequestError');
const Calibration = require("../Services/Calibration");
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
const NodeCache = require( "node-cache" );
let cache = new NodeCache();


/** 
 * Class which defines LastProcessedTimestamp
 * @memberof mdxWebApiCore.Metrics
 * */

class LastProcessedTimestamp {

    async #getLastProcessedTsOfSensorFromEs(elasticDb, {sensorId}){
        const index = elasticDb.getConfigs().get("rawIndex");
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ term: { "sensorId.keyword": sensorId } });
        let queryObject = {
            index,
            body: queryBody,
            size: 1,
            sort: "timestamp:desc",
            _source_includes: ["timestamp"]
        };
        let results = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return results;
    }

    async #getLastProcessedTsOfSensor(documentDb, input){
        let lastProcessedTimestamp = null;
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let results = await this.#getLastProcessedTsOfSensorFromEs(documentDb,input);
                if (!results.indexAbsent) {
                    results = Elasticsearch.searchResultFormatter(results.body);
                    if (results.length > 0) {
                        lastProcessedTimestamp = results[0]["timestamp"];
                    }
                }
                return lastProcessedTimestamp;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #getLastProcessedTsOfPlaceWithSensorFromEs(elasticDb, {place}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("behavior")}`;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ term: { "place.name.keyword": place } });
        queryBody.aggs = {
            sensorIds: {
                terms: {
                    field: "sensor.id.keyword",
                    size: 10000
                },
                aggs: {
                    lastProcessedTimestamp: {
                        top_hits: {
                            size: 1,
                            sort: [
                                {
                                    end: {
                                        order: "desc"
                                    }
                                }
                            ],
                            _source: {
                                includes: ["end"]
                            }
                        }
                    }
                }
            }
        };
        let queryObject = {
            index,
            body: queryBody,
            size: 1,
            sort: "end:desc",
            _source_includes: ["sensor.id", "end"]
        };
        let results = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return results;
    }

    async #getLastProcessedTsOfPlaceWithSensor(documentDb, input){
        let lastProcessedTsDetails = { latestTimestamp: null, sensorTimestampMap: new Map() };
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let results = await this.#getLastProcessedTsOfPlaceWithSensorFromEs(documentDb, input);
                if (!results.indexAbsent) {
                    results = results.body;
                    let lastProcessedTs = Elasticsearch.searchResultFormatter(results);
                    if (lastProcessedTs.length > 0) {
                        lastProcessedTsDetails.latestTimestamp = {
                            sensorId: lastProcessedTs[0].sensor.id,
                            timestamp: lastProcessedTs[0].end
                        };
                        for (let sensorObject of results.aggregations.sensorIds.buckets) {
                            let sensorId = sensorObject.key;
                            let sensorLastProcessedTs = Elasticsearch.searchResultFormatter(sensorObject.lastProcessedTimestamp);
                            sensorLastProcessedTs = sensorLastProcessedTs[0].end;
                            lastProcessedTsDetails.sensorTimestampMap.set(sensorId, sensorLastProcessedTs);
                        }
                    }
                }
                return lastProcessedTsDetails;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #getLastProcessedTsOfNonLeafPlaceFromEs(elasticDb,{place}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("behavior")}`;
        let placeHierarchyLevel = place.split("/").length;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ prefix: { "place.name.keyword": place } });
        queryBody.aggs = {
            placeSuccessor: {
                terms: {
                    script: {
                        lang: "painless",
                        source: `String place_prefix = ''; 
                                int i=0; 
                                for (item in doc['place.name.keyword'].value.splitOnToken('/')) { 
                                    i+=1;
                                    if(i!=1){
                                        place_prefix +='/';
                                    }
                                    place_prefix += item; 
                                    if (i==${placeHierarchyLevel + 1}){
                                        break;
                                    }
                                } 
                                return place_prefix;`
                    },
                    size: 10000
                },
                aggs: {
                    lastProcessedTimestamp: {
                        top_hits: {
                            size: 1,
                            sort: [
                                {
                                    end: {
                                        order: "desc"
                                    }
                                }
                            ],
                            _source: {
                                includes: ["end"]
                            }
                        }
                    }
                }
            }
        };
        let queryObject = {
            index,
            body: queryBody,
            size: 1,
            sort: "end:desc",
            _source_includes: ["place.name", "end"]
        };
        let results = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return results;
    }

    async #getLastProcessedTsOfNonLeafPlace(documentDb,input){
        let lastProcessedTsDetails = { latestTimestamp: null, placeTimestampMap: new Map() };
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let results = await this.#getLastProcessedTsOfNonLeafPlaceFromEs(documentDb,input);
                if (!results.indexAbsent) {
                    results = results.body;
                    let lastProcessedTs = Elasticsearch.searchResultFormatter(results);
                    if (lastProcessedTs.length > 0) {
                        let placeHierarchyLevel = input.place.split("/").length;
                        lastProcessedTsDetails.latestTimestamp = {
                            place: lastProcessedTs[0].place.name.split("/").slice(0, placeHierarchyLevel + 1).join("/"),
                            timestamp: lastProcessedTs[0].end
                        };
                        for (let placeObject of results.aggregations.placeSuccessor.buckets) {
                            let place = placeObject.key;
                            let placeLastProcessedTs = Elasticsearch.searchResultFormatter(placeObject.lastProcessedTimestamp);
                            placeLastProcessedTs = placeLastProcessedTs[0].end;
                            lastProcessedTsDetails.placeTimestampMap.set(place, placeLastProcessedTs);
                        }
                    }
                }
                return lastProcessedTsDetails;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    /** 
     * Retrieves an object containing last processed timestamp.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} [input.sensorId] - Either sensorId or place should be present.
     * @param {string} [input.place] - Either sensorId or place should be present.
     * @returns {Promise<Object>} Last Processed Timestamp is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {sensorId: "abc"};
     * let lastProcessedTimestampObject = new mdx.Metrics.LastProcessedTimestamp();
     * let result = await lastProcessedTimestampObject.getLastProcessedTimestamp(elastic,input);
     */
    async getLastProcessedTimestamp(documentDb,input){
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
                place:{
                    type: "string",
                    minLength: 1,
                    errorMessage: {
                        minLength: "place should have atleast 1 character."
                    }
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
            errorMessage:{
                oneOf:"Input should have either 'sensorId' or 'place'."
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        if("sensorId" in input){
            let result = {latestTimestamp: null};
            let timestamp = await this.#getLastProcessedTsOfSensor(documentDb,input);
            if(timestamp!=null){
                result.latestTimestamp = { sensorId: input.sensorId, timestamp };
            }
            return result;
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
            let cachedSensorPlaceMap = cache.get("sensorPlaceMap");
            let cachedPlaceHierarchyMap = cache.get("placeHierarchyMap");
            if(!cachedPlaceHierarchyMap.has(input.place)){
                return {latestTimestamp: null};
            }
            let placeDetails = cachedPlaceHierarchyMap.get(input.place);
            let placeMetadata = {
                leafPlace: (placeDetails.places == null),
                sensor: (placeDetails.sensors != null)
            };
            let result = { latestTimestamp: null, details: null };
            if (!placeMetadata.leafPlace && placeMetadata.sensor) {
                let [placeWithSensorResult, resultOfNonLeafPlace] = await Promise.all([
                    this.#getLastProcessedTsOfPlaceWithSensor(documentDb,input),
                    this.#getLastProcessedTsOfNonLeafPlace(documentDb,input)]);
                if (placeWithSensorResult.latestTimestamp != null) {
                    result = { latestTimestamp: placeWithSensorResult.latestTimestamp, details: new Array() };
                    result.latestTimestamp.place = cachedSensorPlaceMap.get(result.latestTimestamp.sensorId);
                    for (let [sensorId, timestamp] of placeWithSensorResult.sensorTimestampMap) {
                        result.details.push({
                            place: cachedSensorPlaceMap.get(sensorId),
                            sensorId,
                            timestamp
                        });
                    }
                }
                if (resultOfNonLeafPlace.latestTimestamp != null) {
                    if (result.latestTimestamp == null) {
                        result = { latestTimestamp: resultOfNonLeafPlace.latestTimestamp, details: new Array() };
                    } else {
                        if (Utils.tsCompare(resultOfNonLeafPlace.latestTimestamp.timestamp, ">", result.latestTimestamp.timestamp)) {
                            result.latestTimestamp = resultOfNonLeafPlace.latestTimestamp;
                        }
                    }
                    for (let [place, timestamp] of resultOfNonLeafPlace.placeTimestampMap) {
                        result.details.push({
                            place,
                            timestamp
                        });
                    }
                }
                return result;
            } else if (!placeMetadata.leafPlace) {
                let queryResult = await this.#getLastProcessedTsOfNonLeafPlace(documentDb,input);
                if (queryResult.latestTimestamp != null) {
                    result = { latestTimestamp: queryResult.latestTimestamp, details: new Array() };
                    for (let [place, timestamp] of queryResult.placeTimestampMap) {
                        result.details.push({
                            place,
                            timestamp
                        });
                    }
                }
                return result;
            } else if (placeMetadata.leafPlace) {
                let queryResult = await this.#getLastProcessedTsOfPlaceWithSensor(documentDb,input);
                if (queryResult.latestTimestamp != null) {
                    result = { latestTimestamp: queryResult.latestTimestamp, details: new Array() };
                    result.latestTimestamp.place = cachedSensorPlaceMap.get(result.latestTimestamp.sensorId);
                    for (let [sensorId, timestamp] of queryResult.sensorTimestampMap) {
                        result.details.push({
                            place: cachedSensorPlaceMap.get(sensorId),
                            sensorId,
                            timestamp
                        });
                    }
                }
                return result;
            }
        }
    }
}

module.exports = LastProcessedTimestamp;
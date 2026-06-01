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

const BadRequestError = require('../Errors/BadRequestError');
const deepcopy = require("deepcopy");
const Database = require("../Utils/Database");
const Validator = require("../Utils/Validator");
const roadNetworkSchema = require("../schemas/ajv/roadNetwork.json");
const InternalServerError = require('../Errors/InternalServerError');
const ResourceNotFoundError = require('../Errors/ResourceNotFoundError');
const Elasticsearch = require("../Utils/Elasticsearch");
const Utils = require('../Utils/Utils');

/** 
 * Class which defines Road Network
 * @memberof mdxWebApiCore.Services
 * */

class RoadNetwork {
    
    #formatGeoLocation(roadNetwork) {
        for (let intersection of roadNetwork.intersections) {
            let formattedSegments = new Array();
            for (let segment of intersection.segments) {
                if (segment.start.hasOwnProperty("lon")) {
                    segment.start.lng = deepcopy(segment.start.lon);
                    delete segment.start.lon;
                }
                if (segment.end.hasOwnProperty("lon")) {
                    segment.end.lng = deepcopy(segment.end.lon);
                    delete segment.end.lon;
                }
                let formattedPoints = new Array();
                for (let point of segment.points) {
                    if (point.hasOwnProperty("lon")) {
                        point.lng = deepcopy(point.lon);
                        delete point.lon;
                    }
                    formattedPoints.push(point);
                }
                segment.points = formattedPoints;
                formattedSegments.push(segment);
            }
            intersection.segments = formattedSegments;
        }
        return roadNetwork;
    }

    async #initRoadNetworkEsIndex(elasticDb, index){
        const esClient = elasticDb.getClient();
        const mappings = {
            properties: {
                "intersections.segments.start.lat": { "type": "float" },
                "intersections.segments.start.lng": { "type": "float" },
                "intersections.segments.end.lat": { "type": "float" },
                "intersections.segments.end.lng": { "type": "float" },
                "intersections.segments.points.lat": { "type": "float" },
                "intersections.segments.points.lng": { "type": "float" },
                "intersections.segments.points.alt": { "type": "float" }
            }
        };
        let indexExist = await esClient.indices.existsIndexTemplate({name:`${index}-template`});
        if (!indexExist) {
            await esClient.indices.putIndexTemplate({
                name: `${index}-template`,
                body: {
                    index_patterns: [index],
                    priority: 553,
                    template: {
                        mappings
                    }
                }
            });
        }
        return {success: true};
    }

    async #insertRoadNetworkEs(elasticDb, roadNetwork){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("roadNetwork")}`;
        await this.#initRoadNetworkEsIndex(elasticDb, index);
        const esClient = elasticDb.getClient();
        const ingestPipelineId = "insertion-timestamp-pipeline";
        
        await Elasticsearch.checkIngestPipelineExists(esClient, ingestPipelineId);
        
        const targetTimestampField = "timestamp";
        
        let queryObject = {
            index,
            pipeline: ingestPipelineId,
            id: "roadNetwork",
            body: {
                roadNetwork,
                targetFieldName: targetTimestampField
            }
        };
        let result = await esClient.index(queryObject);
        await esClient.indices.refresh({index});
        return result;
    }

    /**
     * Validates and uploads a road-network configuration document.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} [options={}] - Uploaded file metadata and field information.
     * @param {Object|null} [options.fileDetails=null] - Uploaded files grouped by field name.
     * @param {string|null} [options.fieldName=null] - Form field name containing the road-network file.
     * @returns {Promise<Object>} Success status after persisting the road-network document.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let roadNetworkObject = new mdx.Services.RoadNetwork();
     * let result = await roadNetworkObject.upload(elastic,{fileDetails:req.files, fieldName:"configFiles"});
     */
    async upload(documentDb, {fileDetails=null, fieldName=null}={}){
        if(fieldName == null){
            throw (new BadRequestError("fieldName is required to access the uploaded files."));
        }
        if (fileDetails ==null || !(fieldName in fileDetails) || fileDetails[fieldName].length == 0){
            let errorMessage = "No file has been uploaded. Please upload the roadNetwork file."
            throw(new BadRequestError(errorMessage));
        }
        let roadNetwork = require(fileDetails[fieldName][0].path);
        await Utils.deleteFiles([fileDetails[fieldName][0].path]);
        let validationResult = Validator.validateJsonSchema(roadNetwork,roadNetworkSchema,false);
        if (!validationResult.valid) {
            throw(new BadRequestError("Uploaded file doesn't follow roadNetwork schema."));
        }
        let formattedRoadNetwork = this.#formatGeoLocation(roadNetwork);
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                await this.#insertRoadNetworkEs(documentDb, formattedRoadNetwork);
                return {success:true};
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #getRoadNetworkFromEs(elasticDb){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("roadNetwork")}`;
        let queryObject = { 
            index, 
            body: { 
                query: {
                    ids: {
                        values: ["roadNetwork"]
                    }
                }
            }, 
            size: 1 
        };
        let result = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return result;
    }

    /**
     * Retrieves the stored road-network configuration document.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {boolean} [configMissingErr=true] - Whether to throw when the config is absent.
     * @returns {Promise<Object>} Road-network payload with its timestamp, or an empty default when allowed.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let roadNetworkObject = new mdx.Services.RoadNetwork();
     * let result = await roadNetworkObject.getRoadNetwork(elastic);
     */
    async getRoadNetwork(documentDb, configMissingErr = true){
        let roadNetworkResult = {roadNetwork:{}, timestamp: null};
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let result = await this.#getRoadNetworkFromEs(documentDb);
                if(result.indexAbsent){
                    if(configMissingErr){
                        throw(new ResourceNotFoundError("Resource: roadNetwork not found."));
                    }
                    return roadNetworkResult;
                }else{
                    result = Elasticsearch.searchResultFormatter(result.body);
                    if(result.length == 0 && configMissingErr){
                        throw(new ResourceNotFoundError("Resource: roadNetwork not found."));
                    }else if(result.length != 0){
                        roadNetworkResult = result[0];
                    }
                    return roadNetworkResult;
                }
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    /**
     * Builds a lookup map of intersections keyed by their fully qualified place path.
     * @public
     * @param {Object} roadNetwork - Road network document containing city and intersections.
     * @returns {Map<string, Object>} Intersection metadata keyed by `city=.../intersection=...`.
     */
    getIntersectionInfoMap(roadNetwork) {
        let intersectionInfoMap = new Map();
        for (let intersection of roadNetwork.intersections) {
            intersectionInfoMap.set(
                `city=${roadNetwork.city}/intersection=${intersection.name}`,
                intersection
            );
        }
        return intersectionInfoMap;
    }

    /**
     * Builds a mutable segment map from a list of intersections.
     * @public
     * @param {Array<Object>} intersectionList - Intersections whose segments should be indexed.
     * @returns {Map<string, Object>} Segment metadata keyed by segment ID.
     */
    getSegmentMap(intersectionList) {
        let segmentMap = new Map();
        for (let intersection of intersectionList) {
            for (let segment of intersection.segments) {
                segmentMap.set(segment.id, {
                    direction: segment.direction,
                    start: deepcopy(segment.start),
                    end: deepcopy(segment.end),
                    points: deepcopy(segment.points),
                    speed: 0,
                    objectCount: 0
                });
            }
        }
        return segmentMap;
    }

    /**
     * Reduces a segment map to only the speed and object-count fields needed for responses.
     * @public
     * @param {Map<string, Object>} segmentMap - Segment metadata keyed by segment ID.
     * @returns {Map<string, Object>} Minimal segment statistics keyed by segment ID.
     */
    getMinimalSegmentMap(segmentMap) {
        let minimalSegmentMap = new Map();
        for (let [segmentId, segmentDetails] of segmentMap) {
            minimalSegmentMap.set(segmentId, {
                speed: segmentDetails.speed,
                objectCount: segmentDetails.objectCount
            });
        }
        return minimalSegmentMap;
    }

}

module.exports = RoadNetwork;

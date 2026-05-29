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

/** 
 * Class which defines Sensor
 * @memberof mdxWebApiCore.Services
 * */

class Sensor {

    async #lookupSensorsFromEs(elasticDb, {place, x, y ,z}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("sensorLookup")}`;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({term :{"id.keyword": `${x}|${y}|${z}`}});
        queryBody.query.bool.must.push({term:{"place.keyword": place}});
        let queryObject = { 
            index, 
            body: queryBody,
            size: 1
        }
        let searchResults = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return searchResults;
    }

    /**
     * Finds sensor IDs associated with a rounded world-coordinate lookup within a place.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} input.place - Place used to filter the sensor lookup.
     * @param {number} input.x - X coordinate used for lookup.
     * @param {number} input.y - Y coordinate used for lookup.
     * @param {number} [input.z=0] - Z coordinate used for lookup.
     * @returns {Promise<Object>} Matching sensor IDs wrapped in a sensorIds array are returned.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {place: "building=abc/room=xyz", x: 10.4, y: 20.6, z: 0};
     * let sensorObject = new mdx.Services.Sensor();
     * let sensorLookup = await sensorObject.lookup(elastic,input);
     */
    async lookup(documentDb, input){
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
                x: {
                    type: "number",
                    errorMessage: {
                        type: "'x' should be of type number."
                    }
                },
                y:{
                    type: "number",
                    errorMessage: {
                        type: "'y' should be of type number."
                    }
                },
                z: {
                    type: "number",
                    default: 0,
                    errorMessage: {
                        type: "'z' should be of type number."
                    }
                }
            },
            required: ["place", "x", "y"],
            errorMessage: {
                required: "Input should have required properties 'place', 'x' and 'y'."
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        if (!Number.isFinite(input.x)) {
            throw (new InvalidInputError("'x' is not a finite number."));
        }
        if (!Number.isFinite(input.y)) {
            throw (new InvalidInputError("'y' is not a finite number."));
        }
        if (!Number.isFinite(input.z)) {
            throw (new InvalidInputError("'z' is not a finite number."));
        }
        input.x = Math.round(input.x);
        input.y = Math.round(input.y);
        input.z = Math.round(input.z);
        let sensorIds= new Array();
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let lookupResult = await this.#lookupSensorsFromEs(documentDb, input);
                if (!lookupResult.indexAbsent) {
                    let formattedResult = Elasticsearch.searchResultFormatter(lookupResult.body);
                    if(formattedResult.length>0){
                        sensorIds = formattedResult[0].sensorIds;
                    }
                }
                return {sensorIds};
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }
}

module.exports = Sensor;

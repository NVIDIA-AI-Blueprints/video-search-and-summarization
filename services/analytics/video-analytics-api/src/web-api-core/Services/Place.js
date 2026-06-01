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
const InvalidInputError = require('../Errors/InvalidInputError');
const Validator = require("../Utils/Validator");

/** 
 * Class which defines Place
 * @memberof mdxWebApiCore.Services
 * */

class Place {
    
    /**
     * Returns Elasticsearch query body with a sensor ID aggregation.
     * @public
     * @static
     * @param {Object} input - Input object.
     * @param {string} input.place - Place used to filter the sensor ID aggregation.
     * @param {string} input.sensorIdField - Sensor ID field used in the aggregation. sensorIdField should be one of 'sensor.id' or 'sensorId'.
     * @returns {Object} Elasticsearch query body with a sensor ID aggregation is returned.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * let input = {place: "building=abc/room=xyz", sensorIdField: "sensor.id"};
     * let queryBody = mdx.Services.Place.buildEsSensorIdAggQueryForLeafPlace(input);
     */
    static buildEsSensorIdAggQueryForLeafPlace(input){
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                place: {
                    type: ["string"],
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "place should have atleast 1 character.",
                        maxLength: "place should have atmost 10000 characters."
                    }
                },
                sensorIdField: {
                    type: ["string"],
                    enum: [
                        "sensor.id", 
                        "sensorId"
                    ],
                    errorMessage: {
                        enum: "sensorIdField must be one of the following values: 'sensor.id', 'sensorId'."
                    }
                }
            },
            required: [ "place", "sensorIdField" ],
            errorMessage:{
                required: "Input should have required properties 'place' and 'sensorIdField'.",
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new InvalidInputError(validationResult.reason));
        }
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ term: { "place.name.keyword": input.place } });
        queryBody.aggs={
            sensorIds: {
                terms: {
                    field: `${input.sensorIdField}.keyword`,
                    size: 10000
                }
            }
        }
        return queryBody;
    }

    /**
     * Builds an Elasticsearch aggregation query that returns immediate child places for a non-leaf place.
     * @public
     * @static
     * @param {Object} input - Input object.
     * @param {string} input.place - Non-leaf place used to filter documents.
     * @returns {Object} Elasticsearch query body with a place-successor aggregation is returned.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * let input = {place: "building=abc"};
     * let queryBody = mdx.Services.Place.buildEsPlaceSuccessorAggQueryForNonLeafPlace(input);
     */
    static buildEsPlaceSuccessorAggQueryForNonLeafPlace(input){
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                place: {
                    type: ["string"],
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "place should have atleast 1 character.",
                        maxLength: "place should have atmost 10000 characters."
                    }
                }
            },
            required: [ "place" ],
            errorMessage:{
                required: "Input should have the required property 'place'.",
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new InvalidInputError(validationResult.reason));
        }
        let placeHierarchyLevel = input.place.split("/").length;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ prefix: { "place.name.keyword": input.place } });
        queryBody.aggs={
            placeSuccessor: {
                terms: {
                    script: {
                        lang: "painless",
                        source:`String place_prefix = ''; 
                                int i=0; 
                                for (item in doc['place.name.keyword'].value.splitOnToken('/')) { 
                                    i+=1;
                                    if(i!=1){
                                        place_prefix +='/';
                                    }
                                    place_prefix += item; 
                                    if (i==${placeHierarchyLevel+1})
                                    {
                                        break;
                                    }
                                } 
                                return place_prefix;`
                    },
                    size: 10000
                }
            }
        };
        return queryBody;
    }
}

module.exports = Place;

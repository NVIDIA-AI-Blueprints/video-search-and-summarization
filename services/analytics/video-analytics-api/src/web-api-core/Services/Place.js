/*
 * SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
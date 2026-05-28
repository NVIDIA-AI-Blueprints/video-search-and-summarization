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

const Utils = require("./Utils");
const Validator = require("./Validator");
const InvalidInputError = require('../Errors/InvalidInputError');

/** 
 * Class which defines Histogram
 * @memberof mdxWebApiCore.Utils
 * */

class Histogram {

    static #bucketSizeMultipleInSec = 5;
    static #defaultHistogramBucketCount = 20;
    static #maxHistogramBucketCount = 1000;

    /**
      * returns default histogram bucket count  
      * @public
      * @static
      * @returns {number} - Default histogram bucket count (an integer) is returned
      * @example
      * const mdx = require("@nvidia-mdx/web-api-core");
      * let defaultHistogramBucketCount = mdx.Utils.Histogram.getDefaultHistogramBucketCount();
      */
    static getDefaultHistogramBucketCount(){
        return this.#defaultHistogramBucketCount;
    }

    /**
      * returns max histogram bucket count  
      * @public
      * @static
      * @returns {number} - Max histogram bucket count (an integer) is returned
      * @example
      * const mdx = require("@nvidia-mdx/web-api-core");
      * let maxHistogramBucketCount = mdx.Utils.Histogram.getMaxHistogramBucketCount();
      */
    static getMaxHistogramBucketCount(){
        return this.#maxHistogramBucketCount;
    }

    /**
      * returns bucket size in seconds  
      * @public
      * @static
      * @param {Object} input - Input object.
     * @param {number} [input.bucketCount=20] - Number of histogram buckets returned.
     * @param {string} input.fromTimestamp - fromTimestamp for the histogram in ISO 8601 format.
     * @param {string} input.toTimestamp - toTimestamp for the histogram in ISO 8601 format.
      * @returns {number} - Bucket size in seconds (an integer) is returned
      * @example
      * const mdx = require("@nvidia-mdx/web-api-core");
      * let bucketSizeInSec = mdx.Utils.Histogram.computeBucketSizeInSec({fromTimestamp: "2023-01-12T11:20:10.000Z", toTimestamp: "2023-01-12T14:20:10.000Z"});
      */
    static computeBucketSizeInSec(input){
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                bucketCount:{
                    type: "integer",
                    minimum: 1,
                    default: this.#defaultHistogramBucketCount,
                    maximum: this.#maxHistogramBucketCount,
                    errorMessage: {
                        type: "bucketCount is not an integer.",
                        minimum: "bucketCount can have a minimum value of 1.",
                        maximum: `bucketCount can have a maximum value of ${this.#maxHistogramBucketCount}.`
                    }
                },
                fromTimestamp: {
                    type: "string"
                },
                toTimestamp: {
                    type: "string"
                }
            },
            required: ["fromTimestamp", "toTimestamp"],
            errorMessage:{
                required: "Input should have required properties 'fromTimestamp' and 'toTimestamp'."
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new InvalidInputError(validationResult.reason));
        }
        let timeRangeValidationResult = Validator.isValidTimeRange(input.fromTimestamp, input.toTimestamp);
        if (!timeRangeValidationResult.valid) {
            throw (new InvalidInputError(timeRangeValidationResult.reason));
        }
        if(!Number.isFinite(input.bucketCount)){
            throw (new InvalidInputError("bucketCount is not a finite integer."));
        }
        let timeDifference = new Date(input.toTimestamp) - new Date(input.fromTimestamp);
        let bucketSizeInSec = Math.ceil(timeDifference / (input.bucketCount * 1000));
        let reminder = bucketSizeInSec % this.#bucketSizeMultipleInSec;
        if (reminder != 0) {
            bucketSizeInSec = bucketSizeInSec + this.#bucketSizeMultipleInSec - reminder;
        }
        return bucketSizeInSec;
    }

    /**
      * returns an empty histogram
      * @public
      * @static
      * @param {Object} input - Input object.
     * @param {number} input.bucketSizeInSec - Histogram bucket size in seconds.
     * @param {string} input.fromTimestamp - fromTimestamp for the histogram in ISO 8601 format.
     * @param {string} input.toTimestamp - toTimestamp for the histogram in ISO 8601 format.
      * @returns {Array<start:string,end:string>} - An empty histogram with start and end timestamps are returned
      * @example
      * const mdx = require("@nvidia-mdx/web-api-core");
      * let emptyHistogram = mdx.Utils.Histogram.getEmptyHistogram({ bucketSizeInSec:600, fromTimestamp: "2023-01-12T11:20:10.000Z", toTimestamp: "2023-01-12T14:20:10.000Z"});
      */
    static getEmptyHistogram(input) {
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                bucketSizeInSec:{
                    type: "integer",
                    minimum: 1,
                    errorMessage: {
                        type: "bucketSizeInSec is not an integer.",
                        minimum: "bucketSizeInSec can have a minimum value of 1."
                    }
                },
                fromTimestamp: {
                    type: "string"
                },
                toTimestamp: {
                    type: "string"
                }
            },
            required: ["bucketSizeInSec","fromTimestamp", "toTimestamp"],
            errorMessage:{
                required: "Input should have required properties 'bucketSizeInSec', 'fromTimestamp' and 'toTimestamp'."
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new InvalidInputError(validationResult.reason));
        }
        let timeRangeValidationResult = Validator.isValidTimeRange(input.fromTimestamp, input.toTimestamp);
        if (!timeRangeValidationResult.valid) {
            throw (new InvalidInputError(timeRangeValidationResult.reason));
        }
        let histogram = new Array();
        let start = new Date(Math.floor(new Date(input.fromTimestamp) / (input.bucketSizeInSec * 1000)) * (input.bucketSizeInSec * 1000)).toISOString();
        while (Utils.tsCompare(start, "<", input.toTimestamp)) {
            let end = new Date(new Date(start).valueOf() + (input.bucketSizeInSec * 1000)).toISOString();
            histogram.push({ start, end });
            start = end;
        }
        return histogram;
    }
}

module.exports = Histogram;

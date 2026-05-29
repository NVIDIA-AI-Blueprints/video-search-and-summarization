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
const Histogram = require("../Utils/Histogram");
const Utils = require("../Utils/Utils");
const InternalServerError = require('../Errors/InternalServerError');
const InvalidInputError = require('../Errors/InvalidInputError');
const BadRequestError = require('../Errors/BadRequestError');
const winston = require('winston');
const logger = winston.createLogger({
    format: winston.format.combine(
        winston.format.timestamp(),
        winston.format.printf(({ timestamp, level, message, ...meta }) => {
            return JSON.stringify({ timestamp, level, message, ...meta });
        })
    ),
    transports: [
        new winston.transports.Console()
    ],
    exitOnError: false
});

/** 
 * Class which defines SpaceUtilization
 * @memberof mdxWebApiCore.Metrics
 * */

class SpaceUtilization {

    async #getSpaceUtilizationHistogramFromES(elasticDb, { roiIds, fromTimestamp, toTimestamp, bucketSizeInSec }){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("spaceUtilization")}`;
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ range: { "timestamp": { gte: fromTimestamp, lte: toTimestamp } } });
        if (roiIds != null) {
            let roiIdClauses = new Array();
            for (let roiId of roiIds) {
                roiIdClauses.push({ term: { "id.keyword": roiId } });
            }
            queryBody.query.bool.must.push({ bool: { should: roiIdClauses, minimum_should_match: 1 } });
        }
        queryBody.aggs = {
            eventsOverTime: {
                date_histogram: {
                    field: "timestamp",
                    fixed_interval: `${bucketSizeInSec}s`,
                    min_doc_count: 0,
                    extended_bounds: {
                        min: fromTimestamp,
                        max: toTimestamp
                    }
                },
                aggs: {
                    rois: {
                        terms: {
                            field: "id.keyword",
                            size: 10000
                        },
                        aggs:{
                            avgSpaceOccupied: {
                                avg: {
                                    field: "metrics.spaceOccupied"
                                }
                            },
                            avgFreeSpace: {
                                avg: {
                                    field: "metrics.freeSpace"
                                }
                            },
                            avgTotalSpace: {
                                avg: {
                                    field: "metrics.totalSpace"
                                }
                            },
                            avgSpaceUtilization:{
                                avg: {
                                    field: "metrics.spaceUtilization"
                                }
                            },
                            avgNumExtraPallets:{
                                avg: {
                                    field: "metrics.numExtraPallets"
                                }
                            },
                            avgUtilizableFreeSpace:{
                                avg: {
                                    field: "metrics.utilizableFreeSpace"
                                }
                            },
                            avgFreeSpaceQuality:{
                                avg: {
                                    field: "metrics.freeSpaceQuality"
                                }
                            }
                        }
                    }
                }
            }
        }
        let queryObject = { index, body: queryBody, size: 0 };
        let results = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        console.log(results)
        return results;
    }

    #getMetricsForMissingRoi(){
        return {
            avgUtilizableFreeSpace: 0,
            avgSpaceUtilization: 0,
            avgSpaceOccupied: 0,
            avgTotalSpace: 0,
            avgFreeSpaceQuality: 0,
            avgNumExtraPallets: 0,
            avgFreeSpace: 0
        }
    }

    /** 
     * Retrieves an object containing histogram of space-utilization metrics.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {Array<string>} [input.roiIds] - ROI IDs used to filter space-utilization metrics.
     * @param {string} [input.fromTimestamp] - Either fromTimestamp and toTimestamp should be present together or minutesAgo should be present.
     * @param {string} [input.toTimestamp] - Either fromTimestamp and toTimestamp should be present together or minutesAgo should be present.
     * @param {number} [input.minutesAgo] - Time window in minutes before now. Either fromTimestamp and toTimestamp should be present together or minutesAgo should be present.
     * @param {number} [input.bucketCount=20] - Number of histogram buckets returned.
     * @returns {Promise<Object>} Histogram of space-utilization metrics is returned.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let input = {roiIds: ["roi-1"], fromTimestamp: "2023-01-12T11:20:10.000Z", toTimestamp: "2023-01-12T14:20:10.000Z", bucketCount: 24};
     * let spaceUtilizationObject = new mdx.Metrics.SpaceUtilization();
     * let histogramResult = await spaceUtilizationObject.getHistogramOfSpaceUtilizationMetrics(elastic,input);
     */
    async getHistogramOfSpaceUtilizationMetrics(documentDb, input){
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                roiIds: {
                    type: ["array", "null"],
                    minItems: 1,
                    maxItems: 10000,
                    default: null,
                    items: {
                        type: "string",
                        minLength: 1,
                        maxLength: 10000,
                        errorMessage: {
                            minLength: "Elements of roiIds array should have atleast 1 character.",
                            maxLength: "Elements of roiIds array should have atmost 10000 characters."
                        }
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
                minutesAgo: {
                    type: ["integer","null"],
                    minimum: 1,
                    default: null,
                    errorMessage: {
                        type: "minutesAgo is not an integer.",
                        minimum: "minutesAgo can have a minimum value of 1."
                    }
                },
                bucketCount:{
                    type: "integer",
                    minimum: 1,
                    default: Histogram.getDefaultHistogramBucketCount(),
                    maximum: Histogram.getMaxHistogramBucketCount(),
                    errorMessage: {
                        type: "bucketCount is not an integer.",
                        minimum: "bucketCount can have a minimum value of 1.",
                        maximum: `bucketCount can have a maximum value of ${Histogram.getMaxHistogramBucketCount()}.`
                    }
                }
            },
            oneOf:[
                {
                    required:["minutesAgo"],
                    not: {
                        anyOf: [
                            { required: ["fromTimestamp", "minutesAgo"] },
                            { required: ["toTimestamp", "minutesAgo"] }
                        ]
                    }
                },
                {
                    required:["fromTimestamp", "toTimestamp"],
                    not: {
                        required: ["minutesAgo"]
                    }
                }
            ],
            errorMessage:{
                oneOf: "One of the following combinations should be present in input: ('fromTimestamp', 'toTimestamp') or ('minutesAgo')."
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
        }else if(input.minutesAgo!=null){
            if(!Number.isFinite(input.minutesAgo)){
                throw (new InvalidInputError("minutesAgo is not a finite integer."));
            }
            input.toTimestamp = new Date().toISOString();
            input.fromTimestamp = new Date(new Date(input.toTimestamp) - (input.minutesAgo * 60 * 1000)).toISOString();
        }
        if(!Number.isFinite(input.bucketCount)){
            throw (new InvalidInputError("bucketCount is not a finite integer."));
        }
        input.bucketSizeInSec = Histogram.computeBucketSizeInSec({
            bucketCount: input.bucketCount,
            fromTimestamp:input.fromTimestamp,
            toTimestamp:input.toTimestamp
        });

        let spaceUtilizationHistogram = { bucketSizeInSec: input.bucketSizeInSec, rois: new Array() };

        switch(documentDb.getName()){
            case "Elasticsearch": {
                let histogramResult = await this.#getSpaceUtilizationHistogramFromES(documentDb, input);
                let bucketMap = new Map();
                let roiIds = new Set();
                if(!histogramResult.indexAbsent){
                    for (let bucket of histogramResult.body.aggregations.eventsOverTime.buckets) {
                        let start = bucket["key_as_string"];
                        let end = new Date(new Date(start).valueOf() + (input.bucketSizeInSec * 1000)).toISOString();
                        let histogramBucket = { start, end, roiMetricsMap: new Map() };
                        for (let roiBucket of bucket.rois.buckets) {
                            const roiId = roiBucket.key;
                            roiIds.add(roiId);
                            histogramBucket.roiMetricsMap.set(roiId,{
                                avgUtilizableFreeSpace: Number(roiBucket.avgUtilizableFreeSpace.value.toFixed(2)),
                                avgSpaceUtilization: Number(roiBucket.avgSpaceUtilization.value.toFixed(2)),
                                avgSpaceOccupied: Number(roiBucket.avgSpaceOccupied.value.toFixed(2)),
                                avgTotalSpace: Number(roiBucket.avgTotalSpace.value.toFixed(2)),
                                avgFreeSpaceQuality: Number(roiBucket.avgFreeSpaceQuality.value.toFixed(2)),
                                avgNumExtraPallets: Number(roiBucket.avgNumExtraPallets.value.toFixed(2)),
                                avgFreeSpace: Number(roiBucket.avgFreeSpace.value.toFixed(2))
                            });
                        }
                        bucketMap.set(start,histogramBucket);
                    }
                }
                let roiBucketMap = new Map();
                for (let [start, histogramBucket] of bucketMap) {
                    for(let roiId of roiIds){
                        if(!roiBucketMap.has(roiId)){
                            roiBucketMap.set(roiId, new Array());
                        }
                        roiBucketMap.get(roiId).push({
                            start,
                            end:histogramBucket.end,
                            metrics: histogramBucket.roiMetricsMap.has(roiId)?(histogramBucket.roiMetricsMap.get(roiId)):this.#getMetricsForMissingRoi()
                        });
                    }
                }
                for(let [roiId, histogram] of roiBucketMap){
                    if(Utils.tsCompare(histogram[0].start,"<",input.fromTimestamp)){
                        histogram[0].start = input.fromTimestamp;
                    }
                    if(Utils.tsCompare(histogram[histogram.length-1].end,">",input.toTimestamp)){
                        histogram[histogram.length-1].end = input.toTimestamp;
                    }
                    spaceUtilizationHistogram.rois.push({ id: roiId, histogram })
                }
                return spaceUtilizationHistogram;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }

    }

}

module.exports = SpaceUtilization;

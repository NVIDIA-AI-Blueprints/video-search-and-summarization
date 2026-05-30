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
const moment = require("moment");
const fs = require('fs');
const fsPromises = fs.promises;
const InternalServerError = require('../Errors/InternalServerError');
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
 * Class containing utils.
 * @memberof mdxWebApiCore.Utils
 * */

class Utils {
    /**
     * Extracts place hierarchy from input place
     * @public
     * @static
     * @param {string} place - Place string to split into levels.
     * @returns {string} 
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * let result = mdx.Utils.Utils.getPlaceHierarchy(city=abc/intersection=xyz);
     * // returns city/intersection
     */
    static getPlaceHierarchy(place) {
        let placeSplitList = place.split("/");
        let placeHierarchy = new Array();
        for (let placeAttributeString of placeSplitList) {
            let placeAttributeSplit = placeAttributeString.split("=");
            placeHierarchy.push(placeAttributeSplit[0]);
        }
        return placeHierarchy.join("/");
    }

    /**
     * Compares two timestamps.
     * @public
     * @static
     * @param {string} timestamp1 - First timestamp in ISO 8601 format.
     * @param {('>'|'>='|'<'|'<='|'==')} comparison - Comparison operator used between timestamps.
     * @param {string} timestamp2 - Second timestamp in ISO 8601 format.
     * @returns {boolean} Returns whether the comparison is true or false
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * let fromTimestamp = "2023-01-12T11:20:10.000Z";
     * let toTimestamp = "2023-01-12T14:20:10.000Z";
     * let result = mdx.Utils.Utils.tsCompare(fromTimestamp,"<",toTimestamp);
     */
    static tsCompare(timestamp1, comparison, timestamp2) {
        switch (comparison) {
            case ">":
                return moment.utc(timestamp1).isAfter(moment.utc(timestamp2));
            case ">=":
                return moment.utc(timestamp1).isSameOrAfter(moment.utc(timestamp2));
            case "<":
                return moment.utc(timestamp1).isBefore(moment.utc(timestamp2));
            case "<=":
                return moment.utc(timestamp1).isSameOrBefore(moment.utc(timestamp2));
            case "==":
                return moment.utc(timestamp1).isSame(moment.utc(timestamp2));
            default:
                throw (new InternalServerError("Invalid comparison operator. Valid comparison operators include: '>', '>=', '<', '<=', '=='"));
        }
    }

    /**
     * Calculates the time interval between two timestamps in seconds with millisecond precision.
     * @public
     * @static
     * @param {string} fromTimestamp - Start timestamp in ISO format
     * @param {string} toTimestamp - End timestamp in ISO format
     * @returns {number} Time interval in seconds rounded to 3 decimal places (millisecond precision)
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const interval = mdx.Utils.Utils.getTimeInterval("2023-01-01T10:00:00.000Z", "2023-01-01T10:00:01.234Z");
     * // Returns: 1.234
     */
    static getTimeInterval(fromTimestamp, toTimestamp) {
        const endTime = new Date(toTimestamp).getTime();
        const startTime = new Date(fromTimestamp).getTime();
        const timeInterval = (endTime - startTime) / 1000;
        return Math.round(timeInterval * 1000) / 1000;
    }

    /**
     * Calculates set difference.
     * @public
     * @static
     * @param {Set} set1 - First set.
     * @param {Set} set2 - Second set.
     * @returns {Set} Returns a set which is a set difference of the input sets
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * let result = mdx.Utils.Utils.setDifference(new Set([1,2,3]),new Set([2,3,4]));
     */
    static setDifference(set1, set2) {
        let difference = new Set([...set1].filter(x => !set2.has(x)));
        return difference;
    }

    /**
     * Calculates set intersection.
     * @public
     * @static
     * @param {Set} set1 - First set.
     * @param {Set} set2 - Second set.
     * @returns {Set} Returns a set which is a set intersection of the input sets
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * let result = mdx.Utils.Utils.setIntersection(new Set([1,2,3]),new Set([2,3,4]));
     */
    static setIntersection(set1, set2) {
        let intersection = new Set([...set1].filter(x => set2.has(x)));
        return intersection;
    }

    /**
     * Asynchronously waits for a specified amount of time.
     * @public
     * @static
     * @async
     * @param {number} ms - Number of milliseconds to wait.
     * @returns {Promise<void>} A promise that resolves after the specified time has elapsed.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * await mdx.Utils.Utils.sleep(2000);
     */
    static async sleep(ms) {
        await new Promise(resolve => setTimeout(resolve, ms));
    }

    /**
     * Used to delete files
     * @public
     * @static
     * @async
     * @param {Array<string>} filePaths - File paths to delete.
     * @returns {Promise<Object>} A success message is returned once files are deleted
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * let filePaths = ["/path/to/file1","/path/to/file2"];
     * let result = await mdx.Utils.Utils.deleteFiles(filePaths);
     */
    static async deleteFiles(filePaths){
        let delFilesPromiseList = new Array();
        for (let filePath of filePaths) {
            if (fs.existsSync(filePath)) {
                delFilesPromiseList.push(fsPromises.unlink(filePath));
            }else{
                logger.warn(`[FILE] Cannot delete the file: '${filePath}' as it doesn't exist.`);
            }
        }
        await Promise.all(delFilesPromiseList);
        return ({ success: true });
    }

    /**
     * Awaits the input only when it is a promise and otherwise returns the value unchanged.
     * @public
     * @static
     * @async
     * @template T
     * @param {T|Promise<T>} value - Synchronous value or promise-like value.
     * @returns {Promise<T>} Resolved value.
     */
    static async conditionalAsync(value){
        if(value instanceof Promise){
            return await value;
        }
        return value;
    }

    /**
     * Wraps a function to catch any errors thrown and handle them using a provided function.
     * @public
     * @static
     * @param {Function} fn - The function to wrap.
     * @returns {Function} A new function that calls the original function and catches any errors thrown.
     * @example
     * const express = require('express');
     * let router = express.Router();
     * router.route("/new-endpoint").get(mdx.Utils.Utils.expressAsyncWrapper(async(req,res,next)=>{}));
     */
    static expressAsyncWrapper = (fn) =>(...args) => fn(...args).catch(args[2]);
}

module.exports = Utils;

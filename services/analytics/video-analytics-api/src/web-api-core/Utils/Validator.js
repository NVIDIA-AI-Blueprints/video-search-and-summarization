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
const Utils = require("./Utils");
const Ajv2019 = require("ajv/dist/2019");
const ajvErrors = require("ajv-errors");
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
 * Class containing different validators.
 * @memberof mdxWebApiCore.Utils
 * */

class Validator {

    /**
     * Used to check if input is a finite number.
     * @public
     * @static
     * @param {string} input - Input string to validate.
     * @returns {boolean} Returns a boolean signifying whether the input was a finite number
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * // returns true
     * let result = mdx.Utils.Validator.isStringAFiniteNumber("12.5");
     */
    static isStringAFiniteNumber(input) {
        if (isFinite(input) && typeof input === "string") {
            input = input.trim();
            if (input !== "") {
                return true;
            }
        }
        return false;
    }

    /**
     * Used to check if input is a finite integer.
     * @public
     * @static
     * @param {string} input - Input string to validate.
     * @returns {boolean} Returns a boolean signifying whether the input was a finite integer
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * // returns false
     * let result = mdx.Utils.Validator.isStringAFiniteInteger("12.5");
     */
    static isStringAFiniteInteger(input) {
        if (this.isStringAFiniteNumber(input) && Number(input) % 1 === 0) {
            return true
        } else {
            return false
        }
    }

    /**
     * Checks if input date string is in ISO 8601 format.
     * @public
     * @static
     * @param {string} timestamp - Timestamp to validate.
     * @returns {boolean} Returns a boolean signifying whether the input was a valid timestamp
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * let result = mdx.Utils.Validator.isValidTimestamp("2023-01-12T11:20:10.000Z");
     */
    static isValidISOTimestamp(timestamp) {
        return moment(timestamp, "YYYY-MM-DDTHH:mm:ss.SSSZ", true).isValid();
    }

    /**
     * Checks if input time range is valid.
     * @public
     * @static
     * @param {string} fromTimestamp - fromTimestamp to validate.
     * @param {string} toTimestamp - toTimestamp to validate.
     * @returns {boolean} Returns a boolean signifying whether the input time range was valid
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * let result = mdx.Utils.Validator.isValidTimeRange("2023-01-12T11:20:10.000Z","2023-01-12T14:20:10.000Z");
     */
    static isValidTimeRange(fromTimestamp, toTimestamp){
        if(!this.isValidISOTimestamp(fromTimestamp)){
            return {valid: false, reason: "Invalid fromTimestamp."};
        }else if (!this.isValidISOTimestamp(toTimestamp)){
            return {valid: false, reason: "Invalid toTimestamp."};
        }else if(!Utils.tsCompare(fromTimestamp, "<", toTimestamp)){
            return {valid: false, reason: "fromTimestamp is not lesser than toTimestamp."};
        }else{
            return {valid: true, reason: null};
        }
    }

    /**
     * Checks if a json input follows a schema.
     * @public
     * @static
     * @param {Object} jsonInput - JSON input object to validate.
     * @param {Object} schema - JSON schema used for validation.
     * @param {boolean} [coerceTypes=true] - Whether to coerce input types during validation.
     * @returns {{valid:boolean,reason:?string}} Returns the validity of the json input.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * let result = mdx.Utils.Validator.validateJsonSchema(jsonInput,schema);
     */
    static validateJsonSchema(jsonInput, schema, coerceTypes=true){
        let ajvOptions = {allErrors: true, useDefaults: true};
        if(coerceTypes){
            ajvOptions.coerceTypes = "array";
        }
        const ajv = new Ajv2019(ajvOptions);
        ajvErrors(ajv);
        const validate = ajv.compile(schema);
        const valid =validate(jsonInput);
        if(valid){
            return {valid, reason:null}
        }else{
            logger.error(`[INPUT ERROR] AJV Error: ${JSON.stringify(validate.errors)}.`);
            let filteredErrors = new Array();
            for(let error of validate.errors){
                if(!(error.schemaPath.includes("oneOf") && error.keyword!=="oneOf")){
                    filteredErrors.push(error);
                }
            }
            let errorMessage = "";
            for(let i=0;i<filteredErrors.length;i++){
                let error = filteredErrors[i];
                if(i!=0){
                    errorMessage+=" ";
                }
                errorMessage += `Error ${i+1}: ${error.message}`;
            }
            errorMessage = errorMessage.replace(/"/g, "'");
            return {valid, reason:`Invalid input. ${errorMessage}`};
        }
    }
}

module.exports = Validator;
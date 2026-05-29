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
const BadRequestError = require("../Errors/BadRequestError");
const InternalServerError = require("../Errors/InternalServerError");
const InvalidInputError = require("../Errors/InvalidInputError");
const ResourceNotFoundError = require("../Errors/ResourceNotFoundError");
const Validator = require("../Utils/Validator");
const NotificationManager= require("./NotificationManager");
const Elasticsearch = require("../Utils/Elasticsearch");
const filterTemplate = require("../queryTemplates/filter.json");
const behaviorAnalyticsConfigSchema = require("../schemas/ajv/behaviorAnalyticsConfig.json");
const behaviorAnalyticsConfigStrictSchema = require("../schemas/ajv/behaviorAnalyticsConfigStrict.json");
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
 * Class which defines ConfigManager
 * @memberof mdxWebApiCore.Services
 * */

class ConfigManager {

    static #validDocTypes = new Set(["behavior-analytics"]);
    static #CONFIG_UPDATE_SUCCESS_STATUSES = new Set(["success", "partial-success"]);
    static #CONFIG_UPDATE_PUBLISH_RETRIES = 5;
    static #VIDEO_ANALYTICS_API_CONFIG_UPDATE_REFERENCE_PREFIX = "video-analytics-api-";

    #isConfigStatusTimeoutCheckRunning = false;

    #parseConfig(config){
        if(typeof config !== "string"){
            return config;
        }
        try{
            return JSON.parse(config);
        }catch(error){
            return config;
        }
    }

    #stringifyConfig(config){
        if(config == null || typeof config === "string"){
            return config;
        }
        return JSON.stringify(config);
    }

    #validateConfigResult({docType, status, config, error}){
        if(!ConfigManager.#validDocTypes.has(docType)){
            throw new BadRequestError(`Invalid docType: ${docType}.`);
        }
        const validStatuses = new Set(["success", "partial-success", "failure"]);
        if(!validStatuses.has(status)){
            throw new BadRequestError(`Invalid status: ${status}.`);
        }
        if(error != null && typeof error !== "string"){
            throw new BadRequestError("Invalid error message.");
        }
        if(status === "failure"){
            if(config !== null){
                throw new BadRequestError("Config must be null when status is failure.");
            }
            return;
        }
        if(config == null || typeof config !== "object" || Array.isArray(config)){
            throw new BadRequestError(`Config is required when status is ${status}.`);
        }
    }

    async #produceConfigNotificationWithRetry(notificationManagerObject, messageBroker, input, retryCount){
        let lastError = null;
        for(let attempt = 0; attempt <= retryCount; attempt++){
            try{
                return await notificationManagerObject.produceConfigNotification(messageBroker, input);
            }catch(error){
                lastError = error;
                if(attempt >= retryCount){
                    break;
                }
                logger.warn(`[Kafka Message] Failed to produce behavior analytics config update on attempt ${attempt + 1} of ${retryCount + 1}: ${error.name} - ${error.message}`);
            }
        }
        throw lastError;
    }

    /**
     * Returns valid docTypes for ConfigManager.
     * @public
     * @static
     * @returns {Set<string>} A set containing valid docTypes is returned.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * let result = mdx.Services.ConfigManager.getValidDocTypes();
     */
    static getValidDocTypes(){
        return this.#validDocTypes;
    }

    async #getConfigFromEs(elasticDb, docType){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("configs")}`;
        const esClient = elasticDb.getClient();
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ ids: { values: [docType] } });
        let queryObject = { 
            index, 
            body: queryBody, 
            size: 1 
        };
        let result = await Elasticsearch.getSearchResults(esClient, queryObject, false);
        let configResult = null;
        if(!result.indexAbsent){
            result = Elasticsearch.searchResultFormatter(result.body);
            if(result.length>0){
                configResult=result[0];
            }
        }
        return configResult;
    }

    /** 
     * returns an object containing config and the timestamp associated with it.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {("behavior-analytics")} docType - Config document type.
     * @returns {Promise<Object>} Config Object along with timestamp is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let docType = "behavior-analytics";
     * let configManagerObject = new mdx.Services.ConfigManager();
     * let result = await configManagerObject.getConfig(elastic,docType);
     */
    async getConfig(documentDb,docType){
        if(!ConfigManager.#validDocTypes.has(docType)){
            throw new BadRequestError(`Invalid docType: ${docType}.`);
        }
        let configResult = null;
        const queryTime = new Date().toISOString();
        switch(documentDb.getName()){
            case "Elasticsearch":{
                configResult = await this.#getConfigFromEs(documentDb,docType);
                if(configResult!=null){
                    return configResult;
                }else{
                    return({
                        config: null,
                        timestamp: queryTime
                    });
                }
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }

    }

    async #insertConfigEs(elasticDb, {docType, config, timestamp, status=null, referenceId=null, error=null, resultConfig=null}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("configs")}`;
        const esClient = elasticDb.getClient();
        
        let queryObject = {
            index,
            id: docType,
            body: {
                docType,
                config,
                status,
                referenceId,
                error,
                timestamp
            }
        };
        let result = await esClient.index(queryObject);
        await esClient.indices.refresh({index});
        return result;
    }

    async #auditHasSuccessfulConfigUpdateEs(elasticDb, docType){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("configsAudit")}`;
        const esClient = elasticDb.getClient();
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ term: { "docType.keyword": docType } });
        queryBody.query.bool.must.push({ terms: { "status.keyword": ["success", "partial-success"] } });
        let queryObject = {
            index,
            _source: false,
            track_total_hits: false,
            body: queryBody,
            size: 1
        };
        let result = await Elasticsearch.getSearchResults(esClient, queryObject, false);
        if(result.indexAbsent){
            return false;
        }
        return result.body.hits.hits.length > 0;
    }

    /**
     * Checks whether a docType has at least one successful config update audit record.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {("behavior-analytics")} docType - Config document type.
     * @returns {Promise<boolean>} True when a successful or partial-success config update exists.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let configManagerObject = new mdx.Services.ConfigManager();
     * let result = await configManagerObject.auditHasSuccessfulConfigUpdate(elastic,"behavior-analytics");
     */
    async auditHasSuccessfulConfigUpdate(documentDb, docType){
        switch(documentDb.getName()){
            case "Elasticsearch":
                return await this.#auditHasSuccessfulConfigUpdateEs(documentDb, docType);
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #getAuditConfigFromEs(elasticDb, input){
        const referenceId = input.referenceId;
        const docType = input.docType;
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("configsAudit")}`;
        const esClient = elasticDb.getClient();
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ ids: { values: [referenceId] } });
        queryBody.query.bool.must.push({ term: { "docType.keyword": docType } });
        let queryObject = {
            index,
            body: queryBody,
            size: 1
        };
        let result = await Elasticsearch.getSearchResults(esClient, queryObject, false);
        if(!result.indexAbsent){
            result = Elasticsearch.searchResultFormatter(result.body);
            if(result.length > 0){
                return result[0];
            }
        }
        return null;
    }

    async #insertConfigAuditEs(elasticDb, {docType, config, timestamp, eventType, referenceId, status=null, error=null}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("configsAudit")}`;
        const esClient = elasticDb.getClient();
        let queryObject = {
            index,
            body: {docType, config, timestamp, eventType, status, error}
        };
        if(referenceId != null){
            queryObject.id = referenceId;
            queryObject.body.referenceId = referenceId;
        }
        let result = await esClient.index(queryObject);
        await esClient.indices.refresh({index});
        return result;
    }

    async #markTimedOutConfigUpdatesEs(elasticDb, timeoutThresholdMs){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("configsAudit")}`;
        const esClient = elasticDb.getClient();
        const timeoutCutoff = new Date(Date.now() - timeoutThresholdMs).toISOString();
        let queryBody = deepcopy(filterTemplate);
        queryBody.query.bool.must.push({ term: { "docType.keyword": "behavior-analytics" } });
        queryBody.query.bool.must.push({ term: { "status.keyword": "pending" } });
        queryBody.query.bool.must.push({ range: { timestamp: { lt: timeoutCutoff } } });
        let queryObject = {
            index,
            body: queryBody,
            sort: "timestamp:asc",
            size: 10000
        };
        let result = await Elasticsearch.getSearchResults(esClient, queryObject, false);
        if(result.indexAbsent){
            return {updated: 0, timeoutCutoff};
        }

        const timedOutConfigUpdates = Elasticsearch.searchResultFormatter(result.body);
        const error = `Config update timed out after ${timeoutThresholdMs} ms.`;
        await Promise.all(timedOutConfigUpdates.map(auditConfigResult => {
            return this.#insertConfigAuditEs(elasticDb, {
                docType: auditConfigResult.docType,
                config: null,
                timestamp: auditConfigResult.timestamp,
                eventType: auditConfigResult.eventType,
                referenceId: auditConfigResult.referenceId,
                status: "failure",
                error
            });
        }));

        return {updated: timedOutConfigUpdates.length, timeoutCutoff};
    }

    /**
     * Returns a config audit record for a reference ID and docType.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {("behavior-analytics")} input.docType - Config document type.
     * @param {string} input.referenceId - Config update reference ID.
     * @returns {Promise<?Object>} Config audit record is returned when found, otherwise null is returned.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let configManagerObject = new mdx.Services.ConfigManager();
     * let result = await configManagerObject.getAuditConfig(elastic,{docType: "behavior-analytics", referenceId: "update-1"});
     */
    async getAuditConfig(documentDb, input){
        switch(documentDb.getName()){
            case "Elasticsearch":
                return await this.#getAuditConfigFromEs(documentDb, input);
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    /**
     * Records a pending config update request in the audit index.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {("behavior-analytics")} input.docType - Config document type.
     * @param {string} input.referenceId - Config update reference ID.
     * @param {Object|string} input.config - Config object or JSON stringified config object.
     * @param {string} input.timestamp - Request timestamp in ISO 8601 format.
     * @param {("upsert")} input.eventType - Config update event type.
     * @param {?string} [input.error=null] - Optional error message associated with the request.
     * @returns {Promise<Object>} Pending config update request details are returned.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let configManagerObject = new mdx.Services.ConfigManager();
     * let result = await configManagerObject.recordConfigUpdateRequest(elastic,{docType: "behavior-analytics", referenceId: "update-1", config, timestamp, eventType: "upsert"});
     */
    async recordConfigUpdateRequest(documentDb, {docType, referenceId, config, timestamp, eventType, error=null}){
        if(!ConfigManager.#validDocTypes.has(docType)){
            throw new BadRequestError(`Invalid docType: ${docType}.`);
        }
        if(typeof referenceId !== "string" || referenceId.trim() === ""){
            throw new BadRequestError("Invalid referenceId.");
        }
        if(eventType !== "upsert"){
            throw new BadRequestError(`Invalid eventType: ${eventType}.`);
        }
        if(!Validator.isValidISOTimestamp(timestamp)){
            throw new InvalidInputError("Invalid timestamp.");
        }
        if(error != null && typeof error !== "string"){
            throw new BadRequestError("Invalid error.");
        }

        const parsedConfig = this.#parseConfig(config);
        if(parsedConfig == null || typeof parsedConfig !== "object" || Array.isArray(parsedConfig)){
            throw new BadRequestError("Config is required for a config update request.");
        }
        if(docType==="behavior-analytics"){
            const behaviorAnalyticsConfigValidationResult = Validator.validateJsonSchema(parsedConfig, behaviorAnalyticsConfigSchema, false);
            if(!behaviorAnalyticsConfigValidationResult.valid){
                throw new BadRequestError("Behavior analytics configuration doesn't follow schema.");
            }
        }

        const existingAuditConfig = await this.getAuditConfig(documentDb, {docType, referenceId});
        let auditError = error;
        if(existingAuditConfig != null){
            const duplicateReferenceIdError = `Duplicate referenceId ${referenceId} received via Kafka config update request. Overwriting audit config with the duplicate request config.`;
            const existingError = typeof existingAuditConfig.error === "string" ? existingAuditConfig.error.trim() : "";
            auditError = existingError === "" ? duplicateReferenceIdError : `${existingError}; ${duplicateReferenceIdError}`;
            logger.warn(`[DATA] ${auditError}`);
        }

        switch(documentDb.getName()){
            case "Elasticsearch":
                await this.#insertConfigAuditEs(documentDb, {
                    docType,
                    config: this.#stringifyConfig(parsedConfig),
                    timestamp,
                    eventType,
                    referenceId,
                    status: "pending",
                    error: auditError
                });
                return {referenceId, status: "pending", error: auditError};
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    /**
     * Returns the status of a config update request and marks it failed if it has timed out.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {("behavior-analytics")} input.docType - Config document type.
     * @param {string} input.referenceId - Config update reference ID.
     * @param {number} timeoutThresholdMs - Time after which a pending update is marked as failure.
     * @returns {Promise<Object>} Config update status, config, error, and timestamp are returned.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let configManagerObject = new mdx.Services.ConfigManager();
     * let result = await configManagerObject.getConfigStatus(elastic,{docType: "behavior-analytics", referenceId: "update-1"},30000);
     */
    async getConfigStatus(documentDb, input, timeoutThresholdMs){
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                docType: {
                    type: "string",
                    enum: Array.from(ConfigManager.#validDocTypes),
                    errorMessage: {
                        enum: `docType must be one of the following values: ${Array.from(ConfigManager.#validDocTypes).join(", ")}.`
                    }
                },
                referenceId: {
                    type: "string",
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "referenceId should have atleast 1 character.",
                        maxLength: "referenceId should have atmost 10000 characters.",
                        pattern: "Invalid referenceId."
                    }
                }
            },
            required: ["docType", "referenceId"],
            errorMessage: {
                required: "Input should have required properties 'docType' and 'referenceId'."
            }
        };
        let validationResult = Validator.validateJsonSchema(input, schema);
        if(!validationResult.valid){
            throw new BadRequestError(validationResult.reason);
        }
        if(!Number.isInteger(timeoutThresholdMs) || timeoutThresholdMs < 0){
            throw new BadRequestError("Invalid timeoutThresholdMs.");
        }

        const referenceId = input.referenceId;
        const docType = input.docType;
        let auditConfigResult = await this.getAuditConfig(documentDb, input);
        if(auditConfigResult == null){
            throw new BadRequestError(`Invalid referenceId ${referenceId} for docType ${docType}.`);
        }

        if(auditConfigResult.status === "pending"){
            const requestTimestampEpoch = new Date(auditConfigResult.timestamp).getTime();
            if(Number.isNaN(requestTimestampEpoch)){
                throw new InternalServerError(`Invalid audit timestamp for referenceId ${referenceId}.`);
            }

            if((Date.now() - requestTimestampEpoch) > timeoutThresholdMs){
                const error = `Config update timed out after ${timeoutThresholdMs} ms.`;
                await this.#insertConfigAuditEs(documentDb, {
                    docType: auditConfigResult.docType,
                    config: null,
                    timestamp: auditConfigResult.timestamp,
                    eventType: auditConfigResult.eventType,
                    referenceId,
                    status: "failure",
                    error
                });
                auditConfigResult = {
                    ...auditConfigResult,
                    referenceId,
                    config: null,
                    status: "failure",
                    error
                };
            }
        }

        return {
            referenceId: auditConfigResult.referenceId,
            docType: auditConfigResult.docType,
            status: auditConfigResult.status,
            config: this.#parseConfig(auditConfigResult.config),
            error: auditConfigResult.error,
            timestamp: auditConfigResult.timestamp
        };
    }

    /**
     * Marks pending behavior analytics config audit records as failure after they time out.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {number} timeoutThresholdMs - Time after which pending updates are marked as failure.
     * @returns {Promise<Object>} Timeout sweep details are returned.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let configManagerObject = new mdx.Services.ConfigManager();
     * await configManagerObject.markTimedOutConfigUpdates(elastic,30000);
     */
    async markTimedOutConfigUpdates(documentDb, timeoutThresholdMs){
        if(!Number.isInteger(timeoutThresholdMs) || timeoutThresholdMs < 0){
            throw new BadRequestError("Invalid timeoutThresholdMs.");
        }
        switch(documentDb.getName()){
            case "Elasticsearch":
                return await this.#markTimedOutConfigUpdatesEs(documentDb, timeoutThresholdMs);
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    /**
     * Runs a guarded timeout check for pending config update requests.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {number} timeoutThresholdMs - Time after which pending updates are marked as failure.
     * @returns {Promise<Object>} Timeout sweep details are returned, including whether the run was skipped.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let configManagerObject = new mdx.Services.ConfigManager();
     * let result = await configManagerObject.runConfigStatusTimeoutCheck(elastic,30000);
     */
    async runConfigStatusTimeoutCheck(documentDb, timeoutThresholdMs){
        if(this.#isConfigStatusTimeoutCheckRunning){
            return {skipped: true};
        }
        this.#isConfigStatusTimeoutCheckRunning = true;
        try{
            const result = await this.markTimedOutConfigUpdates(documentDb, timeoutThresholdMs);
            if(result.updated > 0){
                logger.info(`[CONFIG STATUS] Marked ${result.updated} timed-out behavior analytics config update(s) as failure.`);
            }
            return {...result, skipped: false};
        }catch(error){
            logger.error(`[CONFIG STATUS ERROR] Failed to mark timed-out config updates: ${error.name} - ${error.message}`);
            console.error(error);
            return {updated: 0, skipped: false, error};
        }finally{
            this.#isConfigStatusTimeoutCheckRunning = false;
        }
    }

     /** 
     * Inserts the initial config into the database.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {("behavior-analytics")} docType - Config document type.
     * @param {string} config - A json stringified config object
     * @returns {Promise<void>}
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let docType = "behavior-analytics";
     * let configManagerObject = new mdx.Services.ConfigManager();
     * await configManagerObject.initConfig(elastic,docType,config);
     */
    async initConfig(documentDb,docType,config){
        if(!ConfigManager.#validDocTypes.has(docType)){
            throw new BadRequestError(`Invalid docType: ${docType}.`);
        }
        if(docType==="behavior-analytics"){
            let behaviorAnalyticsConfigValidationResult = Validator.validateJsonSchema(JSON.parse(config),behaviorAnalyticsConfigStrictSchema,false);
            if (!behaviorAnalyticsConfigValidationResult.valid) {
                throw(new BadRequestError("Behavior analytics configuration doesn't follow schema."));
            }
        }
        switch(documentDb.getName()){
            case "Elasticsearch":{
                await this.#insertConfigEs(documentDb,{docType,config});
                let configResult = await this.#getConfigFromEs(documentDb,docType);
                if(configResult!=null){
                    let {timestamp}=configResult;
                    let updateDetails = {
                        docType,
                        config,
                        timestamp,
                        eventType: "upsert-all"
                    };
                    await this.#insertConfigAuditEs(documentDb, updateDetails);
                    logger.info(`[DATA] Config init of docType: ${docType} was successful.`);
                    break;
                }else{
                    throw (new InternalServerError(`Insertion of ${docType} config has failed. Couldn't find config before audit index insertion.`));
                }
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    /**
     * Records the result of a pending config update and persists successful configs.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {("behavior-analytics")} input.docType - Config document type.
     * @param {string} input.referenceId - Config update reference ID.
     * @param {("success"|"partial-success"|"failure")} input.status - Config update result status.
     * @param {?Object} input.config - Result config object. config should be null when status is failure.
     * @param {?string} input.error - Optional error message for the result.
     * @returns {Promise<void>}
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let configManagerObject = new mdx.Services.ConfigManager();
     * await configManagerObject.updateConfigResult(elastic,{docType: "behavior-analytics", referenceId: "update-1", status: "success", config, error: null});
     */
    async updateConfigResult(documentDb, {docType, referenceId, status, config, error}){
        if(typeof referenceId !== "string" || referenceId.trim() === ""){
            throw new BadRequestError("Invalid referenceId.");
        }
        this.#validateConfigResult({docType, status, config, error});
        if(docType === "behavior-analytics" && ConfigManager.#CONFIG_UPDATE_SUCCESS_STATUSES.has(status)){
            let behaviorAnalyticsConfigValidationResult = null;
            if(referenceId.startsWith(ConfigManager.#VIDEO_ANALYTICS_API_CONFIG_UPDATE_REFERENCE_PREFIX)){
                behaviorAnalyticsConfigValidationResult = Validator.validateJsonSchema(config, behaviorAnalyticsConfigStrictSchema, false);
            }else if(referenceId.startsWith("kafka")){
                behaviorAnalyticsConfigValidationResult = Validator.validateJsonSchema(config, behaviorAnalyticsConfigSchema, false);
            }
            if(behaviorAnalyticsConfigValidationResult != null && !behaviorAnalyticsConfigValidationResult.valid){
                throw new BadRequestError("Behavior analytics configuration doesn't follow schema.");
            }
        }
        const auditConfigResult = await this.getAuditConfig(documentDb, {docType, referenceId});
        if(auditConfigResult == null){
            throw new BadRequestError(`No pending config update found for referenceId ${referenceId}.`);
        }
        if(auditConfigResult.status !== "pending"){
            throw new BadRequestError(`Config update for referenceId ${referenceId} is not pending.`);
        }
        const stringifiedConfig = this.#stringifyConfig(config);

        if(ConfigManager.#CONFIG_UPDATE_SUCCESS_STATUSES.has(status)){
            await this.#insertConfigEs(documentDb,{
                docType,
                config: stringifiedConfig,
                status,
                referenceId,
                error,
                timestamp: auditConfigResult.timestamp
            });
        }
        await this.#insertConfigAuditEs(documentDb, {
            docType,
            config: auditConfigResult.config,
            timestamp: auditConfigResult.timestamp,
            eventType: auditConfigResult.eventType,
            referenceId,
            status,
            error
        });
    }
    
    /** 
     * returns a success message once the config has been updated and kafka message is sent.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {MessageBroker} messageBroker - MessageBroker Object
     * @param {("behavior-analytics")} docType - Config document type.
     * @param {string} inputConfig - A json stringified config object
     * @returns {Promise<Object>} A success message is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * const kafka = new mdx.Utils.Kafka({brokers: ["kafka-broker-url"]}, kafkaConfigMap);
     * let docType = "behavior-analytics";
     * let configManagerObject = new mdx.Services.ConfigManager();
     * let result = await configManagerObject.update(elastic,kafka,docType,inputConfig);
     */
    async update(documentDb, messageBroker, docType, inputConfig){
        const requestTimestamp = new Date().toISOString();
        if(messageBroker==null){
            throw(new InvalidInputError("A message broker like 'kafka' is required to dynamically update a microservice's config."));
        }
        if(!ConfigManager.#validDocTypes.has(docType)){
            throw new BadRequestError(`Invalid docType: ${docType}.`);
        }
        if(docType==="behavior-analytics"){
            let behaviorAnalyticsConfigValidationResult = Validator.validateJsonSchema(inputConfig,behaviorAnalyticsConfigStrictSchema,false);
            if (!behaviorAnalyticsConfigValidationResult.valid) {
                throw(new BadRequestError("Behavior analytics configuration doesn't follow schema."));
            }
        }
        let notificationManagerObject = new NotificationManager();
        switch(documentDb.getName()){
            case "Elasticsearch":{
                const requestTimestampEpoch = new Date(requestTimestamp).getTime();
                const uuid = `${requestTimestampEpoch}-${Math.round(Math.random() * 1E9)}`;
                const referenceId = `${ConfigManager.#VIDEO_ANALYTICS_API_CONFIG_UPDATE_REFERENCE_PREFIX}${uuid}`;
                await this.#insertConfigAuditEs(documentDb,{
                    docType,
                    config: JSON.stringify(inputConfig),
                    eventType: "upsert",
                    status: "pending",
                    referenceId,
                    timestamp: requestTimestamp
                });
                try{
                    await this.#produceConfigNotificationWithRetry(notificationManagerObject, messageBroker, {docType, timestamp: requestTimestamp, eventType: "upsert", referenceId, config: JSON.stringify(inputConfig)}, ConfigManager.#CONFIG_UPDATE_PUBLISH_RETRIES);
                }catch(error){
                    const publishError = `Unable to send config update to behavior analytics via kafka.`;
                    await this.#insertConfigAuditEs(documentDb,{
                        docType,
                        config: null,
                        eventType: "upsert",
                        status: "failure",
                        referenceId,
                        timestamp: requestTimestamp,
                        error: publishError
                    });
                    logger.error(`[Kafka Message] ${publishError}`);
                    return {referenceId, status: "failure", config: null, error: publishError};
                }
                return {referenceId, status: "pending"};
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }
}

module.exports = ConfigManager;
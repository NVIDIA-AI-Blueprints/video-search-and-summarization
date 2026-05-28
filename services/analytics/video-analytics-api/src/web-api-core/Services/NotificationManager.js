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

const BadRequestError = require("../Errors/BadRequestError");
const InvalidInputError = require('../Errors/InvalidInputError');
const InternalServerError = require("../Errors/InternalServerError");
const calibrationSchema = require("../schemas/ajv/calibration.json");
const Kafka = require("../Utils/Kafka");
const Validator = require("../Utils/Validator");
const Database = require("../Utils/Database");
const MessageBroker = require("../Utils/MessageBroker");
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
 * Class which defines NotificationManager
 * @memberof mdxWebApiCore.Services
 * */

class NotificationManager {

    async #produceNotificationKafka(kafka, {messageKey, messageValue, timestamp, eventType, referenceId=null}){
        const kafkaClient = kafka.getClient();
        const topic = Kafka.getTopic("notification");
        let message ={
            key: messageKey,
            value: messageValue,
            headers:{
                "event.type": Buffer.from(eventType, 'utf8'),
                "timestamp": Buffer.from(timestamp, 'utf8')
            }
        };
        if(referenceId!=null){
            message.headers["reference-id"] = Buffer.from(referenceId, 'utf8');
        }
        let result = await Kafka.produceMessages(kafkaClient, topic, [message]);
        return result;
    }

    /** 
     * returns a success message when the calibration input message is sent to the message broker.
     * @public
     * @async
     * @param {MessageBroker} messageBroker - MessageBroker Object
     * @param {Object} input - Input object.
     * @param {string} input.timestamp - Notification timestamp in ISO 8601 format.
     * @param {("upsert-all"|"upsert"|"delete")} input.eventType - Calibration notification event type.
     * @param {Object} input.calibration - Calibration object
     * @returns {Promise<Object>} A success message is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const kafka = new mdx.Utils.Kafka({brokers: ["kafka-broker-url"]}, kafkaConfigMap);
     * let input = {timestamp: "2023-01-12T14:20:10.000Z", eventType: "upsert-all", calibration};
     * let notificationManagerObject = new mdx.Services.NotificationManager();
     * let result = await notificationManagerObject.produceCalibrationNotification(kafka,input);
     */
    async produceCalibrationNotification(messageBroker, input){
        if(messageBroker==null){
            throw(new InvalidInputError("A message broker like 'kafka' is required to produce calibration notification."));
        }
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                timestamp: {
                    type: "string"
                },
                eventType:{
                    type: "string",
                    enum: [
                        "upsert-all",
                        "upsert",
                        "delete"
                    ],
                    errorMessage:{
                        enum: "eventType must be one of the following values: 'upsert-all', 'upsert' or 'delete'."
                    }
                },
                calibration:{
                    type: "object"
                }
            },
            required: ["timestamp","eventType","calibration"],
            errorMessage:{
                required: "Input should have required properties 'timestamp', 'eventType' and 'calibration'."
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        if (!Validator.isValidISOTimestamp(input.timestamp)) {
            throw (new InvalidInputError("Invalid timestamp."));
        }
        let calibrationValidationResult = Validator.validateJsonSchema(input.calibration,calibrationSchema,false);
        if (!calibrationValidationResult.valid) {
            if(calibrationValidationResult.reason === "Invalid input. Error 1: calibrationType must be one of the following values: 'geo', 'cartesian' or 'image'."){
                if(input.calibration.calibrationType!=="" || (input.calibration.calibrationType==="" && input.calibration.sensors.length>0)){
                    throw(new InvalidInputError("Calibration doesn't follow schema."));
                }
            }else{
                throw(new InvalidInputError("Calibration doesn't follow schema."));
            }
        }
        switch (messageBroker.getName()){
            case "Kafka": {
                let result = await this.#produceNotificationKafka(messageBroker, {
                    timestamp:input.timestamp, 
                    eventType:input.eventType,
                    messageKey:"calibration", 
                    messageValue: JSON.stringify(input.calibration)
                });
                logger.info("[Kafka Message] Produced calibration message.");
                return result;
            }
            default:
                throw (new InternalServerError(`Invalid message broker: ${messageBroker.getName()}.`));
        }
    }

    async #consumeAndProcessKafkaNotification(kafka,documentDb,configManagerObject,calibrationObject){
        const processNotificationKafkaMessage = async ({topic, partition, message}) => {
            if(message.key!=null){
                let key = message.key.toString();
                const eventType = message.headers?.["event.type"]?.toString();
                if (key === "request-calibration") {
                    logger.info("[Kafka Message] Received calibration request.");
                    let {calibration,timestamp} = await calibrationObject.getCalibration(documentDb);
                    let stringifiedMessageValue = JSON.stringify(calibration);
                    await this.#produceNotificationKafka(kafka, {messageKey:"calibration",messageValue:stringifiedMessageValue,timestamp,eventType:"upsert-all"});
                    logger.info("[Kafka Message] Produced calibration message.");
                }
                else if(key === "behavior-analytics-config" && eventType === "request-config"){
                    logger.info("[Kafka Message] Received behavior analytics config request.");
                    const docType = "behavior-analytics";
                    const referenceId = message.headers?.["reference-id"]?.toString();
                    if(referenceId==null){
                        throw(new InvalidInputError("Config request is missing reference-id header."));
                    }
                    const [
                        {config,timestamp},
                        auditHasSuccessfulConfigUpdate
                    ] = await Promise.all([
                        configManagerObject.getConfig(documentDb,docType),
                        configManagerObject.auditHasSuccessfulConfigUpdate(documentDb,docType)
                    ]);
                    const isBootstrap = config==null && !auditHasSuccessfulConfigUpdate;
                    const configNotification = {
                        docType,
                        timestamp,
                        referenceId,
                        eventType: "upsert-all",
                        config,
                        isBootstrap
                    };
                    logger.info(`[Kafka Message] Produced behavior analytics config: ${JSON.stringify(configNotification)}`);
                    await this.produceConfigNotification(kafka, configNotification);
                }else if(key === "behavior-analytics-config" && eventType === "upsert"){
                    logger.info("[Kafka Message] Received behavior analytics config update.");
                    let referenceId = message.headers?.["reference-id"]?.toString();
                    if(referenceId==null){
                        logger.warn("[Kafka Message] Ignoring behavior analytics config update missing reference-id header.");
                        return;
                    }
                    const timestamp = message.headers?.timestamp?.toString() || new Date().toISOString();
                    if(referenceId === "kafka"){
                        const timestampEpoch = new Date(timestamp).getTime();
                        if(Number.isNaN(timestampEpoch)){
                            logger.warn("[Kafka Message] Ignoring behavior analytics config update for referenceId kafka: invalid timestamp header.");
                            return;
                        }
                        referenceId = `kafka-${timestampEpoch}`;
                    }
                    let requestBody;
                    try{
                        requestBody = JSON.parse(message.value.toString());
                    }catch(error){
                        logger.warn(`[Kafka Message] Ignoring behavior analytics config update for referenceId ${referenceId}: invalid JSON body.`);
                        return;
                    }
                    if(referenceId.startsWith("kafka-")){
                        const schema = {
                            type: "object",
                            additionalProperties: {
                                not: true,
                                errorMessage: "Invalid additional Input ${0#}."
                            },
                            properties: {
                                status: {
                                    type: ["string","null"]
                                },
                                config: {
                                    type: "object"
                                },
                                error: {
                                    type: ["string","null"]
                                }
                            },
                            required: ["status","config","error"],
                            errorMessage: {
                                required: "Input should have required properties 'status', 'config' and 'error'."
                            }
                        };
                        let validationResult = Validator.validateJsonSchema(requestBody, schema);
                        if (!validationResult.valid) {
                            logger.warn(`[Kafka Message] Ignoring behavior analytics config update for referenceId ${referenceId}: ${validationResult.reason}`);
                            return;
                        }
                        try{
                            const result = await configManagerObject.recordConfigUpdateRequest(documentDb,{
                                docType: "behavior-analytics",
                                referenceId,
                                config: requestBody.config,
                                timestamp,
                                eventType,
                                error: requestBody.error
                            });
                            logger.info(`[Kafka Message] Recorded behavior analytics config update: ${JSON.stringify(result)}`);
                        }catch(error){
                            if(error instanceof BadRequestError || error instanceof InvalidInputError){
                                logger.warn(`[Kafka Message] Ignoring behavior analytics config update for referenceId ${referenceId}: ${error.message}`);
                                return;
                            }
                            throw error;
                        }
                    }
                }else if(key === "behavior-analytics-config" && eventType === "ack"){
                    const referenceId = message.headers?.["reference-id"]?.toString();
                    if(referenceId==null){
                        throw(new InvalidInputError("ack message is missing reference-id header."));
                    }
                    let responseBody = JSON.parse(message.value.toString());
                    const schema = {
                        type: "object",
                        additionalProperties: {
                            not: true,
                            errorMessage: "Invalid additional Input ${0#}."
                        },
                        properties: {
                            status: {
                                type: "string",
                                enum: ["success","partial-success","failure"],
                                errorMessage: {
                                    enum: "status must be one of the following values: 'success', 'partial-success' or 'failure'."
                                }
                            },
                            config: {
                                type: ["object","null"]
                            },
                            error: {
                                type: ["string","null"]
                            }
                        },
                        required: ["status","config","error"],
                        errorMessage: {
                            required: "Input should have required properties 'status', 'config' and 'error'."
                        }
                    };
                    let validationResult = Validator.validateJsonSchema(responseBody, schema);
                    if (!validationResult.valid) {
                        throw(new BadRequestError(validationResult.reason));
                    }

                    if(responseBody.status === "failure"){
                        if(responseBody.config !== null){
                            throw(new BadRequestError("config must be null when status is failure."));
                        }
                    }else{
                        if(responseBody.config == null){
                            throw(new BadRequestError(`config is required when status is ${responseBody.status}.`));
                        }
                        let behaviorAnalyticsConfigValidationResult = null;
                        if(referenceId.startsWith("video-analytics-api-")){
                            behaviorAnalyticsConfigValidationResult = Validator.validateJsonSchema(responseBody.config, behaviorAnalyticsConfigStrictSchema, false);
                        }else if(referenceId.startsWith("kafka")){
                            behaviorAnalyticsConfigValidationResult = Validator.validateJsonSchema(responseBody.config, behaviorAnalyticsConfigSchema, false);
                        }
                        if(behaviorAnalyticsConfigValidationResult != null && !behaviorAnalyticsConfigValidationResult.valid){
                            throw(new BadRequestError("Behavior analytics configuration doesn't follow schema."));
                        }
                    }

                    await configManagerObject.updateConfigResult(documentDb,{
                        docType: "behavior-analytics",
                        referenceId,
                        status: responseBody.status,
                        config: responseBody.config,
                        error: responseBody.error
                    });
                }
            }
        }
        
        const kafkaClient = kafka.getClient();
        const adminClient = kafka.getAdminClient();
        const topic = Kafka.getTopic("notification");
        const consumerGroup = "mdx-notification-web-api";
        await Kafka.initializeConsumer(({topic, partition, message})=> processNotificationKafkaMessage({topic, partition, message}), kafkaClient, adminClient, consumerGroup, topic, {autoCommit: false});
    }

    /** 
     * consumes and processes incoming notification messages.
     * @public
     * @async
     * @param {MessageBroker} messageBroker - MessageBroker Object
     * @param {Database} documentDb - Database Object.
     * @param {Object} configManagerObject - configManager Object
     * @param {Object} calibrationObject - calibration Object
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const kafka = new mdx.Utils.Kafka({brokers: ["kafka-broker-url"]}, kafkaConfigMap);
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let notificationManagerObject = new mdx.Services.NotificationManager();
     * let configManagerObject = new mdx.Services.ConfigManager();
     * let calibrationObject = new mdx.Services.Calibration();
     * await notificationManagerObject.consumeAndProcessNotification(kafka,elastic,configManagerObject,calibrationObject);
     */
    async consumeAndProcessNotification(messageBroker, documentDb, configManagerObject, calibrationObject){
        if(messageBroker==null){
            throw(new InvalidInputError("A message broker like 'kafka' is required to consume and process notification."));
        }
        switch (messageBroker.getName()){
            case "Kafka": {
                await this.#consumeAndProcessKafkaNotification(messageBroker,documentDb,configManagerObject,calibrationObject);
                break;
            }
            default:
                throw (new InternalServerError(`Invalid message broker: ${messageBroker.getName()}.`));
        }
    }

    /** 
     * returns a success message when the config related input message is sent to the message broker.
     * @public
     * @async
     * @param {MessageBroker} messageBroker - MessageBroker Object
     * @param {Object} input - Input object.
     * @param {("behavior-analytics")} input.docType - Config document type.
     * @param {string} input.timestamp - Notification timestamp in ISO 8601 format.
     * @param {string} input.referenceId - Config update reference ID.
     * @param {("upsert-all"|"upsert")} input.eventType - Config notification event type.
     * @param {?string} input.config - JSON stringified config object
     * @returns {Promise<Object>} A success message is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const kafka = new mdx.Utils.Kafka({brokers: ["kafka-broker-url"]}, kafkaConfigMap);
     * let input = {docType: "behavior-analytics", timestamp: "2026-01-12T14:20:10.000Z", referenceId: "video-analytics-api-...", eventType: "upsert", config};
     * let notificationManagerObject = new mdx.Services.NotificationManager();
     * let result = await notificationManagerObject.produceConfigNotification(kafka,input);
     */
    async produceConfigNotification(messageBroker, input){
        if(messageBroker==null){
            throw(new InvalidInputError("A message broker like 'kafka' is required to produce config notification."));
        }
        const validConfigDocTypes = ["behavior-analytics"];
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                docType:{
                    type: "string",
                    enum: validConfigDocTypes,
                    errorMessage:{
                        enum: `docType must be one of the following values: ${validConfigDocTypes.join(", ")}.`
                    }
                },
                timestamp: {
                    type: "string"
                },
                referenceId: {
                    type: "string"
                },
                eventType:{
                    type: "string",
                    enum: [
                        "upsert-all",
                        "upsert"
                    ],
                    errorMessage:{
                        enum: "eventType must be one of the following values: 'upsert-all' or 'upsert'."
                    }
                },
                config:{
                    type: ["string","null"]
                },
                isBootstrap:{
                    type: "boolean"
                }
            },
            required: ["docType","timestamp","referenceId","eventType","config"],
            errorMessage:{
                required: "Input should have required properties 'docType', 'timestamp', 'referenceId', 'eventType' and 'config'."
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        if (!Validator.isValidISOTimestamp(input.timestamp)) {
            throw (new InvalidInputError("Invalid timestamp."));
        }
        let config = null;
        if(input.config!=null){
            config = JSON.parse(input.config);
        }
        switch (messageBroker.getName()){
            case "Kafka": {
                const messageValue = JSON.stringify({
                    status: (config==null && !input.isBootstrap) ? "failure" : "success",
                    config,
                    error: (config==null && !input.isBootstrap) ? `no config for ${input.docType} in Elasticsearch` : null
                });
                let result = await this.#produceNotificationKafka(messageBroker, {
                    timestamp:input.timestamp, 
                    referenceId: input.referenceId,
                    eventType:input.eventType,
                    messageKey: "behavior-analytics-config", 
                    messageValue
                });
                logger.info(`[Kafka Message] Produced behavior analytics config: ${messageValue}`);
                return result;
            }
            default:
                throw (new InternalServerError(`Invalid message broker: ${messageBroker.getName()}.`));
        }
    }
}


module.exports = NotificationManager;

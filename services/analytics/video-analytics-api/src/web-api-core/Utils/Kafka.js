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

const MessageBroker = require("./MessageBroker");
const Validator = require("../Utils/Validator");
const InvalidInputError = require('../Errors/InvalidInputError');
const { Kafka: KafkaClient, AssignerProtocol, KafkaJSNonRetriableError } = require('kafkajs');
const Utils = require("./Utils");
const winston = require('winston');
const logger = winston.createLogger({
    format: winston.format.combine(
        winston.format.timestamp(),
        winston.format.printf(({ timestamp, level, message, ...meta }) => {
            return JSON.stringify({ timestamp, level, message, ...meta });
        })
    ),
    transports: [
        new winston.transports.Console({ level: 'info' })
    ],
    exitOnError: false
});

/** 
 * Class containing Kafka Utils
 * @memberof mdxWebApiCore.Utils
 * @extends MessageBroker
 * */

class Kafka extends MessageBroker {

    #adminClient;

    static #DEFAULT_CLIENT_OPTIONS = {
        connectionTimeout: 10000,
        requestTimeout: 30000,
        retry: {
            initialRetryTime: 1000,
            retries: 15,
        },
    };

    /**
     * Constructor
     * @param {Object} connectionObject - KafkaJS client options (brokers, ssl, sasl, retry, connectionTimeout, etc.).
     *                                    Caller-provided values override the defaults in #DEFAULT_CLIENT_OPTIONS.
     * @param {Map} configs
     */
    constructor(connectionObject,configs){
        const defaultClientOptions = Kafka.#DEFAULT_CLIENT_OPTIONS;
        const clientOptions = {
            ...defaultClientOptions,
            ...connectionObject,
            retry: {
                ...defaultClientOptions.retry,
                ...connectionObject?.retry,
            },
        };

        const kafkaClient = new KafkaClient(clientOptions);
        super({name: "Kafka", client: kafkaClient, configs});
        this.#adminClient = kafkaClient.admin();
    }

    static #topics = this.#initTopics();
    static #topicPattern=this.#initTopicPattern();

    static #initTopics(){
        let topics = new Map();
        topics.set("notification","mdx-notification");
        topics.set("amr","mdx-amr");
        return topics;
    }

    static #initTopicPattern(){
        let topics = new Map();
        topics.set("rtls","^mdx-rtls.*");
        return topics;
    }

    /**
     * Used to return topic
     * @public
     * @static
     * @param {string} topicType - Topic type used to retrieve the configured topic.
     * @returns {string|undefined} Returns topic
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * let topicType = "notification";
     * let topic = mdx.Utils.Kafka.getTopic(topicType);
     */
    static getTopic(topicType){
        return this.#topics.get(topicType);
    }

    /**
     * Returns the regex pattern string used to subscribe to a Kafka topic family.
     * @public
     * @static
     * @param {string} topicType - Topic pattern identifier.
     * @returns {string|undefined} Topic regex pattern for the given type.
     */
    static getTopicPattern(topicType){
        return this.#topicPattern.get(topicType);
    }

    /** 
     * returns the admin client of kafka.
     * @public
     * @returns {Object}
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const kafka = new mdx.Utils.Kafka({brokers: ["kafka-broker-url"]}, kafkaConfigMap);
     * let adminClient = kafka.getAdminClient();
     */
    getAdminClient(){
        return this.#adminClient;
    }

    /**
     * returns a success message once the input messages are produced
     * @public
     * @static
     * @async
     * @param {Object} client - Kafka client object.
     * @param {string} topic - Kafka topic to consume.
     * @param {Array<Object>} messages - Each message will have value and may contain key and headers.
     * @returns {Promise<Object>} A success message is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const kafka = new mdx.Utils.Kafka({brokers: ["kafka-broker-url"]}, kafkaConfigMap);
     * let result = await kafka.produceMessages(kafka.getClient(), topic, messages);
     */
    static async produceMessages(client, topic, messages){
        const producer = client.producer();
        await producer.connect();
        await producer.send({
            topic,
            messages
        });
        await producer.disconnect();
        return ({ success: true});
    }

    static async #getAllTopics(adminClient){
        let allTopics = null;
        try {
            allTopics = await adminClient.listTopics();
        } catch (error) {
            if (error.name === 'KafkaJSConnectionError') {
                await adminClient.connect();
                allTopics = await adminClient.listTopics();
            } else {
                throw error;
            }
        }
        return allTopics;
    }

    /**
     * Creates, subscribes, and runs a Kafka consumer with restart handling.
     * @public
     * @static
     * @async
     * @param {Function} messageTransferFunction - Callback invoked for each consumed message.
     * @param {Object} client - Kafka client used to create the consumer.
     * @param {Object} adminClient - Kafka admin client used to inspect topic availability.
     * @param {string} consumerGroup - Consumer group identifier.
     * @param {string|RegExp} topic - Topic name or regex pattern to subscribe to.
     * @param {Object} [options={}] - Consumer startup options.
     */
    static async initializeConsumer(messageTransferFunction, client, adminClient, consumerGroup, topic, options={}) {
        const optionsSchema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                isTopicPattern: {
                    type: "boolean",
                    default: false,
                    errorMessage: {
                        type: "isTopicPattern doesn't have a boolean value.",
                    }
                },
                autoCommit: {
                    type: "boolean",
                    default: true,
                    errorMessage: {
                        type: "autoCommit doesn't have a boolean value.",
                    }
                },
                fromBeginning: {
                    type: "boolean",
                    default: false,
                    errorMessage: {
                        type: "fromBeginning doesn't have a boolean value.",
                    }
                }
            }
        }
        let validationResult = Validator.validateJsonSchema(options, optionsSchema);
        if (!validationResult.valid) {
            throw (new InvalidInputError(validationResult.reason));
        }
        const {isTopicPattern, autoCommit, fromBeginning} = options;
        let consumer = null;
        let removeCrashListener = null;
        let topicCheckInterval = null;
        let isRestarting = false;
        let restartAttempts = 0;
        const INITIAL_BACKOFF = 1000;
        const MAX_BACKOFF = 32000;
        const MAX_RESTART_ATTEMPTS = 1000;
        const CHECK_INTERVAL = 30000;
    
        const createConsumer = async () => {
            consumer = client.consumer({
                groupId: consumerGroup,
                maxWaitTimeInMs: 100
            });
            await consumer.connect();
            await consumer.subscribe({ topic, fromBeginning });
        };
    
        const isTopicPresent = async () => {
            let allTopics = await this.#getAllTopics(adminClient);
            if(isTopicPattern){
                const topicRegex = topic;
                const matchingTopics = allTopics.filter(topicName => topicRegex.test(topicName));
                if(matchingTopics.length>0){
                    return true;
                }
                return false;
            }else{
                for(let topicName of allTopics){
                    if(topicName === topic){
                        return true;
                    }
                }
                return false;
            }
        }

        const waitTillTopicIsPresent = async () => {
            let topicPresent = await isTopicPresent();
            while(!topicPresent){
                logger.info(`[KAFKA TOPIC] Kafka topic ${isTopicPattern ? `matching the pattern: ${topic.toString()}` : `${topic}`} is not present. Consumer will start once topics are created.`);
                await Utils.sleep(1000);
                topicPresent = await isTopicPresent();
            }
            logger.info(`[KAFKA TOPIC] Kafka topic ${isTopicPattern ? `matching the pattern: ${topic.toString()}` : `${topic}`} has been created.`);
        }

        const hasNewTopicMatches = async () => {
            try{
                const topicRegex = topic;
                let allTopics = await this.#getAllTopics(adminClient);
                const matchingTopics = allTopics.filter(topicName => topicRegex.test(topicName));
                let groupDescription= await consumer.describeGroup();
                if(groupDescription.state === "Stable"){
                    const decodedAssignments = groupDescription.members.map(member => {
                        return {
                            memberId: member.memberId,
                            assignment: AssignerProtocol.MemberAssignment.decode(member.memberAssignment)
                        }
                    });
                    if(decodedAssignments[0].hasOwnProperty("assignment") && decodedAssignments[0].assignment!= null){
                        const currentTopics = Object.keys(decodedAssignments[0].assignment.assignment);
                        const newTopics = matchingTopics.filter(topicName => !currentTopics.includes(topicName));
                        if (newTopics.length > 0) {
                            logger.info(`New topic(s) found: ${newTopics.join(", ")}.`);
                            return true;
                        }
                    }
                }
            }catch (error) {
                logger.error(`[KAFKA ERROR] Error while trying to find new topics: ${error.name} - ${error.message}`);
                console.error(error);
            }
            return false;
        }
    
        const setupTopicCheckInterval = () => {
            topicCheckInterval = setInterval(async () => {
                const hasMatchingNewTopics = await hasNewTopicMatches();
                if (hasMatchingNewTopics) {
                    await restartConsumer();
                }
            }, CHECK_INTERVAL);
        };
    
        const updateCrashHandler = () => {
            if (removeCrashListener) {
                removeCrashListener();
            }
            removeCrashListener = consumer.on('consumer.crash', crashHandler);
        };
    
        const startConsumer = async () => {
            await consumer.run({
                autoCommit,
                restartOnFailure: async (error) => {
                    logger.error(`[KAFKA CONSUMER ERROR] Consumer failure detected: ${error.name} - ${error.message}`);
                    console.error(error);
                    return false;
                },
                eachMessage: async ({ topic, partition, message }) => {
                    try {
                        await Utils.conditionalAsync(messageTransferFunction({ topic, partition, message}));
                    } catch (error) {
                        logger.error(`[KAFKA MESSAGE ERROR] Error processing message from ${topic}-${partition}: ${error.name} - ${error.message}`);
                        console.error(error);
                        throw error;
                    }
                }
            });
        };
    
        const restartConsumer = async (error = null) => {
            if (isRestarting) return false;
            isRestarting = true;
    
            try {
    
                if (restartAttempts > 0) {
                    let backoffTime = INITIAL_BACKOFF * Math.pow(2, restartAttempts - 1);
                    if(backoffTime > MAX_BACKOFF){
                        backoffTime = MAX_BACKOFF;
                    }
                    logger.info(`[KAFKA CONSUMER INFO] Waiting for ${backoffTime}ms before attempting restart`);
                    await Utils.sleep(backoffTime);
                }
    
                restartAttempts++;
    
                if (error instanceof KafkaJSNonRetriableError) {
                    logger.error(`[KAFKA CONSUMER ERROR] Non-retriable error occurred: ${error.name} - ${error.message}`);
                    console.error(error);
                    return false;
                }

                if (restartAttempts > MAX_RESTART_ATTEMPTS) {
                    logger.error('[KAFKA CONSUMER ERROR] Max restart attempts reached. Exiting.');
                    return false;
                }
    
                logger.info(`[KAFKA CONSUMER] Restarting consumer${error ? ` due to error: ${error.name} - ${error.message}` : ''}`);
    
                if (removeCrashListener) {
                    removeCrashListener();
                }

                if (consumer) {
                    try {
                        await consumer.disconnect();  
                    }
                    catch (disconnectError) {
                        logger.error(`[KAFKA CONSUMER ERROR] Error during disconnect: ${disconnectError.name} - ${disconnectError.message}`); 
                        console.error(disconnectError);
                    }
                    await Utils.sleep(1000);
                    consumer = null;
                }

                await waitTillTopicIsPresent();
                
                await createConsumer();
                
                updateCrashHandler();
    
                if (topicCheckInterval) {
                    clearInterval(topicCheckInterval);
                }
    
                await startConsumer();
                if(isTopicPattern){
                    setupTopicCheckInterval();
                }
                restartAttempts = 0;
                return true;
            } catch (restartError) {
                logger.error(`[KAFKA CONSUMER ERROR] Failed to restart consumer: ${restartError.name} - ${restartError.message}`);
                console.error(restartError);
                return false;
            } finally {
                isRestarting = false;
            }
        };
    
        const crashHandler = async ({ payload }) => {
            logger.info('[KAFKA CONSUMER] Consumer crashed, attempting to restart');
            const restartSuccessful = await restartConsumer(payload.error);
            if (restartSuccessful) {
                logger.info('Consumer successfully restarted');
            } else {
                logger.error('[KAFKA CONSUMER ERROR] Failed to restart consumer.');
            }
        };
    
        // Initial setup
        await waitTillTopicIsPresent();
        await createConsumer();
        updateCrashHandler();
        await startConsumer();
        if(isTopicPattern){
            setupTopicCheckInterval();
        }
    }
}

module.exports = Kafka;

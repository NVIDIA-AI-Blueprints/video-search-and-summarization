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

const express = require('express');
const mdx = require("@nvidia-mdx/web-api-core");
const fs = require('fs');
const path = require('path');
const defaultBootstrapConfig = require('../../../configs/default-configs/config.json');
const cache = require('./cache');
const cron = require('./cron');
const elasticErrors = mdx.Utils.Elasticsearch.getElasticErrors();
const morgan = require('morgan');
const winston = require('winston');
const FILE_UPLOAD_DIRECTORY = path.join(__dirname, '../files');
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



module.exports = {
    /**
     * Starts the web API server and initializes its dependencies.
     * @param {string|null} bootstrapConfigPath - Optional path to a bootstrap config override file.
     * @returns {void}
     */
    start: (bootstrapConfigPath) => {

        logger.info('[APP] Starting server initialization');

        let ConfigObject = new mdx.Utils.Config({ bootstrap: defaultBootstrapConfig });

        if (bootstrapConfigPath !== null) {
            if (fs.existsSync(bootstrapConfigPath)) {
                const bootstrapConfig = require(bootstrapConfigPath);
                ConfigObject.overrideBootstrapConfig(bootstrapConfig);
            } else {
                logger.error("[INPUT ERROR] Invalid path for bootstrap config file.");
                process.exit(1);
            }
        }

        try {
            if (!fs.existsSync(FILE_UPLOAD_DIRECTORY)) {
                fs.mkdirSync(FILE_UPLOAD_DIRECTORY, { recursive: true });
                logger.info(`[SERVER] Created files directory: ${FILE_UPLOAD_DIRECTORY}`);
            }

            if (!fs.statSync(FILE_UPLOAD_DIRECTORY).isDirectory()) {
                logger.error(`[SERVER ERROR] Expected files directory path is not a directory: ${FILE_UPLOAD_DIRECTORY}`);
                process.exit(1);
            }
        } catch (error) {
            logger.error(`[SERVER ERROR] Failed to prepare files directory: ${FILE_UPLOAD_DIRECTORY}`, { error: error.message });
            process.exit(1);
        }

        let bootstrapObjectMap = ConfigObject.getBootstrapObjectMap();

        cache.set("bootstrap-config", bootstrapObjectMap);

        // Configure express 
        let app = express();
        app.set("query parser", "extended");
        app.use(morgan('common'));
        app.use(express.urlencoded({ extended: true }));
        app.use(express.json({ limit: bootstrapObjectMap.server.configs.get("postBodySizeLimit"), type: 'application/json' }));
        app.use((req, res, next) => {
            res.setHeader("Access-Control-Allow-Origin", "*");
            res.setHeader("Access-Control-Allow-Headers", "Origin, X-Requested-With, Content-Type, Accept, Remote-User");
            next();
        });

        let port = bootstrapObjectMap.server.port;

        let server = app.listen(port);

        const inSimulationMode = (bootstrapObjectMap.server.configs.get("inSimulationMode") === "true");
        cache.set("inSimulationMode", inSimulationMode);

        logger.info(`[SERVER] Listening on port: ${port}`);

        const elastic = require('./elastic');

        elastic.getClient().ping().then(async(_) => {

            const kafka = require('./kafka');
            if(kafka!=null){
                let calibrationObject = new mdx.Services.Calibration();
                let configManagerObject = new mdx.Services.ConfigManager();
                let notificationManagerObject = new mdx.Services.NotificationManager();
                let {calibration,timestamp} = await calibrationObject.getCalibration(elastic);
                if(calibration.calibrationType!==""){
                    notificationManagerObject.produceCalibrationNotification(kafka, {calibration,timestamp,eventType:"upsert-all"}).catch(error => {
                        throw(error);
                    });
                }
                notificationManagerObject.consumeAndProcessNotification(kafka,elastic,configManagerObject,calibrationObject).catch(error => {
                    throw(error);
                });

                cron.startConfigStatusTimeoutCheck(elastic);
                let mtmcObject = new mdx.Services.MTMC();
                let rtlsConfig = { inSimulationMode }
                mtmcObject.consumeRTLSMessages(kafka, rtlsConfig).catch(error=>{
                    throw(error);
                });
                let amrConfig = {
                    amrRetentionInSec: parseInt(bootstrapObjectMap.server.configs.get("amrRetentionInSec")),
                    inSimulationMode
                } 
                mtmcObject.consumeAMRMessages(kafka, amrConfig).catch(error=>{
                    throw(error);
                });
            }

            logger.info('[SERVER] Initializing routes');

            require('./routes')(app);

            app.use(function (error, req, res, next) {
                if (error instanceof mdx.Errors.BadRequestError) {
                    logger.error(`[BAD REQUEST ERROR] ${error.message}`);
                    res.status(400).json({ error: error.message });
                } else if (error instanceof mdx.Errors.ResourceNotFoundError) {
                    logger.error(`[RESOURCE ERROR] ${error.message}`);
                    res.status(404).json({ error: error.message });
                } else if (error instanceof mdx.Errors.InvalidInputError) {
                    logger.error(`[INPUT ERROR] ${error.message}`);
                    res.status(422).json({ error: error.message });
                } else if (error instanceof mdx.Errors.ServiceUnavailableError) {
                    logger.error(`[SERVICE UNAVAILABLE ERROR] ${error.message}`);
                    res.status(503).json({ error: error.message });
                } else {
                    logger.error(`[SERVER ERROR] ${error.toString()}`);
                    if (error instanceof elasticErrors.ElasticsearchClientError) {
                        error = error.body;
                    }
                    console.error(error);
                    res.status(500).json({ error: 'Something broke!' });
                }
                return next();
            });

            logger.info('[APP] Server initialization successful');
        }).catch(error => {
            logger.error(`[APP ERROR] Server initialization failed: ${error.toString()}`);
            if (error instanceof Error) {
                console.error(error);
            }
            server.close();
        });
    }
}

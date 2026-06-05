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

const changeCase = require('change-case');
const express = require('express');
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
 * Discovers and mounts REST API route modules on the Express application.
 * @param {import("express").Application} app - Express application instance.
 * @returns {void}
 */
module.exports = (app) => {
    let routesLocation = "../controllers/rest-apis";
    let routes = require('require-dir')(routesLocation);
    logger.info(`[ROUTES] Initializing routers: ${Object.keys(routes)}`);
    // Initialize all routes
    Object.keys(routes).forEach( (routeName) => {
        let router = express.Router();
        // You can add some middleware here 
        // router.use(someMiddleware);

        logger.info(`[ROUTES] Initializing router for ${routeName}`);
        // Initialize the route to add its functionality to router
        require(`${routesLocation}/${routeName}`)(router);

        // Add router to the speficied route name in the app
        let route=`/${changeCase.kebabCase(routeName)}`;
        app.use(route, router);

        logger.info(`[ROUTES] Initialized under API call: ${route}`);
    });

}

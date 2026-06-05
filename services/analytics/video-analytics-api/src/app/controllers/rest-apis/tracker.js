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

const mdx = require("@nvidia-mdx/web-api-core");
const elastic = require('../../initializers/elastic');
const kafka = require('../../initializers/kafka');
const cache = require('../../initializers/cache');

/**
 * Registers tracker routes on the provided router.
 * @param {import("express").Router} router - Router instance used for the tracker API.
 * @returns {void}
 */
module.exports = (router) => {

    // This will handle the url calls for /tracker

    router.route("/unique-object-count").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let mtmc = new mdx.Services.MTMC();
        let uniqueObjectCount = await mtmc.getUniqueObjectCount(elastic, input);
        res.status(200).json(uniqueObjectCount);
        return next();
    }));

    router.route("/unique-objects").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let mtmc = new mdx.Services.MTMC();
        let uniqueObjects = await mtmc.getUniqueObjects(elastic, input);
        res.status(200).json(uniqueObjects);
        return next();        
    }));

    router.route("/behavior-locations").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let mtmc = new mdx.Services.MTMC();
        let behaviors = await mtmc.getLocationsOfMatchedBehaviors(elastic, input);
        res.status(200).json(behaviors);
        return next();
    }));

    router.route("/unique-object-count-with-locations").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let inSimulationMode = cache.get("inSimulationMode");
        let mtmc = new mdx.Services.MTMC();
        let uniqueObjectCountWithLocations = await mtmc.getUniqueObjectCountWithLocations(elastic, kafka, input, inSimulationMode);
        res.status(200).json(uniqueObjectCountWithLocations);
        return next();
    }));

    router.route("/last-record").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let mtmc = new mdx.Services.MTMC();
        let lastRecord = await mtmc.getLastRecord(elastic, input);
        res.status(200).json(lastRecord);
        return next();
    }));

}

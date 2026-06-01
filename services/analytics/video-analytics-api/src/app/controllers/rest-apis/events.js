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

/**
 * Registers event routes on the provided router.
 * @param {import("express").Router} router - Router instance used for the events API.
 * @returns {void}
 */
module.exports = (router) => {

    // This will handle the url calls for /events

    router.route("/tripwire").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let tripwireMetadata = new mdx.Services.Events();
        let tripwireEvents = await tripwireMetadata.getTripwireEvents(elastic,input);
        res.status(200).json(tripwireEvents);
        return next();
    }));
    
    router.route("/roi").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let eventsMetadata = new mdx.Services.Events();
        let roiEvents = await eventsMetadata.getRoiEvents(elastic, input);
        res.status(200).json(roiEvents);
        return next();
    }));

    router.route("/amr").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let mtmc = new mdx.Services.MTMC();
        let amrEvents = await mtmc.getAMREvents(elastic, input);
        res.status(200).json(amrEvents);
        return next();
    }));
}

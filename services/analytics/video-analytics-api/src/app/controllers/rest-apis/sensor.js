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
 * Registers sensor routes on the provided router.
 * @param {import("express").Router} router - Router instance used for the sensor API.
 * @returns {void}
 */
module.exports = (router) => {

    // This will handle the url calls for /sensor

    router.route("/lookup").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let sensorObject = new mdx.Services.Sensor();
        let lookupResponse = await sensorObject.lookup(elastic,input);
        res.status(200).json(lookupResponse);
        return next();
    }));
}

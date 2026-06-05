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
 * Registers clustering routes on the provided router.
 * @param {import("express").Router} router - Router instance used for the clustering API.
 * @returns {void}
 */
module.exports = (router) => {

    // This will handle the url calls for /clustering

    router.route("/behavior").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let clusteringObject = new mdx.Services.Clustering();
        let clusters = await clusteringObject.getSampledBehaviorClusters(elastic,input);
        res.status(200).json(clusters);
        return next();
    }));

    router.route("/add-label").post(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.body;
        let clusteringObject = new mdx.Services.Clustering();
        let result = await clusteringObject.addClusterLabel(elastic,input);
        res.status(201).json(result);
        return next();
    }));

}

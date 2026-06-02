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
 * Registers frame routes on the provided router.
 * @param {import("express").Router} router - Router instance used for the frames API.
 * @returns {void}
 */
module.exports = (router) => {
    
    // This will handle the url calls for /frames

    router.route("/").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let framesMetadata = new mdx.Services.Frames();
        let frames = await framesMetadata.getFrames(elastic,input);
        res.status(200).json(frames);
        return next();
    }));

    router.route("/enhanced").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let framesMetadata = new mdx.Services.Frames();
        let enhancedFrames = await framesMetadata.getEnhancedFrames(elastic,input);
        res.status(200).json(enhancedFrames);
        return next();
    }));

    router.route("/bev").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let framesMetadata = new mdx.Services.Frames();
        let bevFrames = await framesMetadata.getBevFrames(elastic,input);
        res.status(200).json(bevFrames);
        return next();
    }));

    router.route("/high-confidence-objects").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let framesMetadata = new mdx.Services.Frames();
        let objects = await framesMetadata.getMaxConfidenceDetectionOfObjects(elastic, input);
        res.status(200).json(objects);
        return next();
    }));

    router.route("/proximity-detection").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let framesMetadata = new mdx.Services.Frames();
        let proximityClusterResult = await framesMetadata.getProximityClusters(elastic,input);
        res.status(200).json(proximityClusterResult);
        return next();
    }));

    router.route("/pts").get(mdx.Utils.Utils.expressAsyncWrapper(async(req,res,next)=>{
        let input = req.query;
        let framesMetadata = new mdx.Services.Frames();
        let pts = await framesMetadata.getPts(elastic,input);
        res.status(200).json(pts);
        return next();
    }));

    router.route("/alerts").get(mdx.Utils.Utils.expressAsyncWrapper(async(req,res,next)=>{
        let input = req.query;
        let framesMetadata = new mdx.Services.Frames();
        let alerts = await framesMetadata.getAlerts(elastic,input);
        res.status(200).json(alerts);
        return next();
    }));
}

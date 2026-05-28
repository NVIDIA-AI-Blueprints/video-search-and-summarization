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
 * Registers analytics metrics routes on the provided router.
 * @param {import("express").Router} router - Router instance used for the metrics API.
 * @returns {void}
 */
module.exports = (router) => {

    // This will handle the url calls for /metrics

    router.route("/last-processed-timestamp").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let lastProcessedTimestampObject = new mdx.Metrics.LastProcessedTimestamp();
        let result = await lastProcessedTimestampObject.getLastProcessedTimestamp(elastic,input);
        res.status(200).json(result);
        return next();
    }));

    router.route("/tripwire/counts").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let tripwireMetricObject = new mdx.Metrics.TripwireEvent();
        let tripwireCounts = await tripwireMetricObject.getTripwireCounts(elastic,input);
        res.status(200).json(tripwireCounts);
        return next();
    }));

    router.route("/occupancy/tripwire").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let occupancyObject = new mdx.Metrics.Occupancy();
        let occupancyResult = await occupancyObject.getOccupancyBasedOnTripwireEvents(elastic,input);
        res.status(200).json(occupancyResult);
        return next();
    }));

    router.route("/occupancy/reset").post(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.body;
        let occupancyObject = new mdx.Metrics.Occupancy();
        let result = await occupancyObject.resetOccupancy(elastic,input);
        res.status(201).json(result);
        return next();
    }));

    router.route("/tripwire/histogram").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let tripwireMetricObject = new mdx.Metrics.TripwireEvent();
        let histogramResult = await tripwireMetricObject.getTripwireHistogram(elastic,input);
        res.status(200).json(histogramResult);
        return next();
    }));

    router.route("/occupancy/fov").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let occupancyObject = new mdx.Metrics.Occupancy();
        let occupancyResult = await occupancyObject.getAverageFovOccupancy(elastic,input);
        res.status(200).json(occupancyResult);
        return next();
    }));

    router.route("/occupancy/fov/histogram").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let occupancyObject = new mdx.Metrics.Occupancy();
        let fovHistogramResult = await occupancyObject.getHistogramOfAverageFovOccupancy(elastic,input);
        res.status(200).json(fovHistogramResult);
        return next();
    }));

    router.route("/occupancy/roi").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let occupancyObject = new mdx.Metrics.Occupancy();
        let occupancyResult = await occupancyObject.getRoiOccupancy(elastic,input);
        res.status(200).json(occupancyResult);
        return next();
    }));

    router.route("/occupancy/roi/histogram").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let occupancyObject = new mdx.Metrics.Occupancy();
        let roiHistogramResult = await occupancyObject.getHistogramOfRoiOccupancy(elastic,input);
        res.status(200).json(roiHistogramResult);
        return next();
    }));

    router.route("/occupancy/roi/mutually-exclusive").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let occupancyObject = new mdx.Metrics.Occupancy();
        let result = await occupancyObject.getUniqueObjectCountInMutuallyExclusiveRois(elastic,input);
        res.status(200).json(result);
        return next();
    }));

    router.route("/occupancy/tracker").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let mtmcObject = new mdx.Services.MTMC();
        let result = await mtmcObject.getOccupancyTracker(elastic,input);
        res.status(200).json(result);
        return next();
    }));

    router.route("/occupancy/tracker/histogram").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let occupancyObject = new mdx.Metrics.Occupancy();
        let result = await occupancyObject.getHistogramOfAverageOccupancyOfAPlace(elastic,input);
        res.status(200).json(result);
        return next();
    }));

    router.route("/space-utilization/histogram").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let spaceUtilizationObject = new mdx.Metrics.SpaceUtilization();
        let result = await spaceUtilizationObject.getHistogramOfSpaceUtilizationMetrics(elastic,input);
        res.status(200).json(result);
        return next();
    }));

    router.route("/average-speed").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let averageSpeedObject = new mdx.Metrics.Behavior();
        let directionAvgSpeedPerDirection = await averageSpeedObject.getAverageSpeedPerDirection(elastic, input);
        res.status(200).json(directionAvgSpeedPerDirection);
        return next();
    }));

    router.route("/flowrate").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let flowRateObject = new mdx.Metrics.Behavior();
        let directionFlowratePerDirection = await flowRateObject.getFlowratePerDirection(elastic, input);
        res.status(200).json(directionFlowratePerDirection);
        return next();
    }));

    router.route("/average-speed-with-flowrate").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let behaviorObject = new mdx.Metrics.Behavior();
        let averageSpeedWithFlowrate = await behaviorObject.getAverageSpeedWithFlowrate(elastic, input);
        res.status(200).json(averageSpeedWithFlowrate);
        return next();
    }));
    
    router.route("/average-speed-with-travel-time").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let behaviorObject = new mdx.Metrics.Behavior();
        let averageSpeedWithFlowrate = await behaviorObject.getAverageSpeedWithTravelTime(elastic, input);
        res.status(200).json(averageSpeedWithFlowrate);
        return next();
    }));
    
    router.route("/road-network/segment-speed").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let behaviorObject = new mdx.Metrics.Behavior();
        let roadNetworkSegmentSpeed = await behaviorObject.getRoadSegmentSpeed(elastic, input);
        res.status(200).json(roadNetworkSegmentSpeed);
        return next();
    }));

}

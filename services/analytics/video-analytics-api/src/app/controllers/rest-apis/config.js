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
const path = require('path');
const fileUploadBaseDirectory = path.join(__dirname,"../../files");

/**
 * Registers configuration routes on the provided router.
 * @param {import("express").Router} router - Router instance used for the config API.
 * @returns {void}
 */
module.exports = (router) => {

    // This will handle the url calls for /config
    
    router.route("/upload-file/:docType").post(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        const docType = req.params.docType;
        const formFields = [{name:"configFiles" , maxCount:1,validFileExtensions: new Set([".json"])}];
        let multerUpload = mdx.Utils.FileUploadHandler.getMulterUpload(formFields,fileUploadBaseDirectory);
        multerUpload(req, res, async (error)=>{
            try{
                if(error){
                    throw mdx.Utils.FileUploadHandler.errorHandler(error, formFields);
                }
                switch (docType){
                    case "calibration":{
                        let calibrationObject = new mdx.Services.Calibration();
                        let result = await calibrationObject.upload(elastic,kafka,{fileDetails:req.files, fieldName:formFields[0].name});
                        res.status(201).json(result);
                        return next();
                    }
                    case "road-network":{
                        let roadNetworkObject = new mdx.Services.RoadNetwork();
                        let result = await roadNetworkObject.upload(elastic,{fileDetails:req.files, fieldName:formFields[0].name});
                        res.status(201).json(result);
                        return next();
                    }
                    case "usd-assets":{
                        let UsdAssetsObject = new mdx.Services.UsdAssets();
                        let result = await UsdAssetsObject.upload(elastic,{fileDetails:req.files, fieldName:formFields[0].name});
                        res.status(201).json(result);
                        return next();
                    }
                    default:
                        return next(new mdx.Errors.BadRequestError(`Invalid docType: ${docType}.`));
                }
            }catch(error){
                return next(error);
            }
        });
    }));

    router.route("/update/:docType").post(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        const docType = req.params.docType;
        let inputConfig = req.body;
        let configManager = new mdx.Services.ConfigManager();
        let result = await configManager.update(elastic, kafka, docType, inputConfig);
        res.status(201).json(result);
        return next();
    }));

    router.route("/update/status/:docType/:referenceId").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        const input = {docType: req.params.docType, referenceId: req.params.referenceId};
        const configStatusTimeoutMs = cache.get("configStatusTimeoutMs");
        let configManager = new mdx.Services.ConfigManager();
        let result = await configManager.getConfigStatus(elastic, input, configStatusTimeoutMs);
        res.status(200).json(result);
        return next();
    }));

    router.route("/calibration").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let calibrationObject = new mdx.Services.Calibration();
        let {calibration} = await calibrationObject.getCalibration(elastic, input);
        res.status(200).json(calibration);
        return next();
    }));

    router.route("/calibration/upsert").post(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next)=>{
        let input = req.body;
        let calibrationObject = new mdx.Services.Calibration();
        let result = await calibrationObject.upsert(elastic, kafka, input);
        res.status(201).json(result);
        return next();
    }));

    router.route("/calibration/delete-sensor").post(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next)=>{
        let input = req.body;
        let calibrationObject = new mdx.Services.Calibration();
        let result = await calibrationObject.deleteSensors(elastic, kafka, input);
        res.status(201).json(result);
        return next();
    }));

    router.route("/calibration/last-modified-timestamp").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next)=>{
        let input = req.query;
        let calibrationObject = new mdx.Services.Calibration();
        let result = await calibrationObject.getLastModifiedTimestamp(elastic, input);
        res.status(200).json(result);
        return next();
    }));

    router.route("/calibration/images").post(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next)=>{
        const formFields = [{
            name:"images", 
            maxCount:20,
            validFileExtensions: new Set([".jpg",".jpeg",".png",".svg"])
        },
        {
            name:"imageMetadata", 
            maxCount:1,
            validFileExtensions: new Set([".json"])
        }];
        let multerUpload = mdx.Utils.FileUploadHandler.getMulterUpload(formFields,fileUploadBaseDirectory);
        multerUpload(req, res, async (error)=>{
            try{
                if(error){
                    throw mdx.Utils.FileUploadHandler.errorHandler(error, formFields);
                }
                let calibrationObject = new mdx.Services.Calibration();
                let result = await calibrationObject.uploadImages(elastic,{fileDetails:req.files, imageFieldName:formFields[0].name, metadataFieldName:formFields[1].name});
                res.status(201).json(result);
                return next();
            }catch(error){
                return next(error);
            }
        });
    }));

    router.route("/calibration/image").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let calibrationObject = new mdx.Services.Calibration();
        let imagePath = await calibrationObject.getImage(elastic,input);
        res.status(200).sendFile(imagePath);
        return ;
    }));

    router.route("/calibration/image-metadata").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let input = req.query;
        let calibrationObject = new mdx.Services.Calibration();
        let imageMetadata = await calibrationObject.getImageMetadata(elastic,input);
        res.status(200).json(imageMetadata);
        return next();
    }));

    router.route("/calibration/delete-images").post(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next)=>{
        let input = req.body;
        let calibrationObject = new mdx.Services.Calibration();
        let result = await calibrationObject.deleteCalibrationImages(elastic, input);
        res.status(201).json(result);
        return next();
    }));

    router.route("/road-network").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let roadNetworkObject = new mdx.Services.RoadNetwork();
        let {roadNetwork} = await roadNetworkObject.getRoadNetwork(elastic);
        res.status(200).json(roadNetwork);
        return next();
    }));

    router.route("/usd-assets").get(mdx.Utils.Utils.expressAsyncWrapper(async (req, res, next) => {
        let usdAssetsObject = new mdx.Services.UsdAssets();
        let usdAssets = await usdAssetsObject.getAssets(elastic);
        res.status(200).json(usdAssets);
        return next();
    }));
}

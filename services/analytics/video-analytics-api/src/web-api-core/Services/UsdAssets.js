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

const BadRequestError = require('../Errors/BadRequestError');
const Database = require("../Utils/Database");
const Validator = require("../Utils/Validator");
const usdAssetsSchema = require("../schemas/ajv/usdAssets.json");
const InternalServerError = require('../Errors/InternalServerError');
const ResourceNotFoundError = require('../Errors/ResourceNotFoundError');
const Elasticsearch = require("../Utils/Elasticsearch");
const Utils = require('../Utils/Utils');

/** 
 * Class which defines USD Assets
 * @memberof mdxWebApiCore.Services
 * */

class UsdAssets {
    async #initUsdAssetsEsIndex(elasticDb, index){
        const esClient = elasticDb.getClient();
        const mappings = {
            properties: {
                "assets.bbox.dimension.x": { "type": "double" },
                "assets.bbox.dimension.y": { "type": "double" },
                "assets.bbox.dimension.z": { "type": "double" }
            }
        };
        let indexExist = await esClient.indices.existsIndexTemplate({name:`${index}-template`});
        if (!indexExist) {
            await esClient.indices.putIndexTemplate({
                name: `${index}-template`,
                body: {
                    index_patterns: [index],
                    priority: 552,
                    template: {
                        mappings
                    }
                }
            });
        }
        return {success: true};
    }

    async #insertUsdAssetsEs(elasticDb, usdAssets){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("usdAssets")}`;
        await this.#initUsdAssetsEsIndex(elasticDb, index);
        const esClient = elasticDb.getClient();
        const ingestPipelineId = "insertion-timestamp-pipeline";
        
        await Elasticsearch.checkIngestPipelineExists(esClient, ingestPipelineId);
        
        const targetTimestampField = "timestamp";
        
        let queryObject = {
            index,
            pipeline: ingestPipelineId,
            id: "usdAssets",
            body: {
                usdAssets,
                targetFieldName: targetTimestampField
            }
        };
        let result = await esClient.index(queryObject);
        await esClient.indices.refresh({index});
        return result;
    }

    /**
     * Validates and uploads a USD assets configuration document.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} [input={}] - Input object.
     * @param {?Object} [input.fileDetails=null] - Uploaded files grouped by field name.
     * @param {?string} [input.fieldName=null] - Form field name containing the USD assets file.
     * @returns {Promise<Object>} Success status after persisting the USD assets document.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let usdAssetsObject = new mdx.Services.UsdAssets();
     * let result = await usdAssetsObject.upload(elastic,{fileDetails:req.files, fieldName:"configFiles"});
     */
    async upload(documentDb, {fileDetails=null, fieldName=null}={}){
        if(fieldName == null){
            throw (new BadRequestError("fieldName is required to access the uploaded files."));
        }
        if (fileDetails ==null || !(fieldName in fileDetails) || fileDetails[fieldName].length == 0){
            let errorMessage = "No file has been uploaded. Please upload the usd assets file."
            throw(new BadRequestError(errorMessage));
        }
        let usdAssets = require(fileDetails[fieldName][0].path);
        await Utils.deleteFiles([fileDetails[fieldName][0].path]);
        let validationResult = Validator.validateJsonSchema(usdAssets,usdAssetsSchema,false);
        if (!validationResult.valid) {
            throw(new BadRequestError("Uploaded file doesn't follow usd assets schema."));
        }
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                await this.#insertUsdAssetsEs(documentDb, usdAssets);
                return {success:true};
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #getAssetsFromEs(elasticDb){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("usdAssets")}`;
        let queryObject = {
            index,
            body: {
                query: {
                    ids: {
                        values: ["usdAssets"]
                    }
                }
            },
            size: 1
        };
        let result = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return result;
    }

    /**
     * Retrieves the stored USD assets configuration.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {boolean} [configMissingErr=true] - Whether to throw when the config is absent.
     * @returns {Promise<Object>} USD assets document is returned.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let usdAssetsObject = new mdx.Services.UsdAssets();
     * let result = await usdAssetsObject.getAssets(elastic);
     */
    async getAssets(documentDb, configMissingErr = true){
        let assetsResult = {};
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let result = await this.#getAssetsFromEs(documentDb);
                if(result.indexAbsent){
                    if(configMissingErr){
                        throw(new ResourceNotFoundError("Resource: usdAssets not found."));
                    }
                    return assetsResult;
                }else{
                    result = Elasticsearch.searchResultFormatter(result.body);
                    if(result.length == 0 && configMissingErr){
                        throw(new ResourceNotFoundError("Resource: usdAssets not found."));
                    }else if(result.length != 0){
                        assetsResult = result[0];
                    }
                    return assetsResult?.usdAssets || {};
                }
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }
}

module.exports = UsdAssets;

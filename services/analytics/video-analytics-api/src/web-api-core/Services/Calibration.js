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
const deepcopy = require("deepcopy");
const Database = require("../Utils/Database");
const MessageBroker = require("../Utils/MessageBroker");
const filterTemplate = require("../queryTemplates/filter.json");
const Validator = require("../Utils/Validator");
const calibrationSchema = require("../schemas/ajv/calibration.json");
const imageMetadataSchema = require("../schemas/ajv/imageMetadata.json");
const InternalServerError = require('../Errors/InternalServerError');
const ResourceNotFoundError = require('../Errors/ResourceNotFoundError');
const InvalidInputError = require('../Errors/InvalidInputError');
const Elasticsearch = require("../Utils/Elasticsearch");
const NotificationManager = require("./NotificationManager");
const Utils = require('../Utils/Utils');

/** 
 * Class which defines Calibration
 * @memberof mdxWebApiCore.Services
 * */

class Calibration {

    async #initCalibrationEsIndex(elasticDb, index){
        const esClient = elasticDb.getClient();
        const mappings = {
            properties: {
                "calibration.sensors.origin.lng": { "type": "double" },
                "calibration.sensors.origin.lat": { "type": "double" },
                "calibration.sensors.geoLocation.lng": { "type": "double" },
                "calibration.sensors.geoLocation.lat": { "type": "double" },
                "calibration.sensors.coordinates.x": { "type": "double" },
                "calibration.sensors.coordinates.y": { "type": "double" },
                "calibration.sensors.coordinates.z": { "type": "double" },
                "calibration.sensors.translationToGlobalCoordinates.x": { "type": "double" },
                "calibration.sensors.translationToGlobalCoordinates.y": { "type": "double" },
                "calibration.sensors.translationToGlobalCoordinates.z": { "type": "double" },
                "calibration.sensors.scaleFactor": { "type": "double" },
                "calibration.sensors.attributes.value": {"type": "text", "index": false },
                "calibration.sensors.group.origin": {"type": "nested", "enabled": false,"properties":{"inner_array":{"type":"double"}}},
                "calibration.sensors.group.dimensions": {"type": "nested", "enabled": false,"properties":{"inner_array":{"type":"double"}}},
                "calibration.sensors.region.origin": {"type": "nested", "enabled": false,"properties":{"inner_array":{"type":"double"}}},
                "calibration.sensors.region.dimensions.length": { "type": "double" },
                "calibration.sensors.region.dimensions.width": { "type": "double" },
                "calibration.sensors.imageCoordinates.x": { "type": "double" },
                "calibration.sensors.imageCoordinates.y": { "type": "double" },
                "calibration.sensors.globalCoordinates.x": { "type": "double" },
                "calibration.sensors.globalCoordinates.y": { "type": "double" },
                "calibration.sensors.rois.roiCoordinates.x": { "type": "double" },
                "calibration.sensors.rois.roiCoordinates.y": { "type": "double" },
                "calibration.sensors.rois.roiCoordinates.z": { "type": "double" },
                "calibration.sensors.intrinsicMatrix": {"type": "nested", "enabled": false,"properties":{"inner_array":{"type":"double"}}},
                "calibration.sensors.extrinsicMatrix": {"type": "nested", "enabled": false, "properties":{"inner_array":{"type":"double"}}},
                "calibration.sensors.cameraMatrix": {"type": "nested", "enabled": false, "properties":{"inner_array":{"type":"double"}}},
                "calibration.sensors.homography": {"type": "nested", "enabled": false, "properties":{"inner_array":{"type":"double"}}},
                "calibration.sensors.tripwires.wire.p1.x": { "type": "double" },
                "calibration.sensors.tripwires.wire.p1.y": { "type": "double" },
                "calibration.sensors.tripwires.wire.p2.x": { "type": "double" },
                "calibration.sensors.tripwires.wire.p2.y": { "type": "double" },
                "calibration.sensors.tripwires.direction.p1.x": { "type": "double" },
                "calibration.sensors.tripwires.direction.p1.y": { "type": "double" },
                "calibration.sensors.tripwires.direction.p2.x": { "type": "double" },
                "calibration.sensors.tripwires.direction.p2.y": { "type": "double" },
                "calibration.rois.roiCoordinates.x": { "type": "double" },
                "calibration.rois.roiCoordinates.y": { "type": "double" },
                "calibration.rois.roiCoordinates.z": { "type": "double" },
                "calibration.corridors.length": { "type": "double" }
            }
        };
        let indexExist = await esClient.indices.existsIndexTemplate({name:`${index}-template`});
        if (!indexExist) {
            await esClient.indices.putIndexTemplate({
                name: `${index}-template`,
                body: {
                    index_patterns: [index],
                    priority: 551,
                    template: {
                        mappings
                    }
                }
            });
        }
        return {success: true};
    }

    async #insertCalibrationEs(elasticDb, calibration) {
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("calibration")}`;
        await this.#initCalibrationEsIndex(elasticDb, index);
        const esClient = elasticDb.getClient();
        const ingestPipelineId = "insertion-timestamp-pipeline";
        
        await Elasticsearch.checkIngestPipelineExists(esClient, ingestPipelineId);
        
        const targetTimestampField = "timestamp";
        
        let queryObject = {
            index,
            pipeline: ingestPipelineId,
            id: "calibration",
            body: {
                calibration,
                targetFieldName: targetTimestampField
            }
        };
        let result = await esClient.index(queryObject);
        await esClient.indices.refresh({index});
        return result;
    }
    async #insertCalibrationAuditEs(elasticDb, {calibration, timestamp, eventType}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("calibrationAudit")}`;
        await this.#initCalibrationEsIndex(elasticDb, index);
        const esClient = elasticDb.getClient();
        let queryObject = {
            index,
            body: {calibration, timestamp, eventType}
        };
        let result = await esClient.index(queryObject);
        await esClient.indices.refresh({index});
        return result;
    }

    async #getCalibrationEs(elasticDb){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("calibration")}`;
        const esClient = elasticDb.getClient();
        let queryObject = { 
            index, 
            body: { 
                query: {
                    ids: {
                        values: ["calibration"]
                    }
                }
            }, 
            size: 1 
        };
        let result = await Elasticsearch.getSearchResults(esClient, queryObject, false);
        let calibrationResult = null;
        if(!result.indexAbsent){
            result = Elasticsearch.searchResultFormatter(result.body);
            if(result.length>0){
                calibrationResult=result[0];
            }
        }
        return calibrationResult;
    }

    #getEmptyCalibrationTemplate(sensorId=null) {
        let emptyTemplate = {
            version: "1.0",
            osmURL: "",
            calibrationType: "",
            sensors: []
        };
        if(sensorId!=null){
            emptyTemplate.sensors.push({
                type: "",
                id: sensorId,
                origin: {
                    lng: 0.0,
                    lat: 0.0
                },
                geoLocation: {
                    lng: 0.0,
                    lat: 0.0
                },
                coordinates:{
                    x: 0.0,
                    y: 0.0
                },
                scaleFactor:0.0,
                attributes:[],
                place: [],
                imageCoordinates: [],
                globalCoordinates: [],
                tripwires: [],
                rois: []
            });
        }
        return emptyTemplate;
    }

    /** 
     * returns an object containing calibration and the timestamp associated with it.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} [input={}] - Input object.
     * @param {?string} [input.sensorId=null] - Sensor ID used to retrieve calibration for a specific sensor.
     * @param {?boolean} [input.emptyIfNotFound=true] - Whether to return an empty calibration object when no calibration is found.
     * @returns {Promise<Object>} Calibration Object along with timestamp is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let calibrationObject = new mdx.Services.Calibration();
     * let {calibration, timestamp} = await calibrationObject.getCalibration(elastic);
     */
    async getCalibration(documentDb, input={}){
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                sensorId: {
                    type: ["string","null"],
                    default: null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "sensorId should have atleast 1 character.",
                        maxLength: "sensorId should have atmost 10000 characters."
                    }
                },
                emptyIfNotFound: {
                    type: "boolean",
                    default: true,
                    errorMessage: {
                        type: "emptyIfNotFound should be a boolean."
                    },
                    description: "If set to true, empty calibration will be returned when calibration config is not found."
                }
            }
        }
        let validationResult = Validator.validateJsonSchema(input, schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        let calibrationResult = null;
        const queryTime = new Date().toISOString();
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                calibrationResult = await this.#getCalibrationEs(documentDb);
                break;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
        if(calibrationResult !=null){
            if(input.sensorId !=null){
                let {calibration, timestamp} = calibrationResult;
                let calibratedSensor = null;
                for(let sensor of calibration.sensors){
                    if(sensor.id === input.sensorId){
                        calibratedSensor=sensor;
                        break;
                    }
                }
                if(calibratedSensor==null){
                    if(!input.emptyIfNotFound){
                        throw (new ResourceNotFoundError(`SensorId '${input.sensorId}' not found in calibration.`));
                    }
                    let emptyCalibrationTemplate = this.#getEmptyCalibrationTemplate(input.sensorId);
                    calibratedSensor = emptyCalibrationTemplate.sensors[0];
                    timestamp = queryTime;
                }
                calibration.sensors=[calibratedSensor];
                return {calibration,timestamp};
            }else{
                return calibrationResult;
            }
        }else{
            if(!input.emptyIfNotFound){
                throw (new ResourceNotFoundError(`Calibration not found.`));
            }
            return ({
                calibration: this.#getEmptyCalibrationTemplate(input.sensorId),
                timestamp: queryTime
            });
        }
    }

    async #getCalibrationTypeEs(elasticDb){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("calibration")}`;
        let queryObject = { 
            index, 
            body: { 
                query: {
                    ids: {
                        values: ["calibration"]
                    }
                }
            },
            _source_includes: ["calibration.calibrationType"],
            size: 1 
        };
        let searchResults = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        return searchResults;
    }

    /**
     * Retrieves the configured calibration type from documentDb.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @returns {Promise<string|null>} Calibration type is returned, or null when no calibration is stored.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let calibrationObject = new mdx.Services.Calibration();
     * let calibrationType = await calibrationObject.getCalibrationType(elastic);
     */
    async getCalibrationType(documentDb){
        let calibrationType = null;
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let result = await this.#getCalibrationTypeEs(documentDb);
                if(!result.indexAbsent){
                    result = Elasticsearch.searchResultFormatter(result.body);
                    if(result.length>0){
                        calibrationType=result[0].calibration.calibrationType;
                    }
                }
                return calibrationType;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    /**
     * Returns the timestamp of when calibration was last modified.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} [input={}] - Input object.
     * @returns {Promise<string|null>} Last modified timestamp or null if no calibration data exists is returned.
     * @throws {InternalServerError} When database connection is invalid or database type is unsupported.
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let calibrationObject = new mdx.Services.Calibration();
     * let lastModifiedTimestamp = await calibrationObject.getLastModifiedTimestamp(elastic);
     */
    async getLastModifiedTimestamp(documentDb, input={}){
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                return await this.#getLastModifiedTimestampEs(documentDb);
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #getLastModifiedTimestampEs(elasticDb){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("calibration")}`;
        const queryObject = { 
            index, 
            body: { 
                query: {
                    ids: {
                        values: ["calibration"]
                    }
                }
            },
            _source_includes: ["timestamp"],
            size: 1 
        };
        let result = await Elasticsearch.getSearchResults(elasticDb.getClient(), queryObject, false);
        if(!result.indexAbsent){
            let formattedResult = Elasticsearch.searchResultFormatter(result.body);
            if(formattedResult.length > 0){
                return {timestamp: formattedResult[0].timestamp};
            }
        }
        return {timestamp: null};
    }

    #getSensorCalibrationMap(calibration){
        let sensorCalibrationMap = new Map();
        for(let sensor of calibration.sensors){
            sensorCalibrationMap.set(sensor.id,sensor);
        }
        return sensorCalibrationMap;
    }

     /** 
     * returns a success message once the calibration file is uploaded and kafka message is sent.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {MessageBroker} messageBroker - MessageBroker Object
     * @param {Object} [input={}] - Input object.
     * @param {?Object} [input.fileDetails=null] - File details object.
     * @param {?string} [input.fieldName=null] - Field name used to access the uploaded files.
     * @returns {Promise<Object>} A success message is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * const kafka = new mdx.Utils.Kafka({brokers: ["kafka-broker-url"]}, kafkaConfigMap);
     * let calibrationObject = new mdx.Services.Calibration();
     * let result = await calibrationObject.upload(elastic,kafka,{fileDetails:req.files, fieldName:"configFiles"});
     */
    async upload(documentDb, messageBroker, {fileDetails=null, fieldName=null}={}){
        if(fieldName == null){
            throw (new BadRequestError("fieldName is required to access the uploaded files."));
        }
        if (fileDetails ==null || !(fieldName in fileDetails) || fileDetails[fieldName].length == 0){
            let errorMessage = "No file has been uploaded. Please upload the calibration file."
            throw(new BadRequestError(errorMessage));
        }
        let calibration = require(fileDetails[fieldName][0].path);
        await Utils.deleteFiles([fileDetails[fieldName][0].path]);
        let validationResult = Validator.validateJsonSchema(calibration,calibrationSchema,false);
        if (!validationResult.valid) {
            throw(new BadRequestError("Uploaded file doesn't follow calibration schema."));
        }
        if(calibration.sensors.length==0){
            throw(new InvalidInputError("There should be atleast one sensor in calibration."))
        }
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                await this.#insertCalibrationEs(documentDb,calibration);
                let calibrationResult = await this.#getCalibrationEs(documentDb);
                if(calibrationResult!=null){
                    let {timestamp} = calibrationResult;
                    let updateDetails = {
                        calibration,
                        timestamp,
                        eventType: "upsert-all"
                    };
                    if(messageBroker==null){
                        await this.#insertCalibrationAuditEs(documentDb, updateDetails);
                    }else{
                        let notificationManagerObject = new NotificationManager();
                        await Promise.all([
                            this.#insertCalibrationAuditEs(documentDb, updateDetails),
                            notificationManagerObject.produceCalibrationNotification(messageBroker, updateDetails)
                        ]);
                    }
                    return {success:true};
                }else{
                    throw (new InternalServerError("Insertion of calibration config has failed. Couldn't find config before audit index insertion."));
                }
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    /** 
     * returns a success message once the input calibration is updated/inserted and kafka message is sent.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {MessageBroker} messageBroker - MessageBroker Object
     * @param {Object} inputCalibration - Calibration object to update or insert.
     * @returns {Promise<Object>} A success message is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * const kafka = new mdx.Utils.Kafka({brokers: ["kafka-broker-url"]}, kafkaConfigMap);
     * let calibrationObject = new mdx.Services.Calibration();
     * let result = await calibrationObject.upsert(elastic,kafka,inputCalibration);
     */ 
    async upsert(documentDb, messageBroker, inputCalibration){
        let validationResult = Validator.validateJsonSchema(inputCalibration,calibrationSchema,false);
        if (!validationResult.valid) {
            throw(new BadRequestError("Upserted data doesn't follow calibration schema."));
        }
        if(inputCalibration.sensors.length==0){
            throw(new InvalidInputError("There should be atleast one sensor in calibration."))
        }
        let {calibration,timestamp:previousTimestamp} = await this.getCalibration(documentDb);
        if(calibration.version!==inputCalibration.version){
            throw(new InvalidInputError("Input's 'version' doesn't match the current calibration."));
        }
        if(calibration.calibrationType!=="" && calibration.calibrationType!==inputCalibration.calibrationType){
            throw(new InvalidInputError("Input's 'calibrationType' doesn't match the current calibration."));
        }
        if(calibration.calibrationType!=="" && calibration.osmURL !== inputCalibration.osmURL){
            throw(new InvalidInputError("Input's 'osmURL' doesn't match the current calibration."))
        }
        calibration.version=inputCalibration.version;
        calibration.calibrationType=inputCalibration.calibrationType;
        calibration.osmURL=inputCalibration.osmURL;
        let sensorCalibrationMap = this.#getSensorCalibrationMap(inputCalibration);
        let updatedSensors = new Set();
        for(let i=0;i<calibration.sensors.length;i++){
            let sensor = calibration.sensors[i];
            if(sensorCalibrationMap.has(sensor.id)){
                calibration.sensors[i] = sensorCalibrationMap.get(sensor.id);
                updatedSensors.add(sensor.id);
            }
        }
        let newSensors = Utils.setDifference(new Set(sensorCalibrationMap.keys()),updatedSensors);
        for(let sensorId of newSensors){
            calibration.sensors.push(sensorCalibrationMap.get(sensorId));
        }
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                await this.#insertCalibrationEs(documentDb,calibration);
                let calibrationResult = await this.#getCalibrationEs(documentDb);
                if(calibrationResult!=null){
                    let {timestamp} = calibrationResult;
                    if(Utils.tsCompare(timestamp,"<=",previousTimestamp)){
                        throw (new InternalServerError("Insertion of calibration config has failed. Couldn't find config before audit index insertion."));
                    }
                    let updateDetails = {
                        calibration:inputCalibration,
                        timestamp,
                        eventType: "upsert"
                    };
                    if(messageBroker==null){
                        await this.#insertCalibrationAuditEs(documentDb, updateDetails);
                    }else{
                        let notificationManagerObject = new NotificationManager();
                        await Promise.all([
                            this.#insertCalibrationAuditEs(documentDb, updateDetails),
                            notificationManagerObject.produceCalibrationNotification(messageBroker, updateDetails)
                        ]);
                    }
                    return {success:true};
                }else{
                    throw (new InternalServerError("Insertion of calibration config has failed. Couldn't find config before audit index insertion."));
                }
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    /** 
     * returns a success message along with invalid input and deleted sensors once the sensors in calibration have been deleted and kafka message is sent.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {MessageBroker} messageBroker - MessageBroker Object
     * @param {Object} input - Input object.
     * @param {Array<string>} input.sensorIds - Sensor IDs to delete from calibration.
     * @returns {Promise<Object>} A success message is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * const kafka = new mdx.Utils.Kafka({brokers: ["kafka-broker-url"]}, kafkaConfigMap);
     * let calibrationObject = new mdx.Services.Calibration();
     * let result = await calibrationObject.deleteSensors(elastic,kafka,{sensorIds:["abc","xyz"]});
     */
    async deleteSensors(documentDb, messageBroker, input){
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                sensorIds:{
                    type: "array",
                    minItems: 1,
                    items:{
                        type: "string",
                        minLength: 1,
                        maxLength: 10000,
                        errorMessage: {
                            minLength: "sensorId should have atleast 1 character.",
                            maxLength: "sensorId should have atmost 10000 characters."
                        }
                    },
                    errorMessage: {
                        minItems: "sensorIds should have atleast 1 item."
                    }
                }
            },
            required: ["sensorIds"],
            errorMessage:{
                required: "Input should have required property 'sensorIds'."
            }
        };
        let validationResult = Validator.validateJsonSchema(input,schema,false);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        let {calibration,timestamp:previousTimestamp} = await this.getCalibration(documentDb);
        let sensorCalibrationMap = this.#getSensorCalibrationMap(calibration);
        let deletedSensors = new Array();
        let invalidSensors = new Array();
        let inputSensorIds = new Set(input.sensorIds);
        for(let sensorId of inputSensorIds){
            if(!sensorCalibrationMap.has(sensorId)){
                invalidSensors.push(sensorId);
            }else{
                deletedSensors.push(sensorCalibrationMap.get(sensorId));
                sensorCalibrationMap.delete(sensorId);
            }
        }
        if(invalidSensors.length == inputSensorIds.size){
            return {success:{partial:false,complete:false}, invalidSensors, deletedCalibration:null };
        }
        let deletedCalibration = {
            version: calibration.version,
            osmURL: calibration.osmURL,
            calibrationType: calibration.calibrationType,
            sensors: deletedSensors
        };
        calibration.sensors = Array.from(sensorCalibrationMap.values());
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                await this.#insertCalibrationEs(documentDb,calibration);
                let calibrationResult = await this.#getCalibrationEs(documentDb);
                if(calibrationResult!=null){
                    let {timestamp} = calibrationResult;
                    if(Utils.tsCompare(timestamp,"<=",previousTimestamp)){
                        throw (new InternalServerError("Insertion of calibration config has failed. Couldn't find config before audit index insertion."));
                    }
                    let updateDetails = {
                        calibration:deletedCalibration,
                        timestamp,
                        eventType: "delete"
                    };
                    if(messageBroker==null){
                        await this.#insertCalibrationAuditEs(documentDb, updateDetails);
                    }else{
                        let notificationManagerObject = new NotificationManager();
                        await Promise.all([
                            this.#insertCalibrationAuditEs(documentDb, updateDetails),
                            notificationManagerObject.produceCalibrationNotification(messageBroker, updateDetails)
                        ]);
                    }
                    if(invalidSensors.length==0){
                        return {success:{partial:false,complete:true}, invalidSensors:null, deletedCalibration };
                    }else{
                        return {success:{partial:true,complete:false}, invalidSensors, deletedCalibration };
                    }
                }else{
                    throw (new InternalServerError("Insertion of calibration config has failed. Couldn't find config before audit index insertion."));
                }
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #getImageMetadataUsingIds(documentDb,imageIds){
        let imageMetadata = new Array();
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                const indexPrefix = documentDb.getConfigs().get("indexPrefix");
                const index = `${indexPrefix}${Elasticsearch.getIndex("calibrationImages")}`;
                const esClient = documentDb.getClient();
                let queryObject = { 
                    index, 
                    body: { 
                        query: {
                            ids: {
                                values: imageIds
                            }
                        }
                    }, 
                    size: imageIds.length
                };
                let result = await Elasticsearch.getSearchResults(esClient, queryObject, false);
                if(!result.indexAbsent){
                    imageMetadata = Elasticsearch.searchResultFormatter(result.body);
                    imageMetadata = imageMetadata.filter(metadata=>(!metadata.deleted));
                }
                return imageMetadata;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    /** 
     * returns an object containing calibration maps.
     * @public
     * @param {Object} calibration - Calibration object used to build lookup maps.
     * @returns {Promise<Object>} An object containing calibration maps is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * let calibrationObject = new mdx.Services.Calibration();
     * let calibrationMaps = calibrationObject.getCalibrationMaps(calibration);
     */
    getCalibrationMaps(calibration){
        let calibrationMaps={
            placeHierarchyMap : new Map(),
            sensorPlaceMap: new Map(),
            corridorInfoMap: new Map()
        }
        for(let sensor of calibration.sensors){
            let placeHierarchyParent = null;
            for (let placeTag of sensor.place){
                if(placeHierarchyParent==null){
                    placeHierarchyParent = `${placeTag.name}=${placeTag.value}`;
                    if(!calibrationMaps.placeHierarchyMap.has(placeHierarchyParent)){
                        calibrationMaps.placeHierarchyMap.set(placeHierarchyParent, { places: null, sensors: null });
                    }
                }else{
                    if (calibrationMaps.placeHierarchyMap.get(placeHierarchyParent).places == null) {
                        calibrationMaps.placeHierarchyMap.get(placeHierarchyParent).places = new Set();
                    }
                    let placeHierarchyChild = `${placeHierarchyParent}/${placeTag.name}=${placeTag.value}`;
                    calibrationMaps.placeHierarchyMap.get(placeHierarchyParent).places.add(placeHierarchyChild);
                    if (!calibrationMaps.placeHierarchyMap.has(placeHierarchyChild)) {
                        calibrationMaps.placeHierarchyMap.set(placeHierarchyChild, { places: null, sensors: null });
                    }
                    placeHierarchyParent = placeHierarchyChild;
                }
            }
            if(placeHierarchyParent!=null){
                if (calibrationMaps.placeHierarchyMap.get(placeHierarchyParent).sensors == null) {
                    calibrationMaps.placeHierarchyMap.get(placeHierarchyParent).sensors = new Set();
                }
                calibrationMaps.placeHierarchyMap.get(placeHierarchyParent).sensors.add(sensor.id);    
            }
            calibrationMaps.sensorPlaceMap.set(sensor.id, placeHierarchyParent);
        }
        if(calibration.hasOwnProperty("corridors")){
            for (let corridor of calibration.corridors) {
                calibrationMaps.corridorInfoMap.set(corridor.name, corridor);
            }
        }
        return calibrationMaps;
    }

    #getFileNameImageMetadataMap(imageMetadata){
        let imageMetadataMap = new Map();
        for(let metadata of imageMetadata.images){
            if(!imageMetadataMap.has(metadata.fileName)){
                imageMetadataMap.set(metadata.fileName,[metadata]);
            }else{
                let currentMetadataList = imageMetadataMap.get(metadata.fileName);
                currentMetadataList.push(metadata);
                imageMetadataMap.set(metadata.fileName,currentMetadataList);
            }
        }
        return imageMetadataMap;
    }

    async #getFileCountMap(documentDb,fileNames){
        let fileCountMap = new Map();
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let queryBody = deepcopy(filterTemplate);
                const indexPrefix = documentDb.getConfigs().get("indexPrefix");
                const index = `${indexPrefix}${Elasticsearch.getIndex("calibrationImages")}`;
                const esClient = documentDb.getClient();
                let fileNameClauses = new Array();
                for (let fileName of fileNames) {
                    fileNameClauses.push({ term: { "fileName.keyword": fileName } });
                }
                queryBody.query.bool.must.push({ bool: { should: fileNameClauses, minimum_should_match: 1 } });
                queryBody.aggs = {
                    fileNames:{
                        terms:{
                            field: "fileName.keyword",
                            size: fileNames.length
                        }
                    }
                };
                let queryObject = { 
                    index, 
                    body: queryBody, 
                    size: 0
                };
                let result = await Elasticsearch.getSearchResults(esClient, queryObject, false);
                if(!result.indexAbsent){
                    for(let fileCount of result.body.aggregations.fileNames.buckets){
                        fileCountMap.set(fileCount.key,fileCount.doc_count);
                    }
                }
                return fileCountMap;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #insertImageMetadata(documentDb,dataToBeInserted){
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                const indexPrefix = documentDb.getConfigs().get("indexPrefix");
                const index = `${indexPrefix}${Elasticsearch.getIndex("calibrationImages")}`;
                const esClient = documentDb.getClient();
                
                const ingestPipelineId = "insertion-timestamp-pipeline";
                
                await Elasticsearch.checkIngestPipelineExists(esClient, ingestPipelineId);
                
                const targetTimestampField = "timestamp";
                
                let queryBody = dataToBeInserted.flatMap(doc => [
                    { index: { _index: index, _id: doc.id } }, 
                    { ...doc.body, targetFieldName: targetTimestampField }
                ]);
                await esClient.bulk({ refresh: true, body: queryBody, pipeline: ingestPipelineId });
                return ({ success: true });
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    /** 
     * returns a success message once the calibration images are uploaded.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} [input={}] - Input object.
     * @param {?Object} [input.fileDetails=null] - Uploaded files grouped by field name.
     * @param {?string} [input.imageFieldName=null] - Form field name containing the calibration images.
     * @param {?string} [input.metadataFieldName=null] - Form field name containing the calibration image metadata.
     * @returns {Promise<Object>} A success message is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let calibrationObject = new mdx.Services.Calibration();
     * let result = await calibrationObject.uploadImages(elastic,{fileDetails:req.files, imageFieldName:"images", metadataFieldName: "imageMetadata"});
     */
    async uploadImages(documentDb,{fileDetails=null, imageFieldName=null, metadataFieldName=null}={}){
        if(imageFieldName == null){
            throw (new BadRequestError("imageFieldName is required to access the uploaded files."));
        }
        if(metadataFieldName == null){
            throw (new BadRequestError("metadataFieldName is required to access the uploaded files."));
        }
        if (fileDetails ==null ){
            let errorMessage = "No file has been uploaded. Please upload images and imageMetadata file."
            throw(new BadRequestError(errorMessage));
        }
        if(!(imageFieldName in fileDetails) || fileDetails[imageFieldName].length == 0){
            let errorMessage = "No images have been uploaded. Please upload images."
            throw(new BadRequestError(errorMessage));
        }
        if(!(metadataFieldName in fileDetails) || fileDetails[metadataFieldName].length == 0){
            let errorMessage = "imageMetadata file has not been uploaded. Please upload the image metadata file."
            throw(new BadRequestError(errorMessage));
        }
        let filesToBeDeleted = new Set();
        let invalidImages = new Array();
        let imageMetadata = require(fileDetails[metadataFieldName][0].path);
        filesToBeDeleted.add(fileDetails[metadataFieldName][0].path);
        let validationResult = Validator.validateJsonSchema(imageMetadata,imageMetadataSchema,false);
        if (!validationResult.valid) {
            throw(new BadRequestError("Uploaded file doesn't follow imageMetadata schema."));
        }
        let imageMetadataMap = this.#getFileNameImageMetadataMap(imageMetadata);
        let fileDetailsToBeInserted = new Map();
        for(let imageDetails of fileDetails[imageFieldName]){
            if(imageMetadataMap.has(imageDetails.originalname)){
                fileDetailsToBeInserted.set(imageDetails.originalname,imageDetails);
            }else{
                filesToBeDeleted.add(imageDetails.path);
                invalidImages.push(imageDetails.originalname);
            }
        }
        if(invalidImages.length == fileDetails[imageFieldName].length){
            await Utils.deleteFiles(Array.from(filesToBeDeleted));
            return {success:{partial:false,complete:false}, invalidImages};
        }
        let imageIds = new Array();
        for(let [uploadedImageName, metadataList] of imageMetadataMap.entries()){
            if(fileDetailsToBeInserted.has(uploadedImageName)){
                for(let metadata of metadataList){
                    if(metadata.hasOwnProperty("sensorId")){
                        imageIds.push(`sensorId-${metadata.sensorId}-${metadata.view}`);
                    }else{
                        imageIds.push(`place-${metadata.place}-${metadata.view}`);
                    }
                }
            }
        }
        let storedMetadata = await this.#getImageMetadataUsingIds(documentDb,imageIds);
        if(storedMetadata.length > 0){
            let storedFileDetailsMap = new Map();
            for(let metadata of storedMetadata){
                if(!storedFileDetailsMap.has(metadata.fileName)){
                    storedFileDetailsMap.set(metadata.fileName,{path:metadata.path,count:1});
                }else{
                    let currentFileDetails= storedFileDetailsMap.get(metadata.fileName);
                    storedFileDetailsMap.count+=1;
                    storedFileDetailsMap.set(metadata.fileName,currentFileDetails);
                }
            }
            let fileCountMap = await this.#getFileCountMap(documentDb,Array.from(storedFileDetailsMap.keys()));
            for(let [fileName, metadataCount] of fileCountMap.entries()){
                let fileDetails = storedFileDetailsMap.get(fileName);
                if(metadataCount==fileDetails.count){
                    filesToBeDeleted.add(fileDetails.path);
                }
            }
        }
        await Utils.deleteFiles(Array.from(filesToBeDeleted));
        let dataToBeInserted = new Array();
        for(let [originalName, fileDetails] of fileDetailsToBeInserted.entries()){
            let imageMetadataList = imageMetadataMap.get(originalName);
            for(let imageMetadata of imageMetadataList){
                let combinedMetadata = {
                    view: imageMetadata.view,
                    fileName: fileDetails.filename,
                    path: fileDetails.path,
                    deleted: false
                }
                if(imageMetadata.hasOwnProperty("sensorId")){
                    combinedMetadata.sensorId=imageMetadata.sensorId;
                    combinedMetadata.id=`sensorId-${combinedMetadata.sensorId}-${combinedMetadata.view}`;
                }else{
                    combinedMetadata.place=imageMetadata.place;
                    combinedMetadata.id=`place-${combinedMetadata.place}-${combinedMetadata.view}`;
                }
                dataToBeInserted.push({id: combinedMetadata.id,body:combinedMetadata});
            }
        }
        await this.#insertImageMetadata(documentDb,dataToBeInserted);
        if(invalidImages.length ==0){
            return {success:{complete:true,partial:false},invalidImages:null};
        }else{
            return {success:{complete:false,partial:true},invalidImages};
        }
    }

    async #getMetadataOfImage(documentDb, {sensorId, place, view}){
        let imageMetadata = null;
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                const indexPrefix = documentDb.getConfigs().get("indexPrefix");
                const index = `${indexPrefix}${Elasticsearch.getIndex("calibrationImages")}`;
                const id = (sensorId!=null)?`sensorId-${sensorId}-${view}`:`place-${place}-${view}`;
                const esClient = documentDb.getClient();
                let queryObject = { 
                    index, 
                    body: { 
                        query: {
                            ids: {
                                values: [id]
                            }
                        }
                    }, 
                    size: 1 
                };
                let result = await Elasticsearch.getSearchResults(esClient, queryObject, false);
                if(!result.indexAbsent){
                    result = Elasticsearch.searchResultFormatter(result.body);
                    if(result.length>0){
                        imageMetadata=result[0];
                        if(imageMetadata.deleted){
                            imageMetadata=null;
                        }
                    }
                }
                return imageMetadata;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    /** 
     * returns the path of calibration image.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {string} [input.sensorId] - Either sensorId or place should be present.
     * @param {string} [input.place] - Either sensorId or place should be present.
     * @param {("camera-view"|"warped-camera-view"|"plan-view")} input.view - View used to select the calibration image.
     * @returns {Promise<string>} Path of calibration image is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let calibrationObject = new mdx.Services.Calibration();
     * let result = await calibrationObject.getImage(elastic,{sensorId:"abc", view:"warped-camera-view"});
     */
    async getImage(documentDb, input){
        const schema = {
            type: "object",
            additionalProperties: false,
            properties: {
                sensorId:{
                    type: ["string","null"],
                    default: null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "sensorId should have atleast 1 character.",
                        maxLength: "sensorId should have atmost 10000 characters."  
                    }
                },
                place: {
                    type: ["string","null"],
                    default: null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "place should have atleast 1 character.",
                        maxLength: "place should have atmost 10000 characters."
                    }
                },
                view:{
                    type: "string",
                    enum:[
                        "camera-view",
                        "warped-camera-view",
                        "plan-view"
                    ]
                }
            },
            required:["view"],
            oneOf:[
                { required: ["sensorId"]} ,
                { required: ["place"]}
            ],
            errorMessage:{
                required: "Input should have required property 'view'.",
                oneOf: "Input can either have 'sensorId' or 'place'."
            }
        };
        let validationResult = Validator.validateJsonSchema(input,schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        let imageMetadata = await this.#getMetadataOfImage(documentDb,input);
        if(imageMetadata==null){
            throw (new InvalidInputError("Image doesn't exist for the given input params."));
        }
        return imageMetadata.path;
    }

    async #getImageMetadataFromEs(elasticDb,{sensorId,place,view}){
        const indexPrefix = elasticDb.getConfigs().get("indexPrefix");
        const index = `${indexPrefix}${Elasticsearch.getIndex("calibrationImages")}`;
        let queryBody = deepcopy(filterTemplate);
        if(sensorId!=null){
            queryBody.query.bool.must.push({ term: { "sensorId.keyword": sensorId } });
        }else if(place!=null){
            queryBody.query.bool.must.push({ term: { "place.keyword": place } });
        }
        if(view!=null){
            queryBody.query.bool.must.push({ term: { "view.keyword": view } });
        }
        queryBody.query.bool.must.push({ term: { "deleted": false } });
        const esClient = elasticDb.getClient();
        let queryObject = { 
            index, 
            body: queryBody,
            size: 10000,
            _source_includes:["view","place","sensorId"]
        };
        let result = await Elasticsearch.getSearchResults(esClient, queryObject, false);
        if(!result.indexAbsent){
            result = Elasticsearch.searchResultFormatter(result.body);
        }
        return result;
    }

    /** 
     * returns the calibration image metadata.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} [input={}] - Input object.
     * @param {?string} [input.sensorId=null] - Either sensorId or place can be present.
     * @param {?string} [input.place=null] - Either sensorId or place can be present.
     * @param {(null|"camera-view"|"warped-camera-view"|"plan-view")} [input.view=null] - View used to filter calibration image metadata.
     * @returns {Promise<Object>} Object containing image metadata is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let calibrationObject = new mdx.Services.Calibration();
     * let result = await calibrationObject.getImageMetadata(elastic,{sensorId:"abc"});
     */
    async getImageMetadata(documentDb,input={}){
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                sensorId: {
                    type: ["string","null"],
                    default: null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "sensorId should have atleast 1 character.",
                        maxLength: "sensorId should have atmost 10000 characters."
                    }
                },
                place: {
                    type: ["string","null"],
                    default: null,
                    minLength: 1,
                    maxLength: 10000,
                    errorMessage: {
                        minLength: "place should have atleast 1 character.",
                        maxLength: "place should have atmost 10000 characters."
                    }
                },
                view: {
                    type: ["string","null"],
                    enum:[
                        null,
                        "camera-view",
                        "warped-camera-view",
                        "plan-view"
                    ],
                    default:null,
                    errorMessage:{
                        enum: "view is an optional input, but when present it must have one of the following values: 'camera-view', 'warped-camera-view' or 'plan-view'."
                    }
                }
            },
            if: {
                not: {
                    properties: {
                        place: {
                            const: null
                        }
                    }
                }
            },
            then: {
                properties: {
                    sensorId: {
                        const: null
                    }
                }
            },
            errorMessage: {
                if: "Input can have either 'sensorId' or 'place'. Both of them are optional inputs, but can't be present together."
            }
        }
        let validationResult = Validator.validateJsonSchema(input,schema);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        let imageMetadata = new Array();
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                imageMetadata = await this.#getImageMetadataFromEs(documentDb,input);
                return {imageMetadata};
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }

    async #deleteCalibrationImagesEs(elasticDb,{calibrationImages}){
        let idsToBeDeleted= new Map();
        for(let {sensorId,place,view} of calibrationImages){
            const id = (sensorId!=null)?`sensorId-${sensorId}-${view}`:`place-${place}-${view}`;
            idsToBeDeleted.set(id,{sensorId,place,view});
        }
        let metadataOfImages = await this.#getImageMetadataUsingIds(elasticDb,Array.from(idsToBeDeleted.keys()));
        let invalidIds = null;
        let validIds = null;
        if(metadataOfImages.length==0){
            invalidIds=new Set(idsToBeDeleted.keys());
        }else{
            validIds = new Set();
            let fileDetails = new Map();
            let dataToBeInserted = new Array();
            for(let imageMetadata of metadataOfImages){
                validIds.add(imageMetadata.id);
                if(!fileDetails.has(imageMetadata.fileName)){
                    fileDetails.set(imageMetadata.fileName,{
                        deletionCount: 1,
                        path:imageMetadata.path
                    });
                }else{
                    let details = fileDetails.get(imageMetadata.fileName);
                    details.deletionCount+=1;
                    fileDetails.set(imageMetadata.fileName,details);
                }
                imageMetadata.path=null;
                imageMetadata.fileName=null;
                imageMetadata.deleted=true;
                dataToBeInserted.push({id: imageMetadata.id,body:imageMetadata});
            }
            invalidIds = (validIds.size == idsToBeDeleted.size)? null : Utils.setDifference(new Set(idsToBeDeleted.keys()),validIds);
            let fileCountMap = await this.#getFileCountMap(elasticDb,Array.from(fileDetails.keys()));
            let filesToBeDeleted = new Set();
            for(let [fileName, metadataCount] of fileCountMap.entries()){
                let detailsOfAFile = fileDetails.get(fileName);
                if(metadataCount==detailsOfAFile.deletionCount){
                    filesToBeDeleted.add(detailsOfAFile.path);
                }
            }
            await Promise.all([
                this.#insertImageMetadata(elasticDb,dataToBeInserted),
                Utils.deleteFiles(Array.from(filesToBeDeleted))
            ]);
        }
        let formattedValidIds = (validIds==null)?null:new Array();
        let formattedInvalidIds = (invalidIds==null)?null:new Array();
        if(validIds!=null){
            for(let id of validIds){
                let {sensorId,place,view} = idsToBeDeleted.get(id);
                if(sensorId!=null){
                    formattedValidIds.push({sensorId,view});
                }else{
                    formattedValidIds.push({place,view});
                }
            }
        }
        if(invalidIds!=null){
            for(let id of invalidIds){
                let {sensorId,place,view} = idsToBeDeleted.get(id);
                if(sensorId!=null){
                    formattedInvalidIds.push({sensorId,view});
                }else{
                    formattedInvalidIds.push({place,view});
                }
            }
        }
        let result={invalidInput:formattedInvalidIds,deletedCalibrationImages:formattedValidIds} ;
        if(invalidIds==null){
            result.success={complete:true, partial:false};
        }else if(validIds == null){
            result.success={complete:false, partial:false};
        }else{
            result.success={complete:false, partial:true};
        }
        return result;
    }

    /** 
     * returns a success message along with invalid input and deleted calibration images.
     * @public
     * @async
     * @param {Database} documentDb - Database Object.
     * @param {Object} input - Input object.
     * @param {Array<{sensorId:?string,place:?string,view:("camera-view"|"warped-camera-view"|"plan-view")}>} input.calibrationImages - Each item in the array can contain either sensorId or place
     * @returns {Promise<Object>} A success message is returned
     * @example
     * const mdx = require("@nvidia-mdx/web-api-core");
     * const elastic = new mdx.Utils.Elasticsearch({node: "elasticsearch-url"},databaseConfigMap);
     * let calibrationObject = new mdx.Services.Calibration();
     * let result = await calibrationObject.deleteCalibrationImages(elastic,{calibrationImages:[{sensorId:"abc", view:"warped-camera-view"}]});
     */
    async deleteCalibrationImages(documentDb,input){
        const schema = {
            type: "object",
            additionalProperties: {
                not: true,
                errorMessage: "Invalid additional Input ${0#}."
            },
            properties: {
                calibrationImages:{
                    type: "array",
                    items: {
                        type: "object",
                        additionalProperties: {
                            not: true,
                            errorMessage: "Invalid additional Input ${0#}."
                        },
                        properties:{
                            sensorId: {
                                type: ["string","null"],
                                default: null,
                                minLength: 1,
                                maxLength: 10000,
                                errorMessage: {
                                    minLength: "sensorId should have atleast 1 character.",
                                    maxLength: "sensorId should have atmost 10000 characters."
                                }
                            },
                            place: {
                                type: ["string","null"],
                                default: null,
                                minLength: 1,
                                maxLength: 10000,
                                errorMessage: {
                                    minLength: "place should have atleast 1 character.",
                                    maxLength: "place should have atmost 10000 characters."
                                }
                            },
                            view: {
                                type: "string",
                                enum:[
                                    "camera-view",
                                    "warped-camera-view",
                                    "plan-view"
                                ],
                                errorMessage:{
                                    enum: "view must have one of the following values: 'camera-view', 'warped-camera-view' or 'plan-view'."
                                }
                            }
                        },
                        required:["view"],
                        oneOf:[
                            {
                                required: ["sensorId"]
                            },
                            {
                                required: ["place"]
                            }
                        ],
                        errorMessage:{
                            required: "Object in calibrationImages array should have required property 'view'.",
                            oneOf: "Input should have either 'sensorId' or 'place'."
                        }
                    }
                }
            },
            required:["calibrationImages"],
            errorMessage:{
                required: "Input should have required property 'calibrationImages'."
            }
        }
        let validationResult = Validator.validateJsonSchema(input,schema,false);
        if (!validationResult.valid) {
            throw (new BadRequestError(validationResult.reason));
        }
        switch (documentDb.getName()) {
            case "Elasticsearch": {
                let deletedResponse = await this.#deleteCalibrationImagesEs(documentDb,input);
                return deletedResponse;
            }
            default:
                throw (new InternalServerError(`Invalid database: ${documentDb.getName()}.`));
        }
    }
}

module.exports = Calibration;

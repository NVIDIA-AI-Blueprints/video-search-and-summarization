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

const sinon = require('sinon');
const { expect } = require('chai');
const express = require('express');
const request = require('supertest');
const proxyquire = require('proxyquire').noCallThru().noPreserveCache();

describe('Config Controller', () => {
    let app;
    let configController;
    let mockElastic;
    let mockKafka;
    let calibrationStub;
    let roadNetworkStub;
    let usdAssetsStub;
    let configManagerStub;
    let mockCache;

    beforeEach(() => {
        app = express();
        app.use(express.json());

        mockElastic = {
            getName: () => 'Elasticsearch',
            getClient: () => ({}),
            getConfigs: () => new Map([['indexPrefix', 'mdx-']])
        };

        mockKafka = {
            getName: () => 'Kafka',
            getClient: () => ({}),
            getAdminClient: () => ({})
        };

        calibrationStub = {
            upload: sinon.stub(),
            getCalibration: sinon.stub(),
            upsert: sinon.stub(),
            deleteSensors: sinon.stub(),
            getLastModifiedTimestamp: sinon.stub(),
            uploadImages: sinon.stub(),
            getImage: sinon.stub(),
            getImageMetadata: sinon.stub(),
            deleteCalibrationImages: sinon.stub()
        };

        roadNetworkStub = {
            upload: sinon.stub(),
            getRoadNetwork: sinon.stub()
        };

        usdAssetsStub = {
            upload: sinon.stub(),
            getAssets: sinon.stub()
        };

        configManagerStub = {
            update: sinon.stub(),
            getConfigStatus: sinon.stub()
        };

        mockCache = {
            get: sinon.stub().withArgs('configStatusTimeoutMs').returns(30000)
        };

        const mockMdx = {
            Services: {
                Calibration: sinon.stub().returns(calibrationStub),
                RoadNetwork: sinon.stub().returns(roadNetworkStub),
                UsdAssets: sinon.stub().returns(usdAssetsStub),
                ConfigManager: sinon.stub().returns(configManagerStub)
            },
            Utils: {
                Utils: {
                    expressAsyncWrapper: (fn) => async (req, res, next) => {
                        try {
                            await fn(req, res, next);
                        } catch (error) {
                            next(error);
                        }
                    }
                },
                FileUploadHandler: {
                    getMulterUpload: sinon.stub().returns((req, res, callback) => {
                        req.files = { configFiles: [{ path: '/tmp/test.json' }] };
                        callback(null);
                    }),
                    errorHandler: (error) => new Error(error?.message || 'Upload error')
                }
            },
            Errors: {
                BadRequestError: class BadRequestError extends Error {
                    constructor(msg) { super(msg); this.name = 'BadRequestError'; }
                },
                InvalidInputError: class InvalidInputError extends Error {
                    constructor(msg) { super(msg); this.name = 'InvalidInputError'; }
                }
            }
        };

        configController = proxyquire('../../../../../src/app/controllers/rest-apis/config', {
            '@nvidia-mdx/web-api-core': mockMdx,
            '../../initializers/elastic': mockElastic,
            '../../initializers/kafka': mockKafka,
            '../../initializers/cache': mockCache
        });

        const router = express.Router();
        configController(router);
        app.use('/config', router);

        app.use((err, req, res, next) => {
            if (err.name === 'BadRequestError') {
                res.status(400).json({ error: err.message });
            } else {
                res.status(500).json({ error: err.message });
            }
        });
    });

    afterEach(() => {
        sinon.restore();
    });

    describe('GET /config/calibration', () => {
        it('should return calibration with 200 status', async () => {
            const mockCalibration = {
                calibration: {
                    version: '1.0',
                    calibrationType: 'geo',
                    sensors: []
                }
            };
            calibrationStub.getCalibration.resolves(mockCalibration);

            const response = await request(app).get('/config/calibration');

            expect(response.status).to.equal(200);
            expect(response.body.version).to.equal('1.0');
            expect(calibrationStub.getCalibration.calledOnce).to.be.true;
        });
    });

    describe('POST /config/calibration/upsert', () => {
        it('should upsert calibration with 201 status', async () => {
            const mockResult = { success: true };
            calibrationStub.upsert.resolves(mockResult);

            const response = await request(app)
                .post('/config/calibration/upsert')
                .send({ version: '1.0', sensors: [] });

            expect(response.status).to.equal(201);
            expect(response.body.success).to.be.true;
            expect(calibrationStub.upsert.calledOnce).to.be.true;
        });
    });

    describe('POST /config/calibration/delete-sensor', () => {
        it('should delete sensors with 201 status', async () => {
            const mockResult = { success: true, deletedCount: 2 };
            calibrationStub.deleteSensors.resolves(mockResult);

            const response = await request(app)
                .post('/config/calibration/delete-sensor')
                .send({ sensorIds: ['sensor1', 'sensor2'] });

            expect(response.status).to.equal(201);
            expect(response.body.success).to.be.true;
            expect(calibrationStub.deleteSensors.calledOnce).to.be.true;
        });
    });

    describe('GET /config/calibration/last-modified-timestamp', () => {
        it('should return last modified timestamp with 200 status', async () => {
            const mockResult = { timestamp: '2023-01-12T14:20:10.000Z' };
            calibrationStub.getLastModifiedTimestamp.resolves(mockResult);

            const response = await request(app).get('/config/calibration/last-modified-timestamp');

            expect(response.status).to.equal(200);
            expect(response.body.timestamp).to.equal('2023-01-12T14:20:10.000Z');
            expect(calibrationStub.getLastModifiedTimestamp.calledOnce).to.be.true;
        });
    });

    describe('GET /config/calibration/image-metadata', () => {
        it('should return image metadata with 200 status', async () => {
            const mockMetadata = { images: [{ name: 'sensor1.jpg', sensorId: 'sensor1' }] };
            calibrationStub.getImageMetadata.resolves(mockMetadata);

            const response = await request(app)
                .get('/config/calibration/image-metadata')
                .query({ sensorId: 'sensor1' });

            expect(response.status).to.equal(200);
            expect(response.body.images).to.be.an('array');
            expect(calibrationStub.getImageMetadata.calledOnce).to.be.true;
        });
    });

    describe('POST /config/calibration/delete-images', () => {
        it('should delete calibration images with 201 status', async () => {
            const mockResult = { success: true };
            calibrationStub.deleteCalibrationImages.resolves(mockResult);

            const response = await request(app)
                .post('/config/calibration/delete-images')
                .send({ sensorId: 'sensor1' });

            expect(response.status).to.equal(201);
            expect(response.body.success).to.be.true;
            expect(calibrationStub.deleteCalibrationImages.calledOnce).to.be.true;
        });
    });

    describe('GET /config/road-network', () => {
        it('should return road network with 200 status', async () => {
            const mockRoadNetwork = {
                roadNetwork: { version: '1.0', segments: [] }
            };
            roadNetworkStub.getRoadNetwork.resolves(mockRoadNetwork);

            const response = await request(app).get('/config/road-network');

            expect(response.status).to.equal(200);
            expect(response.body.version).to.equal('1.0');
            expect(roadNetworkStub.getRoadNetwork.calledOnce).to.be.true;
        });
    });

    describe('GET /config/usd-assets', () => {
        it('should return USD assets with 200 status', async () => {
            const mockAssets = { assets: [{ id: 'asset1', type: 'model' }] };
            usdAssetsStub.getAssets.resolves(mockAssets);

            const response = await request(app).get('/config/usd-assets');

            expect(response.status).to.equal(200);
            expect(response.body.assets).to.be.an('array');
            expect(usdAssetsStub.getAssets.calledOnce).to.be.true;
        });
    });

    describe('POST /config/update/:docType', () => {
        const makeBadRequestError = (message) => {
            const error = new Error(message);
            error.name = 'BadRequestError';
            return error;
        };

        it('should submit valid behavior analytics update scenarios with 201 pending', async () => {
            const scenarios = [
                {
                    name: 'valid app-only update',
                    body: {
                        app: [{ name: 'behaviorMaxPoints', value: '411' }]
                    }
                },
                {
                    name: 'valid sensor-only update',
                    body: {
                        sensors: [{
                            id: 'default',
                            configs: [{ name: 'proximityDetectionThreshold', value: '5' }]
                        }]
                    }
                },
                {
                    name: 'valid app and sensors update',
                    body: {
                        app: [{ name: 'behaviorMaxPoints', value: '412' }],
                        sensors: [{
                            id: 'default',
                            configs: [{ name: 'proximityDetectionThreshold', value: '6' }]
                        }]
                    }
                },
                {
                    name: 'unknown app key with valid schema accepted by API and decided by BA ack later',
                    body: {
                        app: [{ name: 'codex-unallowlisted', value: '1' }]
                    }
                }
            ];

            for(const scenario of scenarios){
                const mockResult = {
                    referenceId: `video-analytics-api-${scenario.name.replaceAll(' ', '-')}`,
                    status: 'pending'
                };
                configManagerStub.update.resetHistory();
                configManagerStub.update.resolves(mockResult);

                const response = await request(app)
                    .post('/config/update/behavior-analytics')
                    .send(scenario.body);

                expect(response.status, scenario.name).to.equal(201);
                expect(response.body, scenario.name).to.deep.equal(mockResult);
                expect(configManagerStub.update.calledOnceWith(
                    mockElastic,
                    mockKafka,
                    'behavior-analytics',
                    scenario.body
                ), scenario.name).to.be.true;
            }
        });

        it('should return final failure body with 201 when publish fails after audit creation', async () => {
            const inputConfig = {
                app: [{ name: 'behaviorMaxPoints', value: '411' }]
            };
            const mockResult = {
                referenceId: 'video-analytics-api-publish-failed',
                status: 'failure',
                config: null,
                error: 'Unable to send config update to behavior analytics via kafka.'
            };
            configManagerStub.update.resolves(mockResult);

            const response = await request(app)
                .post('/config/update/behavior-analytics')
                .send(inputConfig);

            expect(response.status).to.equal(201);
            expect(response.body).to.deep.equal(mockResult);
            expect(configManagerStub.update.calledOnceWith(
                mockElastic,
                mockKafka,
                'behavior-analytics',
                inputConfig
            )).to.be.true;
        });

        it('should return 400 for invalid behavior analytics update request scenarios', async () => {
            const validationError = "Behavior analytics configuration doesn't follow schema.";
            const scenarios = [
                {
                    name: 'empty object',
                    docType: 'behavior-analytics',
                    body: {},
                    error: validationError
                },
                {
                    name: 'empty app and sensors arrays',
                    docType: 'behavior-analytics',
                    body: { app: [], sensors: [] },
                    error: validationError
                },
                {
                    name: 'full ba-config-like runtime config',
                    docType: 'behavior-analytics',
                    body: {
                        kafka: { brokers: 'localhost:9092' },
                        redisStream: { host: 'localhost' },
                        app: [{ name: 'behaviorMaxPoints', value: '411' }],
                        sensors: []
                    },
                    error: validationError
                },
                {
                    name: 'unknown top-level field',
                    docType: 'behavior-analytics',
                    body: { kafka: { brokers: ['localhost:9092'] } },
                    error: validationError
                },
                {
                    name: 'app key missing name',
                    docType: 'behavior-analytics',
                    body: { app: [{ value: '1' }] },
                    error: validationError
                },
                {
                    name: 'app key missing value',
                    docType: 'behavior-analytics',
                    body: { app: [{ name: 'behaviorMaxPoints' }] },
                    error: validationError
                },
                {
                    name: 'app value is not string',
                    docType: 'behavior-analytics',
                    body: { app: [{ name: 'behaviorMaxPoints', value: 411 }] },
                    error: validationError
                },
                {
                    name: 'sensor missing id',
                    docType: 'behavior-analytics',
                    body: { sensors: [{ configs: [{ name: 'proximityDetectionThreshold', value: '5' }] }] },
                    error: validationError
                },
                {
                    name: 'sensor config malformed',
                    docType: 'behavior-analytics',
                    body: { sensors: [{ id: 'default', configs: [{ value: '5' }] }] },
                    error: validationError
                },
                {
                    name: 'invalid doc type',
                    docType: 'foo',
                    body: { app: [{ name: 'behaviorMaxPoints', value: '411' }] },
                    error: 'Invalid docType: foo.'
                }
            ];

            for(const scenario of scenarios){
                configManagerStub.update.resetHistory();
                configManagerStub.update.rejects(makeBadRequestError(scenario.error));

                const response = await request(app)
                    .post(`/config/update/${scenario.docType}`)
                    .send(scenario.body);

                expect(response.status, scenario.name).to.equal(400);
                expect(response.body, scenario.name).to.deep.equal({ error: scenario.error });
                expect(configManagerStub.update.calledOnceWith(
                    mockElastic,
                    mockKafka,
                    scenario.docType,
                    scenario.body
                ), scenario.name).to.be.true;
            }
        });
    });

    describe('GET /config/update/status/:docType/:referenceId', () => {
        it('should return config status with configured timeout', async () => {
            const mockResult = { 'reference-id': 'request-1', status: 'pending' };
            configManagerStub.getConfigStatus.resolves(mockResult);

            const response = await request(app).get('/config/update/status/behavior-analytics/request-1');

            expect(response.status).to.equal(200);
            expect(response.body.status).to.equal('pending');
            expect(configManagerStub.getConfigStatus.calledOnceWith(
                mockElastic,
                { docType: 'behavior-analytics', referenceId: 'request-1' },
                30000
            )).to.be.true;
        });

        it('should return behavior analytics final status scenarios', async () => {
            const scenarios = [
                {
                    name: 'pending before ack',
                    referenceId: 'video-analytics-api-pending',
                    result: {
                        referenceId: 'video-analytics-api-pending',
                        docType: 'behavior-analytics',
                        status: 'pending',
                        config: { app: [{ name: 'behaviorMaxPoints', value: '411' }] },
                        error: null,
                        timestamp: '2026-05-14T12:00:00.000Z'
                    }
                },
                {
                    name: 'success after BA ack',
                    referenceId: 'video-analytics-api-success',
                    result: {
                        referenceId: 'video-analytics-api-success',
                        docType: 'behavior-analytics',
                        status: 'success',
                        config: { app: [{ name: 'behaviorMaxPoints', value: '411' }] },
                        error: null,
                        timestamp: '2026-05-14T12:00:01.000Z'
                    }
                },
                {
                    name: 'partial success after BA ack',
                    referenceId: 'video-analytics-api-partial',
                    result: {
                        referenceId: 'video-analytics-api-partial',
                        docType: 'behavior-analytics',
                        status: 'partial-success',
                        config: { app: [{ name: 'behaviorMaxPoints', value: '411' }] },
                        error: "app[1]: name='notDynamic' is not allowlisted for dynamic update",
                        timestamp: '2026-05-14T12:00:02.000Z'
                    }
                },
                {
                    name: 'BA failure',
                    referenceId: 'video-analytics-api-failure',
                    result: {
                        referenceId: 'video-analytics-api-failure',
                        docType: 'behavior-analytics',
                        status: 'failure',
                        config: null,
                        error: "rejected: app[0]: name='codex-unallowlisted' is not allowlisted for dynamic update",
                        timestamp: '2026-05-14T12:00:03.000Z'
                    }
                },
                {
                    name: 'timeout failure',
                    referenceId: 'video-analytics-api-timeout',
                    result: {
                        referenceId: 'video-analytics-api-timeout',
                        docType: 'behavior-analytics',
                        status: 'failure',
                        config: null,
                        error: 'Config update timed out after 30000 ms.',
                        timestamp: '2026-05-14T12:00:04.000Z'
                    }
                }
            ];

            for(const scenario of scenarios){
                configManagerStub.getConfigStatus.resetHistory();
                configManagerStub.getConfigStatus.resolves(scenario.result);

                const response = await request(app)
                    .get(`/config/update/status/behavior-analytics/${scenario.referenceId}`);

                expect(response.status, scenario.name).to.equal(200);
                expect(response.body, scenario.name).to.deep.equal(scenario.result);
                expect(configManagerStub.getConfigStatus.calledOnceWith(
                    mockElastic,
                    { docType: 'behavior-analytics', referenceId: scenario.referenceId },
                    30000
                ), scenario.name).to.be.true;
            }
        });

        it('should return 400 for invalid behavior analytics status lookup scenarios', async () => {
            const makeBadRequestError = (message) => {
                const error = new Error(message);
                error.name = 'BadRequestError';
                return error;
            };
            const scenarios = [
                {
                    name: 'unknown reference id',
                    docType: 'behavior-analytics',
                    referenceId: 'video-analytics-api-unknown',
                    error: 'Invalid referenceId video-analytics-api-unknown for docType behavior-analytics.'
                },
                {
                    name: 'invalid doc type',
                    docType: 'foo',
                    referenceId: 'video-analytics-api-1',
                    error: 'Invalid docType: foo.'
                }
            ];

            for(const scenario of scenarios){
                configManagerStub.getConfigStatus.resetHistory();
                configManagerStub.getConfigStatus.rejects(makeBadRequestError(scenario.error));

                const response = await request(app)
                    .get(`/config/update/status/${scenario.docType}/${scenario.referenceId}`);

                expect(response.status, scenario.name).to.equal(400);
                expect(response.body, scenario.name).to.deep.equal({ error: scenario.error });
                expect(configManagerStub.getConfigStatus.calledOnceWith(
                    mockElastic,
                    { docType: scenario.docType, referenceId: scenario.referenceId },
                    30000
                ), scenario.name).to.be.true;
            }
        });
    });

    describe('POST /config/upload-file/:docType', () => {
        it('should upload calibration file with 201 status', async () => {
            const mockResult = { success: true, message: 'Calibration uploaded' };
            calibrationStub.upload.resolves(mockResult);

            const response = await request(app)
                .post('/config/upload-file/calibration')
                .attach('configFiles', Buffer.from('{}'), 'test.json');

            expect(response.status).to.equal(201);
            expect(response.body.success).to.be.true;
            expect(calibrationStub.upload.calledOnce).to.be.true;
        });

        it('should upload road-network file with 201 status', async () => {
            const mockResult = { success: true, message: 'Road network uploaded' };
            roadNetworkStub.upload.resolves(mockResult);

            const response = await request(app)
                .post('/config/upload-file/road-network')
                .attach('configFiles', Buffer.from('{}'), 'test.json');

            expect(response.status).to.equal(201);
            expect(response.body.success).to.be.true;
            expect(roadNetworkStub.upload.calledOnce).to.be.true;
        });

        it('should upload usd-assets file with 201 status', async () => {
            const mockResult = { success: true, message: 'USD assets uploaded' };
            usdAssetsStub.upload.resolves(mockResult);

            const response = await request(app)
                .post('/config/upload-file/usd-assets')
                .attach('configFiles', Buffer.from('{}'), 'test.json');

            expect(response.status).to.equal(201);
            expect(response.body.success).to.be.true;
            expect(usdAssetsStub.upload.calledOnce).to.be.true;
        });

        it('should return 400 for invalid docType', async () => {
            const response = await request(app)
                .post('/config/upload-file/invalid-type')
                .attach('configFiles', Buffer.from('{}'), 'test.json');

            expect(response.status).to.equal(400);
            expect(response.body.error).to.include('Invalid docType');
        });
    });

    describe('POST /config/upload-file/:docType with upload error', () => {
        beforeEach(() => {
            const mockMdxWithError = {
                Services: {
                    Calibration: sinon.stub().returns(calibrationStub),
                    RoadNetwork: sinon.stub().returns(roadNetworkStub),
                    UsdAssets: sinon.stub().returns(usdAssetsStub),
                    ConfigManager: sinon.stub().returns(configManagerStub)
                },
                Utils: {
                    Utils: {
                        expressAsyncWrapper: (fn) => async (req, res, next) => {
                            try {
                                await fn(req, res, next);
                            } catch (error) {
                                next(error);
                            }
                        }
                    },
                    FileUploadHandler: {
                        getMulterUpload: sinon.stub().returns((req, res, callback) => {
                            callback(new Error('File too large'));
                        }),
                        errorHandler: (error) => new Error(error?.message || 'Upload error')
                    }
                },
                Errors: {
                    BadRequestError: class BadRequestError extends Error {
                        constructor(msg) { super(msg); this.name = 'BadRequestError'; }
                    },
                    InvalidInputError: class InvalidInputError extends Error {
                        constructor(msg) { super(msg); this.name = 'InvalidInputError'; }
                    }
                }
            };

            const newApp = express();
            newApp.use(express.json());

            const errorConfigController = proxyquire('../../../../../src/app/controllers/rest-apis/config', {
                '@nvidia-mdx/web-api-core': mockMdxWithError,
                '../../initializers/elastic': mockElastic,
                '../../initializers/kafka': mockKafka,
                '../../initializers/cache': mockCache
            });

            const router = express.Router();
            errorConfigController(router);
            newApp.use('/config', router);

            newApp.use((err, req, res, next) => {
                res.status(500).json({ error: err.message });
            });

            app = newApp;
        });

        it('should handle upload error properly', async () => {
            const response = await request(app)
                .post('/config/upload-file/calibration')
                .attach('configFiles', Buffer.from('{}'), 'test.json');

            expect(response.status).to.equal(500);
            expect(response.body.error).to.equal('File too large');
        });

        it('should handle upload error for calibration images endpoint', async () => {
            const response = await request(app)
                .post('/config/calibration/images')
                .attach('images', Buffer.from('fake-image'), 'sensor1.jpg')
                .attach('imageMetadata', Buffer.from('{}'), 'metadata.json');

            expect(response.status).to.equal(500);
            expect(response.body.error).to.equal('File too large');
        });
    });

    describe('POST /config/calibration/images', () => {
        it('should upload calibration images with 201 status', async () => {
            const mockResult = { success: true, uploadedCount: 2 };
            calibrationStub.uploadImages.resolves(mockResult);

            const response = await request(app)
                .post('/config/calibration/images')
                .attach('images', Buffer.from('fake-image'), 'sensor1.jpg')
                .attach('imageMetadata', Buffer.from('{}'), 'metadata.json');

            expect(response.status).to.equal(201);
            expect(response.body.success).to.be.true;
            expect(calibrationStub.uploadImages.calledOnce).to.be.true;
        });

        it('should handle errors during image upload', async () => {
            calibrationStub.uploadImages.rejects(new Error('Failed to process images'));

            const response = await request(app)
                .post('/config/calibration/images')
                .attach('images', Buffer.from('fake-image'), 'sensor1.jpg')
                .attach('imageMetadata', Buffer.from('{}'), 'metadata.json');

            expect(response.status).to.equal(500);
            expect(response.body.error).to.equal('Failed to process images');
        });
    });

    describe('GET /config/calibration/image', () => {
        it('should return calibration image with 200 status', async () => {
            const path = require('path');
            const os = require('os');
            const fs = require('fs');
            const tempFilePath = path.join(os.tmpdir(), 'test-image.jpg');
            fs.writeFileSync(tempFilePath, 'fake-image-data');
            
            calibrationStub.getImage.resolves(tempFilePath);

            const response = await request(app)
                .get('/config/calibration/image')
                .query({ sensorId: 'sensor1' });

            expect(response.status).to.equal(200);
            expect(calibrationStub.getImage.calledOnce).to.be.true;

            // Cleanup
            fs.unlinkSync(tempFilePath);
        });

        it('should handle errors when getting image', async () => {
            calibrationStub.getImage.rejects(new Error('Image not found'));

            const response = await request(app)
                .get('/config/calibration/image')
                .query({ sensorId: 'sensor1' });

            expect(response.status).to.equal(500);
            expect(response.body.error).to.equal('Image not found');
        });
    });

    describe('Error handling', () => {
        it('should return 400 when service throws BadRequestError', async () => {
            const BadRequestError = class extends Error {
                constructor(msg) { super(msg); this.name = 'BadRequestError'; }
            };
            calibrationStub.getCalibration.rejects(new BadRequestError('Invalid calibration input'));

            const response = await request(app).get('/config/calibration');

            expect(response.status).to.equal(400);
            expect(response.body).to.deep.equal({ error: 'Invalid calibration input' });
        });

        it('should return 500 when service throws generic Error', async () => {
            calibrationStub.getCalibration.rejects(new Error('Database connection failed'));

            const response = await request(app).get('/config/calibration');

            expect(response.status).to.equal(500);
            expect(response.body).to.deep.equal({ error: 'Database connection failed' });
        });
    });
});

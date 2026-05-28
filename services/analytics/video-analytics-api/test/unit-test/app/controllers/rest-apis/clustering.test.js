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

describe('Clustering Controller', () => {
    let app;
    let clusteringController;
    let clusteringStub;
    let mockElastic;

    beforeEach(() => {
        app = express();
        app.use(express.json());

        mockElastic = {
            getName: () => 'Elasticsearch',
            getClient: () => ({}),
            getConfigs: () => new Map([['indexPrefix', 'mdx-']])
        };

        clusteringStub = {
            getSampledBehaviorClusters: sinon.stub(),
            addClusterLabel: sinon.stub()
        };

        const mockMdx = {
            Services: {
                Clustering: sinon.stub().returns(clusteringStub)
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
                }
            }
        };

        clusteringController = proxyquire('../../../../../src/app/controllers/rest-apis/clustering', {
            '@nvidia-mdx/web-api-core': mockMdx,
            '../../initializers/elastic': mockElastic
        });

        const router = express.Router();
        clusteringController(router);
        app.use('/clustering', router);

        app.use((err, req, res, next) => {
            res.status(500).json({ error: err.message });
        });
    });

    afterEach(() => {
        sinon.restore();
    });

    describe('GET /clustering/behavior', () => {
        it('should return sampled behavior clusters with 200 status', async () => {
            const mockResult = {
                clusters: [
                    { clusterId: 'cluster1', behaviors: [{ objectId: 'obj1' }] }
                ]
            };
            clusteringStub.getSampledBehaviorClusters.resolves(mockResult);

            const response = await request(app)
                .get('/clustering/behavior')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(200);
            expect(response.body.clusters).to.be.an('array');
            expect(clusteringStub.getSampledBehaviorClusters.calledOnce).to.be.true;
        });

        it('should handle errors properly', async () => {
            clusteringStub.getSampledBehaviorClusters.rejects(new Error('Database error'));

            const response = await request(app)
                .get('/clustering/behavior')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(500);
            expect(response.body.error).to.equal('Database error');
        });
    });

    describe('POST /clustering/add-label', () => {
        it('should add cluster label with 201 status', async () => {
            const mockResult = { success: true, message: 'Label added' };
            clusteringStub.addClusterLabel.resolves(mockResult);

            const response = await request(app)
                .post('/clustering/add-label')
                .send({
                    clusterId: 'cluster1',
                    label: 'Walking'
                });

            expect(response.status).to.equal(201);
            expect(response.body.success).to.be.true;
            expect(clusteringStub.addClusterLabel.calledOnce).to.be.true;
        });

        it('should handle errors properly', async () => {
            clusteringStub.addClusterLabel.rejects(new Error('Database error'));

            const response = await request(app)
                .post('/clustering/add-label')
                .send({
                    clusterId: 'cluster1',
                    label: 'Walking'
                });

            expect(response.status).to.equal(500);
            expect(response.body.error).to.equal('Database error');
        });
    });
});

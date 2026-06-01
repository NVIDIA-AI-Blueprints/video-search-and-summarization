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

describe('Incidents Controller', () => {
    let app;
    let incidentsController;
    let incidentsStub;
    let mockElastic;

    beforeEach(() => {
        app = express();
        app.use(express.json());

        mockElastic = {
            getName: () => 'Elasticsearch',
            getClient: () => ({}),
            getConfigs: () => new Map([['indexPrefix', 'mdx-']])
        };

        incidentsStub = {
            getIncidents: sinon.stub(),
            getSevereIncidentsResult: sinon.stub()
        };

        const mockMdx = {
            Services: {
                Incidents: sinon.stub().returns(incidentsStub)
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

        incidentsController = proxyquire('../../../../../src/app/controllers/rest-apis/incidents', {
            '@nvidia-mdx/web-api-core': mockMdx,
            '../../initializers/elastic': mockElastic
        });

        const router = express.Router();
        incidentsController(router);
        app.use('/incidents', router);

        app.use((err, req, res, next) => {
            res.status(500).json({ error: err.message });
        });
    });

    afterEach(() => {
        sinon.restore();
    });

    describe('GET /incidents/', () => {
        it('should return incidents with 200 status', async () => {
            const mockResult = {
                incidents: [
                    { incidentId: 'inc1', type: 'alert', timestamp: '2023-01-12T14:20:10.000Z' }
                ]
            };
            incidentsStub.getIncidents.resolves(mockResult);

            const response = await request(app)
                .get('/incidents/')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(200);
            expect(response.body.incidents).to.be.an('array');
            expect(incidentsStub.getIncidents.calledOnce).to.be.true;
        });

        it('should handle errors properly', async () => {
            incidentsStub.getIncidents.rejects(new Error('Database error'));

            const response = await request(app)
                .get('/incidents/')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(500);
            expect(response.body.error).to.equal('Database error');
        });
    });

    describe('GET /incidents/severe', () => {
        it('should return severe incidents result with 200 status', async () => {
            const mockResult = {
                severeIncidents: {
                    sensors: ['sensor123', 'sensor456']
                }
            };
            incidentsStub.getSevereIncidentsResult.resolves(mockResult);

            const response = await request(app)
                .get('/incidents/severe')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(200);
            expect(response.body.severeIncidents).to.have.property('sensors');
            expect(incidentsStub.getSevereIncidentsResult.calledOnce).to.be.true;
        });

        it('should handle errors properly', async () => {
            incidentsStub.getSevereIncidentsResult.rejects(new Error('Database error'));

            const response = await request(app)
                .get('/incidents/severe')
                .query({
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    sensorId: 'sensor123'
                });

            expect(response.status).to.equal(500);
            expect(response.body.error).to.equal('Database error');
        });
    });
});

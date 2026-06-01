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

describe('Alerts Controller', () => {
    let app;
    let alertsController;
    let alertsStub;
    let mockElastic;

    beforeEach(() => {
        app = express();
        app.use(express.json());

        mockElastic = {
            getName: () => 'Elasticsearch',
            getClient: () => ({}),
            getConfigs: () => new Map([['indexPrefix', 'mdx-']])
        };

        alertsStub = {
            getAlerts: sinon.stub(),
            getSevereAlertsResult: sinon.stub()
        };

        const mockMdx = {
            Services: {
                Alerts: sinon.stub().returns(alertsStub)
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

        alertsController = proxyquire('../../../../../src/app/controllers/rest-apis/alerts', {
            '@nvidia-mdx/web-api-core': mockMdx,
            '../../initializers/elastic': mockElastic
        });

        const router = express.Router();
        alertsController(router);
        app.use('/alerts', router);

        app.use((err, req, res, next) => {
            res.status(500).json({ error: err.message });
        });
    });

    afterEach(() => {
        sinon.restore();
    });

    describe('GET /alerts', () => {
        it('should return alerts with 200 status', async () => {
            const mockAlerts = {
                alerts: [
                    { id: 'alert1', type: 'intrusion', timestamp: '2023-01-12T14:20:10.000Z' }
                ]
            };
            alertsStub.getAlerts.resolves(mockAlerts);

            const response = await request(app)
                .get('/alerts')
                .query({ fromTimestamp: '2023-01-12T14:00:00.000Z', toTimestamp: '2023-01-12T15:00:00.000Z' });

            expect(response.status).to.equal(200);
            expect(response.body).to.deep.equal(mockAlerts);
            expect(alertsStub.getAlerts.calledOnce).to.be.true;
        });

        it('should pass query parameters to getAlerts', async () => {
            alertsStub.getAlerts.resolves({ alerts: [] });

            await request(app)
                .get('/alerts')
                .query({ 
                    fromTimestamp: '2023-01-12T14:00:00.000Z',
                    toTimestamp: '2023-01-12T15:00:00.000Z',
                    place: 'city=test'
                });

            const callArgs = alertsStub.getAlerts.getCall(0).args[1];
            expect(callArgs.fromTimestamp).to.equal('2023-01-12T14:00:00.000Z');
            expect(callArgs.toTimestamp).to.equal('2023-01-12T15:00:00.000Z');
            expect(callArgs.place).to.equal('city=test');
        });
    });

    describe('GET /alerts/severe', () => {
        it('should return severe alerts with 200 status', async () => {
            const mockSevereAlerts = {
                severeAlerts: [
                    { place: 'city=test', alertCount: 5 }
                ]
            };
            alertsStub.getSevereAlertsResult.resolves(mockSevereAlerts);

            const response = await request(app)
                .get('/alerts/severe')
                .query({ timestamp: '2023-01-12T14:20:10.000Z' });

            expect(response.status).to.equal(200);
            expect(response.body).to.deep.equal(mockSevereAlerts);
            expect(alertsStub.getSevereAlertsResult.calledOnce).to.be.true;
        });
    });
});

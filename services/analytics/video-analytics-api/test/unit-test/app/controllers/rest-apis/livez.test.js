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

describe('Livez Controller', () => {
    let app;
    let livezController;

    beforeEach(() => {
        app = express();
        app.use(express.json());

        const mockMdx = {
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

        livezController = proxyquire('../../../../../src/app/controllers/rest-apis/livez', {
            '@nvidia-mdx/web-api-core': mockMdx
        });

        const router = express.Router();
        livezController(router);
        app.use('/livez', router);
    });

    afterEach(() => {
        sinon.restore();
    });

    describe('GET /livez', () => {
        it('should return isAlive: true with 200 status', async () => {
            const response = await request(app).get('/livez');

            expect(response.status).to.equal(200);
            expect(response.body).to.deep.equal({ isAlive: true });
        });

        it('should respond to health check quickly', async () => {
            const startTime = Date.now();
            await request(app).get('/livez');
            const endTime = Date.now();

            expect(endTime - startTime).to.be.lessThan(100);
        });
    });
});
